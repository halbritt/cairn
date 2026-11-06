package core

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/plane"
)

func assertNoForbiddenCoordinationFields(t *testing.T, v WorkItemCoordination, sentinels ...string) {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal WorkItemCoordination: %v", err)
	}
	var top map[string]any
	if err := json.Unmarshal(raw, &top); err != nil {
		t.Fatalf("unmarshal WorkItemCoordination: %v", err)
	}
	expectedTop := map[string]bool{
		"schema": true, "collection": true, "binding_id": true, "plane_item_uuid": true,
		"observed_at": true, "restore_generation": true, "waits": true, "quiet_notices": true,
	}
	if len(top) != len(expectedTop) {
		t.Fatalf("unexpected top-level field count %d: %v", len(top), top)
	}
	for k := range top {
		if !expectedTop[k] {
			t.Fatalf("unexpected top-level field %q", k)
		}
	}
	forbidden := map[string]bool{
		"response_ref": true, "reserved_source_record_uuid": true, "owner_principal": true,
		"profile_binding_id": true, "source_body": true, "body": true, "title": true,
		"payload": true, "response_text": true, "owner_input_attestation": true, "native_turn_id": true,
	}
	var walk func(any)
	walk = func(node any) {
		switch x := node.(type) {
		case map[string]any:
			for k, val := range x {
				if forbidden[k] {
					t.Fatalf("forbidden field %q exposed in coordination DTO", k)
				}
				walk(val)
			}
		case []any:
			for _, item := range x {
				walk(item)
			}
		}
	}
	walk(top)
	rawStr := string(raw)
	for _, s := range sentinels {
		if s != "" && containsSubstr(rawStr, s) {
			t.Fatalf("sentinel %q leaked in coordination DTO: %s", s, rawStr)
		}
	}
}

func containsSubstr(s, sub string) bool {
	return len(sub) > 0 && len(s) >= len(sub) && (s == sub || jsonContains(s, sub))
}

func jsonContains(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}

func setupCoordWorkItems(t *testing.T, op *Store, repo, slug string) (WorkProject, uuid.UUID, uuid.UUID) {
	t.Helper()
	ctx := context.Background()
	for _, feat := range []string{"coordination-waits/1", "next-turn-notices/1"} {
		if err := op.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
			RequestID: uuid.NewString(), FeatureName: feat, Status: "active",
		}); err != nil {
			t.Fatalf("enable %s: %v", feat, err)
		}
	}
	proj, err := op.AddWorkProject(ctx, AddWorkProjectRequest{
		RequestID: uuid.NewString(), Collection: repo, LocalSlug: slug,
		PlaneInstance: "work", PlaneOrigin: "https://plane.example.com",
		WorkspaceUUID: uuid.New(), ProjectUUID: uuid.New(), ConfigRevision: "rev-1",
	})
	if err != nil {
		t.Fatalf("AddWorkProject: %v", err)
	}
	itemA, itemB := uuid.New(), uuid.New()
	ref, err := op.PrepareRefresh(ctx, repo, slug)
	if err != nil {
		t.Fatalf("PrepareRefresh: %v", err)
	}
	if _, err := op.CommitRefreshSnapshot(ctx, ref, []plane.NormalizedItem{
		{PlaneItemUUID: itemA, Identifier: "COORD-1", Title: "Coordination item A"},
		{PlaneItemUUID: itemB, Identifier: "COORD-2", Title: "Coordination item B"},
	}, false, nil); err != nil {
		t.Fatalf("CommitRefreshSnapshot: %v", err)
	}
	return proj, itemA, itemB
}

func TestWorkItemCoordination_ExactMembershipDeduplicationAndPagination(t *testing.T) {
	op, openStore := waitTestDB(t)
	ctx := context.Background()
	_, _, agentA, agentB, sessA, _, dest := waitFixture(t, openStore)
	repo := sessA.channel.Repo
	proj, itemA, itemB := setupCoordWorkItems(t, op, repo, "coord-membership")

	// Non-operator and cross-repo calls must refuse.
	if _, err := sessA.GetWorkItemCoordination(ctx, WorkCoordinationRequest{Collection: repo, ProjectSlug: proj.LocalSlug, Item: itemA.String()}); Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("expected AUTHORITY_DENIED for non-operator, got %v", err)
	}
	otherOp := openStore(Channel{Principal: "operator:scoped", Operator: true, Repo: "repo:other"})
	if _, err := otherOp.GetWorkItemCoordination(ctx, WorkCoordinationRequest{Collection: repo, ProjectSlug: proj.LocalSlug, Item: itemA.String()}); err == nil {
		t.Fatal("expected cross-repo operator refusal")
	}

	sentinel := "PRIVATE_COORD_SOURCE_SENTINEL_991"
	src, err := sessA.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft:     Draft{Kind: "note", Body: sentinel, ClaimType: "self", Sensitivity: "local", Scope: Scope{Repo: repo, TaskID: "*", RunID: "*"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().UTC().Add(15 * time.Minute)
	var linkedWaits []string
	for i := 0; i < 4; i++ {
		pred := WaitPredicateRequestResponse
		var rg *ResponseGroupSpec
		if i == 0 {
			pred = WaitPredicateResponseGroupClosed
			rg = &ResponseGroupSpec{Deadline: deadline, PartialPolicy: "all"}
		}
		ev, err := sessA.PublishEvent(ctx, PublishEventRequest{
			RequestID: uuid.NewString(), Repo: repo, Kind: "request",
			Ref:           RecordVersionRef{RecordID: src.RecordID, Version: src.Version},
			Destination:   EventDestination{Type: "agent", Name: agentB.Inbox},
			CorrelationID: uuid.NewString(), ResponseGroup: rg,
		}, dest)
		if err != nil {
			t.Fatal(err)
		}
		w, err := sessA.CreateWait(ctx, CreateWaitRequest{
			RequestID: uuid.NewString(), Collection: repo, PredicateKind: pred, DependencyEventUUID: ev.EventID, Deadline: deadline,
		}, dest)
		if err != nil {
			t.Fatal(err)
		}
		if i == 3 {
			continue // Unrelated wait owned by same session A in same collection.
		}
		linkedWaits = append(linkedWaits, w.WaitUUID)
		if i == 0 {
			if _, err := op.AssociateWork(ctx, AssociateWorkRequest{
				Collection: repo, ProjectSlug: proj.LocalSlug, ItemUUID: itemA,
				TargetType: "response_group", TargetID: ev.EventID,
			}); err != nil {
				t.Fatal(err)
			}
		}
		if _, err := op.AssociateWork(ctx, AssociateWorkRequest{
			Collection: repo, ProjectSlug: proj.LocalSlug, ItemUUID: itemA,
			TargetType: "event", TargetID: ev.EventID,
		}); err != nil {
			t.Fatal(err)
		}
	}

	// Associate session A to itemA: session association alone MUST NOT import unrelated wait!
	if _, err := op.AssociateWork(ctx, AssociateWorkRequest{
		Collection: repo, ProjectSlug: proj.LocalSlug, ItemUUID: itemA,
		TargetType: "session", TargetID: agentA.AgentID + ":" + agentA.ExecutionID,
	}); err != nil {
		t.Fatal(err)
	}

	seen := make(map[string]WorkCoordinationWaitItem)
	cursor := ""
	pages := 0
	for {
		page, err := op.GetWorkItemCoordination(ctx, WorkCoordinationRequest{
			Collection: repo, ProjectSlug: proj.LocalSlug, Item: itemA.String(), Limit: 2, WaitCursor: cursor,
		})
		if err != nil {
			t.Fatalf("GetWorkItemCoordination page %d: %v", pages, err)
		}
		assertNoForbiddenCoordinationFields(t, page, sentinel)
		pages++
		for _, w := range page.Waits.Items {
			if _, dup := seen[w.WaitUUID.String()]; dup {
				t.Fatalf("duplicate wait %s across pages", w.WaitUUID)
			}
			seen[w.WaitUUID.String()] = w
		}
		if page.Waits.NextCursor == nil {
			break
		}
		// Cursor from itemA must refuse on itemB and under notice_cursor.
		if _, err := op.GetWorkItemCoordination(ctx, WorkCoordinationRequest{
			Collection: repo, ProjectSlug: proj.LocalSlug, Item: itemB.String(), Limit: 2, WaitCursor: *page.Waits.NextCursor,
		}); Code(err) != "INVALID_REQUEST" {
			t.Fatalf("expected INVALID_REQUEST for cross-item cursor, got %v", err)
		}
		if _, err := op.GetWorkItemCoordination(ctx, WorkCoordinationRequest{
			Collection: repo, ProjectSlug: proj.LocalSlug, Item: itemA.String(), Limit: 2, NoticeCursor: *page.Waits.NextCursor,
		}); Code(err) != "INVALID_REQUEST" {
			t.Fatalf("expected INVALID_REQUEST for cross-panel cursor, got %v", err)
		}
		cursor = *page.Waits.NextCursor
	}
	if pages != 2 || len(seen) != 3 {
		t.Fatalf("expected 2 pages and 3 exact linked waits, got pages=%d len=%d", pages, len(seen))
	}
	firstWait, ok := seen[linkedWaits[0]]
	if !ok || len(firstWait.Associations) != 2 ||
		firstWait.Associations[0].Basis != "explicit_response_group" ||
		firstWait.Associations[1].Basis != "explicit_event" {
		t.Fatalf("expected deduplicated group+event provenance on first wait, got %+v", firstWait.Associations)
	}
}

func TestWorkItemCoordination_QuietNoticesExecutionIsolationAndPrivacy(t *testing.T) {
	_, openStore := waitTestDB(t)
	repo := "repo:coord-notices:" + uuid.NewString()
	f := setupOrganicFixtureWithStores(t, repo, openStore)
	ctx := context.Background()
	proj, itemA, _ := setupCoordWorkItems(t, f.operator, repo, "coord-notices")
	f.activateCapability(t)

	// Publish notice 1 and admit it to execution A via signed owner turn.
	evAdmitted := f.publishNotice(t, "NOTICE_BODY_ADMITTED_SECRET")
	att1, p1 := f.signAttestation(t, "turn-admit-1", "owner_composer", "fresh_start", uuid.NewString(), time.Now().UnixMilli())
	claimRes, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID: uuid.NewString(), Session: f.sessionRef, NativeOwnerTurnID: p1.NativeTurnID, OwnerInputAttestation: &att1,
	}, f.dest)
	if err != nil || claimRes.Decision != "admitted" {
		t.Fatalf("ClaimOrganicNotice failed: %v %+v", err, claimRes)
	}

	// Publish notice 2 and associate its event explicitly to itemA.
	evExplicit := f.publishNotice(t, "NOTICE_BODY_EXPLICIT_SECRET")
	if _, err := f.operator.AssociateWork(ctx, AssociateWorkRequest{
		Collection: repo, ProjectSlug: proj.LocalSlug, ItemUUID: itemA,
		TargetType: "event", TargetID: evExplicit.EventID,
	}); err != nil {
		t.Fatal(err)
	}

	// Associate execution A to itemA.
	assocA, err := f.operator.AssociateWork(ctx, AssociateWorkRequest{
		Collection: repo, ProjectSlug: proj.LocalSlug, ItemUUID: itemA,
		TargetType: "session", TargetID: f.sessionRef.AgentID + ":" + f.sessionRef.ExecutionID,
	})
	if err != nil {
		t.Fatal(err)
	}

	// Initial read with current supported execution A: both notices visible, recipient supported.
	v1, err := f.operator.GetWorkItemCoordination(ctx, WorkCoordinationRequest{
		Collection: repo, ProjectSlug: proj.LocalSlug, Item: itemA.String(),
	})
	if err != nil {
		t.Fatalf("GetWorkItemCoordination v1: %v", err)
	}
	assertNoForbiddenCoordinationFields(t, v1, "NOTICE_BODY_ADMITTED_SECRET", "NOTICE_BODY_EXPLICIT_SECRET")
	if len(v1.QuietNotices.Recipients) != 1 || v1.QuietNotices.Recipients[0].Support != "supported" || v1.QuietNotices.Recipients[0].Historical {
		t.Fatalf("expected current supported recipient A, got %+v", v1.QuietNotices.Recipients)
	}
	if v1.QuietNotices.QueuedCount != 1 || len(v1.QuietNotices.Items) != 2 {
		t.Fatalf("expected 1 queued and 2 total notices on v1, got %+v", v1.QuietNotices)
	}

	// Settle held attempt on execution A before resuming the conversation as execution B.
	if _, err := f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  claimRes.Attempt.Delivery.DeliveryID,
		LeaseID:     claimRes.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	if _, err := f.recipient.ReconcileSessionInbox(ctx, SessionInboxReconcile{
		RequestID: uuid.NewString(),
		Session:   f.sessionRef,
		AttemptID: claimRes.Attempt.ID,
		Reason:    "delivery_completed",
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	// Resume same conversation (agent_id) as execution B (with active capability so B can receive queued notices).
	pubKeyB, privKeyB, _ := ed25519.GenerateKey(nil)
	launchB, err := f.operator.RegisterNativeOwnerLaunch(ctx, RegisterNativeOwnerLaunchRequest{
		RequestID: uuid.NewString(), LaunchID: uuid.NewString(), Repo: repo,
		ProfilePrincipal: "consumer-" + repo, Binding: f.binding,
		PublicKey:           base64.RawURLEncoding.EncodeToString(pubKeyB),
		AttestationContract: "cairn.owner-input-attestation/1", OriginContract: "codex.owner-input/1", OriginVersion: 1,
		UpstreamCommit: "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856",
		PatchSHA256:    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
		RuntimeSHA256:  "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
	})
	if err != nil {
		t.Fatal(err)
	}
	regB, err := f.recipient.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID: uuid.NewString(), Repo: repo, Binding: f.binding,
		NativeSessionID: f.nativeSessID, LaunchID: launchB.LaunchID,
		Metadata: AgentMetadata{Harness: "codex", Project: "test-proj", Workspace: "/home/test", State: "idle", DeliveryMode: "existing-session"},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	attBInit := signAttestation(t, privKeyB, OwnerInputAttestationPayload{
		IssuedAtUnixMS: time.Now().UnixMilli(), LaunchID: launchB.LaunchID, RestoreGeneration: launchB.RestoreGeneration,
		NativeSessionID: f.nativeSessID, NativeTurnID: "turn-b-init", OriginContract: "codex.owner-input/1", OriginVersion: 1,
		Source: "unknown", Admission: "fresh_start",
	})
	if _, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID: uuid.NewString(), Session: AgentSessionRef{AgentID: regB.AgentID, ExecutionID: regB.ExecutionID},
		NativeOwnerTurnID: "turn-b-init", OwnerInputAttestation: &attBInit,
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	// Publish a brand-new queued notice for B's conversation (NOT explicitly associated to itemA).
	evSuccessorOnly := f.publishNotice(t, "SUCCESSOR_QUEUE_SECRET")

	// Read itemA (linked only to old execution A + explicit evExplicit):
	// A MUST be historical (online=false, support="historical") and MUST NOT import evSuccessorOnly!
	v2, err := f.operator.GetWorkItemCoordination(ctx, WorkCoordinationRequest{
		Collection: repo, ProjectSlug: proj.LocalSlug, Item: itemA.String(),
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(v2.QuietNotices.Recipients) != 1 || !v2.QuietNotices.Recipients[0].Historical ||
		v2.QuietNotices.Recipients[0].Online || v2.QuietNotices.Recipients[0].Support != "historical" {
		t.Fatalf("expected historical recipient A without successor leak, got %+v", v2.QuietNotices.Recipients)
	}
	for _, it := range v2.QuietNotices.Items {
		if it.EventID.String() == evSuccessorOnly.EventID {
			t.Fatalf("old execution A association leaked successor queued notice %s", evSuccessorOnly.EventID)
		}
	}
	if v2.QuietNotices.QueuedCount != 1 {
		t.Fatalf("expected QueuedCount=1 (only explicit evExplicit, NOT successor queue), got %d", v2.QuietNotices.QueuedCount)
	}
	_ = assocA
	_ = evAdmitted

	// Explicitly associating execution B brings B's conversation backlog into scope.
	assocB, err := f.operator.AssociateWork(ctx, AssociateWorkRequest{
		Collection: repo, ProjectSlug: proj.LocalSlug, ItemUUID: itemA,
		TargetType: "session", TargetID: regB.AgentID + ":" + regB.ExecutionID,
	})
	if err != nil {
		t.Fatal(err)
	}
	v3, err := f.operator.GetWorkItemCoordination(ctx, WorkCoordinationRequest{
		Collection: repo, ProjectSlug: proj.LocalSlug, Item: itemA.String(),
	})
	if err != nil {
		t.Fatal(err)
	}
	if v3.QuietNotices.QueuedCount != 2 || len(v3.QuietNotices.Items) != 3 {
		t.Fatalf("expected 2 queued and 3 total notices once B is linked, got %+v", v3.QuietNotices)
	}

	// Unassociating B immediately removes evSuccessorOnly from items and QueuedCount.
	if _, err := f.operator.UnassociateWork(ctx, UnassociateWorkRequest{
		Collection: repo, ProjectSlug: proj.LocalSlug, ItemUUID: itemA,
		AssociationID: assocB.AssociationID, ExpectedRevision: &assocB.Revision,
	}); err != nil {
		t.Fatal(err)
	}
	v4, err := f.operator.GetWorkItemCoordination(ctx, WorkCoordinationRequest{
		Collection: repo, ProjectSlug: proj.LocalSlug, Item: itemA.String(),
	})
	if err != nil {
		t.Fatal(err)
	}
	if v4.QuietNotices.QueuedCount != 1 || len(v4.QuietNotices.Items) != 2 {
		t.Fatalf("expected successor notice removed after UnassociateWork, got %+v", v4.QuietNotices)
	}
}

func TestWorkItemCoordination_RestorePauseFenceSourcePrivacyAndDisabledReads(t *testing.T) {
	op, openStore := waitTestDB(t)
	ctx := context.Background()
	_, _, agentA, agentB, sessA, sessB, dest := waitFixture(t, openStore)
	repo := sessA.channel.Repo
	proj, itemA, _ := setupCoordWorkItems(t, op, repo, "coord-restore")

	// Create shareable source, publish request, create wait, satisfy via response, drain outbox (published, unclaimed!).
	src, err := sessA.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft:     Draft{Kind: "note", Body: "WAIT_DEP_SECRET_BODY", ClaimType: "self", Sensitivity: "shareable", Scope: Scope{Repo: repo, TaskID: "*", RunID: "*"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	corrID := uuid.NewString()
	reqEv, err := sessA.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: repo, Kind: "request",
		Ref:           RecordVersionRef{RecordID: src.RecordID, Version: src.Version},
		Destination:   EventDestination{Type: "agent", Name: agentB.Inbox},
		CorrelationID: corrID,
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	w, err := sessA.CreateWait(ctx, CreateWaitRequest{
		RequestID: uuid.NewString(), Collection: repo, PredicateKind: WaitPredicateRequestResponse,
		DependencyEventUUID: reqEv.EventID, Deadline: time.Now().UTC().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	// Create 2nd wait so limit=1 produces a valid generation-0 cursor.
	reqEv2, err := sessA.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: repo, Kind: "request",
		Ref:           RecordVersionRef{RecordID: src.RecordID, Version: src.Version},
		Destination:   EventDestination{Type: "agent", Name: agentB.Inbox},
		CorrelationID: uuid.NewString(),
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	w2, err := sessA.CreateWait(ctx, CreateWaitRequest{
		RequestID: uuid.NewString(), Collection: repo, PredicateKind: WaitPredicateRequestResponse,
		DependencyEventUUID: reqEv2.EventID, Deadline: time.Now().UTC().Add(10 * time.Minute),
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	for _, idStr := range []string{reqEv.EventID, reqEv2.EventID} {
		if _, err := op.AssociateWork(ctx, AssociateWorkRequest{
			Collection: repo, ProjectSlug: proj.LocalSlug, ItemUUID: itemA,
			TargetType: "event", TargetID: idStr,
		}); err != nil {
			t.Fatal(err)
		}
	}

	// Satisfy first wait via response and drain outbox so notification is published (unclaimed).
	respSrc, err := sessB.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft:     Draft{Kind: "note", Body: "RESP_SECRET_BODY", ClaimType: "self", Sensitivity: "shareable", Scope: Scope{Repo: repo, TaskID: "*", RunID: "*"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := sessB.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: repo, Kind: "response",
		Ref:         RecordVersionRef{RecordID: respSrc.RecordID, Version: respSrc.Version},
		Destination: EventDestination{Type: "agent", Name: agentA.Inbox},
		CausationID: reqEv.EventID, CorrelationID: corrID,
	}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err := op.DrainWaitOutbox(ctx, repo, 10); err != nil {
		t.Fatal(err)
	}

	preFence, err := op.GetWorkItemCoordination(ctx, WorkCoordinationRequest{
		Collection: repo, ProjectSlug: proj.LocalSlug, Item: itemA.String(), Limit: 1,
	})
	if err != nil || preFence.Waits.NextCursor == nil {
		t.Fatalf("preFence read failed: %v %+v", err, preFence)
	}
	gen0Cursor := *preFence.Waits.NextCursor

	// Forget source -> dependency_status becomes forgotten; no body leaked.
	root, err := op.Bootstrap(ctx, BootstrapRequest{RequestID: uuid.NewString(), Reason: "Install synthetic test operator root"})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := op.PreviewDeletion(ctx, src.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := op.Forget(ctx, ForgetRequest{
		RequestID: uuid.NewString(), RecordID: src.RecordID, ExpectedVersion: src.Version,
		GrantID: root.ID, PreviewID: preview.PreviewID,
	}); err != nil {
		t.Fatal(err)
	}

	// FenceRestore advances generation and fences published unclaimed wait notification (and waiting w2!).
	_ = w2
	if _, err := op.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "test restore fence"}); err != nil {
		t.Fatal(err)
	}
	if _, err := op.GetWorkItemCoordination(ctx, WorkCoordinationRequest{
		Collection: repo, ProjectSlug: proj.LocalSlug, Item: itemA.String(), Limit: 1, WaitCursor: gen0Cursor,
	}); Code(err) != "STALE_CURSOR" {
		t.Fatalf("expected STALE_CURSOR after restore fence, got %v", err)
	}

	// Disable features -> retained reads still succeed and report disabled status + fenced notification + forgotten dependency.
	if err := op.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "coordination-waits/1", Status: "disabled"}); err != nil {
		t.Fatal(err)
	}
	if err := op.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "next-turn-notices/1", Status: "disabled"}); err != nil {
		t.Fatal(err)
	}

	postFence, err := op.GetWorkItemCoordination(ctx, WorkCoordinationRequest{
		Collection: repo, ProjectSlug: proj.LocalSlug, Item: itemA.String(),
	})
	if err != nil {
		t.Fatal(err)
	}
	assertNoForbiddenCoordinationFields(t, postFence, "WAIT_DEP_SECRET_BODY", "RESP_SECRET_BODY")
	if postFence.Waits.FeatureStatus != "disabled" || postFence.QuietNotices.FeatureStatus != "disabled" {
		t.Fatalf("expected disabled feature statuses, got waits=%q notices=%q", postFence.Waits.FeatureStatus, postFence.QuietNotices.FeatureStatus)
	}
	foundW := false
	for _, item := range postFence.Waits.Items {
		if item.WaitUUID.String() == w.WaitUUID {
			foundW = true
			if item.DependencyStatus != "forgotten" || !item.Notification.RestoreFenced || item.Notification.OutboxState != "published" {
				t.Fatalf("expected forgotten dependency + fenced published notification, got %+v", item)
			}
		}
	}
	if !foundW {
		t.Fatalf("expected satisfied wait %s in post-fence view", w.WaitUUID)
	}
}
