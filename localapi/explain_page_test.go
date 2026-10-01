package localapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"sort"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5"
)

type explanationAPIFixture struct {
	server *Server
	admin  *core.Store
	db     *pgx.Conn
	repo   string
}

func newExplanationAPIFixture(t *testing.T) *explanationAPIFixture {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	admin, err := core.Open(ctx, dsn, core.Channel{Principal: "operator:tests", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(admin.Close)
	if err = admin.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	f := &explanationAPIFixture{admin: admin, repo: uuid.NewString()}
	f.db, err = pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := f.db.Close(context.Background()); err != nil {
			t.Error(err)
		}
	})
	var identities []Identity
	for _, x := range []struct{ token, principal, repo, dest string }{
		{"owner", "candidate-owner", f.repo, "hosted"},
		{"foreign", "candidate-foreign", f.repo, "hosted"},
	} {
		h := sha256.Sum256([]byte("synthetic-" + x.token))
		identities = append(identities, Identity{TokenSHA256: hex.EncodeToString(h[:]), Principal: x.principal, Repo: x.repo, Role: "agent", Destination: x.dest})
	}
	f.server, err = New(ctx, dsn, identities)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(f.server.Close)
	return f
}

func (f *explanationAPIFixture) call(t *testing.T, token, operation string, request any) (string, json.RawMessage, []byte) {
	t.Helper()
	b, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest("POST", "/v1/"+operation, bytes.NewReader(b))
	if token != "" {
		req.Header.Set("Authorization", "Bearer synthetic-"+token)
	}
	response := httptest.NewRecorder()
	f.server.ServeHTTP(response, req)
	var envelope struct {
		Status string          `json:"status"`
		Data   json.RawMessage `json:"data"`
	}
	if err = json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	return envelope.Status, envelope.Data, append([]byte(nil), response.Body.Bytes()...)
}

func (f *explanationAPIFixture) note(t *testing.T, body string) core.Record {
	t.Helper()
	r, err := f.admin.Create(context.Background(), core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: body, ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: f.repo, TaskID: "*", RunID: "*"}}})
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func (f *explanationAPIFixture) index(t *testing.T) core.IndexResult {
	t.Helper()
	status, data, _ := f.call(t, "owner", "index", core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: f.repo, TaskID: "original", RunID: "original"}, Purpose: "context", Query: "needletarget", AvailableTokens: 32000})
	if status != "OK" {
		t.Fatalf("fixture index: %s %s", status, data)
	}
	var index core.IndexResult
	if err := json.Unmarshal(data, &index); err != nil {
		t.Fatal(err)
	}
	return index
}

func TestExplainPageRefusesOffPageUnrankedPrivateCandidate(t *testing.T) {
	f := newExplanationAPIFixture(t)
	var notes []core.Record
	for i := 0; i < 3; i++ {
		notes = append(notes, f.note(t, "UNMATCHED-PRIVATE-CANARY"))
	}
	sort.Slice(notes, func(i, j int) bool { return notes[i].RecordID < notes[j].RecordID })
	index := f.index(t)
	request := map[string]any{"receipt_id": index.Package.ReceiptID, "limit": 1}
	status, data, _ := f.call(t, "owner", "explain-page", request)
	if status != "OK" {
		t.Fatalf("initial inspection: %s %s", status, data)
	}
	var page struct {
		Candidates []struct {
			RecordID string `json:"record_id"`
			Rank     int    `json:"rank"`
			Reason   string `json:"reason"`
		} `json:"candidates"`
		More bool `json:"more"`
	}
	if err := json.Unmarshal(data, &page); err != nil {
		t.Fatal(err)
	}
	if len(page.Candidates) != 1 || page.Candidates[0].RecordID != notes[0].RecordID || page.Candidates[0].Rank != 0 || page.Candidates[0].Reason != "NO_LEXICAL_MATCH" || !page.More {
		t.Fatalf("fixture must contain ordered unranked candidates: %s", data)
	}
	// The next page must recheck even a rank-zero candidate beyond that page.
	request["after_record_id"] = notes[0].RecordID
	request["after_version"] = 1
	// Sensitivity has no ordinary mutation API. Restrict this owned fixture directly.
	if _, err := f.db.Exec(context.Background(), `UPDATE cairn.memory_record SET sensitivity='local' WHERE record_id=$1`, notes[2].RecordID); err != nil {
		t.Fatal(err)
	}
	status, data, wire := f.call(t, "owner", "explain-page", request)
	if status != "PAYLOAD_UNAVAILABLE" || (len(data) > 0 && string(data) != "null") {
		t.Fatalf("off-page privacy disclosed data: %s %s", status, wire)
	}
	for _, forbidden := range []string{notes[0].RecordID, notes[2].RecordID, "UNMATCHED-PRIVATE-CANARY", `"candidates"`, `"more"`, `"next_record_id"`} {
		if bytes.Contains(wire, []byte(forbidden)) {
			t.Fatalf("refusal leaked %q: %s", forbidden, wire)
		}
	}
}

func TestExplainPageRefusesDeletedHistoricalVersionAndReceipt(t *testing.T) {
	f := newExplanationAPIFixture(t)
	ctx := context.Background()
	note := f.note(t, "ORIGINAL-HISTORY-CANARY")
	index := f.index(t)
	if _, err := f.admin.Revise(ctx, core.ReviseRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 1, Repo: f.repo, Body: "CURRENT-VERSION-REMAINS-AVAILABLE"}); err != nil {
		t.Fatal(err)
	}
	// Obtain a real forgetting marker from a separate, later source. It is not a
	// member of the receipt under test and must not itself invalidate that receipt.
	// The package shares one database: reuse the canonical bootstrap request
	// and principal used by refusal_details_test.go and the core fixtures.
	grant, err := f.admin.Bootstrap(ctx, core.BootstrapRequest{RequestID: "82bf5bce-dc6c-4d03-a49f-1677bdcc9a32", Reason: "Install synthetic test operator root"})
	if err != nil {
		t.Fatal(err)
	}
	sacrificial := f.note(t, "later sacrificial deletion fixture")
	preview, err := f.admin.PreviewDeletion(ctx, sacrificial.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	deletion, err := f.admin.Forget(ctx, core.ForgetRequest{RequestID: uuid.NewString(), RecordID: sacrificial.RecordID, ExpectedVersion: 1, GrantID: grant.ID, PreviewID: preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	request := map[string]any{"receipt_id": index.Package.ReceiptID}
	if status, data, _ := f.call(t, "owner", "explain-page", request); status != "OK" {
		t.Fatalf("unrelated later forgetting changed inspection: %s %s", status, data)
	}
	// Model exclusion of only the old payload: the current version stays readable.
	if _, err = f.db.Exec(ctx, `UPDATE cairn.record_version SET payload_deleted_by=$2 WHERE record_id=$1 AND version=1`, note.RecordID, deletion.DeletionID); err != nil {
		t.Fatal(err)
	}
	if status, data, _ := f.call(t, "owner", "history", map[string]any{"record_id": note.RecordID, "version": 2}); status != "OK" || !bytes.Contains(data, []byte("CURRENT-VERSION-REMAINS-AVAILABLE")) {
		t.Fatalf("current version fixture unavailable: %s %s", status, data)
	}
	status, data, oldWire := f.call(t, "owner", "explain-page", request)
	if status != "PAYLOAD_UNAVAILABLE" || (len(data) > 0 && string(data) != "null") || bytes.Contains(oldWire, []byte(note.RecordID)) || bytes.Contains(oldWire, []byte("ORIGINAL-HISTORY-CANARY")) {
		t.Fatalf("excluded historical candidate disclosed: %s %s", status, oldWire)
	}
	// A new receipt can reference the available current version, then become
	// unavailable independently through its receipt-level deletion marker.
	current := f.index(t)
	request = map[string]any{"receipt_id": current.Package.ReceiptID}
	if status, data, _ := f.call(t, "owner", "explain-page", request); status != "OK" {
		t.Fatalf("current receipt fixture unavailable: %s %s", status, data)
	}
	if _, err = f.db.Exec(ctx, `UPDATE cairn.retrieval_receipt SET payload_deleted_by=$2 WHERE receipt_id=$1`, current.Package.ReceiptID, deletion.DeletionID); err != nil {
		t.Fatal(err)
	}
	status, data, receiptWire := f.call(t, "owner", "explain-page", request)
	if status != "PAYLOAD_UNAVAILABLE" || (len(data) > 0 && string(data) != "null") || !bytes.Equal(oldWire, receiptWire) {
		t.Fatalf("receipt deletion changed protected refusal shape: historical=%s receipt=%s", oldWire, receiptWire)
	}
}

func TestExplainPageKeepsAuthenticatedOwnerScopeAndDestination(t *testing.T) {
	f := newExplanationAPIFixture(t)
	note := f.note(t, "needletarget PRIVATE-RESPONSE-CANARY")
	index := f.index(t)
	ctx := context.Background()
	owner, err := core.Open(ctx, os.Getenv("CAIRN_TEST_DATABASE_URL"), core.Channel{Principal: "candidate-owner"})
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	compile := func(repo string, dest core.Destination) string {
		t.Helper()
		p, e := owner.Compile(ctx, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "original", RunID: "original"}, Purpose: "context", AvailableTokens: 32000}, dest)
		if e != nil {
			t.Fatal(e)
		}
		return p.ReceiptID
	}
	localReceipt := compile(f.repo, core.Destination{Name: "local", AllowLocal: true})
	foreignRepoReceipt := compile(uuid.NewString(), core.Destination{Name: "hosted"})
	tests := []struct {
		name, token, code string
		request           map[string]any
	}{
		{"no authentication", "", "AUTHORITY_DENIED", map[string]any{"receipt_id": index.Package.ReceiptID}},
		{"different owner", "foreign", "AUTHORITY_DENIED", map[string]any{"receipt_id": index.Package.ReceiptID}},
		{"wrong repository", "owner", "AUTHORITY_DENIED", map[string]any{"receipt_id": foreignRepoReceipt}},
		{"wrong destination", "owner", "AUTHORITY_DENIED", map[string]any{"receipt_id": localReceipt}},
		{"principal override", "owner", "INVALID_REQUEST", map[string]any{"receipt_id": index.Package.ReceiptID, "principal": "operator"}},
		{"destination override", "owner", "INVALID_REQUEST", map[string]any{"receipt_id": index.Package.ReceiptID, "destination": map[string]any{"name": "local", "allow_local": true}}},
		{"query override", "owner", "INVALID_REQUEST", map[string]any{"receipt_id": index.Package.ReceiptID, "query": "PRIVATE-QUERY-CANARY"}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			status, data, wire := f.call(t, tc.token, "explain-page", tc.request)
			if status != tc.code || (len(data) > 0 && string(data) != "null") {
				t.Fatalf("authorization returned data: %s %s", status, wire)
			}
			for _, canary := range []string{note.RecordID, "PRIVATE-RESPONSE-CANARY", "PRIVATE-QUERY-CANARY"} {
				if bytes.Contains(wire, []byte(canary)) {
					t.Fatalf("refusal disclosed %q: %s", canary, wire)
				}
			}
		})
	}
	status, data, _ := f.call(t, "owner", "explain-page", map[string]any{"receipt_id": index.Package.ReceiptID})
	if status != "OK" || !bytes.Contains(data, []byte(note.RecordID)) {
		t.Fatalf("owner cannot inspect retained reference: %s %s", status, data)
	}
}

func TestExplainPagePreservesHistoricalMembershipWithinWireBoundWithoutWrites(t *testing.T) {
	f := newExplanationAPIFixture(t)
	ctx := context.Background()
	originals := map[string]int{}
	var first core.Record
	for i := 0; i < 105; i++ {
		r := f.note(t, fmt.Sprintf("needletarget BODY-ONLY-CANARY-%03d", i))
		originals[r.RecordID] = r.Version
		if i == 0 {
			first = r
		}
	}
	index := f.index(t)
	if _, err := f.admin.Revise(ctx, core.ReviseRequest{RequestID: uuid.NewString(), RecordID: first.RecordID, ExpectedVersion: 1, Repo: f.repo, Body: "later replacement BODY-ONLY-CANARY"}); err != nil {
		t.Fatal(err)
	}
	later := f.note(t, "needletarget LATER-NOTE-CANARY")
	// This scoped state witness covers the receipt's authority to expand plus
	// observation/mutation counts. Only fixture setup writes have occurred so far.
	state := func() string {
		t.Helper()
		var value string
		err := f.db.QueryRow(ctx, `SELECT jsonb_build_array(
   (SELECT count(*) FROM cairn.retrieval_receipt),
   (SELECT count(*) FROM cairn.mutation_request),
   (SELECT count(*) FROM cairn.record_use),
   (SELECT count(*) FROM cairn.delivery_receipt),
   (SELECT count(*) FROM cairn.usage_observation),
   (SELECT to_jsonb(i) FROM cairn.index_session i WHERE receipt_id=$1),
   (SELECT jsonb_agg(to_jsonb(v) ORDER BY v.record_id,v.version) FROM cairn.record_version v WHERE v.repo=$2)
  )::text`, index.Package.ReceiptID, f.repo).Scan(&value)
		if err != nil {
			t.Fatal(err)
		}
		return value
	}
	before := state()
	request := map[string]any{"receipt_id": index.Package.ReceiptID, "limit": 100}
	seen := map[string]bool{}
	var firstWire []byte
	pages := 0
	for {
		status, data, wire := f.call(t, "owner", "explain-page", request)
		if status != "OK" {
			t.Fatalf("page refused: %s %s", status, wire)
		}
		if len(wire) > 32*1024 || len(data) > 30*1024 {
			t.Fatalf("encoded limits exceeded: wire=%d data=%d", len(wire), len(data))
		}
		for _, forbidden := range []string{"BODY-ONLY-CANARY", "LATER-NOTE-CANARY", "needletarget", later.RecordID, `"facts"`, `"evidence"`, `"authority"`, `"handle"`} {
			if bytes.Contains(wire, []byte(forbidden)) {
				t.Fatalf("unexpected disclosure %q", forbidden)
			}
		}
		var page core.ExplanationPage
		if err := json.Unmarshal(data, &page); err != nil {
			t.Fatal(err)
		}
		if !page.Historical || page.Coverage != "retained_evaluated_set" || len(page.Candidates) == 0 {
			t.Fatalf("page lacks historical coverage: %s", data)
		}
		if pages == 0 {
			firstWire = wire
			if len(page.Candidates) >= 100 || !page.More {
				t.Fatalf("fixture did not exercise byte-limited whole-row page: %d rows, more=%v", len(page.Candidates), page.More)
			}
		}
		for _, row := range page.Candidates {
			if originals[row.RecordID] != row.Version || seen[row.RecordID] {
				t.Fatalf("historical membership changed: %+v", row)
			}
			seen[row.RecordID] = true
		}
		pages++
		if !page.More {
			if page.NextAfterRecordID != "" || page.NextAfterVersion != 0 {
				t.Fatal("end page has cursor")
			}
			break
		}
		if pages > 5 {
			t.Fatal("pagination made no bounded progress")
		}
		last := page.Candidates[len(page.Candidates)-1]
		if page.NextAfterRecordID != last.RecordID || page.NextAfterVersion != last.Version {
			t.Fatal("cursor did not follow last whole row")
		}
		request["after_record_id"] = page.NextAfterRecordID
		request["after_version"] = page.NextAfterVersion
	}
	if len(seen) != len(originals) {
		t.Fatalf("lost original candidates: %d/%d", len(seen), len(originals))
	}
	status, _, repeated := f.call(t, "owner", "explain-page", map[string]any{"receipt_id": index.Package.ReceiptID, "limit": 100})
	if status != "OK" || !bytes.Equal(firstWire, repeated) {
		t.Fatal("identical first-page read changed")
	}
	if after := state(); after != before {
		t.Fatal("inspection changed receipts, expansion allowance, observations, mutations or versions")
	}
}
