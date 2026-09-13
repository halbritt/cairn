package core

import (
	"context"
	"reflect"
	"strings"
	"sync"
	"testing"

	"github.com/google/uuid"
)

func TestOrdinaryCitationsRetainSourceThroughRevisionAndPull(t *testing.T) {
	ctx := context.Background()
	repo := uuid.NewString()
	s := testStore(t, Channel{Principal: "agent:source-reader", Repo: repo})
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	draft.Body = "Check the retained installation procedure before restarting the service."
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	e, err := s.CaptureEvidence(ctx, EvidenceRequest{RequestID: uuid.NewString(), Repo: repo, Body: "Context\nBack up before upgrade.\n", Source: "selected procedure", Sensitivity: "shareable"})
	if err != nil {
		t.Fatal(err)
	}
	span := ByteSpanRequest{Offset: len("Context\n"), Length: len("Back up before upgrade.")}
	req := CiteRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: 1, Repo: repo, EvidenceCitations: []EvidenceCitationRequest{{EvidenceID: e.ID, ExpectedSHA256: e.Digest, Spans: []ByteSpanRequest{span}}}}
	cited, err := s.Cite(ctx, req)
	if err != nil || cited.Version != 2 {
		t.Fatalf("cite: %+v %v", cited, err)
	}
	got, err := s.Get(ctx, r.RecordID)
	if err != nil || got.Class != "A" || !reflect.DeepEqual(got.Draft, r.Draft) {
		t.Fatalf("citation changed note: %+v %v", got, err)
	}
	_, err = s.Revise(ctx, ReviseRequest{uuid.NewString(), r.RecordID, 2, repo, draft.Body + " Use the current source."})
	if err != nil {
		t.Fatal(err)
	}
	index, err := s.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "build", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"hosted", false})
	if err != nil || len(index.Handles) != 1 {
		t.Fatalf("index: %+v %v", index, err)
	}
	handle := index.Handles[0].Handle
	pull, err := s.Expand(ctx, ExpandRequest{uuid.NewString(), index.Package.ReceiptID, handle, nil}, Destination{"hosted", false})
	if err != nil || len(pull.Selection.Evidence) != 1 {
		t.Fatalf("source lost: %+v %v", pull, err)
	}
	ref := pull.Selection.Evidence[0]
	if ref.Citation == nil || ref.Citation.SHA256 != e.Digest || !reflect.DeepEqual(ref.Citation.Spans, []ByteSpanRequest{span}) {
		t.Fatalf("citation metadata: %+v", ref)
	}
	source, err := s.ExpandEvidence(ctx, ExpandEvidenceRequest{e.Digest, uuid.NewString(), index.Package.ReceiptID, handle, e.ID, &span}, Destination{"hosted", false})
	if err != nil || source.Span == nil || source.Span.Body != "Back up before upgrade." {
		t.Fatalf("source passage: %+v %v", source, err)
	}
	retry, err := s.Cite(ctx, req)
	if err != nil || retry != cited {
		t.Fatalf("retry changed with current version: %+v %v", retry, err)
	}
	plan, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "plan", "run"}, Purpose: "planning", AvailableTokens: 64000}, Destination{"hosted", false})
	if err != nil || len(plan.Semantic.Selected) != 0 {
		t.Fatalf("citation conferred qualification: %+v %v", plan, err)
	}
	replayed, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID})
	if err != nil || replayed.Seal != index.Package.Seal {
		t.Fatalf("frozen citation: %+v %v", replayed, err)
	}
}

func TestOrdinaryCitationRefusalsAndClearingPreserveHistory(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	r, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	writer := testStore(t, Channel{Principal: "agent:citer", Repo: repo})
	capture := func(sourceRepo, sensitivity string) Evidence {
		t.Helper()
		e, err := op.CaptureEvidence(ctx, EvidenceRequest{RequestID: uuid.NewString(), Repo: sourceRepo, Body: "retained source", Source: "selected source", Sensitivity: sensitivity})
		if err != nil {
			t.Fatal(err)
		}
		return e
	}
	e, local, outside := capture(repo, "shareable"), capture(repo, "local"), capture(uuid.NewString(), "shareable")
	ref := func(e Evidence) EvidenceCitationRequest {
		return EvidenceCitationRequest{EvidenceID: e.ID, ExpectedSHA256: e.Digest}
	}
	req := CiteRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: 1, Repo: repo, EvidenceCitations: []EvidenceCitationRequest{ref(e)}}
	for _, tc := range []struct {
		name string
		refs []EvidenceCitationRequest
		code string
	}{
		{"missing list", nil, "INVALID_REQUEST"},
		{"duplicate", []EvidenceCitationRequest{ref(e), ref(e)}, "INVALID_REQUEST"},
		{"missing digest", []EvidenceCitationRequest{{EvidenceID: e.ID}}, "INVALID_REQUEST"},
		{"wrong digest", []EvidenceCitationRequest{{EvidenceID: e.ID, ExpectedSHA256: strings.Repeat("0", 64)}}, "EVIDENCE_UNAVAILABLE"},
		{"out of range", []EvidenceCitationRequest{{EvidenceID: e.ID, ExpectedSHA256: e.Digest, Spans: []ByteSpanRequest{{Offset: 1, Length: 99}}}}, "INVALID_REQUEST"},
		{"local source", []EvidenceCitationRequest{ref(local)}, "DESTINATION_PROHIBITED"},
		{"foreign source", []EvidenceCitationRequest{ref(outside)}, "EVIDENCE_UNAVAILABLE"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			bad := req
			bad.EvidenceCitations = tc.refs
			_, err := writer.Cite(ctx, bad)
			requireCode(t, err, tc.code)
			got, err := op.Get(ctx, r.RecordID)
			if err != nil || got.Version != 1 {
				t.Fatalf("refused update changed note: %+v %v", got, err)
			}
		})
	}
	bad := req
	bad.Repo = outside.ID
	_, err = writer.Cite(ctx, bad)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = op.Cite(ctx, bad)
	requireCode(t, err, "AUTHORITY_DENIED")
	if _, err = writer.Cite(ctx, req); err != nil {
		t.Fatal(err)
	}
	bad = req
	bad.EvidenceCitations = []EvidenceCitationRequest{}
	_, err = writer.Cite(ctx, bad)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	bad.RequestID = uuid.NewString()
	_, err = writer.Cite(ctx, bad)
	requireCode(t, err, "VERSION_CONFLICT")
	// Corrupt only this disposable source. Ordinary editing must retain the
	// degraded citation without pretending that it checked new support.
	if _, err = op.pool.Exec(ctx, `UPDATE cairn.evidence SET body=$2 WHERE evidence_id=$1`, e.ID, []byte("changed source")); err != nil {
		t.Fatal(err)
	}
	got, err := op.Get(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	got.Body += " revised"
	if _, err = writer.Edit(ctx, EditRequest{uuid.NewString(), r.RecordID, 2, got.Draft}); err != nil {
		t.Fatal(err)
	}
	compile := func() Package {
		t.Helper()
		p, err := writer.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"hosted", false})
		if err != nil || len(p.Semantic.Selected) != 1 {
			t.Fatalf("compile: %+v %v", p, err)
		}
		return p
	}
	before := compile()
	if refs := before.Semantic.Selected[0].Evidence; len(refs) != 1 || refs[0].State != "divergent" {
		t.Fatalf("degraded reference lost: %+v", refs)
	}
	bad.ExpectedVersion = 3
	if _, err = writer.Cite(ctx, bad); err != nil {
		t.Fatal(err)
	}
	if refs := compile().Semantic.Selected[0].Evidence; len(refs) != 0 {
		t.Fatalf("clear retained current citations: %+v", refs)
	}
	replay, err := writer.Recompile(ctx, RecompileRequest{ReceiptID: before.ReceiptID})
	if err != nil || replay.Seal != before.Seal {
		t.Fatalf("clear rewrote history: %+v %v", replay, err)
	}
	// Qualified support still requires the existing operator-authorized path.
	qualified, err := writer.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	support := capture(repo, "shareable")
	qualified, err = op.Promote(ctx, PromoteRequest{RequestID: uuid.NewString(), RecordID: qualified.RecordID, ExpectedVersion: 1, GrantID: root.ID, EvidenceIDs: []string{support.ID}, Reason: "independently qualify fixture"})
	if err != nil {
		t.Fatal(err)
	}
	bad.RecordID, bad.ExpectedVersion = qualified.RecordID, qualified.Version
	bad.RequestID = uuid.NewString()
	_, err = writer.Cite(ctx, bad)
	requireCode(t, err, "AUTHORITY_DENIED")
}

func TestOrdinaryCitationRacingRevisionHonorsExpectedVersion(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:citation-race"})
	repo := uuid.NewString()
	e, err := s.CaptureEvidence(ctx, EvidenceRequest{RequestID: uuid.NewString(), Repo: repo, Body: "source", Source: "race source"})
	if err != nil {
		t.Fatal(err)
	}
	for range 5 {
		r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
		if err != nil {
			t.Fatal(err)
		}
		start := make(chan struct{})
		results := make(chan error, 2)
		var wg sync.WaitGroup
		wg.Add(2)
		go func() {
			defer wg.Done()
			<-start
			_, err := s.Cite(ctx, CiteRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: 1, Repo: repo, EvidenceCitations: []EvidenceCitationRequest{{EvidenceID: e.ID, ExpectedSHA256: e.Digest}}})
			results <- err
		}()
		go func() {
			defer wg.Done()
			<-start
			_, err := s.Revise(ctx, ReviseRequest{uuid.NewString(), r.RecordID, 1, repo, "changed body"})
			results <- err
		}()
		close(start)
		wg.Wait()
		close(results)
		ok, conflict := 0, 0
		for err := range results {
			if err == nil {
				ok++
			} else if Code(err) == "VERSION_CONFLICT" {
				conflict++
			} else {
				t.Fatal(err)
			}
		}
		if ok != 1 || conflict != 1 {
			t.Fatalf("race outcomes: success=%d conflict=%d", ok, conflict)
		}
	}
}
