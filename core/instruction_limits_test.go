package core

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func categoryRules() *PolicyRules {
	return &PolicyRules{OptionalPercent: 10, OptionalMaxTokens: 6000, InstructionLimits: &InstructionLimits{
		Security:   &InstructionLimit{MaxCount: 10, MaxTokens: 10000},
		Workflow:   &InstructionLimit{MaxCount: 10, MaxTokens: 10000},
		Preference: &InstructionLimit{MaxCount: 10, MaxTokens: 10000},
	}}
}

func TestInstructionLimitsUseUTF8BodyTokensAndAllowExactBoundary(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	rules := categoryRules()
	rules.InstructionLimits.Security.MaxTokens = 5
	policy, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: rules, Reason: "Use the conservative byte bound for category tokens"})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Kind = "instruction"
	draft.Body = "界界" // Six UTF-8 bytes, not two conservative tokens.
	_, err = op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, PolicyKey: "unicode", Category: "security", Reason: "Require a multibyte instruction at a known token boundary"})
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"", "index"} {
		_, err = op.Compile(ctx, CompileRequest{Mode: mode, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		requireCode(t, err, "BUDGET_REFUSED")
	}
	rules.InstructionLimits.Security.MaxTokens = 6
	_, err = op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, ExpectedRevisionID: policy.RevisionID, GrantID: root.ID, Rules: rules, Reason: "Allow exactly the immutable instruction body byte count"})
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"", "index"} {
		p, err := op.Compile(ctx, CompileRequest{Mode: mode, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil || len(p.Semantic.Selected) != 1 || p.Semantic.Selected[0].Category != "security" {
			t.Fatalf("exact category boundary refused: %+v %v", p, err)
		}
	}
}

func TestInstructionLimitsOmitOptionalOverflowAndReserveIndexBodies(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	for _, bound := range []string{"count", "tokens"} {
		t.Run(bound, func(t *testing.T) {
			repo := uuid.NewString()
			rules := categoryRules()
			if bound == "count" {
				rules.InstructionLimits.Security.MaxCount = 1
			} else {
				rules.InstructionLimits.Security.MaxTokens = 1500
			}
			policy, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: rules, Reason: "Limit optional security instructions independently of other categories"})
			if err != nil {
				t.Fatal(err)
			}
			for i, category := range []string{"security", "security", "workflow", "preference"} {
				d := projectNote(repo)
				d.Kind = "instruction"
				d.Body = strings.Repeat(string(rune('a'+i)), 1000)
				if category != "security" {
					d.Body = d.Body[:40]
				}
				_, err = op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, PolicyKey: uuid.NewString(), Category: category, Reason: "Issue a distinct optional category fixture"})
				if err != nil {
					t.Fatal(err)
				}
			}
			_, err = op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
			if err != nil {
				t.Fatal(err)
			}
			packages := []Package{}
			for _, mode := range []string{"", "index"} {
				query := CompileRequest{Mode: mode, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}
				var p Package
				var index IndexResult
				if mode == "index" {
					index, err = op.Index(ctx, query, Destination{"local", true})
					p = index.Package
				} else {
					p, err = op.Compile(ctx, query, Destination{"local", true})
				}
				if err != nil {
					t.Fatal(err)
				}
				counts := map[string]int{}
				for _, entry := range p.Semantic.Selected {
					counts[entry.Category]++
				}
				for _, entry := range p.Semantic.Index {
					counts[entry.Category]++
				}
				if p.Semantic.Omitted["CATEGORY_BUDGET"] != 1 || counts["security"] != 1 || counts["workflow"] != 1 || counts["preference"] != 1 || counts[""] != 1 {
					t.Fatalf("mode %q: category quota or non-instruction retrieval changed: counts=%+v omissions=%+v", mode, counts, p.Semantic.Omitted)
				}
				if mode == "index" {
					instructions := map[string]bool{}
					for _, entry := range p.Semantic.Index {
						instructions[entry.RecordID] = entry.Class == "C"
					}
					for _, h := range index.Handles {
						if !instructions[h.RecordID] {
							continue
						}
						if _, err = op.Expand(ctx, ExpandRequest{uuid.NewString(), p.ReceiptID, h.Handle, nil}, Destination{"local", true}); err != nil {
							t.Fatal(err)
						}
					}
				}
				packages = append(packages, p)
			}
			later, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, ExpectedRevisionID: policy.RevisionID, GrantID: root.ID, Rules: &PolicyRules{OptionalPercent: 10, OptionalMaxTokens: 6000}, Reason: "Return to the previous engine while retaining category history"})
			if err != nil {
				t.Fatal(err)
			}
			for _, p := range packages {
				replayed, err := op.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID})
				if err != nil || replayed.Seal != p.Seal {
					t.Fatalf("category replay changed after policy revision: %v", err)
				}
			}
			restored, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, ExpectedRevisionID: later.RevisionID, RestoreRevisionID: policy.RevisionID, GrantID: root.ID, Reason: "Restore category rules under a new authorized revision"})
			if err != nil || restored.Engine != "local-loop/3" || restored.RevisionID == policy.RevisionID {
				t.Fatalf("category policy rollback failed: %+v %v", restored, err)
			}
			fresh, err := op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "fresh"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
			if err != nil || fresh.Semantic.Omitted["CATEGORY_BUDGET"] != 1 {
				t.Fatalf("restored category policy failed to enforce its quota: %+v %v", fresh, err)
			}
		})
	}
}

func TestInstructionLimitsReserveMandatoryBeforeOptional(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	rules := categoryRules()
	rules.InstructionLimits.Security.MaxCount = 1
	_, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: rules, Reason: "Reserve the single security slot for mandatory policy"})
	if err != nil {
		t.Fatal(err)
	}
	var required Record
	for _, mandatory := range []bool{true, false} {
		draft := projectNote(repo)
		draft.Kind = "instruction"
		if mandatory {
			draft.Body = "Required security instruction"
		} else {
			draft.Body = "Newer optional security instruction"
		}
		record, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: mandatory, Category: "security", PolicyKey: uuid.NewString(), Reason: "Exercise shared mandatory and optional category accounting"})
		if err != nil {
			t.Fatal(err)
		}
		if mandatory {
			required = record
		}
	}
	for _, mode := range []string{"", "index"} {
		p, err := op.Compile(ctx, CompileRequest{Mode: mode, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		if err != nil || len(p.Semantic.Selected) != 1 || p.Semantic.Selected[0].Record.RecordID != required.RecordID || len(p.Semantic.Index) != 0 || p.Semantic.Omitted["CATEGORY_BUDGET"] != 1 {
			t.Fatalf("optional instruction displaced required policy or escaped the shared cap: %+v %v", p, err)
		}
	}
}

func TestInstructionLimitsRefuseMandatoryCategoryOverflow(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	rules := categoryRules()
	rules.InstructionLimits.Security.MaxCount = 1
	_, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: rules, Reason: "Bound security instruction count independently of workflow"})
	if err != nil {
		t.Fatal(err)
	}
	for range 2 {
		draft := projectNote(repo)
		draft.Kind = "instruction"
		_, err = op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, PolicyKey: uuid.NewString(), Category: "security", Reason: "Issue independently required security instruction"})
		if err != nil {
			t.Fatal(err)
		}
	}
	for _, mode := range []string{"", "index"} {
		_, err = op.Compile(ctx, CompileRequest{Mode: mode, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
		requireCode(t, err, "BUDGET_REFUSED")
	}
}

func TestInstructionLimitsKeepCategoryThroughScopeAndRetraction(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	rules := categoryRules()
	rules.InstructionLimits.Workflow.MaxCount = 0
	_, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: rules, Reason: "Only explicitly categorized security instructions fit this fixture"})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Kind = "instruction"
	draft.Scope.TaskID = "original"
	req := IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, PolicyKey: "preserved", Category: "security", Reason: "Retain the authority-assigned instruction category across scope changes"}
	instruction, err := op.Issue(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	preview, err := op.PreviewRetraction(ctx, instruction.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.AuthorizeScope(ctx, AuthorizeScopeRequest{RequestID: uuid.NewString(), RecordID: instruction.RecordID, ExpectedVersion: instruction.Version, Scope: Scope{repo, "*", "*"}, Pins: &Applicability{}, GrantID: root.ID, PreviewID: preview.PreviewID, Reason: "Authorize broader applicability without recategorizing the instruction"})
	if err != nil {
		t.Fatal(err)
	}
	info, err := op.InstructionPolicy(ctx, instruction.RecordID)
	if err != nil || info.Version != 2 || info.Category != "security" || !info.Mandatory {
		t.Fatalf("scope lost instruction metadata: %+v %v", info, err)
	}
	p, err := op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "other", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil || len(p.Semantic.Selected) != 1 || p.Semantic.Selected[0].Category != "security" {
		t.Fatalf("widened instruction used another category budget: %+v %v", p, err)
	}
	preview, err = op.PreviewRetraction(ctx, instruction.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.Retract(ctx, RetractRequest{RequestID: uuid.NewString(), RecordID: instruction.RecordID, ExpectedVersion: info.Version, GrantID: root.ID, Reason: "Retire the instruction while preserving its category history", PreviewID: preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	info, err = op.InstructionPolicy(ctx, instruction.RecordID)
	if err != nil || info.Category != "security" || info.Lifecycle != "retracted" {
		t.Fatalf("retraction lost category: %+v %v", info, err)
	}
	other := testStore(t, Channel{Principal: "scoped-inspector", Repo: uuid.NewString()})
	_, err = other.InstructionPolicy(ctx, instruction.RecordID)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = op.Issue(ctx, req)
	if err != nil {
		t.Fatalf("transport retry should retain original issued response: %v", err)
	}
	req.Category = "preference"
	_, err = op.Issue(ctx, req)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
}

func TestInstructionLimitsValidateCategoriesAndPreserveLegacyPolicies(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Kind = "instruction"
	req := IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, PolicyKey: "legacy-default", Reason: "An omitted category remains the legacy workflow classification"}
	instruction, err := op.Issue(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	info, err := op.InstructionPolicy(ctx, instruction.RecordID)
	if err != nil || info.Category != "workflow" {
		t.Fatalf("omitted category default: %+v %v", info, err)
	}
	p, err := op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil || len(p.Semantic.Selected) != 1 || p.Semantic.Selected[0].Category != "" {
		t.Fatalf("legacy policy gained category payload: %v", err)
	}
	for _, rules := range []*PolicyRules{
		{InstructionLimits: &InstructionLimits{}},
		{InstructionLimits: &InstructionLimits{Security: &InstructionLimit{}, Workflow: &InstructionLimit{}}},
	} {
		_, err = op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: rules, Reason: "Incomplete category limits must fail before policy adoption"})
		requireCode(t, err, "INVALID_REQUEST")
	}
	for _, bad := range []*InstructionLimit{{MaxCount: -1}, {MaxCount: 10001}, {MaxTokens: -1}, {MaxTokens: 1000001}} {
		rules := categoryRules()
		rules.InstructionLimits.Security = bad
		_, err = op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: rules, Reason: "Out of bounds category limits must fail before policy adoption"})
		requireCode(t, err, "INVALID_REQUEST")
	}
	req.RequestID = uuid.NewString()
	req.Category = "arbitrary"
	_, err = op.Issue(ctx, req)
	requireCode(t, err, "INVALID_REQUEST")
	rules := categoryRules()
	rules.InstructionLimits.Workflow.MaxCount = 0
	_, err = op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: rules, Reason: "Explicitly adopt limits that reject the legacy workflow instruction"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "fresh"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	requireCode(t, err, "BUDGET_REFUSED")
	historical, err := op.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID})
	if err != nil || historical.Seal != p.Seal {
		t.Fatalf("adopting categories reinterpreted legacy policy history: %v", err)
	}
}

func TestInstructionLimitsDoNotExposePrivateCategoryCandidates(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	rules := categoryRules()
	rules.InstructionLimits.Workflow.MaxCount = 0
	_, err := op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: rules, Reason: "A category quota must not expose private optional candidates"})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Kind = "instruction"
	draft.Body = "PRIVATE-CATEGORY-CANARY"
	private, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, PolicyKey: "private", Category: "workflow", Reason: "Keep this optional private instruction outside hosted visibility"})
	if err != nil {
		t.Fatal(err)
	}
	draft.Body = "Visible instruction"
	draft.Sensitivity = "shareable"
	_, err = op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, PolicyKey: "public", Category: "security", Reason: "Deliver this shareable category-labelled instruction"})
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"", "index"} {
		p, err := op.Compile(ctx, CompileRequest{Mode: mode, RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"hosted", false})
		if err != nil || len(p.Semantic.Selected) != 1 || p.Semantic.Omitted["CATEGORY_BUDGET"] != 0 {
			t.Fatalf("private candidate consumed a visible category slot: %+v %v", p, err)
		}
		rendered, err := p.Render()
		if err != nil || strings.Contains(rendered, private.RecordID) || strings.Contains(rendered, "PRIVATE-CATEGORY-CANARY") {
			t.Fatalf("private category candidate leaked: %v", err)
		}
		e, err := op.Explain(ctx, p.ReceiptID)
		if err != nil || len(e.Candidates) != 1 {
			t.Fatalf("private candidate entered explanation: %+v %v", e, err)
		}
	}
}
