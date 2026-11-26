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
	commonWords := strings.Fields(`retry backoff scheduler queue worker lease timeout deploy release rollback migration
schema postgres index search ranking preview budget receipt handle pull expand token socket api hook session inbox
delivery notice request response agent codex claude opencode hermes surveyor audit report finding coverage config
binary build test integration check lint format commit branch worktree merge rebase push remote origin main review
approval owner decision lesson procedure observation claim evidence citation source revision version currentness
eligibility scope project repository task run workspace file symbol entity literal quoted semantic embedding model
vector cosine threshold latency throughput memory cache disk gpu cpu process service systemd restart reload journal
log error failure exit status refusal integrity seal hash digest body summary span offset length bytes utf8 escape
json cbor openapi contract compatibility legacy replay recompile historical cohort weight frequency idf lexical
tokenizer term query room optional mandatory instruction conflict advisory policy grant authority retention delete
forget purge backup restore fence generation watcher presence heartbeat wake channel queue listener native turn
prompt stop continuation completion acknowledgement journal intent replay settle reconcile hold lease expire renew
cleanup stale orphan socket permission ownership sandbox fixture disposable cluster database transaction serializable
retry conflict deadlock chunk batch scan limit bounded window page cursor offset next previous first last newest
oldest ordering stable deterministic seed random sample median percentile benchmark profile flame allocation garbage
the a to of and in is for on with that this it as be are was by not do from at or but if when then must should never
always only after before during while because however instead unless except without between within across through`)
	words := append(strings.Fields("the and is it of what how do does did a_b the_x under_score café Ünï retry backoff 42 x_1"), commonWords...)
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
