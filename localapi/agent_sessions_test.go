package localapi_test

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestAgentSessionUsesExistingProfileAndDistinctInbox(t *testing.T) {
	client, _, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	registration := core.RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "codex-default", NativeSessionID: "session-one",
		Metadata: core.AgentMetadata{Harness: "codex", Project: "rhumb", Workspace: "/fixture/rhumb", State: "busy", DeliveryMode: "existing-session"}}
	var agent core.AgentInstance
	if err := client.Call(ctx, "agent-register", registration, &agent); err != nil {
		t.Fatal(err)
	}
	var note core.Record
	if err := client.Call(ctx, "create", core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "selected session message", Sensitivity: "shareable", ClaimType: "self", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}}, &note); err != nil {
		t.Fatal(err)
	}
	var event core.AgentEvent
	if err := client.Call(ctx, "event-publish", core.PublishEventRequest{RequestID: uuid.NewString(), Ref: core.RecordVersionRef{RecordID: note.RecordID, Version: 1}, Kind: "request", Destination: core.EventDestination{Type: "agent", Name: agent.Inbox}}, &event); err != nil {
		t.Fatal(err)
	}
	var next core.NextEventResult
	if err := client.Call(ctx, "event-next", core.NextEventRequest{}, &next); err != nil || next.Delivery != nil {
		t.Fatalf("base inbox consumed session work: %+v %v", next, err)
	}
	session := client.ForAgentSession(core.AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID})
	if err := session.Call(ctx, "event-next", core.NextEventRequest{}, &next); err != nil || next.Delivery == nil || next.Delivery.Event.EventID != event.EventID {
		t.Fatalf("session inbox: %+v %v", next, err)
	}
	var done core.AgentDelivery
	if err := session.Call(ctx, "event-complete", core.CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: next.Delivery.DeliveryID, LeaseID: next.Delivery.LeaseID, Disposition: "handled"}, &done); err != nil || done.State != "handled" {
		t.Fatalf("session completion: %+v %v", done, err)
	}
	registration.RequestID = uuid.NewString()
	var resumed core.AgentInstance
	if err := client.Call(ctx, "agent-register", registration, &resumed); err != nil {
		t.Fatal(err)
	}
	if err := session.Call(ctx, "event-next", core.NextEventRequest{}, &next); core.Code(err) != "STALE_SESSION" {
		t.Fatalf("old session transport remained current: %v", err)
	}
	var directory core.AgentDirectoryPage
	if err := client.Call(ctx, "agent-directory", core.AgentDirectoryQuery{Project: "rhumb"}, &directory); err != nil || len(directory.Agents) != 1 || directory.Agents[0].AgentID != agent.AgentID {
		t.Fatalf("directory: %+v %v", directory, err)
	}
	if err := client.ForAgentSession(core.AgentSessionRef{}).Call(ctx, "event-next", core.NextEventRequest{}, &next); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("empty session selection silently used the base inbox: %v", err)
	}
}
