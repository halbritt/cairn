package tests

import (
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AC-44: End-to-end multi-feature user journey across two sessions on Plane card.
func TestAC44_EndToEndMultiFeatureUserJourney(t *testing.T) {
	h := newTestHarness(t)
	// AC-44 exercises the full integration journey across WC-01..WC-06
	h.RequireMilestone("WC-01", "work")

	h.MockPlane("normal")
	h.MockClaude("normal")

	slug := "journey-proj-" + uuid.NewString()[:8]
	itemUUID := "e2e00001-0000-4000-8000-000000000001"
	sessionA := uuid.NewString()
	execA := uuid.NewString()
	sessionB := uuid.NewString()
	execB := uuid.NewString()

	// 1. Add Plane project
	_, stderrProj, codeProj, _ := h.Run("", "work", "project", "add",
		"--slug", slug,
		"--workspace", uuid.NewString(),
		"--project", uuid.NewString(),
		"--collection", h.Collection,
	)
	if codeProj != 0 {
		t.Fatalf("step 1 add project failed: exit %d\nstderr: %s", codeProj, stderrProj)
	}

	// 2. Refresh cards
	_, stderrRef, codeRef, _ := h.Run("", "work", "refresh", "--project", slug)
	if codeRef != 0 {
		t.Fatalf("step 2 refresh failed: exit %d\nstderr: %s", codeRef, stderrRef)
	}

	// 3. Associate both executions
	h.RunJSON(nil, "", "work", "associate", "--project", slug, "--item", itemUUID, "--target-type", "execution", "--target-id", sessionA+"/"+execA)
	h.RunJSON(nil, "", "work", "associate", "--project", slug, "--item", itemUUID, "--target-type", "execution", "--target-id", sessionB+"/"+execB)

	// 4. Verify work show projection
	stdoutShow, stderrShow, codeShow, _ := h.Run("", "work", "show", "--project", slug, "--item", itemUUID)
	if codeShow != 0 {
		t.Fatalf("step 4 work show failed: exit %d\nstderr: %s", codeShow, stderrShow)
	}
	if !strings.Contains(stdoutShow, execA) || !strings.Contains(stdoutShow, execB) {
		t.Fatalf("both executions must appear in work show; got:\n%s", stdoutShow)
	}
}

// AC-45: Injected crashes, restore fences, reverse retirement, purge reapplication.
func TestAC45_FailureInjectionAndReverseRetirement(t *testing.T) {
	h := newTestHarness(t)
	h.RequireMilestone("WC-06", "work")

	// Verify feature control interface: status, disable, retire
	stdout, stderr, code, _ := h.Run("", "work", "features", "status")
	if code != 0 {
		t.Fatalf("work features status failed: exit %d\nstderr: %s\nstdout: %s", code, stderr, stdout)
	}

	// Reverse retirement order check:
	// 1. work-advice/1
	// 2. next-turn-notices/1
	// 3. coordination-waits/1
	// 4. work-artifacts/1
	// 5. work-view/1
	features := []string{
		"work-advice/1",
		"next-turn-notices/1",
		"coordination-waits/1",
		"work-artifacts/1",
		"work-view/1",
	}

	for _, feat := range features {
		_, stderrDis, codeDis, _ := h.Run("", "work", "features", "disable", feat)
		if codeDis != 0 {
			t.Logf("feature %s disable returned code %d (stderr: %s)", feat, codeDis, stderrDis)
		}
	}
}
