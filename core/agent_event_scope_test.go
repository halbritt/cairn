package core

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestTaskScopedAssignmentDeliveryAndSearch(t *testing.T) {
	ctx := context.Background()
	repo := uuid.NewString()
	publisher := testStore(t, Channel{Principal: "publisher-" + repo, Repo: repo})
	recipient := testStore(t, Channel{Principal: "recipient-" + repo, Repo: repo})
	hosted := Destination{"hosted", false}
	publicationID := uuid.NewString()
	task := "coordination/" + publicationID
	draft := projectNote(repo)
	draft.Scope.TaskID = task
	draft.Sensitivity = "shareable"
	draft.Body = "Review retry guidance for this one-off assignment."
	source, err := publisher.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	lessonDraft := projectNote(repo)
	lessonDraft.Sensitivity = "shareable"
	lessonDraft.Kind = "lesson"
	lessonDraft.Body = "Reusable retry guidance: keep the operation request identity on retry."
	lesson, err := publisher.Create(ctx, CreateRequest{uuid.NewString(), lessonDraft})
	if err != nil {
		t.Fatal(err)
	}
	privateDraft := draft
	privateDraft.Sensitivity = "local"
	privateDraft.Body = "Private retry guidance."
	private, err := publisher.Create(ctx, CreateRequest{uuid.NewString(), privateDraft})
	if err != nil {
		t.Fatal(err)
	}
	_, err = recipient.History(ctx, RecordHistoryRequest{RecordID: private.RecordID, Version: 1}, hosted)
	requireCode(t, err, "NOT_FOUND")
	publication := PublishEventRequest{RequestID: publicationID, Repo: repo, Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", recipient.channel.Principal}}
	event, err := publisher.PublishEvent(ctx, publication, hosted)
	if err != nil {
		t.Fatal(err)
	}
	retry, err := publisher.PublishEvent(ctx, publication, hosted)
	if err != nil || retry.EventID != event.EventID {
		t.Fatalf("publication retry: %+v %v", retry, err)
	}
	privatePublication := publication
	privatePublication.RequestID = uuid.NewString()
	privatePublication.Ref = RecordVersionRef{private.RecordID, 1}
	_, err = publisher.PublishEvent(ctx, privatePublication, hosted)
	requireCode(t, err, "NOT_FOUND")

	semanticCalls := 0
	expectedSource := false
	recipient.semanticRanker = func(_ context.Context, req SemanticRankRequest) (SemanticRankResult, error) {
		semanticCalls++
		result := SemanticRankResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "scope-fixture/1"}
		seen := map[string]bool{}
		for _, note := range req.Notes {
			if note.RecordID != lesson.RecordID && !(expectedSource && note.RecordID == source.RecordID) {
				t.Fatalf("ineligible assignment/private source reached scorer: %s", note.RecordID)
			}
			seen[note.RecordID] = true
			result.Scores = append(result.Scores, SemanticScore{note.RecordID, note.Version, note.BodySHA256, 900000})
		}
		if !seen[lesson.RecordID] || seen[source.RecordID] != expectedSource {
			t.Fatalf("wrong scorer candidates: %v", seen)
		}
		return result, nil
	}
	var scoped IndexResult
	for _, semantic := range []bool{false, true} {
		for _, searchTask := range []string{"engineering-review", task} {
			expectedSource = searchTask == task
			index, err := recipient.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, searchTask, "recipient-run"}, Query: "retry guidance", Purpose: "context", AvailableTokens: 32000, Semantic: semantic}, hosted)
			if err != nil {
				t.Fatal(err)
			}
			if semantic && (index.Package.Semantic.Discovery == nil || index.Package.Semantic.Discovery.State != "ready") {
				t.Fatal("semantic assertion ran against fallback")
			}
			seen := map[string]bool{}
			for _, entry := range index.Package.Semantic.Index {
				seen[entry.RecordID] = true
			}
			if !seen[lesson.RecordID] || seen[source.RecordID] != expectedSource || seen[private.RecordID] {
				t.Fatalf("scope=%s semantic=%v results=%v", searchTask, semantic, seen)
			}
			if expectedSource {
				scoped = index
			}
		}
	}
	if semanticCalls != 2 {
		t.Fatalf("semantic scorer was not exercised twice: %d", semanticCalls)
	}
	// Publication retains its exact reference even after the current text changes.
	if _, err = publisher.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: source.RecordID, ExpectedVersion: 1, Repo: repo, Body: "Revised retry guidance."}); err != nil {
		t.Fatal(err)
	}
	checkedStaleHandle := false
	for _, handle := range scoped.Handles {
		if handle.RecordID == source.RecordID {
			checkedStaleHandle = true
			_, err = recipient.Expand(ctx, ExpandRequest{uuid.NewString(), scoped.Package.ReceiptID, handle.Handle, nil}, hosted)
			requireCode(t, err, "STALE_HANDLE")
		}
	}
	if !checkedStaleHandle {
		t.Fatal("scoped source lacked a pull handle")
	}
	delivery := nextFixture(t, recipient, hosted)
	if delivery.Event.EventID != event.EventID || delivery.Event.Ref != publication.Ref || delivery.Event.From != publisher.channel.Principal {
		t.Fatalf("event reference changed: %+v", delivery.Event)
	}
	history, err := recipient.History(ctx, RecordHistoryRequest{RecordID: delivery.Event.Ref.RecordID, Version: delivery.Event.Ref.Version}, hosted)
	if err != nil || len(history.Versions) != 1 || history.Versions[0].Body == nil || *history.Versions[0].Body != draft.Body || history.Versions[0].Scope.TaskID != task || history.CurrentVersion != 2 {
		t.Fatalf("exact scoped assignment History: %+v %v", history, err)
	}
	result := draft
	result.Body = "Review completed for this assignment."
	completion := CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: "handled", Draft: &result}
	done, err := recipient.CompleteEvent(ctx, completion, hosted)
	if err != nil || done.State != "handled" || done.Result == nil {
		t.Fatalf("completion: %+v %v", done, err)
	}
	again, err := recipient.CompleteEvent(ctx, completion, hosted)
	if err != nil || again.Result == nil || *again.Result != *done.Result {
		t.Fatalf("completion retry: %+v %v", again, err)
	}
	// Forgetting still removes payload access, not the retained event reference.
	operator, grant := testOperator(t)
	preview, err := operator.PreviewDeletion(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = operator.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: source.RecordID, ExpectedVersion: 2, GrantID: grant.ID, PreviewID: preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	_, err = recipient.History(ctx, RecordHistoryRequest{RecordID: source.RecordID, Version: 1}, hosted)
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	status, err := publisher.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, hosted)
	if err != nil || status.Event.Ref != publication.Ref {
		t.Fatalf("forgetting changed event reference: %+v %v", status, err)
	}
}
