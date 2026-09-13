package core

import (
	"context"
	"reflect"
	"strings"
	"sync"
	"testing"

	"github.com/google/uuid"
)

func TestRevisePreservesMetadataAndOriginalRetryAcrossLaterEdits(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	source, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	draft.Kind, draft.Body = "procedure", "Original selected procedure."
	draft.Pins = &Applicability{TaskClass: "build"}
	draft.Relations = []RecordRelation{{source.RecordID, 1, "derived_from"}}
	draft.ClaimType, draft.AttributedProducer, draft.AttemptID, draft.ResultRef = "partial", "agent:original", uuid.NewString(), "retained-result"
	old, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	writer := testStore(t, Channel{Principal: "agent:reviser", Repo: repo})
	req := ReviseRequest{uuid.NewString(), old.RecordID, old.Version, repo, "Corrected procedure.\nKeep literal 日本語.\n"}
	updated, err := writer.Revise(ctx, req)
	if err != nil || updated != (Revision{old.RecordID, 2}) {
		t.Fatalf("revise: %+v %v", updated, err)
	}
	got, err := op.Get(ctx, old.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	want := old.Draft
	want.Body = req.Body
	if !reflect.DeepEqual(got.Draft, want) || got.Sensitivity != old.Sensitivity || got.Class != "A" || got.ObservedWriter != "agent:reviser" || got.Witness != "testimony" {
		t.Fatalf("metadata changed: %+v", got)
	}
	if _, err = writer.Edit(ctx, EditRequest{uuid.NewString(), old.RecordID, 2, got.Draft}); err != nil {
		t.Fatal(err)
	}
	retry, err := writer.Revise(ctx, req)
	if err != nil || retry != updated {
		t.Fatalf("retry followed later version: %+v %v", retry, err)
	}
	req.Body += "different intent"
	_, err = writer.Revise(ctx, req)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	req.RequestID = uuid.NewString()
	_, err = writer.Revise(ctx, req)
	requireCode(t, err, "VERSION_CONFLICT")
	req.Repo = "different-repo"
	_, err = op.Revise(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
	req.Repo, req.ExpectedVersion = repo, 3
	if _, err = writer.Delete(ctx, DeleteRequest{uuid.NewString(), old.RecordID, 3}); err != nil {
		t.Fatal(err)
	}
	_, err = writer.Revise(ctx, req)
	requireCode(t, err, "NOT_FOUND")
}

func TestReviseRacingFullEditNeverOverwritesNewerMetadata(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:revision-race"})
	for range 5 {
		draft := projectNote(uuid.NewString())
		old, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
		if err != nil {
			t.Fatal(err)
		}
		draft.Kind, draft.Body = "procedure", "Full edit with changed kind."
		start := make(chan struct{})
		errs := make(chan error, 2)
		var wg sync.WaitGroup
		wg.Add(2)
		go func() {
			defer wg.Done()
			<-start
			_, err := s.Edit(ctx, EditRequest{uuid.NewString(), old.RecordID, 1, draft})
			errs <- err
		}()
		go func() {
			defer wg.Done()
			<-start
			_, err := s.Revise(ctx, ReviseRequest{uuid.NewString(), old.RecordID, 1, old.Scope.Repo, "Only the body changes."})
			errs <- err
		}()
		close(start)
		wg.Wait()
		close(errs)
		successes := 0
		for err := range errs {
			if err == nil {
				successes++
			} else {
				requireCode(t, err, "VERSION_CONFLICT")
			}
		}
		got, err := s.Get(ctx, old.RecordID)
		if err != nil || successes != 1 || got.Version != 2 {
			t.Fatalf("race: %+v successes=%d err=%v", got, successes, err)
		}
		if !((got.Body == draft.Body && got.Kind == draft.Kind) || (got.Body == "Only the body changes." && got.Kind == old.Kind)) {
			t.Fatalf("mixed revisions: %+v", got)
		}
	}
}

func TestReviseRejectsInvalidIntentBeforeStoreAccess(t *testing.T) {
	valid := ReviseRequest{uuid.NewString(), uuid.NewString(), 1, "repo", "Selected body"}
	for _, change := range []func(*ReviseRequest){
		func(r *ReviseRequest) { r.RecordID = "invalid" },
		func(r *ReviseRequest) { r.ExpectedVersion = 0 },
		func(r *ReviseRequest) { r.Repo = "*" },
		func(r *ReviseRequest) { r.Body = " \n" },
		func(r *ReviseRequest) { r.Body = strings.Repeat("x", 65537) },
	} {
		req := valid
		change(&req)
		_, err := (*Store)(nil).Revise(context.Background(), req)
		requireCode(t, err, "INVALID_REQUEST")
	}
}
