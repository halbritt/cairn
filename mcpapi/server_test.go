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
	if !reflect.DeepEqual(names, []string{"cairn_edit", "cairn_pull", "cairn_pull_evidence", "cairn_remember", "cairn_search"}) {
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
