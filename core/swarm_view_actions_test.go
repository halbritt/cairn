package core

import (
	"context"
	"errors"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func swarmActionOperator(t *testing.T) *Store {
	t.Helper()
	return testStore(t, Channel{Principal: "local-uid:" + strconv.Itoa(os.Geteuid()), Operator: true})
}

func swarmActionPublish(t *testing.T, store *Store, source Record, dest Destination, req PublishEventRequest) AgentEvent {
	t.Helper()
	req.RequestID = uuid.NewString()
	req.Repo = source.Scope.Repo
	req.Ref = RecordVersionRef{source.RecordID, source.Version}
	event, err := store.PublishEvent(context.Background(), req, dest)
	if err != nil {
		t.Fatalf("publish %s: %v", req.Kind, err)
	}
	return event
}

func TestSwarmActionFabricOperatorReceivesReplies(t *testing.T) {
	ctx := context.Background()
	configuration, _, sender, source, dest, worker := poolFixture(t)
	op := swarmActionOperator(t)
	deadline := time.Now().Add(time.Hour)
	managed := swarmActionPublish(t, op, source, dest, PublishEventRequest{
		Kind: "request", Destination: EventDestination{"pool", "coding"},
		Pool: &PoolRequirements{Workspace: worker.Spec.Workspace}, TaskDeadline: &deadline, CorrelationID: uuid.NewString(),
	})
	if attempt := poolClaim(t, sender, worker, dest); attempt == nil || attempt.Delivery.Event.EventID != managed.EventID {
		t.Fatalf("managed attempt: %+v", attempt)
	}
	fixtureFor(t, sender).pastDeadline(managed.EventID)
	if _, err := configuration.SweepRequestControls(ctx, RequestControlSweep{Repo: source.Scope.Repo}); err != nil {
		t.Fatal(err)
	}
	request := swarmActionPublish(t, op, source, dest, PublishEventRequest{
		Kind: "request", Destination: EventDestination{"agent", sender.channel.Principal}, CorrelationID: uuid.NewString(),
	})
	for _, tc := range []struct {
		name        string
		cause       string
		correlation string
	}{
		{"direct", request.EventID, request.CorrelationID},
		{"without correlation", request.EventID, ""},
		{"past task deadline", managed.EventID, managed.CorrelationID},
	} {
		t.Run(tc.name, func(t *testing.T) {
			reply := swarmActionPublish(t, sender, source, dest, PublishEventRequest{
				Kind: "response", Destination: EventDestination{"agent", op.channel.Principal},
				CausationID: tc.cause, CorrelationID: tc.correlation,
			})
			got, err := op.AgentEventStatus(ctx, EventStatusRequest{EventID: reply.EventID}, dest)
			if err != nil || len(got.Deliveries) != 1 || got.Deliveries[0].Consumer != op.channel.Principal || got.Deliveries[0].State != "pending" {
				t.Fatalf("operator's reply delivery: %+v %v", got, err)
			}
		})
	}
}

func TestSwarmActionFabricOperatorClaimsAndCompletesInOrder(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	op := swarmActionOperator(t)
	var replies []AgentEvent
	for range 2 {
		e := swarmActionPublish(t, op, f.source, f.dest, PublishEventRequest{
			Kind: "request", Destination: EventDestination{"agent", f.receiver.channel.Principal},
		})
		replies = append(replies, swarmActionPublish(t, f.receiver, f.source, f.dest, PublishEventRequest{
			Kind: "response", Destination: EventDestination{"agent", op.channel.Principal}, CausationID: e.EventID,
		}))
	}
	for _, want := range replies {
		next, err := op.NextEvent(ctx, NextEventRequest{Repo: f.repo}, f.dest)
		if err != nil || next.Delivery == nil || next.Delivery.Event.EventID != want.EventID {
			t.Fatalf("claim of %s: %+v %v", want.EventID, next, err)
		}
		done, err := op.CompleteEvent(ctx, CompleteEventRequest{
			RequestID: uuid.NewString(), DeliveryID: next.Delivery.DeliveryID, LeaseID: next.Delivery.LeaseID, Disposition: "handled",
		}, f.dest)
		if err != nil || done.State != "handled" || done.Event.EventID != want.EventID {
			t.Fatalf("complete: %+v %v", done, err)
		}
	}
	next, err := op.NextEvent(ctx, NextEventRequest{Repo: f.repo}, f.dest)
	if err != nil || next.Delivery != nil {
		t.Fatalf("empty inbox: %+v %v", next, err)
	}
}

func TestSwarmActionFabricUnreadOperatorReplyKeepsOrdinaryCapacity(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	op := swarmActionOperator(t)
	enablePublicEnforcePolicy(t, op, f.repo, func(l *EventAdmissionLimits) {
		l.MaxCollectionOutstanding = 1
		l.MaxProducerRecipientOutstanding = 1
	})
	request := swarmActionPublish(t, op, f.source, f.dest, PublishEventRequest{
		Kind: "request", Destination: EventDestination{"agent", f.receiver.channel.Principal},
	})
	d := nextFixture(t, f.receiver, f.dest)
	if _, err := f.receiver.CompleteEvent(ctx, CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: d.DeliveryID, LeaseID: d.LeaseID, Disposition: "handled",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	reply := swarmActionPublish(t, f.receiver, f.source, f.dest, PublishEventRequest{
		Kind: "response", Destination: EventDestination{"agent", op.channel.Principal}, CausationID: request.EventID,
	})
	// The sender can still use its one ordinary slot, even to the same
	// recipient. The reply has a reservation and remains unread.
	swarmActionPublish(t, f.receiver, f.source, f.dest, PublishEventRequest{
		Kind: "notice", Destination: EventDestination{"agent", op.channel.Principal}, Action: "review",
	})
	got, err := op.AgentEventStatus(ctx, EventStatusRequest{EventID: reply.EventID}, f.dest)
	if err != nil || len(got.Deliveries) != 1 || got.Deliveries[0].Attempts != 0 || got.Deliveries[0].State != "pending" {
		t.Fatalf("reply is not unread: %+v %v", got, err)
	}
}

func TestSwarmActionCancelPreviewGroupAndWaits(t *testing.T) {
	ctx := context.Background()
	op, open := waitTestDB(t)
	_, profile, root, first, publisher, firstView, dest := waitFixture(t, open)
	repo := root.Repo
	const campaign = "swarm-action-preview"
	if _, err := publisher.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: repo, Campaign: campaign, RootAgent: root.Inbox,
		OutOfChainPolicy: "allow", Sensitivity: "shareable",
	}, dest); err != nil {
		t.Fatal(err)
	}
	if _, err := op.SetCoordinationWaitsEnabled(ctx, WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatal(err)
	}
	source, err := publisher.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{
		Kind: "note", ClaimType: "self", Sensitivity: "shareable", Body: "Group work request",
		Scope: Scope{Repo: repo, TaskID: "*", RunID: "*"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	const topic = "swarm-action-recipients"
	for i, name := range []string{"first", "second", "third"} {
		member, view := first, firstView
		if i > 0 {
			member, err = profile.RegisterAgent(ctx, RegisterAgentRequest{
				RequestID: uuid.NewString(), Binding: name, NativeSessionID: name,
				Metadata: existingSessionMetadata("codex", "idle"),
			}, dest)
			if err != nil {
				t.Fatal(err)
			}
			view, err = profile.ForAgentSession(sessionRef(member), dest)
			if err != nil {
				t.Fatal(err)
			}
		}
		if _, err := publisher.SetTeamMember(ctx, SetTeamMemberRequest{
			RequestID: uuid.NewString(), Repo: repo, Campaign: campaign,
			Member: member.Inbox, Lead: root.Inbox, Role: "worker",
		}, dest); err != nil {
			t.Fatal(err)
		}
		if _, err := view.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: topic, Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	group := swarmActionPublish(t, publisher, source, dest, PublishEventRequest{
		Kind: "request", Destination: EventDestination{"topic", topic},
		ResponseGroup: &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: "all"},
	})
	wait := func(event AgentEvent, predicate string) AgentWait {
		t.Helper()
		w, err := publisher.CreateWait(ctx, CreateWaitRequest{
			RequestID: uuid.NewString(), Collection: repo, PredicateKind: predicate,
			DependencyEventUUID: event.EventID, Deadline: time.Now().Add(2 * time.Hour),
		}, dest)
		if err != nil {
			t.Fatal(err)
		}
		return w
	}
	closure := wait(group, WaitPredicateResponseGroupClosed)
	swarmActionPublish(t, firstView, source, dest, PublishEventRequest{
		Kind: "response", Destination: EventDestination{"agent", root.Inbox},
		CausationID: group.EventID, CorrelationID: group.CorrelationID,
	})
	detail, err := op.GetSwarmEvent(ctx, repo, campaign, group.EventID)
	if err != nil || len(detail.Deliveries) != 3 {
		t.Fatalf("group deliveries: %+v %v", detail, err)
	}
	var selected string
	for _, d := range detail.Deliveries {
		if d.Consumer == first.Inbox {
			selected = d.DeliveryID
		}
	}
	if selected == "" {
		t.Fatal("missing selected delivery")
	}
	req := SwarmCancelPreviewRequest{Repo: repo, Campaign: campaign, DeliveryID: selected}
	before, err := op.GetSwarmCancelPreview(ctx, req)
	if err != nil || !before.Cancellable || before.Refusal != nil || before.Delivery.Consumer != first.Inbox || len(before.Siblings) != 2 {
		t.Fatalf("recipient and siblings: %+v %v", before, err)
	}
	if before.Group == nil || before.Group.State != "open" || before.Group.Expected != 3 || before.Group.Responded != 1 ||
		!before.Group.RecipientReplyCounts || before.Group.ClosureWaits.Status != "found" ||
		len(before.Group.ClosureWaits.Waits) != 1 || before.Group.ClosureWaits.Waits[0].WaitID != closure.WaitUUID ||
		before.Group.ClosureWaits.Waits[0].State != "waiting" || len(before.Group.ClosureWaits.Waits[0].Awaited) != 2 ||
		before.DirectReplyWaits.Status != "empty" {
		t.Fatalf("group and waits: %+v %+v", before.Group, before.DirectReplyWaits)
	}
	if _, err := op.CancelWork(ctx, CancelWorkRequest{
		RequestID: uuid.NewString(), Repo: repo, DeliveryID: selected, Reason: "The recipient no longer needs to act",
	}); err != nil {
		t.Fatal(err)
	}
	after, err := op.GetSwarmCancelPreview(ctx, req)
	if err != nil || after.Cancellable || after.Refusal == nil || after.Refusal.Code != "VERSION_CONFLICT" ||
		after.Delivery.Code != "operator_cancelled" || !reflect.DeepEqual(before.Group, after.Group) || !reflect.DeepEqual(before.Siblings, after.Siblings) {
		t.Fatalf("cancel affected another row: before %+v after %+v, err %v", before, after, err)
	}

	// A direct wait with the same correlation as the group must not
	// appear on the grouped event.
	direct := swarmActionPublish(t, publisher, source, dest, PublishEventRequest{
		Kind: "request", Destination: EventDestination{"agent", first.Inbox}, CorrelationID: group.CorrelationID,
	})
	directWait := wait(direct, WaitPredicateRequestResponse)
	detail, err = op.GetSwarmEvent(ctx, repo, campaign, direct.EventID)
	if err != nil || len(detail.Deliveries) != 1 {
		t.Fatalf("direct delivery: %+v %v", detail, err)
	}
	req.DeliveryID = detail.Deliveries[0].DeliveryID
	preview, err := op.GetSwarmCancelPreview(ctx, req)
	if err != nil || preview.Group != nil || preview.DirectReplyWaits.Status != "found" ||
		len(preview.DirectReplyWaits.Waits) != 1 || preview.DirectReplyWaits.Waits[0].WaitID != directWait.WaitUUID {
		t.Fatalf("exact dependency wait: %+v %v", preview, err)
	}
	req.DeliveryID = selected
	preview, err = op.GetSwarmCancelPreview(ctx, req)
	if err != nil || preview.DirectReplyWaits.Status != "empty" {
		t.Fatalf("correlation incorrectly matched a wait: %+v %v", preview, err)
	}

	// Simulate an unavailable relation only in this disposable database.
	if _, err := op.pool.Exec(ctx, `ALTER TABLE cairn.agent_wait RENAME TO swarm_action_fixture_wait_offline`); err != nil {
		t.Fatal(err)
	}
	preview, err = op.GetSwarmCancelPreview(ctx, req)
	if err != nil || preview.Group == nil || preview.Group.ClosureWaits.Status != "unavailable" ||
		preview.DirectReplyWaits.Status != "unavailable" || preview.DirectReplyWaits.Reason == "" {
		t.Fatalf("unavailable waits: %+v %v", preview, err)
	}
}

func TestSwarmActionCancelPreviewRefusesLiveLeases(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "swarm-action-live-lease")
	member, view := f.member("swarm-action-lease-member")
	for _, kind := range []string{"request", "notice"} {
		t.Run(kind, func(t *testing.T) {
			req := f.noticeRequest(member.Inbox)
			if kind == "request" {
				req.Kind, req.Action = kind, ""
			}
			event, err := f.rootView.PublishEvent(ctx, req, f.dest)
			if err != nil {
				t.Fatal(err)
			}
			lease := nextFixture(t, view, f.dest)
			if lease.Event.EventID != event.EventID {
				t.Fatalf("wrong test lease: %+v", lease)
			}
			id := lease.DeliveryID
			preview, err := f.op.GetSwarmCancelPreview(ctx, SwarmCancelPreviewRequest{Repo: f.repo, Campaign: f.campaign, DeliveryID: id})
			if err != nil || preview.Cancellable || preview.Refusal == nil || preview.Refusal.Code != "UNSUPPORTED_CONTROL" ||
				preview.DirectReplyWaits.Status != "empty" || preview.DirectReplyWaits.Waits == nil {
				t.Fatalf("live lease preview: %+v %v", preview, err)
			}
			_, err = f.op.CancelWork(ctx, CancelWorkRequest{
				RequestID: uuid.NewString(), Repo: f.repo, DeliveryID: id, Reason: "The running delivery cannot be cancelled",
			})
			var refusal *Error
			if !errors.As(err, &refusal) || refusal.Code != preview.Refusal.Code || refusal.Message != preview.Refusal.Message {
				t.Fatalf("preview differs from store: %+v %v", preview.Refusal, err)
			}
			if _, err := view.CompleteEvent(ctx, CompleteEventRequest{
				RequestID: uuid.NewString(), DeliveryID: id, LeaseID: lease.LeaseID, Disposition: "handled",
			}, f.dest); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestSwarmActionSendRejectsCrossCampaignHandoff(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "swarm-action-origin")
	former, formerView := f.member("action-former-coordinator")
	inactive := false
	if _, err := f.rootView.SetTeamMember(ctx, SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign,
		Member: former.Inbox, Lead: f.root.Inbox, Role: "worker", Active: &inactive,
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	const away = "swarm-action-away"
	if _, err := formerView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: away,
		RootAgent: former.Inbox, OutOfChainPolicy: "allow", Sensitivity: "shareable",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	handoff, err := formerView.HandoffCampaignCoordinator(ctx, HandoffCampaignCoordinatorRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: away,
		NewSession: &NewCoordinatorSessionSpec{Binding: "action-former-coordinator", NativeSessionID: "action-away-successor"},
		Note:       "Use the new coordinator session", Shareable: true,
	}, f.dest)
	if err != nil || !handoff.Performed || handoff.ToSession == nil {
		t.Fatalf("handoff fixture: %+v %v", handoff, err)
	}
	req := SwarmSendRequest{
		SwarmActionRequest: SwarmActionRequest{Repo: f.repo, Campaign: f.campaign, RequestID: uuid.NewString()},
		To:                 former.Inbox, Kind: "request", Body: "Intended for the selected campaign",
	}
	_, err = f.op.SendSwarmEvent(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
	if !strings.Contains(err.Error(), "publication would be routed outside this campaign") {
		t.Fatalf("expected refusal of the resolved successor: %v", err)
	}
	swarmActionAssertSourceCount(t, f.op, req, 0)
	succ := f.view(sessionRef(*handoff.ToSession))
	next, err := succ.NextEvent(ctx, NextEventRequest{Repo: f.repo}, f.dest)
	if err != nil || next.Delivery != nil {
		t.Fatalf("refused send reached the other campaign: %+v %v", next, err)
	}

	// The same handoff is valid when the send targets its own campaign.
	req.Campaign = away
	got, err := f.op.SendSwarmEvent(ctx, req)
	if err != nil || len(got.Deliveries) != 1 || got.Deliveries[0].Consumer != handoff.ToPrincipal ||
		got.Event.Routing == nil || got.Event.Routing.Campaign != away {
		t.Fatalf("same-campaign handoff: %+v %v", got, err)
	}
	swarmActionAssertSourceCount(t, f.op, req, 1)
}

func swarmActionAssertSourceCount(t *testing.T, op *Store, req SwarmSendRequest, want int) {
	t.Helper()
	var count int
	scope := "coordination/swarm/" + req.Campaign + "/" + req.RequestID
	err := op.pool.QueryRow(context.Background(), `SELECT count(*) FROM cairn.record_version
		WHERE repo=$1 AND task_id=$2`, req.Repo, scope).Scan(&count)
	if err != nil || count != want {
		t.Fatalf("retained %d send source records, want %d: %v", count, want, err)
	}
}

func TestSwarmActionSendRefusalsRollBackShareableSource(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "swarm-action-refused-send")
	req := SwarmSendRequest{
		SwarmActionRequest: SwarmActionRequest{Repo: f.repo, Campaign: f.campaign, RequestID: uuid.NewString()},
		To:                 f.root.Inbox, Kind: "request", Body: strings.Repeat("x", MaxEventSummaryBytes+1),
	}
	_, err := f.op.SendSwarmEvent(ctx, req)
	requireCode(t, err, "INVALID_REQUEST")
	swarmActionAssertSourceCount(t, f.op, req, 0)

	req.Body = "Corrected message"
	got, err := f.op.SendSwarmEvent(ctx, req)
	if err != nil || len(got.Deliveries) != 1 || got.Deliveries[0].Consumer != f.root.Inbox {
		t.Fatalf("corrected send with the same request UUID: %+v %v", got, err)
	}
	swarmActionAssertSourceCount(t, f.op, req, 1)

	local := Destination{Name: "local", AllowLocal: true}
	localRoot := f.register(nativeAgent{Binding: "swarm-action-local-root", Dest: &local})
	localView, err := f.receiver.ForAgentSession(sessionRef(localRoot), local)
	if err != nil {
		t.Fatal(err)
	}
	const localCampaign = "swarm-action-local"
	if _, err := localView.SetTeamCampaign(ctx, SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: localCampaign,
		RootAgent: localRoot.Inbox, OutOfChainPolicy: "allow", Sensitivity: "local",
	}, local); err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	req.Campaign = localCampaign
	req.To = campaignRootRoleAddress(localCampaign)
	_, err = f.op.SendSwarmEvent(ctx, req)
	requireCode(t, err, "DESTINATION_PROHIBITED")
	swarmActionAssertSourceCount(t, f.op, req, 0)
}
