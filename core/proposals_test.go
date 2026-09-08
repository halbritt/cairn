package core

import (
	"context"
	"github.com/google/uuid"
	"strings"
	"testing"
	"time"
)

func TestStandaloneFailureProposalLifecycle(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	s = testStore(t, Channel{Principal: s.channel.Principal, Operator: true, Instrumented: true})
	repo := uuid.NewString()
	makeAssessment := func(outcome, domain, signature string) string {
		t.Helper()
		p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "same-task", uuid.NewString()}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		if len(p.Semantic.Selected) != 0 {
			t.Fatal("fixture must have no memory exposure")
		}
		_, err = s.BindRun(ctx, RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, TaskClass: "repair", BindingID: "host:local", CapabilityID: "repair:v1", CommandSHA256: strings.Repeat("a", 64)})
		if err != nil {
			t.Fatal(err)
		}
		kind := ""
		if outcome == "rejected" {
			kind = "regression"
		}
		if domain == "binding" {
			kind = "adapter"
		}
		e := testEvidence(t, s, repo)
		_, err = s.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, TaskOutcome: outcome, FailureDomain: domain, FailureKind: kind, ErrorSignature: signature, Method: "selected-test/1", EvidenceIDs: []string{e.ID}, Reason: "Review selected regression check"})
		if err != nil {
			t.Fatal(err)
		}
		return p.ReceiptID
	}
	generate := func() []Proposal {
		t.Helper()
		batch, err := s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo})
		if err != nil {
			t.Fatal(err)
		}
		return batch.Proposals
	}
	due := func() map[string]string {
		t.Helper()
		d, err := s.Docket(ctx, repo)
		if err != nil {
			t.Fatal(err)
		}
		items := map[string]string{}
		for _, item := range d.Items {
			if item.ProposalID != "" {
				items[item.ProposalID] = item.Reason
			}
		}
		return items
	}
	review := func(p Proposal, disposition string, until *time.Time) Proposal {
		t.Helper()
		out, err := s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: p.Version, Disposition: disposition, Until: until, Reason: "Review bounded failure evidence"})
		if err != nil {
			t.Fatal(err)
		}
		return out
	}
	// Missing signatures and binding failures never become task-failure demand.
	makeAssessment("rejected", "task", "")
	makeAssessment("unknown", "binding", strings.Repeat("c", 64))
	if got := generate(); len(got) != 0 {
		t.Fatalf("ineligible failures: %+v", got)
	}
	failureID := makeAssessment("rejected", "task", strings.Repeat("b", 64))
	type generationResult struct {
		batch ProposalBatch
		err   error
	}
	results := make(chan generationResult, 4)
	start := make(chan struct{})
	for range 4 {
		go func() {
			<-start
			batch, err := s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo})
			results <- generationResult{batch, err}
		}()
	}
	close(start)
	var generated []Proposal
	for range 4 {
		result := <-results
		if result.err != nil || len(result.batch.Proposals) != 1 {
			t.Fatalf("concurrent generation: %+v %v", result.batch, result.err)
		}
		if generated != nil && generated[0].ID != result.batch.Proposals[0].ID {
			t.Fatal("concurrent calls duplicated failure demand")
		}
		generated = result.batch.Proposals
	}
	if len(generated) != 1 {
		t.Fatalf("standalone failure missing: %+v", generated)
	}
	p := generated[0]
	if p.Kind != "failure" || p.Method != "standalone-task-failure/1" || p.FailureReceipt != failureID || p.RecoveryReceipt != "" || p.RecoveryVersion != 0 || !p.SourceCurrent || len(p.EvidenceIDs) != 1 {
		t.Fatalf("standalone source attachment: %+v", p)
	}
	if _, err := s.ReadEvidence(ctx, p.EvidenceIDs[0]); err != nil {
		t.Fatal(err)
	}
	if got := generate(); len(got) != 1 || got[0].ID != p.ID {
		t.Fatalf("duplicate failure: %+v", got)
	}
	if got := due(); len(got) != 1 || got[p.ID] != "TASK_FAILURE" {
		t.Fatalf("missing due failure: %+v", got)
	}
	until := time.Now().Add(time.Hour)
	p = review(p, "deferred", &until)
	if got := due(); len(got) != 0 {
		t.Fatalf("deferred failure due: %+v", got)
	}
	p = review(p, "open", nil)
	// A richer source pair takes precedence without erasing standalone history.
	recoveryID := makeAssessment("accepted", "none", "")
	generated = generate()
	if len(generated) != 1 || generated[0].RecoveryReceipt != recoveryID || generated[0].ID == p.ID {
		t.Fatalf("missing recovery pair: %+v", generated)
	}
	pair := generated[0]
	if pair.Kind != "failure_recovery" {
		t.Fatalf("wrong pair kind: %+v", pair)
	}
	if got := due(); len(got) != 1 || got[pair.ID] != "FAILURE_RECOVERY" {
		t.Fatalf("duplicate demand: %+v", got)
	}
	pair = review(pair, "dismissed", nil)
	if got := due(); len(got) != 0 {
		t.Fatalf("dismissed pair revived standalone: %+v", got)
	}
	_, err := s.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: recoveryID, ExpectedVersion: 1, TaskOutcome: "unknown", FailureDomain: "unknown", Method: "correction/1", Reason: "Withdraw premature acceptance"})
	if err != nil {
		t.Fatal(err)
	}
	if got := due(); len(got) != 1 || got[p.ID] != "TASK_FAILURE" {
		t.Fatalf("current standalone not restored: %+v", got)
	}
	p = review(p, "dismissed", nil)
	if got := due(); len(got) != 0 {
		t.Fatalf("dismissed failure due: %+v", got)
	}
	target, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	p, err = s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: p.Version, Disposition: "converted", ResultRecord: target.RecordID, Reason: "Link a manually written ordinary lesson"})
	if err != nil || p.ResultRecord != target.RecordID {
		t.Fatalf("standalone conversion: %+v %v", p, err)
	}
	record, err := s.Get(ctx, target.RecordID)
	if err != nil || record.Class != "A" {
		t.Fatalf("conversion changed class: %+v %v", record, err)
	}
	_, err = s.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: failureID, ExpectedVersion: 1, TaskOutcome: "unknown", FailureDomain: "unknown", Method: "correction/1", Reason: "Withdraw initial rejection"})
	if err != nil {
		t.Fatal(err)
	}
	stale, err := s.Proposal(ctx, p.ID)
	if err != nil || stale.SourceCurrent {
		t.Fatalf("corrected standalone current: %+v %v", stale, err)
	}
	_, err = s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: p.Version, Disposition: "open", Reason: "Try reopening stale failure"})
	requireCode(t, err, "STALE_PROPOSAL")
	if got := generate(); len(got) != 0 {
		t.Fatalf("corrected failure generated: %+v", got)
	}
}

func TestFailureRecoveryProposalRetainsEvidenceAndReviewDisposition(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	observer := testStore(t, Channel{Principal: s.channel.Principal, Operator: true, Instrumented: true})
	repo := uuid.NewString()
	receipts := []string{}
	for i, outcome := range []string{"rejected", "accepted"} {
		p, err := observer.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "same-task", uuid.NewString()}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		receipts = append(receipts, p.ReceiptID)
		_, err = observer.BindRun(ctx, RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, TaskClass: "build", BindingID: "compiler:local", CapabilityID: "compiler:v1", CommandSHA256: strings.Repeat("a", 64)})
		if err != nil {
			t.Fatal(err)
		}
		e := testEvidence(t, observer, repo)
		domain, kind, signature := "task", "compile_error", strings.Repeat("b", 64)
		if i == 1 {
			domain = "none"
			kind = ""
			signature = ""
		}
		_, err = observer.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, TaskOutcome: outcome, FailureDomain: domain, FailureKind: kind, ErrorSignature: signature, Method: "fixture-check/1", EvidenceIDs: []string{e.ID}, Reason: "Observed fixture build outcome"})
		if err != nil {
			t.Fatal(err)
		}
	}
	request := GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo}
	generated, err := s.GenerateProposals(ctx, request)
	if err != nil || len(generated.Proposals) != 1 {
		t.Fatalf("failure recovery proposal: %+v %v", generated, err)
	}
	proposal := generated.Proposals[0]
	if !proposal.SourceCurrent || proposal.FailureReceipt != receipts[0] || proposal.RecoveryReceipt != receipts[1] || len(proposal.EvidenceIDs) != 2 {
		t.Fatalf("missing source attachment: %+v", proposal)
	}
	if _, err = s.ReadEvidence(ctx, proposal.EvidenceIDs[0]); err != nil {
		t.Fatal(err)
	}
	// Pre-migration pair JSON has no kind field. Reading derives it without
	// rewriting the original source detail or review version.
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.lesson_proposal SET detail=detail-'kind' WHERE proposal_id=$1`, proposal.ID); err != nil {
		t.Fatal(err)
	}
	legacy, err := s.Proposal(ctx, proposal.ID)
	if err != nil || legacy.Kind != "failure_recovery" || legacy.Version != proposal.Version {
		t.Fatalf("legacy pair: %+v %v", legacy, err)
	}
	request.RequestID = uuid.NewString()
	again, err := s.GenerateProposals(ctx, request)
	if err != nil || again.Proposals[0].ID != proposal.ID {
		t.Fatal("generator repeated a proposal")
	}
	until := time.Now().Add(time.Hour)
	reviewed, err := s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: proposal.ID, ExpectedVersion: proposal.Version, Disposition: "deferred", Until: &until, Reason: "Defer bounded fixture review"})
	if err != nil || reviewed.Version != 2 {
		t.Fatalf("review: %+v %v", reviewed, err)
	}
	docket, err := s.Docket(ctx, repo)
	if err != nil {
		t.Fatal(err)
	}
	for _, item := range docket.Items {
		if item.ProposalID == proposal.ID {
			t.Fatal("deferred proposal remained due")
		}
	}
	t.Run("forgotten result cannot complete review", func(t *testing.T) {
		target, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
		if err != nil {
			t.Fatal(err)
		}
		preview, err := s.PreviewDeletion(ctx, target.RecordID)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = s.Forget(ctx, ForgetRequest{uuid.NewString(), target.RecordID, target.Version, root.ID, preview.PreviewID}); err != nil {
			t.Fatal(err)
		}
		_, err = s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: proposal.ID, ExpectedVersion: 2, Disposition: "converted", ResultRecord: target.RecordID, Reason: "Do not convert a forgotten lesson"})
		requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	})
	// Source versions alone do not establish that their selected bytes remain
	// available. Conversion checks current evidence before linking advice.
	target, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.evidence SET body='changed proposal evidence' WHERE evidence_id=$1`, proposal.EvidenceIDs[0]); err != nil {
		t.Fatal(err)
	}
	if _, err = s.CheckEvidence(ctx, EvidenceCheckRequest{uuid.NewString(), proposal.EvidenceIDs[0]}); err != nil {
		t.Fatal(err)
	}
	_, err = s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: proposal.ID, ExpectedVersion: 2, Disposition: "converted", ResultRecord: target.RecordID, Reason: "Do not convert unavailable source evidence"})
	requireCode(t, err, "EVIDENCE_UNAVAILABLE")
	// A correction invalidates review based on the old pair, while preserving
	// the old source attachment for inspection.
	_, err = observer.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: receipts[1], ExpectedVersion: 1, TaskOutcome: "unknown", FailureDomain: "unknown", Method: "fixture-correction/1", Reason: "Withdraw premature acceptance after reviewing the fixture"})
	if err != nil {
		t.Fatal(err)
	}
	stale, err := s.Proposal(ctx, proposal.ID)
	if err != nil || stale.SourceCurrent {
		t.Fatalf("corrected source remained current: %+v %v", stale, err)
	}
	_, err = s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: proposal.ID, ExpectedVersion: 2, Disposition: "open", Reason: "Try to reopen an outdated proposal"})
	requireCode(t, err, "STALE_PROPOSAL")
	_, err = s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: proposal.ID, ExpectedVersion: 1, Disposition: "dismissed", Reason: "Try stale review version"})
	requireCode(t, err, "VERSION_CONFLICT")
	_, err = s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: proposal.ID, ExpectedVersion: 2, Disposition: "dismissed", Reason: "Dismiss after corrected acceptance"})
	if err != nil {
		t.Fatal(err)
	}
	outsider := testStore(t, Channel{Principal: "agent:" + repo, Repo: uuid.NewString()})
	_, err = outsider.Proposal(ctx, proposal.ID)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = outsider.ReadEvidence(ctx, proposal.EvidenceIDs[0])
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = outsider.GenerateProposals(ctx, request)
	requireCode(t, err, "AUTHORITY_DENIED")

}
