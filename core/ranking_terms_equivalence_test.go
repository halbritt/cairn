package core

import (
	"maps"
	"math/rand/v2"
	"strings"
	"testing"
)

// legacyRankingTerms is the pre-CAIRN-116 implementation, kept verbatim so the
// stop-list precomputation is proven not to change any historical token set.
func legacyRankingTerms(text, version string) map[string]bool {
	if version == "semantic-scope-recency/1" || hasIndexedRanking(version) || hasLiteralRanking(version) || hasIDFRanking(version) {
		version = "lexical-scope-recency/4"
	}
	terms := lexical(text)
	if version == "lexical-scope-recency/3" || version == "lexical-scope-recency/4" {
		for word := range terms {
			for _, part := range strings.Split(word, "_") {
				if part != "" {
					terms[part] = true
				}
			}
		}
	}
	if version == "lexical-scope-recency/2" || version == "lexical-scope-recency/3" || version == "lexical-scope-recency/4" {
		for _, word := range strings.Fields("a an and are as at be by for from in is it of on or that the this to was were with") {
			delete(terms, word)
		}
	}
	if version == "lexical-scope-recency/4" {
		for _, word := range strings.Fields("what where when why who whom whose which how do does did") {
			delete(terms, word)
		}
	}
	return terms
}

func TestRankingTermsMatchesLegacyTokenSets(t *testing.T) {
	versions := []string{"", "lexical-scope-recency/1", "lexical-scope-recency/2", "lexical-scope-recency/3",
		"lexical-scope-recency/4", "lexical-scope-recency/5", "lexical-scope-recency/7", "semantic-scope-recency/1",
		"semantic-scope-recency/2", "hybrid-scope-recency/2", "interleaved-scope-recency/4",
		"binary-idf-scope-recency/1", "binary-idf-scope-recency/2", "binary-idf-scope-recency/4"}
	texts := []string{"", "the", "The a AN", "what", "Do not reuse the cached result.", "how_do the_one a_b__c _is_",
		"When does it WORK? who whom whose which", "Ünïcode café ÄND the naïve Straße 東京 and",
		"single", "a an and are as at be by for from in is it of on or that the this to was were with",
		"what where when why who whom whose which how do does did",
		"config.key_name=12.5 path/to/file_the.go does_not_apply"}
	words := append(strings.Fields("the and is it of what how do does did a_b the_x under_score café Ünï retry backoff 42 x_1"), scaleCommon...)
	r := rand.New(rand.NewPCG(116, 235))
	for range 300 {
		var b strings.Builder
		for n := r.IntN(60); n >= 0; n-- {
			b.WriteString(words[r.IntN(len(words))])
			b.WriteString([]string{" ", "_", ". ", "\n", "-", ""}[r.IntN(6)])
		}
		texts = append(texts, b.String())
	}
	for _, version := range versions {
		for _, text := range texts {
			if got, want := rankingTerms(text, version), legacyRankingTerms(text, version); !maps.Equal(got, want) {
				t.Fatalf("version %q text %q: got %v want %v", version, text, got, want)
			}
		}
	}
}
