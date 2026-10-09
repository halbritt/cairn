package core

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// In a plain compile, a mandatory body that fits on its own
// but not with the serialized envelope must refuse, never be dropped. The body
// is multi-byte and escape-heavy so encoding overhead is part of the overflow.
func TestMandatoryPlusEnvelopeOverflowRefusesPlainCompile(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Kind, draft.Body = "instruction", strings.Repeat("必須 \"guard\"\n", 200)
	issued, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, PolicyKey: "envelope-overflow", Reason: "Required source near the total limit"})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "unrelated-query", Purpose: "context", AvailableTokens: 64000}
	full, err := op.Compile(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(full.Semantic.Selected) != 1 || full.Semantic.Selected[0].Record.RecordID != issued.RecordID {
		t.Fatalf("fixture did not deliver the required source: %+v", full.Semantic.Omitted)
	}
	rendered, err := full.Render()
	if err != nil {
		t.Fatal(err)
	}
	if len(issued.Body) >= len(rendered)-1 {
		t.Fatalf("fixture must overflow only with the envelope: body=%d rendered=%d", len(issued.Body), len(rendered))
	}
	// The envelope carries the budget itself, so step down from the package's own
	// rendered size until refusal. Every accepted package must keep the required
	// source whole and fit its budget; dropping it is never an acceptable outcome.
	budget := len(rendered) - 1
	for attempt := 0; ; attempt++ {
		if attempt == 8 {
			t.Fatalf("no refusal reached near the envelope boundary; last budget %d", budget)
		}
		if budget <= len(issued.Body) {
			t.Fatalf("fixture reached body overflow before envelope refusal: body=%d budget=%d", len(issued.Body), budget)
		}
		req.RequestID, req.AvailableTokens = uuid.NewString(), budget
		tight, err := op.Compile(ctx, req, Destination{"local", true})
		if err != nil {
			requireCode(t, err, "BUDGET_REFUSED")
			break
		}
		if len(tight.Semantic.Selected) != 1 || tight.Semantic.Selected[0].Record.RecordID != issued.RecordID || tight.Semantic.Selected[0].Record.Body != issued.Body {
			t.Fatalf("mandatory context was dropped instead of refused at budget %d: selected=%d status=%s omitted=%v", budget, len(tight.Semantic.Selected), tight.Semantic.Status, tight.Semantic.Omitted)
		}
		accepted, err := tight.Render()
		if err != nil {
			t.Fatal(err)
		}
		if len(accepted) > budget {
			t.Fatalf("accepted package exceeds its budget: %d > %d", len(accepted), budget)
		}
		budget = len(accepted) - 1
	}
}
