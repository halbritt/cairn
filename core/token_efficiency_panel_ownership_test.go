package core

import (
	"context"
	"encoding/json"
	"os"
	"testing"

	"github.com/google/uuid"
)

type panelOwnershipEvent struct {
	ID        string `json:"id"`
	Kind      string `json:"kind"`
	Recipient string `json:"recipient"`
	Body      string `json:"body"`
}

type panelOwnershipCase struct {
	ID    string `json:"id"`
	Setup struct {
		Events      []panelOwnershipEvent `json:"events"`
		Transitions []struct {
			Phase  string `json:"phase"`
			Action string `json:"action"`
			Target string `json:"target"`
		} `json:"transitions"`
	} `json:"setup"`
}

type panelOwnershipFixture struct {
	Cases               []panelOwnershipCase `json:"cases"`
	EventSourceDefaults struct {
		Kind        string `json:"kind"`
		RecordClass string `json:"record_class"`
		Operation   string `json:"operation"`
		Sensitivity string `json:"sensitivity"`
		ClaimType   string `json:"claim_type"`
		Scope       Scope  `json:"scope"`
	} `json:"event_source_defaults"`
}

func readPanelOwnershipFixture(t *testing.T) panelOwnershipFixture {
	t.Helper()
	raw, err := os.ReadFile("../trials/token-efficiency/FIXTURE.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture panelOwnershipFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	defaults := fixture.EventSourceDefaults
	if defaults.Kind != "note" || defaults.RecordClass != "A" || defaults.Operation != "create" ||
		defaults.ClaimType != "self" || defaults.Sensitivity != "shareable" ||
		defaults.Scope.Repo != "fixture:cairn-token-efficiency" ||
		defaults.Scope.TaskID != "*" || defaults.Scope.RunID != "*" {
		t.Fatal("panel event source defaults no longer match native source contract")
	}
	return fixture
}

func (fixture panelOwnershipFixture) selectedCase(t *testing.T, name string) panelOwnershipCase {
	t.Helper()
	var selected *panelOwnershipCase
	for index := range fixture.Cases {
		if fixture.Cases[index].ID == name {
			if selected != nil {
				t.Fatalf("duplicate panel case %q", name)
			}
			selected = &fixture.Cases[index]
		}
	}
	if selected == nil {
		t.Fatalf("missing panel case %q", name)
	}
	return *selected
}

func (fixture panelOwnershipFixture) createSource(t *testing.T, sender *Store, event panelOwnershipEvent) RecordVersionRef {
	t.Helper()
	draft := Draft{Kind: fixture.EventSourceDefaults.Kind, Body: event.Body,
		Scope: fixture.EventSourceDefaults.Scope, ClaimType: fixture.EventSourceDefaults.ClaimType,
		Sensitivity: fixture.EventSourceDefaults.Sensitivity}
	record, err := sender.Create(context.Background(), CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil || record.Body != event.Body || record.Version != 1 ||
		record.ObservedWriter != sender.channel.Principal {
		t.Fatalf("source for %s: %+v %v", event.ID, record, err)
	}
	return RecordVersionRef{RecordID: record.RecordID, Version: record.Version}
}

func registerPanelReceiver(t *testing.T, receiver *Store, dest Destination) AgentInstance {
	t.Helper()
	sessionID := "panel-native-" + uuid.NewString()
	agent, err := receiver.RegisterAgent(context.Background(), RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: sessionID, NativeSessionID: sessionID,
		Metadata: AgentMetadata{Harness: "codex", Project: "fixture", Workspace: "/work/fixture",
			State: "idle", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	return agent
}

// These probes exercise native setup and a source-loss transition. They do not
// run a recipient model, perform the requested task, or grade its outcome.
func TestTokenEfficiencyPanelOwnershipAndSourceLoss(t *testing.T) {
	ctx := context.Background()
	fixture := readPanelOwnershipFixture(t)
	repo := fixture.EventSourceDefaults.Scope.Repo
	dest := Destination{Name: "hosted", AllowLocal: false}

	t.Run("concurrent_receivers", func(t *testing.T) {
		row := fixture.selectedCase(t, "concurrent_receivers")
		if len(row.Setup.Events) != 2 || len(row.Setup.Transitions) != 0 ||
			row.Setup.Events[0].ID != "request_a" || row.Setup.Events[0].Kind != "request" ||
			row.Setup.Events[0].Recipient != "worker_a" ||
			row.Setup.Events[1].ID != "request_b" || row.Setup.Events[1].Kind != "request" ||
			row.Setup.Events[1].Recipient != "worker_b" {
			t.Fatal("concurrent receiver fixture contract changed")
		}
		sender := testStore(t, Channel{Principal: "agent:panel-concurrent-sender-" + uuid.NewString(), Repo: repo})
		workerA := testStore(t, Channel{Principal: "agent:panel-worker-a-" + uuid.NewString(), Repo: repo})
		workerB := testStore(t, Channel{Principal: "agent:panel-worker-b-" + uuid.NewString(), Repo: repo})
		agentA := registerPanelReceiver(t, workerA, dest)
		agentB := registerPanelReceiver(t, workerB, dest)
		refs := []AgentSessionRef{{AgentID: agentA.AgentID, ExecutionID: agentA.ExecutionID},
			{AgentID: agentB.AgentID, ExecutionID: agentB.ExecutionID}}
		receivers := []*Store{workerA, workerB}
		inboxes := []string{agentA.Inbox, agentB.Inbox}
		events := make([]AgentEvent, 2)
		deliveries := make([]string, 2)
		for index, spec := range row.Setup.Events {
			source := fixture.createSource(t, sender, spec)
			published, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(),
				Kind: spec.Kind, Ref: source, Destination: EventDestination{Type: "agent", Name: inboxes[index]}}, dest)
			if err != nil || published.Ref != source || published.From != sender.channel.Principal {
				t.Fatalf("%s publication: %+v %v", spec.ID, published, err)
			}
			events[index] = published
			status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: published.EventID}, dest)
			if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].Consumer != inboxes[index] ||
				status.Deliveries[0].State != "pending" {
				t.Fatalf("%s delivery: %+v %v", spec.ID, status, err)
			}
			deliveries[index] = status.Deliveries[0].DeliveryID
		}
		_, err := workerA.SessionInboxReady(ctx, refs[1], dest)
		requireCode(t, err, "NOT_FOUND")
		wrong, err := workerA.ClaimSessionInbox(ctx, SessionInboxClaim{
			RequestID: uuid.NewString(), Session: refs[0], DeliveryID: deliveries[1],
			NativeTurnID: "panel-wrong-turn-" + uuid.NewString(), TurnExclusive: true}, dest)
		if err != nil || wrong.Attempt != nil {
			t.Fatalf("worker A claimed worker B's delivery: %+v %v", wrong, err)
		}
		untouched, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: events[1].EventID}, dest)
		if err != nil || len(untouched.Deliveries) != 1 || untouched.Deliveries[0].State != "pending" ||
			untouched.Deliveries[0].Attempts != 0 {
			t.Fatalf("wrong receiver changed worker B's delivery: %+v %v", untouched, err)
		}
		for index := range receivers {
			claimed, err := receivers[index].ClaimSessionInbox(ctx, SessionInboxClaim{
				RequestID: uuid.NewString(), Session: refs[index], DeliveryID: deliveries[index],
				NativeTurnID: "panel-turn-" + uuid.NewString(), TurnExclusive: true}, dest)
			if err != nil || claimed.Attempt == nil ||
				claimed.Attempt.Delivery.Event.EventID != events[index].EventID ||
				claimed.Attempt.Delivery.DeliveryID != deliveries[index] {
				t.Fatalf("worker %d claim: %+v %v", index, claimed, err)
			}
		}
		if events[0].EventID == events[1].EventID || deliveries[0] == deliveries[1] {
			t.Fatal("distinct requests collapsed across recipients")
		}
	})

	t.Run("source_forgotten_before_read", func(t *testing.T) {
		row := fixture.selectedCase(t, "source_forgotten_before_read")
		if len(row.Setup.Events) != 1 || row.Setup.Events[0].ID != "request_missing_source" ||
			row.Setup.Events[0].Kind != "request" || row.Setup.Events[0].Recipient != "worker" ||
			len(row.Setup.Transitions) != 1 || row.Setup.Transitions[0].Phase != "after_admission_before_read" ||
			row.Setup.Transitions[0].Action != "remove_source" ||
			row.Setup.Transitions[0].Target != row.Setup.Events[0].ID {
			t.Fatal("source-loss fixture contract changed")
		}
		sender := testStore(t, Channel{Principal: "agent:panel-forgotten-sender-" + uuid.NewString(), Repo: repo})
		receiver := testStore(t, Channel{Principal: "agent:panel-forgotten-worker-" + uuid.NewString(), Repo: repo})
		agent := registerPanelReceiver(t, receiver, dest)
		source := fixture.createSource(t, sender, row.Setup.Events[0])
		published, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(),
			Kind: row.Setup.Events[0].Kind, Ref: source,
			Destination: EventDestination{Type: "agent", Name: agent.Inbox}}, dest)
		if err != nil || published.Ref != source {
			t.Fatalf("source-loss publication: %+v %v", published, err)
		}
		pending, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: published.EventID}, dest)
		if err != nil || len(pending.Deliveries) != 1 || pending.Deliveries[0].State != "pending" ||
			pending.Deliveries[0].Consumer != agent.Inbox {
			t.Fatalf("source-loss pending delivery: %+v %v", pending, err)
		}
		ref := AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}
		claimed, err := receiver.ClaimSessionInbox(ctx, SessionInboxClaim{RequestID: uuid.NewString(),
			Session: ref, DeliveryID: pending.Deliveries[0].DeliveryID,
			NativeTurnID: "panel-turn-" + uuid.NewString(), TurnExclusive: true}, dest)
		if err != nil || claimed.Attempt == nil || claimed.Attempt.Delivery.Event.EventID != published.EventID {
			t.Fatalf("source-loss admission: %+v %v", claimed, err)
		}
		operator, root := testOperator(t)
		preview, err := operator.PreviewDeletion(ctx, source.RecordID)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := operator.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(),
			RecordID: source.RecordID, ExpectedVersion: source.Version,
			GrantID: root.ID, PreviewID: preview.PreviewID}); err != nil {
			t.Fatal(err)
		}
		_, err = receiver.History(ctx, RecordHistoryRequest{RecordID: source.RecordID, Version: source.Version}, dest)
		requireCode(t, err, "PAYLOAD_UNAVAILABLE")
		view, err := receiver.ForAgentSession(ref, dest)
		if err != nil {
			t.Fatal(err)
		}
		failed, err := view.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(),
			DeliveryID: claimed.Attempt.Delivery.DeliveryID, LeaseID: claimed.Attempt.Delivery.LeaseID,
			Disposition: "failed", Code: "source_unavailable"}, dest)
		if err != nil || failed.State != "failed" || failed.Code != "source_unavailable" || failed.Result != nil {
			t.Fatalf("source-loss settlement: %+v %v", failed, err)
		}
		if _, err = receiver.ReconcileSessionInbox(ctx, SessionInboxReconcile{RequestID: uuid.NewString(),
			Session: ref, AttemptID: claimed.Attempt.ID, Reason: "delivery_completed"}, dest); err != nil {
			t.Fatal(err)
		}
		status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: published.EventID}, dest)
		if err != nil || status.Event.Ref != source || len(status.Deliveries) != 1 ||
			status.Deliveries[0].DeliveryID != claimed.Attempt.Delivery.DeliveryID ||
			status.Deliveries[0].State != "failed" || status.Deliveries[0].Code != "source_unavailable" {
			t.Fatalf("source-loss durable status: %+v %v", status, err)
		}
	})
}
