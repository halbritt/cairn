package core

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

func TestPolicyRevisionChangesFreshCompileAndRollbackPreservesHistory(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	_, err := op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}
	before, err := op.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(before.Semantic.Selected) != 1 || before.Semantic.Policy != "local-loop/1" {
		t.Fatalf("default policy: %+v %v", before, err)
	}
	first, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: &PolicyRules{OptionalPercent: 10, OptionalMaxTokens: 6000}, Reason: "Adopt the existing optional memory budget as governed policy"})
	if err != nil {
		t.Fatal(err)
	}
	query := req
	query.RequestID = uuid.NewString()
	firstRun, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil || firstRun.Semantic.PolicyRevision == nil || firstRun.Semantic.PolicyRevision.RevisionID != first.RevisionID {
		t.Fatalf("policy revision not pinned: %+v %v", firstRun, err)
	}
	second, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, ExpectedRevisionID: first.RevisionID, GrantID: root.ID, Rules: &PolicyRules{}, Reason: "Disable optional memory for a governed baseline"})
	if err != nil {
		t.Fatal(err)
	}
	query.RequestID = uuid.NewString()
	disabled, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil || len(disabled.Semantic.Selected) != 0 || disabled.Semantic.OptionalLimit != 0 {
		t.Fatalf("revised policy not applied: %+v %v", disabled, err)
	}
	_, err = op.Compile(ctx, req, Destination{"local", true})
	requireCode(t, err, "STALE_PACKAGE")
	rollback, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, ExpectedRevisionID: second.RevisionID, RestoreRevisionID: first.RevisionID, GrantID: root.ID, Reason: "Restore the reviewed earlier budget as a new authorized revision"})
	if err != nil || rollback.RevisionID == first.RevisionID || rollback.RestoresRevisionID != first.RevisionID || rollback.Version != 3 {
		t.Fatalf("rollback rewrote history: %+v %v", rollback, err)
	}
	query.RequestID = uuid.NewString()
	restored, err := op.Compile(ctx, query, Destination{"local", true})
	if err != nil || len(restored.Semantic.Selected) != 1 || restored.Semantic.PolicyRevision.RevisionID != rollback.RevisionID {
		t.Fatalf("rollback did not affect fresh compilation: %+v %v", restored, err)
	}
	for _, original := range []Package{before, firstRun, disabled, restored} {
		replay, err := op.Recompile(ctx, RecompileRequest{ReceiptID: original.ReceiptID})
		if err != nil || replay.Seal != original.Seal {
			t.Fatalf("policy history changed: %v", err)
		}
	}
}

func TestPolicyRevisionFencesContextRetriesOnlyInChangedRepository(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	host := testStore(t, Channel{Principal: "policy-context:" + uuid.NewString(), Instrumented: true})
	changedRepo, otherRepo := uuid.NewString(), uuid.NewString()
	compile := func(repo string) Package {
		t.Helper()
		p, err := host.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		return p
	}
	changed, other := compile(changedRepo), compile(otherRepo)
	if err := host.ClaimRun(ctx, changed.ReceiptID); err != nil {
		t.Fatal(err)
	}
	contextRequest := ManagedContextRequest{RequestID: uuid.NewString(), OwnershipID: uuid.NewString(), ReceiptID: changed.ReceiptID, Directory: filepath.Join(t.TempDir(), changed.ReceiptID), DirectoryDevice: "1", DirectoryInode: "2"}
	if _, err := host.RegisterManagedContext(ctx, contextRequest); err != nil {
		t.Fatal(err)
	}
	_, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: changedRepo, GrantID: root.ID, Rules: &PolicyRules{}, Reason: "Change policy only for this repository's next deliveries"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = host.RegisterManagedContext(ctx, contextRequest)
	requireCode(t, err, "STALE_PACKAGE")
	if err = host.ClaimRun(ctx, other.ReceiptID); err != nil {
		t.Fatalf("policy change invalidated an unrelated repository: %v", err)
	}
	_, err = host.Replay(ctx, changed.ReceiptID)
	if err != nil {
		t.Fatalf("policy change erased history: %v", err)
	}
}

func TestPolicyRefusalsLeaveEffectiveRevisionUnchanged(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	initial, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, Rules: &PolicyRules{OptionalPercent: 10, OptionalMaxTokens: 6000}, GrantID: root.ID, Reason: "Establish a policy for refusal boundary tests"})
	if err != nil {
		t.Fatal(err)
	}
	foreign, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: uuid.NewString(), Rules: &PolicyRules{}, GrantID: root.ID, Reason: "A separate repository policy cannot be imported by rollback"})
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name string
		code string
		edit func(*RevisePolicyRequest)
	}{
		{"stale", "VERSION_CONFLICT", func(r *RevisePolicyRequest) { r.ExpectedRevisionID = "" }},
		{"missing_rules", "INVALID_REQUEST", func(r *RevisePolicyRequest) { r.Rules = nil }},
		{"both_paths", "INVALID_REQUEST", func(r *RevisePolicyRequest) { r.RestoreRevisionID = initial.RevisionID }},
		{"cross_repo_rollback", "AUTHORITY_DENIED", func(r *RevisePolicyRequest) { r.Rules = nil; r.RestoreRevisionID = foreign.RevisionID }},
		{"unknown_rollback", "NOT_FOUND", func(r *RevisePolicyRequest) { r.Rules = nil; r.RestoreRevisionID = uuid.NewString() }},
		{"percent_ceiling", "INVALID_REQUEST", func(r *RevisePolicyRequest) { r.Rules.OptionalPercent = 11 }},
		{"token_ceiling", "INVALID_REQUEST", func(r *RevisePolicyRequest) { r.Rules.OptionalMaxTokens = 6001 }},
		{"negative", "INVALID_REQUEST", func(r *RevisePolicyRequest) { r.Rules.OptionalPercent = -1 }},
		{"missing_reason", "INVALID_REQUEST", func(r *RevisePolicyRequest) { r.Reason = "" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req := RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, ExpectedRevisionID: initial.RevisionID, Rules: &PolicyRules{OptionalPercent: 10, OptionalMaxTokens: 6000}, GrantID: root.ID, Reason: "Refused changes must not replace effective policy"}
			tc.edit(&req)
			_, err := op.RevisePolicy(ctx, req)
			requireCode(t, err, tc.code)
			current, err := op.Policy(ctx, repo)
			if err != nil || current.Revision.RevisionID != initial.RevisionID {
				t.Fatalf("refusal changed policy: %+v %v", current, err)
			}
		})
	}
	other := testStore(t, Channel{Principal: "policy-other:" + repo, Repo: repo})
	_, err = other.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, ExpectedRevisionID: initial.RevisionID, Rules: &PolicyRules{}, GrantID: root.ID, Reason: "Payload cannot borrow another actor's policy grant"})
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = other.PolicyRevision(ctx, foreign.RevisionID)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = other.Policy(ctx, foreign.Repo)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = other.RunReport(ctx, RunReportRequest{Repo: foreign.Repo, PolicyRevision: foreign.RevisionID, Limit: 100})
	requireCode(t, err, "AUTHORITY_DENIED")
}

func TestPolicyBudgetCannotDropMandatoryOrWaiveRuntimeRequirement(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	for _, runtime := range []bool{false, true} {
		repo := uuid.NewString()
		_, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, Rules: &PolicyRules{}, GrantID: root.ID, Reason: "Zero optional budget must preserve mandatory instruction enforcement"})
		if err != nil {
			t.Fatal(err)
		}
		draft := projectNote(repo)
		draft.Kind = "instruction"
		draft.Sensitivity = "shareable"
		instruction, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, RequiresRuntime: runtime, PolicyKey: "required", Reason: "Require the reviewed mandatory instruction"})
		if err != nil {
			t.Fatal(err)
		}
		for _, mode := range []string{"", "index"} {
			p, err := op.Compile(ctx, CompileRequest{Mode: mode, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"hosted", false})
			if runtime {
				requireCode(t, err, "POLICY_UNENFORCEABLE")
			} else if err != nil || len(p.Semantic.Selected) != 1 || p.Semantic.Selected[0].Record.RecordID != instruction.RecordID {
				t.Fatalf("zero optional budget omitted mandatory instruction: %+v %v", p, err)
			}
		}
	}
}

func TestPolicyRevisionAfterSnapshotForcesSerializationRetry(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	tx, err := op.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	// Establish the snapshot before the first policy exists, without holding
	// its generation lock. The later lock must not authorize this old view.
	var before int64
	if err = tx.QueryRow(ctx, `SELECT generation FROM cairn.policy_generation WHERE singleton`).Scan(&before); err != nil {
		t.Fatal(err)
	}
	_, err = op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, Rules: &PolicyRules{}, GrantID: root.ID, Reason: "Install a first policy after a compiler snapshot already exists"})
	if err != nil {
		t.Fatal(err)
	}
	_, _, err = op.collectCandidates(ctx, tx, CompileRequest{Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true}, map[string]*CandidateEvaluation{})
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "40001" {
		t.Fatalf("old snapshot bypassed policy revision: %v", err)
	}
}

func TestPolicyRevocationBlocksFreshDeliveryAndAllowsExplicitReauthorization(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	issuer := testStore(t, Channel{Principal: "policy-issuer:" + repo, Repo: repo})
	host := testStore(t, Channel{Principal: "policy-host:" + repo, Repo: repo, Instrumented: true})
	grant, err := op.Grant(ctx, GrantRequest{RequestID: uuid.NewString(), ParentID: root.ID, Principal: issuer.channel.Principal, Repo: repo, Capabilities: []string{"issue"}, Reason: "Delegate repository policy revision authority"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	change := RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, Rules: &PolicyRules{OptionalPercent: 10, OptionalMaxTokens: 6000}, GrantID: grant.ID, Reason: "Authorize optional repository memory budget"}
	policy, err := issuer.RevisePolicy(ctx, change)
	if err != nil {
		t.Fatal(err)
	}
	query := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}
	index, err := host.Index(ctx, query, Destination{"local", true})
	if err != nil || len(index.Handles) != 1 {
		t.Fatalf("index under policy: %+v %v", index, err)
	}
	pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: index.Handles[0].Handle}
	if _, err = host.Expand(ctx, pull, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	binding := RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, TaskClass: "repair", BindingID: "fixture", CapabilityID: "fixture", CommandSHA256: strings.Repeat("a", 64)}
	if _, err = host.BindRun(ctx, binding); err != nil {
		t.Fatal(err)
	}
	_, err = op.RevokeGrant(ctx, RevokeGrantRequest{RequestID: uuid.NewString(), GrantID: grant.ID, AuthorityID: root.ID, ExpectedVersion: 1, Reason: "Withdraw current policy authority"})
	if err != nil {
		t.Fatal(err)
	}
	query.RequestID = uuid.NewString()
	_, err = host.Compile(ctx, query, Destination{"local", true})
	requireCode(t, err, "POLICY_UNENFORCEABLE")
	_, err = host.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "POLICY_UNENFORCEABLE")
	_, err = host.BindRun(ctx, binding)
	requireCode(t, err, "POLICY_UNENFORCEABLE")
	requireCode(t, host.ClaimRun(ctx, index.Package.ReceiptID), "POLICY_UNENFORCEABLE")
	state, err := op.Policy(ctx, repo)
	if err != nil || state.Revision == nil || state.Revision.AuthorityLive {
		t.Fatalf("inspection hid revoked authority: %+v %v", state, err)
	}
	historical, err := host.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID})
	if err != nil || historical.Seal != index.Package.Seal {
		t.Fatalf("revocation reinterpreted history: %v", err)
	}
	historical, err = host.RecompileForDestination(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID}, Destination{"local", true})
	if err != nil || historical.Seal != index.Package.Seal {
		t.Fatalf("destination-aware inspection revived or reinterpreted authority: %v", err)
	}
	retry, err := issuer.RevisePolicy(ctx, change)
	if err != nil || retry.RevisionID != policy.RevisionID {
		t.Fatalf("transport retry repeated policy mutation: %+v %v", retry, err)
	}
	reauthorized, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, ExpectedRevisionID: policy.RevisionID, RestoreRevisionID: policy.RevisionID, GrantID: root.ID, Reason: "Reauthorize the same reviewed rules under live authority"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = host.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "STALE_PACKAGE")
	_, err = host.BindRun(ctx, binding)
	requireCode(t, err, "STALE_PACKAGE")
	requireCode(t, host.ClaimRun(ctx, index.Package.ReceiptID), "STALE_PACKAGE")
	query.RequestID = uuid.NewString()
	fresh, err := host.Compile(ctx, query, Destination{"local", true})
	if err != nil || fresh.Semantic.PolicyRevision.RevisionID != reauthorized.RevisionID {
		t.Fatalf("new authority did not restore fresh compile: %v", err)
	}
	if err = host.ClaimRun(ctx, fresh.ReceiptID); err != nil {
		t.Fatal(err)
	}
}

func TestPolicyConcurrentRevisionsHaveOneWinner(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	start := make(chan struct{})
	results := make(chan error, 2)
	var wg sync.WaitGroup
	for range 2 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			_, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: &PolicyRules{OptionalPercent: 10, OptionalMaxTokens: 6000}, Reason: "Competing first policy revisions must have one winner"})
			results <- err
		}()
	}
	close(start)
	wg.Wait()
	close(results)
	winners := 0
	for err := range results {
		if err == nil {
			winners++
		} else {
			requireCode(t, err, "VERSION_CONFLICT")
		}
	}
	p, err := op.Policy(ctx, repo)
	if err != nil || winners != 1 || p.Revision == nil || p.Revision.Version != 1 {
		t.Fatalf("policy CAS: winners=%d %+v %v", winners, p, err)
	}
	cp, err := op.Checkpoint(ctx, CheckpointRequest{RequestID: uuid.NewString(), ExportID: "policy-concurrency"})
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, member := range cp.Members {
		found = found || member.EventID == p.Revision.EventID
	}
	if !found {
		t.Fatal("policy C event missing from audit checkpoint")
	}
}

func TestPolicyRunQueryIncludesZeroMemoryRunsAndRetainsOldRevision(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	host := testStore(t, Channel{Principal: "policy-host:" + repo, Repo: repo, Instrumented: true})
	compile := func() Package {
		t.Helper()
		p, err := host.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", uuid.NewString()}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		return p
	}
	legacy := compile()
	if err := host.ClaimRun(ctx, legacy.ReceiptID); err != nil {
		t.Fatal(err)
	}
	policy, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: &PolicyRules{}, Reason: "Disable optional memory for a governed run baseline"})
	if err != nil {
		t.Fatal(err)
	}
	first, second := compile(), compile()
	compile() // Pure retrieval must not enter the run population.
	for _, p := range []Package{first, second} {
		if err := host.ClaimRun(ctx, p.ReceiptID); err != nil {
			t.Fatal(err)
		}
	}
	_, err = op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, ExpectedRevisionID: policy.RevisionID, GrantID: root.ID, Rules: &PolicyRules{OptionalPercent: 10, OptionalMaxTokens: 6000}, Reason: "Restore optional retrieval after the baseline"})
	if err != nil {
		t.Fatal(err)
	}
	zero := 0
	_, err = host.RecordOutcome(ctx, OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: first.ReceiptID, ExitCode: &zero, ProcessState: "exited", StdoutSHA256: strings.Repeat("a", 64), StderrSHA256: strings.Repeat("b", 64)})
	if err != nil {
		t.Fatalf("policy revision blocked delayed outcome: %v", err)
	}
	for i, p := range []Package{first, second} {
		report, err := host.RunReport(ctx, RunReportRequest{Repo: repo, PolicyRevision: policy.RevisionID, Limit: 1, Offset: i})
		if err != nil || len(report.Rows) != 1 || report.Rows[0].ReceiptID != p.ReceiptID || report.Rows[0].PolicyRevision != policy.RevisionID || report.Rows[0].ExposureRows != 0 || report.More != (i == 0) {
			t.Fatalf("policy run query: %+v %v", report, err)
		}
	}
	baseline, err := host.RunReport(ctx, RunReportRequest{Repo: repo, PolicyRevision: "local-loop/1", Limit: 100})
	if err != nil || len(baseline.Rows) != 1 || baseline.Rows[0].ReceiptID != legacy.ReceiptID {
		t.Fatalf("legacy policy run query: %+v %v", baseline, err)
	}
}
