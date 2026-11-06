package core

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

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
	reg1, err := s.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Repo:            p.Collection,
		Binding:         "binding1",
		NativeSessionID: "sess1",
		Metadata: AgentMetadata{
			Harness:      "test",
			Project:      "test",
			Workspace:    "/workspace/test",
			DeliveryMode: "existing-session",
			TaskSummary:  "Execution 1 Task Summary",
			State:        "busy",
		},
	}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}

	agentID, err := uuid.Parse(reg1.AgentID)
	if err != nil {
		t.Fatal(err)
	}
	exec1, err := uuid.Parse(reg1.ExecutionID)
	if err != nil {
		t.Fatal(err)
	}

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

	// Now session resumes with a new execution 2 and updated task summary via RegisterAgent
	reg2, err := s.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Repo:            p.Collection,
		Binding:         "binding1",
		NativeSessionID: "sess1",
		Metadata: AgentMetadata{
			Harness:      "test",
			Project:      "test",
			Workspace:    "/workspace/test",
			DeliveryMode: "existing-session",
			TaskSummary:  "Execution 2 New Task Summary",
			State:        "busy",
		},
	}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	exec2, err := uuid.Parse(reg2.ExecutionID)
	if err != nil {
		t.Fatal(err)
	}
	if exec2 == exec1 {
		t.Fatal("resumed execution ID should differ from execution 1")
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

	_, err = agentStore.DisableWorkProject(ctx, "repo:test", "fail", uuid.NewString())
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
	s := restoreTestStore(t)
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

	// Create a real memory record and event using Store public APIs
	d := note()
	d.Scope.Repo = p.Collection
	d.Body = "Real payload content"
	rec, err := s.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft:     d,
	})
	if err != nil {
		t.Fatal(err)
	}

	dest := Destination{Name: "local", AllowLocal: true}
	ev, err := s.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Repo:        p.Collection,
		Kind:        "request",
		Ref:         RecordVersionRef{RecordID: rec.RecordID, Version: rec.Version},
		Destination: EventDestination{Type: "agent", Name: "agent:consumer"},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}

	eventUUID, err := uuid.Parse(ev.EventID)
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

	// Deliver and complete event using consumer store on the same database
	consumerStore := &Store{pool: s.pool, channel: Channel{Principal: "agent:consumer", Repo: p.Collection}, compatibility: DefaultCompatibilityPolicy()}
	nextRes, err := consumerStore.NextEvent(ctx, NextEventRequest{Repo: p.Collection}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if nextRes.Delivery == nil {
		t.Fatal("expected delivery")
	}
	_, err = consumerStore.CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  nextRes.Delivery.DeliveryID,
		LeaseID:     nextRes.Delivery.LeaseID,
		Disposition: "handled",
	}, dest)
	if err != nil {
		t.Fatal(err)
	}

	// Forget and purge record payload using Store APIs
	root, err := s.Bootstrap(ctx, BootstrapRequest{RequestID: uuid.NewString(), Reason: "Bootstrap root authority for purge"})
	if err != nil {
		t.Fatal(err)
	}
	prev, err := s.PreviewDeletion(ctx, rec.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	del, err := s.Forget(ctx, ForgetRequest{
		RequestID:       uuid.NewString(),
		RecordID:        rec.RecordID,
		ExpectedVersion: rec.Version,
		GrantID:         root.ID,
		PreviewID:       prev.PreviewID,
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.PurgeDeletion(ctx, del.DeletionID)
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

	// Add a real retrieval receipt association to item 1
	pkg, err := s.Compile(ctx, CompileRequest{
		RequestID:       uuid.NewString(),
		Scope:           Scope{Repo: p.Collection, TaskID: "t-cache", RunID: "r-cache"},
		Purpose:         "context",
		AvailableTokens: 64000,
	}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.AssociateWork(ctx, AssociateWorkRequest{
		RequestID:   uuid.NewString(),
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    item1,
		TargetType:  "run_receipt",
		TargetID:    pkg.ReceiptID,
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
	if _, err := s.pool.Exec(ctx, `UPDATE cairn.work_project SET next_refresh_allowed_at = NULL WHERE binding_id = $1`, p.BindingID); err != nil {
		t.Fatal(err)
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
	if _, err := s.pool.Exec(ctx, `UPDATE cairn.work_project SET next_refresh_allowed_at = NULL WHERE binding_id = $1`, p.BindingID); err != nil {
		t.Fatal(err)
	}
	refCtx3, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.CommitRefreshSnapshot(ctx, refCtx3, nil, true, nil)
	requireCode(t, err, "AUTH_REJECTED")

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
		RequestID:   uuid.NewString(),
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
	if _, err := s.pool.Exec(ctx, `UPDATE cairn.work_project SET next_refresh_allowed_at = NULL WHERE binding_id = $1`, p.BindingID); err != nil {
		t.Fatal(err)
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

	// Non-empty project requirement: seed initial items and annotations
	initialItemUUID := uuid.New()
	initialItems := []plane.NormalizedItem{
		{
			PlaneItemUUID: initialItemUUID,
			Identifier:    "EXIST-1",
			Title:         "Existing Normal Task",
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
	}
	refCtx0, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.CommitRefreshSnapshot(ctx, refCtx0, initialItems, false, nil); err != nil {
		t.Fatal(err)
	}
	_, err = s.AddProgressAnnotation(ctx, AddProgressAnnotationRequest{
		RequestID:   uuid.NewString(),
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    initialItemUUID,
		Body:        "Initial progress before staged refresh",
	})
	if err != nil {
		t.Fatal(err)
	}

	// Prepare staged refresh with confidential data
	confidentialItemUUID := uuid.New()
	stagedItems := []plane.NormalizedItem{
		{
			PlaneItemUUID: initialItemUUID,
			Identifier:    "EXIST-1",
			Title:         "Existing Normal Task",
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
		{
			PlaneItemUUID: confidentialItemUUID,
			Identifier:    "BAR-1",
			Title:         "Confidential Staged Data: LEAK_123",
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
	}

	_, _ = s.pool.Exec(ctx, "UPDATE cairn.work_project SET next_refresh_allowed_at = NULL WHERE binding_id = $1", p.BindingID)
	refCtx, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}

	// Concurrency barrier: separate connection/goroutine executes purge before commit acquires lock
	s2 := testStore(t, Channel{Principal: "operator:test", Operator: true})
	var barrierFired bool
	barrierHook := func() {
		barrierFired = true
		_, purgeErr := s2.PurgeMetadata(ctx, PurgeMetadataRequest{
			RequestID:   uuid.NewString(),
			Collection:  p.Collection,
			ProjectSlug: p.LocalSlug,
			Pattern:     "LEAK_123",
			FieldName:   "title",
			Exclude:     true,
		})
		if purgeErr != nil {
			t.Errorf("concurrent purge on s2 failed: %v", purgeErr)
		}
	}

	// Commit staged snapshot with barrier hook: must be rejected with VERSION_CONFLICT
	_, err = s.CommitRefreshSnapshot(ctx, refCtx, stagedItems, false, barrierHook)
	if err == nil {
		t.Fatal("expected CommitRefreshSnapshot to fail with VERSION_CONFLICT due to concurrent purge/exclusion")
	}
	requireCode(t, err, "VERSION_CONFLICT")

	if !barrierFired {
		t.Fatal("expected barrier hook to execute")
	}

	// Verify that confidential item LEAK_123 was NOT inserted into project
	items, count, err := s.ListWorkItems(ctx, p.Collection, p.LocalSlug, "", 10, 0)
	if err != nil {
		t.Fatal(err)
	}
	if count != 1 {
		t.Fatalf("expected 1 item in project after rejected refresh, got %d", count)
	}
	for _, it := range items {
		if strings.Contains(it.Title, "LEAK_123") {
			t.Fatalf("confidential staged data leaked into work items: %s", it.Title)
		}
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
	_, _ = s.pool.Exec(ctx, "UPDATE cairn.work_project SET refresh_inflight_expires_at = clock_timestamp() - interval '1 second', next_refresh_allowed_at = NULL WHERE binding_id = $1", p.BindingID)
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
	if _, err := s.pool.Exec(ctx, `UPDATE cairn.work_project SET next_refresh_allowed_at = NULL WHERE binding_id = $1`, p.BindingID); err != nil {
		t.Fatal(err)
	}
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

func TestRELEASE02_CompatibilityPolicyAndAdmission(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	// 1. Ensure work-view/1 feature is active and add an active work project
	err := s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		RequestID:   uuid.NewString(),
		FeatureName: "work-view/1",
		Status:      "active",
	})
	if err != nil {
		t.Fatalf("SetWorkFeatureStatus active failed: %v", err)
	}

	wsUUID := uuid.New()
	projUUID := uuid.New()
	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "compat-proj",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  wsUUID,
		ProjectUUID:    projUUID,
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatalf("AddWorkProject failed: %v", err)
	}

	// 2. Lower-capability store that retains current schema ceiling and omits only work-view/1 support
	lowPolicy := DefaultCompatibilityPolicy()
	lowPolicy.SupportedWorkFeatures["work-view/1"] = false

	// Attempting to open Store against active work-view/1 state MUST fail with SCHEMA_MISMATCH
	dsn := s.pool.Config().ConnString()
	lowStore, err := OpenWithCompatibility(ctx, dsn, Channel{Principal: "operator:low", Operator: true}, lowPolicy)
	if err == nil {
		lowStore.Close()
		t.Fatal("expected OpenWithCompatibility to fail with SCHEMA_MISMATCH on active work-view/1, got nil")
	}
	requireCode(t, err, "SCHEMA_MISMATCH")

	// 3. Disable the active work project and set work-view/1 status to "disabled"
	_, err = s.DisableWorkProject(ctx, "repo:test", p.LocalSlug, uuid.NewString())
	if err != nil {
		t.Fatalf("DisableWorkProject failed: %v", err)
	}
	err = s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		RequestID:   uuid.NewString(),
		FeatureName: "work-view/1",
		Status:      "disabled",
	})
	if err != nil {
		t.Fatalf("SetWorkFeatureStatus disabled failed: %v", err)
	}

	// 4. Now lower-capability store opening MUST succeed for compatible maintenance
	_, err = s.pool.Exec(ctx, "UPDATE cairn.work_project SET status = 'disabled' WHERE status = 'active'")
	if err != nil {
		t.Fatalf("failed to disable active projects: %v", err)
	}
	lowStore, err = OpenWithCompatibility(ctx, dsn, Channel{Principal: "operator:low", Operator: true}, lowPolicy)
	if err != nil {
		t.Fatalf("expected OpenWithCompatibility to succeed when work-view/1 is disabled, got %v", err)
	}
	lowStore.Close()

	// Separate old-schema ceiling check: schema version above MaxMigrationVersion still refuses with SCHEMA_MISMATCH
	if defaultMaxMigrationVersion() > 75 {
		oldSchemaPolicy := DefaultCompatibilityPolicy()
		oldSchemaPolicy.MaxMigrationVersion = 75
		oldStore, err := OpenWithCompatibility(ctx, dsn, Channel{Principal: "operator:old-schema", Operator: true}, oldSchemaPolicy)
		if err == nil {
			oldStore.Close()
			t.Fatal("expected OpenWithCompatibility to fail with SCHEMA_MISMATCH when schema exceeds MaxMigrationVersion 75")
		}
		requireCode(t, err, "SCHEMA_MISMATCH")
	}

	// 5. Test ListWorkFeatures enrichment: pending count and unsafe downgrade reason
	// Reactivate project and feature
	err = s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "active"})
	if err != nil {
		t.Fatalf("SetWorkFeatureStatus active failed: %v", err)
	}
	p2, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "active-for-enrich",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  uuid.New(),
		ProjectUUID:    uuid.New(),
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatalf("AddWorkProject p2 failed: %v", err)
	}
	features, err := s.ListWorkFeatures(ctx)
	if err != nil {
		t.Fatalf("ListWorkFeatures failed: %v", err)
	}
	var wvFeature *WorkFeature
	for i := range features {
		if features[i].FeatureName == "work-view/1" {
			wvFeature = &features[i]
			break
		}
	}
	if wvFeature == nil {
		t.Fatal("work-view/1 feature not found in ListWorkFeatures")
	}
	if wvFeature.PendingCount < 1 {
		t.Fatalf("expected PendingCount >= 1, got %d", wvFeature.PendingCount)
	}
	if !strings.Contains(wvFeature.UnsafeDowngradeReason, "active work project(s)") {
		t.Fatalf("expected UnsafeDowngradeReason to mention active work projects, got %q", wvFeature.UnsafeDowngradeReason)
	}
	_, _ = s.DisableWorkProject(ctx, "repo:test", p2.LocalSlug, uuid.NewString())
}

func TestRELEASE02_AdmissionVsReadableSeparation(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	// Ensure work-view/1 is active
	err := s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "active"})
	if err != nil {
		t.Fatalf("SetWorkFeatureStatus active failed: %v", err)
	}

	itemUUID := uuid.New()
	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "admit-guard-proj",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  uuid.New(),
		ProjectUUID:    uuid.New(),
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatalf("AddWorkProject failed: %v", err)
	}

	// Commit an initial item
	refCtx, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatalf("PrepareRefresh failed: %v", err)
	}
	_, err = s.CommitRefreshSnapshot(ctx, refCtx, []plane.NormalizedItem{
		{
			PlaneItemUUID: itemUUID,
			Identifier:    "ADMIT-1",
			Title:         "Initial Item",
			StateName:     "Todo",
			StateGroup:    "unstarted",
		},
	}, false, nil)
	if err != nil {
		t.Fatalf("CommitRefreshSnapshot failed: %v", err)
	}

	// Add an initial progress annotation and blocker annotation while active
	prog, err := s.AddProgressAnnotation(ctx, AddProgressAnnotationRequest{
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		Body:        "Active progress",
	})
	if err != nil {
		t.Fatalf("AddProgressAnnotation failed: %v", err)
	}
	blocker, err := s.AddBlockerAnnotation(ctx, AddBlockerAnnotationRequest{
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		Body:        "Active blocker",
	})
	if err != nil {
		t.Fatalf("AddBlockerAnnotation failed: %v", err)
	}

	// NOW: Set feature to "disabled"
	err = s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "disabled"})
	if err != nil {
		t.Fatalf("SetWorkFeatureStatus disabled failed: %v", err)
	}

	// 1. All new-admission operations MUST fail with FEATURE_DISABLED
	_, err = s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "should-fail",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  uuid.New(),
		ProjectUUID:    uuid.New(),
		ConfigRevision: "rev1",
	})
	requireCode(t, err, "FEATURE_DISABLED")

	_, err = s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	requireCode(t, err, "FEATURE_DISABLED")

	_, err = s.AddProgressAnnotation(ctx, AddProgressAnnotationRequest{
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		Body:        "Blocked progress",
	})
	requireCode(t, err, "FEATURE_DISABLED")

	_, err = s.EditProgressAnnotation(ctx, EditProgressAnnotationRequest{
		Collection:       p.Collection,
		ProjectSlug:      p.LocalSlug,
		ItemUUID:         itemUUID,
		ExpectedRevision: 1,
		AnnotationID:     prog.AnnotationID,
		Body:             "Edited progress should fail",
	})
	requireCode(t, err, "FEATURE_DISABLED")

	_, err = s.AddBlockerAnnotation(ctx, AddBlockerAnnotationRequest{
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		Body:        "New blocker should fail",
	})
	requireCode(t, err, "FEATURE_DISABLED")

	_, err = s.ResolveBlockerAnnotation(ctx, ResolveBlockerAnnotationRequest{
		Collection:       p.Collection,
		ProjectSlug:      p.LocalSlug,
		ItemUUID:         itemUUID,
		ExpectedRevision: 1,
		AnnotationID:     blocker.AnnotationID,
	})
	requireCode(t, err, "FEATURE_DISABLED")

	_, err = s.AssociateWork(ctx, AssociateWorkRequest{
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		TargetType:  "session",
		TargetID:    uuid.NewString(),
	})
	requireCode(t, err, "FEATURE_DISABLED")

	// 2. Inspection, purge, and disable MUST CONTINUE TO SUCCEED
	gotP, err := s.GetWorkProject(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatalf("GetWorkProject failed during disabled state: %v", err)
	}
	if gotP.BindingID != p.BindingID {
		t.Fatalf("unexpected binding ID %s", gotP.BindingID)
	}

	projs, err := s.ListWorkProjects(ctx, p.Collection)
	if err != nil {
		t.Fatalf("ListWorkProjects failed during disabled state: %v", err)
	}
	if len(projs) == 0 {
		t.Fatal("expected at least 1 project in ListWorkProjects")
	}

	items, total, err := s.ListWorkItems(ctx, p.Collection, p.LocalSlug, "", 50, 0)
	if err != nil {
		t.Fatalf("ListWorkItems failed during disabled state: %v", err)
	}
	if total < 1 || len(items) < 1 {
		t.Fatalf("expected items in ListWorkItems during disabled state")
	}

	detail, err := s.GetWorkItemDetail(ctx, p.Collection, p.LocalSlug, itemUUID.String())
	if err != nil {
		t.Fatalf("GetWorkItemDetail failed during disabled state: %v", err)
	}
	if detail.Item.PlaneItemUUID != itemUUID {
		t.Fatalf("detail returned wrong item: %+v", detail)
	}

	// Purge metadata must continue to succeed
	_, err = s.PurgeMetadata(ctx, PurgeMetadataRequest{
		RequestID:   uuid.NewString(),
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		Pattern:     "Initial",
	})
	if err != nil {
		t.Fatalf("PurgeMetadata failed during disabled state: %v", err)
	}

	// Disable work project must continue to succeed
	disP, err := s.DisableWorkProject(ctx, p.Collection, p.LocalSlug, uuid.NewString())
	if err != nil {
		t.Fatalf("DisableWorkProject failed during disabled state: %v", err)
	}
	if disP.Status != "disabled" {
		t.Fatalf("expected status=disabled, got %s", disP.Status)
	}
}

func TestRELEASE02_DisableWorkProjectRequestIDIdempotency(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	err := s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "active"})
	if err != nil {
		t.Fatalf("SetWorkFeatureStatus active failed: %v", err)
	}

	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "idempotent-disable",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  uuid.New(),
		ProjectUUID:    uuid.New(),
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatalf("AddWorkProject failed: %v", err)
	}

	reqID := uuid.NewString()

	// First call to DisableWorkProject with explicit requestID
	d1, err := s.DisableWorkProject(ctx, p.Collection, p.LocalSlug, reqID)
	if err != nil {
		t.Fatalf("first DisableWorkProject failed: %v", err)
	}
	if d1.Status != "disabled" {
		t.Fatalf("expected disabled, got %s", d1.Status)
	}

	// Retry call to DisableWorkProject with identical requestID MUST return idempotent committed result
	d2, err := s.DisableWorkProject(ctx, p.Collection, p.LocalSlug, reqID)
	if err != nil {
		t.Fatalf("retry DisableWorkProject failed: %v", err)
	}
	if d2.BindingID != d1.BindingID || d2.Status != d1.Status {
		t.Fatalf("idempotent replay mismatch: d1=%+v, d2=%+v", d1, d2)
	}
}

func TestRELEASE02_DisableWorkProjectRequestIDValidation(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	err := s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "active"})
	if err != nil {
		t.Fatalf("SetWorkFeatureStatus active failed: %v", err)
	}

	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "validate-disable-req",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  uuid.New(),
		ProjectUUID:    uuid.New(),
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatalf("AddWorkProject failed: %v", err)
	}

	// Empty requestID must be rejected
	_, err = s.DisableWorkProject(ctx, p.Collection, p.LocalSlug, "")
	requireCode(t, err, "INVALID_REQUEST")

	// Malformed requestID must be rejected
	_, err = s.DisableWorkProject(ctx, p.Collection, p.LocalSlug, "not-a-uuid")
	requireCode(t, err, "INVALID_REQUEST")
}

func TestRELEASE02_SetWorkFeatureStatusGuards(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	// Empty FeatureName rejected
	err := s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "", Status: "active"})
	requireCode(t, err, "INVALID_REQUEST")

	// Unknown FeatureName rejected
	err = s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "unknown/1", Status: "active"})
	requireCode(t, err, "INVALID_REQUEST")

	// Verify cairn.work_feature has no unknown row
	var count int
	_ = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.work_feature WHERE feature_name = 'unknown/1'`).Scan(&count)
	if count != 0 {
		t.Fatalf("expected 0 rows for unknown feature, got %d", count)
	}

	// Retiring with active projects (PendingCount > 0) rejected
	err = s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "active"})
	if err != nil {
		t.Fatalf("SetWorkFeatureStatus active failed: %v", err)
	}

	_, err = s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "retire-pending-proj",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  uuid.New(),
		ProjectUUID:    uuid.New(),
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatalf("AddWorkProject failed: %v", err)
	}

	// Retiring must fail with INVALID_REQUEST because active project exists (negative test)
	err = s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "retired"})
	requireCode(t, err, "INVALID_REQUEST")

	// Positive test: disable projects -> settle/flush -> retire succeeds
	_, err = s.pool.Exec(ctx, "UPDATE cairn.work_project SET status = 'disabled' WHERE status = 'active'")
	if err != nil {
		t.Fatalf("failed to disable active projects: %v", err)
	}
	err = s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "disabled"})
	if err != nil {
		t.Fatalf("SetWorkFeatureStatus disabled failed: %v", err)
	}
	err = s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "retired"})
	if err != nil {
		t.Fatalf("expected SetWorkFeatureStatus retired to succeed when disabled and settled, got %v", err)
	}
	stat, err := s.GetWorkFeatureStatus(ctx, "work-view/1")
	if err != nil || stat != "retired" {
		t.Fatalf("expected status 'retired', got %s (%v)", stat, err)
	}

	// Reactivating retired feature must be rejected with FEATURE_RETIRED
	err = s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "active"})
	requireCode(t, err, "FEATURE_RETIRED")

	// Restore work-view/1 to active for subsequent tests
	_, _ = s.pool.Exec(ctx, "UPDATE cairn.work_feature SET status = 'active', retired_at = NULL WHERE feature_name = 'work-view/1'")
}

func TestWorkPurgeAndRecoveryGapIntegration(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	err := s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "active"})
	if err != nil {
		t.Fatalf("SetWorkFeatureStatus active failed: %v", err)
	}

	itemUUID := uuid.New()
	p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		LocalSlug:      "purge-recovery-proj",
		Collection:     "repo:test",
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  uuid.New(),
		ProjectUUID:    uuid.New(),
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatalf("AddWorkProject failed: %v", err)
	}

	refCtx, err := s.PrepareRefresh(ctx, p.Collection, p.LocalSlug)
	if err != nil {
		t.Fatalf("PrepareRefresh failed: %v", err)
	}

	_, err = s.CommitRefreshSnapshot(ctx, refCtx, []plane.NormalizedItem{
		{
			PlaneItemUUID: itemUUID,
			Identifier:    "PRG-1",
			Title:         "Secret sensitive title to be purged",
			StateName:     "In Progress",
			StateGroup:    "started",
			SourceURL:     "https://plane.example.com/item/1",
		},
	}, false, nil)
	if err != nil {
		t.Fatalf("CommitRefreshSnapshot failed: %v", err)
	}

	// Add progress annotation with sensitive content
	annotReqID := uuid.NewString()
	annot, err := s.AddProgressAnnotation(ctx, AddProgressAnnotationRequest{
		RequestID:   annotReqID,
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		Body:        "Secret confidential progress update",
	})
	if err != nil {
		t.Fatalf("AddProgressAnnotation failed: %v", err)
	}

	// Purge with exclusion
	purgeRes, err := s.PurgeMetadata(ctx, PurgeMetadataRequest{
		RequestID:   uuid.NewString(),
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		FieldName:   "all",
		Pattern:     "confidential",
		Exclude:     true,
	})
	if err != nil {
		t.Fatalf("PurgeMetadata failed: %v", err)
	}
	if purgeRes.PurgedAnnotations == 0 {
		t.Fatalf("expected purged annotations > 0, got %d", purgeRes.PurgedAnnotations)
	}

	// Verify mutation_request row was scrubbed: ordinary_deleted = true and response IS NULL
	var ordDel bool
	var resp []byte
	err = s.pool.QueryRow(ctx, `SELECT ordinary_deleted, response FROM cairn.mutation_request WHERE request_id = $1`, annotReqID).Scan(&ordDel, &resp)
	if err != nil {
		t.Fatalf("query mutation_request failed: %v", err)
	}
	if !ordDel {
		t.Fatal("expected ordinary_deleted=true on scrubbed mutation_request")
	}
	if resp != nil {
		t.Fatalf("expected response IS NULL on scrubbed mutation_request, got %s", string(resp))
	}

	// Replaying the purged requestID must cleanly return PAYLOAD_UNAVAILABLE
	_, err = s.AddProgressAnnotation(ctx, AddProgressAnnotationRequest{
		RequestID:   annotReqID,
		Collection:  p.Collection,
		ProjectSlug: p.LocalSlug,
		ItemUUID:    itemUUID,
		Body:        "Secret confidential progress update",
	})
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")

	// Verify annotation row body is redacted
	detail, err := s.GetWorkItemDetail(ctx, p.Collection, p.LocalSlug, annot.PlaneItemUUID.String())
	if err != nil {
		t.Fatalf("GetWorkItemDetail failed: %v", err)
	}
	if len(detail.Annotations) == 0 || detail.Annotations[0].Body != "[purged]" {
		t.Fatalf("expected annotation body '[purged]', got %+v", detail.Annotations)
	}
}

func TestCommitRefreshSnapshot_NoSelfDeadlockOnFailure(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	slug := "deadlock-test-" + uuid.NewString()[:8]
	proj, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		RequestID:      uuid.NewString(),
		Collection:     "repo:test",
		LocalSlug:      slug,
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  uuid.New(),
		ProjectUUID:    uuid.New(),
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatalf("AddWorkProject failed: %v", err)
	}

	// 1. Test authRejected with 2-second timeout
	refCtx, err := s.PrepareRefresh(ctx, proj.Collection, proj.LocalSlug)
	if err != nil {
		t.Fatalf("PrepareRefresh failed: %v", err)
	}

	timeoutCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()

	_, err = s.CommitRefreshSnapshot(timeoutCtx, refCtx, nil, true, nil)
	if err == nil {
		t.Fatal("expected error on authRejected, got nil")
	}
	requireCode(t, err, "AUTH_REJECTED")

	// Verify inflight token cleared and failure retained
	var inflightToken *uuid.UUID
	var lastErr *string
	var authRej bool
	err = s.pool.QueryRow(ctx, `SELECT refresh_inflight_token, last_refresh_error, auth_rejected FROM cairn.work_project WHERE binding_id = $1`, proj.BindingID).Scan(&inflightToken, &lastErr, &authRej)
	if err != nil {
		t.Fatalf("query project failed: %v", err)
	}
	if inflightToken != nil {
		t.Fatalf("expected refresh_inflight_token to be cleared, got %v", *inflightToken)
	}
	if !authRej {
		t.Fatal("expected auth_rejected to be true")
	}
	if lastErr == nil || !strings.Contains(*lastErr, "Plane auth rejected") {
		t.Fatalf("expected last_refresh_error to contain auth rejection, got %v", lastErr)
	}

	// Reset next_refresh_allowed_at and auth_rejected to allow immediate next refresh
	_, err = s.pool.Exec(ctx, `UPDATE cairn.work_project SET next_refresh_allowed_at = NULL, auth_rejected = false WHERE binding_id = $1`, proj.BindingID)
	if err != nil {
		t.Fatalf("reset next_refresh_allowed_at failed: %v", err)
	}

	// 2. Test oversized items with 2-second timeout
	refCtx2, err := s.PrepareRefresh(ctx, proj.Collection, proj.LocalSlug)
	if err != nil {
		t.Fatalf("PrepareRefresh 2 failed: %v", err)
	}

	oversized := make([]plane.NormalizedItem, plane.MaxItemLimit+1)
	for i := range oversized {
		oversized[i] = plane.NormalizedItem{
			PlaneItemUUID: uuid.New(),
			Identifier:    fmt.Sprintf("ITEM-%d", i),
			Title:         "Oversized item",
		}
	}

	timeoutCtx2, cancel2 := context.WithTimeout(ctx, 2*time.Second)
	defer cancel2()

	_, err = s.CommitRefreshSnapshot(timeoutCtx2, refCtx2, oversized, false, nil)
	if err == nil {
		t.Fatal("expected error on oversized project, got nil")
	}
	if !errors.Is(err, plane.ErrTooLarge) {
		t.Fatalf("expected plane.ErrTooLarge, got %v", err)
	}

	// Verify inflight token cleared and failure retained
	err = s.pool.QueryRow(ctx, `SELECT refresh_inflight_token, last_refresh_error FROM cairn.work_project WHERE binding_id = $1`, proj.BindingID).Scan(&inflightToken, &lastErr)
	if err != nil {
		t.Fatalf("query project failed: %v", err)
	}
	if inflightToken != nil {
		t.Fatalf("expected refresh_inflight_token to be cleared on oversized error, got %v", *inflightToken)
	}
	if lastErr == nil || !strings.Contains(*lastErr, "PROJECT_TOO_LARGE") {
		t.Fatalf("expected last_refresh_error to contain PROJECT_TOO_LARGE, got %v", lastErr)
	}
}

func TestRecordRefreshFailure_ExclusionRevisionAndClaimFences(t *testing.T) {
	s := testStore(t, Channel{Principal: "operator:test", Operator: true})
	ctx := context.Background()

	slug := "fence-test-" + uuid.NewString()[:8]
	proj, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
		RequestID:      uuid.NewString(),
		Collection:     "repo:test",
		LocalSlug:      slug,
		PlaneInstance:  "work",
		PlaneOrigin:    "https://plane.example.com",
		WorkspaceUUID:  uuid.New(),
		ProjectUUID:    uuid.New(),
		ConfigRevision: "rev1",
	})
	if err != nil {
		t.Fatalf("AddWorkProject failed: %v", err)
	}

	// (a) Purge metadata between PrepareRefresh and RecordRefreshFailure
	refCtx, err := s.PrepareRefresh(ctx, proj.Collection, proj.LocalSlug)
	if err != nil {
		t.Fatalf("PrepareRefresh failed: %v", err)
	}

	// Exclude metadata (which increments metadata_exclusion_revision)
	_, err = s.ExcludeMetadata(ctx, ExcludeMetadataRequest{
		RequestID:   uuid.NewString(),
		Collection:  proj.Collection,
		ProjectSlug: proj.LocalSlug,
		FieldName:   "title",
		Pattern:     "secret",
	})
	if err != nil {
		t.Fatalf("ExcludeMetadata failed: %v", err)
	}

	// RecordRefreshFailure must be refused with VERSION_CONFLICT and lease released without writing stale failure
	err = s.RecordRefreshFailure(ctx, refCtx, false, "stale failure text should not be written")
	if err == nil {
		t.Fatal("expected VERSION_CONFLICT error, got nil")
	}
	requireCode(t, err, "VERSION_CONFLICT")

	var inflightToken *uuid.UUID
	var lastErr *string
	err = s.pool.QueryRow(ctx, `SELECT refresh_inflight_token, last_refresh_error FROM cairn.work_project WHERE binding_id = $1`, proj.BindingID).Scan(&inflightToken, &lastErr)
	if err != nil {
		t.Fatalf("query project failed: %v", err)
	}
	if inflightToken != nil {
		t.Fatalf("expected inflight token cleared on revision conflict, got %v", *inflightToken)
	}
	if lastErr != nil {
		t.Fatalf("expected last_refresh_error to NOT be written, got %q", *lastErr)
	}

	// (b) RecordRefreshFailure called after CommitRefreshSnapshot already succeeded for the same refCtx
	_, err = s.pool.Exec(ctx, `UPDATE cairn.work_project SET next_refresh_allowed_at = NULL WHERE binding_id = $1`, proj.BindingID)
	if err != nil {
		t.Fatalf("reset next_refresh_allowed_at failed: %v", err)
	}

	refCtx2, err := s.PrepareRefresh(ctx, proj.Collection, proj.LocalSlug)
	if err != nil {
		t.Fatalf("PrepareRefresh 2 failed: %v", err)
	}

	validItems := []plane.NormalizedItem{
		{
			PlaneItemUUID: uuid.New(),
			Identifier:    "ITEM-1",
			Title:         "Legitimate Item",
		},
	}
	snap, err := s.CommitRefreshSnapshot(ctx, refCtx2, validItems, false, nil)
	if err != nil {
		t.Fatalf("CommitRefreshSnapshot failed: %v", err)
	}
	if snap == nil {
		t.Fatal("expected valid snapshot")
	}

	// Now call RecordRefreshFailure with refCtx2 (whose claim is now completed)
	err = s.RecordRefreshFailure(ctx, refCtx2, false, "late failure attempt")
	if err == nil {
		t.Fatal("expected VERSION_CONFLICT on late failure after commit, got nil")
	}
	requireCode(t, err, "VERSION_CONFLICT")

	// Verify project still has last_refresh_error = NULL and success preserved
	var lastSuccess *time.Time
	err = s.pool.QueryRow(ctx, `SELECT last_refresh_error, last_successful_refresh_at FROM cairn.work_project WHERE binding_id = $1`, proj.BindingID).Scan(&lastErr, &lastSuccess)
	if err != nil {
		t.Fatalf("query project failed: %v", err)
	}
	if lastErr != nil {
		t.Fatalf("expected last_refresh_error to be nil, got %q", *lastErr)
	}
	if lastSuccess == nil {
		t.Fatal("expected last_successful_refresh_at to be preserved")
	}
}
