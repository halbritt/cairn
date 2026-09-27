package localapi

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

// Use a real committed record, then lose its response on a connection already
// used successfully. Neither relay nor client may hide that uncertainty. An
// explicit identical retry must return the same durable record.
func TestRelayReusedConnectionLostCommittedResponse(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	admin, err := core.Open(ctx, dsn, core.Channel{Principal: "relay-reuse-test", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	if err := admin.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	token := "synthetic-relay-reuse-token"
	digest := sha256.Sum256([]byte(token))
	repo := uuid.NewString()
	api, err := New(ctx, dsn, []Identity{{TokenSHA256: hex.EncodeToString(digest[:]), Principal: "machine:reuse/agent", Repo: repo, Role: "agent", Destination: "hosted", Remote: true, MachineID: "reuse"}})
	if err != nil {
		t.Fatal(err)
	}
	defer api.Close()
	var calls, connections atomic.Int32
	committed := make(chan core.Record, 1)
	upstream := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := calls.Add(1)
		result := httptest.NewRecorder()
		api.RemoteHandler().ServeHTTP(result, r)
		if n == 2 && result.Code == 200 {
			var envelope struct {
				Data core.Record `json:"data"`
			}
			if err := json.Unmarshal(result.Body.Bytes(), &envelope); err != nil {
				t.Error(err)
				return
			}
			committed <- envelope.Data
			c, _, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Error(err)
				return
			}
			_ = c.Close()
			return
		}
		for k, v := range result.Header() {
			w.Header()[k] = v
		}
		w.WriteHeader(result.Code)
		_, _ = w.Write(result.Body.Bytes())
	}))
	upstream.Config.ConnState = func(_ net.Conn, s http.ConnState) {
		if s == http.StateNew {
			connections.Add(1)
		}
	}
	upstream.StartTLS()
	defer upstream.Close()
	relay := trustedRelay(t, upstream)
	call := func(op, body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "/v1/"+op, strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		relay.ServeHTTP(w, req)
		return w
	}
	if w := call("version", `{}`); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	body, err := json.Marshal(core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "durably committed before response loss", ClaimType: "self", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}})
	if err != nil {
		t.Fatal(err)
	}
	w := call("create", string(body))
	if w.Code != 504 || !strings.Contains(w.Body.String(), "UPSTREAM_UNCERTAIN") || calls.Load() != 2 || connections.Load() != 1 {
		t.Fatalf("%d %s calls=%d conns=%d", w.Code, w.Body.String(), calls.Load(), connections.Load())
	}
	var record core.Record
	select {
	case record = <-committed:
	default:
		t.Fatal("no committed result captured")
	}
	stored, err := admin.Get(ctx, record.RecordID)
	if err != nil || stored.Body != record.Body {
		t.Fatal("commit missing", err)
	}
	w = call("create", string(body))
	var retry struct {
		Data core.Record `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &retry); err != nil {
		t.Fatal(err)
	}
	if w.Code != 200 || retry.Data.RecordID != record.RecordID || retry.Data.Version != record.Version || calls.Load() != 3 {
		t.Fatalf("explicit retry changed result: %d %s", w.Code, w.Body.String())
	}
}
