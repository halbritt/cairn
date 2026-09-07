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
	if _, err := op.Issue(ctx, IssueRequest{uuid.NewString(), draft, root.ID, true, false, "currentness-test", "Require explicit policy applicability"}); err != nil {
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
				if _, err := op.Issue(ctx, IssueRequest{uuid.NewString(), d, root.ID, true, false, "workflow", "Issue distinct applicability fixture"}); err != nil {
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
