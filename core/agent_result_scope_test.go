package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

// Result scope narrows discovery, not access to an authorized exact reference.
func TestAgentCompletionResultScopeSearchHistoryAndRetry(t *testing.T) {
	ctx := context.Background()
	a, b, source, dest := eventFixture(t)
	publishFixture(t, a, b, source, dest)
	delivery := nextFixture(t, b, dest)
	draft := projectNote(source.Scope.Repo)
	draft.Scope.TaskID = "coordination-result/" + delivery.DeliveryID
	draft.Body = "scopedresultfixture completion inventory"
	draft.Sensitivity = "shareable"
	req := CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: "handled", Draft: &draft}
	done, err := b.CompleteEvent(ctx, req, dest)
	if err != nil || done.Result == nil {
		t.Fatalf("complete: %+v %v", done, err)
	}
	for _, task := range []string{"unrelated-task", draft.Scope.TaskID} {
		index, err := b.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{source.Scope.Repo, task, "different-run"}, Query: "scopedresultfixture", Purpose: "context", AvailableTokens: 64000}, dest)
		if err != nil {
			t.Fatal(err)
		}
		found := false
		for _, entry := range index.Package.Semantic.Index {
			if entry.RecordID == done.Result.RecordID {
				found = true
			}
		}
		if found != (task == draft.Scope.TaskID) {
			t.Fatalf("task %q scoped result visible=%v", task, found)
		}
	}
	history, err := a.History(ctx, RecordHistoryRequest{RecordID: done.Result.RecordID, Version: done.Result.Version}, dest)
	if err != nil || len(history.Versions) != 1 || history.Versions[0].Body == nil || *history.Versions[0].Body != draft.Body || history.Versions[0].Scope != draft.Scope {
		t.Fatalf("authorized exact result history: %+v %v", history, err)
	}
	retry, err := b.CompleteEvent(ctx, req, dest)
	if err != nil || retry.Result == nil || *retry.Result != *done.Result {
		t.Fatalf("retry changed result: %+v %v", retry, err)
	}
	changed := draft
	changed.Scope.TaskID = "*"
	req.Draft = &changed
	_, err = b.CompleteEvent(ctx, req, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	stored, err := b.Get(ctx, done.Result.RecordID)
	if err != nil || stored.Scope != draft.Scope || stored.Version != 1 {
		t.Fatalf("retry rewrote scope: %+v %v", stored, err)
	}
}
