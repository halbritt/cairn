package core

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestClaimRunRejectsChangedSelectionAndRequiredContext(t *testing.T) {
	for _, mode := range []string{"", "index"} {
		for _, change := range []string{"selected-edit", "selected-retract", "selected-dispute", "new-mandatory", "new-runtime-mandatory"} {
			t.Run(mode+"/"+change, func(t *testing.T) {
				ctx := context.Background()
				op, root := testOperator(t)
				repo := uuid.NewString()
				host := testStore(t, Channel{Principal: "host:" + repo, Instrumented: true, Repo: repo})
				draft := projectNote(repo)
				record, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
				if err != nil {
					t.Fatal(err)
				}
				request := CompileRequest{Mode: mode, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "compiler", Purpose: "context", AvailableTokens: 32000}
				pkg, err := host.Compile(ctx, request, Destination{"local", true})
				if err != nil {
					t.Fatal(err)
				}
				if len(pkg.Semantic.Selected)+len(pkg.Semantic.Index) != 1 {
					t.Fatal("fixture did not select the note")
				}
				switch change {
				case "selected-edit":
					draft.Body = "The corrected compiler procedure supersedes the old note."
					_, err = op.Edit(ctx, EditRequest{uuid.NewString(), record.RecordID, record.Version, draft})
				case "selected-retract":
					preview, previewErr := op.PreviewRetraction(ctx, record.RecordID)
					if previewErr != nil {
						t.Fatal(previewErr)
					}
					_, err = op.Retract(ctx, RetractRequest{uuid.NewString(), record.RecordID, record.Version, root.ID, "Withdraw guidance before launch", preview.PreviewID})
				case "selected-dispute":
					draft.Body = "A conflicting compiler procedure."
					other, createErr := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
					if createErr != nil {
						t.Fatal(createErr)
					}
					_, err = op.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{record.RecordID, other.RecordID}, "These procedures disagree"})
				case "new-mandatory", "new-runtime-mandatory":
					draft.Body = "Required guidance published after retrieval."
					draft.Kind = "instruction"
					_, err = op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, RequiresRuntime: change == "new-runtime-mandatory", PolicyKey: "new-rule", Reason: "Require context that the retained package lacks"})
				}
				if err != nil {
					t.Fatal(err)
				}
				code := "STALE_PACKAGE"
				if change == "new-runtime-mandatory" {
					code = "POLICY_UNENFORCEABLE"
				}
				requireCode(t, host.ClaimRun(ctx, pkg.ReceiptID), code)
				var claimed bool
				if err = host.pool.QueryRow(ctx, `SELECT launch_claimed FROM cairn.retrieval_receipt WHERE receipt_id=$1`, pkg.ReceiptID).Scan(&claimed); err != nil || claimed {
					t.Fatalf("refused launch consumed its claim: %v, %v", claimed, err)
				}
				replayed, err := host.Replay(ctx, pkg.ReceiptID)
				if err != nil || replayed.Seal != pkg.Seal {
					t.Fatalf("freshness refusal damaged historical replay: %v", err)
				}
				request.RequestID = uuid.NewString()
				fresh, err := host.Compile(ctx, request, Destination{"local", true})
				if change == "new-runtime-mandatory" {
					requireCode(t, err, "POLICY_UNENFORCEABLE")
					return
				}
				if err != nil {
					t.Fatal(err)
				}
				if err = host.ClaimRun(ctx, fresh.ReceiptID); err != nil {
					t.Fatalf("fresh package cannot launch: %v", err)
				}
				requireCode(t, host.ClaimRun(ctx, fresh.ReceiptID), "RUN_ALREADY_STARTED")
			})
		}
	}
}

func TestClaimRunRechecksGrantAndEvidence(t *testing.T) {
	for _, mode := range []string{"", "index"} {
		for _, change := range []string{"revoked-grant", "divergent-evidence"} {
			t.Run(mode+"/"+change, func(t *testing.T) {
				ctx := context.Background()
				op, root := testOperator(t)
				repo := uuid.NewString()
				host := testStore(t, Channel{Principal: "host:" + repo, Instrumented: true, Repo: repo})
				draft := projectNote(repo)
				grant, err := op.Grant(ctx, GrantRequest{RequestID: uuid.NewString(), ParentID: root.ID, Principal: op.channel.Principal, Repo: repo, Capabilities: []string{"issue", "promote"}, Reason: "Grant authority for launch eligibility fixture"})
				if err != nil {
					t.Fatal(err)
				}
				var evidence Evidence
				if change == "revoked-grant" {
					draft.Kind = "instruction"
					_, err = op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: grant.ID, PolicyKey: "optional-rule", Reason: "Issue optional instruction for grant recheck"})
				} else {
					record, createErr := host.Create(ctx, CreateRequest{uuid.NewString(), draft})
					if createErr != nil {
						t.Fatal(createErr)
					}
					evidence = testEvidence(t, op, repo)
					_, err = op.Promote(ctx, PromoteRequest{uuid.NewString(), record.RecordID, record.Version, grant.ID, []string{evidence.ID}, "Support the consequential launch fixture", nil})
				}
				if err != nil {
					t.Fatal(err)
				}
				pkg, err := host.Compile(ctx, CompileRequest{Mode: mode, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "compiler", Purpose: "planning", AvailableTokens: 32000}, Destination{"local", true})
				if err != nil || len(pkg.Semantic.Selected)+len(pkg.Semantic.Index) != 1 {
					t.Fatalf("fixture not selected: %+v, %v", pkg, err)
				}
				if change == "revoked-grant" {
					_, err = op.RevokeGrant(ctx, RevokeGrantRequest{uuid.NewString(), grant.ID, root.ID, 1, "Revoke authority after package compilation"})
				} else {
					// Simulate changed external evidence, then observe it through
					// the same supported check used by the evidence test suite.
					if _, err = op.pool.Exec(ctx, `UPDATE cairn.evidence SET body='changed fixture evidence' WHERE evidence_id=$1`, evidence.ID); err != nil {
						t.Fatal(err)
					}
					_, err = op.CheckEvidence(ctx, EvidenceCheckRequest{uuid.NewString(), evidence.ID})
				}
				if err != nil {
					t.Fatal(err)
				}
				requireCode(t, host.ClaimRun(ctx, pkg.ReceiptID), "STALE_PACKAGE")
			})
		}
	}
}

func TestClaimRunRejectsExpiredSelection(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	host := testStore(t, Channel{Principal: "host:" + repo, Instrumented: true, Repo: repo})
	draft := projectNote(repo)
	until := time.Now().Add(2 * time.Second)
	draft.Pins = &Applicability{ValidUntil: &until}
	if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
		t.Fatal(err)
	}
	var receipts []string
	for _, mode := range []string{"", "index"} {
		pkg, err := host.Compile(ctx, CompileRequest{Mode: mode, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "compiler", Purpose: "context", AvailableTokens: 32000}, Destination{"local", true})
		if err != nil || len(pkg.Semantic.Selected)+len(pkg.Semantic.Index) != 1 {
			t.Fatalf("fixture expired before compilation: %+v, %v", pkg, err)
		}
		receipts = append(receipts, pkg.ReceiptID)
	}
	time.Sleep(time.Until(until) + time.Millisecond)
	for _, id := range receipts {
		requireCode(t, host.ClaimRun(ctx, id), "STALE_PACKAGE")
	}
}

func TestClaimRunDoesNotRerankOrAddOptionalMemory(t *testing.T) {
	for _, mode := range []string{"", "index"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			op, _ := testOperator(t)
			repo := uuid.NewString()
			host := testStore(t, Channel{Principal: "host:" + repo, Instrumented: true, Repo: repo})
			draft := projectNote(repo)
			if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
				t.Fatal(err)
			}
			pkg, err := host.Compile(ctx, CompileRequest{Mode: mode, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "compiler", Purpose: "context", AvailableTokens: 32000}, Destination{"local", true})
			if err != nil {
				t.Fatal(err)
			}
			draft.Body = "New optional compiler guidance with higher scope specificity."
			draft.Scope.TaskID = "task"
			if _, err = op.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
				t.Fatal(err)
			}
			if err = host.ClaimRun(ctx, pkg.ReceiptID); err != nil {
				t.Fatalf("unrelated optional addition prevented exact delivery: %v", err)
			}
			replayed, err := host.Replay(ctx, pkg.ReceiptID)
			if err != nil || replayed.Seal != pkg.Seal || len(replayed.Semantic.Selected)+len(replayed.Semantic.Index) != 1 {
				t.Fatalf("claim changed the retained package: %+v, %v", replayed, err)
			}
		})
	}
}
