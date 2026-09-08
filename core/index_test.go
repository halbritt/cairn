package core

import (
	"context"
	"github.com/google/uuid"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestIndexPullIsBoundedScopedAndChecksCurrentVersion(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body = "compiler diagnostics. " + strings.Repeat("explicit selected body ", 100)
	r, err := op.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "compiler", Purpose: "context", AvailableTokens: 64000}
	index, err := op.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(index.Package.Semantic.Selected) != 0 || len(index.Package.Semantic.Index) != 1 || len(index.Handles) != 1 {
		t.Fatalf("unexpected index: %+v", index)
	}
	if strings.Contains(index.Package.Semantic.Index[0].Summary, strings.Repeat("explicit selected body ", 20)) {
		t.Fatal("full body entered index")
	}
	replay, err := op.Recompile(ctx, RecompileRequest{index.Package.ReceiptID, req.Query})
	if err != nil || replay.Seal != index.Package.Seal {
		t.Fatalf("index historical recompile: %v", err)
	}
	explanation, err := op.Explain(ctx, index.Package.ReceiptID)
	if err != nil || len(explanation.Candidates) != 1 || explanation.Candidates[0].LexicalMatches != 1 {
		t.Fatalf("original index ranking missing: %+v %v", explanation, err)
	}
	pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: index.Handles[0].Handle}
	expanded, err := op.Expand(ctx, pull, Destination{"local", true})
	if err != nil || expanded.Selection.Record.Body != d.Body {
		t.Fatalf("pull: %+v %v", expanded, err)
	}
	if expanded.Selection.Reason != "indexed record; current eligibility revalidated" {
		t.Fatalf("pull reported queryless ranking instead of its recheck: %q", expanded.Selection.Reason)
	}
	after, err := op.Explain(ctx, index.Package.ReceiptID)
	if err != nil || len(after.Candidates) != 1 || after.Candidates[0].LexicalMatches != explanation.Candidates[0].LexicalMatches || after.Candidates[0].Rank != explanation.Candidates[0].Rank {
		t.Fatalf("pull changed original ranking: %+v %v", after, err)
	}
	again, err := op.Expand(ctx, pull, Destination{"local", true})
	if err != nil || again.CreditsRemaining != expanded.CreditsRemaining {
		t.Fatal("retry spent credit")
	}
	report, err := op.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 100})
	if err != nil || (report.Rows[0].Usage != "expanded" || report.Rows[0].ExposureKind != "index") {
		t.Fatalf("expansion not observed: %+v %v", report, err)
	}
	_, err = op.Expand(ctx, ExpandRequest{uuid.NewString(), pull.ReceiptID, pull.Handle}, Destination{"hosted", false})
	requireCode(t, err, "AUTHORITY_DENIED")
	d.Body = "compiler corrected advice"
	if _, err = op.Edit(ctx, EditRequest{uuid.NewString(), r.RecordID, 1, d}); err != nil {
		t.Fatal(err)
	}
	_, err = op.Expand(ctx, ExpandRequest{uuid.NewString(), pull.ReceiptID, pull.Handle}, Destination{"local", true})
	requireCode(t, err, "STALE_HANDLE")
	outsider := testStore(t, Channel{Principal: "outsider:" + repo, Repo: repo})
	_, err = outsider.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "AUTHORITY_DENIED")
	// Expiry also applies to transport retries: a prior body response is not a
	// fresh authorization to disclose retained bytes forever.
	if _, err = op.pool.Exec(ctx, `UPDATE cairn.index_session SET expires_at=$2 WHERE receipt_id=$1`, pull.ReceiptID, time.Now().Add(-time.Hour)); err != nil {
		t.Fatal(err)
	}
	_, err = op.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "STALE_HANDLE")
}

func TestExpansionCreditsSerializeAndInvalidateRetractionPreview(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "index-budget-writer"})
	record, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	evidence := testEvidence(t, op, repo)
	record, err = op.Promote(ctx, PromoteRequest{uuid.NewString(), record.RecordID, 1, root.ID, []string{evidence.ID}, "Support bounded expansion fixture"})
	if err != nil {
		t.Fatal(err)
	}
	idx, err := op.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := op.PreviewRetraction(ctx, record.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	results := make(chan error, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := op.Expand(ctx, ExpandRequest{uuid.NewString(), idx.Package.ReceiptID, idx.Handles[0].Handle}, Destination{"local", true})
			results <- err
		}()
	}
	wg.Wait()
	close(results)
	success := 0
	for err := range results {
		if err == nil {
			success++
		} else if Code(err) != "BUDGET_REFUSED" && Code(err) != "VERSION_CONFLICT" {
			t.Fatal(err)
		}
	}
	if success != 4 {
		t.Fatalf("expected exactly four credit spends, got %d", success)
	}
	_, err = op.Retract(ctx, RetractRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, GrantID: root.ID, Reason: "Reject preview made before body expansion", PreviewID: preview.PreviewID})
	requireCode(t, err, "STALE_PREVIEW")
}
func TestExpansionRechecksMandatoryBootstrapAndAuthority(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	grant, err := op.Grant(ctx, GrantRequest{RequestID: uuid.NewString(), ParentID: root.ID, Principal: op.channel.Principal, Repo: repo, Capabilities: []string{"issue"}, Reason: "Bound test instruction authority"})
	if err != nil {
		t.Fatal(err)
	}
	d := projectNote(repo)
	d.Kind = "instruction"
	if _, err = op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: grant.ID, Mandatory: false, RequiresRuntime: false, PolicyKey: "optional", Reason: "Issue optional fixture guidance"}); err != nil {
		t.Fatal(err)
	}
	idx, err := op.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	pull := ExpandRequest{uuid.NewString(), idx.Package.ReceiptID, idx.Handles[0].Handle}
	if _, err = op.Expand(ctx, pull, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	d.Body = "New mandatory instruction"
	d.Scope.TaskID = "task"
	if _, err = op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, RequiresRuntime: false, PolicyKey: "new-mandatory", Reason: "Issue new mandatory bootstrap"}); err != nil {
		t.Fatal(err)
	}
	_, err = op.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "STALE_HANDLE")
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}
	idx, err = op.Index(ctx, req, Destination{"local", true})
	if err != nil || len(idx.Package.Semantic.Selected) != 1 {
		t.Fatalf("mandatory bootstrap missing: %v", err)
	}
	if _, err = op.RevokeGrant(ctx, RevokeGrantRequest{uuid.NewString(), grant.ID, root.ID, 1, "Revoke indexed instruction authority"}); err != nil {
		t.Fatal(err)
	}
	_, err = op.Expand(ctx, ExpandRequest{uuid.NewString(), idx.Package.ReceiptID, idx.Handles[0].Handle}, Destination{"local", true})
	requireCode(t, err, "STALE_HANDLE")
}

func TestExpansionExpiryAndSizeRefusalDoNotDiscloseOrSpend(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body = "oversized fixture " + strings.Repeat("payload ", 4000)
	if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	idx, err := op.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	pull := ExpandRequest{uuid.NewString(), idx.Package.ReceiptID, idx.Handles[0].Handle}
	_, err = op.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "BUDGET_REFUSED")
	var credits int
	if err = op.pool.QueryRow(ctx, `SELECT credits FROM cairn.index_session WHERE receipt_id=$1`, pull.ReceiptID).Scan(&credits); err != nil || credits != 4 {
		t.Fatal("refused expansion spent credit")
	}
	if _, err = op.InvalidateHandles(ctx, InvalidateHandlesRequest{uuid.NewString(), "Fence restored expansion sessions"}); err != nil {
		t.Fatal(err)
	}
	_, err = op.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "STALE_HANDLE")
}
