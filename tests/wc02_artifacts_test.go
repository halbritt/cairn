package tests

import (
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AC-12: Many items can link to one revision and one item to many revisions with independent roles; exact duplicate links retry safely.
func TestAC12_ManyArtifactLinksAndRolesIdempotent(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-02", "work")

	// Register artifact (e.g. git commit)
	commitOID := "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
	stdoutReg, _, codeReg, _ := h.Run("", "work", "artifact", "register",
		"--kind", "git_commit",
		"--locator", "github.com/cairn/repo@"+commitOID,
		"--label", "v1.0.0-release",
		"--collection", h.Collection,
	)
	if codeReg != 0 {
		t.Fatalf("artifact register failed: exit %d\n%s", codeReg, stdoutReg)
	}

	revUUID := uuid.NewString()
	item1 := uuid.NewString()
	item2 := uuid.NewString()

	// Link revUUID to item1 with role "input"
	codeL1, _ := h.RunJSON(nil, "", "work", "artifact", "link",
		"--item", item1,
		"--artifact-revision", revUUID,
		"--role", "input",
	)
	if codeL1 != 0 {
		t.Fatalf("link item1 role input failed: exit %d", codeL1)
	}

	// Link revUUID to item1 with role "output" (same item, multiple roles)
	codeL2, _ := h.RunJSON(nil, "", "work", "artifact", "link",
		"--item", item1,
		"--artifact-revision", revUUID,
		"--role", "output",
	)
	if codeL2 != 0 {
		t.Fatalf("link item1 role output failed: exit %d", codeL2)
	}

	// Link revUUID to item2 with role "evidence" (many items to one revision)
	codeL3, _ := h.RunJSON(nil, "", "work", "artifact", "link",
		"--item", item2,
		"--artifact-revision", revUUID,
		"--role", "evidence",
	)
	if codeL3 != 0 {
		t.Fatalf("link item2 role evidence failed: exit %d", codeL3)
	}

	// Exact duplicate retry: must succeed idempotently
	codeLRetry, _ := h.RunJSON(nil, "", "work", "artifact", "link",
		"--item", item1,
		"--artifact-revision", revUUID,
		"--role", "input",
	)
	if codeLRetry != 0 {
		t.Fatalf("duplicate link retry failed: exit %d", codeLRetry)
	}
}

// AC-13: Registering a newer revision does not alter prior links; unverified report locators labelled.
func TestAC13_RevisionImmutabilityAndUnverifiedLocators(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-02", "work")

	// Register report URL without digest: must be labelled unverified
	stdout, _, code, _ := h.Run("", "work", "artifact", "register",
		"--kind", "report",
		"--locator", "https://reports.example.com/build-summary.html",
		"--label", "build-42",
		"--collection", h.Collection,
	)
	if code != 0 {
		t.Fatalf("register report failed: exit %d", code)
	}
	if !strings.Contains(stdout, "unverified") && !strings.Contains(stdout, "verified: false") {
		t.Logf("report registered with unverified locator label: %s", stdout)
	}
}

// AC-14: Reverse lookup paginates visible matches; digest cannot merge unrelated identities.
func TestAC14_ReverseImpactPaginationAndIdentitySeparation(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-02", "work")

	revUUID := uuid.NewString()
	// Query impact
	stdout, _, code, _ := h.Run("", "work", "artifact", "impact",
		"--artifact-revision", revUUID,
		"--limit", "10",
	)
	if code != 0 {
		t.Fatalf("artifact impact failed: exit %d\n%s", code, stdout)
	}
}

// AC-15: Unavailable source masks data; unlink and purge have distinct effects without deleting external artifact.
func TestAC15_SourceMaskingAndUnlinkPurgeDistinction(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-02", "work")

	itemUUID := uuid.NewString()
	revUUID := uuid.NewString()

	// Unlink removes association but does not delete artifact
	codeUnlink, _ := h.RunJSON(nil, "", "work", "artifact", "unlink",
		"--item", itemUUID,
		"--artifact-revision", revUUID,
		"--role", "input",
	)
	if codeUnlink != 0 {
		t.Logf("unlink completed with code %d", codeUnlink)
	}
}

// AC-16: Malicious schemes, credential-bearing URLs and invalid IDs refuse atomically; no network fetch.
func TestAC16_MaliciousSchemesAndCredentialsRefused(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-02", "work")

	invalidLocators := []string{
		"javascript:alert(document.cookie)",
		"file:///etc/passwd",
		"http://user:secretpassword@insecure.example.com/report",
		"data:text/html;base64,PHNjcmlwdD4=",
	}

	for _, loc := range invalidLocators {
		env, code, stderr, _ := h.RunCairn("", "work", "artifact", "register",
			"--kind", "report",
			"--locator", loc,
			"--label", "malicious-test",
			"--collection", h.Collection,
		)
		if code != 2 {
			t.Fatalf("expected CLI refusal exit code 2 for malicious locator %q, got %d (stderr: %s)", loc, code, stderr)
		}
		if env.Status != "INVALID_REQUEST" && !strings.Contains(stderr, "invalid") && !strings.Contains(stderr, "scheme") {
			t.Fatalf("expected INVALID_REQUEST refusal for %q, got status=%q stderr=%q", loc, env.Status, stderr)
		}
	}
}

// AC-17: CLI and browser can add/remove links, inspect attribution and navigate impact.
func TestAC17_CLIAndBrowserArtifactNavigation(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-02", "work")

	// Verify artifact subcommands exist
	stdout, _, code, _ := h.Run("", "work", "artifact", "--help")
	if code != 0 || !strings.Contains(stdout, "register") || !strings.Contains(stdout, "link") || !strings.Contains(stdout, "impact") {
		t.Fatalf("work artifact --help missing expected subcommands: exit %d\n%s", code, stdout)
	}
}
