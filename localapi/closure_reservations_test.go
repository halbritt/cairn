package localapi_test

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestClosureReservationAPIListAndClose(t *testing.T) {
	client, _, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	var registration core.AgentInstance
	if err := client.Call(ctx, "agent-register", core.RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "closures", NativeSessionID: uuid.NewString(), Metadata: core.AgentMetadata{Harness: "codex", Project: "test", Workspace: "/fixture", State: "idle", DeliveryMode: "existing-session"}}, &registration); err != nil {
		t.Fatal(err)
	}
	var note core.Record
	if err := client.Call(ctx, "create", core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "closure source", Sensitivity: "shareable", ClaimType: "self", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}}, &note); err != nil {
		t.Fatal(err)
	}
	var request core.AgentEvent
	if err := client.Call(ctx, "event-publish", core.PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: core.RecordVersionRef{RecordID: note.RecordID, Version: 1}, Destination: core.EventDestination{Type: "agent", Name: registration.Inbox}}, &request); err != nil {
		t.Fatal(err)
	}
	var page core.ClosureReservationPage
	if err := client.Call(ctx, "event-closures", core.ClosureReservationQuery{}, &page); err != nil {
		t.Fatal(err)
	}
	if page.Scope != "inbox" || len(page.Reservations) != 1 || page.Counts.Available || page.Counts.Reserved != nil {
		t.Fatalf("publisher closures: %+v", page)
	}
	c := page.Reservations[0]
	if c.RequestEventID != request.EventID || c.State != "reserved" || c.ExpectedSender != registration.Inbox {
		t.Fatalf("reservation identity: %+v", c)
	}
	// The consumer session is not the recipient of the reply obligation.
	session := client.ForAgentSession(core.AgentSessionRef{AgentID: registration.AgentID, ExecutionID: registration.ExecutionID})
	var other core.ClosureReservationPage
	if err := session.Call(ctx, "event-closures", core.ClosureReservationQuery{}, &other); err != nil || len(other.Reservations) != 0 {
		t.Fatalf("consumer view: %+v %v", other, err)
	}
	closeReq := core.CloseReplyObligationsRequest{RequestID: uuid.NewString(), Reason: "answer no longer needed", Items: []core.CloseReplyObligationItem{{ReservationID: c.ReservationID, ExpectedRevision: c.Revision}}}
	var denied core.CloseReplyObligationsResult
	if err := session.Call(ctx, "event-close-replies", closeReq, &denied); core.Code(err) != "NOT_FOUND" {
		t.Fatalf("non-recipient close: %v", err)
	}
	var closed, again core.CloseReplyObligationsResult
	if err := client.Call(ctx, "event-close-replies", closeReq, &closed); err != nil {
		t.Fatal(err)
	}
	if len(closed.Reservations) != 1 || closed.Reservations[0].State != "released" || closed.Reservations[0].ReleaseReason != "publisher_closed" {
		t.Fatalf("closed: %+v", closed)
	}
	if err := client.Call(ctx, "event-close-replies", closeReq, &again); err != nil || again.Reservations[0].Revision != closed.Reservations[0].Revision {
		t.Fatalf("idempotent retry: %+v %v", again, err)
	}
	stale := closeReq
	stale.RequestID = uuid.NewString()
	if err := client.Call(ctx, "event-close-replies", stale, &again); core.Code(err) != "VERSION_CONFLICT" {
		t.Fatalf("stale revision: %v", err)
	}
	for _, name := range []string{"event-closure-reconcile", "event-closures-collection", "event-closure-close"} {
		if err := client.Call(ctx, name, map[string]any{"repo": repo}, &json.RawMessage{}); err == nil {
			t.Fatalf("operator route %s was exposed", name)
		}
	}
	if err := client.Call(ctx, "event-closures", map[string]any{"recipient": "someone"}, &page); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("recipient selector accepted: %v", err)
	}
}
