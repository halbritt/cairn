package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/advice"
)

func TestWorkAdviceReviewForgetReopenedBlockerPreservesManualReplacement(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	sentinel := "REOPENED_BLOCKER_A_" + uuid.NewString()
	f := newAdviceReviewCase(t, ctx, restoreTestStore(t), sentinel)
	_, oldProgress := applyCapturedReview(t, ctx, f, capturedReviewGeneration{f.job, f.proposal, f.actions[0]}, "progress", sentinel)
	manualText := "Independent operator replacement " + uuid.NewString()
	manual, err := f.store.EditProgressAnnotation(ctx, EditProgressAnnotationRequest{RequestID: uuid.NewString(), Collection: f.collection,
		ProjectSlug: f.project.LocalSlug, ItemUUID: f.item, AnnotationID: oldProgress.AnnotationID, ExpectedRevision: oldProgress.Revision, Body: manualText})
	if err != nil || manual.AnnotationID != oldProgress.AnnotationID || manual.Revision <= oldProgress.Revision || manual.Body != manualText {
		t.Fatalf("public edit did not replace the exact A-derived progress body independently: %+v %v", manual, err)
	}
	sourceB, checkControl := newCapturedReviewControl(t, ctx, f)
	createBytes, err := json.Marshal(map[string]any{"actions": []any{map[string]any{
		"action_type": advice.ActionAnnotateBlocker, "blocker_action": "create", "text": sentinel,
	}}})
	if err != nil {
		t.Fatal(err)
	}
	creator := &capturedReviewAdviceProvider{workflowParsedAdviceProvider: &workflowParsedAdviceProvider{result: string(createBytes)}}
	a := generateCapturedReview(t, ctx, f, f.source, creator)
	assertCapturedReviewSources(t, ctx, f, a, f.source, creator)
	creation, blocker := applyCapturedReview(t, ctx, f, a, "blocker", sentinel)
	if len(blocker.SourceReferences) != 0 {
		t.Fatal("blocker fixture unexpectedly has explicit output citations")
	}
	if err := f.store.SetWorkAdvicePolicy(ctx, f.project.BindingID, []string{"title", "sources", "blockers"}); err != nil {
		t.Fatal(err)
	}
	resolveBytes, err := json.Marshal(map[string]any{"actions": []any{map[string]any{
		"action_type": advice.ActionAnnotateBlocker, "blocker_action": "resolve", "existing_blocker_id": blocker.AnnotationID.String(),
		"expected_revision": blocker.Revision,
	}}})
	if err != nil {
		t.Fatal(err)
	}
	resolver := &capturedReviewAdviceProvider{workflowParsedAdviceProvider: &workflowParsedAdviceProvider{result: string(resolveBytes)}}
	resolution := generateCapturedReview(t, ctx, f, sourceB, resolver)
	assertCapturedReviewSources(t, ctx, f, resolution, sourceB, resolver)
	resolved, err := f.store.ApplyAdviceProposal(ctx, &WorkAdviceApplicationReceipt{RequestUUID: uuid.New(), ProposalUUID: resolution.proposal.ProposalUUID},
		resolution.proposal.ReviewVersion, []uuid.UUID{resolution.action.ActionUUID})
	if err != nil || resolved == nil {
		t.Fatalf("real generated blocker resolution failed: %v", err)
	}
	type revisionStamp struct {
		Type          string    `json:"type"`
		AnnotationID  uuid.UUID `json:"annotation_id"`
		Revision      int64     `json:"revision"`
		PriorRevision int64     `json:"prior_revision"`
	}
	stamp := func(raw json.RawMessage, actionID uuid.UUID) revisionStamp {
		t.Helper()
		var entries map[string]revisionStamp
		if err := json.Unmarshal(raw, &entries); err != nil {
			t.Fatal(err)
		}
		value, ok := entries[actionID.String()]
		if !ok || len(entries) != 1 {
			t.Fatal("receipt does not identify its one exact selected action")
		}
		return value
	}
	createdStamp := stamp(creation.OutputRevisions, a.action.ActionUUID)
	resolvedStamp := stamp(resolved.OutputRevisions, resolution.action.ActionUUID)
	if createdStamp.Type != "blocker_created" || createdStamp.AnnotationID != blocker.AnnotationID || createdStamp.Revision != blocker.Revision ||
		resolvedStamp.Type != "blocker_resolved" || resolvedStamp.AnnotationID != blocker.AnnotationID ||
		resolvedStamp.PriorRevision != blocker.Revision || resolvedStamp.Revision != blocker.Revision+1 {
		t.Fatal("creation/resolution receipt does not bind the exact blocker revision transition")
	}
	if body := capturedReviewRow(t, ctx, f.store, `SELECT body FROM cairn.work_annotation WHERE annotation_id=$1 AND revision=$2 AND state='resolved'`, blocker.AnnotationID, resolvedStamp.Revision); body != sentinel {
		t.Fatal("real resolution did not retain the original body at its recorded successor revision")
	}
	if err := f.store.UndoAdviceApplication(ctx, resolved.RequestUUID); err != nil {
		t.Fatal(err)
	}
	undone, err := f.store.GetAdviceReceipt(ctx, resolved.RequestUUID)
	if err != nil || undone == nil || !undone.Undone || undone.ReceiptUUID != resolved.ReceiptUUID || undone.RequestUUID != resolved.RequestUUID || undone.ProposalUUID != resolution.proposal.ProposalUUID {
		t.Fatalf("Undo did not retain the exact original application identity: %+v %v", undone, err)
	}
	reopenedStamp := stamp(undone.CompensatingRevisions, resolution.action.ActionUUID)
	if reopenedStamp.Type != "blocker_reopened" || reopenedStamp.AnnotationID != blocker.AnnotationID || reopenedStamp.Revision != resolvedStamp.Revision+1 {
		t.Fatal("Undo omitted the exact newly reopened blocker revision")
	}
	if body := capturedReviewRow(t, ctx, f.store, `SELECT body FROM cairn.work_annotation WHERE annotation_id=$1 AND revision=$2 AND state='open'`, blocker.AnnotationID, reopenedStamp.Revision); body != sentinel {
		t.Fatal("actual reopened revision did not retain A-derived body")
	}
	stillCreated, err := f.store.GetAdviceReceipt(ctx, creation.RequestUUID)
	if err != nil || stillCreated == nil || stillCreated.Undone || stillCreated.ReceiptUUID != creation.ReceiptUUID ||
		stillCreated.RequestUUID != creation.RequestUUID || stillCreated.ProposalUUID != a.proposal.ProposalUUID {
		t.Fatalf("resolving and reopening rewrote the original creation receipt: %v", err)
	}
	if stamp(stillCreated.OutputRevisions, a.action.ActionUUID) != createdStamp {
		t.Fatal("resolving and reopening changed the original creation revision stamp")
	}
	provider := &capturedReviewAdviceProvider{workflowParsedAdviceProvider: &workflowParsedAdviceProvider{}, copyBlocker: blocker.AnnotationID}
	b := generateCapturedReview(t, ctx, f, sourceB, provider)
	assertCapturedReviewSources(t, ctx, f, b, sourceB, provider)
	var input advice.Snapshot
	if err := json.Unmarshal(provider.snapshot, &input); err != nil {
		t.Fatal(err)
	}
	matched := false
	for _, supplied := range input.ExistingBlockers {
		if supplied.BlockerID == blocker.AnnotationID.String() {
			matched = supplied.Revision == reopenedStamp.Revision && supplied.Text == sentinel
		}
	}
	if !matched || !strings.Contains(string(b.action.TargetPayload), sentinel) {
		t.Fatal("B did not actually receive and reproduce the exact reopened blocker revision")
	}
	receipt, applied := applyCapturedReview(t, ctx, f, b, "progress", sentinel)
	const manualCurrent = `SELECT to_jsonb(a)::text FROM cairn.work_annotation a WHERE annotation_id=$1 AND state='superseded' AND body=$2`
	const history = `SELECT to_jsonb(h)::text FROM cairn.work_annotation_history h WHERE annotation_id=$1 AND revision=$2`
	manualBefore := capturedReviewRow(t, ctx, f.store, manualCurrent, manual.AnnotationID, manualText)
	manualHistoryBefore := capturedReviewRow(t, ctx, f.store, history, manual.AnnotationID, manual.Revision)
	for _, revision := range []int64{blocker.Revision, resolvedStamp.Revision, reopenedStamp.Revision} {
		if !strings.Contains(capturedReviewRow(t, ctx, f.store, history, blocker.AnnotationID, revision), sentinel) {
			t.Fatal("real blocker transition history missing before Forget")
		}
	}
	forgetCapturedReviewSource(t, ctx, f)
	assertCapturedReviewScrub(t, ctx, f, b, receipt, applied, sentinel)
	assertCapturedReviewSources(t, ctx, f, b, sourceB, provider)
	for _, revision := range []int64{blocker.Revision, resolvedStamp.Revision, reopenedStamp.Revision} {
		if strings.Contains(capturedReviewRow(t, ctx, f.store, history, blocker.AnnotationID, revision), sentinel) {
			t.Errorf("blocker history revision %d retained forgotten A text", revision)
		}
	}
	if capturedReviewRow(t, ctx, f.store, manualCurrent, manual.AnnotationID, manualText) != manualBefore ||
		capturedReviewRow(t, ctx, f.store, history, manual.AnnotationID, manual.Revision) != manualHistoryBefore {
		t.Error("Forget erased or changed the independent manual replacement of an A-derived annotation")
	}
	checkControl()
}
