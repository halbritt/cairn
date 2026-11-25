package memoryhook

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestCaptureObservationsDefaultOnAndOptOut(t *testing.T) {
	dir := t.TempDir()
	sock := filepath.Join(dir, "api.sock")
	tokenPath := filepath.Join(dir, "token")
	if err := os.WriteFile(tokenPath, []byte("test-token"), 0o600); err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var bodies []core.CaptureObservationRequest
	ln, err := net.Listen("unix", sock)
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req core.CaptureObservationRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err == nil {
			mu.Lock()
			bodies = append(bodies, req)
			mu.Unlock()
		}
		w.WriteHeader(200)
		_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":{"observation_id":"obs-1"}}`))
	})}
	go func() { _ = srv.Serve(ln) }()
	defer srv.Close()

	cfgDefault := value.Mapping(
		value.Field("repo", value.Text("shared")),
		value.Field("harness", value.Text("hermes")),
		value.Field("socket", value.Text(sock)),
		value.Field("token_file", value.Text(tokenPath)),
	)
	event := value.Mapping(
		value.Field("hook_event_name", value.Text("PreCompact")),
		value.Field("session_id", value.Text("Hermes session/run:01")),
	)
	observed := value.Mapping(
		value.Field("outcome", value.Text("timeout")),
		value.Field("selector_outcome", value.Text("timeout")),
		value.Field("dialogue_messages", integer(4)),
		value.Field("dialogue_bytes", integer(240)),
		value.Field("selector", value.Mapping(
			value.Field("partial_usage", value.Boolean(true)),
			value.Field("usage", value.Mapping(
				value.Field("input_tokens", integer(10)),
				value.Field("reasoning_tokens", integer(33)),
			)),
		)),
	)
	captureReport(context.Background(), cfgDefault, event, time.Now(), observed)
	mu.Lock()
	count := len(bodies)
	mu.Unlock()
	if count != 1 {
		t.Fatalf("expected default-on capture observation, got %d", count)
	}
	if bodies[0].NativeSessionID != "Hermes session/run:01" || bodies[0].ReasoningTokens == nil || *bodies[0].ReasoningTokens != 33 ||
		bodies[0].PartialUsage == nil || !*bodies[0].PartialUsage || bodies[0].DialogueMessages == nil || *bodies[0].DialogueMessages != 4 {
		t.Fatalf("unexpected capture payload: %+v", bodies[0])
	}

	// Explicit false opts out cleanly.
	cfgOff := clone(cfgDefault)
	cfgOff.Set("capture_observations", value.Boolean(false))
	captureReport(context.Background(), cfgOff, event, time.Now(), observed)
	mu.Lock()
	countAfter := len(bodies)
	mu.Unlock()
	if countAfter != 1 {
		t.Fatalf("expected opt-out to emit 0 observations, got %d", countAfter-1)
	}
}

func TestSkipEmptyRecallDefaultAndOptOut(t *testing.T) {
	dir := t.TempDir()
	if err := os.Mkdir(filepath.Join(dir, ".git"), 0o700); err != nil {
		t.Fatal(err)
	}
	stateDir := filepath.Join(dir, "state")
	cfg := value.Mapping(
		value.Field("repo", value.Text("shared")),
		value.Field("cairn", value.Text("/nonexistent/cairn")),
		value.Field("socket", value.Text(filepath.Join(dir, "absent.sock"))),
		value.Field("token_file", value.Text(filepath.Join(dir, "absent.tok"))),
		value.Field("state_dir", value.Text(stateDir)),
		value.Field("harness", value.Text("hermes")),
	)
	event := value.Mapping(
		value.Field("hook_event_name", value.Text("SessionStart")),
		value.Field("source", value.Text("startup")),
		value.Field("prompt", value.Text("  ")),
		value.Field("cwd", value.Text(dir)),
		value.Field("session_id", value.Text("Hermes/raw:session")),
	)
	res := RunCheckedOrdinary(context.Background(), cfg, event, dir, []string{"HOME=" + dir})
	if res.ExitCode != 0 || res.Stdout == "" {
		t.Fatalf("expected clean skip output, got %#v", res)
	}
	statePath := filepath.Join(stateDir, hashText("Hermes/raw:session")+".json")
	raw, err := os.ReadFile(statePath)
	if err != nil {
		t.Fatalf("expected hashed hermes state file at %s: %v", statePath, err)
	}
	st, err := value.DecodeText(raw, "utf-8", 0)
	if err != nil {
		t.Fatal(err)
	}
	if !value.SameText(st.Get("last_recall").Get("outcome"), "skipped") {
		t.Fatalf("expected outcome=skipped, got %v", st.Get("last_recall"))
	}

	// Opt-out via env var skips the shortcut and attempts API connection.
	if _, skip := skipEmptyRecall(cfg, event, map[string][]rune{"CAIRN_RECALL_SKIP_EMPTY": []rune("0")}, false); skip {
		t.Fatal("CAIRN_RECALL_SKIP_EMPTY=0 should opt out of skipping")
	}
	cfgOptOut := clone(cfg)
	cfgOptOut.Set("skip_empty_recall", value.Boolean(false))
	if _, skip := skipEmptyRecall(cfgOptOut, event, nil, false); skip {
		t.Fatal("skip_empty_recall=false should opt out of skipping")
	}
}
