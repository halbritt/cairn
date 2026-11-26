package mcpapi

import (
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestMessagingCD1CrossCheckAndPlainProseMode(t *testing.T) {
	f := newMessagingFixture(t, Config{ToolProfile: "full", NativeSessionID: "native-self"})

	// Mismatched act vs action fails before calling API.
	f.refused(t, "cairn_send", map[string]any{
		"request_id": uuid.NewString(),
		"to":         "agent-9",
		"kind":       "notice",
		"action":     "none",
		"body":       "cd1 notice repo T1 active act=work\nobs working",
	}, "ENVELOPE_ACTION_MISMATCH")
	if calls := f.api.taken(); len(calls) != 0 {
		t.Fatalf("expected zero API calls on early cd1 mismatch, got %v", operations(calls))
	}

	// plain:true renders valid cd1 to prose before saving/publishing.
	f.ok(t, "cairn_send", map[string]any{
		"request_id": uuid.NewString(),
		"to":         "agent-9",
		"kind":       "notice",
		"action":     "none",
		"summary":    "T2 cache warmed",
		"plain":      true,
		"body":       "cd1 notice repo T2 done act=info\nobs cache warmed\nchk unit pass",
	})
	calls := f.api.taken()
	if len(calls) < 2 || calls[len(calls)-2].Operation != "create" {
		t.Fatalf("expected create + event-publish calls, got %v", operations(calls))
	}
	draft := calls[len(calls)-2].Body["draft"].(map[string]any)
	savedBody := draft["body"].(string)
	if strings.HasPrefix(savedBody, "cd1 ") || !strings.Contains(savedBody, "Observation: cache warmed") {
		t.Fatalf("expected plain:true to store rendered prose, got:\n%s", savedBody)
	}
}
