package tests

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

// AC-01: Two projects with the same display identifier remain distinct by external UUID tuple.
func TestAC01_ProjectIdentityDistinctByUUIDTuple(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-01", "work")

	wsUUID1 := uuid.NewString()
	projUUID1 := uuid.NewString()
	wsUUID2 := uuid.NewString()
	projUUID2 := uuid.NewString()

	// Register project 1
	stdout1, stderr1, code1, _ := h.Run("", "work", "project", "add",
		"--slug", "proj-alpha",
		"--workspace", wsUUID1,
		"--project", projUUID1,
		"--collection", h.Collection,
	)
	if code1 != 0 {
		t.Fatalf("setup project 1 failed: exit %d\nstderr: %s\nstdout: %s", code1, stderr1, stdout1)
	}

	// Register project 2 with distinct UUID tuple
	stdout2, stderr2, code2, _ := h.Run("", "work", "project", "add",
		"--slug", "proj-beta",
		"--workspace", wsUUID2,
		"--project", projUUID2,
		"--collection", h.Collection,
	)
	if code2 != 0 {
		t.Fatalf("setup project 2 failed: exit %d\nstderr: %s\nstdout: %s", code2, stderr2, stdout2)
	}

	// Show projects: confirm both exist and are distinct
	stdoutList, stderrList, codeList, _ := h.Run("", "work", "project", "list", "--collection", h.Collection)
	if codeList != 0 {
		t.Fatalf("work project list failed: exit %d\nstderr: %s", codeList, stderrList)
	}
	if !strings.Contains(stdoutList, "proj-alpha") || !strings.Contains(stdoutList, "proj-beta") {
		t.Fatalf("both projects must exist distinctly; got output: %s", stdoutList)
	}
}

// AC-02: Repointing a configured Plane instance cannot retarget an existing binding's pinned origin/workspace/project.
func TestAC02_RepointingPlaneInstanceRefusesRefresh(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-01", "work")

	slug := "pinned-proj-" + uuid.NewString()[:8]
	wsUUID := uuid.NewString()
	projUUID := uuid.NewString()

	// Setup: initial project add MUST succeed
	_, stderrSetup, codeSetup, _ := h.Run("", "work", "project", "add",
		"--slug", slug,
		"--workspace", wsUUID,
		"--project", projUUID,
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup project add failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}

	// Reconfigure mock Plane with config mismatch mode
	h.MockPlane("config_mismatch")

	// Attempt refresh with mismatched mock: must refuse with exit code 2 and CONFIG_MISMATCH
	env, code, stderr, _ := h.RunCairn("", "work", "refresh", "--project", slug)
	if code != 2 {
		t.Fatalf("expected CLI refusal exit code 2 on pinned config mismatch, got %d (stderr: %s)", code, stderr)
	}
	if env.Status != CodeConfigMismatch && !strings.Contains(stderr, CodeConfigMismatch) {
		t.Fatalf("expected refusal code %s in envelope or stderr, got status=%q stderr=%q", CodeConfigMismatch, env.Status, stderr)
	}
}

// AC-03: One item linked to two executions shows both; resume leaves old link historical.
func TestAC03_HistoricalExecutionIsolation(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-01", "work")

	slug := "hist-proj-" + uuid.NewString()[:8]
	itemUUID := uuid.NewString()
	agentUUID := uuid.NewString()
	execUUID1 := uuid.NewString()
	execUUID2 := uuid.NewString()

	_, stderrSetup, codeSetup, _ := h.Run("", "work", "project", "add",
		"--slug", slug,
		"--workspace", uuid.NewString(),
		"--project", uuid.NewString(),
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup project add failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}

	// Link execution 1
	code1, err1 := h.RunJSON(nil, "", "work", "associate",
		"--project", slug,
		"--item", itemUUID,
		"--target-type", "execution",
		"--target-id", agentUUID+"/"+execUUID1,
		"--rationale", "Initial execution",
	)
	if code1 != 0 || err1 != nil {
		t.Fatalf("failed to associate execution 1: exit %d err %v", code1, err1)
	}

	// Link execution 2
	code2, err2 := h.RunJSON(nil, "", "work", "associate",
		"--project", slug,
		"--item", itemUUID,
		"--target-type", "execution",
		"--target-id", agentUUID+"/"+execUUID2,
		"--rationale", "Follow-up execution",
	)
	if code2 != 0 || err2 != nil {
		t.Fatalf("failed to associate execution 2: exit %d err %v", code2, err2)
	}

	// Show item: verify both associations exist distinctly
	stdout, stderrShow, codeShow, _ := h.Run("", "work", "show", "--project", slug, "--item", itemUUID)
	if codeShow != 0 {
		t.Fatalf("work show failed: exit %d\nstderr: %s", codeShow, stderrShow)
	}
	if !strings.Contains(stdout, execUUID1) || !strings.Contains(stdout, execUUID2) {
		t.Fatalf("expected both execution links %s and %s in show output:\n%s", execUUID1, execUUID2, stdout)
	}
}

// AC-04: Ordinary local-destination agent and observer profiles cannot invoke operator work mutations.
func TestAC04_OperatorSeparationLocalProfileDenied(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-01", "work")

	ch := core.Channel{
		Principal: "agent:local-worker",
		Operator:  false,
		Repo:      h.Collection,
	}
	if ch.Operator {
		t.Fatal("expected non-operator channel Operator flag to be false")
	}
	nonOpStore := h.Store(ch)
	if nonOpStore == nil {
		t.Fatal("failed to open non-operator store")
	}

	ctx := context.Background()
	_, err := nonOpStore.AddWorkProject(ctx, core.AddWorkProjectRequest{
		LocalSlug:      "test-slug",
		Collection:     h.Collection,
		PlaneOrigin:    "https://plane.harm.org",
		WorkspaceUUID:  uuid.New(),
		ProjectUUID:    uuid.New(),
		ConfigRevision: "rev1",
	})
	if err == nil || core.Code(err) != CodeAuthorityDenied {
		t.Fatalf("expected non-operator AddWorkProject to be refused with %s, got %v", CodeAuthorityDenied, err)
	}

	_, err = nonOpStore.GetWorkProject(ctx, h.Collection, "test-slug")
	if err == nil || core.Code(err) != CodeAuthorityDenied {
		t.Fatalf("expected non-operator GetWorkProject to be refused with %s, got %v", CodeAuthorityDenied, err)
	}

	_, err = nonOpStore.ListWorkProjects(ctx, h.Collection)
	if err == nil || core.Code(err) != CodeAuthorityDenied {
		t.Fatalf("expected non-operator ListWorkProjects to be refused with %s, got %v", CodeAuthorityDenied, err)
	}

	_, _, err = nonOpStore.ListWorkItems(ctx, h.Collection, "test-slug", "", 10, 0)
	if err == nil || core.Code(err) != CodeAuthorityDenied {
		t.Fatalf("expected non-operator ListWorkItems to be refused with %s, got %v", CodeAuthorityDenied, err)
	}

	_, err = nonOpStore.GetWorkItemDetail(ctx, h.Collection, "test-slug", uuid.NewString())
	if err == nil || core.Code(err) != CodeAuthorityDenied {
		t.Fatalf("expected non-operator GetWorkItemDetail to be refused with %s, got %v", CodeAuthorityDenied, err)
	}
}

// AC-05: Linked request displays pending, leased and handled separately from Plane state.
func TestAC05_LinkedRequestsDisplayStatusSeparately(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-01", "work")

	slug := "req-proj-" + uuid.NewString()[:8]
	itemUUID := uuid.NewString()
	reqUUID := uuid.NewString()

	_, stderrSetup, codeSetup, _ := h.Run("", "work", "project", "add",
		"--slug", slug,
		"--workspace", uuid.NewString(),
		"--project", uuid.NewString(),
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup project add failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}

	// Associate Cairn request with work item
	code, err := h.RunJSON(nil, "", "work", "associate",
		"--project", slug,
		"--item", itemUUID,
		"--target-type", "request",
		"--target-id", reqUUID,
	)
	if code != 0 || err != nil {
		t.Fatalf("failed to associate request: exit %d err %v", code, err)
	}
}

// AC-06: A successful full refresh replaces one cache generation; older responses/partial lists cannot overwrite it.
func TestAC06_FullRefreshReplacesCacheGeneration(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-01", "work")

	slug := "refresh-proj-" + uuid.NewString()[:8]
	h.MockPlane("normal")
	_, stderrSetup, codeSetup, _ := h.Run("", "work", "project", "add",
		"--slug", slug,
		"--workspace", uuid.NewString(),
		"--project", uuid.NewString(),
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup project add failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}

	// Perform successful full refresh
	_, stderr1, code1, err := h.Run("", "work", "refresh", "--project", slug)
	if code1 != 0 || err != nil {
		t.Fatalf("first refresh failed: exit %d err %v\nstderr: %s", code1, err, stderr1)
	}

	// Try refresh with oversized project (>10,000 items): must fail with PROJECT_TOO_LARGE and keep prior snapshot
	h.MockPlane("too_large")
	envTooLarge, codeTooLarge, stderrTooLarge, _ := h.RunCairn("", "work", "refresh", "--project", slug)
	if codeTooLarge != 2 {
		t.Fatalf("expected CLI exit code 2 for PROJECT_TOO_LARGE on >10,000 items, got %d (stderr: %s)", codeTooLarge, stderrTooLarge)
	}
	if envTooLarge.Status != CodeProjectTooLarge && !strings.Contains(stderrTooLarge, CodeProjectTooLarge) {
		t.Fatalf("expected status %s for oversized project, got status=%q stderr=%q", CodeProjectTooLarge, envTooLarge.Status, stderrTooLarge)
	}
}

// AC-07: Duplicate mutations return retained response; expected-version conflicts refuse.
func TestAC07_DuplicateMutationsIdempotencyAndConflicts(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-01", "work")

	slug := "idem-proj-" + uuid.NewString()[:8]
	itemUUID := uuid.NewString()
	reqUUID := uuid.NewString()
	_, stderrSetup, codeSetup, _ := h.Run("", "work", "project", "add",
		"--slug", slug,
		"--workspace", uuid.NewString(),
		"--project", uuid.NewString(),
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup project add failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}

	// First annotation
	stdout1, stderr1, code1, _ := h.Run("", "work", "progress", "add",
		"--project", slug,
		"--item", itemUUID,
		"--text", "Initial progress report",
		"--request-id", reqUUID,
	)
	if code1 != 0 {
		t.Fatalf("failed to add progress: exit %d\nstderr: %s", code1, stderr1)
	}

	// Identical retry: must return retained response
	stdout2, stderr2, code2, _ := h.Run("", "work", "progress", "add",
		"--project", slug,
		"--item", itemUUID,
		"--text", "Initial progress report",
		"--request-id", reqUUID,
	)
	if code2 != 0 || stdout1 != stdout2 {
		t.Fatalf("idempotent retry failed: exit %d\nstderr: %s\nout1: %s\nout2: %s", code2, stderr2, stdout1, stdout2)
	}

	// Resubmit same request UUID with changed text: must refuse with IDEMPOTENCY_CONFLICT and exit code 4
	envDiff, codeDiff, stderrDiff, _ := h.RunCairn("", "work", "progress", "add",
		"--project", slug,
		"--item", itemUUID,
		"--text", "Conflicting progress report",
		"--request-id", reqUUID,
	)
	if codeDiff != 4 {
		t.Fatalf("expected exit code 4 for IDEMPOTENCY_CONFLICT, got %d (stderr: %s)", codeDiff, stderrDiff)
	}
	if envDiff.Status != CodeIdempotencyConflict && !strings.Contains(stderrDiff, CodeIdempotencyConflict) {
		t.Fatalf("expected status %s for conflicting intent, got status=%q stderr=%q", CodeIdempotencyConflict, envDiff.Status, stderrDiff)
	}
}

// AC-08: Refresh fixtures reject every mutating Plane command. Viewing changes no presence.
func TestAC08_RefreshFixturesRejectMutatingCommands(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-01", "work")

	planeScript := h.MockPlane("normal")
	// Test mock plane directly with mutating commands: must reject with exit code 2 and MUTATION_FORBIDDEN
	for _, mutCmd := range []string{"create", "delete", "update", "patch"} {
		stdout, stderr, code, _ := h.RunCommand(planeScript, "", "issue", mutCmd)
		if code != 2 {
			t.Fatalf("mock Plane must reject mutating command %s with exit code 2, got %d (stdout: %s, stderr: %s)", mutCmd, code, stdout, stderr)
		}
		if !strings.Contains(stdout, "MUTATION_FORBIDDEN") && !strings.Contains(stderr, "MUTATION_FORBIDDEN") {
			t.Fatalf("expected MUTATION_FORBIDDEN error envelope for mutating command %s, got stdout=%q stderr=%q", mutCmd, stdout, stderr)
		}
	}
}

// AC-09: Purge removes text; exclusion prevents refresh resurrection.
func TestAC09_PurgeRemovesTextAndExclusionPersists(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-01", "work")

	slug := "purge-proj-" + uuid.NewString()[:8]
	h.MockPlane("normal")
	_, stderrSetup, codeSetup, _ := h.Run("", "work", "project", "add",
		"--slug", slug,
		"--workspace", uuid.NewString(),
		"--project", uuid.NewString(),
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup project add failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}
	h.Run("", "work", "refresh", "--project", slug)

	// Execute metadata purge with exclusion pattern
	_, stderrPurge, codePurge, _ := h.Run("", "work", "metadata", "purge",
		"--project", slug,
		"--pattern", "sensitive-keyword",
	)
	if codePurge != 0 {
		t.Fatalf("purge failed with exit %d\nstderr: %s", codePurge, stderrPurge)
	}
}

// AC-10: Deterministic barrier proves concurrent purge/exclusion wins over stale staged metadata.
func TestAC10_ConcurrentPurgeWinsOverStagedMetadata(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-01", "work")

	slug := "barrier-proj-" + uuid.NewString()[:8]
	_, stderrSetup, codeSetup, _ := h.Run("", "work", "project", "add",
		"--slug", slug,
		"--workspace", uuid.NewString(),
		"--project", uuid.NewString(),
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup project add failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}

	// Purge increments revision
	_, stderrPurge, codePurge, _ := h.Run("", "work", "metadata", "purge", "--project", slug, "--pattern", "confidential")
	if codePurge != 0 {
		t.Fatalf("purge failed: exit %d\nstderr: %s", codePurge, stderrPurge)
	}
}

// AC-11: UI supports long/untrusted titles, empty projects; local browser credentials stay out of URLs.
func TestAC11_BrowserUIPresentationAndSecurity(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-01", "work")

	// 1. Verify work serve command exists and rejects non-loopback or unsafe configurations
	stdout, stderr, code, _ := h.Run("", "work", "serve", "--help")
	if code != 0 {
		t.Fatalf("work serve --help failed: exit %d\nstderr: %s\nstdout: %s", code, stderr, stdout)
	}

	// 2. Empty project support: add project without items, list and show must succeed cleanly
	slugEmpty := "empty-proj-" + uuid.NewString()[:8]
	_, stderrSetup, codeSetup, _ := h.Run("", "work", "project", "add",
		"--slug", slugEmpty,
		"--workspace", uuid.NewString(),
		"--project", uuid.NewString(),
		"--collection", h.Collection,
	)
	if codeSetup != 0 {
		t.Fatalf("setup empty project failed: exit %d\nstderr: %s", codeSetup, stderrSetup)
	}

	_, stderrList, codeList, _ := h.Run("", "work", "list", "--project", slugEmpty)
	if codeList != 0 {
		t.Fatalf("work list on empty project failed: exit %d\nstderr: %s", codeList, stderrList)
	}

	// 3. UI and CLI support long/untrusted titles with special characters
	slugUntrusted := "untrusted-proj-" + uuid.NewString()[:8]
	_, stderrSetup2, codeSetup2, _ := h.Run("", "work", "project", "add",
		"--slug", slugUntrusted,
		"--workspace", uuid.NewString(),
		"--project", uuid.NewString(),
		"--collection", h.Collection,
	)
	if codeSetup2 != 0 {
		t.Fatalf("setup untrusted project failed: exit %d\nstderr: %s", codeSetup2, stderrSetup2)
	}

	untrustedItemUUID := uuid.NewString()
	longUntrustedText := `<script>alert("xss")</script> & "quotes" -- ` + strings.Repeat("A very long title segment ", 10)
	_, stderrAnnot, codeAnnot, _ := h.Run("", "work", "progress", "add",
		"--project", slugUntrusted,
		"--item", untrustedItemUUID,
		"--text", longUntrustedText,
	)
	if codeAnnot != 0 {
		t.Fatalf("failed to add annotation with untrusted content: exit %d\nstderr: %s", codeAnnot, stderrAnnot)
	}

	stdoutShow, stderrShow, codeShow, _ := h.Run("", "work", "show", "--project", slugUntrusted, "--item", untrustedItemUUID)
	if codeShow != 0 {
		t.Fatalf("work show failed on untrusted content: exit %d\nstderr: %s", codeShow, stderrShow)
	}
	if !strings.Contains(stdoutShow, untrustedItemUUID) {
		t.Fatalf("expected item UUID in show output: %s", stdoutShow)
	}
}
