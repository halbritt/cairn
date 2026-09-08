package core

import (
	"context"
	"github.com/google/uuid"
	"strings"
	"testing"
	"time"
)

func TestDeclaredCurrentnessGatesAndCannotBeBroadenedByEdit(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "currentness"})
	repo := uuid.NewString()
	until := time.Now().Add(time.Hour)
	draft := projectNote(repo)
	draft.Pins = &Applicability{Revision: strings.Repeat("a", 40), TaskClass: "build", ValidUntil: &until}
	record, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "t", "r"}, Purpose: "context", AvailableTokens: 64000}
	p, err := s.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(p.Semantic.Selected) != 0 || p.Semantic.Omitted["CONTEXT_MISSING"] != 1 {
		t.Fatalf("missing pins: %+v %v", p, err)
	}
	req.RequestID = uuid.NewString()
	req.Context = &ContextPins{Revision: strings.Repeat("b", 40), TaskClass: "build"}
	p, err = s.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(p.Semantic.Selected) != 0 {
		t.Fatal("obsolete revision selected")
	}
	req.RequestID = uuid.NewString()
	req.Context.Revision = draft.Pins.Revision
	p, err = s.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(p.Semantic.Selected) != 1 {
		t.Fatalf("matching currentness: %+v %v", p, err)
	}
	replay, err := s.Replay(ctx, p.ReceiptID)
	if err != nil || replay.Seal != p.Seal || replay.Semantic.Context.Revision != draft.Pins.Revision {
		t.Fatal("currentness not sealed")
	}
	widened := record.Draft
	widened.Pins = nil
	_, err = s.Edit(ctx, EditRequest{uuid.NewString(), record.RecordID, record.Version, widened})
	requireCode(t, err, "AUTHORITY_DENIED")
	expired := draft
	past := time.Now().Add(-time.Hour)
	expired.Pins = &Applicability{ValidUntil: &past}
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), expired}); err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	p, err = s.Compile(ctx, req, Destination{"local", true})
	if err != nil || p.Semantic.Omitted["OUTSIDE_VALIDITY"] != 1 {
		t.Fatalf("expired advice selected: %+v %v", p, err)
	}
}

func TestMandatoryApplicabilityCannotBeSkippedByOmittingPins(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Kind = "instruction"
	draft.Pins = &Applicability{Revision: strings.Repeat("a", 40)}
	if _, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, RequiresRuntime: false, PolicyKey: "currentness-test", Reason: "Require explicit policy applicability"}); err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}
	_, err := op.Compile(ctx, req, Destination{"local", true})
	requireCode(t, err, "POLICY_UNENFORCEABLE")
	req.RequestID = uuid.NewString()
	req.Context = &ContextPins{Revision: strings.Repeat("a", 40)}
	p, err := op.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(p.Semantic.Selected) != 1 {
		t.Fatalf("qualified policy missing: %+v %v", p, err)
	}
}

func TestHostedMandatoryPolicyRespectsApplicability(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Kind = "instruction"
	draft.Body = "PRIVATE-POLICY-CANARY: inspect the legacy compiler"
	draft.Pins = &Applicability{Revision: strings.Repeat("a", 40)}
	_, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, RequiresRuntime: false, PolicyKey: "private-currentness", Reason: "Apply this private requirement only to the old revision"})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Context: &ContextPins{Revision: strings.Repeat("b", 40)}, Purpose: "context", AvailableTokens: 64000}
	p, err := op.Compile(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatalf("inapplicable private instruction blocked hosted compilation: %v", err)
	}
	if len(p.Semantic.Selected) != 0 {
		t.Fatal("private instruction escaped to hosted context")
	}
	for _, count := range p.Semantic.Omitted {
		if count != 0 {
			t.Fatal("private policy omission census leaked")
		}
	}
	explanation, err := op.Explain(ctx, p.ReceiptID)
	if err != nil || len(explanation.Candidates) != 0 {
		t.Fatalf("private policy candidate metadata leaked: %+v %v", explanation, err)
	}
	historical, err := op.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID})
	if err != nil || historical.Seal != p.Seal {
		t.Fatalf("private applicability replay: %v", err)
	}
	for _, pins := range []*ContextPins{nil, {Revision: draft.Pins.Revision}} {
		req.RequestID = uuid.NewString()
		req.Context = pins
		_, err = op.Compile(ctx, req, Destination{"hosted", false})
		requireCode(t, err, "POLICY_UNENFORCEABLE")
	}
}

func TestHostedPolicyApplicabilityBoundaries(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	past, future := time.Now().Add(-time.Hour), time.Now().Add(time.Hour)
	for _, tc := range []struct {
		name    string
		pins    *Applicability
		context *ContextPins
		blocked bool
	}{
		{"expired", &Applicability{ValidUntil: &past}, nil, false},
		{"not_yet_effective", &Applicability{ValidFrom: &future}, nil, false},
		{"expired_missing_context", &Applicability{ValidUntil: &past, TaskClass: "build"}, nil, false},
		{"workspace_mismatch", &Applicability{WorkspaceSHA256: strings.Repeat("a", 64)}, &ContextPins{WorkspaceSHA256: strings.Repeat("b", 64)}, false},
		{"task_mismatch", &Applicability{TaskClass: "build"}, &ContextPins{TaskClass: "review"}, false},
		{"binding_mismatch", &Applicability{BindingID: "old"}, &ContextPins{BindingID: "new"}, false},
		{"capability_mismatch", &Applicability{CapabilityID: "old"}, &ContextPins{CapabilityID: "new"}, false},
		{"task_missing", &Applicability{TaskClass: "build"}, &ContextPins{}, true},
		{"task_match", &Applicability{TaskClass: "build"}, &ContextPins{TaskClass: "build"}, true},
		{"unconstrained", nil, nil, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			repo := uuid.NewString()
			draft := projectNote(repo)
			draft.Kind = "instruction"
			draft.Pins = tc.pins
			draft.Body = "PRIVATE-POLICY-CANARY: inspect the legacy compiler"
			private, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, RequiresRuntime: true, PolicyKey: "private-boundary", Reason: "Exercise private mandatory runtime policy applicability"})
			if err != nil {
				t.Fatal(err)
			}
			publicDraft := projectNote(repo)
			publicDraft.Sensitivity = "shareable"
			public, err := op.Create(ctx, CreateRequest{uuid.NewString(), publicDraft})
			if err != nil {
				t.Fatal(err)
			}
			for _, mode := range []string{"", "index"} {
				req := CompileRequest{Mode: mode, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Context: tc.context, Purpose: "context", AvailableTokens: 64000}
				p, err := op.Compile(ctx, req, Destination{"hosted", false})
				if tc.blocked {
					requireCode(t, err, "POLICY_UNENFORCEABLE")
					continue
				}
				if err != nil {
					t.Fatalf("inapplicable private policy blocked mode %q: %v", mode, err)
				}
				rendered, err := p.Render()
				if err != nil || strings.Contains(rendered, private.RecordID) || strings.Contains(rendered, "PRIVATE-POLICY-CANARY") || !strings.Contains(rendered, public.RecordID) {
					t.Fatalf("hosted context privacy or public retrieval failed: %v", err)
				}
				for _, count := range p.Semantic.Omitted {
					if count != 0 {
						t.Fatal("private policy omission census leaked")
					}
				}
				explanation, err := op.Explain(ctx, p.ReceiptID)
				if err != nil || len(explanation.Candidates) != 1 || explanation.Candidates[0].RecordID != public.RecordID {
					t.Fatalf("private candidate metadata leaked or public candidate missing: %+v %v", explanation, err)
				}
				historical, err := op.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID})
				if err != nil || historical.Seal != p.Seal {
					t.Fatalf("hosted mode %q replay: %v", mode, err)
				}
			}
		})
	}
}

func TestNonoverlappingInstructionPinsDoNotCreatePolicyConflict(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	for _, dimension := range []string{"revision", "validity"} {
		t.Run(dimension, func(t *testing.T) {
			repo := uuid.NewString()
			past := time.Now().Add(-time.Hour)
			now := time.Now().Add(-time.Minute)
			left, right := &Applicability{Revision: strings.Repeat("a", 40)}, &Applicability{Revision: strings.Repeat("b", 40)}
			if dimension == "validity" {
				left = &Applicability{ValidFrom: &past, ValidUntil: &now}
				right = &Applicability{ValidFrom: &now}
			}
			for i, pins := range []*Applicability{left, right} {
				d := projectNote(repo)
				d.Kind = "instruction"
				d.Pins = pins
				if i == 0 {
					d.Body = "Use the prior workflow"
				} else {
					d.Body = "Use the current workflow"
				}
				if _, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, RequiresRuntime: false, PolicyKey: "workflow", Reason: "Issue distinct applicability fixture"}); err != nil {
					t.Fatal(err)
				}
			}
			pkg, err := op.Compile(ctx, CompileRequest{Context: &ContextPins{Revision: strings.Repeat("b", 40)}, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
			if err != nil || len(pkg.Semantic.Selected) != 1 || pkg.Semantic.Selected[0].Record.Body != "Use the current workflow" {
				t.Fatalf("nonoverlapping instructions conflicted: %+v %v", pkg, err)
			}
		})
	}
}
