package core

import (
	"context"
	"sync"
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
	b, err := op.Promote(ctx, PromoteRequest{uuid.NewString(), r.RecordID, 1, root.ID, []string{e.ID}, "Promote synthetic audit claim", nil})
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

func TestConcurrentOverlappingExpandsAndDependentRetraction(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	parentDraft := projectNote(repo)
	parentDraft.Body = "parent exposure barrier fixture_error note"
	parent, err := s.Create(ctx, CreateRequest{uuid.NewString(), parentDraft})
	if err != nil {
		t.Fatal(err)
	}
	depDraft := projectNote(repo)
	depDraft.Body = "dependent exposure barrier fixture_error note"
	depDraft.Relations = []RecordRelation{{parent.RecordID, parent.Version, "derived_from"}}
	dep, err := s.Create(ctx, CreateRequest{uuid.NewString(), depDraft})
	if err != nil {
		t.Fatal(err)
	}
	// Four concurrent writers repeatedly index AND pull the same two notes with zero VERSION_CONFLICT.
	const workers, rounds = 4, 8
	var wg sync.WaitGroup
	errs := make(chan error, workers*rounds)
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for r := 0; r < rounds; r++ {
				idx, err := s.Index(ctx, CompileRequest{
					RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"},
					Query: "fixture_error", Purpose: "context", AvailableTokens: 32000,
				}, Destination{"local", true})
				if err != nil {
					errs <- err
					return
				}
				for _, h := range idx.Handles {
					if _, err := s.Expand(ctx, ExpandRequest{
						RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: h.Handle,
					}, Destination{"local", true}); err != nil {
						errs <- err
						return
					}
				}
			}
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Fatalf("concurrent index+expand failed: %v", err)
	}
	var parentGen, depGen int64
	if err := s.pool.QueryRow(ctx, `SELECT cairn.record_exposure_generation($1), cairn.record_exposure_generation($2)`, parent.RecordID, dep.RecordID).Scan(&parentGen, &depGen); err != nil {
		t.Fatal(err)
	}
	wantDepGen := int64(workers * rounds * 2) // 1 index exposure + 1 expand observation per round
	wantParentGen := wantDepGen + 1           // +1 from dep's incoming derived_from relation (013_record_relations.sql)
	if parentGen != wantParentGen || depGen != wantDepGen {
		t.Fatalf("exposure generation mismatch: parent=%d (want %d) dep=%d (want %d)", parentGen, wantParentGen, depGen, wantDepGen)
	}
	// Dependent exposure invalidates parent retraction preview!
	preview, err := s.PreviewRetraction(ctx, parent.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Index(ctx, CompileRequest{
		RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"},
		Query: "dependent", Purpose: "context", AvailableTokens: 32000,
	}, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	_, err = s.Retract(ctx, RetractRequest{
		RequestID: uuid.NewString(), RecordID: parent.RecordID, ExpectedVersion: parent.Version,
		GrantID: root.ID, Reason: "Dependent exposure invalidation check", PreviewID: preview.PreviewID,
	})
	requireCode(t, err, "STALE_PREVIEW")
}
