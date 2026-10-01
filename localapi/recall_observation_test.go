package localapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5"
)

func TestRecallObservationRouteKeepsProfileBoundaries(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	admin, err := core.Open(ctx, dsn, core.Channel{Principal: "recall-route-test", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	if err = admin.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	repo := "recall-route/" + uuid.NewString()
	identity := func(name, principal, role, destination, machine string) Identity {
		digest := sha256.Sum256([]byte("synthetic-" + name))
		remote := machine != ""
		return Identity{TokenSHA256: hex.EncodeToString(digest[:]), Principal: principal, Repo: repo, Role: role, Destination: destination, Remote: remote, MachineID: machine}
	}
	server, err := New(ctx, dsn, []Identity{
		identity("hosted", "agent:recall-hosted", "agent", "hosted", ""),
		identity("local", "agent:recall-local", "agent", "local", ""),
		identity("box", "machine:box/agent", "agent", "hosted", "box"),
		identity("watcher", "machine:box/observer", "observer", "hosted", "box"),
	})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	if err = server.SetLocalMachineID("central"); err != nil {
		t.Fatal(err)
	}
	call := func(handler http.Handler, token, operation string, body any) (int, json.RawMessage) {
		t.Helper()
		encoded, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		req := httptest.NewRequest("POST", "/v1/"+operation, bytes.NewReader(encoded))
		req.Header.Set("Authorization", "Bearer synthetic-"+token)
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, req)
		var envelope struct {
			Data json.RawMessage `json:"data"`
		}
		_ = json.Unmarshal(w.Body.Bytes(), &envelope)
		if w.Code == 200 {
			return w.Code, envelope.Data
		}
		return w.Code, w.Body.Bytes()
	}
	observation := func(harness string) core.RecallObservationRequest {
		elapsed := int64(250)
		return core.RecallObservationRequest{RequestID: uuid.NewString(), Repo: repo, Harness: harness, HookEvent: "UserPromptSubmit",
			Method: "route-test/1", Status: "completed", ElapsedMS: &elapsed}
	}
	remote := server.RemoteHandler()

	// A hosted local profile and a remote machine agent may report their own hook.
	for _, report := range []struct {
		handler http.Handler
		token   string
		harness string
	}{{server, "hosted", "codex"}, {remote, "box", "opencode"}} {
		if code, body := call(report.handler, report.token, "recall-observation", observation(report.harness)); code != 200 {
			t.Fatalf("%s could not report: %d %s", report.token, code, body)
		}
	}
	// The same request UUID and content retries to the same observation; the route is not a mutation shortcut.
	retry := observation("claude")
	_, first := call(server, "hosted", "recall-observation", retry)
	_, again := call(server, "hosted", "recall-observation", retry)
	if !bytes.Equal(first, again) || len(first) == 0 {
		t.Fatalf("an exact retry did not return the original observation: %s %s", first, again)
	}
	// Protected reading stays with the local profile; reporting grants no read access.
	for _, handler := range []http.Handler{server, remote} {
		for _, token := range []string{"hosted", "box", "watcher"} {
			if code, _ := call(handler, token, "use-report", core.UseReportRequest{Repo: repo, Limit: 10}); code != 403 {
				t.Fatalf("%s read the protected report: %d", token, code)
			}
		}
	}
	// An observer cannot write, and no profile can report outside its repository.
	if code, _ := call(remote, "watcher", "recall-observation", observation("codex")); code != 403 {
		t.Fatalf("a remote observer reported: %d", code)
	}
	outside := observation("codex")
	outside.Repo = "outside/" + repo
	for _, handler := range []struct {
		handler http.Handler
		token   string
	}{{server, "hosted"}, {remote, "box"}} {
		if code, _ := call(handler.handler, handler.token, "recall-observation", outside); code != 403 {
			t.Fatalf("%s reported outside its repository: %d", handler.token, code)
		}
	}
	// Nothing about the report can be selected by the payload: the caller is the credential's principal.
	invalid := observation("not-a-harness")
	if code, _ := call(server, "hosted", "recall-observation", invalid); code != 400 {
		t.Fatalf("invalid harness accepted: %d", code)
	}
	code, body := call(server, "local", "use-report", core.UseReportRequest{Repo: repo, Limit: 10})
	if code != 200 {
		t.Fatalf("local report: %d %s", code, body)
	}
	var report core.UseReport
	if err = json.Unmarshal(body, &report); err != nil {
		t.Fatal(err)
	}
	seen := map[string]int{}
	for _, row := range report.RecallRollups.Rows {
		seen[row.Harness] += row.Observations
	}
	if seen["codex"] != 1 || seen["opencode"] != 1 || seen["claude"] != 1 || len(seen) != 3 {
		t.Fatalf("reports missing or refused observations stored: %+v", report.RecallRollups)
	}
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(ctx)
	var principals int
	if err = conn.QueryRow(ctx, `SELECT count(DISTINCT caller) FROM cairn.recall_observation WHERE repo=$1 AND caller IN ('agent:recall-hosted','machine:box/agent')`, repo).Scan(&principals); err != nil || principals != 2 {
		t.Fatalf("callers must be the authenticated principals: %d %v", principals, err)
	}
}
