package core

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/advice"
	"github.com/halbritt/cairn/internal/plane"
	"github.com/jackc/pgx/v5/pgxpool"
)

func TestWorkAdviceMigrationAndFeatureControl(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	agentStore := testStore(t, Channel{Principal: "agent:test", Operator: false})

	// 1. Feature should start disabled by migration
	enabled, err := operatorStore.CheckAdviceFeature(ctx)
	if err != nil {
		t.Fatalf("CheckAdviceFeature error: %v", err)
	}
	if enabled {
		t.Fatalf("expected work-advice/1 to be disabled by default")
	}

	// 2. Non-operator caller is denied
	err = agentStore.SetAdviceFeature(ctx, "active")
	requireCode(t, err, "AUTHORITY_DENIED")

	// 3. Operator can enable feature
	if err := operatorStore.SetAdviceFeature(ctx, "active"); err != nil {
		t.Fatalf("SetAdviceFeature active error: %v", err)
	}
	enabled, err = operatorStore.CheckAdviceFeature(ctx)
	if err != nil || !enabled {
		t.Fatalf("expected work-advice/1 to be active, got %v (err: %v)", enabled, err)
	}

	// 4. Operator can disable feature
	if err := operatorStore.SetAdviceFeature(ctx, "disabled"); err != nil {
		t.Fatalf("SetAdviceFeature disabled error: %v", err)
	}
	enabled, err = operatorStore.CheckAdviceFeature(ctx)
	if err != nil || enabled {
		t.Fatalf("expected work-advice/1 to be disabled, got %v (err: %v)", enabled, err)
	}
}

func TestWorkAdviceJobLifecycle(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	agentStore := testStore(t, Channel{Principal: "agent:test", Operator: false})

	_ = operatorStore.SetAdviceFeature(ctx, "active")

	jobUUID := uuid.New()
	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	requestUUID := uuid.New()

	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	job := &WorkAdviceJob{
		JobUUID:             jobUUID,
		BindingID:           bindingID,
		PlaneItemUUID:       planeItemUUID,
		ProfileName:         advice.RouteCodexSubscription,
		ModelRoute:          "gpt-4o",
		TemplateVersion:     1,
		RequestUUID:         requestUUID,
		InputManifestDigest: "manifest-sha256-test",
		InputPayloadDigest:  "payload-sha256-test",
		Status:              "running",
		DeadlineSeconds:     90,
		ExpiresAt:           time.Now().Add(1 * time.Hour),
	}

	// Non-operator denied
	err := agentStore.CreateAdviceJob(ctx, job)
	requireCode(t, err, "AUTHORITY_DENIED")

	// Operator creates job
	if err := operatorStore.CreateAdviceJob(ctx, job); err != nil {
		t.Fatalf("CreateAdviceJob failed: %v", err)
	}

	// Retrieve job
	retrieved, err := operatorStore.GetAdviceJob(ctx, jobUUID)
	if err != nil {
		t.Fatalf("GetAdviceJob failed: %v", err)
	}
	if retrieved.JobUUID != jobUUID || retrieved.BindingID != bindingID || retrieved.PlaneItemUUID != planeItemUUID {
		t.Errorf("job identity mismatch: got %+v", retrieved)
	}
	if retrieved.Status != "running" || retrieved.DeadlineSeconds != 90 {
		t.Errorf("job status/deadline mismatch: got %+v", retrieved)
	}

	// Non-operator cannot finish job
	err = agentStore.FinishAdviceJob(ctx, jobUUID, "completed", "")
	requireCode(t, err, "AUTHORITY_DENIED")

	// Operator finishes job
	if err := operatorStore.FinishAdviceJob(ctx, jobUUID, "completed", ""); err != nil {
		t.Fatalf("FinishAdviceJob failed: %v", err)
	}

	retrieved, err = operatorStore.GetAdviceJob(ctx, jobUUID)
	if err != nil {
		t.Fatalf("GetAdviceJob after finish failed: %v", err)
	}
	if retrieved.Status != "completed" || retrieved.FinishedAt == nil {
		t.Errorf("expected completed status with non-nil FinishedAt: got %+v", retrieved)
	}
}

func TestWorkAdviceProposalAndActions(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	agentStore := testStore(t, Channel{Principal: "agent:test", Operator: false})

	_ = operatorStore.SetAdviceFeature(ctx, "active")

	jobUUID := uuid.New()
	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	requestUUID := uuid.New()

	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	job := &WorkAdviceJob{
		JobUUID:             jobUUID,
		BindingID:           bindingID,
		PlaneItemUUID:       planeItemUUID,
		ProfileName:         advice.RouteCodexSubscription,
		ModelRoute:          "gpt-4o",
		TemplateVersion:     1,
		RequestUUID:         requestUUID,
		InputManifestDigest: "manifest-digest",
		InputPayloadDigest:  "payload-digest",
		Status:              "completed",
		ExpiresAt:           time.Now().Add(1 * time.Hour),
	}
	if err := operatorStore.CreateAdviceJob(ctx, job); err != nil {
		t.Fatalf("CreateAdviceJob failed: %v", err)
	}

	proposalUUID := uuid.New()
	proposal := &WorkAdviceProposal{
		ProposalUUID:      proposalUUID,
		JobUUID:           jobUUID,
		BindingID:         bindingID,
		PlaneItemUUID:     planeItemUUID,
		ReviewVersion:     1,
		Status:            "pending",
		RawResult:         `{"actions":[{"action_type":"annotate_progress"}]}`,
		NormalizedPayload: json.RawMessage(`{"actions":[]}`),
		ExpiresAt:         time.Now().Add(1 * time.Hour),
	}

	actions := []*WorkAdviceAction{
		{
			ActionUUID:    uuid.New(),
			ActionIndex:   0,
			ActionType:    "annotate_progress",
			TargetPayload: json.RawMessage(`{"text":"progress summary"}`),
			Rationale:     "rationale 0",
		},
		{
			ActionUUID:    uuid.New(),
			ActionIndex:   1,
			ActionType:    "annotate_blocker",
			TargetPayload: json.RawMessage(`{"blocker_action":"create","text":"blocker note"}`),
			Rationale:     "rationale 1",
		},
	}

	// Non-operator denied
	err := agentStore.RecordAdviceProposal(ctx, proposal, actions)
	requireCode(t, err, "AUTHORITY_DENIED")

	// Operator records proposal
	if err := operatorStore.RecordAdviceProposal(ctx, proposal, actions); err != nil {
		t.Fatalf("RecordAdviceProposal failed: %v", err)
	}

	// Retrieve proposal and actions
	gotProp, gotActions, err := operatorStore.GetAdviceProposal(ctx, proposalUUID)
	if err != nil {
		t.Fatalf("GetAdviceProposal failed: %v", err)
	}
	if gotProp.ProposalUUID != proposalUUID || gotProp.Status != "pending" || gotProp.ReviewVersion != 1 {
		t.Errorf("proposal mismatch: got %+v", gotProp)
	}
	if len(gotActions) != 2 {
		t.Fatalf("expected 2 actions, got %d", len(gotActions))
	}
	if gotActions[0].ActionIndex != 0 || gotActions[0].ActionType != "annotate_progress" {
		t.Errorf("action 0 mismatch: got %+v", gotActions[0])
	}
	if gotActions[1].ActionIndex != 1 || gotActions[1].ActionType != "annotate_blocker" {
		t.Errorf("action 1 mismatch: got %+v", gotActions[1])
	}

	// List proposals
	list, err := operatorStore.ListAdviceProposals(ctx, bindingID, planeItemUUID)
	if err != nil {
		t.Fatalf("ListAdviceProposals failed: %v", err)
	}
	if len(list) != 1 || list[0].ProposalUUID != proposalUUID {
		t.Errorf("unexpected list result: %+v", list)
	}

	// Reject proposal with version conflict check
	err = operatorStore.RejectAdviceProposal(ctx, proposalUUID, 999) // wrong version
	requireCode(t, err, "VERSION_CONFLICT")

	// Successful rejection
	if err := operatorStore.RejectAdviceProposal(ctx, proposalUUID, 1); err != nil {
		t.Fatalf("RejectAdviceProposal failed: %v", err)
	}

	// Verify rejected status
	gotProp, _, err = operatorStore.GetAdviceProposal(ctx, proposalUUID)
	if err != nil {
		t.Fatalf("GetAdviceProposal after reject failed: %v", err)
	}
	if gotProp.Status != "rejected" || gotProp.ReviewVersion != 2 {
		t.Errorf("expected rejected status with review_version 2: got %+v", gotProp)
	}

	// Reject again fails with INVALID_STATE (already rejected)
	err = operatorStore.RejectAdviceProposal(ctx, proposalUUID, 2)
	requireCode(t, err, "INVALID_STATE")
}

type mockAdviceProvider struct {
	proposal *advice.Proposal
	err      error
}

func (m *mockAdviceProvider) Name() string { return "mock-provider" }
func (m *mockAdviceProvider) VerifyCapabilities(ctx context.Context) (*advice.CodexCapabilities, error) {
	return &advice.CodexCapabilities{
		Schema:         advice.CodexAdviceCapabilitiesSchema,
		RequestSchema:  advice.CodexAdviceRequestSchema,
		ResultSchema:   advice.CodexAdviceResultSchema,
		Provider:       "openai",
		AuthMethod:     "chatgpt_subscription",
		OneRequest:     true,
		Tools:          false,
		Retries:        false,
		UpstreamCommit: advice.RequiredCodexUpstreamCommit,
		PatchSHA256:    "0000000000000000000000000000000000000000000000000000000000000000",
	}, nil
}
func (m *mockAdviceProvider) GenerateAdvice(ctx context.Context, snapshot *advice.Snapshot, deadline time.Duration, onLaunch func(advice.ProcessIdentity) error) (*advice.Proposal, *advice.ProcessResult, error) {
	if onLaunch != nil {
		_ = onLaunch(advice.ProcessIdentity{PID: 1234, PGID: 1234, StartTicks: 100, BootID: "test-boot"})
	}
	if m.err != nil {
		return nil, nil, m.err
	}
	return m.proposal, &advice.ProcessResult{Stdout: []byte(`{"actions":[]}`), ExitCode: 0, Identity: advice.ProcessIdentity{PID: 1234, PGID: 1234, StartTicks: 100, BootID: "test-boot"}}, nil
}

func seedWorkAdviceTestEntities(t *testing.T, ctx context.Context, s *Store, bindingID, planeItemUUID uuid.UUID) {
	t.Helper()
	slug := "test-proj-" + bindingID.String()[:8]
	_, err := s.pool.Exec(ctx, `
		INSERT INTO cairn.work_project (
			binding_id, local_slug, collection, plane_origin, workspace_uuid, project_uuid, config_revision,
			plane_instance, status, auth_rejected
		) VALUES (
			$1, $2, 'fixture:repo', 'https://plane.test', gen_random_uuid(), gen_random_uuid(), '1',
			'work', 'active', false
		) ON CONFLICT (binding_id) DO UPDATE SET
			status = 'active', auth_rejected = false
	`, bindingID, slug)
	if err != nil {
		t.Fatalf("failed to seed work_project: %v", err)
	}

	_, err = s.pool.Exec(ctx, `
		INSERT INTO cairn.work_item (
			binding_id, plane_item_uuid, identifier, title,
			state_id, state_name, state_group, cache_generation, is_absent, last_refreshed_at
		) VALUES (
			$1, $2, 'TEST-1', 'Test Item',
			gen_random_uuid(), 'In Progress', 'started', 1, false, clock_timestamp()
		) ON CONFLICT (binding_id, plane_item_uuid) DO UPDATE SET
			is_absent = false, last_refreshed_at = clock_timestamp(), cache_generation = 1
	`, bindingID, planeItemUUID)
	if err != nil {
		t.Fatalf("failed to seed work_item: %v", err)
	}

	err = s.SetWorkAdvicePolicy(ctx, bindingID, []string{"identifier", "title", "state", "blockers", "artifacts", "sources"})
	if err != nil {
		t.Fatalf("failed to set advice policy: %v", err)
	}
}

func TestWorkAdviceExecuteJob_LifecycleAndEligibility(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	jobUUID := uuid.New()
	reqUUID := uuid.New()

	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	shareableRec, err := operatorStore.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft: Draft{
			Kind:        "note",
			Body:        "Valid shareable note content",
			Sensitivity: "shareable",
			Scope:       Scope{Repo: "fixture:repo", TaskID: uuid.NewString(), RunID: uuid.NewString()},
			ClaimType:   "self",
		},
	})
	if err != nil {
		t.Fatalf("Create shareable note failed: %v", err)
	}
	shareableUUID, _ := uuid.Parse(shareableRec.RecordID)

	privateRec, err := operatorStore.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft: Draft{
			Kind:        "note",
			Body:        "Secret private note content",
			Sensitivity: "local",
			Scope:       Scope{Repo: "fixture:repo", TaskID: uuid.NewString(), RunID: uuid.NewString()},
			ClaimType:   "self",
		},
	})
	if err != nil {
		t.Fatalf("Create private note failed: %v", err)
	}
	privateUUID, _ := uuid.Parse(privateRec.RecordID)

	spec := AdviceInputSpec{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		SelectedSources: []SourceRefSpec{
			{RecordID: shareableUUID, Version: shareableRec.Version},
		},
	}

	ineligibleSpec := AdviceInputSpec{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		SelectedSources: []SourceRefSpec{
			{RecordID: privateUUID, Version: privateRec.Version},
		},
	}

	job := &WorkAdviceJob{
		JobUUID:         jobUUID,
		BindingID:       bindingID,
		PlaneItemUUID:   planeItemUUID,
		ProfileName:     "mock-profile",
		ModelRoute:      "mock-route",
		TemplateVersion: 1,
		RequestUUID:     reqUUID,
		DeadlineSeconds: 30,
		ExpiresAt:       time.Now().Add(1 * time.Hour),
	}

	mockProv := &mockAdviceProvider{
		proposal: &advice.Proposal{
			Actions: []advice.ProposedAction{
				{
					ActionIndex: 0,
					ActionType:  advice.ActionAnnotateProgress,
					Progress:    &advice.ProgressPayload{Text: "Progress made"},
					Rationale:   "Test rationale",
				},
			},
			Summary: "Mock advice summary",
		},
	}

	// 1. Feature disabled -> fails FEATURE_DISABLED
	_ = operatorStore.SetAdviceFeature(ctx, "disabled")
	_, _, err = operatorStore.ExecuteAdviceJob(ctx, job, spec, mockProv)
	requireCode(t, err, "FEATURE_DISABLED")

	// 2. Enable feature
	if err := operatorStore.SetAdviceFeature(ctx, "active"); err != nil {
		t.Fatalf("SetAdviceFeature active error: %v", err)
	}

	// 3. Ineligible source -> fails SOURCE_INELIGIBLE
	_, _, err = operatorStore.ExecuteAdviceJob(ctx, job, ineligibleSpec, mockProv)
	requireCode(t, err, "SOURCE_INELIGIBLE")

	// 4. Successful execution
	prop, actions, err := operatorStore.ExecuteAdviceJob(ctx, job, spec, mockProv)
	if err != nil {
		t.Fatalf("ExecuteAdviceJob failed: %v", err)
	}
	if prop == nil || len(actions) != 1 {
		t.Fatalf("expected 1 action and non-nil proposal: %+v, %+v", prop, actions)
	}

	// Verify job transitioned to completed
	retrievedJob, err := operatorStore.GetAdviceJob(ctx, jobUUID)
	if err != nil {
		t.Fatalf("GetAdviceJob failed: %v", err)
	}
	if retrievedJob.Status != "completed" || retrievedJob.FinishedAt == nil {
		t.Errorf("expected completed job status, got %+v", retrievedJob)
	}
}

func TestWorkAdviceApplyAndUndoProposal(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	jobUUID := uuid.New()
	reqUUID := uuid.New()

	_ = operatorStore.SetAdviceFeature(ctx, "active")
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	job := &WorkAdviceJob{
		JobUUID:         jobUUID,
		BindingID:       bindingID,
		PlaneItemUUID:   planeItemUUID,
		ProfileName:     "mock-profile",
		ModelRoute:      "mock-route",
		TemplateVersion: 1,
		RequestUUID:     reqUUID,
		DeadlineSeconds: 30,
		Status:          "completed",
		ExpiresAt:       time.Now().Add(1 * time.Hour),
	}
	if err := operatorStore.CreateAdviceJob(ctx, job); err != nil {
		t.Fatalf("CreateAdviceJob failed: %v", err)
	}

	proposalUUID := uuid.New()
	prop := &WorkAdviceProposal{
		ProposalUUID:      proposalUUID,
		JobUUID:           jobUUID,
		BindingID:         bindingID,
		PlaneItemUUID:     planeItemUUID,
		ReviewVersion:     1,
		Status:            "pending",
		RawResult:         `{"actions":[]}`,
		NormalizedPayload: json.RawMessage(`{"actions":[]}`),
		ExpiresAt:         time.Now().Add(1 * time.Hour),
	}
	action0 := uuid.New()
	action1 := uuid.New()
	actions := []*WorkAdviceAction{
		{
			ActionUUID:    action0,
			ActionIndex:   0,
			ActionType:    "annotate_progress",
			TargetPayload: json.RawMessage(`{"text":"progress 0"}`),
			Rationale:     "rationale 0",
		},
		{
			ActionUUID:    action1,
			ActionIndex:   1,
			ActionType:    "annotate_blocker",
			TargetPayload: json.RawMessage(`{"blocker_action":"create","text":"blocker 1"}`),
			Rationale:     "rationale 1",
		},
	}
	if err := operatorStore.RecordAdviceProposal(ctx, prop, actions); err != nil {
		t.Fatalf("RecordAdviceProposal failed: %v", err)
	}

	// 1. Version conflict on apply
	applyReqUUID := uuid.New()
	receipt := &WorkAdviceApplicationReceipt{
		RequestUUID:  applyReqUUID,
		ProposalUUID: proposalUUID,
	}
	_, err := operatorStore.ApplyAdviceProposal(ctx, receipt, 999, []uuid.UUID{action0})
	requireCode(t, err, "VERSION_CONFLICT")

	// 2. Successful partial apply of action 0 (leaves action 1 pending)
	appliedReceipt, err := operatorStore.ApplyAdviceProposal(ctx, receipt, 1, []uuid.UUID{action0})
	if err != nil {
		t.Fatalf("ApplyAdviceProposal failed: %v", err)
	}
	if appliedReceipt == nil || appliedReceipt.Undone {
		t.Errorf("unexpected applied receipt: %+v", appliedReceipt)
	}

	// 3. Idempotent retry returns identical receipt
	retryReceipt, err := operatorStore.ApplyAdviceProposal(ctx, receipt, 1, []uuid.UUID{action0})
	if err != nil {
		t.Fatalf("idempotent ApplyAdviceProposal retry failed: %v", err)
	}
	if retryReceipt.ReceiptUUID != appliedReceipt.ReceiptUUID {
		t.Errorf("receipt UUID changed on retry: %s vs %s", retryReceipt.ReceiptUUID, appliedReceipt.ReceiptUUID)
	}

	// 4. Verify proposal is partially_applied (because action 1 is still pending)
	gotProp, gotActions, err := operatorStore.GetAdviceProposal(ctx, proposalUUID)
	if err != nil {
		t.Fatalf("GetAdviceProposal failed: %v", err)
	}
	if gotProp.Status != "partially_applied" || gotProp.ReviewVersion != 2 {
		t.Errorf("expected proposal status partially_applied and version 2, got %+v", gotProp)
	}
	if gotActions[0].Status != "applied" || gotActions[1].Status != "pending" {
		t.Errorf("unexpected actions status: action0=%s, action1=%s", gotActions[0].Status, gotActions[1].Status)
	}

	// 5. Subsequent apply of remaining action 1 advances proposal to 'applied'
	applyReqUUID2 := uuid.New()
	receipt2 := &WorkAdviceApplicationReceipt{
		RequestUUID:  applyReqUUID2,
		ProposalUUID: proposalUUID,
	}
	appliedReceipt2, err := operatorStore.ApplyAdviceProposal(ctx, receipt2, 2, []uuid.UUID{action1})
	if err != nil {
		t.Fatalf("ApplyAdviceProposal for remaining action 1 failed: %v", err)
	}
	if appliedReceipt2 == nil {
		t.Fatal("expected non-nil receipt 2")
	}

	gotProp, gotActions, err = operatorStore.GetAdviceProposal(ctx, proposalUUID)
	if err != nil {
		t.Fatalf("GetAdviceProposal after full apply failed: %v", err)
	}
	if gotProp.Status != "applied" || gotProp.ReviewVersion != 3 {
		t.Errorf("expected proposal status applied and version 3, got %+v", gotProp)
	}
	if gotActions[1].Status != "applied" {
		t.Errorf("expected action 1 applied, got %s", gotActions[1].Status)
	}

	// 6. Undo receipt 2 (undoes action 1, proposal reverts to partially_applied because action 0 is still applied)
	if err := operatorStore.UndoAdviceApplication(ctx, applyReqUUID2); err != nil {
		t.Fatalf("UndoAdviceApplication for receipt 2 failed: %v", err)
	}
	gotProp, gotActions, err = operatorStore.GetAdviceProposal(ctx, proposalUUID)
	if err != nil {
		t.Fatalf("GetAdviceProposal after undo 2 failed: %v", err)
	}
	if gotProp.Status != "partially_applied" || gotProp.ReviewVersion != 4 {
		t.Errorf("expected proposal partially_applied and version 4 after undo 2, got %+v", gotProp)
	}
	if gotActions[1].Status != "undone" {
		t.Errorf("expected action 1 undone, got %s", gotActions[1].Status)
	}

	// 7. Undo receipt 1 (undoes action 0, proposal reverts to pending)
	if err := operatorStore.UndoAdviceApplication(ctx, applyReqUUID); err != nil {
		t.Fatalf("UndoAdviceApplication for receipt 1 failed: %v", err)
	}
	gotProp, gotActions, err = operatorStore.GetAdviceProposal(ctx, proposalUUID)
	if err != nil {
		t.Fatalf("GetAdviceProposal after undo 1 failed: %v", err)
	}
	if gotProp.Status != "pending" || gotProp.ReviewVersion != 5 {
		t.Errorf("expected proposal status pending and version 5 after undo 1, got %+v", gotProp)
	}
	if gotActions[0].Status != "undone" {
		t.Errorf("expected action 0 status undone, got %s", gotActions[0].Status)
	}

	// 8. Idempotent undo
	if err := operatorStore.UndoAdviceApplication(ctx, applyReqUUID); err != nil {
		t.Fatalf("idempotent UndoAdviceApplication failed: %v", err)
	}
}

type mockCleanupProvider struct {
	identity advice.ProcessIdentity
}

func (m *mockCleanupProvider) Name() string { return "mock-cleanup" }
func (m *mockCleanupProvider) VerifyCapabilities(ctx context.Context) (*advice.CodexCapabilities, error) {
	return &advice.CodexCapabilities{
		Schema:         advice.CodexAdviceCapabilitiesSchema,
		RequestSchema:  advice.CodexAdviceRequestSchema,
		ResultSchema:   advice.CodexAdviceResultSchema,
		Provider:       "openai",
		AuthMethod:     "chatgpt_subscription",
		OneRequest:     true,
		Tools:          false,
		Retries:        false,
		UpstreamCommit: advice.RequiredCodexUpstreamCommit,
		PatchSHA256:    "0000000000000000000000000000000000000000000000000000000000000000",
	}, nil
}
func (m *mockCleanupProvider) GenerateAdvice(ctx context.Context, snapshot *advice.Snapshot, deadline time.Duration, onLaunch func(advice.ProcessIdentity) error) (*advice.Proposal, *advice.ProcessResult, error) {
	if onLaunch != nil {
		_ = onLaunch(m.identity)
	}
	return nil, nil, &advice.CleanupUnconfirmedError{
		Identity: m.identity,
		Cause:    nil,
	}
}

func TestWorkAdviceGenerateLifecycle(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})

	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	jobUUID := uuid.New()
	reqUUID := uuid.New()

	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	validSpec := AdviceInputSpec{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
	}

	// 1. Candidate artifacts count > 20 -> INPUT_TOO_LARGE before launch row written
	largeArtifactsSpec := AdviceInputSpec{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
	}
	for i := 0; i < 25; i++ {
		largeArtifactsSpec.CandidateRevisionIDs = append(largeArtifactsSpec.CandidateRevisionIDs, uuid.New())
	}
	jobPreCheck := &WorkAdviceJob{
		JobUUID:       uuid.New(),
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		RequestUUID:   uuid.New(),
	}
	_, _, err := operatorStore.GenerateWorkAdvice(ctx, jobPreCheck, largeArtifactsSpec, &mockAdviceProvider{})
	requireCode(t, err, "INPUT_TOO_LARGE")

	// Ensure no launch row was written
	_, err = operatorStore.GetAdviceJob(ctx, jobPreCheck.JobUUID)
	requireCode(t, err, "NOT_FOUND")

	// 2. Ineligible source -> SOURCE_INELIGIBLE before launch row written
	privRec, err := operatorStore.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft: Draft{
			Kind:        "note",
			Body:        "Secret private note content",
			Sensitivity: "local",
			Scope:       Scope{Repo: "fixture:repo", TaskID: uuid.NewString(), RunID: uuid.NewString()},
			ClaimType:   "self",
		},
	})
	if err != nil {
		t.Fatalf("Create private note failed: %v", err)
	}
	privUUID, _ := uuid.Parse(privRec.RecordID)

	ineligibleSpec := AdviceInputSpec{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		SelectedSources: []SourceRefSpec{
			{RecordID: privUUID, Version: privRec.Version},
		},
	}
	jobIneligible := &WorkAdviceJob{
		JobUUID:       uuid.New(),
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		RequestUUID:   uuid.New(),
	}
	_, _, err = operatorStore.GenerateWorkAdvice(ctx, jobIneligible, ineligibleSpec, &mockAdviceProvider{})
	requireCode(t, err, "SOURCE_INELIGIBLE")
	_, err = operatorStore.GetAdviceJob(ctx, jobIneligible.JobUUID)
	requireCode(t, err, "NOT_FOUND")

	// 3. Provider returns CleanupUnconfirmedError -> job records cleanup_unconfirmed & ProcessIdentity
	cleanupJobUUID := uuid.New()
	jobCleanup := &WorkAdviceJob{
		JobUUID:       cleanupJobUUID,
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		RequestUUID:   uuid.New(),
	}
	cleanupProv := &mockCleanupProvider{
		identity: advice.ProcessIdentity{
			PID:        5555,
			PGID:       5555,
			StartTicks: 123456,
			BootID:     "boot-uuid-xyz",
		},
	}
	_, _, err = operatorStore.GenerateWorkAdvice(ctx, jobCleanup, validSpec, cleanupProv)
	if err == nil {
		t.Fatal("expected error from cleanup failure, got nil")
	}
	var clErr *advice.CleanupUnconfirmedError
	if !adviceIs(err, &clErr) {
		t.Errorf("expected CleanupUnconfirmedError, got %v", err)
	}

	retrievedCleanupJob, err := operatorStore.GetAdviceJob(ctx, cleanupJobUUID)
	if err != nil {
		t.Fatalf("GetAdviceJob failed: %v", err)
	}
	if retrievedCleanupJob.Status != "cleanup_unconfirmed" || retrievedCleanupJob.ErrorCode != "CLEANUP_UNCONFIRMED" {
		t.Errorf("expected status=cleanup_unconfirmed and error_code=CLEANUP_UNCONFIRMED, got %+v", retrievedCleanupJob)
	}
	if !strings.Contains(string(retrievedCleanupJob.ProcessIdentity), "boot-uuid-xyz") || !strings.Contains(string(retrievedCleanupJob.ProcessIdentity), "5555") {
		t.Errorf("expected ProcessIdentity saved, got: %s", string(retrievedCleanupJob.ProcessIdentity))
	}

	// 4. Successful execution with default field policy applied
	successPlaneItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, successPlaneItemUUID)
	validSpecSuccess := AdviceInputSpec{
		BindingID:     bindingID,
		PlaneItemUUID: successPlaneItemUUID,
	}
	jobSuccess := &WorkAdviceJob{
		JobUUID:       jobUUID,
		BindingID:     bindingID,
		PlaneItemUUID: successPlaneItemUUID,
		RequestUUID:   reqUUID,
		// Leaving ProfileName, ModelRoute, TemplateVersion, DeadlineSeconds, ExpiresAt empty
	}
	mockProv := &mockAdviceProvider{
		proposal: &advice.Proposal{
			Actions: []advice.ProposedAction{
				{
					ActionType: "annotate_progress",
					Progress:   &advice.ProgressPayload{Text: "Advice generated successfully."},
					Rationale:  "Good progress",
				},
			},
		},
	}
	prop, actions, err := operatorStore.GenerateWorkAdvice(ctx, jobSuccess, validSpecSuccess, mockProv)
	if err != nil {
		t.Fatalf("GenerateWorkAdvice failed: %v", err)
	}
	if prop == nil || len(actions) != 1 {
		t.Fatalf("unexpected result: %+v, %+v", prop, actions)
	}

	retrievedSuccess, err := operatorStore.GetAdviceJob(ctx, jobUUID)
	if err != nil {
		t.Fatalf("GetAdviceJob failed: %v", err)
	}
	if retrievedSuccess.ProfileName != "mock-provider" || retrievedSuccess.ModelRoute != advice.RouteCodexSubscription {
		t.Errorf("expected defaulted ProfileName and ModelRoute, got %+v", retrievedSuccess)
	}
	if retrievedSuccess.TemplateVersion != 1 || retrievedSuccess.DeadlineSeconds != 90 {
		t.Errorf("expected defaulted TemplateVersion and DeadlineSeconds, got %+v", retrievedSuccess)
	}
	if retrievedSuccess.Status != "completed" {
		t.Errorf("expected completed status, got %s", retrievedSuccess.Status)
	}
}

func adviceIs(err error, target **advice.CleanupUnconfirmedError) bool {
	var e *advice.CleanupUnconfirmedError
	if errors.As(err, &e) {
		*target = e
		return true
	}
	return false
}

func TestWorkAdviceGuardedUndoConflict(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})

	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	jobUUID := uuid.New()
	reqUUID := uuid.New()

	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	job := &WorkAdviceJob{
		JobUUID:       jobUUID,
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		RequestUUID:   reqUUID,
		Status:        "completed",
		ExpiresAt:     time.Now().Add(1 * time.Hour),
	}
	if err := operatorStore.CreateAdviceJob(ctx, job); err != nil {
		t.Fatal(err)
	}

	propUUID := uuid.New()
	prop := &WorkAdviceProposal{
		ProposalUUID:      propUUID,
		JobUUID:           jobUUID,
		BindingID:         bindingID,
		PlaneItemUUID:     planeItemUUID,
		ReviewVersion:     1,
		Status:            "pending",
		RawResult:         `{"actions":[]}`,
		NormalizedPayload: json.RawMessage(`{"actions":[]}`),
		ExpiresAt:         time.Now().Add(1 * time.Hour),
	}
	actUUID := uuid.New()
	act := &WorkAdviceAction{
		ActionUUID:    actUUID,
		ProposalUUID:  propUUID,
		ActionIndex:   0,
		ActionType:    "annotate_progress",
		TargetPayload: json.RawMessage(`{"text":"progress test"}`),
	}
	if err := operatorStore.RecordAdviceProposal(ctx, prop, []*WorkAdviceAction{act}); err != nil {
		t.Fatal(err)
	}

	applyReqUUID := uuid.New()
	receipt := &WorkAdviceApplicationReceipt{
		RequestUUID:  applyReqUUID,
		ProposalUUID: propUUID,
	}
	if _, err := operatorStore.ApplyAdviceProposal(ctx, receipt, 1, []uuid.UUID{actUUID}); err != nil {
		t.Fatalf("ApplyAdviceProposal failed: %v", err)
	}

	// 1. Target annotation revision moved externally -> Undo fails with UNDO_CONFLICT
	_, err := operatorStore.pool.Exec(ctx, `UPDATE cairn.work_annotation SET revision = revision + 1 WHERE body = 'progress test'`)
	if err != nil {
		t.Fatal(err)
	}
	err = operatorStore.UndoAdviceApplication(ctx, applyReqUUID)
	requireCode(t, err, "UNDO_CONFLICT")

	// Restore target annotation revision
	_, _ = operatorStore.pool.Exec(ctx, `UPDATE cairn.work_annotation SET revision = revision - 1 WHERE body = 'progress test'`)

	// 2. Action status changed to 'undone' already -> Undo fails with UNDO_CONFLICT
	_, err = operatorStore.pool.Exec(ctx, `UPDATE cairn.work_advice_action SET status = 'undone' WHERE action_uuid = $1`, actUUID)
	if err != nil {
		t.Fatal(err)
	}
	err = operatorStore.UndoAdviceApplication(ctx, applyReqUUID)
	requireCode(t, err, "UNDO_CONFLICT")
}

func TestWorkAdviceForgetPrivacyPurge(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})

	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	jobUUID := uuid.New()
	reqUUID := uuid.New()

	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	job := &WorkAdviceJob{
		JobUUID:       jobUUID,
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		RequestUUID:   reqUUID,
		Status:        "completed",
		ExpiresAt:     time.Now().Add(1 * time.Hour),
	}
	if err := operatorStore.CreateAdviceJob(ctx, job); err != nil {
		t.Fatal(err)
	}

	propUUID := uuid.New()
	prop := &WorkAdviceProposal{
		ProposalUUID:      propUUID,
		JobUUID:           jobUUID,
		BindingID:         bindingID,
		PlaneItemUUID:     planeItemUUID,
		ReviewVersion:     1,
		Status:            "pending",
		RawResult:         `{"actions":[]}`,
		NormalizedPayload: json.RawMessage(`{"actions":[]}`),
		ExpiresAt:         time.Now().Add(1 * time.Hour),
	}
	actUUID := uuid.New()
	act := &WorkAdviceAction{
		ActionUUID:    actUUID,
		ProposalUUID:  propUUID,
		ActionIndex:   0,
		ActionType:    "annotate_progress",
		TargetPayload: json.RawMessage(`{"text":"progress test"}`),
	}
	if err := operatorStore.RecordAdviceProposal(ctx, prop, []*WorkAdviceAction{act}); err != nil {
		t.Fatal(err)
	}

	applyReqUUID := uuid.New()
	receipt := &WorkAdviceApplicationReceipt{
		RequestUUID:  applyReqUUID,
		ProposalUUID: propUUID,
	}
	if _, err := operatorStore.ApplyAdviceProposal(ctx, receipt, 1, []uuid.UUID{actUUID}); err != nil {
		t.Fatalf("ApplyAdviceProposal failed: %v", err)
	}

	// Purge all advice data for this work item
	if err := operatorStore.PurgeAdviceForWorkItem(ctx, bindingID, planeItemUUID); err != nil {
		t.Fatalf("PurgeAdviceForWorkItem failed: %v", err)
	}

	// Verify job is gone
	_, err := operatorStore.GetAdviceJob(ctx, jobUUID)
	requireCode(t, err, "NOT_FOUND")

	// Verify proposal is gone
	_, _, err = operatorStore.GetAdviceProposal(ctx, propUUID)
	requireCode(t, err, "NOT_FOUND")

	// Verify receipts are gone
	var receiptCount int
	err = operatorStore.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.work_advice_application_receipt WHERE proposal_uuid = $1`, propUUID).Scan(&receiptCount)
	if err != nil {
		t.Fatal(err)
	}
	if receiptCount != 0 {
		t.Errorf("expected 0 receipts remaining after purge, got %d", receiptCount)
	}
}

type countedAdviceProvider struct {
	callCount atomic.Int32
	proposal  *advice.Proposal
	err       error
}

func (c *countedAdviceProvider) Name() string { return "counted-provider" }
func (c *countedAdviceProvider) VerifyCapabilities(ctx context.Context) (*advice.CodexCapabilities, error) {
	return &advice.CodexCapabilities{
		Schema:         advice.CodexAdviceCapabilitiesSchema,
		RequestSchema:  advice.CodexAdviceRequestSchema,
		ResultSchema:   advice.CodexAdviceResultSchema,
		Provider:       "openai",
		AuthMethod:     "chatgpt_subscription",
		OneRequest:     true,
		Tools:          false,
		Retries:        false,
		UpstreamCommit: advice.RequiredCodexUpstreamCommit,
		PatchSHA256:    "0000000000000000000000000000000000000000000000000000000000000000",
	}, nil
}
func (c *countedAdviceProvider) GenerateAdvice(ctx context.Context, snapshot *advice.Snapshot, deadline time.Duration, onLaunch func(advice.ProcessIdentity) error) (*advice.Proposal, *advice.ProcessResult, error) {
	c.callCount.Add(1)
	if onLaunch != nil {
		_ = onLaunch(advice.ProcessIdentity{PID: 9999, PGID: 9999, StartTicks: 50, BootID: "boot-counted"})
	}
	if c.err != nil {
		return nil, nil, c.err
	}
	return c.proposal, &advice.ProcessResult{Stdout: []byte(`{"actions":[]}`), ExitCode: 0, Identity: advice.ProcessIdentity{PID: 9999, PGID: 9999, StartTicks: 50, BootID: "boot-counted"}}, nil
}

func TestWorkAdvice_CountedProvider_StateRefusals(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	states := []struct {
		status    string
		errorCode string
		expectErr bool
	}{
		{status: "completed", errorCode: "", expectErr: false},
		{status: "failed", errorCode: "MODEL_EXECUTION_FAILED", expectErr: true},
		{status: "timed_out", errorCode: "MODEL_TIMEOUT", expectErr: true},
		{status: "uncertain", errorCode: "UNCERTAIN_STATE", expectErr: true},
		{status: "cleanup_unconfirmed", errorCode: "CLEANUP_UNCONFIRMED", expectErr: true},
		{status: "running", errorCode: "INFLIGHT_LIMIT", expectErr: true},
	}

	for _, sc := range states {
		t.Run("state_"+sc.status, func(t *testing.T) {
			bindingID := uuid.New()
			planeItemUUID := uuid.New()
			jobUUID := uuid.New()
			reqUUID := uuid.New()

			seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

			spec := AdviceInputSpec{
				BindingID:     bindingID,
				PlaneItemUUID: planeItemUUID,
			}
			countedProv := &countedAdviceProvider{}
			snap, manifest, err := operatorStore.BuildWorkAdviceSnapshot(ctx, spec)
			if err != nil {
				t.Fatal(err)
			}
			manifestBytes, _ := json.Marshal(manifest)
			manifestDigest := fmt.Sprintf("%x", sha256.Sum256(manifestBytes))
			payloadBytes, _ := json.Marshal(snap)
			payloadDigest := fmt.Sprintf("%x", sha256.Sum256(payloadBytes))

			defer func() {
				_, _ = operatorStore.pool.Exec(ctx, `DELETE FROM cairn.work_advice_job WHERE job_uuid = $1`, jobUUID)
			}()

			job := &WorkAdviceJob{
				JobUUID:             jobUUID,
				BindingID:           bindingID,
				PlaneItemUUID:       planeItemUUID,
				RequestUUID:         reqUUID,
				ProfileName:         countedProv.Name(),
				ModelRoute:          countedProv.Name(),
				TemplateVersion:     1,
				InputManifest:       manifestBytes,
				InputManifestDigest: manifestDigest,
				InputPayloadDigest:  payloadDigest,
				Status:              sc.status,
				ErrorCode:           sc.errorCode,
				ExpiresAt:           time.Now().Add(1 * time.Hour),
			}

			// Pre-insert job in this status directly
			_, err = operatorStore.pool.Exec(ctx, `
				INSERT INTO cairn.work_advice_job (
					job_uuid, binding_id, plane_item_uuid, caller, profile_name, model_route,
					template_version, request_uuid, input_manifest, input_manifest_digest, input_payload_digest,
					status, deadline_seconds, started_at, error_code, expires_at, created_at
				) VALUES (
					$1, $2, $3, 'operator:test', $4, $5,
					1, $6, $7, $8, $9,
					$10, 90, clock_timestamp(), $11, clock_timestamp() + interval '1 hour', clock_timestamp()
				)
			`, jobUUID, bindingID, planeItemUUID, countedProv.Name(), countedProv.Name(), reqUUID, manifestBytes, manifestDigest, payloadDigest, sc.status, sc.errorCode)
			if err != nil {
				t.Fatalf("failed to insert job in state %s: %v", sc.status, err)
			}

			if sc.status == "completed" {
				// Record a proposal so completed lookup finds it
				propUUID := uuid.New()
				_ = operatorStore.RecordAdviceProposal(ctx, &WorkAdviceProposal{
					ProposalUUID:      propUUID,
					JobUUID:           jobUUID,
					BindingID:         bindingID,
					PlaneItemUUID:     planeItemUUID,
					ReviewVersion:     1,
					Status:            "pending",
					RawResult:         `{"actions":[]}`,
					NormalizedPayload: json.RawMessage(`{"actions":[]}`),
					ExpiresAt:         time.Now().Add(1 * time.Hour),
				}, nil)
			}

			_, _, err = operatorStore.ExecuteAdviceJob(ctx, job, spec, countedProv)
			if sc.expectErr && err == nil {
				t.Fatalf("expected refusal error for state %s, got nil", sc.status)
			}
			if !sc.expectErr && err != nil {
				t.Fatalf("unexpected error for state %s: %v", sc.status, err)
			}
			if countedProv.callCount.Load() != 0 {
				t.Fatalf("expected 0 provider calls for state %s, got %d", sc.status, countedProv.callCount.Load())
			}
		})
	}
}

func TestWorkAdvice_ConcurrentAndIdempotentLaunch(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	reqUUID := uuid.New()
	job1 := &WorkAdviceJob{
		JobUUID:            uuid.New(),
		BindingID:          bindingID,
		PlaneItemUUID:      planeItemUUID,
		RequestUUID:        reqUUID,
		ProfileName:        advice.RouteCodexSubscription,
		ModelRoute:         "gpt-4o",
		TemplateVersion:    1,
		InputPayloadDigest: "payload-digest-1",
		Status:             "pending",
	}
	defer func() {
		_, _ = operatorStore.pool.Exec(ctx, `DELETE FROM cairn.work_advice_job WHERE job_uuid = $1`, job1.JobUUID)
	}()

	// 1. Initial launch succeeds
	if err := operatorStore.CreateAdviceJob(ctx, job1); err != nil {
		t.Fatalf("CreateAdviceJob failed: %v", err)
	}

	// 2. Replay with identical params returns the existing job without inserting
	jobReplay := &WorkAdviceJob{
		BindingID:          bindingID,
		PlaneItemUUID:      planeItemUUID,
		RequestUUID:        reqUUID,
		ProfileName:        advice.RouteCodexSubscription,
		ModelRoute:         "gpt-4o",
		TemplateVersion:    1,
		InputPayloadDigest: "payload-digest-1",
	}
	if err := operatorStore.CreateAdviceJob(ctx, jobReplay); err != nil {
		t.Fatalf("Replay CreateAdviceJob failed: %v", err)
	}
	if jobReplay.JobUUID != job1.JobUUID {
		t.Errorf("expected replayed job to have same JobUUID %s, got %s", job1.JobUUID, jobReplay.JobUUID)
	}

	// 3. Replay with different params returns IDEMPOTENCY_CONFLICT
	jobConflict := &WorkAdviceJob{
		BindingID:          bindingID,
		PlaneItemUUID:      planeItemUUID,
		RequestUUID:        reqUUID,
		ProfileName:        advice.RouteCodexSubscription,
		ModelRoute:         "gpt-4o",
		TemplateVersion:    1,
		InputPayloadDigest: "different-payload-digest",
	}
	err := operatorStore.CreateAdviceJob(ctx, jobConflict)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")

	// 4. Concurrent launch with distinct request_uuid on same item fails with INFLIGHT_LIMIT
	distinctReqUUID := uuid.New()
	jobConcurrent := &WorkAdviceJob{
		JobUUID:            uuid.New(),
		BindingID:          bindingID,
		PlaneItemUUID:      planeItemUUID,
		RequestUUID:        distinctReqUUID,
		ProfileName:        advice.RouteCodexSubscription,
		ModelRoute:         "gpt-4o",
		TemplateVersion:    1,
		InputPayloadDigest: "payload-digest-2",
	}
	err = operatorStore.CreateAdviceJob(ctx, jobConcurrent)
	requireCode(t, err, "INFLIGHT_LIMIT")
}

func TestWorkAdvice_PolicyRefusal(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	agentStore := testStore(t, Channel{Principal: "agent:test", Operator: false})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	// 1. Non-operator calling SetWorkAdvicePolicy is denied
	err := agentStore.SetWorkAdvicePolicy(ctx, bindingID, []string{"title"})
	requireCode(t, err, "AUTHORITY_DENIED")

	// 2. Project with no policy row returns POLICY_UNENFORCEABLE
	_, err = operatorStore.pool.Exec(ctx, "DELETE FROM cairn.work_advice_policy WHERE binding_id = $1", bindingID)
	if err != nil {
		t.Fatal(err)
	}
	spec := AdviceInputSpec{BindingID: bindingID, PlaneItemUUID: planeItemUUID}
	_, _, err = operatorStore.BuildWorkAdviceSnapshot(ctx, spec)
	requireCode(t, err, "POLICY_UNENFORCEABLE")

	// 3. Project with empty allowed_fields returns POLICY_UNENFORCEABLE
	if err := operatorStore.SetWorkAdvicePolicy(ctx, bindingID, []string{}); err != nil {
		t.Fatal(err)
	}
	_, _, err = operatorStore.BuildWorkAdviceSnapshot(ctx, spec)
	requireCode(t, err, "POLICY_UNENFORCEABLE")

	// 4. Allowed fields filter: only permitted fields are placed in snapshot
	if err := operatorStore.SetWorkAdvicePolicy(ctx, bindingID, []string{"identifier", "title"}); err != nil {
		t.Fatal(err)
	}
	snap, manifest, err := operatorStore.BuildWorkAdviceSnapshot(ctx, spec)
	if err != nil {
		t.Fatalf("BuildWorkAdviceSnapshot failed: %v", err)
	}
	if snap.Identifier != "TEST-1" || snap.Title != "Test Item" {
		t.Errorf("expected Identifier and Title populated, got %+v", snap)
	}
	if snap.CurrentState != "" {
		t.Errorf("expected CurrentState to be filtered out (empty), got %q", snap.CurrentState)
	}
	if len(manifest.AllowedFields) != 2 {
		t.Errorf("expected 2 allowed fields in manifest, got %v", manifest.AllowedFields)
	}
}

func TestWorkAdvice_StalePlaneAndAbsentRefusal(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)
	spec := AdviceInputSpec{BindingID: bindingID, PlaneItemUUID: planeItemUUID}

	// 1. Stale Plane refresh (> 2 min ago) -> SOURCE_INELIGIBLE
	_, err := operatorStore.pool.Exec(ctx, `
		UPDATE cairn.work_item
		SET last_refreshed_at = clock_timestamp() - interval '3 minutes'
		WHERE binding_id = $1 AND plane_item_uuid = $2
	`, bindingID, planeItemUUID)
	if err != nil {
		t.Fatal(err)
	}
	_, _, err = operatorStore.BuildWorkAdviceSnapshot(ctx, spec)
	requireCode(t, err, "SOURCE_INELIGIBLE")

	// Restore freshness
	_, err = operatorStore.pool.Exec(ctx, `
		UPDATE cairn.work_item
		SET last_refreshed_at = clock_timestamp()
		WHERE binding_id = $1 AND plane_item_uuid = $2
	`, bindingID, planeItemUUID)
	if err != nil {
		t.Fatal(err)
	}

	// 2. is_absent = true -> ITEM_ABSENT
	_, err = operatorStore.pool.Exec(ctx, `
		UPDATE cairn.work_item
		SET is_absent = true
		WHERE binding_id = $1 AND plane_item_uuid = $2
	`, bindingID, planeItemUUID)
	if err != nil {
		t.Fatal(err)
	}
	_, _, err = operatorStore.BuildWorkAdviceSnapshot(ctx, spec)
	requireCode(t, err, "ITEM_ABSENT")

	// Restore present
	_, err = operatorStore.pool.Exec(ctx, `
		UPDATE cairn.work_item
		SET is_absent = false
		WHERE binding_id = $1 AND plane_item_uuid = $2
	`, bindingID, planeItemUUID)
	if err != nil {
		t.Fatal(err)
	}

	// 3. auth_rejected = true on work_project -> SOURCE_INELIGIBLE
	_, err = operatorStore.pool.Exec(ctx, `
		UPDATE cairn.work_project
		SET auth_rejected = true
		WHERE binding_id = $1
	`, bindingID)
	if err != nil {
		t.Fatal(err)
	}
	_, _, err = operatorStore.BuildWorkAdviceSnapshot(ctx, spec)
	requireCode(t, err, "SOURCE_INELIGIBLE")
}

func TestWorkAdvice_MetadataExclusionRefusal(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	// Add exclusion pattern "confidential"
	_, err := operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_metadata_exclusion (exclusion_id, binding_id, field_name, pattern)
		VALUES (gen_random_uuid(), $1, 'annotation', 'confidential')
	`, bindingID)
	if err != nil {
		t.Fatal(err)
	}

	jobUUID := uuid.New()
	propUUID := uuid.New()
	actUUID := uuid.New()

	job := &WorkAdviceJob{
		JobUUID:       jobUUID,
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		RequestUUID:   uuid.New(),
		Status:        "completed",
		ExpiresAt:     time.Now().Add(1 * time.Hour),
	}
	if err := operatorStore.CreateAdviceJob(ctx, job); err != nil {
		t.Fatal(err)
	}

	prop := &WorkAdviceProposal{
		ProposalUUID:      propUUID,
		JobUUID:           jobUUID,
		BindingID:         bindingID,
		PlaneItemUUID:     planeItemUUID,
		ReviewVersion:     1,
		Status:            "pending",
		RawResult:         `{"actions":[]}`,
		NormalizedPayload: json.RawMessage(`{"actions":[]}`),
		ExpiresAt:         time.Now().Add(1 * time.Hour),
	}
	act := &WorkAdviceAction{
		ActionUUID:    actUUID,
		ProposalUUID:  propUUID,
		ActionIndex:   0,
		ActionType:    "annotate_progress",
		TargetPayload: json.RawMessage(`{"text":"This contains confidential information"}`),
	}
	if err := operatorStore.RecordAdviceProposal(ctx, prop, []*WorkAdviceAction{act}); err != nil {
		t.Fatal(err)
	}

	receipt := &WorkAdviceApplicationReceipt{
		RequestUUID:  uuid.New(),
		ProposalUUID: propUUID,
	}
	_, err = operatorStore.ApplyAdviceProposal(ctx, receipt, 1, []uuid.UUID{actUUID})
	requireCode(t, err, "REFUSAL_EXCLUDED")
}

func TestWorkAdvice_RestoreFenceAndStaleManifestRevalidation(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	jobUUID := uuid.New()
	propUUID := uuid.New()
	actUUID := uuid.New()
	reqUUID := uuid.New()

	spec := AdviceInputSpec{BindingID: bindingID, PlaneItemUUID: planeItemUUID}
	_, manifest, err := operatorStore.BuildWorkAdviceSnapshot(ctx, spec)
	if err != nil {
		t.Fatal(err)
	}
	manifestBytes, _ := json.Marshal(manifest)

	job := &WorkAdviceJob{
		JobUUID:             jobUUID,
		BindingID:           bindingID,
		PlaneItemUUID:       planeItemUUID,
		RequestUUID:         reqUUID,
		InputManifest:       manifestBytes,
		InputManifestDigest: "manifest-digest-test",
		InputPayloadDigest:  "payload-digest-test",
		Status:              "completed",
		ExpiresAt:           time.Now().Add(1 * time.Hour),
	}
	if err := operatorStore.CreateAdviceJob(ctx, job); err != nil {
		t.Fatal(err)
	}

	prop := &WorkAdviceProposal{
		ProposalUUID:      propUUID,
		JobUUID:           jobUUID,
		BindingID:         bindingID,
		PlaneItemUUID:     planeItemUUID,
		ReviewVersion:     1,
		Status:            "pending",
		RawResult:         `{"actions":[]}`,
		NormalizedPayload: json.RawMessage(`{"actions":[]}`),
		ExpiresAt:         time.Now().Add(1 * time.Hour),
	}
	act := &WorkAdviceAction{
		ActionUUID:    actUUID,
		ProposalUUID:  propUUID,
		ActionIndex:   0,
		ActionType:    "annotate_progress",
		TargetPayload: json.RawMessage(`{"text":"Regular progress"}`),
	}
	if err := operatorStore.RecordAdviceProposal(ctx, prop, []*WorkAdviceAction{act}); err != nil {
		t.Fatal(err)
	}

	// 1. Advance retrieval generation (restore fence trigger)
	_, err = operatorStore.pool.Exec(ctx, "UPDATE cairn.retrieval_generation SET generation = generation + 1 WHERE singleton = true")
	if err != nil {
		t.Fatal(err)
	}

	// Application must fail with RESTORE_FENCE_TRIGGERED
	receipt := &WorkAdviceApplicationReceipt{
		RequestUUID:  uuid.New(),
		ProposalUUID: propUUID,
	}
	_, err = operatorStore.ApplyAdviceProposal(ctx, receipt, 1, []uuid.UUID{actUUID})
	requireCode(t, err, "RESTORE_FENCE_TRIGGERED")
}

func TestWorkAdvice_ArtifactLinkAndUndoRestore(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	// Seed artifact feature, artifact, and revision
	_, _ = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_feature (feature_name, status, updated_at)
		VALUES ('work-artifacts/1', 'active', clock_timestamp())
		ON CONFLICT (feature_name) DO UPDATE SET status = 'active'
	`)

	artID := uuid.New()
	revID := uuid.New()
	_, err := operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_artifact (artifact_id, collection, kind, canonical_key, display_name)
		VALUES ($1, 'fixture:repo', 'report', 'test-key', 'test-artifact')
	`, artID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_artifact_revision (
			revision_id, artifact_id, collection, revision_label, created_by
		) VALUES (
			$1, $2, 'fixture:repo', 'v1', 'operator:test'
		)
	`, revID, artID)
	if err != nil {
		t.Fatal(err)
	}

	jobUUID := uuid.New()
	propUUID := uuid.New()
	actUUID := uuid.New()
	reqUUID := uuid.New()

	job := &WorkAdviceJob{
		JobUUID:       jobUUID,
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		RequestUUID:   reqUUID,
		Status:        "completed",
		ExpiresAt:     time.Now().Add(1 * time.Hour),
	}
	if err := operatorStore.CreateAdviceJob(ctx, job); err != nil {
		t.Fatal(err)
	}

	prop := &WorkAdviceProposal{
		ProposalUUID:      propUUID,
		JobUUID:           jobUUID,
		BindingID:         bindingID,
		PlaneItemUUID:     planeItemUUID,
		ReviewVersion:     1,
		Status:            "pending",
		RawResult:         `{"actions":[]}`,
		NormalizedPayload: json.RawMessage(`{"actions":[]}`),
		ExpiresAt:         time.Now().Add(1 * time.Hour),
	}
	act := &WorkAdviceAction{
		ActionUUID:    actUUID,
		ProposalUUID:  propUUID,
		ActionIndex:   0,
		ActionType:    "link_artifact",
		TargetPayload: json.RawMessage(fmt.Sprintf(`{"artifact_revision_id": "%s", "role": "input", "link_rationale": "Relevant test artifact"}`, revID)),
		Rationale:     "Relevant test artifact",
	}
	if err := operatorStore.RecordAdviceProposal(ctx, prop, []*WorkAdviceAction{act}); err != nil {
		t.Fatal(err)
	}

	applyReqUUID := uuid.New()
	receipt := &WorkAdviceApplicationReceipt{
		RequestUUID:  applyReqUUID,
		ProposalUUID: propUUID,
	}
	applied, err := operatorStore.ApplyAdviceProposal(ctx, receipt, 1, []uuid.UUID{actUUID})
	if err != nil {
		t.Fatalf("ApplyAdviceProposal failed: %v", err)
	}
	if applied.Undone {
		t.Fatalf("expected receipt not undone")
	}

	// Verify link row created in active state
	var linkCount int
	err = operatorStore.pool.QueryRow(ctx, `
		SELECT count(*) FROM cairn.work_artifact_link
		WHERE binding_id = $1 AND plane_item_uuid = $2 AND artifact_id = $3 AND state = 'active'
	`, bindingID, planeItemUUID, artID).Scan(&linkCount)
	if err != nil {
		t.Fatal(err)
	}
	if linkCount != 1 {
		t.Errorf("expected 1 active artifact link, got %d", linkCount)
	}

	// Undo application
	if err := operatorStore.UndoAdviceApplication(ctx, applyReqUUID); err != nil {
		t.Fatalf("UndoAdviceApplication failed: %v", err)
	}

	// Verify link is moved to removed state
	err = operatorStore.pool.QueryRow(ctx, `
		SELECT count(*) FROM cairn.work_artifact_link
		WHERE binding_id = $1 AND plane_item_uuid = $2 AND artifact_id = $3 AND state = 'removed'
	`, bindingID, planeItemUUID, artID).Scan(&linkCount)
	if err != nil {
		t.Fatal(err)
	}
	if linkCount != 1 {
		t.Errorf("expected link to be in removed state after undo, got count %d", linkCount)
	}
}

func TestWorkAdvice_OpenStoreAfterFeatureActive(t *testing.T) {
	s := restoreTestStore(t)
	ctx := context.Background()
	if err := s.SetAdviceFeature(ctx, "active"); err != nil {
		t.Fatal(err)
	}

	var currentDB string
	if err := s.pool.QueryRow(ctx, "SELECT current_database()").Scan(&currentDB); err != nil {
		t.Fatal(err)
	}

	baseDSN := os.Getenv("CAIRN_TEST_DATABASE_URL")
	cfg, err := pgxpool.ParseConfig(baseDSN)
	if err != nil {
		t.Fatal(err)
	}
	dsn := fmt.Sprintf("host=%s port=%d dbname=%s sslmode=disable", cfg.ConnConfig.Host, cfg.ConnConfig.Port, currentDB)

	freshStore, err := Open(ctx, dsn, Channel{Principal: "test-fresh-operator", Operator: true})
	if err != nil {
		t.Fatalf("Open failed after work-advice/1 activated: %v", err)
	}
	defer freshStore.Close()

	active, err := freshStore.CheckAdviceFeature(ctx)
	if err != nil {
		t.Fatalf("CheckAdviceFeature failed on fresh store: %v", err)
	}
	if !active {
		t.Error("expected work-advice/1 to be active on fresh store")
	}
}

func TestWorkAdviceProfileRegistration(t *testing.T) {
	s := restoreTestStore(t)
	ctx := context.Background()

	prof := &advice.Profile{
		Name:                   "test-profile",
		Route:                  advice.RouteCodexSubscription,
		ExecutablePath:         "/usr/bin/codex",
		BinarySHA256:           "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
		ExpectedUpstreamCommit: advice.RequiredCodexUpstreamCommit,
		ExpectedPatchSHA256:    "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
		CodexHome:              "/tmp/codex-home",
		Model:                  "o3-mini",
		TimeoutMs:              120000,
		MaxOutputBytes:         65536,
	}

	// 1. Register new profile
	if err := s.RegisterAdviceProfile(ctx, prof); err != nil {
		t.Fatalf("RegisterAdviceProfile failed: %v", err)
	}

	// 2. Idempotent re-registration with identical fields succeeds
	if err := s.RegisterAdviceProfile(ctx, prof); err != nil {
		t.Fatalf("idempotent RegisterAdviceProfile failed: %v", err)
	}

	// 3. Differing fields return CONFLICT
	conflicting := *prof
	conflicting.Model = "gpt-4o"
	err := s.RegisterAdviceProfile(ctx, &conflicting)
	requireCode(t, err, "CONFLICT")

	// 4. Get profile
	got, err := s.GetAdviceProfile(ctx, "test-profile")
	if err != nil {
		t.Fatalf("GetAdviceProfile failed: %v", err)
	}
	if got.Name != prof.Name || got.Model != prof.Model || got.ExecutablePath != prof.ExecutablePath {
		t.Errorf("GetAdviceProfile mismatch: %+v", got)
	}

	// 5. List profiles
	profiles, err := s.ListAdviceProfiles(ctx)
	if err != nil {
		t.Fatalf("ListAdviceProfiles failed: %v", err)
	}
	if len(profiles) != 1 || profiles[0].Name != "test-profile" {
		t.Errorf("ListAdviceProfiles unexpected: %+v", profiles)
	}
}

type slowAdviceProvider struct {
	delay     time.Duration
	invokedAt time.Time
}

func (s *slowAdviceProvider) Name() string { return "slow-provider" }
func (s *slowAdviceProvider) VerifyCapabilities(ctx context.Context) (*advice.CodexCapabilities, error) {
	return &advice.CodexCapabilities{
		Schema:         advice.CodexAdviceCapabilitiesSchema,
		RequestSchema:  advice.CodexAdviceRequestSchema,
		ResultSchema:   advice.CodexAdviceResultSchema,
		Provider:       "openai",
		AuthMethod:     "chatgpt_subscription",
		OneRequest:     true,
		UpstreamCommit: advice.RequiredCodexUpstreamCommit,
		PatchSHA256:    "0000000000000000000000000000000000000000000000000000000000000000",
	}, nil
}
func (s *slowAdviceProvider) GenerateAdvice(ctx context.Context, snapshot *advice.Snapshot, deadline time.Duration, onLaunch func(advice.ProcessIdentity) error) (*advice.Proposal, *advice.ProcessResult, error) {
	s.invokedAt = time.Now().UTC()
	if onLaunch != nil {
		_ = onLaunch(advice.ProcessIdentity{PID: 1000, PGID: 1000, StartTicks: 1000, BootID: "test-boot"})
	}
	time.Sleep(s.delay)
	return &advice.Proposal{
		Summary: "slow advice",
		Actions: []advice.ProposedAction{
			{ActionType: advice.ActionAnnotateProgress, Progress: &advice.ProgressPayload{Text: "slow progress"}},
		},
	}, &advice.ProcessResult{ExitCode: 0}, nil
}

func TestWorkAdvice_ProposalCaptureTimeExpiry(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	spec := AdviceInputSpec{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
	}

	slowProv := &slowAdviceProvider{delay: 150 * time.Millisecond}
	job := &WorkAdviceJob{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		RequestUUID:   uuid.New(),
	}

	start := time.Now().UTC()
	prop, _, err := operatorStore.ExecuteAdviceJob(ctx, job, spec, slowProv)
	if err != nil {
		t.Fatalf("ExecuteAdviceJob failed: %v", err)
	}
	elapsed := time.Since(start)
	if elapsed < 150*time.Millisecond {
		t.Fatalf("expected execution to take at least 150ms, took %v", elapsed)
	}

	// Proposal expiry must be anchored to snapshot capture time (+1h), NOT completion (+1h)
	if prop.ExpiresAt.After(slowProv.invokedAt.Add(1 * time.Hour)) {
		t.Fatalf("proposal.ExpiresAt (%v) exceeds provider invocation time + 1h (%v)", prop.ExpiresAt, slowProv.invokedAt.Add(1*time.Hour))
	}
	if !prop.ExpiresAt.After(start.Add(59 * time.Minute)) {
		t.Fatalf("proposal.ExpiresAt (%v) is unexpectedly before start + 59m", prop.ExpiresAt)
	}
}

func TestWorkAdvice_30DayRetentionAndTombstoneDedupe(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	spec := AdviceInputSpec{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
	}

	countedProv := &countedAdviceProvider{
		proposal: &advice.Proposal{
			Summary: "test advice",
			Actions: []advice.ProposedAction{
				{
					ActionType: advice.ActionAnnotateProgress,
					Progress:   &advice.ProgressPayload{Text: "test progress"},
				},
			},
		},
	}
	reqUUID1 := uuid.New()
	job1 := &WorkAdviceJob{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		RequestUUID:   reqUUID1,
	}
	prop1, actions1, err := operatorStore.ExecuteAdviceJob(ctx, job1, spec, countedProv)
	if err != nil {
		t.Fatalf("ExecuteAdviceJob 1 failed: %v", err)
	}
	if countedProv.callCount.Load() != 1 {
		t.Fatalf("expected 1 call, got %d", countedProv.callCount.Load())
	}

	applyReqUUID := uuid.New()
	receipt := &WorkAdviceApplicationReceipt{
		RequestUUID:  applyReqUUID,
		ProposalUUID: prop1.ProposalUUID,
	}
	appliedReceipt, err := operatorStore.ApplyAdviceProposal(ctx, receipt, prop1.ReviewVersion, []uuid.UUID{actions1[0].ActionUUID})
	if err != nil {
		t.Fatalf("ApplyAdviceProposal failed: %v", err)
	}

	reqUUID2 := uuid.New()
	job2 := &WorkAdviceJob{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		RequestUUID:   reqUUID2,
	}
	_, _, err = operatorStore.ExecuteAdviceJob(ctx, job2, spec, countedProv)
	if err != nil {
		t.Fatalf("ExecuteAdviceJob 2 failed: %v", err)
	}
	if countedProv.callCount.Load() != 2 {
		t.Fatalf("expected 2 calls, got %d", countedProv.callCount.Load())
	}

	_, err = operatorStore.pool.Exec(ctx, `
		UPDATE cairn.work_advice_job SET created_at = clock_timestamp() - interval '31 days';
		UPDATE cairn.work_advice_proposal SET created_at = clock_timestamp() - interval '31 days';
		UPDATE cairn.work_advice_action SET created_at = clock_timestamp() - interval '31 days';
	`)
	if err != nil {
		t.Fatalf("failed to age rows: %v", err)
	}

	swept, err := operatorStore.SweepExpiredAdvice(ctx)
	if err != nil {
		t.Fatalf("SweepExpiredAdvice failed: %v", err)
	}
	if swept == 0 {
		t.Fatalf("expected non-zero swept count, got 0")
	}

	var j2Exists bool
	_ = operatorStore.pool.QueryRow(ctx, `SELECT true FROM cairn.work_advice_job WHERE job_uuid = $1`, job2.JobUUID).Scan(&j2Exists)
	if j2Exists {
		t.Fatalf("expected unapplied job %s to be deleted after 30d sweep", job2.JobUUID)
	}

	var j1Snapshot []byte
	err = operatorStore.pool.QueryRow(ctx, `SELECT input_snapshot FROM cairn.work_advice_job WHERE job_uuid = $1`, job1.JobUUID).Scan(&j1Snapshot)
	if err != nil {
		t.Fatalf("expected applied job 1 to survive: %v", err)
	}
	if len(j1Snapshot) > 0 {
		t.Fatalf("expected applied job 1 input_snapshot to be purged after 30d sweep")
	}

	retryJob2 := &WorkAdviceJob{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		RequestUUID:   reqUUID2,
	}
	_, _, err = operatorStore.ExecuteAdviceJob(ctx, retryJob2, spec, countedProv)
	if err == nil {
		t.Fatalf("expected refusal when retrying 30d swept job, got nil")
	}
	if countedProv.callCount.Load() != 2 {
		t.Fatalf("provider was re-invoked on 30d swept request! calls = %d, expected 2", countedProv.callCount.Load())
	}

	changedSpec := spec
	changedSpec.CandidateRevisionIDs = []uuid.UUID{uuid.New()}
	_, _, err = operatorStore.ExecuteAdviceJob(ctx, retryJob2, changedSpec, countedProv)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	if countedProv.callCount.Load() != 2 {
		t.Fatalf("provider was invoked on changed selection! calls = %d, expected 2", countedProv.callCount.Load())
	}

	if err := operatorStore.UndoAdviceApplication(ctx, appliedReceipt.RequestUUID); err != nil {
		t.Fatalf("UndoAdviceApplication on post-30d applied receipt failed: %v", err)
	}
}

func TestWorkAdvice_ArtifactLinkRationalePersistenceAndScrubbing(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})

	_ = operatorStore.SetAdviceFeature(ctx, "active")
	_, _ = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_feature (feature_name, status, updated_at)
		VALUES ('work-artifacts/1', 'active', clock_timestamp())
		ON CONFLICT (feature_name) DO UPDATE SET status = 'active'
	`)

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	// Create source record
	source, err := operatorStore.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft: Draft{
			Kind:        "note",
			Body:        "source text for link rationale test",
			ClaimType:   "self",
			Sensitivity: "shareable",
			Scope:       Scope{Repo: "fixture:repo", TaskID: "*", RunID: "*"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	// Create artifact & revision in fixture:repo
	artID := uuid.New()
	revID := uuid.New()
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_artifact (artifact_id, collection, kind, canonical_key, display_name)
		VALUES ($1, 'fixture:repo', 'report', 'link-test-key', 'link-test-artifact')
	`, artID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_artifact_revision (
			revision_id, artifact_id, collection, revision_label, created_by
		) VALUES (
			$1, $2, 'fixture:repo', 'v1', 'operator:test'
		)
	`, revID, artID)
	if err != nil {
		t.Fatal(err)
	}

	spec := AdviceInputSpec{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		SelectedSources: []SourceRefSpec{{
			RecordID: uuid.MustParse(source.RecordID),
			Version:  source.Version,
		}},
	}
	_, manifest, err := operatorStore.BuildWorkAdviceSnapshot(ctx, spec)
	if err != nil {
		t.Fatal(err)
	}
	manifestJSON, _ := json.Marshal(manifest)

	jobUUID := uuid.New()
	reqUUID := uuid.New()
	job := &WorkAdviceJob{
		JobUUID:             jobUUID,
		BindingID:           bindingID,
		PlaneItemUUID:       planeItemUUID,
		RequestUUID:         reqUUID,
		Status:              "completed",
		InputManifest:       manifestJSON,
		InputManifestDigest: "test-manifest-digest",
		InputPayloadDigest:  "test-payload-digest",
		ExpiresAt:           time.Now().Add(1 * time.Hour),
	}
	if err := operatorStore.CreateAdviceJob(ctx, job); err != nil {
		t.Fatal(err)
	}

	propUUID := uuid.New()
	prop := &WorkAdviceProposal{
		ProposalUUID:      propUUID,
		JobUUID:           jobUUID,
		BindingID:         bindingID,
		PlaneItemUUID:     planeItemUUID,
		ReviewVersion:     1,
		Status:            "pending",
		RawResult:         `{"actions":[]}`,
		NormalizedPayload: json.RawMessage(`{"actions":[]}`),
		ExpiresAt:         time.Now().Add(1 * time.Hour),
	}
	actUUID := uuid.New()
	actPayload, _ := json.Marshal(advice.ArtifactPayload{
		ArtifactRevisionID: revID.String(),
		Role:               "evidence",
	})
	testRationale := "verified evidence rationale from model"
	act := &WorkAdviceAction{
		ActionUUID:    actUUID,
		ProposalUUID:  propUUID,
		ActionIndex:   0,
		ActionType:    "link_artifact",
		TargetPayload: actPayload,
		Rationale:     testRationale,
	}
	if err := operatorStore.RecordAdviceProposal(ctx, prop, []*WorkAdviceAction{act}); err != nil {
		t.Fatal(err)
	}

	// Apply advice proposal
	applyReqUUID := uuid.New()
	receipt := &WorkAdviceApplicationReceipt{
		RequestUUID:  applyReqUUID,
		ProposalUUID: propUUID,
	}
	_, err = operatorStore.ApplyAdviceProposal(ctx, receipt, 1, []uuid.UUID{actUUID})
	if err != nil {
		t.Fatalf("ApplyAdviceProposal failed: %v", err)
	}

	// Verify rationale was persisted on work_artifact_link and work_artifact_link_history
	var linkRationale, histRationale string
	err = operatorStore.pool.QueryRow(ctx, `
		SELECT rationale FROM cairn.work_artifact_link
		WHERE binding_id = $1 AND plane_item_uuid = $2 AND revision_id = $3
	`, bindingID, planeItemUUID, revID).Scan(&linkRationale)
	if err != nil {
		t.Fatalf("failed to query work_artifact_link rationale: %v", err)
	}
	if linkRationale != testRationale {
		t.Fatalf("expected link rationale %q, got %q", testRationale, linkRationale)
	}

	err = operatorStore.pool.QueryRow(ctx, `
		SELECT rationale FROM cairn.work_artifact_link_history
		WHERE link_id = (SELECT link_id FROM cairn.work_artifact_link WHERE binding_id = $1 AND plane_item_uuid = $2 AND revision_id = $3)
		  AND revision = 1
	`, bindingID, planeItemUUID, revID).Scan(&histRationale)
	if err != nil {
		t.Fatalf("failed to query work_artifact_link_history rationale: %v", err)
	}
	if histRationale != testRationale {
		t.Fatalf("expected history rationale %q, got %q", testRationale, histRationale)
	}

	// Now call Store.Forget on the source record
	grant, err := operatorStore.Bootstrap(ctx, BootstrapRequest{
		RequestID: uuid.NewString(),
		Reason:    "test artifact rationale scrub on Forget",
	})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := operatorStore.PreviewDeletion(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := operatorStore.Forget(ctx, ForgetRequest{
		RequestID:       uuid.NewString(),
		RecordID:        source.RecordID,
		ExpectedVersion: source.Version,
		GrantID:         grant.ID,
		PreviewID:       preview.PreviewID,
	}); err != nil {
		t.Fatalf("Forget failed: %v", err)
	}

	// Verify Store.Forget scrubbed rationale on both work_artifact_link and work_artifact_link_history
	var scrubbedLinkRat, scrubbedHistRat string
	err = operatorStore.pool.QueryRow(ctx, `
		SELECT rationale FROM cairn.work_artifact_link
		WHERE binding_id = $1 AND plane_item_uuid = $2 AND revision_id = $3
	`, bindingID, planeItemUUID, revID).Scan(&scrubbedLinkRat)
	if err != nil {
		t.Fatalf("failed to query scrubbed work_artifact_link rationale: %v", err)
	}
	if scrubbedLinkRat != "[purged due to source deletion]" {
		t.Fatalf("expected scrubbed link rationale %q, got %q", "[purged due to source deletion]", scrubbedLinkRat)
	}

	err = operatorStore.pool.QueryRow(ctx, `
		SELECT rationale FROM cairn.work_artifact_link_history
		WHERE link_id = (SELECT link_id FROM cairn.work_artifact_link WHERE binding_id = $1 AND plane_item_uuid = $2 AND revision_id = $3)
		  AND revision = 1
	`, bindingID, planeItemUUID, revID).Scan(&scrubbedHistRat)
	if err != nil {
		t.Fatalf("failed to query scrubbed work_artifact_link_history rationale: %v", err)
	}
	if scrubbedHistRat != "[purged due to source deletion]" {
		t.Fatalf("expected scrubbed history rationale %q, got %q", "[purged due to source deletion]", scrubbedHistRat)
	}
}

func TestWorkAdvice_ProvenanceScrub_ExistingLinkUnlinkAndForget(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})

	_ = operatorStore.SetAdviceFeature(ctx, "active")
	_, _ = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_feature (feature_name, status, updated_at)
		VALUES ('work-artifacts/1', 'active', clock_timestamp())
		ON CONFLICT (feature_name) DO UPDATE SET status = 'active'
	`)

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	slug := "test-proj-" + bindingID.String()[:8]
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	// Create source record
	source, err := operatorStore.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft: Draft{
			Kind:        "note",
			Body:        "source text for existing link scrub test",
			ClaimType:   "self",
			Sensitivity: "shareable",
			Scope:       Scope{Repo: "fixture:repo", TaskID: "*", RunID: "*"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	artID := uuid.New()
	revID := uuid.New()
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_artifact (artifact_id, collection, kind, canonical_key, display_name)
		VALUES ($1, 'fixture:repo', 'report', 'link-test-key-1', 'link-test-artifact-1')
	`, artID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_artifact_revision (
			revision_id, artifact_id, collection, revision_label, created_by
		) VALUES (
			$1, $2, 'fixture:repo', 'v1', 'operator:test'
		)
	`, revID, artID)
	if err != nil {
		t.Fatal(err)
	}

	// 1. Initial operator link at revision 1
	initialOpRationale := "initial operator link rationale"
	link, err := operatorStore.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: slug,
		ItemUUID:    planeItemUUID,
		RevisionID:  revID,
		Role:        "evidence",
		Rationale:   initialOpRationale,
	})
	if err != nil {
		t.Fatalf("LinkWorkArtifact failed: %v", err)
	}
	if link.Revision != 1 {
		t.Fatalf("expected initial link revision 1, got %d", link.Revision)
	}

	// 2. Advice applies link at revision 2
	spec := AdviceInputSpec{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		SelectedSources: []SourceRefSpec{{
			RecordID: uuid.MustParse(source.RecordID),
			Version:  source.Version,
		}},
	}
	_, manifest, err := operatorStore.BuildWorkAdviceSnapshot(ctx, spec)
	if err != nil {
		t.Fatal(err)
	}
	manifestJSON, _ := json.Marshal(manifest)

	jobUUID := uuid.New()
	job := &WorkAdviceJob{
		JobUUID:             jobUUID,
		BindingID:           bindingID,
		PlaneItemUUID:       planeItemUUID,
		RequestUUID:         uuid.New(),
		Status:              "completed",
		InputManifest:       manifestJSON,
		InputManifestDigest: "test-manifest-digest",
		InputPayloadDigest:  "test-payload-digest",
		ExpiresAt:           time.Now().Add(1 * time.Hour),
	}
	if err := operatorStore.CreateAdviceJob(ctx, job); err != nil {
		t.Fatal(err)
	}

	propUUID := uuid.New()
	prop := &WorkAdviceProposal{
		ProposalUUID:      propUUID,
		JobUUID:           jobUUID,
		BindingID:         bindingID,
		PlaneItemUUID:     planeItemUUID,
		ReviewVersion:     1,
		Status:            "pending",
		RawResult:         `{"actions":[]}`,
		NormalizedPayload: json.RawMessage(`{"actions":[]}`),
		ExpiresAt:         time.Now().Add(1 * time.Hour),
	}
	actUUID := uuid.New()
	actPayload, _ := json.Marshal(advice.ArtifactPayload{
		ArtifactRevisionID: revID.String(),
		Role:               "evidence",
	})
	adviceRationale := "advice link rationale"
	act := &WorkAdviceAction{
		ActionUUID:    actUUID,
		ProposalUUID:  propUUID,
		ActionIndex:   0,
		ActionType:    "link_artifact",
		TargetPayload: actPayload,
		Rationale:     adviceRationale,
	}
	if err := operatorStore.RecordAdviceProposal(ctx, prop, []*WorkAdviceAction{act}); err != nil {
		t.Fatal(err)
	}

	receipt := &WorkAdviceApplicationReceipt{
		RequestUUID:  uuid.New(),
		ProposalUUID: propUUID,
	}
	appliedReceipt, err := operatorStore.ApplyAdviceProposal(ctx, receipt, 1, []uuid.UUID{actUUID})
	if err != nil {
		t.Fatalf("ApplyAdviceProposal failed: %v", err)
	}
	_ = appliedReceipt

	// 3. Operator unlinks at revision 3 (copies advice rationale)
	unlinkReqID := uuid.NewString()
	unlinked, err := operatorStore.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		RequestID:        unlinkReqID,
		ProjectSlug:      slug,
		ItemUUID:         planeItemUUID,
		LinkID:           link.LinkID,
		ExpectedRevision: 2,
	})
	if err != nil {
		t.Fatalf("UnlinkWorkArtifact failed: %v", err)
	}
	if unlinked.Revision != 3 {
		t.Fatalf("expected unlinked revision 3, got %d", unlinked.Revision)
	}

	// 4. Forget source record
	grant, err := operatorStore.Bootstrap(ctx, BootstrapRequest{
		RequestID: uuid.NewString(),
		Reason:    "test provenance scrub on Forget",
	})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := operatorStore.PreviewDeletion(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := operatorStore.Forget(ctx, ForgetRequest{
		RequestID:       uuid.NewString(),
		RecordID:        source.RecordID,
		ExpectedVersion: source.Version,
		GrantID:         grant.ID,
		PreviewID:       preview.PreviewID,
	}); err != nil {
		t.Fatalf("Forget failed: %v", err)
	}

	// 5. Assert:
	// - Revision 1 (operator rationale) is PRESERVED!
	var rev1Rat string
	err = operatorStore.pool.QueryRow(ctx, `SELECT rationale FROM cairn.work_artifact_link_history WHERE link_id = $1 AND revision = 1`, link.LinkID).Scan(&rev1Rat)
	if err != nil {
		t.Fatal(err)
	}
	if rev1Rat != initialOpRationale {
		t.Fatalf("expected rev 1 operator rationale %q to be preserved, got %q", initialOpRationale, rev1Rat)
	}

	// - Revision 2 (advice rationale) is SCRUBBED!
	var rev2Rat string
	err = operatorStore.pool.QueryRow(ctx, `SELECT rationale FROM cairn.work_artifact_link_history WHERE link_id = $1 AND revision = 2`, link.LinkID).Scan(&rev2Rat)
	if err != nil {
		t.Fatal(err)
	}
	if rev2Rat != "[purged due to source deletion]" {
		t.Fatalf("expected rev 2 rationale to be scrubbed, got %q", rev2Rat)
	}

	// - Revision 3 (unlinked revision copying advice rationale) is SCRUBBED!
	var rev3Rat string
	err = operatorStore.pool.QueryRow(ctx, `SELECT rationale FROM cairn.work_artifact_link_history WHERE link_id = $1 AND revision = 3`, link.LinkID).Scan(&rev3Rat)
	if err != nil {
		t.Fatal(err)
	}
	if rev3Rat != "[purged due to source deletion]" {
		t.Fatalf("expected rev 3 rationale to be scrubbed, got %q", rev3Rat)
	}

	// - Current link (at revision 3) is SCRUBBED!
	var curLinkRat string
	err = operatorStore.pool.QueryRow(ctx, `SELECT rationale FROM cairn.work_artifact_link WHERE link_id = $1`, link.LinkID).Scan(&curLinkRat)
	if err != nil {
		t.Fatal(err)
	}
	if curLinkRat != "[purged due to source deletion]" {
		t.Fatalf("expected current link rationale to be scrubbed, got %q", curLinkRat)
	}

	// - Cached mutation_request for unlink is SCRUBBED!
	var mutResp []byte
	err = operatorStore.pool.QueryRow(ctx, `SELECT response FROM cairn.mutation_request WHERE request_id = $1`, unlinkReqID).Scan(&mutResp)
	if err != nil {
		t.Fatal(err)
	}
	var mutLink WorkArtifactLink
	if err := json.Unmarshal(mutResp, &mutLink); err != nil {
		t.Fatal(err)
	}
	if mutLink.Rationale != "[purged due to source deletion]" {
		t.Fatalf("expected mutation_request cached unlink rationale to be scrubbed, got %q", mutLink.Rationale)
	}
}

func TestWorkAdvice_ProvenanceScrub_NonExistentLinkReplacedByOperator(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})

	_ = operatorStore.SetAdviceFeature(ctx, "active")
	_, _ = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_feature (feature_name, status, updated_at)
		VALUES ('work-artifacts/1', 'active', clock_timestamp())
		ON CONFLICT (feature_name) DO UPDATE SET status = 'active'
	`)

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	slug := "test-proj-" + bindingID.String()[:8]
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	source, err := operatorStore.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft: Draft{
			Kind:        "note",
			Body:        "source text for non-existent link scrub test",
			ClaimType:   "self",
			Sensitivity: "shareable",
			Scope:       Scope{Repo: "fixture:repo", TaskID: "*", RunID: "*"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	artID := uuid.New()
	revID := uuid.New()
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_artifact (artifact_id, collection, kind, canonical_key, display_name)
		VALUES ($1, 'fixture:repo', 'report', 'link-test-key-2', 'link-test-artifact-2')
	`, artID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_artifact_revision (
			revision_id, artifact_id, collection, revision_label, created_by
		) VALUES (
			$1, $2, 'fixture:repo', 'v1', 'operator:test'
		)
	`, revID, artID)
	if err != nil {
		t.Fatal(err)
	}

	// 1. Advice applies link at revision 1 (!existed)
	spec := AdviceInputSpec{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		SelectedSources: []SourceRefSpec{{
			RecordID: uuid.MustParse(source.RecordID),
			Version:  source.Version,
		}},
	}
	_, manifest, err := operatorStore.BuildWorkAdviceSnapshot(ctx, spec)
	if err != nil {
		t.Fatal(err)
	}
	manifestJSON, _ := json.Marshal(manifest)

	jobUUID := uuid.New()
	job := &WorkAdviceJob{
		JobUUID:             jobUUID,
		BindingID:           bindingID,
		PlaneItemUUID:       planeItemUUID,
		RequestUUID:         uuid.New(),
		Status:              "completed",
		InputManifest:       manifestJSON,
		InputManifestDigest: "test-manifest-digest",
		InputPayloadDigest:  "test-payload-digest",
		ExpiresAt:           time.Now().Add(1 * time.Hour),
	}
	if err := operatorStore.CreateAdviceJob(ctx, job); err != nil {
		t.Fatal(err)
	}

	propUUID := uuid.New()
	prop := &WorkAdviceProposal{
		ProposalUUID:      propUUID,
		JobUUID:           jobUUID,
		BindingID:         bindingID,
		PlaneItemUUID:     planeItemUUID,
		ReviewVersion:     1,
		Status:            "pending",
		RawResult:         `{"actions":[]}`,
		NormalizedPayload: json.RawMessage(`{"actions":[]}`),
		ExpiresAt:         time.Now().Add(1 * time.Hour),
	}
	actUUID := uuid.New()
	actPayload, _ := json.Marshal(advice.ArtifactPayload{
		ArtifactRevisionID: revID.String(),
		Role:               "evidence",
	})
	adviceRationale := "advice link rationale !existed"
	act := &WorkAdviceAction{
		ActionUUID:    actUUID,
		ProposalUUID:  propUUID,
		ActionIndex:   0,
		ActionType:    "link_artifact",
		TargetPayload: actPayload,
		Rationale:     adviceRationale,
	}
	if err := operatorStore.RecordAdviceProposal(ctx, prop, []*WorkAdviceAction{act}); err != nil {
		t.Fatal(err)
	}

	receipt := &WorkAdviceApplicationReceipt{
		RequestUUID:  uuid.New(),
		ProposalUUID: propUUID,
	}
	appliedReceipt, err := operatorStore.ApplyAdviceProposal(ctx, receipt, 1, []uuid.UUID{actUUID})
	if err != nil {
		t.Fatalf("ApplyAdviceProposal failed: %v", err)
	}
	_ = appliedReceipt

	var linkID uuid.UUID
	err = operatorStore.pool.QueryRow(ctx, `SELECT link_id FROM cairn.work_artifact_link WHERE binding_id = $1 AND plane_item_uuid = $2 AND revision_id = $3`, bindingID, planeItemUUID, revID).Scan(&linkID)
	if err != nil {
		t.Fatal(err)
	}

	// 2. Operator unlinks at revision 2
	_, err = operatorStore.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		ProjectSlug:      slug,
		ItemUUID:         planeItemUUID,
		LinkID:           linkID,
		ExpectedRevision: 1,
	})
	if err != nil {
		t.Fatalf("UnlinkWorkArtifact failed: %v", err)
	}

	// 3. Operator replaces link at revision 3 with independent rationale
	opReplacementRationale := "operator replacement rationale"
	replacedLink, err := operatorStore.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		ProjectSlug: slug,
		ItemUUID:    planeItemUUID,
		RevisionID:  revID,
		Role:        "evidence",
		Rationale:   opReplacementRationale,
	})
	if err != nil {
		t.Fatalf("LinkWorkArtifact failed: %v", err)
	}
	if replacedLink.Revision != 3 {
		t.Fatalf("expected replaced link revision 3, got %d", replacedLink.Revision)
	}

	// 4. Forget source record
	grant, err := operatorStore.Bootstrap(ctx, BootstrapRequest{
		RequestID: uuid.NewString(),
		Reason:    "test provenance replacement scrub on Forget",
	})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := operatorStore.PreviewDeletion(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := operatorStore.Forget(ctx, ForgetRequest{
		RequestID:       uuid.NewString(),
		RecordID:        source.RecordID,
		ExpectedVersion: source.Version,
		GrantID:         grant.ID,
		PreviewID:       preview.PreviewID,
	}); err != nil {
		t.Fatalf("Forget failed: %v", err)
	}

	// 5. Assert:
	// - Revision 1 (advice) is SCRUBBED!
	var rev1Rat string
	err = operatorStore.pool.QueryRow(ctx, `SELECT rationale FROM cairn.work_artifact_link_history WHERE link_id = $1 AND revision = 1`, linkID).Scan(&rev1Rat)
	if err != nil {
		t.Fatal(err)
	}
	if rev1Rat != "[purged due to source deletion]" {
		t.Fatalf("expected rev 1 to be scrubbed, got %q", rev1Rat)
	}

	// - Revision 2 (removed rev copying advice) is SCRUBBED!
	var rev2Rat string
	err = operatorStore.pool.QueryRow(ctx, `SELECT rationale FROM cairn.work_artifact_link_history WHERE link_id = $1 AND revision = 2`, linkID).Scan(&rev2Rat)
	if err != nil {
		t.Fatal(err)
	}
	if rev2Rat != "[purged due to source deletion]" {
		t.Fatalf("expected rev 2 to be scrubbed, got %q", rev2Rat)
	}

	// - Revision 3 (operator replacement) is PRESERVED!
	var rev3Rat string
	err = operatorStore.pool.QueryRow(ctx, `SELECT rationale FROM cairn.work_artifact_link_history WHERE link_id = $1 AND revision = 3`, linkID).Scan(&rev3Rat)
	if err != nil {
		t.Fatal(err)
	}
	if rev3Rat != opReplacementRationale {
		t.Fatalf("expected rev 3 operator rationale %q to be preserved, got %q", opReplacementRationale, rev3Rat)
	}

	// - Current link (revision 3) is PRESERVED!
	var curLinkRat string
	err = operatorStore.pool.QueryRow(ctx, `SELECT rationale FROM cairn.work_artifact_link WHERE link_id = $1`, linkID).Scan(&curLinkRat)
	if err != nil {
		t.Fatal(err)
	}
	if curLinkRat != opReplacementRationale {
		t.Fatalf("expected current link rationale %q to be preserved, got %q", opReplacementRationale, curLinkRat)
	}
}

func TestWorkAdvice_ConcurrentPatchAdviceControlDisjointDisables(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})

	_ = operatorStore.SetAdviceFeature(ctx, "active")

	boolPtr := func(b bool) *bool { return &b }

	for i := 0; i < 15; i++ {
		// Reset to true, true
		_, err := operatorStore.SetAdviceControl(ctx, true, true)
		if err != nil {
			t.Fatal(err)
		}

		var wg sync.WaitGroup
		ready := make(chan struct{})
		errCh := make(chan error, 2)

		wg.Add(2)
		go func() {
			defer wg.Done()
			<-ready
			_, err := operatorStore.PatchAdviceControl(ctx, boolPtr(false), nil)
			if err != nil {
				errCh <- err
			}
		}()

		go func() {
			defer wg.Done()
			<-ready
			_, err := operatorStore.PatchAdviceControl(ctx, nil, boolPtr(false))
			if err != nil {
				errCh <- err
			}
		}()

		close(ready)
		wg.Wait()
		close(errCh)

		for err := range errCh {
			t.Fatalf("concurrent PatchAdviceControl error on iter %d: %v", i, err)
		}

		ctrl, err := operatorStore.GetAdviceControl(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if ctrl.GenerationEnabled || ctrl.ApplyEnabled {
			t.Fatalf("iter %d: expected both false after concurrent disjoint disables, got gen=%v apply=%v",
				i, ctrl.GenerationEnabled, ctrl.ApplyEnabled)
		}
	}
}

func TestWorkAdvice_ApplyAndUndoAgainstRemovedLinkEdge(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	for _, f := range []string{"work-view/1", "work-artifacts/1", "work-advice/1"} {
		_ = operatorStore.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{FeatureName: f, Status: "active"})
	}

	collection := "fixture:advice-removed-edge:" + uuid.NewString()
	p, err := operatorStore.AddWorkProject(ctx, AddWorkProjectRequest{
		RequestID:      uuid.NewString(),
		Collection:     collection,
		LocalSlug:      "removed-edge",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  uuid.New(),
		ProjectUUID:    uuid.New(),
		ConfigRevision: "v1",
	})
	if err != nil {
		t.Fatal(err)
	}
	item := uuid.New()
	ref, err := operatorStore.PrepareRefresh(ctx, collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	_, err = operatorStore.CommitRefreshSnapshot(ctx, ref, []plane.NormalizedItem{
		{PlaneItemUUID: item, Identifier: "EDGE-1", Title: "Edge Test"},
	}, false, nil)
	if err != nil {
		t.Fatal(err)
	}

	rev, err := operatorStore.RegisterWorkArtifact(ctx, RegisterWorkArtifactRequest{
		RequestID:     uuid.NewString(),
		Collection:    collection,
		Kind:          "report",
		ReportLocator: "https://reports.example.com/" + uuid.NewString(),
		RevisionLabel: "v1",
	})
	if err != nil {
		t.Fatal(err)
	}

	linked, err := operatorStore.LinkWorkArtifact(ctx, LinkWorkArtifactRequest{
		RequestID:   uuid.NewString(),
		Collection:  collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    item,
		RevisionID:  rev.RevisionID,
		Role:        "evidence",
		Rationale:   "initial link",
	})
	if err != nil {
		t.Fatal(err)
	}

	unlinked, err := operatorStore.UnlinkWorkArtifact(ctx, UnlinkWorkArtifactRequest{
		RequestID:        uuid.NewString(),
		Collection:       collection,
		ProjectSlug:      p.LocalSlug,
		ItemUUID:         item,
		LinkID:           linked.LinkID,
		ExpectedRevision: linked.Revision,
	})
	if err != nil || unlinked.State != "removed" {
		t.Fatalf("unlink failed: %v", err)
	}

	_ = operatorStore.SetWorkAdvicePolicy(ctx, p.BindingID, []string{"artifacts", "sources"})

	rawAdvice, _ := json.Marshal(map[string]any{
		"actions": []any{
			map[string]any{
				"action_type":          advice.ActionLinkArtifact,
				"artifact_revision_id": rev.RevisionID.String(),
				"role":                 "evidence",
				"rationale":            "advice reactivation",
			},
		},
	})
	provider := &workflowParsedAdviceProvider{result: string(rawAdvice)}
	job := &WorkAdviceJob{
		BindingID:       p.BindingID,
		PlaneItemUUID:   item,
		RequestUUID:     uuid.New(),
		ProfileName:     advice.RouteCodexSubscription,
		ModelRoute:      "mock",
		TemplateVersion: 1,
	}
	prop, actions, err := operatorStore.GenerateWorkAdvice(ctx, job, AdviceInputSpec{
		BindingID:            p.BindingID,
		PlaneItemUUID:        item,
		CandidateRevisionIDs: []uuid.UUID{rev.RevisionID},
	}, provider)
	if err != nil || prop == nil || len(actions) != 1 {
		t.Fatalf("GenerateWorkAdvice failed: %v", err)
	}

	receipt, err := operatorStore.ApplyAdviceProposal(ctx, &WorkAdviceApplicationReceipt{
		RequestUUID:  uuid.New(),
		ProposalUUID: prop.ProposalUUID,
	}, prop.ReviewVersion, []uuid.UUID{actions[0].ActionUUID})
	if err != nil || receipt == nil {
		t.Fatalf("ApplyAdviceProposal failed: %v", err)
	}

	var curState string
	var curRev int64
	var remAt *time.Time
	var remBy *string
	err = operatorStore.pool.QueryRow(ctx, `
		SELECT state, revision, removed_at, removed_by
		FROM cairn.work_artifact_link
		WHERE link_id = $1
	`, linked.LinkID).Scan(&curState, &curRev, &remAt, &remBy)
	if err != nil || curState != "active" || remAt != nil || remBy != nil {
		t.Fatalf("expected link active with null removed fields, got state=%s remAt=%v remBy=%v", curState, remAt, remBy)
	}

	err = operatorStore.UndoAdviceApplication(ctx, receipt.RequestUUID)
	if err != nil {
		t.Fatalf("UndoAdviceApplication failed: %v", err)
	}

	err = operatorStore.pool.QueryRow(ctx, `
		SELECT state, revision, removed_at, removed_by
		FROM cairn.work_artifact_link
		WHERE link_id = $1
	`, linked.LinkID).Scan(&curState, &curRev, &remAt, &remBy)
	if err != nil || curState != "removed" || remAt == nil || remBy == nil {
		t.Fatalf("expected restored link to be 'removed' with non-null removed fields, got state=%s remAt=%v remBy=%v", curState, remAt, remBy)
	}
}

type realHeldAdviceProvider struct {
	gateFile string
	readyCh  chan advice.ProcessIdentity
}

func (p *realHeldAdviceProvider) Name() string { return "real-held" }
func (p *realHeldAdviceProvider) VerifyCapabilities(ctx context.Context) (*advice.CodexCapabilities, error) {
	return &advice.CodexCapabilities{
		Schema:         advice.CodexAdviceCapabilitiesSchema,
		RequestSchema:  advice.CodexAdviceRequestSchema,
		ResultSchema:   advice.CodexAdviceResultSchema,
		Provider:       "openai",
		AuthMethod:     "chatgpt_subscription",
		OneRequest:     true,
		UpstreamCommit: advice.RequiredCodexUpstreamCommit,
		PatchSHA256:    "0000000000000000000000000000000000000000000000000000000000000000",
	}, nil
}
func (p *realHeldAdviceProvider) GenerateAdvice(ctx context.Context, snapshot *advice.Snapshot, deadline time.Duration, onLaunch func(advice.ProcessIdentity) error) (*advice.Proposal, *advice.ProcessResult, error) {
	script := fmt.Sprintf("#!/bin/bash\nwhile [ -f %q ]; do\n\tsleep 0.05\ndone\necho '{\"actions\":[{\"action_type\":\"annotate_progress\",\"text\":\"held progress\"}]}'\nexit 0\n", p.gateFile)
	tmpDir := os.TempDir()
	scriptFile := filepath.Join(tmpDir, "held-script-"+uuid.NewString()+".sh")
	if err := os.WriteFile(scriptFile, []byte(script), 0755); err != nil {
		return nil, nil, err
	}
	defer os.Remove(scriptFile)

	res, err := advice.RunIsolatedProcess(ctx, "/bin/bash", []string{scriptFile}, nil, advice.BuildAllowlistedEnv(nil), tmpDir, func(id advice.ProcessIdentity) error {
		if p.readyCh != nil {
			p.readyCh <- id
		}
		if onLaunch != nil {
			return onLaunch(id)
		}
		return nil
	})
	if err != nil {
		return nil, nil, err
	}
	prop, pErr := advice.ParseProposal(string(res.Stdout), snapshot)
	return prop, res, pErr
}

func TestWorkAdvice_RealHeldProviderLifecycle(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	// (a) Healthy in-flight job: ReconcileAdviceJobs + SweepExpiredAdvice preserve running job and child process
	gateFile := filepath.Join(os.TempDir(), "held-gate-"+uuid.NewString())
	if err := os.WriteFile(gateFile, []byte("hold"), 0644); err != nil {
		t.Fatal(err)
	}
	defer os.Remove(gateFile)

	readyCh := make(chan advice.ProcessIdentity, 1)
	prov := &realHeldAdviceProvider{gateFile: gateFile, readyCh: readyCh}
	job := &WorkAdviceJob{
		BindingID:       bindingID,
		PlaneItemUUID:   planeItemUUID,
		RequestUUID:     uuid.New(),
		DeadlineSeconds: 60,
	}
	spec := AdviceInputSpec{BindingID: bindingID, PlaneItemUUID: planeItemUUID}

	type execResult struct {
		prop *WorkAdviceProposal
		err  error
	}
	doneCh := make(chan execResult, 1)
	go func() {
		p, _, err := operatorStore.ExecuteAdviceJob(ctx, job, spec, prov)
		doneCh <- execResult{prop: p, err: err}
	}()

	var childIdent advice.ProcessIdentity
	select {
	case childIdent = <-readyCh:
	case <-time.After(5 * time.Second):
		t.Fatal("timed out waiting for child process launch")
	}

	// Verify child process group is running
	if err := syscall.Kill(-childIdent.PGID, 0); err != nil {
		t.Fatalf("child process group not alive: %v", err)
	}

	// Reconcile and sweep while healthy -> must NOT kill child or fail job
	reconciled, err := operatorStore.ReconcileAdviceJobs(ctx)
	if err != nil {
		t.Fatalf("ReconcileAdviceJobs error: %v", err)
	}
	if len(reconciled) > 0 {
		t.Fatalf("expected 0 reconciled jobs, got %+v", reconciled)
	}
	swept, err := operatorStore.SweepExpiredAdvice(ctx)
	if err != nil {
		t.Fatalf("SweepExpiredAdvice error: %v", err)
	}
	if swept > 0 {
		t.Fatalf("expected 0 swept jobs, got %d", swept)
	}

	// Child still alive
	if err := syscall.Kill(-childIdent.PGID, 0); err != nil {
		t.Fatalf("child process group died unexpectedly: %v", err)
	}

	// Release gate -> completes cleanly
	_ = os.Remove(gateFile)
	select {
	case res := <-doneCh:
		if res.err != nil {
			t.Fatalf("ExecuteAdviceJob failed after releasing gate: %v", res.err)
		}
		if res.prop == nil || res.prop.Status != "pending" {
			t.Fatalf("expected pending proposal, got %+v", res.prop)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("timed out waiting for ExecuteAdviceJob completion")
	}

	// (b) Past-deadline job: ReconcileAdviceJobs kills child and transitions to timed_out/MODEL_TIMEOUT
	gateFile2 := filepath.Join(os.TempDir(), "held-gate-"+uuid.NewString())
	_ = os.WriteFile(gateFile2, []byte("hold"), 0644)
	defer os.Remove(gateFile2)

	readyCh2 := make(chan advice.ProcessIdentity, 1)
	prov2 := &realHeldAdviceProvider{gateFile: gateFile2, readyCh: readyCh2}
	job2 := &WorkAdviceJob{
		BindingID:       bindingID,
		PlaneItemUUID:   planeItemUUID,
		RequestUUID:     uuid.New(),
		DeadlineSeconds: 1, // short deadline
	}
	go func() {
		_, _, _ = operatorStore.ExecuteAdviceJob(ctx, job2, spec, prov2)
	}()

	var childIdent2 advice.ProcessIdentity
	select {
	case childIdent2 = <-readyCh2:
	case <-time.After(5 * time.Second):
		t.Fatal("timed out waiting for child 2 launch")
	}

	// Wait past deadline
	time.Sleep(1200 * time.Millisecond)

	reconciled2, err := operatorStore.ReconcileAdviceJobs(ctx)
	if err != nil {
		t.Fatalf("ReconcileAdviceJobs error: %v", err)
	}
	foundTimedOut := false
	for _, r := range reconciled2 {
		if r.JobUUID == job2.JobUUID {
			foundTimedOut = true
			if r.FinalState != "timed_out" {
				t.Fatalf("expected final state timed_out, got %s", r.FinalState)
			}
		}
	}
	if !foundTimedOut {
		t.Fatal("expected past-deadline job to be reconciled")
	}

	// Child process group should be dead (ESRCH)
	time.Sleep(100 * time.Millisecond)
	if err := syscall.Kill(-childIdent2.PGID, 0); err == nil {
		t.Fatalf("child process group %d is still alive, expected killed", childIdent2.PGID)
	}
}

func TestWorkAdvice_30DayTombstoneTerminalOutcomeReplay(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)
	spec := AdviceInputSpec{BindingID: bindingID, PlaneItemUUID: planeItemUUID}

	// 1. SweepExpiredAdvice TIMEOUT replay
	reqID1 := uuid.New()
	jobID1 := uuid.New()
	_, err := operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_advice_job (
			job_uuid, binding_id, plane_item_uuid, caller, profile_name, model_route,
			template_version, request_uuid, input_manifest, input_manifest_digest, input_payload_digest,
			status, deadline_seconds, process_identity, created_at, input_expires_at, expires_at
		) VALUES (
			$1, $2, $3, 'operator:test', 'default', 'test',
			1, $4, '{}'::jsonb, 'digest', 'digest',
			'pending', 10, '{}'::jsonb, clock_timestamp() - interval '20 seconds', clock_timestamp() + interval '1 hour', clock_timestamp() + interval '1 hour'
		)
	`, jobID1, bindingID, planeItemUUID, reqID1)
	if err != nil {
		t.Fatal(err)
	}
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_advice_request_tombstone (
			caller, request_uuid, job_uuid, binding_id, plane_item_uuid, request_digest, terminal_status
		) VALUES (
			'operator:test', $1, $2, $3, $4, 'digest', 'pending'
		)
	`, reqID1, jobID1, bindingID, planeItemUUID)
	if err != nil {
		t.Fatal(err)
	}

	swept, err := operatorStore.SweepExpiredAdvice(ctx)
	if err != nil || swept != 1 {
		t.Fatalf("expected 1 swept job, got %d, err=%v", swept, err)
	}
	// Simulate 30d deletion of job
	_, err = operatorStore.pool.Exec(ctx, `DELETE FROM cairn.work_advice_job WHERE job_uuid = $1`, jobID1)
	if err != nil {
		t.Fatal(err)
	}

	countedProv := &countedAdviceProvider{}
	replayJob1 := &WorkAdviceJob{BindingID: bindingID, PlaneItemUUID: planeItemUUID, RequestUUID: reqID1}
	_, _, err = operatorStore.ExecuteAdviceJob(ctx, replayJob1, spec, countedProv)
	if err == nil || Code(err) != "TIMEOUT" {
		t.Fatalf("expected TIMEOUT error on replay after 30d deletion, got %v", err)
	}
	if countedProv.callCount.Load() != 0 {
		t.Fatalf("expected 0 provider calls on tombstone replay, got %d", countedProv.callCount.Load())
	}

	// 2. ReconcileAdviceJobs RESTORE_FENCE_TRIGGERED replay
	reqID2 := uuid.New()
	jobID2 := uuid.New()
	oldManifest, _ := json.Marshal(WorkAdviceInputManifest{RestoreGeneration: 999999})
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_advice_job (
			job_uuid, binding_id, plane_item_uuid, caller, profile_name, model_route,
			template_version, request_uuid, input_manifest, input_manifest_digest, input_payload_digest,
			status, deadline_seconds, process_identity, created_at, input_expires_at, expires_at
		) VALUES (
			$1, $2, $3, 'operator:test', 'default', 'test',
			1, $4, $5, 'digest', 'digest',
			'pending', 60, '{}'::jsonb, clock_timestamp(), clock_timestamp() + interval '1 hour', clock_timestamp() + interval '1 hour'
		)
	`, jobID2, bindingID, planeItemUUID, reqID2, oldManifest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_advice_request_tombstone (
			caller, request_uuid, job_uuid, binding_id, plane_item_uuid, request_digest, terminal_status
		) VALUES (
			'operator:test', $1, $2, $3, $4, 'digest', 'pending'
		)
	`, reqID2, jobID2, bindingID, planeItemUUID)
	if err != nil {
		t.Fatal(err)
	}

	reconciled, err := operatorStore.ReconcileAdviceJobs(ctx)
	if err != nil || len(reconciled) != 1 {
		t.Fatalf("expected 1 reconciled job, got %d, err=%v", len(reconciled), err)
	}
	// Simulate 30d deletion
	_, _ = operatorStore.pool.Exec(ctx, `DELETE FROM cairn.work_advice_job WHERE job_uuid = $1`, jobID2)

	replayJob2 := &WorkAdviceJob{BindingID: bindingID, PlaneItemUUID: planeItemUUID, RequestUUID: reqID2}
	_, _, err = operatorStore.ExecuteAdviceJob(ctx, replayJob2, spec, countedProv)
	if err == nil || Code(err) != "RESTORE_FENCE_TRIGGERED" {
		t.Fatalf("expected RESTORE_FENCE_TRIGGERED error on replay, got %v", err)
	}
	if countedProv.callCount.Load() != 0 {
		t.Fatalf("expected 0 provider calls, got %d", countedProv.callCount.Load())
	}
}

func TestWorkAdvice_Lower79UndoAndRetirement(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	prov := &countedAdviceProvider{
		proposal: &advice.Proposal{
			Summary: "test advice",
			Actions: []advice.ProposedAction{
				{
					ActionType: advice.ActionAnnotateProgress,
					Progress:   &advice.ProgressPayload{Text: "lower 79 progress"},
				},
			},
		},
	}
	job := &WorkAdviceJob{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		RequestUUID:   uuid.New(),
	}
	spec := AdviceInputSpec{BindingID: bindingID, PlaneItemUUID: planeItemUUID}
	prop, actions, err := operatorStore.ExecuteAdviceJob(ctx, job, spec, prov)
	if err != nil {
		t.Fatalf("ExecuteAdviceJob failed: %v", err)
	}

	receiptReq := &WorkAdviceApplicationReceipt{
		RequestUUID:  uuid.New(),
		ProposalUUID: prop.ProposalUUID,
	}
	receipt, err := operatorStore.ApplyAdviceProposal(ctx, receiptReq, prop.ReviewVersion, []uuid.UUID{actions[0].ActionUUID})
	if err != nil {
		t.Fatalf("ApplyAdviceProposal failed: %v", err)
	}

	// Disable work-advice/1
	err = operatorStore.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		FeatureName: "work-advice/1",
		Status:      "disabled",
	})
	if err != nil {
		t.Fatalf("SetWorkFeatureStatus disabled failed: %v", err)
	}

	// Open lower-79 Store without work-advice/1
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	lowerPolicy := CompatibilityPolicy{
		MaxMigrationVersion:   79,
		SupportedWorkFeatures: map[string]bool{"work-view/1": true, "work-artifacts/1": true},
	}
	lowerStore, err := OpenWithCompatibility(ctx, dsn, Channel{Principal: "operator:test", Operator: true}, lowerPolicy)
	if err != nil {
		t.Fatalf("OpenWithCompatibility failed: %v", err)
	}
	defer lowerStore.Close()

	// Lower-79 maintenance Undo
	err = lowerStore.UndoAdviceApplication(ctx, receipt.RequestUUID)
	if err != nil {
		t.Fatalf("lowerStore.UndoAdviceApplication failed: %v", err)
	}

	rcpt, err := lowerStore.GetAdviceReceipt(ctx, receipt.RequestUUID)
	if err != nil {
		t.Fatalf("lowerStore.GetAdviceReceipt failed: %v", err)
	}
	if !rcpt.Undone {
		t.Fatalf("expected receipt to be marked Undone")
	}

	// Exact-retry of UndoAdviceApplication
	err = lowerStore.UndoAdviceApplication(ctx, receipt.RequestUUID)
	if err != nil {
		t.Fatalf("exact-retry UndoAdviceApplication failed: %v", err)
	}

	// Retiring work-advice/1 succeeds when 0-action proposal exists
	// All actions undone -> proposal has 0 pending actions!
	err = operatorStore.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		FeatureName: "work-advice/1",
		Status:      "retired",
	})
	if err != nil {
		t.Fatalf("retiring work-advice/1 failed when proposal has 0 pending actions: %v", err)
	}
}

func TestWorkAdvice_ReceiptHydrationAndRepoScoping(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	prov := &countedAdviceProvider{
		proposal: &advice.Proposal{
			Summary: "test advice",
			Actions: []advice.ProposedAction{
				{
					ActionType: advice.ActionAnnotateProgress,
					Progress:   &advice.ProgressPayload{Text: "scoped receipt progress"},
				},
			},
		},
	}
	job := &WorkAdviceJob{
		BindingID:     bindingID,
		PlaneItemUUID: planeItemUUID,
		RequestUUID:   uuid.New(),
	}
	spec := AdviceInputSpec{BindingID: bindingID, PlaneItemUUID: planeItemUUID}
	prop, actions, err := operatorStore.ExecuteAdviceJob(ctx, job, spec, prov)
	if err != nil {
		t.Fatalf("ExecuteAdviceJob failed: %v", err)
	}

	receiptReq := &WorkAdviceApplicationReceipt{
		RequestUUID:  uuid.New(),
		ProposalUUID: prop.ProposalUUID,
	}
	receipt, err := operatorStore.ApplyAdviceProposal(ctx, receiptReq, prop.ReviewVersion, []uuid.UUID{actions[0].ActionUUID})
	if err != nil {
		t.Fatalf("ApplyAdviceProposal failed: %v", err)
	}

	// 1. Live receipt hydration
	r1, err := operatorStore.GetAdviceReceipt(ctx, receipt.RequestUUID)
	if err != nil {
		t.Fatalf("GetAdviceReceipt failed: %v", err)
	}
	if r1.BindingID != bindingID || r1.PlaneItemUUID != planeItemUUID || len(r1.SourceRecordIDs) == 0 {
		t.Fatalf("GetAdviceReceipt did not hydrate fields: binding=%v item=%v srcs=%s", r1.BindingID, r1.PlaneItemUUID, string(r1.SourceRecordIDs))
	}

	r2, err := operatorStore.GetAdviceReceiptByUUID(ctx, receipt.ReceiptUUID)
	if err != nil {
		t.Fatalf("GetAdviceReceiptByUUID failed: %v", err)
	}
	if r2.BindingID != bindingID || r2.PlaneItemUUID != planeItemUUID || len(r2.SourceRecordIDs) == 0 {
		t.Fatalf("GetAdviceReceiptByUUID did not hydrate fields: %+v", r2)
	}

	list, err := operatorStore.ListAdviceApplications(ctx, prop.ProposalUUID)
	if err != nil || len(list) != 1 {
		t.Fatalf("ListAdviceApplications failed: %v len=%d", err, len(list))
	}
	if list[0].BindingID != bindingID || list[0].PlaneItemUUID != planeItemUUID {
		t.Fatalf("ListAdviceApplications item did not hydrate fields: %+v", list[0])
	}

	// 2. >30d retained stub hydration where proposal_uuid IS NULL
	_, err = operatorStore.pool.Exec(ctx, `DELETE FROM cairn.work_advice_proposal WHERE proposal_uuid = $1`, prop.ProposalUUID)
	if err != nil {
		t.Fatal(err)
	}
	// Verify proposal_uuid was set to NULL on receipt via ON DELETE SET NULL
	var nullProp *uuid.UUID
	err = operatorStore.pool.QueryRow(ctx, `SELECT proposal_uuid FROM cairn.work_advice_application_receipt WHERE receipt_uuid = $1`, receipt.ReceiptUUID).Scan(&nullProp)
	if err != nil || nullProp != nil {
		t.Fatalf("expected proposal_uuid to be NULL on receipt, got %v, err=%v", nullProp, err)
	}

	stubR, err := operatorStore.GetAdviceReceipt(ctx, receipt.RequestUUID)
	if err != nil {
		t.Fatalf("GetAdviceReceipt failed on >30d stub: %v", err)
	}
	if stubR.ProposalUUID != uuid.Nil || stubR.BindingID != bindingID || stubR.PlaneItemUUID != planeItemUUID {
		t.Fatalf("unexpected stub receipt hydration: %+v", stubR)
	}

	stubR2, err := operatorStore.GetAdviceReceiptByUUID(ctx, receipt.ReceiptUUID)
	if err != nil {
		t.Fatalf("GetAdviceReceiptByUUID failed on >30d stub: %v", err)
	}
	if stubR2.BindingID != bindingID || stubR2.PlaneItemUUID != planeItemUUID {
		t.Fatalf("unexpected stub receipt by UUID: %+v", stubR2)
	}

	// 3. Repo scoping: matching-Repo operator succeeds, cross-Repo operator fails
	var collection string
	_ = operatorStore.pool.QueryRow(ctx, `SELECT collection FROM cairn.work_project WHERE binding_id = $1`, bindingID).Scan(&collection)

	matchingOpStore := testStore(t, Channel{Principal: "op:matching", Operator: true, Repo: collection})
	if _, err := matchingOpStore.GetAdviceReceipt(ctx, receipt.RequestUUID); err != nil {
		t.Fatalf("matching repo operator failed: %v", err)
	}

	crossOpStore := testStore(t, Channel{Principal: "op:other", Operator: true, Repo: "other-repo-" + uuid.NewString()[:8]})
	if _, err := crossOpStore.GetAdviceReceipt(ctx, receipt.RequestUUID); err == nil {
		t.Fatalf("expected cross-repo operator to be rejected with repo mismatch")
	}
}

func TestWorkAdvice_AnnotationHistorySnapshotAuthAndScoping(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	agentStore := testStore(t, Channel{Principal: "agent:test", Operator: false})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	var collection string
	_ = operatorStore.pool.QueryRow(ctx, `SELECT collection FROM cairn.work_project WHERE binding_id = $1`, bindingID).Scan(&collection)

	annotID := uuid.New()
	_, err := operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_annotation (annotation_id, revision, binding_id, plane_item_uuid, kind, body, state, author, created_at, updated_at)
		VALUES ($1, 1, $2, $3, 'progress', 'first progress', 'active', 'operator:test', clock_timestamp(), clock_timestamp())
	`, annotID, bindingID, planeItemUUID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_annotation_history (annotation_id, revision, body, state, author, created_at)
		VALUES ($1, 1, 'first progress', 'active', 'operator:test', clock_timestamp())
	`, annotID)
	if err != nil {
		t.Fatal(err)
	}

	// 1. Non-operator refusal
	_, _, _, err = agentStore.GetAnnotationHistorySnapshot(ctx, annotID, 1)
	if err == nil {
		t.Fatalf("expected non-operator refusal, got nil")
	}

	// 2. Wrong-repo operator refusal
	wrongOpStore := testStore(t, Channel{Principal: "op:wrong", Operator: true, Repo: "other-repo-" + uuid.NewString()[:8]})
	_, _, _, err = wrongOpStore.GetAnnotationHistorySnapshot(ctx, annotID, 1)
	if err == nil {
		t.Fatalf("expected wrong-repo operator refusal, got nil")
	}

	// 3. Matching-repo operator success
	matchOpStore := testStore(t, Channel{Principal: "op:match", Operator: true, Repo: collection})
	body, state, ok, err := matchOpStore.GetAnnotationHistorySnapshot(ctx, annotID, 1)
	if err != nil || !ok {
		t.Fatalf("expected success, got ok=%v, err=%v", ok, err)
	}
	if body != "first progress" || state != "active" {
		t.Fatalf("unexpected snapshot: body=%q state=%q", body, state)
	}
}

func TestWorkAdvice_ReconcilePreservesUncertainStatusWithChildKill(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	cmd := exec.Command("sleep", "30")
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		t.Fatalf("failed to start isolated child: %v", err)
	}
	childPID := cmd.Process.Pid
	childPGID, err := syscall.Getpgid(childPID)
	if err != nil {
		_ = cmd.Process.Kill()
		t.Fatalf("Getpgid failed: %v", err)
	}
	defer func() {
		_ = syscall.Kill(-childPGID, syscall.SIGKILL)
		_ = cmd.Wait()
	}()

	ident := advice.ProcessIdentity{
		PID:        childPID,
		PGID:       childPGID,
		StartTicks: 1000,
		BootID:     "boot-reconcile-uncertain",
	}
	identBytes, _ := json.Marshal(ident)
	jobUUID := uuid.New()
	reqUUID := uuid.New()

	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_advice_job (
			job_uuid, binding_id, plane_item_uuid, caller, profile_name, model_route,
			template_version, request_uuid, input_manifest, input_manifest_digest, input_payload_digest,
			status, deadline_seconds, process_identity, created_at, input_expires_at, expires_at, error_code
		) VALUES (
			$1, $2, $3, 'operator:test', 'default', 'test',
			1, $4, '{}'::jsonb, 'digest', 'digest',
			'uncertain', 60, $5, clock_timestamp(), clock_timestamp() + interval '1 hour', clock_timestamp() + interval '1 hour', 'RECORD_FAILED'
		)
	`, jobUUID, bindingID, planeItemUUID, reqUUID, identBytes)
	if err != nil {
		t.Fatal(err)
	}

	reconciled, err := operatorStore.ReconcileAdviceJobs(ctx)
	if err != nil {
		t.Fatalf("ReconcileAdviceJobs failed: %v", err)
	}
	if len(reconciled) != 1 {
		t.Fatalf("expected 1 reconciled job, got %d", len(reconciled))
	}
	if reconciled[0].FinalStatus != "uncertain" || reconciled[0].FinalErrorCode != "RECORD_FAILED" {
		t.Fatalf("expected finalStatus=uncertain, finalErrorCode=RECORD_FAILED, got status=%s, errCode=%s", reconciled[0].FinalStatus, reconciled[0].FinalErrorCode)
	}

	probeErr := syscall.Kill(childPID, 0)
	deadline := time.Now().Add(500 * time.Millisecond)
	for probeErr == nil && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
		probeErr = syscall.Kill(childPID, 0)
	}
	if !errors.Is(probeErr, syscall.ESRCH) {
		t.Fatalf("expected child to be killed (ESRCH), got probeErr=%v", probeErr)
	}

	var dbStatus, dbErrCode string
	err = operatorStore.pool.QueryRow(ctx, `SELECT status, error_code FROM cairn.work_advice_job WHERE job_uuid = $1`, jobUUID).Scan(&dbStatus, &dbErrCode)
	if err != nil {
		t.Fatal(err)
	}
	if dbStatus != "uncertain" || dbErrCode != "RECORD_FAILED" {
		t.Fatalf("DB job state: status=%s, error_code=%s (expected uncertain, RECORD_FAILED)", dbStatus, dbErrCode)
	}

	prov := &countedAdviceProvider{}
	spec := AdviceInputSpec{BindingID: bindingID, PlaneItemUUID: planeItemUUID}
	newJob := &WorkAdviceJob{BindingID: bindingID, PlaneItemUUID: planeItemUUID, RequestUUID: uuid.New()}
	_, _, err = operatorStore.ExecuteAdviceJob(ctx, newJob, spec, prov)
	if err == nil {
		t.Fatalf("expected ExecuteAdviceJob to be blocked while uncertain job exists, got nil")
	}
	if prov.callCount.Load() != 0 {
		t.Fatalf("provider was invoked while item in uncertain state: %d calls", prov.callCount.Load())
	}

	// 2. Unconfirmed cleanup on uncertain (e.g. foreign boot_id)
	foreignJobUUID := uuid.New()
	foreignIdent := advice.ProcessIdentity{
		PID:        99999,
		PGID:       99999,
		StartTicks: 1000,
		BootID:     "foreign-boot-id",
	}
	foreignIdentBytes, _ := json.Marshal(foreignIdent)
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_advice_job (
			job_uuid, binding_id, plane_item_uuid, caller, profile_name, model_route,
			template_version, request_uuid, input_manifest, input_manifest_digest, input_payload_digest,
			status, deadline_seconds, process_identity, created_at, input_expires_at, expires_at, error_code
		) VALUES (
			$1, $2, $3, 'operator:test', 'default', 'test',
			1, $4, '{}'::jsonb, 'digest', 'digest',
			'uncertain', 60, $5, clock_timestamp(), clock_timestamp() + interval '1 hour', clock_timestamp() + interval '1 hour', 'RECORD_FAILED'
		)
	`, foreignJobUUID, bindingID, planeItemUUID, uuid.New(), foreignIdentBytes)
	if err != nil {
		t.Fatal(err)
	}

	reconciled2, err := operatorStore.ReconcileAdviceJobs(ctx)
	if err != nil {
		t.Fatalf("ReconcileAdviceJobs 2 failed: %v", err)
	}
	var foundForeign bool
	for _, r := range reconciled2 {
		if r.JobUUID == foreignJobUUID {
			foundForeign = true
			if r.FinalStatus != "uncertain" || r.FinalErrorCode != "RECORD_FAILED" {
				t.Fatalf("expected uncertain/RECORD_FAILED, got: %s/%s", r.FinalStatus, r.FinalErrorCode)
			}
			if r.ActionTaken != "retained_uncertain_cleanup_unconfirmed" || r.CleanupError == "" {
				t.Fatalf("expected retained_uncertain_cleanup_unconfirmed with CleanupError, got action=%s err=%s", r.ActionTaken, r.CleanupError)
			}
		}
	}
	if !foundForeign {
		t.Fatalf("foreign job was not reconciled")
	}
}

func TestWorkAdvice_DerivedSourceRecordIDs_EditedBlockerNotOverPurged(t *testing.T) {
	ctx := context.Background()
	operatorStore := testStore(t, Channel{Principal: "operator:test", Operator: true})
	_ = operatorStore.SetAdviceFeature(ctx, "active")

	bindingID := uuid.New()
	planeItemUUID := uuid.New()
	seedWorkAdviceTestEntities(t, ctx, operatorStore, bindingID, planeItemUUID)

	var collection string
	_ = operatorStore.pool.QueryRow(ctx, `SELECT collection FROM cairn.work_project WHERE binding_id = $1`, bindingID).Scan(&collection)

	source, err := operatorStore.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft: Draft{
			Kind:        "note",
			Body:        "source for blocker test",
			ClaimType:   "self",
			Sensitivity: "shareable",
			Scope:       Scope{Repo: collection, TaskID: "*", RunID: "*"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	sUUID := uuid.MustParse(source.RecordID)

	blockerID := uuid.New()
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_annotation (
			annotation_id, revision, binding_id, plane_item_uuid, kind, body, state, author, created_at, updated_at
		) VALUES (
			$1, 1, $2, $3, 'blocker', 'blocker text', 'open', 'operator:test', clock_timestamp(), clock_timestamp()
		)
	`, blockerID, bindingID, planeItemUUID)
	if err != nil {
		t.Fatal(err)
	}

	outRevs := map[string]any{
		uuid.NewString(): map[string]any{
			"type":          "blocker_created",
			"annotation_id": blockerID.String(),
			"revision":      1,
		},
	}
	outRevsBytes, _ := json.Marshal(outRevs)
	srcIDsBytes, _ := json.Marshal([]uuid.UUID{sUUID})

	receiptUUID := uuid.New()
	_, err = operatorStore.pool.Exec(ctx, `
		INSERT INTO cairn.work_advice_application_receipt (
			receipt_uuid, request_uuid, proposal_uuid, binding_id, plane_item_uuid, source_record_ids,
			applied_action_uuids, applied_by_channel, output_revisions, applied_at, undone, compensating_revisions
		) VALUES (
			$1, $2, $3, $4, $5, $6,
			'[]'::jsonb, 'operator:test', $7, clock_timestamp(), false, '{}'::jsonb
		)
	`, receiptUUID, uuid.New(), uuid.New(), bindingID, planeItemUUID, srcIDsBytes, outRevsBytes)
	if err != nil {
		t.Fatal(err)
	}

	spec := AdviceInputSpec{BindingID: bindingID, PlaneItemUUID: planeItemUUID}
	_, man1, err := operatorStore.BuildWorkAdviceSnapshot(ctx, spec)
	if err != nil {
		t.Fatalf("BuildWorkAdviceSnapshot rev 1 failed: %v", err)
	}
	foundS := false
	for _, id := range man1.DerivedSourceRecordIDs {
		if id == sUUID {
			foundS = true
			break
		}
	}
	if !foundS {
		t.Fatalf("expected sUUID in DerivedSourceRecordIDs for blocker rev 1")
	}

	_, err = operatorStore.pool.Exec(ctx, `
		UPDATE cairn.work_annotation
		SET revision = 2, body = 'manually edited blocker', updated_at = clock_timestamp()
		WHERE annotation_id = $1
	`, blockerID)
	if err != nil {
		t.Fatal(err)
	}

	_, man2, err := operatorStore.BuildWorkAdviceSnapshot(ctx, spec)
	if err != nil {
		t.Fatalf("BuildWorkAdviceSnapshot rev 2 failed: %v", err)
	}
	foundS2 := false
	for _, id := range man2.DerivedSourceRecordIDs {
		if id == sUUID {
			foundS2 = true
			break
		}
	}
	if foundS2 {
		t.Fatalf("edited blocker at revision 2 should NOT have derived source S from receipt matching revision 1")
	}
}
