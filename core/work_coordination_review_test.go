package core

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/plane"
)

func coordinationReviewItems(t *testing.T, s *Store, ctx context.Context, repo string) (WorkProject, uuid.UUID, uuid.UUID) {
	t.Helper()
	if err := s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "active"}); err != nil {
		t.Fatal(err)
	}
	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		RequestID: uuid.NewString(), Collection: repo, LocalSlug: "coordination-review",
		PlaneInstance: "work", PlaneOrigin: "https://plane.example.com",
		WorkspaceUUID: uuid.New(), ProjectUUID: uuid.New(), ConfigRevision: "review-v1",
	})
	if err != nil {
		t.Fatal(err)
	}
	a, b := uuid.New(), uuid.New()
	refresh, err := s.PrepareRefresh(ctx, repo, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.CommitRefreshSnapshot(ctx, refresh, []plane.NormalizedItem{
		{PlaneItemUUID: a, Identifier: "PANEL-1", Title: "Selected work item"},
		{PlaneItemUUID: b, Identifier: "PANEL-2", Title: "Unrelated work item"},
	}, false, nil); err != nil {
		t.Fatal(err)
	}
	return p, a, b
}

func coordinationReviewAssociate(t *testing.T, s *Store, ctx context.Context, p WorkProject, item uuid.UUID, kind, target string) WorkAssociation {
	t.Helper()
	a, err := s.AssociateWork(ctx, AssociateWorkRequest{
		RequestID: uuid.NewString(), Collection: p.Collection, ProjectSlug: p.LocalSlug,
		ItemUUID: item, TargetType: kind, TargetID: target, Rationale: "PRIVATE_ASSOCIATION_RATIONALE",
	})
	if err != nil {
		t.Fatal(err)
	}
	return a
}

func coordinationReviewNoPayload(t *testing.T, view WorkItemCoordination, forbidden ...string) {
	t.Helper()
	raw, err := json.Marshal(view)
	if err != nil {
		t.Fatal(err)
	}
	forbidden = append(forbidden, "PRIVATE_ASSOCIATION_RATIONALE", `"response_ref"`, `"owner_principal"`, `"profile_binding_id"`, `"native_owner_turn_id"`, `"owner_input_attestation"`)
	for _, value := range forbidden {
		if value != "" && strings.Contains(string(raw), value) {
			t.Errorf("coordination view disclosed forbidden payload/identity %q", value)
		}
	}
}

func TestWorkCoordinationReviewWaitMembershipPrivacyAndRestore(t *testing.T) {
	op, openStore := waitTestDB(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if _, err := op.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatal(err)
	}
	_, _, owner, worker, session, responder, dest := waitFixture(t, openStore)
	p, itemA, itemB := coordinationReviewItems(t, op, ctx, owner.Repo)
	request := publishTestRequest(t, ctx, session, owner.Repo, worker.AgentID, "", dest)
	unrelated := publishTestRequest(t, ctx, session, owner.Repo, worker.AgentID, "", dest)
	groupSource, err := session.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{
		Kind: "note", Body: "PRIVATE_GROUP_SOURCE_BODY", Sensitivity: "shareable", ClaimType: "self",
		Scope: Scope{Repo: owner.Repo, TaskID: "*", RunID: "*"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	group, err := session.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: owner.Repo, Kind: "request",
		Ref:           RecordVersionRef{RecordID: groupSource.RecordID, Version: groupSource.Version},
		Destination:   EventDestination{Type: "agent", Name: "agent/" + worker.AgentID},
		ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(5 * time.Minute), PartialPolicy: "all"},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	createWait := func(event AgentEvent, kind string) AgentWait {
		t.Helper()
		w, err := session.CreateWait(ctx, CreateWaitRequest{
			RequestID: uuid.NewString(), Collection: owner.Repo, PredicateKind: kind,
			DependencyEventUUID: event.EventID, Deadline: time.Now().Add(10 * time.Minute),
		}, dest)
		if err != nil {
			t.Fatal(err)
		}
		return w
	}
	requestWait := createWait(request, WaitPredicateRequestResponse)
	groupWait := createWait(group, WaitPredicateResponseGroupClosed)
	unrelatedWait := createWait(unrelated, WaitPredicateRequestResponse)
	requestAssoc := coordinationReviewAssociate(t, op, ctx, p, itemA, "event", request.EventID)
	groupAssoc := coordinationReviewAssociate(t, op, ctx, p, itemA, "response_group", group.EventID)
	groupEventAssoc := coordinationReviewAssociate(t, op, ctx, p, itemA, "event", group.EventID)
	coordinationReviewAssociate(t, op, ctx, p, itemA, "session", owner.AgentID+":"+owner.ExecutionID)
	coordinationReviewAssociate(t, op, ctx, p, itemB, "event", unrelated.EventID)
	query := WorkCoordinationRequest{Collection: owner.Repo, ProjectSlug: p.LocalSlug, Item: itemA.String(), Limit: 1}
	first, err := op.GetWorkItemCoordination(ctx, query)
	if err != nil || len(first.Waits.Items) != 1 || first.Waits.NextCursor == nil {
		t.Fatalf("first bounded wait page: %+v %v", first, err)
	}
	cursor := *first.Waits.NextCursor
	query.WaitCursor = cursor
	second, err := op.GetWorkItemCoordination(ctx, query)
	if err != nil || len(second.Waits.Items) != 1 || second.Waits.NextCursor != nil {
		t.Fatalf("second bounded wait page: %+v %v", second, err)
	}
	seen := map[string]WorkCoordinationWaitItem{}
	for _, row := range append(first.Waits.Items, second.Waits.Items...) {
		if _, duplicate := seen[row.WaitUUID.String()]; duplicate || row.WaitUUID.String() == unrelatedWait.WaitUUID {
			t.Fatalf("unrelated or duplicate wait imported through selected owner: %+v", row)
		}
		seen[row.WaitUUID.String()] = row
	}
	if len(seen) != 2 || seen[requestWait.WaitUUID].Predicate.Kind != WaitPredicateRequestResponse || seen[groupWait.WaitUUID].Predicate.Kind != WaitPredicateResponseGroupClosed {
		t.Fatalf("explicit dependency membership differs: %+v", seen)
	}
	if refs := seen[requestWait.WaitUUID].Associations; len(refs) != 1 || refs[0].AssociationID != requestAssoc.AssociationID || refs[0].Basis != "explicit_event" {
		t.Fatalf("request provenance: %+v", refs)
	}
	groupRefs := map[uuid.UUID]string{}
	for _, ref := range seen[groupWait.WaitUUID].Associations {
		groupRefs[ref.AssociationID] = ref.Basis
	}
	if len(groupRefs) != 2 || groupRefs[groupAssoc.AssociationID] != "explicit_response_group" || groupRefs[groupEventAssoc.AssociationID] != "explicit_event" {
		t.Fatalf("deduplicated group lost explicit bases: %+v", groupRefs)
	}
	wrongItem := query
	wrongItem.Item = itemB.String()
	_, err = op.GetWorkItemCoordination(ctx, wrongItem)
	requireCode(t, err, "INVALID_REQUEST")
	wrongPanel := query
	wrongPanel.WaitCursor, wrongPanel.NoticeCursor = "", cursor
	_, err = op.GetWorkItemCoordination(ctx, wrongPanel)
	requireCode(t, err, "INVALID_REQUEST")
	_, err = session.GetWorkItemCoordination(ctx, query)
	requireCode(t, err, "AUTHORITY_DENIED")

	response := publishTestResponse(t, ctx, responder, owner.Repo, owner.AgentID, request.EventID, "", "response", dest)
	query.WaitCursor, query.Limit = "", 25
	view, err := op.GetWorkItemCoordination(ctx, query)
	if err != nil {
		t.Fatal(err)
	}
	findWait := func(view WorkItemCoordination, id string) WorkCoordinationWaitItem {
		t.Helper()
		for _, row := range view.Waits.Items {
			if row.WaitUUID.String() == id {
				return row
			}
		}
		t.Fatalf("explicit wait %s missing", id)
		return WorkCoordinationWaitItem{}
	}
	resolved := findWait(view, requestWait.WaitUUID)
	if resolved.State != WaitStateSatisfied || resolved.Notification.OutboxState != OutboxStatePending || resolved.Notification.Delivery != nil || resolved.Notification.EventID != nil {
		t.Fatalf("wait satisfaction invented notification delivery: %+v", resolved)
	}
	if count, err := op.DrainWaitOutbox(ctx, owner.Repo, 10); err != nil || count != 1 {
		t.Fatalf("publish resolved wait outbox: %d %v", count, err)
	}
	view, err = op.GetWorkItemCoordination(ctx, query)
	if err != nil {
		t.Fatal(err)
	}
	published := findWait(view, requestWait.WaitUUID)
	if published.Notification.OutboxState != OutboxStatePublished || published.Notification.EventID == nil || published.Notification.Delivery == nil || published.Notification.Delivery.State != "pending" {
		t.Fatalf("published unclaimed notice presentation: %+v", published)
	}
	root, err := op.Bootstrap(ctx, BootstrapRequest{RequestID: uuid.NewString(), Reason: "Install isolated coordination-view fixture authority"})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := op.PreviewDeletion(ctx, groupSource.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := op.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: groupSource.RecordID, ExpectedVersion: groupSource.Version, GrantID: root.ID, PreviewID: preview.PreviewID}); err != nil {
		t.Fatal(err)
	}
	view, err = op.GetWorkItemCoordination(ctx, query)
	if err != nil {
		t.Fatal(err)
	}
	if row := findWait(view, groupWait.WaitUUID); row.DependencyStatus != "forgotten" || len(row.Associations) != 2 {
		t.Fatalf("forgotten dependency lost opaque provenance or retained availability: %+v", row)
	}
	coordinationReviewNoPayload(t, view, groupSource.RecordID, groupSource.Body, response.Ref.RecordID, requestWait.ReservedSourceRecordUUID)
	if err := op.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "coordination-waits/1", Status: "disabled"}); err != nil {
		t.Fatal(err)
	}
	view, err = op.GetWorkItemCoordination(ctx, query)
	if err != nil || view.Waits.FeatureStatus != "disabled" || len(view.Waits.Items) != 2 {
		t.Fatalf("disabled feature hid retained work history: %+v %v", view, err)
	}
	if _, err := op.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Fence the published unclaimed coordination notice"}); err != nil {
		t.Fatal(err)
	}
	stale := query
	stale.Limit, stale.WaitCursor = 1, cursor
	_, err = op.GetWorkItemCoordination(ctx, stale)
	requireCode(t, err, "STALE_CURSOR")
	view, err = op.GetWorkItemCoordination(ctx, query)
	if err != nil {
		t.Fatal(err)
	}
	fenced := findWait(view, requestWait.WaitUUID)
	if fenced.State != WaitStateSatisfied || fenced.Notification.OutboxState != OutboxStatePublished || !fenced.Notification.RestoreFenced || !fenced.Historical {
		t.Fatalf("published unclaimed wait fence was hidden or rewritten: %+v", fenced)
	}
	if _, err := op.BeginRestore(ctx, BeginRestoreRequest{RequestID: uuid.NewString(), Target: "isolated-review-fixture", Reason: "Verify read admission while recovery is paused"}); err != nil {
		t.Fatal(err)
	}
	_, err = op.GetWorkItemCoordination(ctx, query)
	requireCode(t, err, "RESTORE_PAUSED")
}

func TestWorkCoordinationReviewNoticeExecutionMembershipAndWithdrawal(t *testing.T) {
	isolated, _ := waitTestDB(t)
	// Reuse the existing genuine registration/signature fixture in this test's
	// disposable database. No launch capability or reservation row is fabricated.
	t.Setenv("CAIRN_TEST_DATABASE_URL", isolated.pool.Config().ConnString())
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	f := setupOrganicFixture(t, "repo:notice-view:"+uuid.NewString())
	f.activateCapability(t)
	p, itemA, itemB := coordinationReviewItems(t, f.operator, ctx, f.repo)
	oldSession := f.sessionRef
	oldAssoc := coordinationReviewAssociate(t, f.operator, ctx, p, itemA, "session", oldSession.AgentID+":"+oldSession.ExecutionID)
	admittedEvent := f.publishNotice(t, "PRIVATE_OLD_NOTICE_BODY")
	queuedEvent := f.publishNotice(t, "PRIVATE_QUEUED_NOTICE_BODY")
	coordinationReviewAssociate(t, f.operator, ctx, p, itemB, "event", admittedEvent.EventID)

	// Another real native session on the same launch/profile has its own inbox.
	// Its unassociated queue must affect neither this item's rows nor aggregates.
	other := *f
	other.nativeSessID = "unrelated-native-session"
	otherReg, err := other.recipient.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Binding: f.binding, NativeSessionID: other.nativeSessID,
		LaunchID: f.launch.LaunchID, Metadata: AgentMetadata{Harness: "codex", Project: "review", Workspace: "/review", State: "idle", DeliveryMode: "existing-session"},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	other.sessionRef = AgentSessionRef{AgentID: otherReg.AgentID, ExecutionID: otherReg.ExecutionID}
	other.activateCapability(t)
	unrelatedEvent := other.publishNotice(t, "PRIVATE_UNRELATED_NOTICE_BODY")
	query := WorkCoordinationRequest{Collection: f.repo, ProjectSlug: p.LocalSlug, Item: itemA.String(), Limit: 1}
	page, err := f.operator.GetWorkItemCoordination(ctx, query)
	if err != nil || len(page.QuietNotices.Items) != 1 || page.QuietNotices.QueuedCount != 2 || page.QuietNotices.NextCursor == nil {
		t.Fatalf("current conversation page/counts: %+v %v", page, err)
	}
	noticeCursor := *page.QuietNotices.NextCursor
	if len(page.QuietNotices.Recipients) != 1 || page.QuietNotices.Recipients[0].Support != "supported" || page.QuietNotices.Recipients[0].Historical {
		t.Fatalf("verified current recipient support: %+v", page.QuietNotices.Recipients)
	}
	query.NoticeCursor = noticeCursor
	next, err := f.operator.GetWorkItemCoordination(ctx, query)
	if err != nil || len(next.QuietNotices.Items) != 1 || next.QuietNotices.NextCursor != nil || next.QuietNotices.QueuedCount != 2 {
		t.Fatalf("notice aggregate changed with page position: %+v %v", next, err)
	}
	seen := map[string]bool{}
	for _, row := range append(page.QuietNotices.Items, next.QuietNotices.Items...) {
		if seen[row.EventID.String()] || row.EventID.String() == unrelatedEvent.EventID || row.Delivery != nil || row.State != "queued" {
			t.Fatalf("queued read imported unrelated work or materialized delivery: %+v", row)
		}
		seen[row.EventID.String()] = true
		if len(row.Associations) != 1 || row.Associations[0].AssociationID != oldAssoc.AssociationID || row.Associations[0].Basis != "linked_recipient_conversation" {
			t.Fatalf("conversation queue mislabeled as task-specific: %+v", row.Associations)
		}
	}
	if !seen[admittedEvent.EventID] || !seen[queuedEvent.EventID] {
		t.Fatalf("current conversation notices missing: %+v", seen)
	}
	wrongItem := query
	wrongItem.Item = itemB.String()
	_, err = f.operator.GetWorkItemCoordination(ctx, wrongItem)
	requireCode(t, err, "INVALID_REQUEST")
	query.NoticeCursor, query.Limit = "", 25
	attestation, payload := f.signAttestation(t, "PRIVATE_NATIVE_TURN", "owner_composer", "fresh_start", uuid.NewString(), time.Now().UnixMilli())
	claim, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID: uuid.NewString(), Session: oldSession, NativeOwnerTurnID: payload.NativeTurnID, OwnerInputAttestation: &attestation,
	}, f.dest)
	if err != nil || claim.Attempt == nil || claim.Attempt.Delivery.Event.EventID != admittedEvent.EventID {
		t.Fatalf("real signed admission: %+v %v", claim, err)
	}
	if _, err := f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: claim.Attempt.Delivery.DeliveryID,
		LeaseID: claim.Attempt.Delivery.LeaseID, Disposition: "handled",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	if _, err := f.recipient.ReconcileSessionInbox(ctx, SessionInboxReconcile{
		RequestID: uuid.NewString(), Session: oldSession, AttemptID: claim.Attempt.ID, Reason: "delivery_completed",
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	// Resume the same conversation with a new operator-admitted launch. The old
	// work association remains pinned to execution A, never to its successor B.
	f.launch, err = f.operator.RegisterNativeOwnerLaunch(ctx, RegisterNativeOwnerLaunchRequest{
		RequestID: uuid.NewString(), LaunchID: uuid.NewString(), Repo: f.repo,
		ProfilePrincipal: f.recipient.channel.Principal, Binding: f.binding,
		PublicKey: base64.RawURLEncoding.EncodeToString(f.pubKey), AttestationContract: "cairn.owner-input-attestation/1",
		OriginContract: "codex.owner-input/1", OriginVersion: 1,
		UpstreamCommit: "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856", PatchSHA256: strings.Repeat("a", 64), RuntimeSHA256: strings.Repeat("b", 64),
	})
	if err != nil {
		t.Fatal(err)
	}
	resumed, err := f.recipient.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Binding: f.binding, NativeSessionID: f.nativeSessID,
		LaunchID: f.launch.LaunchID, Metadata: AgentMetadata{Harness: "codex", Project: "review", Workspace: "/review", State: "idle", DeliveryMode: "existing-session", TaskSummary: "PRIVATE_SUCCESSOR_TASK"},
	}, f.dest)
	if err != nil || resumed.AgentID != oldSession.AgentID || resumed.ExecutionID == oldSession.ExecutionID {
		t.Fatalf("real conversation resume: %+v %v", resumed, err)
	}
	f.sessionRef = AgentSessionRef{AgentID: resumed.AgentID, ExecutionID: resumed.ExecutionID}
	newAssoc := coordinationReviewAssociate(t, f.operator, ctx, p, itemB, "session", resumed.AgentID+":"+resumed.ExecutionID)
	queryB := query
	queryB.Item = itemB.String()
	pending, err := f.operator.GetWorkItemCoordination(ctx, queryB)
	if err != nil || len(pending.QuietNotices.Recipients) != 1 || pending.QuietNotices.Recipients[0].Support != "unsupported" {
		t.Fatalf("pending registration advertised support before signed activation: %+v %v", pending, err)
	}
	f.activateCapability(t)
	successorEvent := f.publishNotice(t, "PRIVATE_SUCCESSOR_NOTICE_BODY")
	oldView, err := f.operator.GetWorkItemCoordination(ctx, query)
	if err != nil || len(oldView.QuietNotices.Items) != 1 || oldView.QuietNotices.QueuedCount != 0 || len(oldView.QuietNotices.Recipients) != 1 {
		t.Fatalf("old execution inherited successor conversation backlog: %+v %v", oldView, err)
	}
	oldRecipient := oldView.QuietNotices.Recipients[0]
	if !oldRecipient.Historical || oldRecipient.Online || oldRecipient.Support != "historical" || oldRecipient.ExecutionID.String() != oldSession.ExecutionID {
		t.Fatalf("historical association borrowed successor presence/support: %+v", oldRecipient)
	}
	oldRow := oldView.QuietNotices.Items[0]
	if oldRow.EventID.String() != admittedEvent.EventID || !oldRow.Historical || oldRow.AdmittedExecutionID == nil || oldRow.AdmittedExecutionID.String() != oldSession.ExecutionID ||
		len(oldRow.Associations) != 1 || oldRow.Associations[0].Basis != "admitted_execution" || oldRow.Delivery == nil || oldRow.Delivery.State != "handled" {
		t.Fatalf("old admission attribution/delivery differs: %+v", oldRow)
	}
	newView, err := f.operator.GetWorkItemCoordination(ctx, queryB)
	if err != nil || newView.QuietNotices.QueuedCount != 2 || len(newView.QuietNotices.Items) != 3 || newView.QuietNotices.Recipients[0].AssociationID != newAssoc.AssociationID || newView.QuietNotices.Recipients[0].Support != "supported" {
		t.Fatalf("explicit successor association did not enable its own conversation scope: %+v %v", newView, err)
	}
	explicit := coordinationReviewAssociate(t, f.operator, ctx, p, itemA, "event", queuedEvent.EventID)
	root, err := f.operator.Bootstrap(ctx, BootstrapRequest{RequestID: uuid.NewString(), Reason: "Install isolated notice-view withdrawal authority"})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := f.operator.PreviewDeletion(ctx, queuedEvent.Ref.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.operator.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: queuedEvent.Ref.RecordID, ExpectedVersion: queuedEvent.Ref.Version, GrantID: root.ID, PreviewID: preview.PreviewID}); err != nil {
		t.Fatal(err)
	}
	withdrawn, err := f.operator.GetWorkItemCoordination(ctx, query)
	if err != nil || len(withdrawn.QuietNotices.Items) != 2 {
		t.Fatalf("explicit forgotten event lost opaque membership: %+v %v", withdrawn, err)
	}
	found := false
	for _, row := range withdrawn.QuietNotices.Items {
		if row.EventID.String() == queuedEvent.EventID {
			found = true
			if row.SourceStatus != "forgotten" || len(row.Associations) != 1 || row.Associations[0].AssociationID != explicit.AssociationID || row.Associations[0].Basis != "explicit_event" || row.AdmittedExecutionID != nil || row.Delivery != nil {
				t.Errorf("explicit withdrawn queue item gained execution/payload: %+v", row)
			}
		}
		if row.EventID.String() == successorEvent.EventID || row.EventID.String() == unrelatedEvent.EventID {
			t.Errorf("unrelated successor/session notice leaked into historical item: %+v", row)
		}
	}
	if !found {
		t.Fatal("explicit withdrawn notice missing")
	}
	coordinationReviewNoPayload(t, withdrawn, queuedEvent.Ref.RecordID, "PRIVATE_QUEUED_NOTICE_BODY", "PRIVATE_NATIVE_TURN", "PRIVATE_SUCCESSOR_TASK", attestation.Payload, attestation.Signature)
	if _, err := f.operator.UnassociateWork(ctx, UnassociateWorkRequest{
		RequestID: uuid.NewString(), Collection: f.repo, ProjectSlug: p.LocalSlug,
		ItemUUID: itemA, AssociationID: explicit.AssociationID, ExpectedRevision: &explicit.Revision,
	}); err != nil {
		t.Fatal(err)
	}
	afterRemoval, err := f.operator.GetWorkItemCoordination(ctx, query)
	if err != nil || len(afterRemoval.QuietNotices.Items) != 1 || afterRemoval.QuietNotices.QueuedCount != 0 {
		t.Fatalf("removed explicit edge retained successor queue membership/counts: %+v %v", afterRemoval, err)
	}
	if _, err := f.operator.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Fence notice-view cursors and recipient capabilities"}); err != nil {
		t.Fatal(err)
	}
	query.Limit, query.NoticeCursor = 1, noticeCursor
	_, err = f.operator.GetWorkItemCoordination(ctx, query)
	requireCode(t, err, "STALE_CURSOR")
}
