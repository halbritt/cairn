package core

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"
)

func TestOrdinaryDeleteRemovesUnreferencedRevisionsWithoutResurrection(t *testing.T) {
	ctx := context.Background()
	repo := uuid.NewString()
	s := testStore(t, Channel{Principal: "ordinary-delete:" + repo, Repo: repo})
	create := CreateRequest{uuid.NewString(), projectNote(repo)}
	create.Draft.Pins = &Applicability{TaskClass: "repair"}
	r, err := s.Create(ctx, create)
	if err != nil {
		t.Fatal(err)
	}
	draft := r.Draft
	draft.Body = "Revised ordinary fixture that should be removable"
	edit := EditRequest{uuid.NewString(), r.RecordID, 1, draft}
	r, err = s.Edit(ctx, edit)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Delete(ctx, DeleteRequest{uuid.NewString(), r.RecordID, 1})
	requireCode(t, err, "VERSION_CONFLICT")
	outsider := testStore(t, Channel{Principal: "other-delete:" + repo, Repo: uuid.NewString()})
	_, err = outsider.Delete(ctx, DeleteRequest{uuid.NewString(), r.RecordID, r.Version})
	requireCode(t, err, "AUTHORITY_DENIED")
	req := DeleteRequest{uuid.NewString(), r.RecordID, r.Version}
	deleted, err := s.Delete(ctx, req)
	if err != nil || deleted.RecordID != r.RecordID || deleted.DeletedVersion != 2 {
		t.Fatalf("delete: %+v %v", deleted, err)
	}
	_, err = s.Get(ctx, r.RecordID)
	requireCode(t, err, "NOT_FOUND")
	again, err := s.Delete(ctx, req)
	if err != nil || again != deleted {
		t.Fatalf("delete retry: %+v %v", again, err)
	}
	_, err = s.Create(ctx, create)
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	_, err = s.Edit(ctx, edit)
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	var versions, audit, retainedResponses int
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.record_version WHERE record_id=$1`, r.RecordID).Scan(&versions); err != nil {
		t.Fatal(err)
	}
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.authority_event WHERE subject_id=$1`, r.RecordID).Scan(&audit); err != nil {
		t.Fatal(err)
	}
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.mutation_request WHERE request_id IN ($1,$2) AND response IS NOT NULL`, create.RequestID, edit.RequestID).Scan(&retainedResponses); err != nil {
		t.Fatal(err)
	}
	if versions != 0 || audit != 0 || retainedResponses != 0 {
		t.Fatalf("ordinary bodies or audit retained: %d %d %d", versions, audit, retainedResponses)
	}
}

func TestOrdinaryDeleteInvalidatesAllOrdinaryRevisionRetries(t *testing.T) {
	ctx := context.Background()
	repo := uuid.NewString()
	s := testStore(t, Channel{Principal: "ordinary-revision-delete:" + repo, Repo: repo})
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	revise := ReviseRequest{uuid.NewString(), r.RecordID, r.Version, repo, "A revised ordinary note"}
	if _, err = s.Revise(ctx, revise); err != nil {
		t.Fatal(err)
	}
	appendReq := AppendRequest{uuid.NewString(), r.RecordID, 2, repo, " with an appended passage"}
	if _, err = s.Append(ctx, appendReq); err != nil {
		t.Fatal(err)
	}
	replacement := " with a replacement passage"
	replace := ReplaceRequest{uuid.NewString(), r.RecordID, 3, repo, " with an appended passage", &replacement}
	if _, err = s.Replace(ctx, replace); err != nil {
		t.Fatal(err)
	}
	cite := CiteRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: 4, Repo: repo, EvidenceCitations: []EvidenceCitationRequest{}}
	if _, err = s.Cite(ctx, cite); err != nil {
		t.Fatal(err)
	}
	if _, err = s.Delete(ctx, DeleteRequest{uuid.NewString(), r.RecordID, 5}); err != nil {
		t.Fatal(err)
	}
	for name, retry := range map[string]func() error{
		"revise":  func() error { _, err := s.Revise(ctx, revise); return err },
		"append":  func() error { _, err := s.Append(ctx, appendReq); return err },
		"replace": func() error { _, err := s.Replace(ctx, replace); return err },
		"cite":    func() error { _, err := s.Cite(ctx, cite); return err },
	} {
		t.Run(name, func(t *testing.T) {
			requireCode(t, retry(), "PAYLOAD_UNAVAILABLE")
		})
	}
	revise.Body = "Changed intent after deletion"
	_, err = s.Revise(ctx, revise)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
}

func TestOrdinaryDeletePreservesReferencedAndFormerlyPrivilegedRecords(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	writer := testStore(t, Channel{Principal: "ordinary-delete-writer"})
	for _, kind := range []string{"exposure", "relation", "demoted", "conflict"} {
		t.Run(kind, func(t *testing.T) {
			repo := uuid.NewString()
			create := CreateRequest{uuid.NewString(), projectNote(repo)}
			r, err := writer.Create(ctx, create)
			if err != nil {
				t.Fatal(err)
			}
			var receipt string
			switch kind {
			case "exposure":
				p, err := writer.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
				if err != nil || len(p.Semantic.Selected) != 1 {
					t.Fatalf("compile: %+v %v", p, err)
				}
				receipt = p.ReceiptID
			case "relation":
				d := projectNote(repo)
				d.Relations = []RecordRelation{{r.RecordID, r.Version, "derived_from"}}
				if _, err = writer.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
					t.Fatal(err)
				}
			case "demoted":
				e := testEvidence(t, op, repo)
				r, err = op.Promote(ctx, PromoteRequest{uuid.NewString(), r.RecordID, r.Version, root.ID, []string{e.ID}, "Promote independently supported fixture", nil})
				if err != nil {
					t.Fatal(err)
				}
				r, err = op.Demote(ctx, DemoteRequest{uuid.NewString(), r.RecordID, r.Version, root.ID})
				if err != nil {
					t.Fatal(err)
				}
			case "conflict":
				other, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
				if err != nil {
					t.Fatal(err)
				}
				if _, err = writer.Dispute(ctx, DisputeRequest{RequestID: uuid.NewString(), RecordIDs: []string{r.RecordID, other.RecordID}, Reason: "Preserve disputed ordinary observations"}); err != nil {
					t.Fatal(err)
				}
			}
			_, err = writer.Delete(ctx, DeleteRequest{uuid.NewString(), r.RecordID, r.Version})
			if kind == "conflict" {
				requireCode(t, err, "OPEN_CONFLICT")
				var refusal *Error
				if !errors.As(err, &refusal) || refusal.RefusalID == "" {
					t.Fatal("conflict refusal was not retained")
				}
			} else {
				requireCode(t, err, "FORGET_REQUIRED")
			}
			if _, err = writer.Get(ctx, r.RecordID); err != nil {
				t.Fatal(err)
			}
			if _, err = writer.Create(ctx, create); err != nil {
				t.Fatalf("refused delete changed retry: %v", err)
			}
			if receipt != "" {
				if _, err = writer.Replay(ctx, receipt); err != nil {
					t.Fatal(err)
				}
			}
		})
	}
}

func TestCitationAndOrdinaryDeleteCannotBothCommit(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "ordinary-delete-race"})
	for range 8 {
		repo := uuid.NewString()
		source, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
		if err != nil {
			t.Fatal(err)
		}
		draft := projectNote(repo)
		draft.Relations = []RecordRelation{{source.RecordID, source.Version, "derived_from"}}
		start := make(chan struct{})
		cited := make(chan error, 1)
		deleted := make(chan error, 1)
		go func() { <-start; _, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft}); cited <- err }()
		go func() {
			<-start
			_, err := s.Delete(ctx, DeleteRequest{uuid.NewString(), source.RecordID, source.Version})
			deleted <- err
		}()
		close(start)
		citationErr, deletionErr := <-cited, <-deleted
		if citationErr == nil && deletionErr == nil {
			t.Fatal("citation and hard deletion both committed")
		}
		if citationErr != nil && Code(citationErr) != "NOT_FOUND" {
			t.Fatalf("citation: %v", citationErr)
		}
		if deletionErr != nil && Code(deletionErr) != "FORGET_REQUIRED" && Code(deletionErr) != "VERSION_CONFLICT" {
			t.Fatalf("delete: %v", deletionErr)
		}
	}
}
