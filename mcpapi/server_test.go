package mcpapi

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
	"github.com/jackc/pgx/v5"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestToolsUseAuthenticatedStore(t *testing.T) {
	dsn := testDatabase(t)
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	op, err := core.Open(ctx, dsn, core.Channel{Principal: "operator:mcp-test", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	defer op.Close()
	if err = op.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	root, err := op.Bootstrap(ctx, core.BootstrapRequest{RequestID: uuid.NewString(), Reason: "Install isolated MCP test operator"})
	if err != nil {
		t.Fatal(err)
	}
	repo := uuid.NewString()
	mandatory, err := op.Issue(ctx, core.IssueRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "instruction", Body: "Cite selected sources.", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self", Sensitivity: "shareable"}, GrantID: root.ID, Mandatory: true, PolicyKey: "sources", Reason: "Test mandatory MCP context"})
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256([]byte("synthetic-mcp-token"))
	api, err := localapi.New(ctx, dsn, []localapi.Identity{{TokenSHA256: hex.EncodeToString(digest[:]), Principal: "agent:mcp-test", Repo: repo, Role: "agent", Destination: "hosted"}})
	if err != nil {
		t.Fatal(err)
	}
	defer api.Close()
	directory := t.TempDir()
	socket := filepath.Join(directory, "api.sock")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	httpServer := &http.Server{Handler: api}
	served := make(chan error, 1)
	go func() { served <- httpServer.Serve(listener) }()
	defer func() {
		if err := httpServer.Close(); err != nil {
			t.Error(err)
		}
		if err := <-served; !errors.Is(err, http.ErrServerClosed) {
			t.Error(err)
		}
	}()
	token := filepath.Join(directory, "agent.token")
	if err = os.WriteFile(token, []byte("synthetic-mcp-token"), 0600); err != nil {
		t.Fatal(err)
	}
	client, err := localapi.NewClient(socket, token)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	server, err := NewServer(client, Config{Scope: core.Scope{Repo: repo, TaskID: "build", RunID: "attempt"}, AvailableTokens: 64000})
	if err != nil {
		t.Fatal(err)
	}
	session := connect(t, ctx, server)
	listed, err := session.ListTools(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	names := []string{}
	for _, tool := range listed.Tools {
		names = append(names, tool.Name)
	}
	sort.Strings(names)
	if !reflect.DeepEqual(names, []string{"cairn_edit", "cairn_history", "cairn_pull", "cairn_pull_evidence", "cairn_remember", "cairn_search"}) {
		t.Fatal(names)
	}
	invoke := func(name string, args any, wantError string) json.RawMessage {
		t.Helper()
		result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: name, Arguments: args})
		if err != nil {
			t.Fatal(err)
		}
		if len(result.Content) != 1 {
			t.Fatalf("unexpected content: %+v", result)
		}
		body := result.Content[0].(*mcp.TextContent).Text
		if result.IsError != (wantError != "") || (wantError != "" && !strings.Contains(body, wantError)) {
			t.Fatalf("%s: %+v %s", name, result, body)
		}
		return json.RawMessage(body)
	}
	phaseNote := invoke("cairn_remember", map[string]any{
		"request_id": uuid.NewString(), "body": "phaseguide: check the patch before delivery",
		"kind": "procedure", "shareable": true, "pins": map[string]string{"task_phase": "validation"},
		"entities": []map[string]string{{"kind": "file", "name": "core/currentness.go"}},
	}, "")
	var phaseIdentity recordWriteResult
	if err = json.Unmarshal(phaseNote, &phaseIdentity); err != nil {
		t.Fatal(err)
	}
	var phaseView searchResult
	phaseResult := invoke("cairn_search", map[string]any{
		"query": "phaseguide", "context": map[string]string{"task_phase": "validation"},
	}, "")
	if err = json.Unmarshal(phaseResult, &phaseView); err != nil {
		t.Fatal(err)
	}
	if len(phaseView.Index) != 1 || phaseView.Index[0].RecordID != phaseIdentity.RecordID {
		t.Fatalf("per-call phase did not find applicable guidance: %s", phaseResult)
	}
	invoke("cairn_pull", phaseView.Index[0].PullArguments, "")
	entityResult := invoke("cairn_search", map[string]any{
		"entities": []map[string]string{{"kind": "file", "name": "core/currentness.go"}}, "context": map[string]string{"task_phase": "validation"},
	}, "")
	if err = json.Unmarshal(entityResult, &phaseView); err != nil {
		t.Fatal(err)
	}
	if len(phaseView.Index) != 1 || phaseView.Index[0].RecordID != phaseIdentity.RecordID || len(phaseView.Index[0].Entities) != 1 {
		t.Fatalf("entity-only search lost association: %s", entityResult)
	}
	args := rememberArgs{RequestID: uuid.NewString(), Body: "socketguide: verified API connection instructions.\nUnicode 日本語 and literal $(command).", Kind: "lesson", Shareable: true}
	saved := invoke("cairn_remember", args, "")
	if string(saved) != string(invoke("cairn_remember", args, "")) {
		t.Fatal("capture retry changed")
	}
	var identity struct {
		RecordID string `json:"record_id"`
	}
	if err = json.Unmarshal(saved, &identity); err != nil {
		t.Fatal(err)
	}
	record, err := op.Get(ctx, identity.RecordID)
	if err != nil || record.Body != args.Body || record.Class != "A" || record.ObservedWriter != "agent:mcp-test" || record.Witness != "testimony" {
		t.Fatalf("capture: %+v %v", record, err)
	}
	args.Body += " changed"
	invoke("cairn_remember", args, "IDEMPOTENCY_CONFLICT")
	args.RequestID = uuid.NewString()
	args.Shareable = false
	args.Body = "socketguide private material"
	invoke("cairn_remember", args, "")
	args.RequestID = uuid.NewString()
	args.Kind = "instruction"
	invoke("cairn_remember", args, "INVALID_REQUEST")
	invoke("cairn_search", map[string]any{"query": "socketguide", "repo": "outside"}, "additional")
	viewBytes := invoke("cairn_search", searchArgs{Query: "socketguide"}, "")
	var view searchResult
	if err = json.Unmarshal(viewBytes, &view); err != nil {
		t.Fatal(err)
	}
	if view.Schema != "cairn.mcp-search/1" || view.SourceSeal == "" || len(view.Index) != 1 || view.Index[0].RecordID != record.RecordID || len(view.Selected) != 1 || view.Selected[0].Record.RecordID != mandatory.RecordID || view.Scope.TaskID != "build" || view.Destination.Name != "hosted" {
		t.Fatalf("search: %s", viewBytes)
	}
	for _, invalid := range []searchArgs{{}, {Query: " "}, {Browse: true, Query: "socketguide"}, {Browse: true, ErrorSignature: strings.Repeat("b", 64)}} {
		invoke("cairn_search", invalid, "query, entities, error_signature_sha256, or browse=true")
	}
	var browsed searchResult
	var fallback searchResult
	if err = json.Unmarshal(invoke("cairn_search", searchArgs{Query: "socketguide", Semantic: true}, ""), &fallback); err != nil {
		t.Fatal(err)
	}
	if fallback.Discovery == nil || fallback.Discovery.State != "unavailable" || fallback.Status != "DEGRADED_NO_EMBEDDINGS" || fallback.Scope != view.Scope || len(fallback.Index) != len(view.Index) {
		t.Fatalf("semantic fallback lost context: %+v", fallback)
	}
	invoke("cairn_search", searchArgs{Browse: true, Semantic: true}, "cannot be combined")
	if err = json.Unmarshal(invoke("cairn_search", searchArgs{Browse: true}, ""), &browsed); err != nil {
		t.Fatal(err)
	}
	if len(browsed.Index) != 1 || browsed.Index[0].RecordID != record.RecordID || !reflect.DeepEqual(browsed.Selected, view.Selected) || browsed.Scope != view.Scope || browsed.Destination != view.Destination {
		t.Fatalf("browse lost scope, destination, mandatory context or hosted filtering: %+v", browsed)
	}
	var searchPage searchResult
	if err = json.Unmarshal(invoke("cairn_search", map[string]any{"query": "socketguide", "offset": 0}, ""), &searchPage); err != nil || searchPage.Page == nil || searchPage.Page.Offset != 0 || len(searchPage.Index) != 1 || !reflect.DeepEqual(searchPage.Selected, view.Selected) {
		t.Fatalf("MCP ranked first page: %+v %v", searchPage, err)
	}
	var endPage searchResult
	if err = json.Unmarshal(invoke("cairn_search", map[string]any{"browse": true, "offset": 1}, ""), &endPage); err != nil || endPage.Browse == nil || endPage.Browse.Offset != 1 || endPage.Browse.NextOffset != nil || len(endPage.Index) != 0 || !reflect.DeepEqual(endPage.Selected, view.Selected) {
		t.Fatalf("MCP page continuation: %+v %v", endPage, err)
	}
	if err = json.Unmarshal(invoke("cairn_search", map[string]any{"query": "socketguide", "offset": 1, "semantic": true}, ""), &searchPage); err != nil || searchPage.Page == nil || searchPage.Page.Offset != 1 || searchPage.Page.NextOffset != nil || len(searchPage.Index) != 0 || searchPage.Discovery.State != "unavailable" || !reflect.DeepEqual(searchPage.Selected, view.Selected) {
		t.Fatalf("MCP ranked fallback end page: %+v %v", searchPage, err)
	}
	for _, invalid := range []map[string]any{{"query": "socketguide", "offset": -1}, {"browse": true, "offset": -1}, {"browse": true, "offset": 10001}} {
		invoke("cairn_search", invalid, "offset must")
	}
	var expanded core.Expansion
	if err = json.Unmarshal(invoke("cairn_pull", browsed.Index[0].PullArguments, ""), &expanded); err != nil || expanded.Selection.Record.Body != record.Body {
		t.Fatalf("browse pull: %+v %v", expanded, err)
	}
	t.Run("Codex conversation scope", func(t *testing.T) {
		threadNote, err := op.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "socketguide for one conversation", Scope: core.Scope{Repo: repo, TaskID: "codex/thread-one", RunID: "thread-one"}, ClaimType: "self", Sensitivity: "shareable"}})
		if err != nil {
			t.Fatal(err)
		}
		threadServer, err := NewServer(client, Config{Scope: core.Scope{Repo: repo}, CodexThread: true, AvailableTokens: 64000})
		if err != nil {
			t.Fatal(err)
		}
		threadSession := connect(t, ctx, threadServer)
		requestID := uuid.NewString()
		for _, thread := range []string{"thread-one", "thread-two", "thread-one"} {
			params := &mcp.CallToolParams{Meta: mcp.Meta{"threadId": thread}, Name: "cairn_search", Arguments: searchArgs{Query: "socketguide", RequestID: requestID}}
			result, err := threadSession.CallTool(ctx, params)
			if err != nil || result.IsError {
				t.Fatalf("thread search: %+v %v", result, err)
			}
			var got searchResult
			if err := json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &got); err != nil {
				t.Fatal(err)
			}
			if got.Scope != (core.Scope{Repo: repo, TaskID: "codex/" + thread, RunID: thread}) || got.Destination.Name != "hosted" {
				t.Fatalf("scope/destination: %+v", got)
			}
			found := false
			for _, entry := range got.Index {
				found = found || entry.RecordID == threadNote.RecordID
			}
			if found != (thread == "thread-one") {
				t.Fatalf("wrong conversation applicability: %+v", got.Index)
			}
			// Reusing a search UUID in a different conversation must not replay
			// the original conversation's result.
			params.Meta["threadId"] = "different-thread"
			conflict, err := threadSession.CallTool(ctx, params)
			if err != nil || !conflict.IsError || !strings.Contains(conflict.Content[0].(*mcp.TextContent).Text, "IDEMPOTENCY_CONFLICT") {
				t.Fatalf("cross-thread retry: %+v %v", conflict, err)
			}
			requestID = uuid.NewString()
		}
		result, err := session.CallTool(ctx, &mcp.CallToolParams{Meta: mcp.Meta{"threadId": "thread-one"}, Name: "cairn_search", Arguments: searchArgs{Query: "socketguide"}})
		if err != nil || result.IsError {
			t.Fatalf("explicit scope: %+v %v", result, err)
		}
		var explicit searchResult
		if err := json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &explicit); err != nil || explicit.Scope != view.Scope {
			t.Fatalf("metadata changed explicit scope: %+v %v", explicit.Scope, err)
		}
	})
	pulled := invoke("cairn_pull", view.Index[0].PullArguments, "")
	if string(invoke("cairn_pull", view.Index[0].PullArguments, "")) != string(pulled) {
		t.Fatal("pull retry changed")
	}
	var expansion core.Expansion
	if err = json.Unmarshal(pulled, &expansion); err != nil || expansion.Selection.Record.RecordID != record.RecordID || expansion.Selection.Record.Version != record.Version || expansion.Selection.Record.Body != record.Body || expansion.Selection.Record.ObservedWriter != record.ObservedWriter || !expansion.Selection.Record.WrittenAt.Equal(record.WrittenAt) {
		t.Fatalf("pull: %s %v", pulled, err)
	}
	if expansion.CreditsRemaining != view.CreditsRemaining-1 {
		t.Fatal("wrong expansion credit spend")
	}
	edited := record.Draft
	edited.Body += " newer"
	edit := core.EditRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, Draft: edited}
	changed := invoke("cairn_edit", edit, "")
	if string(invoke("cairn_edit", edit, "")) != string(changed) {
		t.Fatal("edit retry changed")
	}
	var change recordWriteResult
	if err = json.Unmarshal(changed, &change); err != nil || change.RecordID != record.RecordID || change.Version != 2 || change.RequestID != edit.RequestID || strings.Contains(string(changed), "body") {
		t.Fatalf("edit response: %s, %v", changed, err)
	}
	editedRecord, err := op.Get(ctx, record.RecordID)
	if err != nil || editedRecord.Body != edited.Body || editedRecord.Version != 2 || editedRecord.ObservedWriter != "agent:mcp-test" || editedRecord.Class != "A" || editedRecord.Witness != "testimony" {
		t.Fatalf("edit stored wrong version or writer: %+v, %v", editedRecord, err)
	}
	edit.Draft.Body += " changed retry"
	invoke("cairn_edit", edit, "IDEMPOTENCY_CONFLICT")
	edit.RequestID = uuid.NewString()
	invoke("cairn_edit", edit, "VERSION_CONFLICT")
	edit.ExpectedVersion = 2
	for _, field := range []string{"repo", "scope", "sensitivity", "pins"} {
		refused := edit
		refused.RequestID = uuid.NewString()
		switch field {
		case "repo":
			refused.Draft.Scope.Repo = "outside"
		case "scope":
			refused.Draft.Scope.TaskID = "different-task"
		case "sensitivity":
			refused.Draft.Sensitivity = "local"
		case "pins":
			refused.Draft.Pins = &core.Applicability{TaskClass: "different-class"}
		}
		invoke("cairn_edit", refused, "AUTHORITY_DENIED")
	}
	invoke("cairn_pull", view.Index[0].PullArguments, "STALE_HANDLE")
	bodyOnly, err := op.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: record.Draft})
	if err != nil {
		t.Fatal(err)
	}
	newBody := "Body-only correction, preserving the saved note's metadata."
	revise := editArgs{RequestID: uuid.NewString(), RecordID: bodyOnly.RecordID, ExpectedVersion: 1, Body: &newBody}
	result := invoke("cairn_edit", revise, "")
	if string(invoke("cairn_edit", revise, "")) != string(result) || strings.Contains(string(result), "body") {
		t.Fatalf("body revision retry or disclosure: %s", result)
	}
	updatedBody, err := op.Get(ctx, bodyOnly.RecordID)
	if err != nil || updatedBody.Version != 2 || updatedBody.Body != newBody || updatedBody.Scope != bodyOnly.Scope || updatedBody.Sensitivity != bodyOnly.Sensitivity {
		t.Fatalf("body-only revision: %+v %v", updatedBody, err)
	}
	var history core.RecordHistory
	if err := json.Unmarshal(invoke("cairn_history", core.RecordHistoryRequest{RecordID: bodyOnly.RecordID, Version: 1}, ""), &history); err != nil {
		t.Fatal(err)
	}
	if !history.Historical || history.CurrentVersion != 2 || len(history.Versions) != 1 || history.Versions[0].Body == nil || *history.Versions[0].Body != bodyOnly.Body {
		t.Fatalf("history did not preserve the prior wording: %+v", history)
	}
	invoke("cairn_history", map[string]any{"record_id": bodyOnly.RecordID, "repo": repo}, "additional")
	revise.RequestID = uuid.NewString()
	invoke("cairn_edit", revise, "VERSION_CONFLICT")
	revise.Draft = &bodyOnly.Draft
	invoke("cairn_edit", revise, "supply exactly one")
	revise.Draft, revise.Body = nil, nil
	invoke("cairn_edit", revise, "supply exactly one")
	evidence, err := op.CaptureEvidence(ctx, core.EvidenceRequest{RequestID: uuid.NewString(), Repo: repo, Body: "selected supporting bytes", Source: "MCP integration fixture", Sensitivity: "shareable"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.Promote(ctx, core.PromoteRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: 2, GrantID: root.ID, EvidenceIDs: []string{evidence.ID}, Reason: "Independently supported fixture"}); err != nil {
		t.Fatal(err)
	}
	edit.RequestID = uuid.NewString()
	edit.ExpectedVersion = 3
	invoke("cairn_edit", edit, "AUTHORITY_DENIED")
	privileged := core.EditRequest{RequestID: uuid.NewString(), RecordID: mandatory.RecordID, ExpectedVersion: mandatory.Version, Draft: mandatory.Draft}
	privileged.Draft.Kind = "note"
	invoke("cairn_edit", privileged, "AUTHORITY_DENIED")
	invoke("cairn_edit", editArgs{RequestID: uuid.NewString(), RecordID: mandatory.RecordID, ExpectedVersion: mandatory.Version, Body: &newBody}, "AUTHORITY_DENIED")
	invoke("cairn_edit", editArgs{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: 3, Body: &newBody}, "AUTHORITY_DENIED")
	if err = json.Unmarshal(invoke("cairn_search", searchArgs{Query: "socketguide"}, ""), &view); err != nil {
		t.Fatal(err)
	}
	evidenceArgs := core.ExpandEvidenceRequest{RequestID: uuid.NewString(), ReceiptID: view.ReceiptID, Handle: view.Index[0].PullArguments.Handle, EvidenceID: evidence.ID, ExpectedSHA256: evidence.Digest}
	evidenceBytes := invoke("cairn_pull_evidence", evidenceArgs, "")
	if !strings.Contains(string(evidenceBytes), "selected supporting bytes") {
		t.Fatalf("missing evidence: %s", evidenceBytes)
	}
	if string(invoke("cairn_pull_evidence", evidenceArgs, "")) != string(evidenceBytes) {
		t.Fatal("evidence retry changed")
	}
	evidenceArgs.ExpectedSHA256 = strings.Repeat("0", 64)
	evidenceArgs.RequestID = uuid.NewString()
	invoke("cairn_pull_evidence", evidenceArgs, "EVIDENCE_UNAVAILABLE")
	outside, err := NewServer(client, Config{Scope: core.Scope{Repo: "outside", TaskID: "task", RunID: "run"}, AvailableTokens: 64000})
	if err != nil {
		t.Fatal(err)
	}
	denied, err := connect(t, ctx, outside).CallTool(ctx, &mcp.CallToolParams{Name: "cairn_search", Arguments: searchArgs{Query: "socketguide"}})
	if err != nil || !denied.IsError || !strings.Contains(denied.Content[0].(*mcp.TextContent).Text, "AUTHORITY_DENIED") {
		t.Fatalf("outside scope: %+v %v", denied, err)
	}
	denied, err = connect(t, ctx, outside).CallTool(ctx, &mcp.CallToolParams{Name: "cairn_history", Arguments: core.RecordHistoryRequest{RecordID: bodyOnly.RecordID}})
	if err != nil || !denied.IsError || !strings.Contains(denied.Content[0].(*mcp.TextContent).Text, "AUTHORITY_DENIED") {
		t.Fatalf("history ignored configured repository: %+v %v", denied, err)
	}
}

// CI shares its service database across packages. Bootstrap this fixture in its
// own database instead of depending on another package's operator root.
func testDatabase(t *testing.T) string {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	host, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := host.Close(ctx); err != nil {
			t.Error(err)
		}
	})
	database := "cairn_mcp_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, err = host.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{database}.Sanitize()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := host.Exec(ctx, "DROP DATABASE "+pgx.Identifier{database}.Sanitize()); err != nil {
			t.Error(err)
		}
	})
	if strings.HasPrefix(dsn, "postgres://") || strings.HasPrefix(dsn, "postgresql://") {
		u, err := url.Parse(dsn)
		if err != nil {
			t.Fatal(err)
		}
		u.Path = "/" + database
		u.RawPath = ""
		query := u.Query()
		query.Del("dbname")
		u.RawQuery = query.Encode()
		return u.String()
	}
	return dsn + " dbname=" + database
}

func connect(t *testing.T, ctx context.Context, server *mcp.Server) *mcp.ClientSession {
	t.Helper()
	clientTransport, serverTransport := mcp.NewInMemoryTransports()
	serverSession, err := server.Connect(ctx, serverTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	session, err := mcp.NewClient(&mcp.Implementation{Name: "cairn-test-client", Version: "1"}, nil).Connect(ctx, clientTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { session.Close(); serverSession.Close() })
	return session
}

func TestToolResultBoundsAndErrors(t *testing.T) {
	if _, _, err := toolResult(strings.Repeat("\"", 300), nil, 300); err == nil || !strings.Contains(err.Error(), "BUDGET_REFUSED") {
		t.Fatal("unbounded encoded content")
	}
	if _, _, err := toolResult(nil, errors.New("secret connection value"), 32000); err == nil || strings.Contains(err.Error(), "secret") {
		t.Fatal("raw transport error leaked")
	}
	if _, _, err := toolResult(nil, &core.Error{Code: "AUTHORITY_DENIED", Message: "AUTHORITY_DENIED: outside profile", RefusalID: "retained-refusal"}, 32000); err == nil || err.Error() != "AUTHORITY_DENIED: outside profile; refusal_id=retained-refusal" {
		t.Fatal("API refusal lost")
	}
}

func TestCodexThreadRequiresValidMetadata(t *testing.T) {
	// No API client: malformed metadata must fail before any database request.
	server, err := NewServer(nil, Config{Scope: core.Scope{Repo: "repo"}, CodexThread: true, AvailableTokens: 32000})
	if err != nil {
		t.Fatal(err)
	}
	session := connect(t, context.Background(), server)
	for _, thread := range []any{nil, "", "*", 42, []string{"thread"}, "two words", "line\nbreak", "control\x00", "space\u00a0", strings.Repeat("x", 241)} {
		result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Meta: mcp.Meta{"threadId": thread}, Name: "cairn_search", Arguments: searchArgs{Query: "note"}})
		if err != nil || !result.IsError || !strings.Contains(result.Content[0].(*mcp.TextContent).Text, "requires tool-call _meta.threadId") {
			t.Fatalf("metadata %q: %+v %v", thread, result, err)
		}
	}
}
