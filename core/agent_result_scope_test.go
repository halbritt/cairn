package core

import (
	"context"
	"encoding/json"
	"strings"
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
	guidance := projectNote(source.Scope.Repo)
	guidance.Body = "Keep reusable guidance available across tasks."
	guidance.Sensitivity = "shareable"
	reusable, err := a.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: guidance})
	if err != nil {
		t.Fatal(err)
	}
	// CAIRN-128: scope must filter bodies before the semantic retrieval boundary,
	// not merely remove a completion from the eventual index. This deterministic
	// retriever tests eligibility, not embedding quality or relevance.
	for _, task := range []string{"unrelated-task", draft.Scope.TaskID} {
		t.Run("semantic/"+task, func(t *testing.T) {
			wantResult := task == draft.Scope.TaskID
			retrieved := false
			b.semanticRetriever = func(_ context.Context, req SemanticRankRequest) (SemanticRetrievalResult, error) {
				retrieved = true
				seen := map[string]bool{}
				hits := []SemanticPassageHit{}
				for _, n := range req.Notes {
					if !wantResult && (n.RecordID == done.Result.RecordID || strings.Contains(n.Body, draft.Body)) {
						t.Fatal("out-of-task completion reached semantic retrieval")
					}
					seen[n.RecordID] = true
					hits = append(hits, SemanticPassageHit{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, 900000}, Span: ByteSpanRequest{Length: len(n.Body)}})
				}
				if seen[done.Result.RecordID] != wantResult || !seen[reusable.RecordID] {
					t.Fatalf("semantic eligibility: result=%v want=%v, repository guidance=%v", seen[done.Result.RecordID], wantResult, seen[reusable.RecordID])
				}
				return SemanticRetrievalResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "scope-fixture/1", Indexed: len(req.Notes), Hits: hits}, nil
			}
			// No lexical match: a fallback cannot satisfy the positive controls.
			index, err := b.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{source.Scope.Repo, task, "different-run"}, Query: "semanticeligibilityprobe", Purpose: "context", AvailableTokens: 64000, Semantic: true}, dest)
			if err != nil {
				t.Fatal(err)
			}
			if !retrieved || index.Package.Semantic.Discovery == nil || index.Package.Semantic.Discovery.State != "ready" {
				t.Fatalf("semantic retrieval did not succeed: %+v", index.Package.Semantic.Discovery)
			}
			found := map[string]bool{}
			for _, entry := range index.Package.Semantic.Index {
				found[entry.RecordID] = true
			}
			if found[done.Result.RecordID] != wantResult || !found[reusable.RecordID] {
				t.Fatalf("semantic index: result=%v want=%v, repository guidance=%v", found[done.Result.RecordID], wantResult, found[reusable.RecordID])
			}
			wire, err := json.Marshal(index)
			if err != nil {
				t.Fatal(err)
			}
			if !wantResult && (strings.Contains(string(wire), done.Result.RecordID) || strings.Contains(string(wire), draft.Body)) {
				t.Fatal("out-of-task completion leaked into index response")
			}
		})
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
