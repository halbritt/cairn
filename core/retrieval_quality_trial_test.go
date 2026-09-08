package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// This opt-in measurement records misses instead of turning authored relevance
// labels into a release gate. Run only with an owned disposable test database.
func TestDocumentationRetrievalQuality(t *testing.T) {
	output := os.Getenv("CAIRN_RETRIEVAL_REPORT")
	if output == "" {
		t.Skip("set CAIRN_RETRIEVAL_REPORT for the documentation retrieval measurement")
	}
	raw, err := os.ReadFile("testdata/retrieval-quality.json")
	if err != nil {
		t.Fatal(err)
	}
	var workload struct {
		SourceCommit    string `json:"source_commit"`
		AvailableTokens int    `json:"available_tokens"`
		Notes           []struct{ ID, Path, Body, SHA256 string }
		Queries         []struct {
			ID, Query string
			Relevant  []string
		}
	}
	if err = json.Unmarshal(raw, &workload); err != nil {
		t.Fatal(err)
	}
	ids := map[string]bool{}
	for _, n := range workload.Notes {
		digest := sha256.Sum256([]byte(n.Body))
		if n.ID == "" || ids[n.ID] || hex.EncodeToString(digest[:]) != n.SHA256 {
			t.Fatalf("duplicate/invalid note or changed source bytes: %s", n.ID)
		}
		ids[n.ID] = true
	}
	queryIDs := map[string]bool{}
	for _, q := range workload.Queries {
		if q.ID == "" || queryIDs[q.ID] || q.Query == "" {
			t.Fatalf("duplicate/invalid question: %s", q.ID)
		}
		queryIDs[q.ID] = true
		for _, id := range q.Relevant {
			if !ids[id] {
				t.Fatalf("unknown relevance label: %s", id)
			}
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	s := testStore(t, Channel{Principal: "agent:documentation-retrieval-trial"})
	type evaluation struct {
		ID      string `json:"id"`
		Rank    int    `json:"eligible_rank"`
		Matches int    `json:"lexical_matches"`
		Reason  string `json:"reason"`
	}
	type result struct {
		QueryID       string         `json:"query_id"`
		Order         string         `json:"insertion_order"`
		Mode          string         `json:"mode"`
		Ranking       string         `json:"ranking"`
		Returned      []string       `json:"returned"`
		Evaluations   []evaluation   `json:"evaluations"`
		Omitted       map[string]int `json:"omitted"`
		RenderedBytes int            `json:"rendered_bytes"`
	}
	results := []result{}
	for _, order := range []string{"forward", "reverse"} {
		repo := "documentation-trial:" + uuid.NewString()
		notes := slices.Clone(workload.Notes)
		if order == "reverse" {
			slices.Reverse(notes)
		}
		labels := map[string]string{}
		for _, n := range notes {
			r, err := s.Create(ctx, CreateRequest{uuid.NewString(), Draft{
				Kind: "note", Body: n.Body, Scope: Scope{repo, "*", "*"}, ClaimType: "self",
			}})
			if err != nil {
				t.Fatal(err)
			}
			labels[r.RecordID] = n.ID
		}
		for _, q := range workload.Queries {
			for _, mode := range []string{"", "index"} {
				p, err := s.Compile(ctx, CompileRequest{
					RequestID: uuid.NewString(), Scope: Scope{repo, q.ID, "measurement"},
					Query: q.Query, Purpose: "context", Mode: mode, AvailableTokens: workload.AvailableTokens,
				}, Destination{"local", true})
				if err != nil {
					t.Fatal(err)
				}
				rendered, err := p.Render()
				if err != nil {
					t.Fatal(err)
				}
				r := result{QueryID: q.ID, Order: order, Mode: mode, Ranking: p.Semantic.Ranking,
					Returned: []string{}, Evaluations: []evaluation{}, Omitted: p.Semantic.Omitted, RenderedBytes: len(rendered)}
				if mode == "" {
					r.Mode = "body"
				}
				for _, entry := range p.Semantic.Selected {
					r.Returned = append(r.Returned, labels[entry.Record.RecordID])
				}
				for _, entry := range p.Semantic.Index {
					r.Returned = append(r.Returned, labels[entry.RecordID])
				}
				explanation, err := s.Explain(ctx, p.ReceiptID)
				if err != nil {
					t.Fatal(err)
				}
				if len(explanation.Candidates) != len(notes) {
					t.Fatalf("incomplete corpus evaluation for %s: %d candidates", q.ID, len(explanation.Candidates))
				}
				for _, e := range explanation.Candidates {
					r.Evaluations = append(r.Evaluations, evaluation{labels[e.RecordID], e.Rank, e.LexicalMatches, e.Reason})
				}
				slices.SortFunc(r.Evaluations, func(a, b evaluation) int {
					return strings.Compare(a.ID, b.ID)
				})
				results = append(results, r)
			}
		}
	}
	digest := sha256.Sum256(raw)
	report := map[string]any{
		"schema": "cairn.retrieval-quality/1", "workload_sha256": hex.EncodeToString(digest[:]),
		"source_commit": workload.SourceCommit, "available_tokens": workload.AvailableTokens,
		"results": results,
		"limits":  "Authored questions on twelve selected documentation passages. Local ordinary A notes, equal scope, two insertion orders. No model, authority promotion, operational memory or downstream task benefit. Scores are observations, not acceptance criteria.",
	}
	body, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(output, append(body, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
}
