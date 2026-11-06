package localapi_test

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestEventPressureAPIViewAndNoOperatorPolicyRoute(t *testing.T) {
	client, _, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	var registration core.AgentInstance
	if err := client.Call(ctx, "agent-register", core.RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "pressure", NativeSessionID: uuid.NewString(), Metadata: core.AgentMetadata{Harness: "codex", Project: "test", Workspace: "/fixture", State: "idle", DeliveryMode: "existing-session"}}, &registration); err != nil {
		t.Fatal(err)
	}
	var note core.Record
	if err := client.Call(ctx, "create", core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "event pressure source", Sensitivity: "shareable", ClaimType: "self", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}}, &note); err != nil {
		t.Fatal(err)
	}
	var event core.AgentEvent
	if err := client.Call(ctx, "event-publish", core.PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: core.RecordVersionRef{RecordID: note.RecordID, Version: 1}, Destination: core.EventDestination{Type: "agent", Name: registration.Inbox}}, &event); err != nil {
		t.Fatal(err)
	}
	session := client.ForAgentSession(core.AgentSessionRef{AgentID: registration.AgentID, ExecutionID: registration.ExecutionID})
	var base, own core.EventPressure
	if err := client.Call(ctx, "event-pressure", core.EventPressureQuery{}, &base); err != nil {
		t.Fatal(err)
	}
	if err := session.Call(ctx, "event-pressure", core.EventPressureQuery{}, &own); err != nil {
		t.Fatal(err)
	}
	if base.Reservoirs.PendingDeliveries != 0 || own.Reservoirs.PendingDeliveries != 1 || own.Identity == nil || own.Identity.Kind != "session" || own.Identity.Session.AgentID != registration.AgentID {
		t.Fatalf("profile/session views: %+v %+v", base, own)
	}
	var page core.EventFanoutPage
	if err := client.Call(ctx, "event-fanout", core.EventFanoutQuery{}, &page); err != nil || len(page.Events) != 1 || page.Events[0].Deliveries != 1 {
		t.Fatalf("publisher fanout: %+v %v", page, err)
	}
	if err := session.Call(ctx, "event-fanout", core.EventFanoutQuery{}, &page); err != nil || len(page.Events) != 0 {
		t.Fatalf("recipient fanout: %+v %v", page, err)
	}
	var preview core.EventAdmissionPreview
	if err := session.Call(ctx, "event-admission-preview", core.EventAdmissionPreviewRequest{Destination: core.EventDestination{Type: "topic", Name: "empty"}}, &preview); err != nil || preview.EnforcementReady || preview.Mode != "disabled" {
		t.Fatalf("disabled preview: %+v %v", preview, err)
	}
	for _, name := range []string{"event-admission-policy", "event-admission-configure", "event-pressure-collection", "event-fanout-collection"} {
		if err := client.Call(ctx, name, map[string]any{"repo": repo}, &json.RawMessage{}); err == nil {
			t.Fatalf("operator route %s was exposed", name)
		}
	}
	// Strict request decoding cannot turn a scoped read into an aggregate read.
	if err := client.Call(ctx, "event-pressure", map[string]any{"collection": true}, &own); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("aggregate selector accepted: %v", err)
	}
}
