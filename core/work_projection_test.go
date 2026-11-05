package core

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/plane"
)

func TestAC01_ProjectIdentityAndTupleBinding(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	ws1 := uuid.MustParse("3753821c-0d30-4f2d-955e-1f4972cc5159")
	proj1 := uuid.MustParse("32043788-2217-4bf3-9aca-aba636355c0d")

	ws2 := uuid.MustParse("3753821c-0d30-4f2d-955e-1f4972cc5159")
	proj2 := uuid.MustParse("86ff1524-f0f5-442d-bc7a-57ecdafb6b69")

	// Bind project 1 in repo:test
	p1, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "proj1",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  ws1,
		ProjectUUID:    proj1,
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatalf("AddWorkProject p1 failed: %v", err)
	}

	// Bind project 2 with distinct project UUID
	p2, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "proj2",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  ws2,
		ProjectUUID:    proj2,
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatalf("AddWorkProject p2 failed: %v", err)
	}

	if p1.BindingID == p2.BindingID {
		t.Fatal("two projects with distinct project UUIDs must have distinct binding IDs")
	}

	// Retarget attempt with existing collection & workspace to a bound project under another slug fails
	_, err = s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "proj-hijack",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  ws1,
		ProjectUUID:    proj1,
		ConfigRevision: "rev1",
	})
	if err == nil {
		t.Fatal("expected failure when binding existing external tuple to different local slug")
	}
	requireCode(t, err, "INVALID_REQUEST")

	// STORE-2 / STORE-4: Two distinct collections can use the same local slug
	pOtherColl, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "proj1",
		Collection:     "repo:other",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  ws2,
		ProjectUUID:    proj2,
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatalf("AddWorkProject in other collection with same slug should succeed: %v", err)
	}
	if pOtherColl.BindingID == p1.BindingID {
		t.Fatal("bindings across different collections must be distinct")
	}
}

func TestAC03_ExecutionLinkHistoryIsolation(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	ws := uuid.New()
	proj := uuid.New()

	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "isolation-proj",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  ws,
		ProjectUUID:    proj,
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatal(err)
	}

	itemUUID := uuid.New()
	items := []plane.NormalizedItem{
		{
			PlaneItemUUID: itemUUID,
			Identifier:    "ISO-1",
			Title:         "Execution Isolation Test Item",
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
	}
	refCtx, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.CommitRefreshSnapshot(ctx, refCtx, items, false, nil)
	if err != nil {
		t.Fatal(err)
	}

	// Register an agent session with execution 1
	agentID := uuid.New()
	exec1 := uuid.New()
	_, err = s.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Repo:            "repo:test",
		Binding:         "binding1",
		NativeSessionID: "sess1",
		Metadata: AgentMetadata{
			Harness:     "test",
			Project:     "test",
			TaskSummary: "Execution 1 Task Summary",
			State:       "busy",
		},
	}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}

	// Update session execution_id in DB to match exec1
	_, err = s.pool.Exec(ctx, `UPDATE cairn.agent_session SET execution_id = $1 WHERE native_session_id = 'sess1' RETURNING agent_id`, exec1)
	if err != nil {
		t.Fatal(err)
	}
	_ = s.pool.QueryRow(ctx, `SELECT agent_id FROM cairn.agent_session WHERE native_session_id = 'sess1'`).Scan(&agentID)

	// Associate execution 1 with the item
	target1 := fmt.Sprintf("%s:%s", agentID, exec1)
	assoc1, err := s.AssociateWork(ctx, AssociateWorkRequest{
		RequestID:   uuid.NewString(),
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		TargetType:  "session",
		TargetID:    target1,
		Rationale:   "First execution link",
	})
	if err != nil {
		t.Fatalf("AssociateWork exec1 failed: %v", err)
	}

	// Now session resumes with a new execution 2 and updated task summary
	exec2 := uuid.New()
	_, err = s.pool.Exec(ctx, `UPDATE cairn.agent_session SET execution_id = $1, metadata = jsonb_set(metadata, '{task_summary}', '"Execution 2 New Task Summary"') WHERE agent_id = $2`, exec2, agentID)
	if err != nil {
		t.Fatal(err)
	}

	// Also link execution 2
	target2 := fmt.Sprintf("%s:%s", agentID, exec2)
	assoc2, err := s.AssociateWork(ctx, AssociateWorkRequest{
		RequestID:   uuid.NewString(),
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		TargetType:  "session",
		TargetID:    target2,
		Rationale:   "Second execution link",
	})
	if err != nil {
		t.Fatalf("AssociateWork exec2 failed: %v", err)
	}

	// Read work item detail
	detail, err := s.GetWorkItemDetail(ctx, p.Collection, p.LocalSlug, itemUUID.String())
	if err != nil {
		t.Fatalf("GetWorkItemDetail failed: %v", err)
	}

	if len(detail.Sessions) != 2 {
		t.Fatalf("expected 2 linked sessions, got %d", len(detail.Sessions))
	}

	var foundExec1, foundExec2 bool
	for _, sess := range detail.Sessions {
		if sess.ExecutionID == exec1 {
			foundExec1 = true
			if !sess.IsHistorical {
				t.Fatalf("execution 1 must be marked historical after session execution changed, got IsHistorical=false")
			}
			if sess.TaskSummary != "Execution 1 Task Summary" {
				t.Fatalf("historical execution 1 must retain its old task summary, got %q", sess.TaskSummary)
			}
			if strings.Contains(sess.TaskSummary, "Execution 2") {
				t.Fatalf("historical execution 1 borrowed task summary from execution 2: %q", sess.TaskSummary)
			}
		}
		if sess.ExecutionID == exec2 {
			foundExec2 = true
			if sess.IsHistorical {
				t.Fatalf("current execution 2 must NOT be marked historical, got IsHistorical=true")
			}
			if sess.TaskSummary != "Execution 2 New Task Summary" {
				t.Fatalf("current execution 2 should have latest task summary, got %q", sess.TaskSummary)
			}
		}
	}

	if !foundExec1 || !foundExec2 {
		t.Fatalf("expected both execution 1 and execution 2 in detail sessions: found1=%v, found2=%v", foundExec1, foundExec2)
	}
	_ = assoc1
	_ = assoc2
}

func TestAC04_OperatorPrivilegeSeparation(t *testing.T) {
	ctx := context.Background()

	// Non-operator store (STORE-4)
	agentStore := testStore(t, Channel{Principal: "agent:worker", Operator: false})

	// Operator mutations must refuse
	_, err := agentStore.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "fail",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  uuid.New(),
		ProjectUUID:    uuid.New(),
		ConfigRevision: "rev",
	})
	requireCode(t, err, "AUTHORITY_DENIED")

	_, err = agentStore.DisableWorkProject(ctx, "repo:test", "fail")
	requireCode(t, err, "AUTHORITY_DENIED")

	_, err = agentStore.AssociateWork(ctx, AssociateWorkRequest{
		RequestID:   uuid.NewString(),
		Collection:  "repo:test",
		ProjectSlug: "fail",
		ItemUUID:    uuid.New(),
		TargetType:  "event",
		TargetID:    uuid.NewString(),
	})
	requireCode(t, err, "AUTHORITY_DENIED")

	_, err = agentStore.AddProgressAnnotation(ctx, AddProgressAnnotationRequest{
		RequestID:   uuid.NewString(),
		Collection:  "repo:test",
		ProjectSlug: "fail",
		ItemUUID:    uuid.New(),
		Body:        "progress",
	})
	requireCode(t, err, "AUTHORITY_DENIED")

	_, err = agentStore.AddBlockerAnnotation(ctx, AddBlockerAnnotationRequest{
		RequestID:   uuid.NewString(),
		Collection:  "repo:test",
		ProjectSlug: "fail",
		ItemUUID:    uuid.New(),
		Body:        "blocker",
	})
	requireCode(t, err, "AUTHORITY_DENIED")

	_, err = agentStore.PurgeMetadata(ctx, PurgeMetadataRequest{
		Collection:  "repo:test",
		ProjectSlug: "fail",
		Pattern:     "secret",
	})
	requireCode(t, err, "AUTHORITY_DENIED")

	// STORE-4: Operator reads must also refuse on non-operator channels
	_, err = agentStore.GetWorkProject(ctx, "repo:test", "fail")
	requireCode(t, err, "AUTHORITY_DENIED")

	_, err = agentStore.ListWorkProjects(ctx, "repo:test")
	requireCode(t, err, "AUTHORITY_DENIED")

	_, _, err = agentStore.ListWorkItems(ctx, "repo:test", "fail", "", 10, 0)
	requireCode(t, err, "AUTHORITY_DENIED")

	_, err = agentStore.GetWorkItemDetail(ctx, "repo:test", "fail", "ITEM-1")
	requireCode(t, err, "AUTHORITY_DENIED")
}

func TestAC05_RequestStatusSeparateFromPlane(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	ws := uuid.New()
	proj := uuid.New()

	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "req-proj",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  ws,
		ProjectUUID:    proj,
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatal(err)
	}

	itemUUID := uuid.New()
	items := []plane.NormalizedItem{
		{
			PlaneItemUUID: itemUUID,
			Identifier:    "REQ-1",
			Title:         "Linked Request Test Item",
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
	}
	refCtx, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.CommitRefreshSnapshot(ctx, refCtx, items, false, nil)
	if err != nil {
		t.Fatal(err)
	}

	// Create a real memory record and event
	recID := uuid.New()
	_, err = s.pool.Exec(ctx, `INSERT INTO cairn.memory_record (record_id, repo, origin, kind, current_version, sensitivity)
		VALUES ($1, 'repo:test', 'test', 'note', 1, 'local')`, recID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.pool.Exec(ctx, `INSERT INTO cairn.record_version (record_id, version, author, body)
		VALUES ($1, 1, 'operator:test', 'Real payload content')`, recID)
	if err != nil {
		t.Fatal(err)
	}

	eventUUID := uuid.New()
	_, err = s.pool.Exec(ctx, `INSERT INTO cairn.agent_event (event_id, repo, publisher, kind, record_id, version, sensitivity, destination_type, destination_name)
		VALUES ($1, 'repo:test', 'operator:test', 'request', $2, 1, 'local', 'agent', 'target')`, eventUUID, recID)
	if err != nil {
		t.Fatal(err)
	}

	deliveryUUID := uuid.New()
	_, err = s.pool.Exec(ctx, `INSERT INTO cairn.agent_delivery (delivery_id, event_id, consumer, state)
		VALUES ($1, $2, 'agent:consumer', 'pending')`, deliveryUUID, eventUUID)
	if err != nil {
		t.Fatal(err)
	}

	// Associate request event with item
	_, err = s.AssociateWork(ctx, AssociateWorkRequest{
		RequestID:   uuid.NewString(),
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		TargetType:  "event",
		TargetID:    eventUUID.String(),
		Rationale:   "Linked request event",
	})
	if err != nil {
		t.Fatalf("AssociateWork event failed: %v", err)
	}

	// Verify detail exposes delivery state and payload availability
	detail, err := s.GetWorkItemDetail(ctx, p.Collection, p.LocalSlug, itemUUID.String())
	if err != nil {
		t.Fatal(err)
	}

	if len(detail.Requests) != 1 {
		t.Fatalf("expected 1 linked request, got %d", len(detail.Requests))
	}
	if detail.Requests[0].DeliveryState != "pending" {
		t.Fatalf("expected delivery_state 'pending', got %q", detail.Requests[0].DeliveryState)
	}
	if !detail.Requests[0].PayloadAvailable {
		t.Fatal("expected payload_available true")
	}

	// Update delivery to handled and purge payload
	_, err = s.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET state='handled', completed_at=clock_timestamp() WHERE delivery_id=$1`, deliveryUUID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.pool.Exec(ctx, `UPDATE cairn.record_version SET body='' WHERE record_id=$1`, recID)
	if err != nil {
		t.Fatal(err)
	}

	detail2, err := s.GetWorkItemDetail(ctx, p.Collection, p.LocalSlug, itemUUID.String())
	if err != nil {
		t.Fatal(err)
	}
	if detail2.Requests[0].DeliveryState != "handled" {
		t.Fatalf("expected delivery_state 'handled', got %q", detail2.Requests[0].DeliveryState)
	}
	if detail2.Requests[0].PayloadAvailable {
		t.Fatal("expected payload_available false after body purged")
	}
}

func TestAC06_CacheSwapAndAuthRejectionMasking(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	ws := uuid.New()
	proj := uuid.New()

	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "cache-proj",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  ws,
		ProjectUUID:    proj,
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatal(err)
	}

	item1 := uuid.New()
	item2 := uuid.New()

	itemsGen1 := []plane.NormalizedItem{
		{
			PlaneItemUUID: item1,
			Identifier:    "ITEM-1",
			Title:         "First Title",
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
		{
			PlaneItemUUID: item2,
			Identifier:    "ITEM-2",
			Title:         "Second Title",
			StateName:     "In Progress",
			StateGroup:    "started",
		},
	}

	refCtx1, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	snap1, err := s.CommitRefreshSnapshot(ctx, refCtx1, itemsGen1, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	if snap1.CacheGeneration != 1 || snap1.ItemCount != 2 {
		t.Fatalf("unexpected snapshot gen 1: %+v", snap1)
	}

	// Add an association to item 1
	_, err = s.AssociateWork(ctx, AssociateWorkRequest{
		RequestID:   uuid.NewString(),
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    item1,
		TargetType:  "run_receipt",
		TargetID:    "run-123",
		Rationale:   "Associated with run",
	})
	if err != nil {
		t.Fatal(err)
	}

	// Generation 2 refresh: item 2 is absent in remote project
	itemsGen2 := []plane.NormalizedItem{
		{
			PlaneItemUUID: item1,
			Identifier:    "ITEM-1",
			Title:         "Updated First Title",
			StateName:     "Done",
			StateGroup:    "completed",
		},
	}
	refCtx2, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	snap2, err := s.CommitRefreshSnapshot(ctx, refCtx2, itemsGen2, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	if snap2.CacheGeneration != 2 || snap2.ItemCount != 1 {
		t.Fatalf("unexpected snapshot gen 2: %+v", snap2)
	}

	// Verify item 2 is marked absent (is_absent=true), NOT deleted!
	detail2, err := s.GetWorkItemDetail(ctx, p.Collection, p.LocalSlug, item2.String())
	if err != nil {
		t.Fatal(err)
	}
	if !detail2.Item.IsAbsent {
		t.Fatal("item 2 dropped from remote project should be marked is_absent=true")
	}

	// STORE-5: Auth rejection refresh: external auth failed
	refCtx3, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.CommitRefreshSnapshot(ctx, refCtx3, nil, true, nil)
	if err != plane.ErrAuthRejected {
		t.Fatalf("expected ErrAuthRejected, got %v", err)
	}

	// Check item 1: external fields MUST be completely masked on read (STORE-5), but association intact!
	detail1, err := s.GetWorkItemDetail(ctx, p.Collection, p.LocalSlug, item1.String())
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(detail1.Item.Title, "hidden") {
		t.Fatalf("expected cached title to be masked on auth rejection, got %q", detail1.Item.Title)
	}
	if detail1.Item.Identifier != "[hidden]" {
		t.Fatalf("expected cached identifier to be masked on auth rejection, got %q", detail1.Item.Identifier)
	}
	if len(detail1.Associations) == 0 {
		t.Fatal("associations must NOT be deleted upon external auth rejection")
	}
}

func TestAC07_IdempotencyAndVersionConflictRefusal(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	ws := uuid.New()
	proj := uuid.New()

	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "idemp-proj",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  ws,
		ProjectUUID:    proj,
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatal(err)
	}

	itemUUID := uuid.New()
	items := []plane.NormalizedItem{
		{
			PlaneItemUUID: itemUUID,
			Identifier:    "IDEMP-1",
			Title:         "Idempotency Item",
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
	}
	refCtx, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.CommitRefreshSnapshot(ctx, refCtx, items, false, nil)
	if err != nil {
		t.Fatal(err)
	}

	// 1. Idempotent progress annotation creation
	reqID := uuid.NewString()
	first, err := s.AddProgressAnnotation(ctx, AddProgressAnnotationRequest{
		RequestID:   reqID,
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		Body:        "First attempt progress",
	})
	if err != nil {
		t.Fatalf("first AddProgressAnnotation failed: %v", err)
	}

	// Retry exact same request with same RequestID
	retry, err := s.AddProgressAnnotation(ctx, AddProgressAnnotationRequest{
		RequestID:   reqID,
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		Body:        "First attempt progress",
	})
	if err != nil {
		t.Fatalf("retry AddProgressAnnotation failed: %v", err)
	}
	if retry.AnnotationID != first.AnnotationID {
		t.Fatalf("expected retained annotation ID %s, got %s", first.AnnotationID, retry.AnnotationID)
	}

	// Resubmit same RequestID with different payload -> IDEMPOTENCY_CONFLICT
	_, err = s.AddProgressAnnotation(ctx, AddProgressAnnotationRequest{
		RequestID:   reqID,
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		Body:        "Changed intent payload",
	})
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")

	// 2. Expected revision conflict on edit
	editReqID := uuid.NewString()
	edited, err := s.EditProgressAnnotation(ctx, EditProgressAnnotationRequest{
		RequestID:        editReqID,
		Collection:       p.Collection,
		ProjectSlug:      p.LocalSlug,
		ItemUUID:         itemUUID,
		AnnotationID:     first.AnnotationID,
		Body:             "Edited progress body",
		ExpectedRevision: 1,
	})
	if err != nil {
		t.Fatalf("edit failed: %v", err)
	}
	if edited.Revision != 2 {
		t.Fatalf("expected revision 2 after edit, got %d", edited.Revision)
	}

	// Edit with stale expected revision (1 instead of 2) -> VERSION_CONFLICT
	_, err = s.EditProgressAnnotation(ctx, EditProgressAnnotationRequest{
		RequestID:        uuid.NewString(),
		Collection:       p.Collection,
		ProjectSlug:      p.LocalSlug,
		ItemUUID:         itemUUID,
		AnnotationID:     first.AnnotationID,
		Body:             "Conflicting edit",
		ExpectedRevision: 1,
	})
	requireCode(t, err, "VERSION_CONFLICT")
}

func TestAC09_PurgeExclusionAndRestorePersistence(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	ws := uuid.New()
	proj := uuid.New()

	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "purge-proj",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  ws,
		ProjectUUID:    proj,
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatal(err)
	}

	itemUUID := uuid.New()
	items := []plane.NormalizedItem{
		{
			PlaneItemUUID: itemUUID,
			Identifier:    "PURGE-1",
			Title:         "Secret credential in title: TOKEN_XYZ",
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
	}
	refCtx, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.CommitRefreshSnapshot(ctx, refCtx, items, false, nil)
	if err != nil {
		t.Fatal(err)
	}

	// Add an annotation containing the secret
	_, err = s.AddProgressAnnotation(ctx, AddProgressAnnotationRequest{
		RequestID:   uuid.NewString(),
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		Body:        "Progress update with secret TOKEN_XYZ",
	})
	if err != nil {
		t.Fatal(err)
	}

	// Purge metadata matching TOKEN_XYZ and record exclusion
	purgeRes, err := s.PurgeMetadata(ctx, PurgeMetadataRequest{
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		Pattern:     "TOKEN_XYZ",
		FieldName:   "all",
		Exclude:     true,
	})
	if err != nil {
		t.Fatalf("PurgeMetadata failed: %v", err)
	}
	if purgeRes.PurgedItems == 0 || purgeRes.PurgedAnnotations == 0 {
		t.Fatalf("expected purged rows, got %+v", purgeRes)
	}

	// Verify title is purged
	detail, err := s.GetWorkItemDetail(ctx, p.Collection, p.LocalSlug, itemUUID.String())
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(detail.Item.Title, "TOKEN_XYZ") {
		t.Fatalf("item title still contains purged secret: %s", detail.Item.Title)
	}
	for _, an := range detail.Annotations {
		if strings.Contains(an.Body, "TOKEN_XYZ") {
			t.Fatalf("annotation still contains purged secret: %s", an.Body)
		}
	}

	// Refresh attempting to resurrect TOKEN_XYZ
	resurrectItems := []plane.NormalizedItem{
		{
			PlaneItemUUID: itemUUID,
			Identifier:    "PURGE-1",
			Title:         "Secret credential in title: TOKEN_XYZ",
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
	}
	refCtx2, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.CommitRefreshSnapshot(ctx, refCtx2, resurrectItems, false, nil)
	if err != nil {
		t.Fatal(err)
	}

	// Exclusion must prevent resurrection!
	detailAfterRefresh, err := s.GetWorkItemDetail(ctx, p.Collection, p.LocalSlug, itemUUID.String())
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(detailAfterRefresh.Item.Title, "TOKEN_XYZ") {
		t.Fatalf("exclusion failed: secret was resurrected on refresh: %s", detailAfterRefresh.Item.Title)
	}

	// Execute restore fence and verify exclusion is reapplied (AC-09)
	_, err = s.FenceRestore(ctx, RestoreFenceRequest{
		RequestID: uuid.NewString(),
		Reason:    "testing restore persistence of exclusion",
	})
	if err != nil {
		t.Fatalf("FenceRestore failed: %v", err)
	}

	detailAfterRestore, err := s.GetWorkItemDetail(ctx, p.Collection, p.LocalSlug, itemUUID.String())
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(detailAfterRestore.Item.Title, "TOKEN_XYZ") {
		t.Fatalf("after restore fence, secret was leaked: %s", detailAfterRestore.Item.Title)
	}
}

func TestAC10_StagingImportConcurrencyBarrier(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	ws := uuid.New()
	proj := uuid.New()

	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "barrier-proj",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  ws,
		ProjectUUID:    proj,
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatal(err)
	}

	itemUUID := uuid.New()
	stagedItems := []plane.NormalizedItem{
		{
			PlaneItemUUID: itemUUID,
			Identifier:    "BAR-1",
			Title:         "Confidential Staged Data: LEAK_123",
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
	}

	refCtx, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}

	// Concurrency barrier hook: runs during staging before import transaction commits
	var barrierFired bool
	var barrierLock sync.Mutex
	barrierHook := func() {
		barrierLock.Lock()
		defer barrierLock.Unlock()
		barrierFired = true
		// Concurrently purge and record exclusion for LEAK_123
		_, purgeErr := s.PurgeMetadata(ctx, PurgeMetadataRequest{
			Collection:  p.Collection,
			ProjectSlug: p.LocalSlug,
			Pattern:     "LEAK_123",
			FieldName:   "title",
			Exclude:     true,
		})
		if purgeErr != nil {
			t.Errorf("concurrent purge inside barrier hook failed: %v", purgeErr)
		}
	}

	// Commit staged snapshot with barrier hook
	_, err = s.CommitRefreshSnapshot(ctx, refCtx, stagedItems, false, barrierHook)
	if err != nil {
		t.Fatalf("CommitRefreshSnapshot failed: %v", err)
	}

	if !barrierFired {
		t.Fatal("expected barrier hook to execute")
	}

	// Verify that concurrent purge/exclusion won over the staged data (AC-10)
	detail, err := s.GetWorkItemDetail(ctx, p.Collection, p.LocalSlug, itemUUID.String())
	if err != nil {
		t.Fatal(err)
	}

	if strings.Contains(detail.Item.Title, "LEAK_123") {
		t.Fatalf("concurrency barrier failed: purge did NOT win over stale staged metadata, title is %q", detail.Item.Title)
	}
}

func TestSTORE6_StagedRefreshOrderingAndRestoreFence(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	ws := uuid.New()
	proj := uuid.New()

	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "ordering-proj",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  ws,
		ProjectUUID:    proj,
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatal(err)
	}

	// Staging 1 starts
	refCtx1, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}

	itemUUID := uuid.New()

	// Staging 2 starts and commits first (newer sequence)
	refCtx2, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	items2 := []plane.NormalizedItem{
		{
			PlaneItemUUID: itemUUID,
			Identifier:    "ORD-1",
			Title:         "Generation 2 title",
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
	}
	_, err = s.CommitRefreshSnapshot(ctx, refCtx2, items2, false, nil)
	if err != nil {
		t.Fatal(err)
	}

	// Now staging 1 attempts to commit late (out-of-order arrival)
	items1 := []plane.NormalizedItem{
		{
			PlaneItemUUID: itemUUID,
			Identifier:    "ORD-1",
			Title:         "Stale Generation 1 title",
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
	}
	_, err = s.CommitRefreshSnapshot(ctx, refCtx1, items1, false, nil)
	if err == nil {
		t.Fatal("expected older refresh sequence to be rejected with VERSION_CONFLICT, got nil")
	}
	requireCode(t, err, "VERSION_CONFLICT")

	// Restore fence occurs during fetch:
	refCtx3, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}

	// Trigger restore fence
	_, err = s.FenceRestore(ctx, RestoreFenceRequest{
		RequestID: uuid.NewString(),
		Reason:    "testing restore fencing of in-flight refresh",
	})
	if err != nil {
		t.Fatal(err)
	}

	// Attempting to commit staging that predates restore fence MUST refuse with STALE_RESTORE (STORE-6)
	_, err = s.CommitRefreshSnapshot(ctx, refCtx3, items2, false, nil)
	if err == nil {
		t.Fatal("expected commit predating restore fence to refuse with STALE_RESTORE, got nil")
	}
	requireCode(t, err, "STALE_RESTORE")
}
