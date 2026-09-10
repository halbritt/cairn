package core

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestAppendPreservesMetadataHistoryAndRetry(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body, d.Sensitivity = "Existing instructions.\r\n日本語  ", "shareable"
	d.Kind, d.Pins = "procedure", &Applicability{TaskClass: "build"}
	d.Entities = []EntityRef{{Kind: "file", Name: "core/revise.go"}}
	d.ClaimType, d.AttributedProducer, d.AttemptID, d.ResultRef = "partial", "agent:original", uuid.NewString(), "retained-result"
	sourceDraft := projectNote(repo)
	sourceDraft.Sensitivity = "shareable"
	source, err := op.Create(ctx, CreateRequest{uuid.NewString(), sourceDraft})
	if err != nil {
		t.Fatal(err)
	}
	d.Relations = []RecordRelation{{source.RecordID, 1, "derived_from"}}
	old, err := op.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	writer := testStore(t, Channel{Principal: "agent:appender", Repo: repo})
	req := AppendRequest{uuid.NewString(), old.RecordID, 1, repo, "\n\nSelected addition.\n"}
	revision, err := writer.Append(ctx, req)
	if err != nil || revision != (Revision{old.RecordID, 2}) {
		t.Fatalf("append: %+v %v", revision, err)
	}
	got, err := op.Get(ctx, old.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	want := old.Draft
	want.Body += req.Body
	if !reflect.DeepEqual(got.Draft, want) || got.Sensitivity != old.Sensitivity || got.Class != "A" || got.ObservedWriter != "agent:appender" || got.Witness != "testimony" {
		t.Fatalf("append changed metadata or bytes: %+v", got)
	}
	history, err := writer.History(ctx, RecordHistoryRequest{RecordID: old.RecordID, Version: 1}, Destination{"hosted", false})
	if err != nil || len(history.Versions) != 1 || history.Versions[0].Body == nil || *history.Versions[0].Body != old.Body {
		t.Fatalf("history: %+v %v", history, err)
	}
	if _, err = writer.Revise(ctx, ReviseRequest{uuid.NewString(), old.RecordID, 2, repo, "Deliberate replacement."}); err != nil {
		t.Fatal(err)
	}
	retry, err := writer.Append(ctx, req)
	if err != nil || retry != revision {
		t.Fatalf("retry followed later edit: %+v %v", retry, err)
	}
	got, err = op.Get(ctx, old.RecordID)
	if err != nil || got.Version != 3 || got.Body != "Deliberate replacement." {
		t.Fatalf("retry mutated note: %+v %v", got, err)
	}
	req.Body += "changed intent"
	_, err = writer.Append(ctx, req)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	req.RequestID = uuid.NewString()
	_, err = writer.Append(ctx, req)
	requireCode(t, err, "VERSION_CONFLICT")
	req.ExpectedVersion = 3
	req.Repo = "foreign"
	_, err = op.Append(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
	req.Repo = repo
	if _, err = writer.Delete(ctx, DeleteRequest{uuid.NewString(), old.RecordID, 3}); err != nil {
		t.Fatal(err)
	}
	_, err = writer.Append(ctx, req)
	requireCode(t, err, "NOT_FOUND")
}

func TestAppendCombinedByteLimitAndFailureRollback(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:append-limit"})
	d := projectNote(uuid.NewString())
	d.Body = strings.Repeat("<", 65533)
	old, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	req := AppendRequest{uuid.NewString(), old.RecordID, 1, d.Scope.Repo, "日本"}
	_, err = s.Append(ctx, req)
	requireCode(t, err, "INVALID_REQUEST")
	got, err := s.Get(ctx, old.RecordID)
	if err != nil || got.Version != 1 || got.Body != d.Body {
		t.Fatalf("overflow mutated note: %+v %v", got, err)
	}
	req.Body = "日" // Three UTF-8 bytes; failed intent must not reserve the request ID.
	revision, err := s.Append(ctx, req)
	if err != nil || revision.Version != 2 {
		t.Fatalf("exact limit: %+v %v", revision, err)
	}
	got, err = s.Get(ctx, old.RecordID)
	if err != nil || got.Body != d.Body+req.Body || len(got.Body) != 65536 {
		t.Fatalf("exact bytes: version=%d bytes=%d err=%v", got.Version, len(got.Body), err)
	}
	for _, suffix := range []string{"", " \n", strings.Repeat("x", 65537)} {
		_, err = (*Store)(nil).Append(ctx, AppendRequest{uuid.NewString(), old.RecordID, 2, d.Scope.Repo, suffix})
		requireCode(t, err, "INVALID_REQUEST")
	}
}

func TestAppendRacingEditsKeepsOneWholeWinner(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:append-race"})
	for _, full := range []bool{false, true} {
		for range 5 {
			d := projectNote(uuid.NewString())
			d.Body = "Original."
			old, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
			if err != nil {
				t.Fatal(err)
			}
			start := make(chan struct{})
			errs := make(chan error, 2)
			go func() {
				<-start
				_, e := s.Append(ctx, AppendRequest{uuid.NewString(), old.RecordID, 1, d.Scope.Repo, " Addition A."})
				errs <- e
			}()
			go func() {
				<-start
				if full {
					changed := d
					changed.Body = "Replacement."
					changed.Kind = "procedure"
					_, e := s.Edit(ctx, EditRequest{uuid.NewString(), old.RecordID, 1, changed})
					errs <- e
				} else {
					_, e := s.Append(ctx, AppendRequest{uuid.NewString(), old.RecordID, 1, d.Scope.Repo, " Addition B."})
					errs <- e
				}
			}()
			close(start)
			wins := 0
			for range 2 {
				if e := <-errs; e == nil {
					wins++
				} else {
					requireCode(t, e, "VERSION_CONFLICT")
				}
			}
			got, err := s.Get(ctx, old.RecordID)
			valid := got.Body == d.Body+" Addition A." && got.Kind == d.Kind
			if full {
				valid = valid || got.Body == "Replacement." && got.Kind == "procedure"
			} else {
				valid = valid || got.Body == d.Body+" Addition B." && got.Kind == d.Kind
			}
			if err != nil || wins != 1 || got.Version != 2 || !valid {
				t.Fatalf("mixed or lost update: %+v wins=%d err=%v", got, wins, err)
			}
		}
	}
}

func TestAppendRetainsDegradedCitationsAndRefusesQualifiedNotes(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "agent:append-source", Repo: repo})
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	note, err := writer.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	e, err := writer.CaptureEvidence(ctx, EvidenceRequest{RequestID: uuid.NewString(), Repo: repo, Body: "Selected source", Source: "fixture", Sensitivity: "shareable"})
	if err != nil {
		t.Fatal(err)
	}
	ref := EvidenceCitationRequest{EvidenceID: e.ID, ExpectedSHA256: e.Digest, Spans: []ByteSpanRequest{{Offset: 0, Length: 8}}}
	_, err = writer.Cite(ctx, CiteRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 1, Repo: repo, EvidenceCitations: []EvidenceCitationRequest{ref}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.pool.Exec(ctx, `UPDATE cairn.evidence SET body=$2 WHERE evidence_id=$1`, e.ID, []byte("Changed source")); err != nil {
		t.Fatal(err)
	}
	_, err = writer.Append(ctx, AppendRequest{uuid.NewString(), note.RecordID, 2, repo, " Source may need review."})
	if err != nil {
		t.Fatal(err)
	}
	p, err := writer.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"hosted", false})
	if err != nil || len(p.Semantic.Selected) != 1 {
		t.Fatalf("compile: %+v %v", p, err)
	}
	refs := p.Semantic.Selected[0].Evidence
	if len(refs) != 1 || refs[0].State != "divergent" || refs[0].Citation == nil || refs[0].Citation.SHA256 != e.Digest || !reflect.DeepEqual(refs[0].Citation.Spans, ref.Spans) {
		t.Fatalf("citation changed: %+v", refs)
	}
	evidence, err := op.CaptureEvidence(ctx, EvidenceRequest{RequestID: uuid.NewString(), Repo: repo, Body: "Independent qualifying source", Source: "fixture", Sensitivity: "shareable"})
	if err != nil {
		t.Fatal(err)
	}
	qualified, err := op.Promote(ctx, PromoteRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 3, GrantID: root.ID, EvidenceIDs: []string{evidence.ID}, Reason: "Qualify append refusal fixture"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = writer.Append(ctx, AppendRequest{uuid.NewString(), note.RecordID, qualified.Version, repo, " Unprivileged change."})
	requireCode(t, err, "AUTHORITY_DENIED")
	instruction, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: "instruction", Body: "Selected rule", Scope: Scope{repo, "*", "*"}, ClaimType: "self", Sensitivity: "shareable"}, GrantID: root.ID, PolicyKey: "append-fixture", Reason: "Append refusal fixture"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = writer.Append(ctx, AppendRequest{uuid.NewString(), instruction.RecordID, instruction.Version, repo, " Unprivileged change."})
	requireCode(t, err, "AUTHORITY_DENIED")
}
