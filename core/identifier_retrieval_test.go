package core

import (
	"context"
	"maps"
	"testing"

	"github.com/google/uuid"
)

func TestIdentifierWordsRetrieveSpecificNote(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:identifier-retrieval"})
	repo := uuid.NewString()
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), Draft{
		Kind: "note", Body: "CAIRN_HOME selects the local directory.", Scope: Scope{repo, "*", "*"}, ClaimType: "self",
	}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), Draft{
		Kind: "note", Body: "Cairn exposes an agent API.", Scope: Scope{repo, "*", "*"}, ClaimType: "self",
	}}); err != nil {
		t.Fatal(err)
	}
	for _, query := range []string{"cairn home", "CAIRN_HOME", "home"} {
		for _, mode := range []string{"", "index"} {
			p, err := s.Compile(ctx, CompileRequest{Mode: mode, RequestID: uuid.NewString(),
				Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 64000,
			}, Destination{"local", true})
			if err != nil {
				t.Fatal(err)
			}
			first := ""
			if mode == "index" && len(p.Semantic.Index) > 0 {
				first = p.Semantic.Index[0].RecordID
			} else if mode == "" && len(p.Semantic.Selected) > 0 {
				first = p.Semantic.Selected[0].Record.RecordID
			}
			if first != r.RecordID {
				t.Fatalf("query %q mode %q did not prefer the identifier note: %+v", query, mode, p.Semantic)
			}
			replayed, err := s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: query})
			if err != nil || replayed.Seal != p.Seal {
				t.Fatalf("identifier retrieval did not recompile: %v", err)
			}
		}
	}
}

func TestRankingTermsPreserveHistoricalIdentifierSemantics(t *testing.T) {
	for _, test := range []struct {
		version string
		want    map[string]bool
	}{
		{"lexical-scope-recency/1", map[string]bool{"the": true, "the_value": true, "not_task": true}},
		{"lexical-scope-recency/2", map[string]bool{"the_value": true, "not_task": true}},
		{"lexical-scope-recency/3", map[string]bool{"the_value": true, "value": true, "not_task": true, "not": true, "task": true}},
	} {
		if got := rankingTerms("the THE_VALUE not_task", test.version); !maps.Equal(got, test.want) {
			t.Errorf("%s: terms %v, want %v", test.version, got, test.want)
		}
	}
}
