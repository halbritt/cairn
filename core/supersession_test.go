package core

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
)

// PostgreSQL and cached JSON may represent the same instant with different
// Go Location pointers. Compare the timestamp instant and all other metadata.
func sameSupersession(a, b Supersession) bool {
	if !a.CreatedAt.Equal(b.CreatedAt) {
		return false
	}
	a.CreatedAt = b.CreatedAt
	return a == b
}

func TestSupersessionPreservesHistoryAndIndependentReplacement(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "supersession-writer", Repo: repo})
	makeClaim := func(body string) Record {
		t.Helper()
		d := projectNote(repo)
		d.Body = body
		a, err := writer.Create(ctx, CreateRequest{uuid.NewString(), d})
		if err != nil {
			t.Fatal(err)
		}
		e := testEvidence(t, op, repo)
		b, err := op.Promote(ctx, PromoteRequest{uuid.NewString(), a.RecordID, a.Version, root.ID, []string{e.ID}, "Independently qualify the synthetic claim", nil})
		if err != nil {
			t.Fatal(err)
		}
		return b
	}
	old := makeClaim("The old compiler workaround is obsolete.")
	query := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "compiler", Purpose: "planning", AvailableTokens: 64000}
	before, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil || len(before.Semantic.Selected) != 1 {
		t.Fatalf("before: %+v %v", before, err)
	}
	replacement := makeClaim("The corrected compiler workaround uses explicit flags.")
	d := projectNote(repo)
	d.Relations = []RecordRelation{{old.RecordID, old.Version, "derived_from"}}
	dependent, err := writer.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := op.PreviewRetraction(ctx, old.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req := SupersedeRequest{RequestID: uuid.NewString(), RecordID: old.RecordID, ExpectedVersion: old.Version, Replacement: RecordVersionRef{replacement.RecordID, replacement.Version}, GrantID: root.ID, PreviewID: preview.PreviewID, Reason: "Replace the obsolete compiler claim with independent support"}
	result, err := op.Supersede(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	if result.PreviousVersion != old.Version || result.RetiredVersion != old.Version+1 || result.Replacement != req.Replacement || result.EventID == "" {
		t.Fatalf("bad transition: %+v", result)
	}
	retry, err := op.Supersede(ctx, req)
	if err != nil || !sameSupersession(retry, result) {
		t.Fatalf("retry: %+v %v", retry, err)
	}
	current, err := writer.Get(ctx, old.RecordID)
	if err != nil || current.Lifecycle != "superseded" {
		t.Fatalf("current: %+v %v", current, err)
	}
	inspected, err := writer.Supersession(ctx, old.RecordID)
	if err != nil || !sameSupersession(inspected, result) {
		t.Fatalf("inspection: %+v %v", inspected, err)
	}
	query.RequestID = uuid.NewString()
	fresh, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil || len(fresh.Semantic.Selected) != 1 || fresh.Semantic.Selected[0].Record.RecordID != replacement.RecordID {
		t.Fatalf("fresh: %+v %v", fresh, err)
	}
	history, err := op.Recompile(ctx, RecompileRequest{before.ReceiptID, query.Query})
	if err != nil || history.Seal != before.Seal {
		t.Fatalf("history rewritten: %v", err)
	}
	docket, err := writer.Docket(ctx, repo)
	if err != nil {
		t.Fatal(err)
	}
	exposure, derivation := false, false
	for _, item := range docket.Items {
		if item.SupersededRecordID != old.RecordID {
			continue
		}
		exposure = exposure || (item.Reason == "SUPERSEDED_EXPOSURE" && item.ReceiptID == before.ReceiptID && item.Version == old.Version)
		derivation = derivation || (item.Reason == "SUPERSEDED_DEPENDENCY" && item.RecordID == dependent.RecordID)
	}
	if !exposure || !derivation {
		t.Fatalf("missing review notices: %+v", docket)
	}
	// Revising the replacement must not retarget the historical replacement
	// link. Reviewing a dependent creates a new version; its old notice survives
	// as history but no longer asks for review of the current version.
	revisedDraft := replacement.Draft
	revisedDraft.Body = "A later refinement of the independently supported compiler workaround."
	e := testEvidence(t, op, repo)
	_, err = op.Correct(ctx, CorrectRequest{RequestID: uuid.NewString(), RecordID: replacement.RecordID, ExpectedVersion: replacement.Version, GrantID: root.ID, Draft: revisedDraft, EvidenceIDs: []string{e.ID}, Reason: "Refine the replacement without rewriting its old version"})
	if err != nil {
		t.Fatal(err)
	}
	inspected, err = writer.Supersession(ctx, old.RecordID)
	if err != nil || !sameSupersession(inspected, result) {
		t.Fatalf("replacement link drifted: %+v %v", inspected, err)
	}
	d.Relations = nil
	d.Body = "Reviewed dependent with the obsolete derivation removed."
	_, err = writer.Edit(ctx, EditRequest{uuid.NewString(), dependent.RecordID, dependent.Version, d})
	if err != nil {
		t.Fatal(err)
	}
	docket, err = writer.Docket(ctx, repo)
	if err != nil {
		t.Fatal(err)
	}
	for _, item := range docket.Items {
		if item.Reason == "SUPERSEDED_DEPENDENCY" && item.RecordID == dependent.RecordID {
			t.Fatalf("reviewed dependent still current in docket: %+v", item)
		}
	}
	// The historical replacement link is not a claim of derivation. Forgetting
	// the obsolete body must not invalidate the independently supported claim.
	deletion, err := op.PreviewDeletion(ctx, old.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: old.RecordID, ExpectedVersion: current.Version, GrantID: root.ID, PreviewID: deletion.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	query.RequestID = uuid.NewString()
	fresh, err = op.Compile(ctx, query, Destination{"local", true})
	if err != nil || len(fresh.Semantic.Selected) != 1 || fresh.Semantic.Selected[0].Record.RecordID != replacement.RecordID {
		t.Fatalf("replacement incorrectly depends on old body: %+v %v", fresh, err)
	}
	inspected, err = writer.Supersession(ctx, old.RecordID)
	if err != nil || !sameSupersession(inspected, result) {
		t.Fatalf("forgotten history metadata: %+v %v", inspected, err)
	}
}

func TestSupersessionRefusalsLeaveTheSourceActive(t *testing.T) {
	for _, scenario := range []struct{ name, code string }{
		{"stale source", "VERSION_CONFLICT"}, {"stale replacement", "VERSION_CONFLICT"},
		{"missing preview", "IMPACT_PREVIEW_REQUIRED"}, {"new exposure", "STALE_PREVIEW"},
		{"wrong actor", "AUTHORITY_DENIED"}, {"ordinary replacement", "AUTHORITY_DENIED"},
		{"revoked replacement authority", "AUTHORITY_DENIED"}, {"missing evidence", "EVIDENCE_UNAVAILABLE"},
		{"broader scope", "AUTHORITY_DENIED"}, {"broader applicability", "AUTHORITY_DENIED"},
		{"broader sensitivity", "AUTHORITY_DENIED"}, {"expired replacement", "INAPPLICABLE"},
		{"open conflict", "OPEN_CONFLICT"}, {"dependent instruction", "DEPENDENCY_CONFLICT"},
		{"self replacement", "INVALID_REQUEST"},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			ctx := context.Background()
			op, root := testOperator(t)
			repo := uuid.NewString()
			writer := testStore(t, Channel{Principal: "supersession-refusal:" + repo, Repo: repo})
			aDraft, bDraft := projectNote(repo), projectNote(repo)
			bDraft.Body = "Independently supported replacement."
			if scenario.name == "broader scope" {
				aDraft.Scope.TaskID = "narrow"
			}
			if scenario.name == "broader applicability" {
				aDraft.Pins = &Applicability{TaskClass: "build"}
			}
			if scenario.name == "broader sensitivity" {
				bDraft.Sensitivity = "shareable"
			}
			if scenario.name == "expired replacement" {
				past := time.Now().Add(-time.Hour)
				bDraft.Pins = &Applicability{ValidUntil: &past}
			}
			makeB := func(d Draft, promoter *Store, grant string) (Record, Evidence) {
				t.Helper()
				a, err := writer.Create(ctx, CreateRequest{uuid.NewString(), d})
				if err != nil {
					t.Fatal(err)
				}
				e := testEvidence(t, op, repo)
				// Shareable claims require shareable evidence as well.
				if d.Sensitivity == "shareable" {
					e, err = op.CaptureEvidence(ctx, EvidenceRequest{uuid.NewString(), repo, "Synthetic shareable support", "fixture", "shareable"})
					if err != nil {
						t.Fatal(err)
					}
				}
				b, err := promoter.Promote(ctx, PromoteRequest{uuid.NewString(), a.RecordID, a.Version, grant, []string{e.ID}, "Independently promote the refusal fixture", nil})
				if err != nil {
					t.Fatal(err)
				}
				return b, e
			}
			old, _ := makeB(aDraft, op, root.ID)
			promoter, grant := op, root.ID
			if scenario.name == "revoked replacement authority" {
				promoter = testStore(t, Channel{Principal: "replacement-promoter:" + repo, Repo: repo})
				g, err := op.Grant(ctx, GrantRequest{RequestID: uuid.NewString(), ParentID: root.ID, Principal: promoter.channel.Principal, Repo: repo, Capabilities: []string{"promote"}, Reason: "Grant only independent replacement promotion"})
				if err != nil {
					t.Fatal(err)
				}
				grant = g.ID
			}
			replacement, evidence := makeB(bDraft, promoter, grant)
			preview, err := op.PreviewRetraction(ctx, old.RecordID)
			if err != nil {
				t.Fatal(err)
			}
			req := SupersedeRequest{RequestID: uuid.NewString(), RecordID: old.RecordID, ExpectedVersion: old.Version, Replacement: RecordVersionRef{replacement.RecordID, replacement.Version}, GrantID: root.ID, PreviewID: preview.PreviewID, Reason: "Attempt the independently checked replacement"}
			caller := op
			switch scenario.name {
			case "stale source":
				req.ExpectedVersion--
			case "stale replacement":
				req.Replacement.Version--
			case "missing preview":
				req.PreviewID = ""
			case "new exposure":
				_, err = op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "planning", AvailableTokens: 64000}, Destination{"local", true})
			case "wrong actor":
				caller = writer
			case "ordinary replacement":
				replacement, err = op.Demote(ctx, DemoteRequest{uuid.NewString(), replacement.RecordID, replacement.Version, root.ID})
				req.Replacement.Version = replacement.Version
			case "revoked replacement authority":
				_, err = op.RevokeGrant(ctx, RevokeGrantRequest{RequestID: uuid.NewString(), GrantID: grant, ExpectedVersion: 1, AuthorityID: root.ID, Reason: "Revoke the replacement qualification authority"})
			case "missing evidence":
				_, err = op.pool.Exec(ctx, `UPDATE cairn.evidence SET state='dangling' WHERE evidence_id=$1`, evidence.ID)
			case "open conflict":
				_, err = op.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{old.RecordID, replacement.RecordID}, "Retain both disputed fixture positions"})
			case "dependent instruction":
				d := projectNote(repo)
				d.Kind = "instruction"
				d.Relations = []RecordRelation{{old.RecordID, old.Version, "derived_from"}}
				_, err = op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, RequiresRuntime: false, PolicyKey: "supersession-guard", Reason: "Issue an instruction depending on the source"})
			case "self replacement":
				req.Replacement.RecordID = old.RecordID
				req.Replacement.Version = old.Version
			}
			if err != nil {
				t.Fatal(err)
			}
			_, err = caller.Supersede(ctx, req)
			requireCode(t, err, scenario.code)
			current, err := op.Get(ctx, old.RecordID)
			if err != nil || current.Version != old.Version || current.Lifecycle != "active" {
				t.Fatalf("refusal changed source: %+v %v", current, err)
			}
			_, err = op.Supersession(ctx, old.RecordID)
			requireCode(t, err, "NOT_FOUND")
		})
	}
}

func TestSupersessionConcurrentReplacementsHaveOneWinner(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "supersession-race"})
	repo := uuid.NewString()
	records := make([]Record, 3)
	for i := range records {
		r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
		if err != nil {
			t.Fatal(err)
		}
		records[i] = r
	}
	p, err := s.PreviewRetraction(ctx, records[0].RecordID)
	if err != nil {
		t.Fatal(err)
	}
	start := make(chan struct{})
	results := make(chan error, 2)
	var wg sync.WaitGroup
	for _, replacement := range records[1:] {
		wg.Add(1)
		go func(r Record) {
			defer wg.Done()
			<-start
			_, err := s.Supersede(ctx, SupersedeRequest{RequestID: uuid.NewString(), RecordID: records[0].RecordID, ExpectedVersion: 1, Replacement: RecordVersionRef{r.RecordID, 1}, PreviewID: p.PreviewID, Reason: "Concurrent proposals must retire the source only once"})
			results <- err
		}(replacement)
	}
	close(start)
	wg.Wait()
	close(results)
	wins := 0
	for err := range results {
		if err == nil {
			wins++
		} else {
			requireCode(t, err, "VERSION_CONFLICT")
		}
	}
	if wins != 1 {
		t.Fatalf("supersession winners=%d", wins)
	}
	transition, err := s.Supersession(ctx, records[0].RecordID)
	if err != nil || transition.RetiredVersion != 2 {
		t.Fatalf("race result: %+v %v", transition, err)
	}
	// An inactive source cannot be revived to manufacture a replacement cycle.
	p, err = s.PreviewRetraction(ctx, transition.Replacement.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Supersede(ctx, SupersedeRequest{RequestID: uuid.NewString(), RecordID: transition.Replacement.RecordID, ExpectedVersion: 1, Replacement: RecordVersionRef{records[0].RecordID, 2}, PreviewID: p.PreviewID, Reason: "Reject a cycle back to the inactive source"})
	requireCode(t, err, "VERSION_CONFLICT")
}

func TestOrdinarySupersessionNarrowsScopeWithoutAuthority(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "ordinary-supersession", Repo: repo})
	old, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	d := projectNote(repo)
	d.Scope.TaskID = "narrow"
	d.Body = "A narrower replacement note."
	replacement, err := writer.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := writer.PreviewRetraction(ctx, old.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req := SupersedeRequest{RequestID: uuid.NewString(), RecordID: old.RecordID, ExpectedVersion: old.Version, Replacement: RecordVersionRef{replacement.RecordID, replacement.Version}, PreviewID: preview.PreviewID, Reason: "Retire the broad note in favor of a narrow replacement"}
	result, err := writer.Supersede(ctx, req)
	if err != nil || result.EventID != "" {
		t.Fatalf("ordinary supersession: %+v %v", result, err)
	}
	var events int
	if err = op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.authority_event WHERE subject_id=$1`, old.RecordID).Scan(&events); err != nil || events != 0 {
		t.Fatalf("A retirement created authority: %d %v", events, err)
	}
	pkg, err := writer.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "other", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil || len(pkg.Semantic.Selected) != 0 {
		t.Fatalf("narrow replacement escaped scope: %+v %v", pkg, err)
	}
	outsider := testStore(t, Channel{Principal: "other-supersession", Repo: uuid.NewString()})
	_, err = outsider.Supersession(ctx, old.RecordID)
	requireCode(t, err, "AUTHORITY_DENIED")
}
