package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestFailureSignatureRetrievesReviewedLesson(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "operator:signature", Operator: true, Instrumented: true})
	repo := uuid.NewString()
	signature := strings.Repeat("b", 64)
	groupFailure(t, s, repo, "original-task", "repair", "old-harness", "old-model", signature)
	batch, err := s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo})
	if err != nil || len(batch.Proposals) != 1 {
		t.Fatalf("proposal: %+v %v", batch, err)
	}
	draft := projectNote(repo)
	draft.Body = "Initialize the isolated database before starting the application."
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	p := batch.Proposals[0]
	_, err = s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: p.Version, Disposition: "converted", ResultRecord: note.RecordID, ResultVersion: note.Version, Reason: "Selected fixture lesson for this failure"})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "new-task", "new-run"}, Query: "connection refused", Purpose: "context", AvailableTokens: 32000}
	// Decode the public field so the pre-feature baseline fails on behavior.
	encoded, _ := json.Marshal(map[string]string{"error_signature_sha256": signature})
	if err = json.Unmarshal(encoded, &req); err != nil {
		t.Fatal(err)
	}
	index, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(index.Package.Semantic.Index) != 1 || index.Package.Semantic.Index[0].RecordID != note.RecordID {
		t.Fatalf("reviewed lesson missing despite matching signature: %+v", index.Package.Semantic.Index)
	}
	_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
	if err != nil {
		t.Fatalf("historical signature retrieval: %v", err)
	}
	// Relevant ordinary testimony remains blocked for consequential use, with
	// demand retained even though the query has no lexical overlap.
	req.RequestID, req.Purpose = uuid.NewString(), "planning"
	consequential, err := s.Compile(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	explanation, err := s.Explain(ctx, consequential.ReceiptID)
	if err != nil {
		t.Fatal(err)
	}
	if len(consequential.Semantic.Selected) != 0 || len(explanation.Candidates) != 1 || !explanation.Candidates[0].EscalationBlocked {
		t.Fatal("signature demand must remain visible without granting consequential use")
	}
}

func signatureLesson(t *testing.T, s *Store, draft Draft) (Proposal, Record) {
	t.Helper()
	ctx := context.Background()
	groupFailure(t, s, draft.Scope.Repo, uuid.NewString(), "repair", "old-harness", "old-model", strings.Repeat("b", 64))
	batch, err := s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: draft.Scope.Repo})
	if err != nil {
		t.Fatal(err)
	}
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	p := batch.Proposals[len(batch.Proposals)-1]
	return p, note
}

func TestFailureSignatureAssociationRequiresExplicitHostedSharing(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "operator:signature-sharing", Operator: true, Instrumented: true})
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity, d.Body = "shareable", "Initialize the isolated database."
	p, note := signatureLesson(t, s, d)
	review := ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: p.Version, Disposition: "converted", ResultRecord: note.RecordID, Reason: "Keep the association local by default"}
	converted, err := s.ReviewProposal(ctx, review)
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "new", "run"}, ErrorSignature: strings.Repeat("b", 64), Query: "connection refused", Purpose: "context", AvailableTokens: 32000}
	before, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil || len(before.Package.Semantic.Index) != 0 {
		t.Fatalf("local review association leaked: %+v %v", before, err)
	}
	review.RequestID, review.ExpectedVersion = uuid.NewString(), converted.Version
	if err = json.Unmarshal([]byte(`{"signature_shareable":true}`), &review); err != nil {
		t.Fatal(err)
	}
	_, err = s.ReviewProposal(ctx, review)
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	after, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil || len(after.Package.Semantic.Index) != 1 {
		t.Fatalf("explicitly shared association unavailable: %+v %v", after, err)
	}
	_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: after.Package.ReceiptID, Query: req.Query})
	if err != nil {
		t.Fatal(err)
	}
}

func TestFailureSignatureFreshSearchTracksReviewAndLessonVersions(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "operator:signature-currentness", Operator: true, Instrumented: true})
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body = "Initialize the isolated database."
	p, note := signatureLesson(t, s, d)
	review := ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: p.Version, Disposition: "converted", ResultRecord: note.RecordID, Reason: "Selected lesson"}
	converted, err := s.ReviewProposal(ctx, review)
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "later-task", "later-run"}, ErrorSignature: strings.Repeat("B", 64), Purpose: "context", AvailableTokens: 32000, PageOffset: new(int)}
	original, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil || len(original.Package.Semantic.Index) != 1 || original.Package.Semantic.ErrorSignature != strings.Repeat("b", 64) {
		t.Fatalf("signature-only page: %+v %v", original, err)
	}
	_, err = s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: converted.Version, Disposition: "open", Reason: "Reconsider the association"})
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	current, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil || len(current.Package.Semantic.Index) != 0 {
		t.Fatalf("reopened association still matches: %+v %v", current, err)
	}
	for _, index := range []IndexResult{original, current} {
		if replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: ""}); err != nil || replay.Seal != index.Package.Seal {
			t.Fatalf("reopened history: %v", err)
		}
	}
	review.RequestID, review.ExpectedVersion = uuid.NewString(), converted.Version+1
	converted, err = s.ReviewProposal(ctx, review)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: note.Version, Repo: repo, Body: "A different procedure after editing."})
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	current, err = s.Index(ctx, req, Destination{"local", true})
	if err != nil || len(current.Package.Semantic.Index) != 0 {
		t.Fatalf("review pin moved to new lesson: %+v %v", current, err)
	}
	_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: original.Package.ReceiptID, Query: ""})
	if err != nil {
		t.Fatal(err)
	}
	review.RequestID, review.ExpectedVersion, review.ResultVersion = uuid.NewString(), converted.Version, 2
	_, err = s.ReviewProposal(ctx, review)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: p.FailureReceipt, ExpectedVersion: p.FailureVersion, TaskOutcome: "unknown", FailureDomain: "unknown", Method: "review/1", Reason: "Withdraw the earlier failure assessment"})
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	current, err = s.Index(ctx, req, Destination{"local", true})
	if err != nil || len(current.Package.Semantic.Index) != 0 {
		t.Fatalf("superseded source still supplies match: %+v %v", current, err)
	}
}

func TestFailureSignaturePreservesEligibilityAndSemanticFallback(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	s := testStore(t, Channel{Principal: op.channel.Principal, Operator: true, Instrumented: true})
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity, d.Kind, d.Body = "shareable", "lesson", "Initialize the isolated database."
	p, note := signatureLesson(t, s, d)
	_, err := s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: p.Version, Disposition: "converted", ResultRecord: note.RecordID, SignatureShareable: true, Reason: "Share the selected association"})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = "Connection refused repair alternative."
	other, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Kind, d.Body = "instruction", "Required source checking."
	required, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, PolicyKey: "sources", Reason: "Required context"})
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"body", "index", "semantic", "fallback", "signature-only", "kind-filtered", "quoted"} {
		t.Run(mode, func(t *testing.T) {
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "new-task", mode}, ErrorSignature: strings.Repeat("b", 64), Query: "connection refused repair", Purpose: "context", AvailableTokens: 64000, Context: &ContextPins{TaskPhase: "implementation"}}
			if mode != "body" {
				req.Mode = "index"
			}
			if mode == "signature-only" {
				req.Query = ""
				req.PageOffset = new(int)
			}
			if mode == "quoted" {
				req.Query = `"database" "repair" connection refused`
			}
			if mode == "kind-filtered" {
				req.Kinds = []string{"decision"}
			}
			s.semanticRanker = nil
			if mode == "semantic" || mode == "fallback" {
				req.Semantic = true
			}
			if mode == "semantic" {
				s.semanticRanker = func(_ context.Context, r SemanticRankRequest) (SemanticRankResult, error) {
					result := SemanticRankResult{ModelSHA256: strings.Repeat("c", 64), Algorithm: "fixture/1"}
					for _, n := range r.Notes {
						score := 900000
						if n.RecordID == note.RecordID {
							score = -900000
						}
						result.Scores = append(result.Scores, SemanticScore{n.RecordID, n.Version, n.BodySHA256, score})
					}
					return result, nil
				}
			}
			pkg, err := s.Compile(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			if len(pkg.Semantic.Selected) == 0 || pkg.Semantic.Selected[0].Record.RecordID != required.RecordID {
				t.Fatal("lost mandatory context")
			}
			var ids []string
			for _, entry := range pkg.Semantic.Selected {
				if !entry.Mandatory {
					ids = append(ids, entry.Record.RecordID)
				}
			}
			for _, entry := range pkg.Semantic.Index {
				ids = append(ids, entry.RecordID)
			}
			if mode == "kind-filtered" {
				if len(ids) != 0 {
					t.Fatal("signature bypassed kind filter")
				}
			} else {
				first, second := note.RecordID, other.RecordID
				if mode == "quoted" {
					first, second = second, first
				}
				if len(ids) == 0 || ids[0] != first {
					t.Fatalf("missing signature preference: %v", ids)
				}
				if mode != "signature-only" && (len(ids) != 2 || ids[1] != second) {
					t.Fatalf("lost ordinary fallback: %v", ids)
				}
			}
			if replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: pkg.ReceiptID, Query: req.Query}); err != nil || replay.Seal != pkg.Seal {
				t.Fatalf("recompile: %v", err)
			}
		})
	}
	// Even an explicitly shared association cannot publish a local note or
	// override declared applicability.
	for _, sensitivity := range []string{"local", "shareable"} {
		d := projectNote(repo)
		d.Sensitivity = sensitivity
		d.Body = "Do not expose this unrelated remedy."
		if sensitivity == "shareable" {
			d.Pins = &Applicability{TaskPhase: "review", Revision: strings.Repeat("a", 40)}
		}
		p, n := signatureLesson(t, s, d)
		_, err := s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: p.Version, Disposition: "converted", ResultRecord: n.RecordID, SignatureShareable: true, Reason: "Fixture association"})
		if err != nil {
			t.Fatal(err)
		}
		pkg, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, ErrorSignature: strings.Repeat("b", 64), Purpose: "context", AvailableTokens: 64000, Context: &ContextPins{TaskPhase: "implementation"}}, Destination{"hosted", false})
		if err != nil {
			t.Fatal(err)
		}
		encoded, _ := json.Marshal(pkg)
		if strings.Contains(string(encoded), n.RecordID) || strings.Contains(string(encoded), d.Body) {
			t.Fatal("signature bypassed visibility or pins")
		}
		if _, err = s.Recompile(ctx, RecompileRequest{ReceiptID: pkg.ReceiptID, Query: ""}); err != nil {
			t.Fatal(err)
		}
	}
}

func TestFailureSignatureValidation(t *testing.T) {
	s := testStore(t, Channel{Principal: "agent:invalid-signature"})
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{uuid.NewString(), "task", "run"}, Purpose: "context", AvailableTokens: 32000, Mode: "index"}
	for _, signature := range []string{"bad", strings.Repeat("b", 63), strings.Repeat("g", 64), strings.Repeat("b", 64) + " "} {
		req.ErrorSignature = signature
		_, err := s.Compile(context.Background(), req, Destination{"local", true})
		requireCode(t, err, "INVALID_REQUEST")
	}
	req.ErrorSignature = strings.Repeat("b", 64)
	req.BrowseOffset = new(int)
	_, err := s.Compile(context.Background(), req, Destination{"local", true})
	requireCode(t, err, "INVALID_REQUEST")
	req.BrowseOffset = nil
	req.Semantic = true
	_, err = s.Compile(context.Background(), req, Destination{"local", true})
	requireCode(t, err, "INVALID_REQUEST")
}
