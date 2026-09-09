package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestCouncilAuditRetractionRequiresPreview(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "audit-writer:" + repo})
	r, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	e := testEvidence(t, op, repo)
	b, err := op.Promote(ctx, PromoteRequest{uuid.NewString(), r.RecordID, 1, root.ID, []string{e.ID}, "Promote synthetic audit claim"})
	if err != nil {
		t.Fatal(err)
	}
	p, err := op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "planning", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil || len(p.Semantic.Selected) != 1 {
		t.Fatalf("fixture compile: %+v %v", p, err)
	}
	// No impact query or preview has been requested for this record.
	_, err = op.Retract(ctx, RetractRequest{uuid.NewString(), b.RecordID, b.Version, root.ID, "Retract synthetic audit claim", ""})
	requireCode(t, err, "IMPACT_PREVIEW_REQUIRED")
}

func TestRetractionPreviewInvalidationAndRetry(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := s.PreviewRetraction(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if len(preview.Uses) != 0 {
		t.Fatal("unexpected initial impact")
	}
	compile := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000}
	p, err := s.Compile(ctx, compile, Destination{"local", true})
	if err != nil || len(p.Semantic.Selected) != 1 {
		t.Fatalf("compile: %+v %v", p, err)
	}
	req := RetractRequest{uuid.NewString(), r.RecordID, r.Version, root.ID, "Retract advice after reviewing its consumers", preview.PreviewID}
	_, err = s.Retract(ctx, req)
	requireCode(t, err, "STALE_PREVIEW")
	other := testStore(t, Channel{Principal: "preview:other"})
	foreign, err := other.PreviewRetraction(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req.PreviewID = foreign.PreviewID
	_, err = s.Retract(ctx, req)
	requireCode(t, err, "IMPACT_PREVIEW_REQUIRED")
	expired, err := s.PreviewRetraction(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retraction_preview SET expires_at=transaction_timestamp()-interval '1 second' WHERE preview_id=$1`, expired.PreviewID); err != nil {
		t.Fatal(err)
	}
	req.PreviewID = expired.PreviewID
	_, err = s.Retract(ctx, req)
	requireCode(t, err, "STALE_PREVIEW")
	fresh, err := s.PreviewRetraction(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if len(fresh.Uses) != 1 || fresh.Uses[0].ReceiptID != p.ReceiptID {
		t.Fatalf("missing affected run: %+v", fresh)
	}
	// An idempotent compile retry must not invalidate a preview a second time.
	if _, err = s.Compile(ctx, compile, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	req.PreviewID = fresh.PreviewID
	retracted, err := s.Retract(ctx, req)
	if err != nil || retracted.Lifecycle != "retracted" {
		t.Fatalf("retraction: %+v %v", retracted, err)
	}
	retry, err := s.Retract(ctx, req)
	if err != nil || retry.Version != retracted.Version {
		t.Fatalf("retry: %+v %v", retry, err)
	}
	compile.RequestID = uuid.NewString()
	p, err = s.Compile(ctx, compile, Destination{"local", true})
	if err != nil || len(p.Semantic.Selected) != 0 {
		t.Fatalf("retracted advice escaped: %+v %v", p, err)
	}
}

func TestConcurrentCompileAndRetraction(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	for range 10 {
		repo := uuid.NewString()
		r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
		if err != nil {
			t.Fatal(err)
		}
		preview, err := s.PreviewRetraction(ctx, r.RecordID)
		if err != nil {
			t.Fatal(err)
		}
		start := make(chan struct{})
		type compileResult struct {
			p   Package
			err error
		}
		compiled := make(chan compileResult, 1)
		retracted := make(chan error, 1)
		go func() {
			<-start
			p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
			compiled <- compileResult{p, err}
		}()
		go func() {
			<-start
			_, err := s.Retract(ctx, RetractRequest{uuid.NewString(), r.RecordID, r.Version, root.ID, "Concurrent retraction fixture", preview.PreviewID})
			retracted <- err
		}()
		close(start)
		c, retractErr := <-compiled, <-retracted
		if c.err != nil {
			t.Fatal(c.err)
		}
		if retractErr != nil {
			requireCode(t, retractErr, "STALE_PREVIEW")
		}
		if retractErr == nil && len(c.p.Semantic.Selected) > 0 {
			t.Fatal("new exposure committed alongside an obsolete impact preview")
		}
	}
}
