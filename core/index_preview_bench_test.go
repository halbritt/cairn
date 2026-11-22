package core

import (
	"fmt"
	"math/rand/v2"
	"strings"
	"testing"
)

// BenchmarkPackIndex packs about 8k candidates, the size of a production
// context index compile, so preview CPU is measured at that scale.
func BenchmarkPackIndex(b *testing.B) {
	vocabulary := strings.Fields("recall latency compile retrieval candidate hook timeout store error index expand session agent " +
		"coordinator campaign rollout binding cohort launcher generation receipt evaluation semantic ranking passage the of and to " +
		"with is a for on in that this Proximal Codex Claude OpenCode Hermes cache_key failure_mode CAIRN-301 f7bec707 2026-11-22 " +
		"verified reviewed landed deployed observed measured migration schema journal lease delivery inbox notice request response")
	rng := rand.New(rand.NewPCG(8000, 1))
	candidates := make([]candidate, 8000)
	for i := range candidates {
		var body strings.Builder
		for body.Len() < 600+rng.IntN(900) {
			body.WriteString(vocabulary[rng.IntN(len(vocabulary))])
			if rng.IntN(9) == 0 {
				body.WriteString(". ")
			} else {
				body.WriteByte(' ')
			}
		}
		id := fmt.Sprintf("00000000-0000-4000-8000-%012d", i)
		candidates[i] = candidate{selection: Selection{Record: Record{RecordID: id, Version: 1, Class: "A", Draft: Draft{Kind: "note", Body: body.String()}}}}
	}
	query := "recall hook timeout store error index"
	base := SemanticPackage{Mode: "index", Purpose: "context", Schema: "cairn.semantic/17", Ranking: "binary-idf-scope-recency/5", AvailableTokens: 32000, OptionalLimit: 3200, Omitted: omissionCensus()}
	b.ReportAllocs()
	b.ResetTimer()
	for b.Loop() {
		evaluations := make(map[string]*CandidateEvaluation, len(candidates))
		for _, c := range candidates {
			evaluations[c.selection.Record.RecordID] = &CandidateEvaluation{}
		}
		p := base
		p.Omitted = omissionCensus()
		got, err := packIndex(p, candidates, evaluations, query)
		if err != nil || len(got.Index) == 0 {
			b.Fatalf("pack: %d entries, %v", len(got.Index), err)
		}
	}
}
