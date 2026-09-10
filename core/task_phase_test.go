package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestTaskPhaseSelectsGuidanceWithinOneTaskClass(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "phase-guidance"})
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Body = "Before validation, run the storage integration checks."
	// Exercise the persisted JSON contract, including an older reader's behavior.
	if err := json.Unmarshal([]byte(`{"task_class":"repair","task_phase":"validation"}`), &draft.Pins); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
		t.Fatal(err)
	}
	var pins *ContextPins
	if err := json.Unmarshal([]byte(`{"task_class":"repair","task_phase":"implementation"}`), &pins); err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "repair-task", "implementation-run"}, Context: pins, Purpose: "context", AvailableTokens: 32000}
	p, err := s.Compile(ctx, req, Destination{"local", true})
	if err != nil || len(p.Semantic.Selected) != 0 || p.Semantic.Omitted["CURRENTNESS_MISMATCH"] != 1 {
		t.Fatalf("validation-only guidance entered implementation: %+v %v", p, err)
	}
}

func TestTaskPhasePackagesRetainIntentAndHistoricalSelection(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "phase-history"})
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Pins = &Applicability{TaskClass: "repair", TaskPhase: "validation"}
	record, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"body", "index", "browse", "filtered", "semantic"} {
		t.Run(mode, func(t *testing.T) {
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "repair", mode}, Context: &ContextPins{TaskClass: "repair", TaskPhase: "validation"}, Purpose: "context", AvailableTokens: 32000}
			if mode != "body" {
				req.Mode = "index"
			}
			if mode == "browse" {
				req.BrowseOffset = new(int)
			}
			if mode == "filtered" {
				req.Kinds = []string{"note"}
			}
			if mode == "semantic" {
				req.Semantic, req.Query = true, "fixture"
			}
			p, err := s.Compile(ctx, req, Destination{"local", true})
			if err != nil || p.Semantic.Schema != "cairn.semantic/10" || len(p.Semantic.Selected)+len(p.Semantic.Index) != 1 || p.Semantic.Context.TaskPhase != "validation" {
				t.Fatalf("phase package lost context or compiler identity: %+v %v", p, err)
			}
			again, err := s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: req.Query})
			if err != nil || again.Seal != p.Seal {
				t.Fatalf("phase intent lost on historical recompile: %+v %v", again, err)
			}
		})
	}
	widened := record.Draft
	widened.Pins = &Applicability{TaskClass: "repair"}
	_, err = s.Edit(ctx, EditRequest{uuid.NewString(), record.RecordID, record.Version, widened})
	requireCode(t, err, "AUTHORITY_DENIED")
}

func TestTaskPhaseMandatoryInstructionsKeepUnknownContextAndConflictBoundaries(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	for _, phase := range []string{"implementation", "validation"} {
		draft := projectNote(repo)
		draft.Kind, draft.Body = "instruction", "Required checks for "+phase
		draft.Pins = &Applicability{TaskClass: "repair", TaskPhase: phase}
		if _, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, PolicyKey: "phase-checks", Reason: "Distinct requirements for disjoint declared phases"}); err != nil {
			t.Fatal(err)
		}
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "repair", "run"}, Context: &ContextPins{TaskClass: "repair"}, Purpose: "context", AvailableTokens: 32000}
	_, err := op.Compile(ctx, req, Destination{"local", true})
	requireCode(t, err, "POLICY_UNENFORCEABLE")
	for _, phase := range []string{"implementation", "validation"} {
		req.RequestID, req.Context.TaskPhase = uuid.NewString(), phase
		p, err := op.Compile(ctx, req, Destination{"local", true})
		if err != nil || len(p.Semantic.Selected) != 1 || p.Semantic.Selected[0].Record.Body != "Required checks for "+phase {
			t.Fatalf("disjoint phases created a false conflict or lost required context: %+v %v", p, err)
		}
	}
	for _, phase := range []string{"*", strings.Repeat("x", 257)} {
		req.RequestID, req.Context.TaskPhase = uuid.NewString(), phase
		_, err := op.Compile(ctx, req, Destination{"local", true})
		requireCode(t, err, "INVALID_REQUEST")
	}
}

func TestTaskPhaseCannotBeDroppedFromDerivedGuidance(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "phase-derivation"})
	draft := projectNote(uuid.NewString())
	draft.Pins = &Applicability{TaskPhase: "validation"}
	source, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	draft.Body = "Guidance derived from the phase-specific source."
	draft.Relations = []RecordRelation{{source.RecordID, source.Version, "derived_from"}}
	draft.Pins = nil
	_, err = s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	requireCode(t, err, "AUTHORITY_DENIED")
	draft.Pins = &Applicability{TaskPhase: "implementation"}
	_, err = s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	requireCode(t, err, "AUTHORITY_DENIED")
	draft.Pins.TaskPhase = "validation"
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
		t.Fatalf("matching derived phase refused: %v", err)
	}
}
