package core

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestWorkstreamStatusOwnerReplaceZeroWakesAndHistory(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	lead := f.register(nativeAgent{Binding: "lead-foundation"})
	coord := f.register(nativeAgent{Binding: "coord-watcher"})
	other := f.register(nativeAgent{Binding: "other-worker"})
	leadView := f.view(sessionRef(lead))
	coordView := f.view(sessionRef(coord))
	otherView := f.view(sessionRef(other))
	dest := f.dest

	cp1 := time.Date(2026, 11, 19, 8, 0, 0, 0, time.UTC)
	cp2 := time.Date(2026, 11, 19, 8, 30, 0, 0, time.UTC)
	req1 := SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "foundation", State: "active",
		Current: "CAIRN-304 F01 qualification", LastLanded: "73865776 procpeer",
		NextCheckpoint: &cp1,
	}
	st1, err := leadView.SetWorkstreamStatus(ctx, req1, dest)
	if err != nil || st1.Revision != 1 || st1.Owner != lead.Inbox || st1.Writer != lead.Inbox {
		t.Fatalf("initial SetWorkstreamStatus: %+v %v", st1, err)
	}
	// Idempotent replay returns exact revision 1.
	replay, err := leadView.SetWorkstreamStatus(ctx, req1, dest)
	if err != nil || replay.Revision != 1 || !replay.UpdatedAt.Equal(st1.UpdatedAt) {
		t.Fatalf("replay mismatch: %+v %v", replay, err)
	}
	// Another agent is refused unless authorized.
	_, err = otherView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "foundation", State: "done",
	}, dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	// Owner replaces the entry; board keeps only the latest entry while history retains both.
	st2, err := leadView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "foundation", State: "blocked",
		Current: "CAIRN-304 F01 qualification", Blocker: "Waiting on test fixture",
		DecisionNeeded: "Approve retry budget", NextCheckpoint: &cp2, ExpectedRevision: 1,
	}, dest)
	if err != nil || st2.Revision != 2 || st2.State != "blocked" {
		t.Fatalf("owner replace failed: %+v %v", st2, err)
	}
	board, err := coordView.GetWorkstreamStatus(ctx, GetWorkstreamStatusRequest{}, dest)
	if err != nil || len(board.Streams) != 1 || board.Streams[0].Revision != 2 || board.Streams[0].Blocker != "Waiting on test fixture" {
		t.Fatalf("board did not keep only latest entry: %+v %v", board, err)
	}
	hist, err := coordView.WorkstreamStatusHistory(ctx, WorkstreamStatusHistoryRequest{Stream: "foundation"}, dest)
	if err != nil || len(hist.Entries) != 2 || hist.Entries[0].Revision != 2 || hist.Entries[1].Revision != 1 {
		t.Fatalf("unexpected stream history: %+v %v", hist, err)
	}

	// Setting status wakes no one and creates zero agent events or deliveries.
	ready, err := f.receiver.SessionInboxReady(ctx, sessionRef(coord), dest)
	if err != nil || ready.DeliveryID != "" {
		t.Fatalf("status board woke coordinator: %+v %v", ready, err)
	}
	events, err := coordView.Events(ctx, EventQuery{}, dest)
	if err != nil || len(events.Events) != 0 {
		t.Fatalf("status board emitted agent events: %+v %v", events, err)
	}
}

func TestWorkstreamStatusAuthorizationBudgetsAndSensitivity(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	lead := f.register(nativeAgent{Binding: "lead-auth"})
	worker := f.register(nativeAgent{Binding: "worker-auth"})
	successor := f.register(nativeAgent{Binding: "successor-auth"})
	leadView := f.view(sessionRef(lead))
	workerView := f.view(sessionRef(worker))
	succView := f.view(sessionRef(successor))
	dest := f.dest
	localDest := Destination{Name: "local", AllowLocal: true}

	if _, err := leadView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "semantic", State: "active", Current: "Slice 1",
	}, dest); err != nil {
		t.Fatal(err)
	}

	// Grant worker co-writer permission, verify write succeeds (without leaking AuthorizedWriters), then revoke and verify refusal.
	authSt, err := leadView.AuthorizeWorkstreamStatus(ctx, AuthorizeWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "semantic", Writer: worker.Inbox,
	}, dest)
	if err != nil || authSt.Revision != 2 {
		t.Fatalf("AuthorizeWorkstreamStatus: %+v %v", authSt, err)
	}
	wReq := SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "semantic", State: "waiting", Current: "Worker checkpoint",
	}
	stW, err := workerView.SetWorkstreamStatus(ctx, wReq, dest)
	if err != nil || stW.Revision != 3 || stW.Writer != worker.Inbox || stW.Owner != lead.Inbox || len(stW.AuthorizedWriters) != 0 {
		t.Fatalf("authorized worker write failed or leaked writers: %+v %v", stW, err)
	}
	// Co-writer cannot transfer ownership.
	_, err = workerView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "semantic", State: "waiting", Owner: worker.Inbox,
	}, dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	if _, err := leadView.AuthorizeWorkstreamStatus(ctx, AuthorizeWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "semantic", Writer: worker.Inbox, Revoke: true,
	}, dest); err != nil {
		t.Fatal(err)
	}
	// Replaying worker's earlier committed request returns no AuthorizedWriters; new write refuses.
	replayW, err := workerView.SetWorkstreamStatus(ctx, wReq, dest)
	if err != nil || len(replayW.AuthorizedWriters) != 0 {
		t.Fatalf("revoked worker replay leaked writers: %+v %v", replayW, err)
	}
	_, err = workerView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "semantic", State: "done",
	}, dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	// Fixed byte budgets enforced per field and per stream (2048 bytes total) with BUDGET_REFUSED.
	_, err = leadView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "semantic", State: "active", Current: strings.Repeat("x", 513),
	}, dest)
	requireCode(t, err, "BUDGET_REFUSED")
	_, err = leadView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "semantic", State: "active",
		Current: strings.Repeat("a", 500), LastLanded: strings.Repeat("b", 500),
		Blocker: strings.Repeat("c", 500), Detail: strings.Repeat("d", 600),
	}, dest)
	requireCode(t, err, "BUDGET_REFUSED")

	// Stale ExpectedRevision refuses with VERSION_CONFLICT.
	_, err = leadView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "semantic", State: "done", ExpectedRevision: 1,
	}, dest)
	requireCode(t, err, "VERSION_CONFLICT")

	// Sensitivity isolation: omitting Sensitivity on a local stream preserves local sensitivity;
	// hosted callers see NOT_FOUND on AuthorizeWorkstreamStatus (no existence oracle).
	_, err = leadView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "semantic", State: "done", Sensitivity: "local",
	}, localDest)
	requireCode(t, err, "DESTINATION_PROHIBITED")

	if _, err := f.operator().SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Stream: "private-lane", State: "active",
		Current: "Local secret lane", Sensitivity: "local",
	}, localDest); err != nil {
		t.Fatal(err)
	}
	stPreserved, err := f.operator().SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Stream: "private-lane", State: "waiting",
		Current: "Still local when Sensitivity omitted",
	}, localDest)
	if err != nil || stPreserved.Sensitivity != "local" {
		t.Fatalf("omitting Sensitivity promoted local stream: %+v %v", stPreserved, err)
	}
	hostedBoard, err := succView.GetWorkstreamStatus(ctx, GetWorkstreamStatusRequest{Stream: "private-lane"}, dest)
	if err != nil || len(hostedBoard.Streams) != 0 {
		t.Fatalf("local stream leaked to hosted reader: %+v %v", hostedBoard, err)
	}
	_, err = succView.AuthorizeWorkstreamStatus(ctx, AuthorizeWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "private-lane", Writer: successor.Inbox,
	}, dest)
	requireCode(t, err, "NOT_FOUND")

	// Explicit operator ownership transfer (event-scoped successor grants do not confer blanket stream takeover).
	if _, err := f.operator().AuthorizeWorkstreamStatus(ctx, AuthorizeWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Stream: "semantic", TransferOwner: successor.Inbox,
	}, localDest); err != nil {
		t.Fatal(err)
	}
	stSucc, err := succView.SetWorkstreamStatus(ctx, SetWorkstreamStatusRequest{
		RequestID: uuid.NewString(), Stream: "semantic", State: "done", Current: "Finished",
	}, dest)
	if err != nil || stSucc.Owner != successor.Inbox || stSucc.Revision != 6 {
		t.Fatalf("operator-transferred successor write failed: %+v %v", stSucc, err)
	}
}
