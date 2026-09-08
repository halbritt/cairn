package core

import (
	"context"
	"sync"
	"testing"

	"github.com/google/uuid"
)

func TestScopeAuthorizationRetainsIndependentQualificationAndReplay(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "scope-writer:" + repo, Repo: repo})
	qualifier := testStore(t, Channel{Principal: "scope-qualifier:" + repo, Repo: repo})
	issuer := testStore(t, Channel{Principal: "scope-issuer:" + repo, Repo: repo})
	grantFor := func(s *Store, capability string) Grant {
		t.Helper()
		g, err := op.Grant(ctx, GrantRequest{RequestID: uuid.NewString(), ParentID: root.ID, Principal: s.channel.Principal, Repo: repo, Capabilities: []string{capability}, Reason: "Grant independent scope fixture authority"})
		if err != nil {
			t.Fatal(err)
		}
		return g
	}
	qualification, scopeGrant := grantFor(qualifier, "promote"), grantFor(issuer, "issue")
	draft := projectNote(repo)
	draft.Scope.TaskID = "original"
	draft.Pins = &Applicability{TaskClass: "build"}
	a, err := writer.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	e := testEvidence(t, op, repo)
	b, err := qualifier.Promote(ctx, PromoteRequest{uuid.NewString(), a.RecordID, a.Version, qualification.ID, []string{e.ID}, "Qualify the original narrowly applicable claim"})
	if err != nil {
		t.Fatal(err)
	}
	query := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "original", "run"}, Context: &ContextPins{TaskClass: "build"}, Purpose: "planning", AvailableTokens: 64000}
	before, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil || len(before.Semantic.Selected) != 1 {
		t.Fatalf("initial: %+v %v", before, err)
	}
	query.Scope.TaskID = "other"
	query.Context = nil
	query.RequestID = uuid.NewString()
	outside, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil || len(outside.Semantic.Selected) != 0 {
		t.Fatalf("narrow claim escaped: %+v %v", outside, err)
	}
	preview, err := issuer.PreviewRetraction(ctx, b.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req := AuthorizeScopeRequest{RequestID: uuid.NewString(), RecordID: b.RecordID, ExpectedVersion: b.Version, Scope: Scope{repo, "*", "*"}, Pins: &Applicability{}, GrantID: scopeGrant.ID, PreviewID: preview.PreviewID, Reason: "Authorize the same supported claim across repository tasks"}
	change, err := issuer.AuthorizeScope(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	if change.Version != b.Version+1 || change.GrantID != scopeGrant.ID {
		t.Fatalf("scope transition: %+v", change)
	}
	retry, err := issuer.AuthorizeScope(ctx, req)
	if err != nil || retry.EventID != change.EventID {
		t.Fatalf("scope retry: %+v %v", retry, err)
	}
	query.RequestID = uuid.NewString()
	wider, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil || len(wider.Semantic.Selected) != 1 {
		t.Fatalf("wider scope: %+v %v", wider, err)
	}
	grants := map[string]bool{}
	for _, g := range wider.Semantic.Selected[0].Authority {
		grants[g.ID] = true
	}
	if !grants[qualification.ID] || !grants[scopeGrant.ID] {
		t.Fatalf("missing independent grant snapshot: %+v", grants)
	}
	replay, err := op.Recompile(ctx, RecompileRequest{ReceiptID: before.ReceiptID})
	if err != nil || replay.Seal != before.Seal {
		t.Fatalf("original scope replay changed: %v", err)
	}
	// Correcting within the authorized range retains the scope requirement.
	current, err := op.Get(ctx, b.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	corrected, err := op.Correct(ctx, CorrectRequest{RequestID: uuid.NewString(), RecordID: b.RecordID, ExpectedVersion: current.Version, GrantID: root.ID, Draft: current.Draft, EvidenceIDs: []string{e.ID}, Reason: "Correct the current claim without discarding its scope grant"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.RevokeGrant(ctx, RevokeGrantRequest{RequestID: uuid.NewString(), GrantID: scopeGrant.ID, AuthorityID: root.ID, ExpectedVersion: 1, Reason: "Withdraw the separately authorized broader applicability"})
	if err != nil {
		t.Fatal(err)
	}
	inspection, err := op.ScopeAuthorization(ctx, b.RecordID)
	if err != nil || inspection.ScopeGrantLive {
		t.Fatalf("scope inspection hides revocation: %+v %v", inspection, err)
	}
	query.RequestID = uuid.NewString()
	blocked, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil || len(blocked.Semantic.Selected) != 0 {
		t.Fatalf("correction bypassed revoked scope: %+v %v", blocked, err)
	}
	if blocked.Semantic.Omitted["SCOPE_AUTHORITY_INACTIVE"] != 1 {
		t.Fatalf("scope refusal lacks its distinct reason: %+v", blocked.Semantic.Omitted)
	}
	for _, historical := range []Package{wider, blocked} {
		p, err := op.Recompile(ctx, RecompileRequest{ReceiptID: historical.ReceiptID})
		if err != nil || p.Seal != historical.Seal {
			t.Fatalf("scope gate history changed: %v", err)
		}
	}
	// A new explicit authorization can replace the revoked scope grant.
	preview, err = op.PreviewRetraction(ctx, b.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	req.ExpectedVersion = corrected.Version
	req.GrantID = root.ID
	req.PreviewID = preview.PreviewID
	_, err = op.AuthorizeScope(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	query.RequestID = uuid.NewString()
	restored, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil || len(restored.Semantic.Selected) != 1 {
		t.Fatalf("scope reauthorization: %+v %v", restored, err)
	}
	// Qualification revocation is checked independently on a separately widened claim.
	draft.Body = "A second independently qualified compiler claim."
	a, err = writer.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	b, err = qualifier.Promote(ctx, PromoteRequest{uuid.NewString(), a.RecordID, a.Version, qualification.ID, []string{e.ID}, "Qualify the independent revocation fixture"})
	if err != nil {
		t.Fatal(err)
	}
	preview, err = op.PreviewRetraction(ctx, b.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	req.RecordID = b.RecordID
	req.ExpectedVersion = b.Version
	req.PreviewID = preview.PreviewID
	_, err = op.AuthorizeScope(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.RevokeGrant(ctx, RevokeGrantRequest{RequestID: uuid.NewString(), GrantID: qualification.ID, AuthorityID: root.ID, ExpectedVersion: 1, Reason: "Withdraw the claim qualification while scope authority remains live"})
	if err != nil {
		t.Fatal(err)
	}
	query.RequestID = uuid.NewString()
	after, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range after.Semantic.Selected {
		if entry.Record.RecordID == b.RecordID {
			t.Fatal("scope issuer renewed revoked qualification")
		}
	}
	current, err = op.Get(ctx, b.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	preview, err = op.PreviewRetraction(ctx, b.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	req.ExpectedVersion = current.Version
	req.PreviewID = preview.PreviewID
	_, err = op.AuthorizeScope(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
	checkpoint, err := op.Checkpoint(ctx, CheckpointRequest{RequestID: uuid.NewString(), ExportID: "scope-fixture"})
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, m := range checkpoint.Members {
		found = found || m.EventID == change.EventID
	}
	if !found {
		t.Fatal("B scope authorization missing from C checkpoint subset")
	}
}

func TestScopeAuthorizationConcurrentDecisionsHaveOneWinner(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Scope.TaskID = "original"
	d.Pins = &Applicability{TaskClass: "build"}
	record, err := op.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := op.PreviewRetraction(ctx, record.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	start := make(chan struct{})
	results := make(chan error, 2)
	var wg sync.WaitGroup
	for _, scope := range []Scope{{repo, "original", "*"}, {repo, "*", "*"}} {
		wg.Add(1)
		go func(target Scope) {
			defer wg.Done()
			<-start
			_, err := op.AuthorizeScope(ctx, AuthorizeScopeRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, Scope: target, Pins: &Applicability{}, GrantID: root.ID, PreviewID: preview.PreviewID, Reason: "Competing scope decisions must publish only one version"})
			results <- err
		}(scope)
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
		t.Fatalf("scope authorization winners=%d", wins)
	}
	latest, err := op.ScopeAuthorization(ctx, record.RecordID)
	if err != nil || latest.Version != 2 {
		t.Fatalf("latest scope: %+v %v", latest, err)
	}
	current, err := op.Get(ctx, record.RecordID)
	if err != nil || current.Version != latest.Version || current.Scope != latest.Scope {
		t.Fatalf("scope and record split: %+v %v", current, err)
	}
}

func TestScopeAuthorizationOrdinaryEditsAndHostedPolicyRevocation(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	for _, class := range []string{"A", "C"} {
		t.Run(class, func(t *testing.T) {
			repo := uuid.NewString()
			d := projectNote(repo)
			d.Scope.TaskID = "original"
			var record Record
			var err error
			if class == "A" {
				record, err = op.Create(ctx, CreateRequest{uuid.NewString(), d})
			} else {
				d.Kind = "instruction"
				record, err = op.Issue(ctx, IssueRequest{uuid.NewString(), d, root.ID, true, false, "scope-policy", "Issue a narrow synthetic instruction"})
			}
			if err != nil {
				t.Fatal(err)
			}
			scopeGrant, err := op.Grant(ctx, GrantRequest{RequestID: uuid.NewString(), ParentID: root.ID, Principal: op.channel.Principal, Repo: repo, Capabilities: []string{"issue"}, Reason: "Grant revocable applicability authority"})
			if err != nil {
				t.Fatal(err)
			}
			preview, err := op.PreviewRetraction(ctx, record.RecordID)
			if err != nil {
				t.Fatal(err)
			}
			change, err := op.AuthorizeScope(ctx, AuthorizeScopeRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, Scope: Scope{repo, "*", "*"}, Pins: &Applicability{}, GrantID: scopeGrant.ID, PreviewID: preview.PreviewID, Reason: "Extend a synthetic record to other repository tasks"})
			if err != nil {
				t.Fatal(err)
			}
			query := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "other", "run"}, Purpose: "context", AvailableTokens: 64000}
			fresh, err := op.Compile(ctx, query, Destination{"local", true})
			if err != nil || len(fresh.Semantic.Selected) != 1 || fresh.Semantic.Selected[0].Record.Class != class {
				t.Fatalf("class changed or missing: %+v %v", fresh, err)
			}
			if class == "A" {
				current, err := op.Get(ctx, record.RecordID)
				if err != nil {
					t.Fatal(err)
				}
				current.Draft.Body = "Ordinary edit in the authorized wider scope."
				_, err = op.Edit(ctx, EditRequest{uuid.NewString(), record.RecordID, change.Version, current.Draft})
				if err != nil {
					t.Fatal(err)
				}
				query.Purpose = "planning"
				query.RequestID = uuid.NewString()
				p, err := op.Compile(ctx, query, Destination{"local", true})
				if err != nil || len(p.Semantic.Selected) != 0 {
					t.Fatalf("scope authorization promoted A: %+v %v", p, err)
				}
				query.Purpose = "context"
			} else {
				query.RequestID = uuid.NewString()
				_, err = op.Compile(ctx, query, Destination{"hosted", false})
				requireCode(t, err, "POLICY_UNENFORCEABLE")
			}
			_, err = op.RevokeGrant(ctx, RevokeGrantRequest{RequestID: uuid.NewString(), GrantID: scopeGrant.ID, AuthorityID: root.ID, ExpectedVersion: 1, Reason: "Revoke applicability while retaining the original record history"})
			if err != nil {
				t.Fatal(err)
			}
			for _, dest := range []Destination{{"local", true}, {"hosted", false}} {
				query.RequestID = uuid.NewString()
				p, err := op.Compile(ctx, query, dest)
				if err != nil || len(p.Semantic.Selected) != 0 {
					t.Fatalf("revoked scope still applies for %s: %+v %v", dest.Name, p, err)
				}
			}
		})
	}
}

func TestScopeAuthorizationDetectsNewInstructionOverlap(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	var source Record
	for _, task := range []string{"first", "second"} {
		d := projectNote(repo)
		d.Kind = "instruction"
		d.Scope.TaskID = task
		d.Body = "Distinct instruction for " + task
		record, err := op.Issue(ctx, IssueRequest{uuid.NewString(), d, root.ID, true, false, "scope-overlap", "Issue initially disjoint fixture instructions"})
		if err != nil {
			t.Fatal(err)
		}
		if task == "first" {
			source = record
		}
	}
	p, err := op.PreviewRetraction(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.AuthorizeScope(ctx, AuthorizeScopeRequest{RequestID: uuid.NewString(), RecordID: source.RecordID, ExpectedVersion: source.Version, Scope: Scope{repo, "*", "*"}, Pins: &Applicability{}, GrantID: root.ID, PreviewID: p.PreviewID, Reason: "Expand applicability and retain the newly exposed conflict"})
	if err != nil {
		t.Fatal(err)
	}
	conflicts, err := op.Conflicts(ctx, ConflictsRequest{Repo: repo, Limit: 100})
	if err != nil || len(conflicts.Conflicts) != 1 {
		t.Fatalf("scope overlap not retained: %+v %v", conflicts, err)
	}
	_, err = op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "second", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	requireCode(t, err, "OPEN_CONFLICT")
}

func TestScopeAuthorizationRefusalsAreAtomic(t *testing.T) {
	for _, scenario := range []struct{ name, code string }{
		{"unknown pins", "INVALID_REQUEST"}, {"stale version", "VERSION_CONFLICT"}, {"wrong actor", "AUTHORITY_DENIED"},
		{"task move", "AUTHORITY_DENIED"}, {"repository move", "AUTHORITY_DENIED"}, {"narrower pins", "AUTHORITY_DENIED"},
		{"no expansion", "INVALID_REQUEST"}, {"missing preview", "IMPACT_PREVIEW_REQUIRED"}, {"new exposure", "STALE_PREVIEW"},
		{"degraded evidence", "EVIDENCE_UNAVAILABLE"}, {"narrow relation", "AUTHORITY_DENIED"}, {"open conflict", "OPEN_CONFLICT"},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			ctx := context.Background()
			op, root := testOperator(t)
			repo := uuid.NewString()
			writer := testStore(t, Channel{Principal: "scope-refusal:" + repo, Repo: repo})
			d := projectNote(repo)
			d.Scope.TaskID = "original"
			if scenario.name == "narrow relation" {
				base, err := writer.Create(ctx, CreateRequest{uuid.NewString(), d})
				if err != nil {
					t.Fatal(err)
				}
				d.Relations = []RecordRelation{{base.RecordID, base.Version, "derived_from"}}
			}
			a, err := writer.Create(ctx, CreateRequest{uuid.NewString(), d})
			if err != nil {
				t.Fatal(err)
			}
			good, bad := testEvidence(t, op, repo), testEvidence(t, op, repo)
			record, err := op.Promote(ctx, PromoteRequest{uuid.NewString(), a.RecordID, a.Version, root.ID, []string{good.ID, bad.ID}, "Qualify the narrow source before testing scope refusals"})
			if err != nil {
				t.Fatal(err)
			}
			preview, err := op.PreviewRetraction(ctx, record.RecordID)
			if err != nil {
				t.Fatal(err)
			}
			req := AuthorizeScopeRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, Scope: Scope{repo, "*", "*"}, Pins: &Applicability{}, GrantID: root.ID, PreviewID: preview.PreviewID, Reason: "Check a refused expansion leaves the original claim unchanged"}
			caller := op
			switch scenario.name {
			case "unknown pins":
				req.Pins = nil
			case "stale version":
				req.ExpectedVersion--
			case "wrong actor":
				caller = writer
			case "task move":
				req.Scope.TaskID = "other"
			case "repository move":
				req.Scope.Repo = uuid.NewString()
			case "narrower pins":
				req.Pins.TaskClass = "build"
			case "no expansion":
				req.Scope = record.Scope
			case "missing preview":
				req.PreviewID = ""
			case "new exposure":
				_, err = op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "original", "run"}, Purpose: "planning", AvailableTokens: 64000}, Destination{"local", true})
			case "degraded evidence":
				_, err = op.pool.Exec(ctx, `UPDATE cairn.evidence SET state='dangling' WHERE evidence_id=$1`, bad.ID)
				if err == nil {
					p, e := op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "original", "run"}, Purpose: "planning", AvailableTokens: 64000}, Destination{"local", true})
					if e != nil || len(p.Semantic.Selected) != 1 {
						t.Fatalf("existential read gate changed: %+v %v", p, e)
					}
				}
			case "open conflict":
				peer, e := writer.Create(ctx, CreateRequest{uuid.NewString(), d})
				if e != nil {
					t.Fatal(e)
				}
				_, err = op.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{record.RecordID, peer.RecordID}, "Retain the current disputed claim before scope changes"})
			}
			if err != nil {
				t.Fatal(err)
			}
			_, err = caller.AuthorizeScope(ctx, req)
			requireCode(t, err, scenario.code)
			current, err := op.Get(ctx, record.RecordID)
			if err != nil || current.Version != record.Version || current.Scope != record.Scope {
				t.Fatalf("refusal changed scope: %+v %v", current, err)
			}
			_, err = op.ScopeAuthorization(ctx, record.RecordID)
			requireCode(t, err, "NOT_FOUND")
		})
	}
}
