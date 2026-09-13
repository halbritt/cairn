package localapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestIndexReaderRequiresConfiguredOrdinaryPeer(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	repo := uuid.NewString()
	op, err := core.Open(ctx, dsn, core.Channel{Principal: "index-reader-test", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	defer op.Close()
	if err = op.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	var identities []Identity
	for _, identity := range []Identity{
		{Principal: "observer", Repo: repo, Role: "observer", Destination: "hosted"},
		{Principal: "reader", Repo: repo, Role: "agent", Destination: "hosted"},
		{Principal: "local", Repo: repo, Role: "agent", Destination: "local"},
		{Principal: "foreign", Repo: "foreign", Role: "agent", Destination: "hosted"},
		{Principal: "other-host", Repo: repo, Role: "observer", Destination: "hosted"},
	} {
		digest := sha256.Sum256([]byte("synthetic-" + identity.Principal))
		identity.TokenSHA256 = hex.EncodeToString(digest[:])
		identities = append(identities, identity)
	}
	server, err := New(ctx, dsn, identities)
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	call := func(caller, endpoint string, payload any, result any) string {
		t.Helper()
		body, err := json.Marshal(payload)
		if err != nil {
			t.Fatal(err)
		}
		r := httptest.NewRequest("POST", "/v1/"+endpoint, bytes.NewReader(body))
		r.Header.Set("Authorization", "Bearer synthetic-"+caller)
		w := httptest.NewRecorder()
		server.ServeHTTP(w, r)
		var envelope struct {
			Status string          `json:"status"`
			Data   json.RawMessage `json:"data"`
		}
		if err := json.Unmarshal(w.Body.Bytes(), &envelope); err != nil {
			t.Fatal(err)
		}
		if result != nil && envelope.Status == "OK" {
			if err := json.Unmarshal(envelope.Data, result); err != nil {
				t.Fatal(err)
			}
		}
		return envelope.Status
	}
	var record core.Record
	if code := call("reader", "create", core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "Shared index source", Sensitivity: "shareable", ClaimType: "self", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}}, &record); code != "OK" {
		t.Fatal(code)
	}
	req := core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 32000}
	for _, reader := range []string{"missing", "local", "foreign", "other-host"} {
		req.ExpansionReader = reader
		if code := call("observer", "index", req, nil); code != "AUTHORITY_DENIED" {
			t.Fatalf("accepted reader %q: %s", reader, code)
		}
	}
	req.ExpansionReader = "reader"
	if code := call("reader", "index", req, nil); code != "AUTHORITY_DENIED" {
		t.Fatal(code)
	}
	var index core.IndexResult
	if code := call("observer", "index", req, &index); code != "OK" {
		t.Fatal(code)
	}
	if index.ExpansionReader != "reader" || len(index.Handles) != 1 {
		t.Fatalf("index: %+v", index)
	}
	var pulled core.Expansion
	pull := core.ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: index.Handles[0].Handle}
	if code := call("reader", "expand", pull, &pulled); code != "OK" || pulled.Selection.Record.Body != record.Body {
		t.Fatalf("pull: %s %+v", code, pulled)
	}
	ref := core.RunPackageRequest{ReceiptID: index.Package.ReceiptID, Seal: index.Package.Seal}
	if code := call("reader", "run-index", ref, nil); code != "AUTHORITY_DENIED" {
		t.Fatal(code)
	}
	if code := call("observer", "run-index", ref, &index); code != "OK" || index.CreditsRemaining != 3 {
		t.Fatalf("retained: %s %+v", code, index)
	}
}
