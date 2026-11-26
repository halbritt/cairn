package core

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/google/uuid"
)

func (fixture panelMemoryFixture) memoryCaseWithEvent(t *testing.T, name string, sourceIDs ...string) panelMemoryCase {
	t.Helper()
	var found *panelMemoryCase
	for i := range fixture.Cases {
		if fixture.Cases[i].ID != name {
			continue
		}
		if found != nil {
			t.Fatalf("duplicate panel case %s", name)
		}
		found = &fixture.Cases[i]
	}
	if found == nil || len(found.Setup.Sources) != len(sourceIDs) || len(found.Setup.Events) != 1 {
		t.Fatalf("%s: missing or changed source/event setup", name)
	}
	for i, id := range sourceIDs {
		if found.Setup.Sources[i] != id {
			t.Fatalf("%s: source %d changed: %s", name, i, found.Setup.Sources[i])
		}
	}
	return *found
}

// These checks retain the fixture's exact source and event bytes in a separate
// disposable database per case. They do not repeat source exposure in a model,
// pause or resume an actor, grade task answers, or measure token savings.
func TestTokenEfficiencyPanelLongSourceAndHandoffSetups(t *testing.T) {
	ctx := context.Background()
	fixture := readPanelMemoryFixture(t)
	dest := Destination{Name: "hosted", AllowLocal: false}

	t.Run("long_source", func(t *testing.T) {
		row := fixture.memoryCaseWithEvent(t, "long_source", "relay_history", "relay_current")
		if row.ActorTask == "" || len(row.Setup.Transitions) != 1 || row.Setup.Transitions[0].Phase != "before_read" ||
			row.Setup.Transitions[0].Action != "repeat_source" ||
			row.Setup.Transitions[0].Target != "relay_history" || row.Setup.Transitions[0].Count != 120 {
			t.Fatal("long-source exposure transition changed")
		}
		var event panelNativeEventSpec
		if err := json.Unmarshal(row.Setup.Events[0], &event); err != nil {
			t.Fatal(err)
		}
		if event.ID != "request_relay" || event.Kind != "request" || event.Recipient != "worker" ||
			event.Body != "Give the current live relay limit." {
			t.Fatalf("long-source request changed: %+v", event)
		}
		_, openStore := reviewDatabase(t)
		repo := fixture.Sources["relay_history"].Scope.Repo
		sender := openStore(Channel{Principal: "agent:panel-relay-sender-" + uuid.NewString(), Repo: repo})
		worker := openStore(Channel{Principal: "agent:panel-relay-worker-" + uuid.NewString(), Repo: repo})
		var records [2]Record
		for i, sourceID := range row.Setup.Sources {
			source := fixture.Sources[sourceID]
			if source.Project != "Cairn" || source.Operation != "create" ||
				source.LogicalVersion != 1 || source.Kind != "note" || source.RecordFamily != sourceID {
				t.Fatalf("%s: source metadata changed", sourceID)
			}
			record, err := sender.Create(ctx, CreateRequest{RequestID: uuid.NewString(),
				Draft: fixture.sourceDraft(t, sourceID)})
			if err != nil || record.Body != source.Body || record.Version != 1 ||
				record.ObservedWriter != sender.channel.Principal {
				t.Fatalf("%s: native source create: %+v %v", sourceID, record, err)
			}
			records[i] = record
		}
		eventFixture := readPanelNativeEventFixture(t)
		ref := eventFixture.createEventSource(t, sender, event)
		request, err := sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(),
			Kind: event.Kind, Ref: ref,
			Destination: EventDestination{Type: "agent", Name: worker.channel.Principal}}, dest)
		if err != nil || request.Kind != event.Kind || request.Ref != ref {
			t.Fatalf("native relay request: %+v %v", request, err)
		}
		status, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: request.EventID}, dest)
		if err != nil || len(status.Deliveries) != 1 ||
			status.Deliveries[0].Consumer != worker.channel.Principal || status.Deliveries[0].State != "pending" {
			t.Fatalf("relay request delivery: %+v %v", status, err)
		}
		index, err := worker.Index(ctx, CompileRequest{RequestID: uuid.NewString(),
			Scope:   Scope{Repo: repo, TaskID: "task", RunID: "run"},
			Query:   row.ActorTask,
			Purpose: "context", AvailableTokens: 64000, Mode: "index"}, dest)
		if err != nil {
			t.Fatal(err)
		}
		for _, record := range records {
			panelMemoryPullWhole(t, worker, index, record, dest)
		}
	})

	t.Run("unfinished_handoff", func(t *testing.T) {
		row := fixture.memoryCase(t, "unfinished_handoff", "handoff_state")
		if row.ActorTask == "" || len(row.Setup.Transitions) != 1 || row.Setup.Transitions[0].Phase != "before_resume" ||
			row.Setup.Transitions[0].Action != "pause_after_source_check" ||
			row.Setup.Transitions[0].Target != "handoff_state" {
			t.Fatal("handoff pause transition changed")
		}
		source := fixture.Sources["handoff_state"]
		if source.Project != "Cairn" || source.Operation != "create" ||
			source.LogicalVersion != 1 || source.Kind != "note" || source.RecordFamily != "handoff_state" {
			t.Fatal("handoff source metadata changed")
		}
		_, openStore := reviewDatabase(t)
		store := openStore(Channel{Principal: "agent:panel-handoff-" + uuid.NewString(), Repo: source.Scope.Repo})
		record, err := store.Create(ctx, CreateRequest{RequestID: uuid.NewString(),
			Draft: fixture.sourceDraft(t, "handoff_state")})
		if err != nil || record.Body != source.Body || record.Version != 1 ||
			record.ObservedWriter != store.channel.Principal {
			t.Fatalf("native handoff source: %+v %v", record, err)
		}
		index, err := store.Index(ctx, CompileRequest{RequestID: uuid.NewString(),
			Scope:   Scope{Repo: source.Scope.Repo, TaskID: "task", RunID: "run"},
			Query:   row.ActorTask,
			Purpose: "context", AvailableTokens: 64000, Mode: "index"}, dest)
		if err != nil {
			t.Fatal(err)
		}
		panelMemoryPullWhole(t, store, index, record, dest)
	})
}
