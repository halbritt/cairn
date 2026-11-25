package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func sha256SumHex(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

func TestCampaignLedgerCoordinatorRebuildFromEmptyDirAndDerivedSummary(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	emptyWorkdir := t.TempDir()

	root := f.register(nativeAgent{Binding: "coord-root", Workspace: emptyWorkdir})
	leadA := f.register(nativeAgent{Binding: "lead-a", Workspace: emptyWorkdir})
	workerA := f.register(nativeAgent{Binding: "worker-a", Workspace: emptyWorkdir})

	rootView := f.view(sessionRef(root))
	leadAView := f.view(sessionRef(leadA))
	workerAView := f.view(sessionRef(workerA))

	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "rebuild",
		RootAgent: root.Inbox, OutOfChainPolicy: "reroute", Sensitivity: "shareable",
	}, f.dest); err != nil {
		t.Fatalf("SetTeamCampaign: %v", err)
	}
	if _, err := rootView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "rebuild", Member: leadA.Inbox, Lead: root.Inbox, Role: "lead",
	}, f.dest); err != nil {
		t.Fatalf("SetTeamMember(leadA): %v", err)
	}
	if _, err := workerAView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "rebuild", Member: workerA.Inbox, Lead: leadA.Inbox, Role: "worker",
	}, f.dest); err != nil {
		t.Fatalf("SetTeamMember(workerA): %v", err)
	}

	// Dispatch two assignments and record one open decision, one open blocker, and one active hold without writing any files.
	send1, err := leadAView.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "rebuild", OpKey: "send-slice-1",
		Body: "Implement auth slice 1", Shareable: true,
		Publication: PublishEventRequest{Kind: "request", Destination: EventDestination{Type: "agent", Name: workerA.Inbox}, Summary: "Dispatch slice-1"},
		ItemKey:     "slice-1",
	}, f.dest)
	if err != nil || send1.State != "committed" {
		t.Fatalf("CampaignSend(slice-1): %+v %v", send1, err)
	}
	if _, err := rootView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "rebuild",
		Kind: "assignment", ItemKey: "slice-2", Owner: workerA.Inbox, Status: "planned", Summary: "Implement auth slice 2",
	}, f.dest); err != nil {
		t.Fatalf("SetCampaignLedgerItem(slice-2): %v", err)
	}
	if _, err := leadAView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "rebuild",
		Kind: "decision", ItemKey: "dec-schema", Owner: root.Inbox, Status: "open", Summary: "Choose token rotation window",
	}, f.dest); err != nil {
		t.Fatalf("SetCampaignLedgerItem(dec-schema): %v", err)
	}
	if _, err := workerAView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "rebuild",
		Kind: "blocker", ItemKey: "blk-kms", Status: "open", Summary: "Waiting on KMS test fixture",
	}, f.dest); err != nil {
		t.Fatalf("SetCampaignLedgerItem(blk-kms): %v", err)
	}
	if _, err := rootView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "rebuild",
		Kind: "hold", ItemKey: "hold-deploy", Status: "active", Summary: "Hold rollout until backfill settles",
	}, f.dest); err != nil {
		t.Fatalf("SetCampaignLedgerItem(hold-deploy): %v", err)
	}

	// Worker claims and completes slice-1 via CampaignComplete, updating the ledger item and replying to leadA.
	nx, err := workerAView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || nx.Delivery == nil {
		t.Fatalf("workerA NextEvent: %+v %v", nx, err)
	}
	comp, err := workerAView.CampaignComplete(ctx, CampaignCompleteRequest{
		Repo: f.repo, Campaign: "rebuild", OpKey: "comp-slice-1",
		DeliveryID: nx.Delivery.DeliveryID, LeaseID: nx.Delivery.LeaseID,
		Body: "Auth slice 1 verified", Shareable: true, ReplyTo: leadA.Inbox, Summary: "Completed slice-1", ItemKey: "slice-1",
	}, f.dest)
	if err != nil || comp.ReplyEvent == nil {
		t.Fatalf("CampaignComplete(slice-1): %+v %v", comp, err)
	}

	// Verify automatic session task_summary derivation on BOTH root's AND intermediate leadA's live AgentInstances!
	rootAgent, err := f.receiver.HeartbeatAgent(ctx, sessionRef(root), f.dest)
	if err != nil {
		t.Fatalf("HeartbeatAgent(root): %v", err)
	}
	if !strings.Contains(rootAgent.Metadata.TaskSummary, "campaign rebuild [root]: 1 active assignments (1 completed), 1 open blockers, 1 open decisions, 1 active holds") {
		t.Fatalf("unexpected derived root task_summary: %q", rootAgent.Metadata.TaskSummary)
	}
	leadAgent, err := f.receiver.HeartbeatAgent(ctx, sessionRef(leadA), f.dest)
	if err != nil || !strings.Contains(leadAgent.Metadata.TaskSummary, "campaign rebuild [lead") {
		t.Fatalf("expected derived leadA task_summary, got %+v %v", leadAgent, err)
	}

	// Coordinator rebuild in a brand-new empty directory with ZERO private files (resumed execution)!
	freshDir := t.TempDir()
	freshCoord := f.register(nativeAgent{Binding: "coord-root", Workspace: freshDir})
	if freshCoord.ExecutionID == root.ExecutionID || freshCoord.AgentID != root.AgentID {
		t.Fatalf("expected new execution on same coordinator agent_id: old=%+v new=%+v", root, freshCoord)
	}
	freshView := f.view(sessionRef(freshCoord))
	syncRes, err := freshView.SyncCampaignContext(ctx, SyncCampaignContextRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "rebuild",
	}, f.dest)
	if err != nil || syncRes.Agent == nil || !strings.Contains(syncRes.Agent.Metadata.TaskSummary, "campaign rebuild [root]") {
		t.Fatalf("SyncCampaignContext(freshCoord): %+v %v", syncRes, err)
	}
	snap, err := freshView.GetCampaignLedger(ctx, GetCampaignLedgerRequest{Repo: f.repo, Campaign: "rebuild"}, f.dest)
	if err != nil {
		t.Fatalf("GetCampaignLedger(freshCoord): %v", err)
	}
	if snap.Counts.ActiveAssignments != 1 || snap.Counts.CompletedAssignments != 1 || snap.Counts.OpenDecisions != 1 || snap.Counts.OpenBlockers != 1 || snap.Counts.ActiveHolds != 1 {
		t.Fatalf("unexpected rebuilt ledger counts: %+v", snap.Counts)
	}
	if len(snap.Assignments) != 2 || len(snap.Decisions) != 1 || len(snap.Blockers) != 1 || len(snap.Holds) != 1 || len(snap.Operations) != 2 {
		t.Fatalf("unexpected rebuilt ledger slices: %+v", snap)
	}
	// Resume active coordination from the rebuilt ledger snapshot alone (settling dec-schema and dispatching slice-2).
	if _, err := freshView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "rebuild",
		Kind: "decision", ItemKey: snap.Decisions[0].ItemKey, Status: "settled",
	}, f.dest); err != nil {
		t.Fatalf("freshView settle decision failed: %v", err)
	}
	if _, err := freshView.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "rebuild", OpKey: "send-slice-2",
		Body: "Dispatch planned slice-2 after rebuild", Shareable: true,
		Publication: PublishEventRequest{Kind: "request", Destination: EventDestination{Type: "agent", Name: workerA.Inbox}, Summary: "Dispatch slice-2"},
		ItemKey:     "slice-2",
	}, f.dest); err != nil {
		t.Fatalf("freshView dispatch slice-2 failed: %v", err)
	}
	entries, _ := os.ReadDir(freshDir)
	emptyEntries, _ := os.ReadDir(emptyWorkdir)
	if len(entries) != 0 || len(emptyEntries) != 0 {
		t.Fatalf("expected zero private journal files on disk, got fresh=%d work=%d", len(entries), len(emptyEntries))
	}
}

func TestCampaignLedgerIdempotentRetryPublishSendCompleteAckAndCrashStates(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	root := f.register(nativeAgent{Binding: "idem-root"})
	worker := f.register(nativeAgent{Binding: "idem-worker"})
	rootView := f.view(sessionRef(root))
	workerView := f.view(sessionRef(worker))

	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "idem", RootAgent: root.Inbox, Sensitivity: "shareable",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	if _, err := rootView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "idem", Member: worker.Inbox, Lead: root.Inbox, Role: "worker",
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	// 1. CampaignPublish retry + IDEMPOTENCY_CONFLICT on mutated arguments.
	srcRef := RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version}
	pubReq := CampaignPublishRequest{
		Repo: f.repo, Campaign: "idem", OpKey: "pub-1",
		Publication: PublishEventRequest{Kind: "notice", Action: "review", Ref: srcRef, Destination: EventDestination{Type: "agent", Name: worker.Inbox}, Summary: "Notice 1"},
	}
	ev1, err := rootView.CampaignPublish(ctx, pubReq, f.dest)
	if err != nil {
		t.Fatalf("CampaignPublish initial: %v", err)
	}
	ev1Replay, err := rootView.CampaignPublish(ctx, pubReq, f.dest)
	if err != nil || ev1Replay.EventID != ev1.EventID {
		t.Fatalf("CampaignPublish replay mismatch: %+v vs %+v (%v)", ev1Replay, ev1, err)
	}
	badPub := pubReq
	badPub.Publication.Summary = "Mutated summary"
	_, err = rootView.CampaignPublish(ctx, badPub, f.dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")

	// 2. CampaignAck retry (including omitting lease_id on retry!).
	nxNotice, err := workerView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || nxNotice.Delivery == nil {
		t.Fatalf("worker NextEvent(notice): %+v %v", nxNotice, err)
	}
	ack1, err := workerView.CampaignAck(ctx, CampaignAckRequest{
		Repo: f.repo, Campaign: "idem", OpKey: "ack-1", DeliveryID: nxNotice.Delivery.DeliveryID, LeaseID: nxNotice.Delivery.LeaseID,
	}, f.dest)
	if err != nil || ack1.State != "handled" {
		t.Fatalf("CampaignAck initial: %+v %v", ack1, err)
	}
	ack1Replay, err := workerView.CampaignAck(ctx, CampaignAckRequest{
		Repo: f.repo, Campaign: "idem", OpKey: "ack-1", DeliveryID: nxNotice.Delivery.DeliveryID,
	}, f.dest)
	if err != nil || ack1Replay.DeliveryID != ack1.DeliveryID {
		t.Fatalf("CampaignAck replay without lease failed: %+v %v", ack1Replay, err)
	}

	// 3. Simulate CampaignSend crash after source_saved -> retry recovers without duplicating source record!
	sendReq := CampaignSendRequest{
		Repo: f.repo, Campaign: "idem", OpKey: "send-recover",
		Body: "Recoverable assignment body", Shareable: true,
		Publication: PublishEventRequest{Kind: "request", Destination: EventDestination{Type: "agent", Name: worker.Inbox}, Summary: "Recoverable send"},
		ItemKey:     "slice-rec",
	}
	preRec, err := rootView.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft:     Draft{Body: sendReq.Body, Kind: "note", ClaimType: "self", Sensitivity: "shareable", Scope: Scope{Repo: f.repo, TaskID: "coordination/test", RunID: "*"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	normPub := sendReq.Publication
	normPub.Repo = f.repo
	bodySum := sha256SumHex(sendReq.Body)
	sendHash, _ := hashCampaignIntent(struct {
		Op          string              `json:"op"`
		Repo        string              `json:"repo"`
		Campaign    string              `json:"campaign"`
		Destination string              `json:"destination"`
		BodySHA256  string              `json:"body_sha256"`
		SourceKind  string              `json:"source_kind"`
		Shareable   bool                `json:"shareable"`
		Publication PublishEventRequest `json:"publication"`
		ItemKey     string              `json:"item_key,omitempty"`
		ItemKind    string              `json:"item_kind,omitempty"`
		ItemStatus  string              `json:"item_status,omitempty"`
	}{"send", f.repo, "idem", f.dest.Name, bodySum, "note", true, normPub, "slice-rec", "", ""})
	if _, err := f.receiver.pool.Exec(ctx, `INSERT INTO cairn.campaign_operation_journal(repo,campaign,caller,op_key,operation,intent_sha256,source_request_id,action_request_id,source_record_id,source_version,state) VALUES($1,'idem',$2,'send-recover','send',$3,$4::uuid,$5::uuid,$6::uuid,$7,'source_saved')`,
		f.repo, root.Inbox, sendHash, uuid.NewString(), uuid.NewString(), preRec.RecordID, preRec.Version); err != nil {
		t.Fatal(err)
	}
	sendOut, err := rootView.CampaignSend(ctx, sendReq, f.dest)
	if err != nil || sendOut.Source.RecordID != preRec.RecordID || sendOut.Event.Ref.RecordID != preRec.RecordID {
		t.Fatalf("CampaignSend recovery from source_saved failed: %+v %v", sendOut, err)
	}

	// 4. Worker claims & completes slice-rec (with reply), plus replay without lease and verify replaying send-recover does NOT clobber completed status!
	nxReq, err := workerView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || nxReq.Delivery == nil {
		t.Fatalf("worker NextEvent(request): %+v %v", nxReq, err)
	}
	compReq := CampaignCompleteRequest{
		Repo: f.repo, Campaign: "idem", OpKey: "comp-rec",
		DeliveryID: nxReq.Delivery.DeliveryID, LeaseID: nxReq.Delivery.LeaseID,
		Body: "Recovered completion result", Shareable: true, ReplyTo: root.Inbox, Summary: "Completed slice-rec", ItemKey: "slice-rec",
	}
	comp1, err := workerView.CampaignComplete(ctx, compReq, f.dest)
	if err != nil || comp1.ReplyEvent == nil {
		t.Fatalf("CampaignComplete initial failed: %+v %v", comp1, err)
	}
	compReplay := compReq
	compReplay.LeaseID = ""
	comp2, err := workerView.CampaignComplete(ctx, compReplay, f.dest)
	if err != nil || comp2.Result != comp1.Result || comp2.ReplyEvent == nil || comp2.ReplyEvent.EventID != comp1.ReplyEvent.EventID {
		t.Fatalf("CampaignComplete replay without lease mismatch: %+v vs %+v (%v)", comp2, comp1, err)
	}
	if _, err := rootView.CampaignSend(ctx, sendReq, f.dest); err != nil {
		t.Fatalf("CampaignSend post-completion replay failed: %v", err)
	}
	snap, err := rootView.GetCampaignLedger(ctx, GetCampaignLedgerRequest{Repo: f.repo, Campaign: "idem"}, f.dest)
	if err != nil || len(snap.Assignments) != 1 || snap.Assignments[0].Status != "completed" {
		t.Fatalf("replaying committed CampaignSend must not overwrite completed assignment status: %+v %v", snap.Assignments, err)
	}

	// 5. Simulate CampaignComplete crash in completed_reply_pending -> retry without lease publishes reply & updates ledger!
	sendPend, err := rootView.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "idem", OpKey: "send-pend",
		Body: "Pending reply assignment\n", Shareable: true,
		Publication: PublishEventRequest{Kind: "request", Destination: EventDestination{Type: "agent", Name: worker.Inbox}},
		ItemKey:     "slice-pend",
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	nxPend, err := workerView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || nxPend.Delivery == nil || nxPend.Delivery.Event.EventID != sendPend.Event.EventID {
		t.Fatalf("worker NextEvent(pend): %+v %v", nxPend, err)
	}
	actReqID := uuid.NewString()
	repReqID := uuid.NewString()
	pendBody := "Finished pending slice\n"
	pendDraft := Draft{Body: pendBody, Kind: "note", ClaimType: "self", Sensitivity: "shareable", Scope: Scope{Repo: f.repo, TaskID: "*", RunID: "*"}}
	settledDeliv, err := workerView.CompleteEvent(ctx, CompleteEventRequest{
		RequestID: actReqID, DeliveryID: nxPend.Delivery.DeliveryID, LeaseID: nxPend.Delivery.LeaseID, Disposition: "handled", Draft: &pendDraft,
	}, f.dest)
	if err != nil || settledDeliv.Result == nil {
		t.Fatal(err)
	}
	pendHash, _ := hashCampaignIntent(struct {
		Op            string `json:"op"`
		Repo          string `json:"repo"`
		Campaign      string `json:"campaign"`
		Destination   string `json:"destination"`
		DeliveryID    string `json:"delivery_id"`
		BodySHA256    string `json:"body_sha256"`
		SourceKind    string `json:"source_kind"`
		Shareable     bool   `json:"shareable"`
		ReplyTo       string `json:"reply_to,omitempty"`
		CausationID   string `json:"causation_id,omitempty"`
		CorrelationID string `json:"correlation_id,omitempty"`
		Summary       string `json:"summary,omitempty"`
		ItemKey       string `json:"item_key,omitempty"`
		ItemStatus    string `json:"item_status,omitempty"`
	}{"complete", f.repo, "idem", f.dest.Name, nxPend.Delivery.DeliveryID, sha256SumHex(pendBody), "note", true, root.Inbox, "", "", "Reply summary", "slice-pend", ""})
	if _, err := f.receiver.pool.Exec(ctx, `INSERT INTO cairn.campaign_operation_journal(repo,campaign,caller,op_key,operation,sensitivity,intent_sha256,action_request_id,reply_request_id,lease_id,source_record_id,source_version,delivery_id,event_id,state) VALUES($1,'idem',$2,'comp-pend','complete','shareable',$3,$4::uuid,$5::uuid,$6::uuid,$7::uuid,$8,$9::uuid,$10::uuid,'completed_reply_pending')`,
		f.repo, worker.Inbox, pendHash, actReqID, repReqID, nxPend.Delivery.LeaseID, settledDeliv.Result.RecordID, settledDeliv.Result.Version, settledDeliv.DeliveryID, settledDeliv.Event.EventID); err != nil {
		t.Fatal(err)
	}
	compPend, err := workerView.CampaignComplete(ctx, CampaignCompleteRequest{
		Repo: f.repo, Campaign: "idem", OpKey: "comp-pend", DeliveryID: nxPend.Delivery.DeliveryID,
		Body: pendBody, Shareable: true, ReplyTo: root.Inbox, Summary: "Reply summary", ItemKey: "slice-pend",
	}, f.dest)
	if err != nil || compPend.ReplyEvent == nil || compPend.Result != *settledDeliv.Result {
		t.Fatalf("recovery from completed_reply_pending failed: %+v %v", compPend, err)
	}
}

func TestCampaignLedgerAuthorizationAndSensitivityIsolation(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	root := f.register(nativeAgent{Binding: "auth-root"})
	leadA := f.register(nativeAgent{Binding: "auth-lead-a"})
	leadB := f.register(nativeAgent{Binding: "auth-lead-b"})
	workerA := f.register(nativeAgent{Binding: "auth-worker-a"})
	outsider := f.register(nativeAgent{Binding: "auth-outsider"})

	rootView := f.view(sessionRef(root))
	leadAView := f.view(sessionRef(leadA))
	leadBView := f.view(sessionRef(leadB))
	workerAView := f.view(sessionRef(workerA))
	outsiderView := f.view(sessionRef(outsider))

	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", RootAgent: root.Inbox, Sensitivity: "shareable",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	for _, m := range []SetTeamMemberRequest{
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", Member: leadA.Inbox, Lead: root.Inbox, Role: "lead"},
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", Member: leadB.Inbox, Lead: root.Inbox, Role: "lead"},
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", Member: workerA.Inbox, Lead: leadA.Inbox, Role: "worker"},
	} {
		if _, err := rootView.SetTeamMember(ctx, m, f.dest); err != nil {
			t.Fatal(err)
		}
	}

	// Outsider cannot write ledger items or journal ops; worker cannot invent assignments; leadB cannot touch leadA's subtree; leadA CAN manage workerA.
	_, err := outsiderView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", Kind: "blocker", ItemKey: "b1", Summary: "Outsider",
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = workerAView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", Kind: "assignment", ItemKey: "a1", Summary: "Self-invented",
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = leadBView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", Kind: "assignment", ItemKey: "a1", Owner: workerA.Inbox, Summary: "Cross-subtree",
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	if _, err := leadAView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", Kind: "assignment", ItemKey: "a1", Owner: workerA.Inbox, Summary: "Valid subtree assignment",
	}, f.dest); err != nil {
		t.Fatalf("leadA subtree assignment failed: %v", err)
	}

	// #2: Subordinate lead cannot forge a settled root decision or settle/steal root's open decision.
	_, err = leadAView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", Kind: "decision", ItemKey: "dec-forged", Owner: root.Inbox, Status: "settled", Summary: "Forged",
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	if _, err := rootView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", Kind: "decision", ItemKey: "dec-root", Owner: root.Inbox, Status: "open", Summary: "Root decision",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	_, err = leadAView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", Kind: "decision", ItemKey: "dec-root", Status: "settled",
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	// #3: Worker CampaignComplete with uncreated --item-key refuses UPFRONT before settling delivery or reserving op_key!
	reqEv, err := leadAView.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "authz", OpKey: "send-w1", Body: "Work item", Shareable: true,
		Publication: PublishEventRequest{Kind: "request", Destination: EventDestination{Type: "agent", Name: workerA.Inbox}},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	nxW, err := workerAView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || nxW.Delivery == nil || nxW.Delivery.Event.EventID != reqEv.Event.EventID {
		t.Fatalf("workerA NextEvent: %+v %v", nxW, err)
	}
	_, err = workerAView.CampaignComplete(ctx, CampaignCompleteRequest{
		Repo: f.repo, Campaign: "authz", OpKey: "comp-unauth-key", DeliveryID: nxW.Delivery.DeliveryID, LeaseID: nxW.Delivery.LeaseID,
		Body: "Done", Shareable: true, ItemKey: "nonexistent-slice",
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	// Same op_key now succeeds cleanly with authorized ItemKey "a1" because nothing was wedged or settled!
	if _, err := workerAView.CampaignComplete(ctx, CampaignCompleteRequest{
		Repo: f.repo, Campaign: "authz", OpKey: "comp-unauth-key", DeliveryID: nxW.Delivery.DeliveryID, LeaseID: nxW.Delivery.LeaseID,
		Body: "Done", Shareable: true, ItemKey: "a1",
	}, f.dest); err != nil {
		t.Fatalf("retry with authorized ItemKey failed: %v", err)
	}

	// #4: Cross-session SyncCampaignContext hijack and outsider SyncCampaignContext are refused.
	wRef := sessionRef(workerA)
	_, err = leadAView.SyncCampaignContext(ctx, SyncCampaignContextRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", Session: &wRef,
	}, f.dest)
	requireCode(t, err, "INVALID_REQUEST")
	_, err = outsiderView.SyncCampaignContext(ctx, SyncCampaignContextRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz",
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	// #5: Sensitivity isolation: shareable ledger refuses local source, local journal ops are hidden on hosted GetCampaignLedger, and local campaign refuses hosted session task_summary sync.
	localDst := Destination{Name: "local", AllowLocal: true}
	localRec, err := rootView.Create(ctx, CreateRequest{
		RequestID: uuid.NewString(),
		Draft:     Draft{Body: "local secret note", Kind: "note", ClaimType: "self", Sensitivity: "local", Scope: Scope{Repo: f.repo, TaskID: "*", RunID: "*"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = rootView.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", Kind: "decision", ItemKey: "d-loc", Summary: "Local ref",
		Source: &RecordVersionRef{RecordID: localRec.RecordID, Version: localRec.Version},
	}, localDst)
	requireCode(t, err, "DESTINATION_PROHIBITED")
	if _, err := rootView.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "authz", OpKey: "local-secret-op", Body: "local secret body", Shareable: false,
		Publication: PublishEventRequest{Kind: "notice", Action: "review", Destination: EventDestination{Type: "agent", Name: leadA.Inbox}},
	}, localDst); err != nil {
		t.Fatalf("local CampaignSend without item_key failed: %v", err)
	}
	hostedSnap, err := rootView.GetCampaignLedger(ctx, GetCampaignLedgerRequest{Repo: f.repo, Campaign: "authz"}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	for _, op := range hostedSnap.Operations {
		if op.OpKey == "local-secret-op" {
			t.Fatalf("hosted GetCampaignLedger leaked local operation row: %+v", op)
		}
	}
	inactive := false
	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "authz", Active: &inactive,
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "local-only", RootAgent: root.Inbox, Sensitivity: "local",
	}, localDst); err != nil {
		t.Fatal(err)
	}
	_, err = rootView.GetCampaignLedger(ctx, GetCampaignLedgerRequest{Repo: f.repo, Campaign: "local-only"}, f.dest)
	requireCode(t, err, "NOT_FOUND")
	_, err = rootView.SyncCampaignContext(ctx, SyncCampaignContextRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "local-only",
	}, localDst)
	requireCode(t, err, "DESTINATION_PROHIBITED")
}
