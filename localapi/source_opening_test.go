package localapi

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestOpeningReaderThroughRelay(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	admin, err := core.Open(ctx, dsn, core.Channel{Principal: "opening-reader-test", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	if err = admin.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	token := "synthetic-opening-reader"
	digest := sha256.Sum256([]byte(token))
	repo := uuid.NewString()
	api, err := New(ctx, dsn, []Identity{{TokenSHA256: hex.EncodeToString(digest[:]), Principal: "machine:opening/agent", Repo: repo, Role: "agent", Destination: "hosted", Remote: true, MachineID: "opening"}})
	if err != nil {
		t.Fatal(err)
	}
	defer api.Close()
	body := "Project API fixture. " + strings.Repeat("context ", 40) + "tail"
	upstream := httptest.NewTLSServer(api.RemoteHandler())
	defer upstream.Close()
	relay := trustedRelay(t, upstream)
	call := func(op string, value any, headers []string, auth string) (int, json.RawMessage) {
		t.Helper()
		encoded, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		req := httptest.NewRequest("POST", "/v1/"+op, strings.NewReader(string(encoded)))
		req.Header.Set("Authorization", "Bearer "+auth)
		for _, h := range headers {
			req.Header.Add(SourceOpeningHeader, h)
		}
		w := httptest.NewRecorder()
		relay.ServeHTTP(w, req)
		var result struct {
			Data   json.RawMessage `json:"data"`
			Status string          `json:"status"`
		}
		if err = json.Unmarshal(w.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		if w.Code != 200 && result.Status != "PROTOCOL_UNSUPPORTED" && result.Status != "INVALID_REQUEST" && w.Code != 401 {
			t.Fatalf("unexpected refusal: %d %s", w.Code, w.Body.String())
		}
		return w.Code, result.Data
	}
	draft := core.Draft{Kind: "note", Body: body, Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, Sensitivity: "shareable", ClaimType: "self"}
	if code, _ := call("create", core.CreateRequest{RequestID: uuid.NewString(), Draft: draft}, nil, token); code != 200 {
		t.Fatal(code)
	}
	code, data := call("index", core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 64000}, nil, token)
	var idx core.IndexResult
	if code != 200 {
		t.Fatal(code)
	}
	if err = json.Unmarshal(data, &idx); err != nil || len(idx.Handles) != 1 {
		t.Fatal(err)
	}
	pull := core.ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: idx.Handles[0].Handle, Span: &core.ByteSpanRequest{Offset: len(body) - 4, Length: 4}}
	for _, headers := range [][]string{nil, {""}, {"0"}, {"01"}, {"2"}, {"1, 1"}, {"1", "1"}} {
		want := 400
		if headers == nil {
			want = 426
		}
		if code, _ := call("expand", pull, headers, token); code != want {
			t.Fatalf("headers %v: %d want %d", headers, code, want)
		}
	}
	if code, _ := call("expand", pull, []string{"1"}, "wrong"); code != 401 {
		t.Fatalf("declaration conferred authority: %d", code)
	}
	code, data = call("expand", pull, []string{"1"}, token)
	var result core.Expansion
	if code != 200 {
		t.Fatal(code)
	}
	if err = json.Unmarshal(data, &result); err != nil || result.SourceOpening == nil || result.SourceOpening.Body != body[:96] || result.CreditsRemaining != 3 {
		t.Fatalf("pair not preserved: %s %v", data, err)
	}
	if code, _ := call("expand", pull, nil, token); code != 426 {
		t.Fatalf("cached pair admitted old reader: %d", code)
	}
	code, again := call("expand", pull, []string{"1"}, token)
	if code != 200 || string(data) != string(again) {
		t.Fatalf("changed retry: %d %s", code, again)
	}
	pull.RequestID, pull.Span = uuid.NewString(), nil
	if code, _ := call("expand", pull, nil, token); code != http.StatusOK {
		t.Fatalf("whole note unavailable: %d", code)
	}
}
