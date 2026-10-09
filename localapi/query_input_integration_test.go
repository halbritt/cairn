package localapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestQueryInputAPIDeclaresBackendAndReplaysWithoutIt(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	admin, err := core.Open(ctx, dsn, core.Channel{Principal: "query-cap-test", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	if err = admin.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	repo := uuid.NewString()
	if _, err = admin.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "storage guidance", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self", Sensitivity: "shareable"}}); err != nil {
		t.Fatal(err)
	}
	token := "synthetic-query-token"
	hash := sha256.Sum256([]byte(token))
	identities := []Identity{{TokenSHA256: hex.EncodeToString(hash[:]), Principal: "agent:query-api", Repo: repo, Role: "agent", Destination: "hosted"}}
	calls := 0
	server, err := NewWithSemanticRetriever(ctx, dsn, identities, func(context.Context, core.SemanticRankRequest) (core.SemanticRetrievalResult, error) {
		calls++
		return core.SemanticRetrievalResult{}, errors.New("fixture offline")
	})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	call := func(s *Server, path string, body any, target any) int {
		t.Helper()
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		req := httptest.NewRequest("POST", path, bytes.NewReader(raw))
		req.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		s.ServeHTTP(w, req)
		var envelope struct {
			Data   json.RawMessage `json:"data"`
			Status string          `json:"status"`
		}
		if err = json.Unmarshal(w.Body.Bytes(), &envelope); err != nil {
			t.Fatal(err)
		}
		if w.Code == 200 && target != nil {
			if err = json.Unmarshal(envelope.Data, target); err != nil {
				t.Fatal(err)
			}
		}
		return w.Code
	}
	var version VersionResponse
	if code := call(server, "/v1/version", struct{}{}, &version); code != 200 || version.QueryInputCapabilities == nil || !version.QueryInputCapabilities.IndexedSemanticQueryV1 {
		t.Fatal("configured indexed compile not declared")
	}
	req := core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Query: "storage", SemanticQuery: "Do not use public storage.", Semantic: true, Purpose: "context", AvailableTokens: 32000}
	var index core.IndexResult
	if code := call(server, "/v1/index", req, &index); code != 200 || calls != 1 || index.Package.Semantic.Schema != core.DistinctQuerySchema {
		t.Fatalf("opted-in API compile: code%d calls%d", code, calls)
	}
	without, err := New(ctx, dsn, identities)
	if err != nil {
		t.Fatal(err)
	}
	defer without.Close()
	if code := call(without, "/v1/version", struct{}{}, &version); code != 200 || version.QueryInputCapabilities == nil || version.QueryInputCapabilities.IndexedSemanticQueryV1 {
		t.Fatal("no-backend declaration missing replay/false compile")
	}
	var replay struct {
		Historical bool         `json:"historical"`
		Package    core.Package `json:"package"`
	}
	if code := call(without, "/v1/recompile", core.RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query, SemanticQuery: req.SemanticQuery}, &replay); code != 200 || !replay.Historical || replay.Package.Seal != index.Package.Seal || replay.Package.Semantic.Schema != core.DistinctQuerySchema || calls != 1 {
		t.Fatalf("backend-free API replay: code%d", code)
	}
	req.RequestID = uuid.NewString()
	if code := call(without, "/v1/index", req, nil); code != 422 {
		t.Fatalf("no-backend compile accepted: %d", code)
	}
}
