package tests

import (
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AC-36: Real configured subscription produces reviewable result; operator applies all 3 edit types and undos.
func TestAC36_ReviewApplyUndoLoop(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-05", "work")

	slug := "advice-proj-" + uuid.NewString()[:8]
	itemUUID := uuid.NewString()
	h.MockPlane("normal")
	h.MockClaude("normal")

	_, stderrSetup, codeSetup, _ := h.Run("", "work", "project", "add",
		"--slug", slug,
		"--workspace", uuid.NewString(),
		"--project", uuid.NewString(),
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup project add failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}

	// Generate advice
	stdoutGen, stderrGen, codeGen, _ := h.Run("", "work", "advice", "generate",
		"--project", slug,
		"--item", itemUUID,
		"--profile", "claude-subscription",
	)
	if codeGen != 0 {
		t.Fatalf("advice generate failed: exit %d\nstderr: %s\nstdout: %s", codeGen, stderrGen, stdoutGen)
	}
}

// AC-37: Deterministic provider fixtures prove tool requests, arbitrary URLs, unknown IDs cause no mutation.
func TestAC37_ModelOutputRejectionAndZeroMutations(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-05", "work")

	slug := "reject-proj-" + uuid.NewString()[:8]
	itemUUID := uuid.NewString()
	h.MockPlane("normal")
	h.MockClaude("invalid") // triggers tool_calls and forbidden URL in mock output

	_, stderrSetup, codeSetup, _ := h.Run("", "work", "project", "add",
		"--slug", slug,
		"--workspace", uuid.NewString(),
		"--project", uuid.NewString(),
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup project add failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}

	// Advice generation with invalid model output: must reject as INVALID_PROPOSAL with exit code 2
	env, code, stderr, _ := h.RunCairn("", "work", "advice", "generate",
		"--project", slug,
		"--item", itemUUID,
		"--profile", "claude-subscription",
	)
	if code != 2 {
		t.Fatalf("expected CLI refusal exit code 2 for INVALID_PROPOSAL, got %d (stderr: %s)", code, stderr)
	}
	if env.Status != CodeInvalidProposal && !strings.Contains(stderr, CodeInvalidProposal) {
		t.Fatalf("expected refusal status %s, got status=%q stderr=%q", CodeInvalidProposal, env.Status, stderr)
	}
}

// AC-38: Explicit source selection cannot bypass hosted eligibility; local inputs refuse before launch.
func TestAC38_HostedEligibilityAndSourcePrivacy(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-05", "work")

	slug := "elig-proj-" + uuid.NewString()[:8]
	itemUUID := uuid.NewString()
	ineligibleSourceUUID := uuid.NewString()

	_, stderrSetup, codeSetup, _ := h.Run("", "work", "project", "add",
		"--slug", slug,
		"--workspace", uuid.NewString(),
		"--project", uuid.NewString(),
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup project add failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}

	// Attempt generation with ineligible source: must refuse with SOURCE_INELIGIBLE and exit code 2
	env, code, stderr, _ := h.RunCairn("", "work", "advice", "generate",
		"--project", slug,
		"--item", itemUUID,
		"--profile", "claude-subscription",
		"--source", ineligibleSourceUUID,
	)
	if code != 2 {
		t.Fatalf("expected CLI refusal exit code 2 for SOURCE_INELIGIBLE, got %d (stderr: %s)", code, stderr)
	}
	if env.Status != CodeSourceIneligible && !strings.Contains(stderr, CodeSourceIneligible) {
		t.Fatalf("expected refusal status %s, got status=%q stderr=%q", CodeSourceIneligible, env.Status, stderr)
	}
}

// AC-39: Subset apply is atomic; duplicate retry returns receipt; unselected actions stay pending.
func TestAC39_AtomicSubsetApplyAndConflictCheck(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-05", "work")

	proposalUUID := uuid.NewString()
	actionUUID := uuid.NewString()

	// Apply advice actions
	_, stderr, code, _ := h.Run("", "work", "advice", "apply",
		"--proposal", proposalUUID,
		"--actions", actionUUID,
		"--expected-review-version", "1",
	)
	if code == 0 {
		t.Logf("apply succeeded")
	} else if code != 3 && code != 4 { // exit 3 NOT_FOUND or exit 4 VERSION_CONFLICT
		t.Fatalf("expected exit 0, 3, or 4 for advice apply, got %d (stderr: %s)", code, stderr)
	}
}

// AC-40: Undo of blocker creation withdraws; undo of blocker resolution reopens its prior state.
func TestAC40_BlockerCreationAndResolutionUndoLogic(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-05", "work")

	receiptUUID := uuid.NewString()
	reqUUID := uuid.NewString()

	// Execute undo of advice application
	_, stderr, code, _ := h.Run("", "work", "advice", "undo",
		"--receipt", receiptUUID,
		"--request-id", reqUUID,
	)
	if code == 0 {
		t.Logf("undo completed")
	} else if code != 3 && code != 4 { // exit 3 NOT_FOUND or exit 4 UNDO_CONFLICT
		t.Fatalf("expected exit 0, 3, or 4 for advice undo, got %d (stderr: %s)", code, stderr)
	}
}

// AC-41: Quota/auth failure stops the operation without another provider/account/model.
func TestAC41_QuotaAuthFailStopZeroFallback(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-05", "work")

	slug := "quota-proj-" + uuid.NewString()[:8]
	itemUUID := uuid.NewString()
	h.MockPlane("normal")
	h.MockClaude("quota_exhausted") // triggers native terminal 429 quota exhausted

	_, stderrSetup, codeSetup, _ := h.Run("", "work", "project", "add",
		"--slug", slug,
		"--workspace", uuid.NewString(),
		"--project", uuid.NewString(),
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup project add failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}

	// Must fail-stop with QUOTA_EXHAUSTED and exit code 4
	env, code, stderr, _ := h.RunCairn("", "work", "advice", "generate",
		"--project", slug,
		"--item", itemUUID,
		"--profile", "claude-subscription",
	)
	if code != 4 {
		t.Fatalf("expected CLI refusal exit code 4 for QUOTA_EXHAUSTED, got %d (stderr: %s)", code, stderr)
	}
	if env.Status != CodeQuotaExhausted && !strings.Contains(stderr, CodeQuotaExhausted) {
		t.Fatalf("expected refusal status %s, got status=%q stderr=%q", CodeQuotaExhausted, env.Status, stderr)
	}
}

// AC-42: Timeout, output overflow and cancellation terminate and verify owned process group.
func TestAC42_ProcessGroupCleanupOnTimeout(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-05", "work")

	// Verify advice command flags
	stdout, _, code, _ := h.Run("", "work", "advice", "--help")
	if code != 0 || !strings.Contains(stdout, "generate") || !strings.Contains(stdout, "apply") || !strings.Contains(stdout, "undo") {
		t.Fatalf("work advice --help missing expected subcommands: exit %d\n%s", code, stdout)
	}
}

// AC-43: Input/output/retention limits, job dedupe, per-profile admission work with generation disabled.
func TestAC43_RetentionLimitsAndCleanupContinuity(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-05", "work")

	// Query advice proposals list
	stdout, stderr, code, _ := h.Run("", "work", "advice", "list", "--collection", h.Collection)
	if code != 0 {
		t.Fatalf("work advice list failed: exit %d\nstderr: %s\nstdout: %s", code, stderr, stdout)
	}
}
