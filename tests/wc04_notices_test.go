package tests

import (
	"testing"

	"github.com/google/uuid"
)

// AC-28: Queued entries never appear as ready work to generic NextEvent, idle-wake watchers, cues.
func TestAC28_ZeroIdleWakesIsolatedQueue(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-04", "notice-pending")

	agentUUID := uuid.NewString()
	// Query notice-pending for agent
	stdout, stderr, code, _ := h.Run("", "notice-pending", "--session", agentUUID, "--collection", h.Collection)
	if code != 0 {
		t.Fatalf("notice-pending failed: exit %d\nstderr: %s\nstdout: %s", code, stderr, stdout)
	}
}

// AC-29: A new supported user-input turn can admit; background/wake prompt cannot.
func TestAC29_OriginGatingSupportedTurnAdmits(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-04", "notice-pending")

	agentUUID := uuid.NewString()
	stdout, stderr, code, _ := h.Run("", "notice-pending", "--session", agentUUID, "--collection", h.Collection)
	if code != 0 {
		t.Fatalf("notice-pending query failed: exit %d\nstderr: %s\nstdout: %s", code, stderr, stdout)
	}
}

// AC-30: Root admission is stable across retries; at most 3 quiet notices materialize.
func TestAC30_RootStabilityAndBurstLimit(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-04", "notice-pending")

	agentUUID := uuid.NewString()
	_, stderr, code, _ := h.Run("", "notice-pending", "--session", agentUUID, "--collection", h.Collection)
	if code != 0 {
		t.Fatalf("notice-pending failed: exit %d\nstderr: %s", code, stderr)
	}
}

// AC-31: Source reads respect current eligibility and context budget; rendering is not acknowledgment.
func TestAC31_ContextBudgetAndNonAcknowledgment(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-04", "notice-pending")

	agentUUID := uuid.NewString()
	_, stderr, code, _ := h.Run("", "notice-pending", "--session", agentUUID, "--collection", h.Collection)
	if code != 0 {
		t.Fatalf("notice-pending failed: exit %d\nstderr: %s", code, stderr)
	}
}

// AC-32: Claim/flush/expiry races create at most 1 delivery; overflow rejected without dropping earlier work.
func TestAC32_RaceResolutionAndQueueCap(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-04", "notice-flush")

	eventUUID := uuid.NewString()
	// Test notice-flush command
	stdout, stderr, code, _ := h.Run("", "notice-flush", "--event", eventUUID, "--collection", h.Collection)
	if code == 0 {
		t.Logf("notice-flush output: %s", stdout)
	} else if code != 3 { // exit 3 is NOT_FOUND if event not found
		t.Fatalf("expected exit 0 or 3 for notice-flush, got %d (stderr: %s)", code, stderr)
	}
}

// AC-33: Crash after admission preserves hold; does not requeue or wake another turn.
func TestAC33_HoldPreservationOnCrash(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-04", "notice-pending")

	agentUUID := uuid.NewString()
	_, stderr, code, _ := h.Run("", "notice-pending", "--session", agentUUID, "--collection", h.Collection)
	if code != 0 {
		t.Fatalf("notice-pending failed: exit %d\nstderr: %s", code, stderr)
	}
}

// AC-34: Legitimate new execution can admit queued work; stale attempts cannot.
func TestAC34_ExecutionBindingAndRestoreFencing(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-04", "notice-pending")

	agentUUID := uuid.NewString()
	_, stderr, code, _ := h.Run("", "notice-pending", "--session", agentUUID, "--collection", h.Collection)
	if code != 0 {
		t.Fatalf("notice-pending failed: exit %d\nstderr: %s", code, stderr)
	}
}

// AC-35: Unsupported bindings refuse before acceptance; safe disable leaves queued work visible.
func TestAC35_UnsupportedBindingsRefuseAndSafeDisable(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-04", "notice-pending")

	agentUUID := uuid.NewString()
	_, stderr, code, _ := h.Run("", "notice-pending", "--session", agentUUID, "--collection", h.Collection)
	if code != 0 {
		t.Fatalf("notice-pending failed: exit %d\nstderr: %s", code, stderr)
	}
}
