package core

import (
	"context"
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
)

type panelNativeEventSpec struct {
	ID        string `json:"id"`
	Kind      string `json:"kind"`
	Recipient string `json:"recipient"`
	Body      string `json:"body"`
	Causation string `json:"causation"`
}

type panelNativeEventCase struct {
	ID    string `json:"id"`
	Setup struct {
		Events []panelNativeEventSpec `json:"events"`
	} `json:"setup"`
}

type panelNativeEventFixture struct {
	Cases               []panelNativeEventCase `json:"cases"`
	EventSourceDefaults struct {
		Kind        string `json:"kind"`
		RecordClass string `json:"record_class"`
		Operation   string `json:"operation"`
		Sensitivity string `json:"sensitivity"`
		ClaimType   string `json:"claim_type"`
		Scope       Scope  `json:"scope"`
	} `json:"event_source_defaults"`
	ArmInterventions map[string]map[string]struct {
		RoutineNoticePolicy *NoticeWakePolicyRequest `json:"routine_notice_policy"`
	} `json:"arm_interventions"`
}

func readPanelNativeEventFixture(t *testing.T) panelNativeEventFixture {
	t.Helper()
	raw, err := os.ReadFile("../trials/token-efficiency/FIXTURE.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture panelNativeEventFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	defaults := fixture.EventSourceDefaults
	if defaults.Kind != "note" || defaults.RecordClass != "A" || defaults.Operation != "create" ||
		defaults.Sensitivity != "shareable" || defaults.ClaimType != "self" ||
		defaults.Scope.Repo != "fixture:cairn-token-efficiency" ||
		defaults.Scope.TaskID != "*" || defaults.Scope.RunID != "*" {
		t.Fatal("panel event source defaults no longer match native source contract")
	}
	return fixture
}

func (fixture panelNativeEventFixture) eventCase(t *testing.T, name string) panelNativeEventCase {
	t.Helper()
	var found *panelNativeEventCase
	for index := range fixture.Cases {
		if fixture.Cases[index].ID != name {
			continue
		}
		if found != nil {
			t.Fatalf("duplicate panel case %q", name)
		}
		found = &fixture.Cases[index]
	}
	if found == nil {
		t.Fatalf("missing panel case %q", name)
	}
	return *found
}

func (fixture panelNativeEventFixture) createEventSource(t *testing.T, sender *Store, event panelNativeEventSpec) RecordVersionRef {
	t.Helper()
	draft := Draft{Kind: fixture.EventSourceDefaults.Kind, Body: event.Body,
		Scope: fixture.EventSourceDefaults.Scope, ClaimType: fixture.EventSourceDefaults.ClaimType,
		Sensitivity: fixture.EventSourceDefaults.Sensitivity}
	record, err := sender.Create(context.Background(), CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil || record.Version != 1 || record.Body != event.Body ||
		record.ObservedWriter != sender.channel.Principal {
		t.Fatalf("panel event source %s: %+v %v", event.ID, record, err)
	}
	return RecordVersionRef{RecordID: record.RecordID, Version: record.Version}
}

// These are native source/publication probes for three more panel setups.
// They do not run actors, provider arms, capture, grading, or an installed canary.
func TestTokenEfficiencyPanelNativeEventSetups(t *testing.T) {
	ctx := context.Background()
	fixture := readPanelNativeEventFixture(t)
	repo := fixture.EventSourceDefaults.Scope.Repo
	dest := Destination{Name: "hosted", AllowLocal: false}

	t.Run("single_notice", func(t *testing.T) {
		row := fixture.eventCase(t, "single_notice")
		if len(row.Setup.Events) != 1 || row.Setup.Events[0].ID != "notice_a" ||
			row.Setup.Events[0].Kind != "notice" || row.Setup.Events[0].Recipient != "worker" {
			t.Fatal("single notice fixture contract changed")
		}
		sender := testStore(t, Channel{Principal: "agent:panel-single-sender-" + uuid.NewString(), Repo: repo})
		receiver := testStore(t, Channel{Principal: "agent:panel-single-worker-" + uuid.NewString(), Repo: repo})
		ref := fixture.createEventSource(t, sender, row.Setup.Events[0])
		notice, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(),
			Kind: row.Setup.Events[0].Kind, Ref: ref,
			Destination: EventDestination{Type: "agent", Name: receiver.channel.Principal}}, dest)
		if err != nil || notice.Ref != ref || notice.Kind != "notice" || notice.NoticePolicy != nil {
			t.Fatalf("native single notice: %+v %v", notice, err)
		}
		status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: notice.EventID}, dest)
		if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].Consumer != receiver.channel.Principal ||
			status.Deliveries[0].State != "pending" || status.Deliveries[0].AvailableAt != nil {
			t.Fatalf("single notice delivery: %+v %v", status, err)
		}
	})

	t.Run("actionable_response", func(t *testing.T) {
		row := fixture.eventCase(t, "actionable_response")
		if len(row.Setup.Events) != 2 || row.Setup.Events[0].ID != "request_decision" ||
			row.Setup.Events[0].Kind != "request" || row.Setup.Events[0].Recipient != "worker_a" ||
			row.Setup.Events[1].ID != "response_a" || row.Setup.Events[1].Kind != "response" ||
			row.Setup.Events[1].Recipient != "worker" || row.Setup.Events[1].Causation != row.Setup.Events[0].ID {
			t.Fatal("actionable response fixture contract changed")
		}
		worker := testStore(t, Channel{Principal: "agent:panel-worker-" + uuid.NewString(), Repo: repo})
		workerA := testStore(t, Channel{Principal: "agent:panel-worker-a-" + uuid.NewString(), Repo: repo})
		requestRef := fixture.createEventSource(t, worker, row.Setup.Events[0])
		request, err := worker.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(),
			Kind: row.Setup.Events[0].Kind, Ref: requestRef,
			Destination: EventDestination{Type: "agent", Name: workerA.channel.Principal}}, dest)
		if err != nil || request.Ref != requestRef || request.From != worker.channel.Principal {
			t.Fatalf("native request: %+v %v", request, err)
		}
		responseRef := fixture.createEventSource(t, workerA, row.Setup.Events[1])
		response, err := workerA.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(),
			Kind: row.Setup.Events[1].Kind, Ref: responseRef,
			Destination: EventDestination{Type: "agent", Name: worker.channel.Principal},
			CausationID: request.EventID}, dest)
		if err != nil || response.CausationID != request.EventID || response.Ref != responseRef ||
			response.From != workerA.channel.Principal {
			t.Fatalf("native causally linked response: %+v %v", response, err)
		}
		for _, item := range []struct {
			publisher *Store
			event     AgentEvent
			consumer  string
		}{{worker, request, workerA.channel.Principal}, {workerA, response, worker.channel.Principal}} {
			status, statusErr := item.publisher.AgentEventStatus(ctx, EventStatusRequest{EventID: item.event.EventID}, dest)
			if statusErr != nil || len(status.Deliveries) != 1 || status.Deliveries[0].Consumer != item.consumer ||
				status.Deliveries[0].State != "pending" {
				t.Fatalf("causal delivery: %+v %v", status, statusErr)
			}
		}
	})

	t.Run("routine_burst_urgent_request", func(t *testing.T) {
		row := fixture.eventCase(t, "routine_burst_urgent_request")
		wantIDs := []string{"routine_1", "routine_2", "routine_3", "urgent_1"}
		if len(row.Setup.Events) != len(wantIDs) {
			t.Fatalf("burst fixture event count %d", len(row.Setup.Events))
		}
		for index, event := range row.Setup.Events {
			wantKind := "notice"
			if index == 3 {
				wantKind = "request"
			}
			if event.ID != wantIDs[index] || event.Kind != wantKind || event.Recipient != "worker" {
				t.Fatalf("burst fixture event %d: %+v", index, event)
			}
		}
		interventions := fixture.ArmInterventions[row.ID]
		if len(interventions) != 4 {
			t.Fatalf("burst intervention arm count %d", len(interventions))
		}
		for _, arm := range []string{"baseline", "envelope", "batch", "combined"} {
			t.Run(arm, func(t *testing.T) {
				intervention, ok := interventions[arm]
				if !ok {
					t.Fatalf("missing %s intervention", arm)
				}
				policy := intervention.RoutineNoticePolicy
				if arm == "baseline" || arm == "envelope" {
					if policy != nil {
						t.Fatalf("%s unexpectedly defers notices: %+v", arm, policy)
					}
				} else if policy == nil || policy.Version != 1 || policy.Urgency != "routine" || policy.DeferSeconds != 30 {
					t.Fatalf("%s routine policy: %+v", arm, policy)
				}
				sender := testStore(t, Channel{Principal: "agent:panel-burst-sender-" + arm + "-" + uuid.NewString(), Repo: repo})
				receiver := testStore(t, Channel{Principal: "agent:panel-burst-worker-" + arm + "-" + uuid.NewString(), Repo: repo})
				sessionID := "panel-burst-" + arm + "-" + uuid.NewString()
				agent, err := receiver.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(),
					Binding: sessionID, NativeSessionID: sessionID,
					Metadata: AgentMetadata{Harness: "codex", Project: "fixture", Workspace: "/work/fixture",
						State: "idle", DeliveryMode: "existing-session"}}, dest)
				if err != nil {
					t.Fatal(err)
				}
				var urgent AgentEvent
				for _, event := range row.Setup.Events {
					ref := fixture.createEventSource(t, sender, event)
					var noticePolicy *NoticeWakePolicyRequest
					if event.Kind == "notice" {
						noticePolicy = policy
					}
					published, publishErr := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(),
						Kind: event.Kind, Ref: ref, Destination: EventDestination{Type: "agent", Name: agent.Inbox},
						NoticePolicy: noticePolicy}, dest)
					if publishErr != nil || published.Ref != ref || published.Kind != event.Kind {
						t.Fatalf("%s publication %s: %+v %v", arm, event.ID, published, publishErr)
					}
					status, statusErr := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: published.EventID}, dest)
					if statusErr != nil || len(status.Deliveries) != 1 || status.Deliveries[0].Consumer != agent.Inbox ||
						status.Deliveries[0].State != "pending" {
						t.Fatalf("%s delivery %s: %+v %v", arm, event.ID, status, statusErr)
					}
					if event.Kind == "notice" && policy != nil {
						if published.NoticePolicy == nil || published.NoticePolicy.Owner != sender.channel.Principal ||
							published.NoticePolicy.Version != 1 || published.NoticePolicy.Urgency != "routine" ||
							status.Deliveries[0].AvailableAt == nil ||
							status.Deliveries[0].AvailableAt.Sub(published.CreatedAt) != 30*time.Second {
							t.Fatalf("%s deferred notice %s: %+v %+v", arm, event.ID, published, status)
						}
					} else if published.NoticePolicy != nil || status.Deliveries[0].AvailableAt != nil {
						t.Fatalf("%s immediate publication %s: %+v %+v", arm, event.ID, published, status)
					}
					if event.Kind == "request" {
						urgent = published
					}
				}
				if policy != nil {
					ready, err := receiver.SessionInboxReady(ctx,
						AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}, dest)
					if err != nil || ready.EventID != urgent.EventID {
						t.Fatalf("%s urgent request delayed by routine notices: %+v %v", arm, ready, err)
					}
				}
			})
		}
	})
}
