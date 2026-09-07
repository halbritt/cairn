package core

import (
	"context"
	"github.com/google/uuid"
	"strings"
	"testing"
	"time"
)

func TestFailureRecoveryProposalRetainsEvidenceAndReviewDisposition(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
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
