package advice

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/google/uuid"
)

func sampleSnapshot() *Snapshot {
	return &Snapshot{
		BindingID:     uuid.NewString(),
		PlaneItemUUID: uuid.NewString(),
		Identifier:    "CAIRN-100",
		Title:         "Sample Work Item for Testing",
		CurrentState:  "In Progress",
		ExistingBlockers: []ExistingBlocker{
			{
				BlockerID: "77777777-7777-7777-7777-777777777777",
				Text:      "Waiting on upstream API spec",
				Revision:  1,
			},
		},
		CandidateArtifacts: []CandidateArtifact{
			{
				RevisionUUID: "88888888-8888-8888-8888-888888888888",
				Kind:         "git_commit",
				Locator:      "github.com/halbritt/cairn@abcdef1234567890abcdef1234567890abcdef12",
				Role:         "evidence",
			},
		},
		SourceReferences: []SnapshotSource{
			{
				ID:    "src-note-1",
				Kind:  "note",
				Title: "Architecture Decision Note",
			},
		},
	}
}

func TestParseProposal_Success(t *testing.T) {
	snap := sampleSnapshot()
	rawJSON := `{
		"actions": [
			{
				"action_type": "annotate_progress",
				"text": "Completed database schema implementation and verified migrations.",
				"source_references": ["src-note-1"],
				"rationale": "Clear milestone progress supported by source note."
			},
			{
				"action_type": "annotate_blocker",
				"blocker_action": "create",
				"text": "Blocked by external dependency review.",
				"rationale": "Independent reviewer pending signoff."
			},
			{
				"action_type": "annotate_blocker",
				"blocker_action": "resolve",
				"existing_blocker_id": "77777777-7777-7777-7777-777777777777",
				"expected_revision": 1,
				"text": "Upstream API spec received and integrated.",
				"rationale": "Blocker condition cleared."
			},
			{
				"action_type": "link_artifact",
				"artifact_revision_id": "88888888-8888-8888-8888-888888888888",
				"role": "evidence",
				"rationale": "Git commit contains implementation artifact."
			}
		],
		"summary": "Overall good progress with one resolved and one new blocker."
	}`

	prop, err := ParseProposal(rawJSON, snap)
	if err != nil {
		t.Fatalf("unexpected ParseProposal error: %v", err)
	}

	if len(prop.Actions) != 4 {
		t.Fatalf("expected 4 actions, got %d", len(prop.Actions))
	}
	if prop.Summary != "Overall good progress with one resolved and one new blocker." {
		t.Errorf("unexpected summary: %s", prop.Summary)
	}

	// Action 0: progress
	act0 := prop.Actions[0]
	if act0.ActionType != ActionAnnotateProgress || act0.Progress == nil {
		t.Fatalf("expected progress action, got %+v", act0)
	}
	if act0.Progress.Text != "Completed database schema implementation and verified migrations." {
		t.Errorf("unexpected progress text: %s", act0.Progress.Text)
	}
	if len(act0.Progress.SourceReferences) != 1 || act0.Progress.SourceReferences[0] != "src-note-1" {
		t.Errorf("unexpected sources: %+v", act0.Progress.SourceReferences)
	}

	// Action 1: blocker create
	act1 := prop.Actions[1]
	if act1.ActionType != ActionAnnotateBlocker || act1.Blocker == nil || act1.Blocker.BlockerAction != "create" {
		t.Fatalf("expected blocker create action, got %+v", act1)
	}

	// Action 2: blocker resolve
	act2 := prop.Actions[2]
	if act2.ActionType != ActionAnnotateBlocker || act2.Blocker == nil || act2.Blocker.BlockerAction != "resolve" {
		t.Fatalf("expected blocker resolve action, got %+v", act2)
	}
	if act2.Blocker.ExistingBlockerID != "77777777-7777-7777-7777-777777777777" || act2.Blocker.ExpectedRevision != 1 {
		t.Errorf("unexpected blocker resolve fields: %+v", act2.Blocker)
	}

	// Action 3: link artifact
	act3 := prop.Actions[3]
	if act3.ActionType != ActionLinkArtifact || act3.Artifact == nil {
		t.Fatalf("expected link artifact action, got %+v", act3)
	}
	if act3.Artifact.ArtifactRevisionID != "88888888-8888-8888-8888-888888888888" || act3.Artifact.Role != "evidence" {
		t.Errorf("unexpected artifact fields: %+v", act3.Artifact)
	}
}

func TestParseProposal_ValidEmpty(t *testing.T) {
	snap := sampleSnapshot()
	rawJSON := `{"actions": []}`
	prop, err := ParseProposal(rawJSON, snap)
	if err != nil {
		t.Fatalf("expected empty actions list to succeed, got %v", err)
	}
	if len(prop.Actions) != 0 {
		t.Errorf("expected 0 actions, got %d", len(prop.Actions))
	}
}

func TestParseProposal_Rejections(t *testing.T) {
	snap := sampleSnapshot()

	tests := []struct {
		name    string
		rawJSON string
		errCode string
	}{
		{
			name:    "markdown code fences",
			rawJSON: "```json\n{\"actions\": []}\n```",
			errCode: ErrInvalidProposal,
		},
		{
			name:    "surrounding prose preamble",
			rawJSON: "Here is your proposal:\n{\"actions\": []}",
			errCode: ErrInvalidProposal,
		},
		{
			name:    "trailing prose",
			rawJSON: "{\"actions\": []}\nHope this helps!",
			errCode: ErrInvalidProposal,
		},
		{
			name:    "multiple json objects",
			rawJSON: "{\"actions\": []} {\"actions\": []}",
			errCode: ErrInvalidProposal,
		},
		{
			name:    "unknown top-level field",
			rawJSON: `{"actions": [], "unexpected_field": true}`,
			errCode: ErrInvalidProposal,
		},
		{
			name:    "unknown action type",
			rawJSON: `{"actions": [{"action_type": "delete_all_files", "rationale": "cleanup"}]}`,
			errCode: ErrInvalidProposal,
		},
		{
			name:    "unknown action field in progress",
			rawJSON: `{"actions": [{"action_type": "annotate_progress", "text": "OK", "model_confidence": 0.99}]}`,
			errCode: ErrInvalidProposal,
		},
		{
			name:    "foreign artifact fields in progress",
			rawJSON: `{"actions": [{"action_type": "annotate_progress", "text": "OK", "artifact_revision_id": "88888888-8888-8888-8888-888888888888"}]}`,
			errCode: ErrInvalidProposal,
		},
		{
			name:    "foreign annotation fields in artifact link",
			rawJSON: `{"actions": [{"action_type": "link_artifact", "artifact_revision_id": "88888888-8888-8888-8888-888888888888", "role": "input", "text": "extra text"}]}`,
			errCode: ErrInvalidProposal,
		},
		{
			name:    "unknown source citation",
			rawJSON: `{"actions": [{"action_type": "annotate_progress", "text": "OK", "source_references": ["unknown-source-id"]}]}`,
			errCode: ErrInvalidProposal,
		},
		{
			name:    "artifact revision not in candidate list",
			rawJSON: `{"actions": [{"action_type": "link_artifact", "artifact_revision_id": "11111111-1111-1111-1111-111111111111", "role": "evidence"}]}`,
			errCode: ErrInvalidProposal,
		},
		{
			name:    "blocker resolve referencing unknown blocker",
			rawJSON: `{"actions": [{"action_type": "annotate_blocker", "blocker_action": "resolve", "existing_blocker_id": "11111111-1111-1111-1111-111111111111", "expected_revision": 1}]}`,
			errCode: ErrInvalidProposal,
		},
		{
			name:    "blocker resolve with revision mismatch",
			rawJSON: `{"actions": [{"action_type": "annotate_blocker", "blocker_action": "resolve", "existing_blocker_id": "77777777-7777-7777-7777-777777777777", "expected_revision": 999}]}`,
			errCode: ErrInvalidProposal,
		},
		{
			name:    "text exceeds 2048 bytes",
			rawJSON: fmt.Sprintf(`{"actions": [{"action_type": "annotate_progress", "text": %q}]}`, strings.Repeat("A", 2049)),
			errCode: ErrInvalidProposal,
		},
		{
			name:    "summary exceeds 2048 bytes",
			rawJSON: fmt.Sprintf(`{"actions": [], "summary": %q}`, strings.Repeat("S", 2049)),
			errCode: ErrInvalidProposal,
		},
		{
			name: "action count exceeds 12",
			rawJSON: func() string {
				acts := make([]string, 13)
				for i := range acts {
					acts[i] = `{"action_type": "annotate_progress", "text": "step"}`
				}
				return fmt.Sprintf(`{"actions": [%s]}`, strings.Join(acts, ","))
			}(),
			errCode: ErrInvalidProposal,
		},
		{
			name: "normalized proposal exceeds 16 KiB",
			rawJSON: func() string {
				// 9 actions each with 1900 bytes of text = ~17 KiB normalized proposal
				acts := make([]string, 9)
				for i := range acts {
					acts[i] = fmt.Sprintf(`{"action_type": "annotate_progress", "text": %q}`, strings.Repeat("X", 1900))
				}
				return fmt.Sprintf(`{"actions": [%s]}`, strings.Join(acts, ","))
			}(),
			errCode: ErrInvalidProposal,
		},
		{
			name:    "rationale exceeds 1024 bytes",
			rawJSON: fmt.Sprintf(`{"actions": [{"action_type": "annotate_progress", "text": "OK", "rationale": %q}]}`, strings.Repeat("R", 1025)),
			errCode: ErrInvalidProposal,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			_, err := ParseProposal(tc.rawJSON, snap)
			if err == nil {
				t.Fatalf("expected error for case %q, but got nil", tc.name)
			}
			var advErr *AdviceError
			if !strings.Contains(err.Error(), tc.errCode) {
				t.Errorf("expected error code %s in %v", tc.errCode, err)
			}
			_ = advErr
		})
	}
}

func TestParseProposal_EmptySelectionBypass(t *testing.T) {
	// Snapshot with ZERO candidate artifacts, ZERO existing blockers, and ZERO source references.
	emptySnap := &Snapshot{
		BindingID:          uuid.NewString(),
		PlaneItemUUID:      uuid.NewString(),
		Identifier:         "CAIRN-200",
		Title:              "Empty Candidate Sets Work Item",
		CandidateArtifacts: []CandidateArtifact{},
		ExistingBlockers:   []ExistingBlocker{},
		SourceReferences:   []SnapshotSource{},
	}

	// 1. Linking an artifact when CandidateArtifacts is empty MUST be rejected
	t.Run("empty candidate artifacts rejects any link_artifact", func(t *testing.T) {
		raw := fmt.Sprintf(`{"actions": [{"action_type": "link_artifact", "artifact_revision_id": %q, "role": "evidence"}]}`, uuid.NewString())
		_, err := ParseProposal(raw, emptySnap)
		if err == nil {
			t.Fatal("expected error, got nil")
		}
		if !strings.Contains(err.Error(), ErrInvalidProposal) {
			t.Errorf("expected INVALID_PROPOSAL, got %v", err)
		}
	})

	// 2. Resolving a blocker when ExistingBlockers is empty MUST be rejected
	t.Run("empty existing blockers rejects any blocker resolve", func(t *testing.T) {
		raw := fmt.Sprintf(`{"actions": [{"action_type": "annotate_blocker", "blocker_action": "resolve", "existing_blocker_id": %q, "expected_revision": 1}]}`, uuid.NewString())
		_, err := ParseProposal(raw, emptySnap)
		if err == nil {
			t.Fatal("expected error, got nil")
		}
		if !strings.Contains(err.Error(), ErrInvalidProposal) {
			t.Errorf("expected INVALID_PROPOSAL, got %v", err)
		}
	})

	// 3. Citing a source when SourceReferences is empty MUST be rejected
	t.Run("empty sources rejects any progress source reference", func(t *testing.T) {
		raw := `{"actions": [{"action_type": "annotate_progress", "text": "work done", "source_references": ["src-note-99"]}]}`
		_, err := ParseProposal(raw, emptySnap)
		if err == nil {
			t.Fatal("expected error, got nil")
		}
		if !strings.Contains(err.Error(), ErrInvalidProposal) {
			t.Errorf("expected INVALID_PROPOSAL, got %v", err)
		}
	})

	t.Run("empty sources rejects any blocker source reference", func(t *testing.T) {
		raw := `{"actions": [{"action_type": "annotate_blocker", "blocker_action": "create", "text": "blocked", "source_references": ["src-note-99"]}]}`
		_, err := ParseProposal(raw, emptySnap)
		if err == nil {
			t.Fatal("expected error, got nil")
		}
		if !strings.Contains(err.Error(), ErrInvalidProposal) {
			t.Errorf("expected INVALID_PROPOSAL, got %v", err)
		}
	})
}

func TestProcessGroup_Termination(t *testing.T) {
	// Script spawns child and sleeps, writing child PID to pidfile
	tmpDir := t.TempDir()
	pidFile := filepath.Join(tmpDir, "child.pid")
	script := fmt.Sprintf(`#!/bin/bash
sleep 30 &
echo $! > %s
wait
`, pidFile)
	scriptPath := filepath.Join(tmpDir, "sleeper.sh")
	if err := os.WriteFile(scriptPath, []byte(script), 0755); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()

	start := time.Now()
	_, err := RunIsolatedProcess(ctx, "/bin/bash", []string{scriptPath}, nil, BuildAllowlistedEnv(nil), tmpDir, nil)
	elapsed := time.Since(start)

	if err == nil {
		t.Fatal("expected timeout error, got nil")
	}
	if !strings.Contains(err.Error(), ErrModelTimeout) {
		t.Errorf("expected MODEL_TIMEOUT error, got %v", err)
	}
	if elapsed > 3*time.Second {
		t.Errorf("process termination took too long: %v (expected ~150ms)", elapsed)
	}

	// Verify the background child PID was terminated (syscall.Kill returns ESRCH)
	pidBytes, readErr := os.ReadFile(pidFile)
	if readErr != nil {
		t.Fatalf("failed to read child PID: %v", readErr)
	}
	childPID, convErr := strconv.Atoi(strings.TrimSpace(string(pidBytes)))
	if convErr != nil || childPID <= 1 {
		t.Fatalf("invalid child PID: %d (convErr: %v)", childPID, convErr)
	}
	killErr := syscall.Kill(childPID, 0)
	if !errors.Is(killErr, syscall.ESRCH) {
		t.Errorf("expected ESRCH for background child %d, got %v", childPID, killErr)
		_ = syscall.Kill(childPID, syscall.SIGKILL)
	}
}

func TestProcessGroup_LeaderExitWithDescendantFails(t *testing.T) {
	// Script spawns child, writes child PID, then leader exits 0 immediately.
	tmpDir := t.TempDir()
	pidFile := filepath.Join(tmpDir, "child.pid")
	script := fmt.Sprintf(`#!/bin/bash
sleep 30 &
echo $! > %s
exit 0
`, pidFile)
	scriptPath := filepath.Join(tmpDir, "leader_exit.sh")
	if err := os.WriteFile(scriptPath, []byte(script), 0755); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	_, err := RunIsolatedProcess(ctx, "/bin/bash", []string{scriptPath}, nil, BuildAllowlistedEnv(nil), tmpDir, nil)
	if err == nil {
		t.Fatal("expected error when leader exits while child descendant remains alive, but got success")
	}
	if !strings.Contains(err.Error(), ErrModelExecutionFailed) || !strings.Contains(err.Error(), "background descendant remained active") {
		t.Errorf("expected background descendant error, got: %v", err)
	}

	// Verify the descendant was killed
	pidBytes, readErr := os.ReadFile(pidFile)
	if readErr != nil {
		t.Fatalf("failed to read child PID: %v", readErr)
	}
	childPID, convErr := strconv.Atoi(strings.TrimSpace(string(pidBytes)))
	if convErr != nil || childPID <= 1 {
		t.Fatalf("invalid child PID: %d (convErr: %v)", childPID, convErr)
	}
	killErr := syscall.Kill(childPID, 0)
	if !errors.Is(killErr, syscall.ESRCH) {
		t.Errorf("descendant %d still alive after runner cleanup (expected ESRCH, got %v)", childPID, killErr)
		_ = syscall.Kill(childPID, syscall.SIGKILL)
	}
}

func TestProcessGroup_CleanupUnconfirmed(t *testing.T) {
	identity := ProcessIdentity{
		PID:        12345,
		PGID:       12345,
		StartTicks: 9876,
		BootID:     "test-boot-id",
	}
	err := &CleanupUnconfirmedError{
		Identity: identity,
		Cause:    errors.New("simulated probe failure"),
	}
	if !strings.Contains(err.Error(), "CLEANUP_UNCONFIRMED") || !strings.Contains(err.Error(), "12345") {
		t.Errorf("unexpected error string: %s", err.Error())
	}
	if err.Identity.BootID != "test-boot-id" || err.Identity.StartTicks != 9876 {
		t.Errorf("identity fields mismatch: %+v", err.Identity)
	}
}

func TestOutputLimit_Enforcement(t *testing.T) {
	// Script attempts to output 2 MiB (> 1 MiB limit)
	script := `#!/bin/bash
head -c 2000000 /dev/zero | tr '\0' 'A'
`
	tmpDir := t.TempDir()
	scriptPath := filepath.Join(tmpDir, "generator.sh")
	if err := os.WriteFile(scriptPath, []byte(script), 0755); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err := RunIsolatedProcess(ctx, "/bin/bash", []string{scriptPath}, nil, BuildAllowlistedEnv(nil), tmpDir, nil)
	if err == nil {
		t.Fatal("expected output limit error, got nil")
	}
	if !strings.Contains(err.Error(), ErrOutputLimit) {
		t.Errorf("expected OUTPUT_LIMIT error, got %v", err)
	}
}

func TestBuildAdvicePrompt(t *testing.T) {
	snap := sampleSnapshot()
	prompt, err := BuildAdvicePrompt(snap)
	if err != nil {
		t.Fatalf("BuildAdvicePrompt failed: %v", err)
	}
	if !strings.Contains(prompt, "You are a work coordination advisor") {
		t.Errorf("expected role instructions in prompt")
	}
	if !strings.Contains(prompt, snap.Identifier) {
		t.Errorf("expected snapshot identifier in prompt")
	}
	if !strings.Contains(prompt, "annotate_progress") || !strings.Contains(prompt, "annotate_blocker") || !strings.Contains(prompt, "link_artifact") {
		t.Errorf("expected allowed action types in prompt schema")
	}

	// Nil snapshot fails
	_, err = BuildAdvicePrompt(nil)
	if err == nil {
		t.Fatal("expected error for nil snapshot, got nil")
	}
}

func TestBuildAdvicePrompt_PromptSchemaRoundTrip(t *testing.T) {
	snap := sampleSnapshot()
	prompt, err := BuildAdvicePrompt(snap)
	if err != nil {
		t.Fatalf("BuildAdvicePrompt failed: %v", err)
	}

	// Verify the prompt explicitly advises flat actions and no nested wrapper objects
	if !strings.Contains(prompt, "Each action is a FLAT object") {
		t.Errorf("prompt missing flat action instruction")
	}

	// Construct a canonical payload matching the exact schema advertised in BuildAdvicePrompt
	payload := `{
  "summary": "Full coordination cycle with all advertised flat action types.",
  "actions": [
    {
      "action_type": "annotate_progress",
      "rationale": "Marked schema implementation completed.",
      "text": "Completed database schema implementation and verified migrations.",
      "source_references": ["src-note-1"]
    },
    {
      "action_type": "annotate_blocker",
      "rationale": "Need dependency review before landing.",
      "blocker_action": "create",
      "text": "Waiting on security review of external dependencies.",
      "source_references": ["src-note-1"]
    },
    {
      "action_type": "annotate_blocker",
      "rationale": "Upstream specification has been clarified.",
      "blocker_action": "resolve",
      "existing_blocker_id": "77777777-7777-7777-7777-777777777777",
      "expected_revision": 1,
      "text": "Upstream API specification confirmed and implemented.",
      "source_references": ["src-note-1"]
    },
    {
      "action_type": "link_artifact",
      "rationale": "Linked implementation commit.",
      "artifact_revision_id": "88888888-8888-8888-8888-888888888888",
      "role": "evidence"
    }
  ]
}`

	proposal, err := ParseProposal(payload, snap)
	if err != nil {
		t.Fatalf("ParseProposal refused canonical prompt-advertised schema payload: %v", err)
	}

	if len(proposal.Actions) != 4 {
		t.Fatalf("expected 4 actions, got %d", len(proposal.Actions))
	}

	// Verify action 0: annotate_progress
	act0 := proposal.Actions[0]
	if act0.ActionType != ActionAnnotateProgress || act0.Progress == nil {
		t.Fatalf("action 0 not parsed as annotate_progress: %+v", act0)
	}
	if act0.Progress.Text != "Completed database schema implementation and verified migrations." {
		t.Errorf("action 0 unexpected text: %q", act0.Progress.Text)
	}

	// Verify action 1: annotate_blocker create
	act1 := proposal.Actions[1]
	if act1.ActionType != ActionAnnotateBlocker || act1.Blocker == nil || act1.Blocker.BlockerAction != "create" {
		t.Fatalf("action 1 not parsed as blocker create: %+v", act1)
	}
	if act1.Blocker.Text != "Waiting on security review of external dependencies." {
		t.Errorf("action 1 unexpected text: %q", act1.Blocker.Text)
	}

	// Verify action 2: annotate_blocker resolve
	act2 := proposal.Actions[2]
	if act2.ActionType != ActionAnnotateBlocker || act2.Blocker == nil || act2.Blocker.BlockerAction != "resolve" {
		t.Fatalf("action 2 not parsed as blocker resolve: %+v", act2)
	}
	if act2.Blocker.ExistingBlockerID != "77777777-7777-7777-7777-777777777777" || act2.Blocker.ExpectedRevision != 1 {
		t.Errorf("action 2 unexpected resolve targets: %+v", act2.Blocker)
	}

	// Verify action 3: link_artifact
	act3 := proposal.Actions[3]
	if act3.ActionType != ActionLinkArtifact || act3.Artifact == nil {
		t.Fatalf("action 3 not parsed as link_artifact: %+v", act3)
	}
	if act3.Artifact.ArtifactRevisionID != "88888888-8888-8888-8888-888888888888" || act3.Artifact.Role != "evidence" {
		t.Errorf("action 3 unexpected artifact fields: %+v", act3.Artifact)
	}
}

func hashFileSHA256(path string) string {
	data, _ := os.ReadFile(path)
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}

func TestCodexSubscriptionAdapter_Verify(t *testing.T) {
	tmpDir := t.TempDir()
	codexHome := filepath.Join(tmpDir, "codex_home")
	if err := os.MkdirAll(codexHome, 0755); err != nil {
		t.Fatal(err)
	}

	validCommit := RequiredCodexUpstreamCommit
	validPatch := "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"

	capScript := fmt.Sprintf(`#!/bin/bash
if [[ "$1" == "cairn-advice" && "$2" == "--capabilities" ]]; then
  cat << 'EOF'
{"schema":"cairn.codex-advice-capabilities/1","request_schema":"cairn.codex-advice-request/1","result_schema":"cairn.codex-advice-result/1","provider":"openai","auth_method":"chatgpt_subscription","one_request":true,"tools":false,"retries":false,"upstream_commit":"%s","patch_sha256":"%s"}
EOF
  exit 0
fi
exit 1
`, validCommit, validPatch)

	binPath := filepath.Join(tmpDir, "codex_bin")
	if err := os.WriteFile(binPath, []byte(capScript), 0755); err != nil {
		t.Fatal(err)
	}
	actualSHA := hashFileSHA256(binPath)

	// 1. Success with valid build stamp and matching binary SHA
	adapter := NewCodexSubscriptionAdapter(binPath, actualSHA, "gpt-4o", codexHome, validPatch)

	caps, err := adapter.VerifyCapabilities(context.Background())
	if err != nil {
		t.Fatalf("unexpected VerifyCapabilities error: %v", err)
	}
	if caps.Provider != "openai" || caps.AuthMethod != "chatgpt_subscription" || !caps.OneRequest || caps.Tools || caps.Retries {
		t.Errorf("unexpected caps: %+v", caps)
	}

	// 2. Binary SHA mismatch refused
	adapterBadSHA := NewCodexSubscriptionAdapter(binPath, "0000000000000000000000000000000000000000000000000000000000000000", "gpt-4o", codexHome, validPatch)
	_, err = adapterBadSHA.VerifyCapabilities(context.Background())
	if err == nil || !strings.Contains(err.Error(), ErrSubscriptionRequired) {
		t.Fatalf("expected SUBSCRIPTION_REQUIRED for binary SHA mismatch, got %v", err)
	}

	// 3. Unverified or invalid patch SHA refused
	adapterUnverified := NewCodexSubscriptionAdapter(binPath, actualSHA, "gpt-4o", codexHome, "unverified")
	_, err = adapterUnverified.VerifyCapabilities(context.Background())
	if err == nil || !strings.Contains(err.Error(), "invalid expected patch_sha256") {
		t.Fatalf("expected invalid expected patch_sha256 refusal, got %v", err)
	}

	// 4. Missing CODEX_HOME refused
	adapterNoHome := NewCodexSubscriptionAdapter(binPath, actualSHA, "gpt-4o", filepath.Join(tmpDir, "nonexistent"), validPatch)
	_, err = adapterNoHome.VerifyCapabilities(context.Background())
	if err == nil || !strings.Contains(err.Error(), "CODEX_HOME") {
		t.Fatalf("expected CODEX_HOME refusal, got %v", err)
	}

	// 5. Upstream commit mismatch refused
	adapterMismatchCommit := NewCodexSubscriptionAdapter(binPath, actualSHA, "gpt-4o", codexHome, validPatch)
	adapterMismatchCommit.ExpectedUpstreamCommit = "0000000000000000000000000000000000000000"
	_, err = adapterMismatchCommit.VerifyCapabilities(context.Background())
	if err == nil || !strings.Contains(err.Error(), "upstream_commit mismatch") {
		t.Fatalf("expected upstream_commit mismatch refusal, got %v", err)
	}

	// 6. Patch SHA mismatch refused
	adapterMismatchPatch := NewCodexSubscriptionAdapter(binPath, actualSHA, "gpt-4o", codexHome, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
	_, err = adapterMismatchPatch.VerifyCapabilities(context.Background())
	if err == nil || !strings.Contains(err.Error(), "patch_sha256 mismatch") {
		t.Fatalf("expected patch_sha256 mismatch refusal, got %v", err)
	}
}

func TestCodexSubscriptionAdapter_Generate(t *testing.T) {
	tmpDir := t.TempDir()
	codexHome := filepath.Join(tmpDir, "codex_home")
	if err := os.MkdirAll(codexHome, 0755); err != nil {
		t.Fatal(err)
	}
	snap := sampleSnapshot()

	validCommit := RequiredCodexUpstreamCommit
	validPatch := "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"

	// 1. Successful generation script emitting native terminal envelope
	genScript := fmt.Sprintf(`#!/bin/bash
if [[ "$1" == "cairn-advice" && "$2" == "--capabilities" ]]; then
  cat << 'EOF'
{"schema":"cairn.codex-advice-capabilities/1","request_schema":"cairn.codex-advice-request/1","result_schema":"cairn.codex-advice-result/1","provider":"openai","auth_method":"chatgpt_subscription","one_request":true,"tools":false,"retries":false,"upstream_commit":"%s","patch_sha256":"%s"}
EOF
  exit 0
fi

# Verify subcommand
if [[ "$1" != "cairn-advice" ]]; then
  echo "Expected cairn-advice subcommand, got $1" >&2
  exit 1
fi

# Verify required flags
for flag in "--model" "--timeout-ms" "--max-output-bytes"; do
  found=false
  for arg in "$@"; do
    if [[ "$arg" == "$flag" ]]; then found=true; break; fi
  done
  if [[ "$found" != "true" ]]; then
    echo "Missing required flag: $flag" >&2
    exit 2
  fi
done

# Read stdin request to get request_id
stdin_content=$(cat)
req_id=$(echo "$stdin_content" | grep -o '"request_id":"[^"]*' | cut -d'"' -f4)

cat << EOF
{
  "schema": "cairn.codex-advice-result/1",
  "request_id": "$req_id",
  "ok": true,
  "model": "gpt-4o",
  "provider": "openai",
  "auth_method": "chatgpt_subscription",
  "result": "{\"actions\":[{\"action_type\":\"annotate_progress\",\"text\":\"All tests green.\",\"rationale\":\"Passing test suite\"}]}"
}
EOF
exit 0
`, validCommit, validPatch)

	cliGen := filepath.Join(tmpDir, "codex_gen.sh")
	if err := os.WriteFile(cliGen, []byte(genScript), 0755); err != nil {
		t.Fatal(err)
	}
	actualSHA := hashFileSHA256(cliGen)

	adapter := NewCodexSubscriptionAdapter(cliGen, actualSHA, "gpt-4o", codexHome, validPatch)

	prop, res, err := adapter.GenerateAdvice(context.Background(), snap, 5*time.Second, nil)
	if err != nil {
		t.Fatalf("unexpected GenerateAdvice error: %v", err)
	}
	if res.ExitCode != 0 {
		t.Errorf("expected exit code 0, got %d", res.ExitCode)
	}
	if len(prop.Actions) != 1 || prop.Actions[0].Progress.Text != "All tests green." {
		t.Errorf("unexpected parsed actions: %+v", prop.Actions)
	}

	// 2. Missing Model parameter refused
	adapterNoModel := NewCodexSubscriptionAdapter(cliGen, actualSHA, "", codexHome, validPatch)
	_, _, err = adapterNoModel.GenerateAdvice(context.Background(), snap, 5*time.Second, nil)
	if err == nil || !strings.Contains(err.Error(), ErrSubscriptionRequired) {
		t.Fatalf("expected SUBSCRIPTION_REQUIRED for empty model, got %v", err)
	}

	// 3. Error envelopes classified properly
	errorCases := []struct {
		code     string
		expected string
	}{
		{"AUTH_UNAVAILABLE", ErrSubscriptionRequired},
		{"POLICY_REFUSED", ErrSubscriptionRequired},
		{"QUOTA_EXHAUSTED", ErrQuotaExhausted},
		{"TIMEOUT", ErrModelTimeout},
		{"OUTPUT_LIMIT", ErrOutputLimit},
		{"INVALID_REQUEST", ErrInvalidProposal},
		{"CANCELLED", ErrCancelled},
		{"PROVIDER_ERROR", ErrProviderError},
	}

	for _, ec := range errorCases {
		t.Run(ec.code, func(t *testing.T) {
			s := fmt.Sprintf(`#!/bin/bash
if [[ "$1" == "cairn-advice" && "$2" == "--capabilities" ]]; then
  cat << 'EOF'
{"schema":"cairn.codex-advice-capabilities/1","request_schema":"cairn.codex-advice-request/1","result_schema":"cairn.codex-advice-result/1","provider":"openai","auth_method":"chatgpt_subscription","one_request":true,"tools":false,"retries":false,"upstream_commit":"%s","patch_sha256":"%s"}
EOF
  exit 0
fi
stdin_content=$(cat)
req_id=$(echo "$stdin_content" | grep -o '"request_id":"[^"]*' | cut -d'"' -f4)
cat << EOF
{
  "schema": "cairn.codex-advice-result/1",
  "request_id": "$req_id",
  "ok": false,
  "error": {
    "code": "%s",
    "message": "test failure message"
  }
}
EOF
exit 1
`, validCommit, validPatch, ec.code)
			scriptP := filepath.Join(tmpDir, "error_"+ec.code+".sh")
			_ = os.WriteFile(scriptP, []byte(s), 0755)
			scriptSHA := hashFileSHA256(scriptP)
			ad := NewCodexSubscriptionAdapter(scriptP, scriptSHA, "gpt-4o", codexHome, validPatch)
			_, _, err := ad.GenerateAdvice(context.Background(), snap, 5*time.Second, nil)
			if err == nil {
				t.Fatalf("expected error for code %q, got nil", ec.code)
			}
			if !strings.Contains(err.Error(), ec.expected) {
				t.Errorf("expected %s in error, got %v", ec.expected, err)
			}
		})
	}

	// 4. Schema and route violation checks
	envelopeCases := []struct {
		name    string
		stdout  string
		errCode string
	}{
		{
			name:    "wrong schema",
			stdout:  `{"schema": "wrong.schema/1", "request_id": "%s", "ok": true, "model": "gpt-4o", "provider": "openai", "auth_method": "chatgpt_subscription", "result": "{\"actions\":[]}"}`,
			errCode: ErrInvalidProposal,
		},
		{
			name:    "wrong provider",
			stdout:  `{"schema": "cairn.codex-advice-result/1", "request_id": "%s", "ok": true, "model": "gpt-4o", "provider": "anthropic", "auth_method": "chatgpt_subscription", "result": "{\"actions\":[]}"}`,
			errCode: ErrSubscriptionRequired,
		},
		{
			name:    "wrong auth_method",
			stdout:  `{"schema": "cairn.codex-advice-result/1", "request_id": "%s", "ok": true, "model": "gpt-4o", "provider": "openai", "auth_method": "api_key", "result": "{\"actions\":[]}"}`,
			errCode: ErrSubscriptionRequired,
		},
		{
			name:    "model mismatch",
			stdout:  `{"schema": "cairn.codex-advice-result/1", "request_id": "%s", "ok": true, "model": "claude-3", "provider": "openai", "auth_method": "chatgpt_subscription", "result": "{\"actions\":[]}"}`,
			errCode: ErrSubscriptionRequired,
		},
		{
			name:    "empty result string",
			stdout:  `{"schema": "cairn.codex-advice-result/1", "request_id": "%s", "ok": true, "model": "gpt-4o", "provider": "openai", "auth_method": "chatgpt_subscription", "result": ""}`,
			errCode: ErrInvalidProposal,
		},
		{
			name:    "null request_id on ok:true refused",
			stdout:  `{"schema": "cairn.codex-advice-result/1", "request_id": null, "ok": true, "model": "gpt-4o", "provider": "openai", "auth_method": "chatgpt_subscription", "result": "{\"actions\":[]}"}`,
			errCode: ErrInvalidProposal,
		},
	}

	for _, tc := range envelopeCases {
		t.Run(tc.name, func(t *testing.T) {
			template := strings.ReplaceAll(tc.stdout, "%s", "$req_id")
			s := fmt.Sprintf(`#!/bin/bash
if [[ "$1" == "cairn-advice" && "$2" == "--capabilities" ]]; then
  cat << 'EOF'
{"schema":"cairn.codex-advice-capabilities/1","request_schema":"cairn.codex-advice-request/1","result_schema":"cairn.codex-advice-result/1","provider":"openai","auth_method":"chatgpt_subscription","one_request":true,"tools":false,"retries":false,"upstream_commit":"%s","patch_sha256":"%s"}
EOF
  exit 0
fi
stdin_content=$(cat)
req_id=$(echo "$stdin_content" | grep -o '"request_id":"[^"]*' | cut -d'"' -f4)
cat << EOF
%s
EOF
exit 0
`, validCommit, validPatch, template)
			scriptP := filepath.Join(tmpDir, "envelope_"+filepath.Base(tc.name)+".sh")
			_ = os.WriteFile(scriptP, []byte(s), 0755)
			scriptSHA := hashFileSHA256(scriptP)
			ad := NewCodexSubscriptionAdapter(scriptP, scriptSHA, "gpt-4o", codexHome, validPatch)
			_, _, err := ad.GenerateAdvice(context.Background(), snap, 5*time.Second, nil)
			if err == nil {
				t.Fatalf("expected error for case %q, got nil", tc.name)
			}
			if !strings.Contains(err.Error(), tc.errCode) {
				t.Errorf("expected %s, got %v", tc.errCode, err)
			}
		})
	}
}

func TestProcessGroup_OnLaunchGate(t *testing.T) {
	tmpDir := t.TempDir()
	scriptPath := filepath.Join(tmpDir, "gate_test.sh")
	script := `#!/usr/bin/env bash
read -r line
echo "received:$line"
exit 0
`
	if err := os.WriteFile(scriptPath, []byte(script), 0755); err != nil {
		t.Fatal(err)
	}

	// 1. Successful onLaunch: captures real PID, PGID, StartTicks, BootID, and input is received
	var captured ProcessIdentity
	launchCalled := false
	onLaunchSuccess := func(id ProcessIdentity) error {
		launchCalled = true
		captured = id
		if id.PID <= 0 || id.PGID <= 0 {
			return fmt.Errorf("invalid identity PID/PGID: %+v", id)
		}
		return nil
	}

	ctx := context.Background()
	res, err := RunIsolatedProcess(ctx, "/bin/bash", []string{scriptPath}, []byte("hello\n"), BuildAllowlistedEnv(nil), tmpDir, onLaunchSuccess)
	if err != nil {
		t.Fatalf("expected successful RunIsolatedProcess with onLaunch, got %v", err)
	}
	if !launchCalled {
		t.Fatal("onLaunch was not called")
	}
	if captured.PID != res.Identity.PID {
		t.Errorf("expected captured PID %d == result PID %d", captured.PID, res.Identity.PID)
	}
	if !strings.Contains(string(res.Stdout), "received:hello") {
		t.Errorf("expected child to receive input after onLaunch success, got: %s", string(res.Stdout))
	}

	// 2. Failed onLaunch: child is killed via SIGKILL, no input delivered, returns error
	onLaunchFail := func(id ProcessIdentity) error {
		return errors.New("simulated launch refusal")
	}
	_, err = RunIsolatedProcess(ctx, "/bin/bash", []string{scriptPath}, []byte("hello\n"), BuildAllowlistedEnv(nil), tmpDir, onLaunchFail)
	if err == nil {
		t.Fatal("expected error on failed onLaunch, got nil")
	}
	if !strings.Contains(err.Error(), "onLaunch gate failed") {
		t.Errorf("expected onLaunch gate failed error, got: %v", err)
	}
}
