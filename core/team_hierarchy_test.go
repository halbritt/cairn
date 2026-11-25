package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestTeamHierarchyAcceptanceRoutineToLeadAndBlockerEscalationOrMatchingSubscription(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	root := f.register(nativeAgent{Binding: "root-coord"})
	leadA := f.register(nativeAgent{Binding: "lead-a"})
	leadB := f.register(nativeAgent{Binding: "lead-b"})
	workerA := f.register(nativeAgent{Binding: "worker-a"})
	workerB := f.register(nativeAgent{Binding: "worker-b"})

	rootView := f.view(sessionRef(root))
	leadAView := f.view(sessionRef(leadA))
	leadBView := f.view(sessionRef(leadB))
	workerAView := f.view(sessionRef(workerA))
	workerBView := f.view(sessionRef(workerB))

	camp, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha",
		RootAgent: root.Inbox, OutOfChainPolicy: "warn",
	}, f.dest)
	if err != nil || camp.Revision != 1 || len(camp.Members) != 1 {
		t.Fatalf("SetTeamCampaign failed: %+v %v", camp, err)
	}
	for _, m := range []SetTeamMemberRequest{
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha", Member: leadA.Inbox, Lead: root.Inbox, Role: "lead"},
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha", Member: leadB.Inbox, Lead: root.Inbox, Role: "lead"},
	} {
		if _, err := rootView.SetTeamMember(ctx, m, f.dest); err != nil {
			t.Fatalf("root SetTeamMember(%s): %v", m.Member, err)
		}
	}
	// Spawned workers self-enroll under their assigned sub-leads; once enrolled, sub-leads manage them without repeating Lead.
	if _, err := workerAView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha", Member: workerA.Inbox, Lead: leadA.Inbox, Role: "worker",
	}, f.dest); err != nil {
		t.Fatalf("workerA self-enroll: %v", err)
	}
	if _, err := leadAView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha", Member: workerA.Inbox, Role: "worker",
	}, f.dest); err != nil {
		t.Fatalf("leadA manage enrolled workerA with omitted Lead: %v", err)
	}
	if _, err := workerBView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha", Member: workerB.Inbox, Lead: leadB.Inbox, Role: "worker",
	}, f.dest); err != nil {
		t.Fatalf("workerB self-enroll: %v", err)
	}

	// 1. Topic routing: leads subscribe to direct reports; root subscribes ONLY to decide actions & requests (not blockers yet).
	topic := "team.alpha"
	if _, err := leadAView.SetSubscription(ctx, SubscriptionRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Topic: topic, Active: true,
		Filter: &SubscriptionFilter{DirectReportsOnly: true},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := leadBView.SetSubscription(ctx, SubscriptionRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Topic: topic, Active: true,
		Filter: &SubscriptionFilter{DirectReportsOnly: true},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := rootView.SetSubscription(ctx, SubscriptionRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Topic: topic, Active: true,
		Filter: &SubscriptionFilter{Actions: []string{"decide", "complete"}},
	}); err != nil {
		t.Fatal(err)
	}

	srcRef := RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version}
	assertEmpty := func(label string, v *Store) {
		t.Helper()
		nx, nErr := v.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
		if nErr != nil || nx.Delivery != nil {
			t.Fatalf("expected %s inbox empty, got %+v %v", label, nx, nErr)
		}
	}
	claimAndAck := func(label string, v *Store, wantEventID string) AgentDelivery {
		t.Helper()
		nx, nErr := v.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
		if nErr != nil || nx.Delivery == nil || nx.Delivery.Event.EventID != wantEventID {
			t.Fatalf("expected %s to claim %s, got %+v %v", label, wantEventID, nx, nErr)
		}
		if _, cErr := v.CompleteEvent(ctx, CompleteEventRequest{
			RequestID: uuid.NewString(), DeliveryID: nx.Delivery.DeliveryID, LeaseID: nx.Delivery.LeaseID, Disposition: "handled",
		}, f.dest); cErr != nil {
			t.Fatal(cErr)
		}
		return *nx.Delivery
	}

	// WorkerA routine notice on topic reaches leadA ONLY — neither leadB nor root!
	routineEv, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review", Summary: "Routine worker progress",
		Ref: srcRef, Destination: EventDestination{Type: "topic", Name: topic},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	claimAndAck("leadA routine", leadAView, routineEv.EventID)
	assertEmpty("leadB on workerA routine", leadBView)
	assertEmpty("root on workerA routine", rootView)

	// WorkerA blocker on topic does NOT reach root while root's subscription excludes blockers; reaches leadA.
	unmatchedBlocker, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review", Blocker: true, Summary: "Worker hit schema lock blocker",
		Ref: srcRef, Destination: EventDestination{Type: "topic", Name: topic},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	claimAndAck("leadA blocker", leadAView, unmatchedBlocker.EventID)
	assertEmpty("root before escalation", rootView)

	// LeadA escalates the blocker to root (either via --action decide on topic or direct --to root).
	escalated, err := leadAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "decide", Blocker: true,
		Summary: "LeadA escalating schema lock blocker", CausationID: unmatchedBlocker.EventID,
		Ref: srcRef, Destination: EventDestination{Type: "topic", Name: topic},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	claimAndAck("root on leadA escalation", rootView, escalated.EventID)

	// Now root updates subscription to include blockers (Actions: decide/complete OR IncludeBlockers: true).
	if _, err := rootView.SetSubscription(ctx, SubscriptionRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Topic: topic, Active: true,
		Filter: &SubscriptionFilter{Actions: []string{"decide", "complete"}, IncludeBlockers: true},
	}); err != nil {
		t.Fatal(err)
	}
	routineEv2, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review", Summary: "Second routine progress",
		Ref: srcRef, Destination: EventDestination{Type: "topic", Name: topic},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	claimAndAck("leadA routine 2", leadAView, routineEv2.EventID)
	assertEmpty("root still ignores routine 2", rootView)

	matchedBlocker, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review", Blocker: true, Summary: "Urgent CI blocker",
		Ref: srcRef, Destination: EventDestination{Type: "topic", Name: topic},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	claimAndAck("leadA matched blocker", leadAView, matchedBlocker.EventID)
	claimAndAck("root matched blocker", rootView, matchedBlocker.EventID)

	// 2. Direct out-of-chain publication: warn policy vs reroute policy vs reply closure exemption.
	warnEv, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review", Summary: "Out of chain under warn",
		Ref: srcRef, Destination: EventDestination{Type: "agent", Name: root.Inbox},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	if warnEv.Routing == nil || !warnEv.Routing.OutOfChain || warnEv.Routing.Rerouted || warnEv.Routing.Lead != leadA.Inbox {
		t.Fatalf("expected out-of-chain warning without reroute, got %+v", warnEv.Routing)
	}
	claimAndAck("root under warn policy", rootView, warnEv.EventID)

	// Switch campaign policy to reroute.
	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "alpha",
		OutOfChainPolicy: "reroute", ExpectedRevision: 1,
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	reroutedEv, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review", Summary: "Worker trying to message root directly",
		Ref: srcRef, Destination: EventDestination{Type: "agent", Name: root.Inbox},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	if reroutedEv.Destination.Name != leadA.Inbox || reroutedEv.Routing == nil || !reroutedEv.Routing.Rerouted ||
		reroutedEv.Routing.OriginalDestination == nil || reroutedEv.Routing.OriginalDestination.Name != root.Inbox {
		t.Fatalf("expected reroute to leadA, got dest=%+v routing=%+v", reroutedEv.Destination, reroutedEv.Routing)
	}
	assertEmpty("root after rerouted worker send", rootView)
	claimAndAck("leadA receives rerouted worker send", leadAView, reroutedEv.EventID)

	// Root sending directly to workerA (skipping leadA) reroutes through leadA; leadA sending to root is in-chain.
	downReroute, err := rootView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review", Summary: "Root downward skip",
		Ref: srcRef, Destination: EventDestination{Type: "agent", Name: workerA.Inbox},
	}, f.dest)
	if err != nil || downReroute.Destination.Name != leadA.Inbox || downReroute.Routing == nil || !downReroute.Routing.Rerouted {
		t.Fatalf("expected downward reroute through leadA, got %+v %v", downReroute, err)
	}
	claimAndAck("leadA receives downward reroute", leadAView, downReroute.EventID)
	assertEmpty("workerA did not receive skipped downward notice", workerAView)

	// Validated reply closure is exempt from rerouting when answering an explicit topic request from root!
	workerReqTopic := "direct.round"
	if _, err := workerAView.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Repo: f.repo, Topic: workerReqTopic, Active: true}); err != nil {
		t.Fatal(err)
	}
	rootReq, err := rootView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "request", Summary: "Root grouped request",
		Ref: srcRef, Destination: EventDestination{Type: "topic", Name: workerReqTopic},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	wClaim, err := workerAView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || wClaim.Delivery == nil {
		t.Fatalf("workerA claim request: %+v %v", wClaim, err)
	}
	resDraft := projectNote(f.repo)
	resDraft.Sensitivity = "shareable"
	resDraft.Body = "WorkerA completed result"
	completed, err := workerAView.CompleteEvent(ctx, CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: wClaim.Delivery.DeliveryID, LeaseID: wClaim.Delivery.LeaseID,
		Disposition: "handled", Draft: &resDraft,
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	replyEv, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "response", CausationID: rootReq.EventID,
		Ref: *completed.Result, Destination: EventDestination{Type: "agent", Name: root.Inbox},
	}, f.dest)
	if err != nil || replyEv.Destination.Name != root.Inbox || replyEv.Routing != nil {
		t.Fatalf("reply closure should bypass reroute and reach root directly: %+v %v", replyEv, err)
	}
	claimAndAck("root receives direct reply closure", rootView, replyEv.EventID)
}

func TestTeamHierarchyAuthorizationCyclesAndSensitivityIsolation(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	localDest := Destination{Name: "local", AllowLocal: true}
	root := f.register(nativeAgent{Binding: "auth-root"})
	leadA := f.register(nativeAgent{Binding: "auth-lead-a"})
	leadB := f.register(nativeAgent{Binding: "auth-lead-b"})
	workerA := f.register(nativeAgent{Binding: "auth-worker-a"})
	workerB := f.register(nativeAgent{Binding: "auth-worker-b"})

	rootView := f.view(sessionRef(root))
	leadAView := f.view(sessionRef(leadA))
	workerAView := f.view(sessionRef(workerA))

	// Rogue worker cannot create a campaign naming root as root_agent.
	_, err := workerAView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", RootAgent: root.Inbox,
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", RootAgent: root.Inbox, OutOfChainPolicy: "warn",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	for _, m := range []SetTeamMemberRequest{
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", Member: leadA.Inbox, Lead: root.Inbox, Role: "lead"},
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", Member: leadB.Inbox, Lead: root.Inbox, Role: "lead"},
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", Member: workerA.Inbox, Lead: leadA.Inbox, Role: "worker"},
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", Member: workerB.Inbox, Lead: leadB.Inbox, Role: "worker"},
	} {
		if _, err := rootView.SetTeamMember(ctx, m, f.dest); err != nil {
			t.Fatal(err)
		}
	}

	// Rogue worker cannot modify campaign or reassign members; leadA cannot hijack leadB's workerB!
	_, err = workerAView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", OutOfChainPolicy: "allow",
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = leadAView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", Member: workerB.Inbox, Lead: leadA.Inbox, Role: "worker",
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	// Cycle detection (leadA -> leadB -> leadA) and active-subordinate guard on deactivate.
	if _, err := rootView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", Member: leadB.Inbox, Lead: leadA.Inbox, Role: "lead",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	_, err = rootView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", Member: leadA.Inbox, Lead: leadB.Inbox, Role: "lead",
	}, f.dest)
	requireCode(t, err, "INVALID_REQUEST")
	inactive := false
	_, err = rootView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", Member: leadA.Inbox, Active: &inactive,
	}, f.dest)
	requireCode(t, err, "INVALID_REQUEST")

	// Principal cannot belong to a second active campaign simultaneously; deactivating campaign frees members.
	_, err = leadAView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec-two", RootAgent: leadA.Inbox,
	}, f.dest)
	requireCode(t, err, "VERSION_CONFLICT")
	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", Active: &inactive,
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	// Local-sensitivity campaign is hidden from hosted destination, cannot be overwritten or probed via SetTeamCampaign,
	// never reroutes shareable events or leaks edges on shareable topic fanout, and preserves subordinate members across pause/resume.
	victim := f.register(nativeAgent{Binding: "auth-victim"})
	// Sub-lead cannot unilaterally conscript an unconsenting third-party agent!
	activeFlag := true
	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", Active: &activeFlag,
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	resumed, err := rootView.GetTeamCampaign(ctx, GetTeamCampaignRequest{Repo: f.repo, Campaign: "sec"}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	activeCount := 0
	for _, m := range resumed.Members {
		if m.Active {
			activeCount++
		}
	}
	if activeCount != 5 {
		t.Fatalf("expected all 5 members preserved as active after campaign pause/resume, got %d (%+v)", activeCount, resumed.Members)
	}
	_, err = leadAView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", Member: victim.Inbox, Lead: leadA.Inbox, Role: "worker",
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	// Enrolled worker cannot unilaterally escape or modify its own membership.
	_, err = workerAView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", Member: workerA.Inbox, Active: &inactive,
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	// Direct request under reroute policy warns without rewriting Destination so request closures remain valid.
	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", OutOfChainPolicy: "reroute",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	reqWarn, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "request", Summary: "Direct request out of chain",
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: root.Inbox},
	}, f.dest)
	if err != nil || reqWarn.Destination.Name != root.Inbox || reqWarn.Routing == nil || !reqWarn.Routing.OutOfChain || reqWarn.Routing.Rerouted {
		t.Fatalf("expected direct request to warn without rerouting, got %+v %v", reqWarn, err)
	}
	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "sec", Active: &inactive,
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	if _, err := leadAView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "local-camp", RootAgent: leadA.Inbox,
		OutOfChainPolicy: "reroute", Sensitivity: "local",
	}, localDest); err != nil {
		t.Fatal(err)
	}
	if _, err := workerAView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "local-camp", Member: workerA.Inbox, Lead: leadA.Inbox, Role: "worker",
	}, localDest); err != nil {
		t.Fatal(err)
	}
	_, err = leadAView.GetTeamCampaign(ctx, GetTeamCampaignRequest{Repo: f.repo, Campaign: "local-camp"}, f.dest)
	requireCode(t, err, "NOT_FOUND")
	_, err = workerAView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "local-camp", RootAgent: leadA.Inbox,
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = leadAView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "local-camp", OutOfChainPolicy: "allow",
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	// Shareable event under local reroute campaign is NOT rerouted (does not leak local lead into shareable Destination) and redacts Routing on hosted reads & replay!
	pubReqID := uuid.NewString()
	evLocalPubShareable, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: pubReqID, Repo: f.repo, Kind: "notice", Action: "review",
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: root.Inbox},
	}, localDest)
	if err != nil || evLocalPubShareable.Destination.Name != root.Inbox || evLocalPubShareable.Routing == nil || evLocalPubShareable.Routing.Rerouted || evLocalPubShareable.Routing.LocalOnly {
		t.Fatalf("local caller publishing shareable event under local reroute campaign: got %+v %v", evLocalPubShareable, err)
	}
	// Replay of the exact same RequestID on hosted destination must redact Routing!
	evReplayHosted, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: pubReqID, Repo: f.repo, Kind: "notice", Action: "review",
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "agent", Name: root.Inbox},
	}, f.dest)
	if err != nil || evReplayHosted.Routing != nil {
		t.Fatalf("hosted replay leaked local campaign routing: %+v %v", evReplayHosted.Routing, err)
	}
	// Shareable topic publication must NOT match direct_reports_only from a local-sensitivity campaign!
	localSubTopic := "team.local.leakcheck"
	if _, err := leadAView.SetSubscription(ctx, SubscriptionRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Topic: localSubTopic, Active: true,
		Filter: &SubscriptionFilter{DirectReportsOnly: true},
	}); err != nil {
		t.Fatal(err)
	}
	shareableTopicEv, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review",
		Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{Type: "topic", Name: localSubTopic},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	stTopicHosted, err := workerAView.AgentEventStatus(ctx, EventStatusRequest{EventID: shareableTopicEv.EventID}, f.dest)
	if err != nil || len(stTopicHosted.Deliveries) != 0 {
		t.Fatalf("shareable topic publication leaked local campaign direct_reports_only edge: %+v %v", stTopicHosted.Deliveries, err)
	}
}

func TestMultiRecipientFanoutScopingSupersedesAndNonSuppression(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	root := f.register(nativeAgent{Binding: "fan-root"})
	leadA := f.register(nativeAgent{Binding: "fan-lead-a"})
	leadB := f.register(nativeAgent{Binding: "fan-lead-b"})
	workerA := f.register(nativeAgent{Binding: "fan-worker-a"})
	workerB := f.register(nativeAgent{Binding: "fan-worker-b"})
	unrelated := f.register(nativeAgent{Binding: "fan-unrelated"})

	rootView := f.view(sessionRef(root))
	leadAView := f.view(sessionRef(leadA))
	leadBView := f.view(sessionRef(leadB))
	workerAView := f.view(sessionRef(workerA))
	workerBView := f.view(sessionRef(workerB))
	unrelatedView := f.view(sessionRef(unrelated))

	if _, err := rootView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: "fanout", RootAgent: root.Inbox, OutOfChainPolicy: "reroute",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	for _, m := range []SetTeamMemberRequest{
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "fanout", Member: leadA.Inbox, Lead: root.Inbox, Role: "lead"},
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "fanout", Member: leadB.Inbox, Lead: root.Inbox, Role: "lead"},
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "fanout", Member: workerA.Inbox, Lead: leadA.Inbox, Role: "worker"},
		{RequestID: uuid.NewString(), Repo: f.repo, Campaign: "fanout", Member: workerB.Inbox, Lead: leadB.Inbox, Role: "worker"},
	} {
		if _, err := rootView.SetTeamMember(ctx, m, f.dest); err != nil {
			t.Fatal(err)
		}
	}
	topic := "team.fanout"
	if _, err := rootView.SetSubscription(ctx, SubscriptionRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Topic: topic, Active: true,
		Filter: DefaultCampaignSubscriptionFilter("root"),
	}); err != nil {
		t.Fatal(err)
	}
	for _, lv := range []*Store{leadAView, leadBView} {
		if _, err := lv.SetSubscription(ctx, SubscriptionRequest{
			RequestID: uuid.NewString(), Repo: f.repo, Topic: topic, Active: true,
			Filter: DefaultCampaignSubscriptionFilter("lead"),
		}); err != nil {
			t.Fatal(err)
		}
	}

	srcRef := RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version}
	// Identical retransmissions with same supersedes key reach leadA only and leave a single active pending delivery.
	ev1, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review",
		Supersedes: "workerA/progress", Summary: "Progress v1", Ref: srcRef,
		Destination: EventDestination{Type: "topic", Name: topic},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	ev2, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "review",
		Supersedes: "workerA/progress", Summary: "Progress v1", Ref: srcRef,
		Destination: EventDestination{Type: "topic", Name: topic},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	st1, err := workerAView.AgentEventStatus(ctx, EventStatusRequest{EventID: ev1.EventID}, f.dest)
	if err != nil || len(st1.Deliveries) != 1 || st1.Deliveries[0].State != "ignored" || st1.Deliveries[0].Code != "superseded" || st1.Deliveries[0].SupersededBy != ev2.EventID {
		t.Fatalf("expected ev1 superseded by ev2 for leadA only: %+v %v", st1, err)
	}
	// Irrelevant subscribers receive zero injected messages.
	for _, idleView := range []*Store{rootView, leadBView, workerBView, unrelatedView} {
		nx, nErr := idleView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
		if nErr != nil || nx.Delivery != nil {
			t.Fatalf("expected irrelevant subscriber empty, got %+v %v", nx, nErr)
		}
	}
	// Materially changed directive / blocker is NOT suppressed and reaches root + leadA immediately.
	blk, err := workerAView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Action: "decide", Blocker: true,
		Summary: "Conflict requires root decision", Ref: srcRef,
		Destination: EventDestination{Type: "topic", Name: topic},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	stBlk, err := workerAView.AgentEventStatus(ctx, EventStatusRequest{EventID: blk.EventID}, f.dest)
	if err != nil || len(stBlk.Deliveries) != 2 {
		t.Fatalf("expected blocker delivered to leadA and root, got %+v %v", stBlk, err)
	}
}
