package core

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestCoordinatorHandoffThresholdsRoleInboxContinuityZeroLostOrDuplicateMessages(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)

	root1 := f.register(nativeAgent{Binding: "coord-slot", Native: "sess-1"})
	leadA := f.register(nativeAgent{Binding: "lead-a"})
	workerA := f.register(nativeAgent{Binding: "worker-a"})

	root1View := f.view(sessionRef(root1))
	leadAView := f.view(sessionRef(leadA))
	workerAView := f.view(sessionRef(workerA))

	maxAge := 3600
	maxComp := 2
	camp, err := root1View.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha",
		RootAgent: root1.Inbox, OutOfChainPolicy: "reroute", Sensitivity: "shareable",
		MaxCoordinatorAgeSeconds: &maxAge, MaxCoordinatorCompactions: &maxComp,
	}, f.dest)
	if err != nil || camp.RoleAddress != "role/alpha/root" {
		t.Fatalf("SetTeamCampaign: %+v %v", camp, err)
	}
	if _, err := root1View.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha", Member: leadA.Inbox, Lead: root1.Inbox, Role: "lead",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	if _, err := workerAView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha", Member: workerA.Inbox, Lead: leadA.Inbox, Role: "worker",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	if _, err := root1View.SetSubscription(ctx, SubscriptionRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Topic: "team.alpha", Active: true,
		Filter: &SubscriptionFilter{Actions: []string{"decide", "complete"}, IncludeBlockers: true},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := root1View.SetCampaignLedgerItem(ctx, SetCampaignLedgerItemRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha",
		Kind: "decision", ItemKey: "dec-1", Owner: root1.Inbox, Status: "open", Summary: "Decide cutover window",
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	// M1 -> role/alpha/root settled by root1 BEFORE handoff (must NEVER be duplicated to root2).
	m1, err := leadAView.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "alpha", OpKey: "lead-m1", Body: "Settled report M1", Shareable: true,
		Publication: PublishEventRequest{Kind: "notice", Destination: EventDestination{Type: "agent", Name: "role/alpha/root"}},
	}, f.dest)
	if err != nil || m1.Event.Routing == nil || m1.Event.Routing.OutOfChain || m1.Event.Routing.RoleAddress != "role/alpha/root" {
		t.Fatalf("M1 to role/alpha/root should be in-chain: %+v %v", m1, err)
	}
	nx1, err := root1View.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || nx1.Delivery == nil || nx1.Delivery.Event.EventID != m1.Event.EventID {
		t.Fatalf("root1 claim M1: %+v %v", nx1, err)
	}
	if _, err := root1View.CampaignAck(ctx, CampaignAckRequest{
		Repo: f.repo, Campaign: "alpha", OpKey: "root1-ack-m1", DeliveryID: nx1.Delivery.DeliveryID, LeaseID: nx1.Delivery.LeaseID,
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	// M2 -> role/alpha/root stays pending in root1's inbox at handoff time; R1 dispatched by root1 to leadA before handoff.
	m2, err := leadAView.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "alpha", OpKey: "lead-m2", Body: "In-flight pending report M2", Shareable: true,
		Publication: PublishEventRequest{Kind: "notice", Destination: EventDestination{Type: "agent", Name: "role/alpha/root"}},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	r1, err := root1View.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "alpha", OpKey: "root1-r1", Body: "Pre-handoff request R1", Shareable: true,
		Publication: PublishEventRequest{Kind: "request", Destination: EventDestination{Type: "agent", Name: leadA.Inbox}},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}

	// Threshold not yet reached -> OnlyIfThresholdReached skips handoff cleanly.
	skipRes, err := root1View.HandoffCampaignCoordinator(ctx, HandoffCampaignCoordinatorRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha", OnlyIfThresholdReached: true,
		NewSession: &NewCoordinatorSessionSpec{Binding: "coord-slot", NativeSessionID: "sess-2"},
	}, f.dest)
	if err != nil || skipRes.Performed || skipRes.Status.ShouldHandoff {
		t.Fatalf("expected OnlyIfThresholdReached to skip before threshold: %+v %v", skipRes, err)
	}

	// Two compactions hit MaxCoordinatorCompactions (2) -> ShouldHandoff becomes true!
	if _, err := root1View.RecordCoordinatorCompaction(ctx, RecordCoordinatorCompactionRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha"}, f.dest); err != nil {
		t.Fatal(err)
	}
	st2, err := root1View.RecordCoordinatorCompaction(ctx, RecordCoordinatorCompactionRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha"}, f.dest)
	if err != nil || !st2.ShouldHandoff || st2.CompactionCount != 2 || st2.ProposedCommand == "" {
		t.Fatalf("expected ShouldHandoff after 2 compactions: %+v %v", st2, err)
	}

	// Perform Handoff 1 (root1 -> root2 via NewSession): writes handoff record, transfers role/alpha/root, M2, subscriptions, and stops root1!
	ho1ReqID := uuid.NewString()
	ho1Req := HandoffCampaignCoordinatorRequest{
		RequestID: ho1ReqID, Repo: f.repo, Campaign: "alpha", OnlyIfThresholdReached: true,
		NewSession: &NewCoordinatorSessionSpec{Binding: "coord-slot", NativeSessionID: "sess-2"},
		Note:       "Handing off after 2 compactions",
		Shareable:  true,
	}
	ho1, err := root1View.HandoffCampaignCoordinator(ctx, ho1Req, f.dest)
	if err != nil || !ho1.Performed || ho1.ToSession == nil || ho1.Handoff == nil || ho1.TransferredDeliveries != 1 || ho1.TransferredSubscriptions != 1 {
		t.Fatalf("Handoff 1 failed: %+v %v", ho1, err)
	}
	root2 := *ho1.ToSession
	root2View := f.view(sessionRef(root2))

	// Old coordinator session root1 is now stopped, EXCEPT idempotent replay of ho1Req!
	ho1Replay, err := root1View.HandoffCampaignCoordinator(ctx, ho1Req, f.dest)
	if err != nil || ho1Replay.HandoffID != ho1.HandoffID || !ho1Replay.Performed {
		t.Fatalf("expected idempotent replay of Handoff 1 on stopped root1View, got %+v %v", ho1Replay, err)
	}
	nonReplayReq := ho1Req
	nonReplayReq.RequestID = uuid.NewString()
	_, err = root1View.HandoffCampaignCoordinator(ctx, nonReplayReq, f.dest)
	requireCode(t, err, "STALE_SESSION")
	_, err = root1View.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	requireCode(t, err, "STALE_SESSION")

	// Handoff note contains ledger state ([root] post-transfer summary) and coordinator note.
	hNote, err := root2View.History(ctx, RecordHistoryRequest{RecordID: ho1.Handoff.RecordID, Version: ho1.Handoff.Version}, f.dest)
	if err != nil || len(hNote.Versions) != 1 || hNote.Versions[0].Body == nil || !strings.Contains(*hNote.Versions[0].Body, "[root]") || !strings.Contains(*hNote.Versions[0].Body, "Decide cutover window") || !strings.Contains(*hNote.Versions[0].Body, "Handing off after 2 compactions") {
		t.Fatalf("unexpected handoff record body: %+v %v", hNote, err)
	}

	// Post-handoff messages from leadA & workerA without telling anyone root2's UUID:
	// M3 -> role/alpha/root; M4 -> blocker on topic team.alpha; M5 -> reply to pre-handoff R1 addressed to old root1.Inbox!
	m3, err := leadAView.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "alpha", OpKey: "lead-m3", Body: "Post-handoff report M3 to role address", Shareable: true,
		Publication: PublishEventRequest{Kind: "notice", Destination: EventDestination{Type: "agent", Name: "role/alpha/root"}},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	m4, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Blocker: true, Summary: "Topic blocker M4",
		Ref: RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version}, Destination: EventDestination{Type: "topic", Name: "team.alpha"},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	nxLeadR1, err := leadAView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || nxLeadR1.Delivery == nil || nxLeadR1.Delivery.Event.EventID != r1.Event.EventID {
		t.Fatalf("leadA claim R1: %+v %v", nxLeadR1, err)
	}
	m5Comp, err := leadAView.CampaignComplete(ctx, CampaignCompleteRequest{
		Repo: f.repo, Campaign: "alpha", OpKey: "lead-comp-r1", DeliveryID: nxLeadR1.Delivery.DeliveryID, LeaseID: nxLeadR1.Delivery.LeaseID,
		Body: "Reply M5 to old root1 address", Shareable: true, ReplyTo: root1.Inbox,
	}, f.dest)
	if err != nil || m5Comp.ReplyEvent == nil {
		t.Fatalf("leadA reply M5: %+v %v", m5Comp, err)
	}

	// root2 drains its inbox: receives M2, M3, M4, M5 (4 distinct messages; M1 NOT duplicated!).
	received := map[string]bool{}
	for i := 0; i < 4; i++ {
		nx, nErr := root2View.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
		if nErr != nil || nx.Delivery == nil {
			t.Fatalf("root2 expected delivery %d/4, got %+v %v", i+1, nx, nErr)
		}
		received[nx.Delivery.Event.EventID] = true
		if _, aErr := root2View.CampaignAck(ctx, CampaignAckRequest{
			Repo: f.repo, Campaign: "alpha", OpKey: "root2-ack-" + nx.Delivery.DeliveryID,
			DeliveryID: nx.Delivery.DeliveryID, LeaseID: nx.Delivery.LeaseID,
		}, f.dest); aErr != nil {
			t.Fatal(aErr)
		}
	}
	nxEmpty, err := root2View.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || nxEmpty.Delivery != nil {
		t.Fatalf("expected root2 inbox empty after 4 deliveries, got %+v %v", nxEmpty, err)
	}
	for _, wantEv := range []string{m2.Event.EventID, m3.Event.EventID, m4.EventID, m5Comp.ReplyEvent.EventID} {
		if !received[wantEv] {
			t.Fatalf("root2 missed expected event %s (received=%v)", wantEv, received)
		}
	}
	if received[m1.Event.EventID] {
		t.Fatal("already-settled M1 was duplicated across handoff")
	}

	// root2 can inspect pre-handoff request R1 via coordinator handoff lineage!
	if _, err := root2View.AgentEventStatus(ctx, EventStatusRequest{EventID: r1.Event.EventID}, f.dest); err != nil {
		t.Fatalf("root2 AgentEventStatus(R1) failed: %v", err)
	}

	// Age threshold Handoff 2 (root2 -> pre-registered root3 via Successor):
	if _, err := f.receiver.pool.Exec(ctx, `UPDATE cairn.agent_session SET started_at = clock_timestamp() - interval '2 hours' WHERE agent_id = $1`, root2.AgentID); err != nil {
		t.Fatal(err)
	}
	ageCheck, err := root2View.CheckCoordinatorHandoff(ctx, CheckCoordinatorHandoffRequest{Repo: f.repo, Campaign: "alpha"}, f.dest)
	if err != nil || !ageCheck.ShouldHandoff || ageCheck.AgeSeconds < 3600 {
		t.Fatalf("expected ShouldHandoff on age threshold: %+v %v", ageCheck, err)
	}
	root3 := f.register(nativeAgent{Binding: "coord-slot", Native: "sess-3"})
	r3Ref := sessionRef(root3)
	root3View := f.view(r3Ref)
	// Pre-subscribe root3 to team.alpha and fan out a topic blocker to both root2 and root3 BEFORE Handoff 2 to verify dedup without 23505!
	if _, err := root3View.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: f.repo, Topic: "team.alpha", Active: true}); err != nil {
		t.Fatal(err)
	}
	dupEv, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Blocker: true, Summary: "Shared topic event before Handoff 2",
		Ref: RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version}, Destination: EventDestination{Type: "topic", Name: "team.alpha"},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	ho2, err := root2View.HandoffCampaignCoordinator(ctx, HandoffCampaignCoordinatorRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha", OnlyIfThresholdReached: true,
		Successor: &r3Ref,
	}, f.dest)
	if err != nil || !ho2.Performed || ho2.ToPrincipal != root3.Inbox || ho2.TransferredDeliveries != 0 {
		t.Fatalf("Handoff 2 (age -> pre-subscribed root3) failed: %+v %v", ho2, err)
	}
	// Drain dupEv on root3 (exactly 1 copy, not duplicated!).
	nxDup, err := root3View.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || nxDup.Delivery == nil || nxDup.Delivery.Event.EventID != dupEv.EventID {
		t.Fatalf("root3 expected single copy of dupEv: %+v %v", nxDup, err)
	}
	if _, err := root3View.CampaignAck(ctx, CampaignAckRequest{
		Repo: f.repo, Campaign: "alpha", OpKey: "root3-ack-dup", DeliveryID: nxDup.Delivery.DeliveryID, LeaseID: nxDup.Delivery.LeaseID,
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	// Transitive lineage: root3 can still inspect root1's pre-handoff request R1, and role/alpha/root routes to root3!
	if _, err := root3View.AgentEventStatus(ctx, EventStatusRequest{EventID: r1.Event.EventID}, f.dest); err != nil {
		t.Fatalf("root3 transitive AgentEventStatus(R1) failed: %v", err)
	}
	m6, err := leadAView.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "alpha", OpKey: "lead-m6", Body: "Post-second-handoff report M6", Shareable: true,
		Publication: PublishEventRequest{Kind: "notice", Destination: EventDestination{Type: "agent", Name: "role/alpha/root"}},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	nx6, err := root3View.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || nx6.Delivery == nil || nx6.Delivery.Event.EventID != m6.Event.EventID {
		t.Fatalf("root3 did not receive M6 sent to role/alpha/root: %+v %v", nx6, err)
	}
}

func TestCoordinatorHandoffBusyGuardAuthorizationAndSensitivityIsolation(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)

	root := f.register(nativeAgent{Binding: "guard-root"})
	lead := f.register(nativeAgent{Binding: "guard-lead"})
	outsider := f.register(nativeAgent{Binding: "guard-outsider"})

	rootView := f.view(sessionRef(root))
	leadView := f.view(sessionRef(lead))
	outsiderView := f.view(sessionRef(outsider))

	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "guards", RootAgent: root.Inbox, Sensitivity: "shareable",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	if _, err := rootView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "guards", Member: lead.Inbox, Lead: root.Inbox, Role: "lead",
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	// Subordinate lead and outsider cannot record coordinator compactions or trigger coordinator handoff.
	_, err := leadView.RecordCoordinatorCompaction(ctx, RecordCoordinatorCompactionRequest{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "guards"}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = outsiderView.HandoffCampaignCoordinator(ctx, HandoffCampaignCoordinatorRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "guards",
		NewSession: &NewCoordinatorSessionSpec{Binding: "guard-root", NativeSessionID: "rogue-1"},
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	// Active unexpired leased delivery on root blocks handoff with AGENT_BUSY until settled!
	if _, err := leadView.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "guards", OpKey: "busy-msg", Body: "Leased message", Shareable: true,
		Publication: PublishEventRequest{Kind: "notice", Destination: EventDestination{Type: "agent", Name: "role/guards/root"}},
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	nxBusy, err := rootView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || nxBusy.Delivery == nil {
		t.Fatalf("root NextEvent: %+v %v", nxBusy, err)
	}
	_, err = rootView.HandoffCampaignCoordinator(ctx, HandoffCampaignCoordinatorRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "guards",
		NewSession: &NewCoordinatorSessionSpec{Binding: "guard-root", NativeSessionID: "next-1"},
	}, f.dest)
	requireCode(t, err, "AGENT_BUSY")
	if _, err := rootView.CampaignAck(ctx, CampaignAckRequest{
		Repo: f.repo, Campaign: "guards", OpKey: "ack-busy", DeliveryID: nxBusy.Delivery.DeliveryID, LeaseID: nxBusy.Delivery.LeaseID,
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	// Un-shareable handoff note on a shareable campaign is refused even from local destination.
	localDst := Destination{Name: "local", AllowLocal: true}
	_, err = rootView.HandoffCampaignCoordinator(ctx, HandoffCampaignCoordinatorRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "guards",
		NewSession: &NewCoordinatorSessionSpec{Binding: "guard-root", NativeSessionID: "note-check"},
		Note:       "Un-shareable note on shareable campaign", Shareable: false,
	}, localDst)
	requireCode(t, err, "DESTINATION_PROHIBITED")

	// Shareable event cannot address a local campaign's role inbox.
	localRoot := f.register(nativeAgent{Binding: "local-root", Dest: &localDst})
	localRootView, err := f.receiver.ForAgentSession(sessionRef(localRoot), localDst)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := localRootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "privcamp", RootAgent: localRoot.Inbox, Sensitivity: "local",
	}, localDst); err != nil {
		t.Fatal(err)
	}
	_, err = localRootView.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "privcamp", OpKey: "leak-attempt", Body: "Shareable msg to local role", Shareable: true,
		Publication: PublishEventRequest{Kind: "notice", Destination: EventDestination{Type: "agent", Name: "role/privcamp/root"}},
	}, localDst)
	requireCode(t, err, "DESTINATION_PROHIBITED")

	// Pending local-sensitivity inbox delivery blocks handoff to a hosted successor session!
	if _, err := leadView.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "guards", OpKey: "local-pending-msg", Body: "Local secret message", Shareable: false,
		Publication: PublishEventRequest{Kind: "notice", Destination: EventDestination{Type: "agent", Name: "role/guards/root"}},
	}, localDst); err != nil {
		t.Fatal(err)
	}
	_, err = rootView.HandoffCampaignCoordinator(ctx, HandoffCampaignCoordinatorRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "guards",
		NewSession: &NewCoordinatorSessionSpec{Binding: "guard-root", NativeSessionID: "hosted-succ"},
	}, f.dest)
	requireCode(t, err, "DESTINATION_PROHIBITED")
}

func TestCoordinatorHandoffResponseGroupInboundRequestAndPredecessorResumption(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)

	root1 := f.register(nativeAgent{Binding: "rg-coord", Native: "rg-sess-1"})
	leadA := f.register(nativeAgent{Binding: "rg-lead"})
	workerA := f.register(nativeAgent{Binding: "rg-worker"})
	root1View, leadAView, workerAView := f.view(sessionRef(root1)), f.view(sessionRef(leadA)), f.view(sessionRef(workerA))

	if _, err := root1View.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "rg", RootAgent: root1.Inbox, Sensitivity: "shareable",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	for _, m := range []SetTeamMemberRequest{
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "rg", Member: leadA.Inbox, Lead: root1.Inbox, Role: "lead"},
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "rg", Member: workerA.Inbox, Lead: root1.Inbox, Role: "lead"},
	} {
		if _, err := root1View.SetTeamMember(ctx, m, f.dest); err != nil {
			t.Fatal(err)
		}
	}
	for _, v := range []*Store{leadAView, workerAView} {
		if _, err := v.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: f.repo, Topic: "team.rg.members", Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	// 1) root1 dispatches a response-group request; 2) leadA sends an inbound request to role/rg/root before handoff.
	rgEv, err := root1View.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "request", Summary: "Group check",
		Ref:           RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:   EventDestination{Type: "topic", Name: "team.rg.members"},
		ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(10 * time.Minute).UTC(), PartialPolicy: "all"},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	inboundReq, err := leadAView.CampaignSend(ctx, CampaignSendRequest{
		Repo: f.repo, Campaign: "rg", OpKey: "lead-inbound-req", Body: "Need root approval", Shareable: true,
		Publication: PublishEventRequest{Kind: "request", Destination: EventDestination{Type: "agent", Name: "role/rg/root"}},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	// Handoff root1 -> root2 (partial metadata override on NewSession).
	ho, err := root1View.HandoffCampaignCoordinator(ctx, HandoffCampaignCoordinatorRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "rg",
		NewSession: &NewCoordinatorSessionSpec{Binding: "rg-coord", NativeSessionID: "rg-sess-2", Metadata: &AgentMetadata{Model: "gpt-5.4"}},
	}, f.dest)
	if err != nil || !ho.Performed || ho.ToSession.Metadata.Model != "gpt-5.4" || ho.ToSession.Metadata.DeliveryMode != "existing-session" {
		t.Fatalf("handoff with partial metadata failed: %+v %v", ho, err)
	}
	root2 := *ho.ToSession
	root2View := f.view(sessionRef(root2))
	// root2 completes the transferred inbound request and replies cleanly to leadA!
	nxIn, err := root2View.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || nxIn.Delivery == nil || nxIn.Delivery.Event.EventID != inboundReq.Event.EventID {
		t.Fatalf("root2 claim transferred inboundReq: %+v %v", nxIn, err)
	}
	if _, err := root2View.CampaignComplete(ctx, CampaignCompleteRequest{
		Repo: f.repo, Campaign: "rg", OpKey: "root2-comp-in", DeliveryID: nxIn.Delivery.DeliveryID, LeaseID: nxIn.Delivery.LeaseID,
		Body: "Approved by root2", Shareable: true,
	}, f.dest); err != nil {
		t.Fatalf("root2 CampaignComplete on transferred request failed: %v", err)
	}
	// leadA replies to role/rg/root and workerA replies to old root1.Inbox -> response group closes 'collected' and root2 inspects it!
	for i, responder := range []struct {
		v  *Store
		to string
	}{{leadAView, "role/rg/root"}, {workerAView, root1.Inbox}} {
		var rgDeliv *AgentDelivery
		for rgDeliv == nil {
			nx, nErr := responder.v.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
			if nErr != nil || nx.Delivery == nil {
				t.Fatalf("responder %d missing rgEv: %+v %v", i, nx, nErr)
			}
			if nx.Delivery.Event.EventID == rgEv.EventID {
				rgDeliv = nx.Delivery
			} else if _, aErr := responder.v.CampaignAck(ctx, CampaignAckRequest{Repo: f.repo, Campaign: "rg", OpKey: uuid.NewString(), DeliveryID: nx.Delivery.DeliveryID, LeaseID: nx.Delivery.LeaseID}, f.dest); aErr != nil {
				t.Fatal(aErr)
			}
		}
		if _, cErr := responder.v.CampaignComplete(ctx, CampaignCompleteRequest{
			Repo: f.repo, Campaign: "rg", OpKey: uuid.NewString(), DeliveryID: rgDeliv.DeliveryID, LeaseID: rgDeliv.LeaseID,
			Body: "Member done", Shareable: true, ReplyTo: responder.to,
		}, f.dest); cErr != nil {
			t.Fatalf("responder %d reply failed: %v", i, cErr)
		}
	}
	rgStatus, err := root2View.ResponseGroup(ctx, ResponseGroupQuery{EventID: rgEv.EventID}, f.dest)
	if err != nil || rgStatus.State != "collected" || rgStatus.Responded != 2 {
		t.Fatalf("expected collected response group via root2View, got %+v %v", rgStatus, err)
	}
	// If root1 binding later resumes a new execution, direct messages to root1.Inbox are NOT hijacked to root2, and root2 cannot inspect post-resumption events of root1!
	resumed1 := f.register(nativeAgent{Binding: "rg-coord", Native: "rg-sess-1"})
	resumed1View := f.view(sessionRef(resumed1))
	postResumeEv, err := resumed1View.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Summary: "Unrelated post-resumption event",
		Ref: RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version}, Destination: EventDestination{Type: "agent", Name: resumed1.Inbox},
	}, f.dest)
	if err != nil || postResumeEv.Destination.Name != resumed1.Inbox {
		t.Fatalf("expected post-resumption message to stay on resumed1.Inbox, got %+v %v", postResumeEv, err)
	}
	_, err = root2View.AgentEventStatus(ctx, EventStatusRequest{EventID: postResumeEv.EventID}, f.dest)
	requireCode(t, err, "NOT_FOUND")
}
