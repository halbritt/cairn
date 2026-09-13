package core

import (
	"context"
	"github.com/google/uuid"
	"testing"
)

func TestCompletedTaskDocketFindsOpenDelegatesAndClearsOnTerminal(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "service:task-observer", Instrumented: true})
	repo := uuid.NewString()
	scope := Scope{repo, "task", "run"}
	attempt := uuid.NewString()
	if _, err := s.RecordSpawn(ctx, SpawnRequest{uuid.NewString(), attempt, "dispatcher", "delegate", scope}); err != nil {
		t.Fatal(err)
	}
	req := TaskStateRequest{RequestID: uuid.NewString(), Scope: scope, State: "completed", Method: "host-task-terminal/1"}
	if _, err := s.ObserveTask(ctx, req); err != nil {
		t.Fatal(err)
	}
	docket, err := s.Docket(ctx, repo)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, item := range docket.Items {
		if item.Reason == "OPEN_DELEGATE_AFTER_TASK" && item.AttemptID == attempt {
			found = true
		}
	}
	if !found {
		t.Fatalf("missing completed-task open loop: %+v", docket)
	}
	if _, err = s.RecordTerminal(ctx, TerminalRequest{uuid.NewString(), attempt, "completed", "fixture:result"}); err != nil {
		t.Fatal(err)
	}
	docket, err = s.Docket(ctx, repo)
	if err != nil {
		t.Fatal(err)
	}
	for _, item := range docket.Items {
		if item.Reason == "OPEN_DELEGATE_AFTER_TASK" {
			t.Fatal("terminal attempt remained open")
		}
	}
}

func TestAttemptObservationsRespectAuthenticatedRepository(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "service:scoped-attempt", Instrumented: true, Repo: "allowed"})
	_, err := s.RecordSpawn(ctx, SpawnRequest{uuid.NewString(), uuid.NewString(), "dispatcher", "delegate", Scope{"outside", "task", "run"}})
	requireCode(t, err, "AUTHORITY_DENIED")
}
