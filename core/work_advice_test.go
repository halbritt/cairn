package core

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/advice"
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
	delay time.Duration
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

	before := time.Now().UTC()
	prop, _, err := operatorStore.ExecuteAdviceJob(ctx, job, spec, slowProv)
	if err != nil {
		t.Fatalf("ExecuteAdviceJob failed: %v", err)
	}

	// Proposal expiry must be anchored to snapshot capture time (+1h), NOT completion (+1h)
	expectedMaxExpiry := before.Add(1*time.Hour + 80*time.Millisecond)
	if prop.ExpiresAt.After(expectedMaxExpiry) {
		t.Fatalf("proposal.ExpiresAt (%v) was not anchored to snapshot capture (exceeds %v)", prop.ExpiresAt, expectedMaxExpiry)
	}
	if !prop.ExpiresAt.After(before.Add(59 * time.Minute)) {
		t.Fatalf("proposal.ExpiresAt (%v) is unexpectedly before snapshot capture + 59m", prop.ExpiresAt)
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

