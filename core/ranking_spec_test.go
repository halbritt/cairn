package core

import (
	"slices"
	"strings"
	"testing"
)

// sealedRankings is the complete list of ranking identities ever sealed into a
// semantic package. It is deliberately a literal list, not derived from the
// table: the test proves the table reproduces exactly these bytes.
var sealedRankings = []string{
	"lexical-scope-recency/1", "lexical-scope-recency/2", "lexical-scope-recency/3", "lexical-scope-recency/4",
	"lexical-scope-recency/5", "lexical-scope-recency/6", "lexical-scope-recency/7",
	"semantic-scope-recency/1", "semantic-scope-recency/2", "semantic-scope-recency/3", "semantic-scope-recency/4",
	"hybrid-scope-recency/1", "hybrid-scope-recency/2", "hybrid-scope-recency/3", "hybrid-scope-recency/4",
	"interleaved-scope-recency/1", "interleaved-scope-recency/2", "interleaved-scope-recency/3", "interleaved-scope-recency/4",
	"interleaved-scope-recency/5", "interleaved-scope-recency/6", "interleaved-scope-recency/7", "interleaved-scope-recency/8",
	"binary-idf-scope-recency/1", "binary-idf-scope-recency/2", "binary-idf-scope-recency/3", "binary-idf-scope-recency/4",
	"binary-idf-scope-recency/5", "binary-idf-scope-recency/6", "binary-idf-scope-recency/7", "binary-idf-scope-recency/8",
}

var unsupportedRankings = []string{
	"", "lexical", "lexical-scope-recency/", "lexical-scope-recency/0", "lexical-scope-recency/8", "lexical-scope-recency/10",
	"semantic-scope-recency/0", "semantic-scope-recency/5", "hybrid-scope-recency/", "hybrid-scope-recency/5", "hybrid-scope-recency/8",
	"interleaved-scope-recency/", "interleaved-scope-recency/0", "interleaved-scope-recency/9",
	"binary-idf-scope-recency/", "binary-idf-scope-recency/0", "binary-idf-scope-recency/9", "Lexical-Scope-Recency/4", "lexical-scope-recency/4 ",
}

func TestRankingSpecRoundTripsEverySealedIdentity(t *testing.T) {
	table := AllRankingSpecs()
	if len(table) != len(sealedRankings) {
		t.Fatalf("table has %d rows, sealed list has %d", len(table), len(sealedRankings))
	}
	rendered := make([]string, 0, len(table))
	for _, spec := range table {
		rendered = append(rendered, spec.String())
	}
	if !slices.Equal(rendered, sealedRankings) {
		t.Fatalf("table renders\n%s\nwant\n%s", strings.Join(rendered, "\n"), strings.Join(sealedRankings, "\n"))
	}
	for _, ranking := range sealedRankings {
		spec, ok := ParseRanking(ranking)
		if !ok {
			t.Fatalf("ParseRanking(%q) unsupported", ranking)
		}
		if got := spec.String(); got != ranking {
			t.Fatalf("ParseRanking(%q).String() = %q", ranking, got)
		}
		if !SupportedRanking(ranking) {
			t.Fatalf("SupportedRanking(%q) = false", ranking)
		}
		if spec.Tokenizer < 1 || spec.Tokenizer > 4 || (spec.Channel != RankingLexical && spec.Tokenizer != 4) || (spec.Tier != RankingTierPlain && spec.Tokenizer != 4) {
			t.Fatalf("%q has tokenizer generation %d", ranking, spec.Tokenizer)
		}
		if spec.IndexedIDF && (spec.Channel != RankingInterleaved || !spec.CompoundTerms) {
			t.Fatalf("%q carries indexed IDF outside the interleaved compound channel", ranking)
		}
		if spec.CompoundTerms && spec.Channel != RankingInterleaved && spec.Channel != RankingBinaryIDF {
			t.Fatalf("%q carries compound terms on a channel without IDF evidence", ranking)
		}
	}
	for _, ranking := range unsupportedRankings {
		if spec, ok := ParseRanking(ranking); ok || spec != (RankingSpec{}) {
			t.Fatalf("ParseRanking(%q) = %+v, %v; want zero, false", ranking, spec, ok)
		}
		if SupportedRanking(ranking) {
			t.Fatalf("SupportedRanking(%q) = true", ranking)
		}
	}
}

func TestRankingChannelOfClassifiesPrefixesOnly(t *testing.T) {
	for _, ranking := range sealedRankings {
		spec, _ := ParseRanking(ranking)
		channel, ok := rankingChannelOf(ranking)
		if !ok || channel != spec.Channel {
			t.Fatalf("rankingChannelOf(%q) = %v, %v", ranking, channel, ok)
		}
	}
	for ranking, want := range map[string]RankingChannel{"interleaved-scope-recency/9": RankingInterleaved, "hybrid-scope-recency/": RankingHybrid, "binary-idf-scope-recency/0": RankingBinaryIDF} {
		if channel, ok := rankingChannelOf(ranking); !ok || channel != want {
			t.Fatalf("rankingChannelOf(%q) = %v, %v", ranking, channel, ok)
		}
	}
	for _, ranking := range []string{"", "lexical", "scope-recency/4", "binary-scope-recency/1"} {
		if _, ok := rankingChannelOf(ranking); ok {
			t.Fatalf("rankingChannelOf(%q) classified an unknown prefix", ranking)
		}
	}
}

// The oracles below are the string-enumerating predicates this module replaced,
// copied verbatim. Every predicate must agree with its oracle on every sealed
// and unsupported identity, which is what keeps retained packages replayable.
func oracleLegacyIDF(version string) string {
	switch version {
	case "binary-idf-scope-recency/5":
		return "binary-idf-scope-recency/1"
	case "binary-idf-scope-recency/6":
		return "binary-idf-scope-recency/2"
	case "binary-idf-scope-recency/7":
		return "binary-idf-scope-recency/3"
	case "binary-idf-scope-recency/8":
		return "binary-idf-scope-recency/4"
	}
	return version
}

func oracleLegacyIndexed(ranking string) string {
	switch ranking {
	case "interleaved-scope-recency/5":
		return "interleaved-scope-recency/1"
	case "interleaved-scope-recency/6":
		return "interleaved-scope-recency/2"
	case "interleaved-scope-recency/7":
		return "interleaved-scope-recency/3"
	case "interleaved-scope-recency/8":
		return "interleaved-scope-recency/4"
	}
	return ranking
}

func oracleFailure(version string) bool {
	version = oracleLegacyIndexed(oracleLegacyIDF(version))
	return version == "lexical-scope-recency/6" || version == "semantic-scope-recency/3" || version == "hybrid-scope-recency/3" || version == "interleaved-scope-recency/3" || version == "binary-idf-scope-recency/3"
}

func oracleEntity(version string) bool {
	version = oracleLegacyIndexed(oracleLegacyIDF(version))
	return version == "lexical-scope-recency/7" || version == "semantic-scope-recency/4" || version == "hybrid-scope-recency/4" || version == "interleaved-scope-recency/4" || version == "binary-idf-scope-recency/4"
}

func oracleLiteral(version string) bool {
	version = oracleLegacyIndexed(oracleLegacyIDF(version))
	return version == "lexical-scope-recency/5" || version == "semantic-scope-recency/2" || version == "hybrid-scope-recency/2" || version == "interleaved-scope-recency/2" || version == "binary-idf-scope-recency/2" || oracleFailure(version) || oracleEntity(version)
}

func oracleIDF(r string) bool {
	r = oracleLegacyIDF(r)
	return strings.HasPrefix(r, "binary-idf-scope-recency/") && (r == "binary-idf-scope-recency/1" || r == "binary-idf-scope-recency/2" || r == "binary-idf-scope-recency/3" || r == "binary-idf-scope-recency/4")
}

func oracleHybrid(ranking string) bool {
	return ranking == "hybrid-scope-recency/1" || ranking == "hybrid-scope-recency/2" || ranking == "hybrid-scope-recency/3" || ranking == "hybrid-scope-recency/4"
}

func oracleInterleaved(ranking string) bool {
	ranking = oracleLegacyIndexed(ranking)
	return ranking == "interleaved-scope-recency/1" || ranking == "interleaved-scope-recency/2" || ranking == "interleaved-scope-recency/3" || ranking == "interleaved-scope-recency/4"
}

func oracleIndexedIDF(ranking string) bool { return oracleLegacyIndexed(ranking) != ranking }

func oracleAdjacentCompound(version string) bool {
	return oracleLegacyIDF(version) != version || oracleIndexedIDF(version)
}

func oracleSemanticScope(ranking string) bool {
	return ranking == "semantic-scope-recency/1" || ranking == "semantic-scope-recency/2" || ranking == "semantic-scope-recency/3" || ranking == "semantic-scope-recency/4"
}

func oracleFallbackLexical(ranking string) bool {
	return ranking == "lexical-scope-recency/4" || ranking == "lexical-scope-recency/5" || ranking == "lexical-scope-recency/6" || ranking == "lexical-scope-recency/7"
}

func oracleSupported(ranking string) bool {
	return ranking == "lexical-scope-recency/1" || ranking == "lexical-scope-recency/2" || ranking == "lexical-scope-recency/3" || ranking == "lexical-scope-recency/4" || ranking == "semantic-scope-recency/1" || oracleLiteral(ranking) || oracleHybrid(ranking) || oracleInterleaved(ranking) || oracleIDF(ranking)
}

func oracleResolveSemantic(current string) string {
	switch {
	case oracleEntity(current):
		return "semantic-scope-recency/4"
	case oracleFailure(current):
		return "semantic-scope-recency/3"
	case oracleLiteral(current):
		return "semantic-scope-recency/2"
	default:
		return "semantic-scope-recency/1"
	}
}

func oracleResolveInterleaved(current string) string {
	switch {
	case oracleEntity(current):
		return "interleaved-scope-recency/4"
	case oracleFailure(current):
		return "interleaved-scope-recency/3"
	case oracleLiteral(current):
		return "interleaved-scope-recency/2"
	default:
		return "interleaved-scope-recency/1"
	}
}

func oracleIndexedIDFRanking(ranking string) string {
	switch ranking {
	case "interleaved-scope-recency/2":
		return "interleaved-scope-recency/6"
	case "interleaved-scope-recency/3":
		return "interleaved-scope-recency/7"
	case "interleaved-scope-recency/4":
		return "interleaved-scope-recency/8"
	default:
		return "interleaved-scope-recency/5"
	}
}

func oracleTerms(text, version string) map[string]bool {
	if version == "semantic-scope-recency/1" || oracleHybrid(version) || oracleInterleaved(version) || oracleLiteral(version) || oracleIDF(version) {
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
		removeRankingStopWords(terms, functionStopWords, functionStopSet)
	}
	if version == "lexical-scope-recency/4" {
		removeRankingStopWords(terms, questionStopWords, questionStopSet)
	}
	return terms
}

func TestRankingPredicatesMatchHistoricalEnumerations(t *testing.T) {
	all := append(slices.Clone(sealedRankings), unsupportedRankings...)
	for _, r := range all {
		checks := []struct {
			name      string
			got, want any
		}{
			{"hasLiteralRanking", hasLiteralRanking(r), oracleLiteral(r)},
			{"hasFailureRanking", hasFailureRanking(r), oracleFailure(r)},
			{"hasEntityRanking", hasEntityRanking(r), oracleEntity(r)},
			{"hasIDFRanking", hasIDFRanking(r), oracleIDF(r)},
			{"hasHybridRanking", hasHybridRanking(r), oracleHybrid(r)},
			{"hasInterleavedRanking", hasInterleavedRanking(r), oracleInterleaved(r)},
			{"hasIndexedRanking", hasIndexedRanking(r), oracleHybrid(r) || oracleInterleaved(r)},
			{"hasIndexedIDFRanking", hasIndexedIDFRanking(r), oracleIndexedIDF(r)},
			{"hasAdjacentCompoundRanking", hasAdjacentCompoundRanking(r), oracleAdjacentCompound(r)},
			{"hasSemanticScopeRanking", hasSemanticScopeRanking(r), oracleSemanticScope(r)},
			{"hasFallbackLexicalRanking", hasFallbackLexicalRanking(r), oracleFallbackLexical(r)},
			{"SupportedRanking", SupportedRanking(r), oracleSupported(r)},
			{"legacyIDFRanking", legacyIDFRanking(r), oracleLegacyIDF(r)},
			{"legacyIndexedRanking", legacyIndexedRanking(r), oracleLegacyIndexed(r)},
			{"resolveSemanticScopeRanking", resolveSemanticScopeRanking(r), oracleResolveSemantic(r)},
			{"resolveInterleavedRanking", resolveInterleavedRanking(r), oracleResolveInterleaved(r)},
			{"indexedIDFRanking", indexedIDFRanking(r), oracleIndexedIDFRanking(r)},
		}
		for _, c := range checks {
			if c.got != c.want {
				t.Fatalf("%s(%q) = %v, historical enumeration gave %v", c.name, r, c.got, c.want)
			}
		}
		text := "What does the_retry_handler do when a Stop hook fails? review-request a_b"
		got, want := rankingTerms(text, r), oracleTerms(text, r)
		if len(got) != len(want) {
			t.Fatalf("rankingTerms(%q) has %d terms, historical %d", r, len(got), len(want))
		}
		for term := range want {
			if !got[term] {
				t.Fatalf("rankingTerms(%q) lost %q", r, term)
			}
		}
	}
}

func TestRankingIntentIdentities(t *testing.T) {
	entity := []EntityRef{{Kind: "file", Name: "core/compiler.go"}}
	cases := []struct {
		entities         []EntityRef
		signature        string
		literals         []string
		idf, compoundIDF string
	}{
		{nil, "", nil, "binary-idf-scope-recency/1", "binary-idf-scope-recency/5"},
		{nil, "", []string{"x"}, "binary-idf-scope-recency/2", "binary-idf-scope-recency/6"},
		{nil, "sig", []string{"x"}, "binary-idf-scope-recency/3", "binary-idf-scope-recency/7"},
		{entity, "sig", []string{"x"}, "binary-idf-scope-recency/4", "binary-idf-scope-recency/8"},
		{entity, "", nil, "binary-idf-scope-recency/4", "binary-idf-scope-recency/8"},
	}
	for _, c := range cases {
		if got := idfRanking(c.entities, c.signature, c.literals); got != c.idf {
			t.Fatalf("idfRanking = %q, want %q", got, c.idf)
		}
		if got := adjacentIDFRanking(c.entities, c.signature, c.literals); got != c.compoundIDF {
			t.Fatalf("adjacentIDFRanking = %q, want %q", got, c.compoundIDF)
		}
		tier := rankingTierForIntent(c.entities, c.signature, c.literals)
		if got := rankingSpec(RankingInterleaved, tier).withIndexedIDF().String(); got != strings.Replace(c.compoundIDF, "binary-idf-", "interleaved-", 1) {
			t.Fatalf("interleaved IDF identity for tier %d = %q", tier, got)
		}
	}
	for _, generation := range []int{1, 2, 3, 4} {
		if got, want := lexicalRanking(generation).String(), "lexical-scope-recency/"+string(rune('0'+generation)); got != want {
			t.Fatalf("lexicalRanking(%d) = %q", generation, got)
		}
	}
}
