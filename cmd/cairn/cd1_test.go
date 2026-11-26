package main

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestCD1CLICommandValidateRenderAndExpandFallback(t *testing.T) {
	validMsg := strings.Join([]string{
		"cd1 notice cairn CAIRN-822 verified act=review",
		"ref e1=387bd21d-6a8f-4b40-8835-7be2d2f42539",
		"obs checked @event:e1",
		"chk unit_tests pass",
	}, "\n")

	out, err := cd1Command([]string{"validate", "--envelope-kind", "notice", "--envelope-action", "review"}, strings.NewReader(validMsg))
	if err != nil {
		t.Fatalf("cd1 validate failed: %v", err)
	}
	resMap := out.(map[string]any)
	if resMap["valid"] != true || resMap["proto"] != core.CD1Proto {
		t.Fatalf("unexpected validate result: %+v", resMap)
	}

	// Mismatched act vs envelope action fails.
	_, err = cd1Command([]string{"validate", "--envelope-kind", "notice", "--envelope-action", "none"}, strings.NewReader(validMsg))
	if err == nil || !strings.Contains(err.Error(), "ENVELOPE_ACTION_MISMATCH") {
		t.Fatalf("expected ENVELOPE_ACTION_MISMATCH, got %v", err)
	}

	// L1 render with --parent-context resolves inherited handle.
	ctxFile := filepath.Join(t.TempDir(), "parent.json")
	rawCtx, _ := json.Marshal(core.CD1ParentContext{
		ThreadID:      "thread-822",
		SourceVersion: 1,
		Refs:          map[string]string{"e1": "387bd21d-6a8f-4b40-8835-7be2d2f42539"},
	})
	if err := os.WriteFile(ctxFile, rawCtx, 0o600); err != nil {
		t.Fatal(err)
	}
	l1Msg := "cd1 resp cairn CAIRN-822 verified re=e1\nchk unit pass"
	renOut, err := cd1Command([]string{"render", "--parent-context", ctxFile, "--thread-id", "thread-822"}, strings.NewReader(l1Msg))
	if err != nil {
		t.Fatalf("cd1 render with parent context failed: %v", err)
	}
	renMap := renOut.(map[string]any)
	if renMap["expanded"] != false || !strings.Contains(renMap["rendered_text"].(string), "387bd21d-6a8f-4b40-8835-7be2d2f42539") {
		t.Fatalf("unexpected render output: %+v", renMap)
	}

	// Missing parent context with --expand-on-missing emits cd1 expand request.
	fbOut, err := cd1Command([]string{"render", "--expand-on-missing"}, strings.NewReader(l1Msg))
	if err != nil {
		t.Fatalf("cd1 render --expand-on-missing failed: %v", err)
	}
	fbMap := fbOut.(map[string]any)
	if fbMap["expanded"] != true || !strings.Contains(fbMap["rendered_text"].(string), "why=missing_ref") {
		t.Fatalf("expected expand fallback, got %+v", fbMap)
	}
}

func TestAgentSendPlainProseFallbackAndCD1CrossCheck(t *testing.T) {
	var createdBodies []string
	recordID := uuid.NewString()
	eventID := uuid.NewString()
	connection := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/create":
			var req core.CreateRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Fatal(err)
			}
			createdBodies = append(createdBodies, req.Draft.Body)
			sendAPIResponse(w, http.StatusOK, core.Record{RecordID: recordID, Version: 1, Sensitivity: req.Draft.Sensitivity, Draft: req.Draft})
		case "/v1/event-publish":
			var req core.PublishEventRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Fatal(err)
			}
			sendAPIResponse(w, http.StatusOK, core.AgentEvent{EventID: eventID, Repo: req.Repo, Kind: req.Kind, From: "agent:sender", Ref: req.Ref, Destination: req.Destination})
		default:
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
	})

	dir := t.TempDir()
	_ = os.Chmod(dir, 0o700)
	to := "agent/" + uuid.NewString()

	// Disagreement between cd1 act=work and --action none is rejected early without API calls.
	badCD1 := "cd1 notice fixture:send T1 active act=work\nobs working"
	badArgs := append(append([]string{}, connection...), "send", "--journal", filepath.Join(dir, "bad.json"),
		"--repo", "fixture:send", "--stdin", "--kind", "notice", "--action", "none", "--to", to)
	_, err := run(context.Background(), badArgs, strings.NewReader(badCD1))
	if err == nil || !strings.Contains(err.Error(), "ENVELOPE_ACTION_MISMATCH") {
		t.Fatalf("expected ENVELOPE_ACTION_MISMATCH on agent send, got %v", err)
	}
	if len(createdBodies) != 0 {
		t.Fatalf("expected zero API create calls on early rejection, got %d", len(createdBodies))
	}

	// --plain converts valid cd1 body to prose before saving and publishing.
	goodCD1 := "cd1 notice fixture:send T2 done act=info\nobs cache warmed\nchk unit pass"
	plainArgs := append(append([]string{}, connection...), "send", "--journal", filepath.Join(dir, "plain.json"),
		"--repo", "fixture:send", "--stdin", "--kind", "notice", "--action", "none", "--to", to, "--plain",
		"--summary", "T2 cache warmed")
	if _, err := run(context.Background(), plainArgs, strings.NewReader(goodCD1)); err != nil {
		t.Fatalf("agent send --plain failed: %v", err)
	}
	if len(createdBodies) != 1 || strings.HasPrefix(createdBodies[0], "cd1 ") || !strings.Contains(createdBodies[0], "Observation: cache warmed") {
		t.Fatalf("expected --plain to save rendered prose body, got %+v", createdBodies)
	}
}
