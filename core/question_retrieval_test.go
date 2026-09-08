package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestQuestionWordsDoNotSupplyRelevance(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Body = "Use run-status to inspect what Cairn recorded after a lost reply."
	note, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	draft.Kind = "instruction"
	draft.Body = "Include source references."
	mandatory, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft,
		GrantID: root.ID, Mandatory: true, PolicyKey: "references", Reason: "Test required context"})
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"", "index"} {
		for _, test := range []struct {
			query    string
			wantNote bool
		}{
			{"What is the monthly subscription price?", false},
			{"what?", false},
			{"What was recorded after a lost reply?", true},
			{"", true},
		} {
			p, err := op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(),
				Scope: Scope{repo, "task", "run"}, Mode: mode, Query: test.query,
				Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
			if err != nil {
				t.Fatal(err)
			}
			foundNote, foundMandatory := false, false
			for _, s := range p.Semantic.Selected {
				foundNote = foundNote || s.Record.RecordID == note.RecordID
				foundMandatory = foundMandatory || s.Record.RecordID == mandatory.RecordID
			}
			for _, s := range p.Semantic.Index {
				foundNote = foundNote || s.RecordID == note.RecordID
			}
			if foundNote != test.wantNote || !foundMandatory {
				t.Errorf("mode=%q query=%q: optional note=%t, required instruction=%t", mode, test.query, foundNote, foundMandatory)
			}
			recompiled, err := op.Recompile(ctx, RecompileRequest{p.ReceiptID, test.query})
			if err != nil || recompiled.Seal != p.Seal {
				t.Fatalf("mode=%q query=%q: recompile: %v", mode, test.query, err)
			}
		}
	}
}

func TestQuestionWordFilteringIsVersioned(t *testing.T) {
	for _, version := range []string{"lexical-scope-recency/1", "lexical-scope-recency/2", "lexical-scope-recency/3", "lexical-scope-recency/4"} {
		terms := rankingTerms("what where when why who whom whose which how do does did not never without must should WHAT_IF", version)
		for _, word := range []string{"what", "where", "when", "why", "who", "whom", "whose", "which", "how", "do", "does", "did"} {
			if terms[word] != (version != "lexical-scope-recency/4") {
				t.Errorf("%s: question word %q present=%t", version, word, terms[word])
			}
		}
		for _, word := range []string{"not", "never", "without", "must", "should", "what_if"} {
			if !terms[word] {
				t.Errorf("%s: meaningful token %q missing", version, word)
			}
		}
	}
}
