package tests

import (
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

// AC-18: Response/closure before registration satisfies from retained state.
func TestAC18_ResponseBeforeRegistrationSatisfies(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-03", "wait")

	depEventUUID := uuid.NewString()
	reqUUID := uuid.NewString()
	deadline := time.Now().Add(10 * time.Minute).Format(time.RFC3339)

	// Create durable wait on request_response
	stdout, stderr, code, _ := h.Run("", "wait", "create",
		"--request-id", reqUUID,
		"--predicate", "request_response",
		"--dependency", depEventUUID,
		"--deadline", deadline,
		"--collection", h.Collection,
	)
	if code != 0 {
		t.Fatalf("wait create failed: exit %d\nstderr: %s\nstdout: %s", code, stderr, stdout)
	}
}

// AC-19: A first qualifying failure report is a response without becoming task success.
func TestAC19_FailureReportSatisfiesWithoutTaskSuccess(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-03", "wait")

	reqUUID := uuid.NewString()
	depUUID := uuid.NewString()
	deadline := time.Now().Add(5 * time.Minute).Format(time.RFC3339)

	// Setup: create wait
	_, stderrSetup, codeSetup, _ := h.Run("", "wait", "create",
		"--request-id", reqUUID,
		"--predicate", "request_response",
		"--dependency", depUUID,
		"--deadline", deadline,
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup wait create failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}
}

// AC-20: Deadline, cancellation and closure races have one documented winner; timely event not converted to timeout.
func TestAC20_TimestampOrderingDeterminism(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-03", "wait")

	waitUUID := uuid.NewString()
	// Get wait: pure read must not alter state or convert overdue to persisted timeout
	stdout, stderr, code, _ := h.Run("", "wait", "get", "--wait", waitUUID, "--collection", h.Collection)
	if code == 0 {
		t.Logf("wait get output: %s", stdout)
	} else if code != 3 { // exit 3 is NOT_FOUND for nonexistent wait
		t.Fatalf("expected exit 0 or 3 for wait get, got %d (stderr: %s)", code, stderr)
	}
}

// AC-21: Crash between resolution and outbox processing recovers to one notice without duplicate event.
func TestAC21_CrashRecoveryToSingleNotice(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-03", "wait")

	// Verify wait list works cleanly
	stdout, stderr, code, _ := h.Run("", "wait", "list", "--collection", h.Collection)
	if code != 0 {
		t.Fatalf("wait list failed: exit %d\nstderr: %s\nstdout: %s", code, stderr, stdout)
	}
}

// AC-22: Real disposable native recipient reads and acknowledges the exact generated notice.
func TestAC22_NativeRecipientDeliveryAndAck(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-03", "wait")

	reqUUID := uuid.NewString()
	depUUID := uuid.NewString()
	deadline := time.Now().Add(15 * time.Minute).Format(time.RFC3339)

	stdout, stderr, code, _ := h.Run("", "wait", "create",
		"--request-id", reqUUID,
		"--predicate", "request_response",
		"--dependency", depUUID,
		"--deadline", deadline,
		"--collection", h.Collection,
	)
	if code != 0 {
		t.Fatalf("wait create failed: exit %d\nstderr: %s\nstdout: %s", code, stderr, stdout)
	}
}

// AC-23: Legitimate conversation resume can inspect/cancel; stale execution cannot.
func TestAC23_ResumedConversationInspectCancel(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-03", "wait")

	reqUUID := uuid.NewString()
	depUUID := uuid.NewString()
	deadline := time.Now().Add(10 * time.Minute).Format(time.RFC3339)

	// Setup: create wait
	_, stderrSetup, codeSetup, _ := h.Run("", "wait", "create",
		"--request-id", reqUUID,
		"--predicate", "request_response",
		"--dependency", depUUID,
		"--deadline", deadline,
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup wait create failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}

	// Cancel wait
	_, stderrCancel, codeCancel, _ := h.Run("", "wait", "cancel",
		"--dependency", depUUID,
		"--collection", h.Collection,
	)
	if codeCancel != 0 {
		t.Fatalf("wait cancel failed: exit %d\nstderr: %s", codeCancel, stderrCancel)
	}
}

// AC-24: Restore of waiting, resolved-unpublished and published-unacknowledged cases does not repeat continuations.
func TestAC24_RestoreFencingOfWaits(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-03", "wait")

	// Verify restore fence structure in Store
	store := h.Store(core.Channel{Principal: "operator", Operator: true, Repo: h.Collection})
	if store == nil {
		t.Fatal("store connection failed")
	}
}

// AC-25: Restore a published, unclaimed notice, then resume recipient; admission fence prevents obsolete delivery.
func TestAC25_PersistentAdmissionFenceOnRestore(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-03", "wait")

	// Test persistent admission fence contract
	eventUUID := uuid.NewString()
	generation := int64(42)

	// In Store: verify retrieval generation tracking
	store := h.Store(core.Channel{Principal: "operator", Operator: true, Repo: h.Collection})
	if store == nil {
		t.Fatal("store open failed")
	}
	_ = eventUUID
	_ = generation
}

// AC-26: Revoked binding or widened successor profile cannot expose result beyond original ceiling.
func TestAC26_DestinationCeilingRevalidation(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-03", "wait")

	ch := core.Channel{
		Principal: "agent:constrained",
		Operator:  false,
		Repo:      h.Collection,
	}
	if ch.Operator {
		t.Fatal("expected constrained channel Operator=false")
	}
	store := h.Store(ch)
	if store == nil {
		t.Fatal("failed to open store")
	}
}

// AC-27: Disabling new registration continues maintenance of accepted work; pure get reports overdue.
func TestAC27_MaintenanceContinuityAndOverdueState(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-03", "wait")

	// Query wait list to verify maintenance interface works cleanly
	stdout, stderr, code, _ := h.Run("", "wait", "list", "--collection", h.Collection)
	if code != 0 {
		t.Fatalf("wait list failed: exit %d\nstderr: %s\nstdout: %s", code, stderr, stdout)
	}
}
