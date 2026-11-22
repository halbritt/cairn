package core

import (
	"fmt"
	"maps"
	"math/rand/v2"
	"reflect"
	"strings"
	"testing"
)

func randomRankingBody(rng *rand.Rand) string {
	pieces := []string{
		"cache", "key", "cache-key", "Cache-Key", "cache_key", "white", "cmf", "white-cmf", "-white-cmf", "white-cmf-", "compile",
		"latency", "compile-latency", "candidate", "the", "of", "what", "how", "not", "storage", "Storage?", "x", "42", "é", "Σ",
		"straße", "K", "\xff", "-", "--", " ", " ", "\t", " ", "\n", ".", ",", "_", "日本",
	}
	var b strings.Builder
	for n := rng.IntN(900); b.Len() < n; {
		b.WriteString(pieces[rng.IntN(len(pieces))])
		if rng.IntN(3) == 0 {
			b.WriteByte(' ')
		}
	}
	return b.String()
}

var bodyReuseQueries = []string{"cache-key latency", "white-cmf", "compile-latency candidate storage", "cache key", "what is the cache", "x", "-"}

// Reusing a base set built under any ranking of the same generation yields
// exactly bodyTerms, and the shared base is never written.
func TestBodyTermsFromSharedBaseMatchesBodyTerms(t *testing.T) {
	rankings := previewEquivalenceRankings()
	for seed := uint64(1); seed <= 60; seed++ {
		body := randomRankingBody(rand.New(rand.NewPCG(seed, 7)))
		for _, query := range bodyReuseQueries {
			for _, ranking := range rankings {
				lexicon := newRankingLexicon(query, ranking)
				want := lexicon.bodyTerms(body)
				for _, source := range rankings {
					if rankingGeneration(source) != rankingGeneration(ranking) {
						continue
					}
					base := rankingTerms(body, source)
					before := maps.Clone(base)
					if got := lexicon.bodyTermsFrom(body, base); !maps.Equal(got, want) {
						t.Fatalf("ranking %q from %q query %q: got %v want %v", ranking, source, query, got, want)
					}
					if !maps.Equal(base, before) {
						t.Fatalf("ranking %q query %q wrote to the shared base set", ranking, query)
					}
				}
			}
		}
	}
}

func indexedRankings() []string {
	var out []string
	for _, spec := range AllRankingSpecs() {
		if spec.IndexedIDF {
			out = append(out, spec.String())
		}
	}
	return out
}

func reuseCandidates(t testing.TB, n int, ranking, query string, withBase bool) ([]candidate, map[string]*CandidateEvaluation) {
	t.Helper()
	lexicon := newRankingLexicon(query, ranking)
	rng := rand.New(rand.NewPCG(uint64(n), 3))
	candidates := make([]candidate, n)
	evaluations := make(map[string]*CandidateEvaluation, n)
	for i := range candidates {
		id := fmt.Sprintf("00000000-0000-4000-8000-%012d", i)
		record := Record{RecordID: id, Version: 1, Class: "A", Draft: Draft{Kind: "note", Body: randomRankingBody(rng)}}
		candidates[i] = candidate{selection: Selection{Record: record}}
		if withBase {
			base, _, _, _ := scoreCandidateLexicalBase(record, lexicon)
			candidates[i].bodyBase, candidates[i].bodyGeneration = base, rankingGeneration(ranking)
		}
		evaluations[id] = &CandidateEvaluation{}
	}
	return candidates, evaluations
}

func TestIndexedIDFMembersReuseIsExact(t *testing.T) {
	for _, ranking := range indexedRankings() {
		for _, query := range bodyReuseQueries {
			plain, plainEval := reuseCandidates(t, 120, ranking, query, false)
			reused, reusedEval := reuseCandidates(t, 120, ranking, query, true)
			want := indexedIDFMembers(query, ranking, plain, plainEval, true)
			got := indexedIDFMembers(query, ranking, reused, reusedEval, true)
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("ranking %q query %q: members differ", ranking, query)
			}
			for i := range plain {
				id := plain[i].selection.Record.RecordID
				if plain[i].score != reused[i].score || plain[i].selection.Reason != reused[i].selection.Reason || plainEval[id].LexicalMatches != reusedEval[id].LexicalMatches {
					t.Fatalf("ranking %q query %q: candidate %d scoring differs", ranking, query, i)
				}
			}
		}
	}
}

// A base from another generation is ignored; one at this generation is used.
func TestIndexedIDFMembersReuseRequiresGeneration(t *testing.T) {
	ranking := indexedRankings()[0]
	query := "cache-key latency"
	plain, plainEval := reuseCandidates(t, 40, ranking, query, false)
	want := indexedIDFMembers(query, ranking, plain, plainEval, false)
	foreign := map[string]bool{"cache": true, "key": true, "latency": true}
	for _, generation := range []int{1, 2, 3} {
		stale, staleEval := reuseCandidates(t, 40, ranking, query, false)
		for i := range stale {
			stale[i].bodyBase, stale[i].bodyGeneration = foreign, generation
		}
		if got := indexedIDFMembers(query, ranking, stale, staleEval, false); !reflect.DeepEqual(got, want) {
			t.Fatalf("generation %d base was reused", generation)
		}
	}
	used, usedEval := reuseCandidates(t, 40, ranking, query, false)
	for i := range used {
		used[i].bodyBase, used[i].bodyGeneration = foreign, rankingGeneration(ranking)
	}
	if got := indexedIDFMembers(query, ranking, used, usedEval, false); reflect.DeepEqual(got, want) {
		t.Fatal("a base at the lexicon generation was not reused")
	}
}

// BenchmarkIndexedIDFMembers measures the ready-index lexical pass over about
// 8k candidates, with bodies re-tokenized (recompute) or reused from
// collectCandidates (reuse).
func BenchmarkIndexedIDFMembers(b *testing.B) {
	ranking := indexedRankings()[0]
	query := "recall hook timeout cache-key latency"
	for _, mode := range []struct {
		name     string
		withBase bool
	}{{"recompute", false}, {"reuse", true}} {
		b.Run(mode.name, func(b *testing.B) {
			candidates, evaluations := reuseCandidates(b, 8000, ranking, query, mode.withBase)
			b.ReportAllocs()
			b.ResetTimer()
			for b.Loop() {
				if members := indexedIDFMembers(query, ranking, candidates, evaluations, true); len(members) != len(candidates) {
					b.Fatalf("members %d", len(members))
				}
			}
		})
	}
}
