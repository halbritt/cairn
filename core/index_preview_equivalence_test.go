package core

import (
	"encoding/json"
	"math/rand/v2"
	"os"
	"strings"
	"testing"
	"unicode"
	"unicode/utf8"
)

// referenceIndexPreview is indexPreview as it was before the per-pack
// previewer: query terms and a rankingTerms set per body word on every call.
// It is the oracle for byte-identical previews and spans.
func referenceIndexPreview(body, query, ranking string) (string, ByteSpanRequest) {
	ranking = legacyIndexedRanking(legacyIDFRanking(ranking))
	const limit = 160
	prefix := body[:utf8Prefix(body, limit)]
	span := ByteSpanRequest{Length: len(prefix)}
	if hasLiteralRanking(ranking) && len(body) > limit {
		if preview, matchedSpan, ok := literalPreview(body, queryLiterals(query)); ok {
			return preview, matchedSpan
		}
	}
	terms := rankingTerms(query, ranking)
	if len(body) <= limit || len(terms) == 0 {
		return prefix, span
	}
	type match struct {
		start, end int
		terms      []string
	}
	matches := []match{}
	start := -1
	add := func(end int) {
		if start < 0 {
			return
		}
		m := match{start: start, end: end}
		for term := range rankingTerms(body[start:end], ranking) {
			if terms[term] {
				m.terms = append(m.terms, term)
			}
		}
		if len(m.terms) > 0 {
			matches = append(matches, m)
		}
		start = -1
	}
	for offset, r := range body {
		if unicode.IsLetter(r) || unicode.IsDigit(r) || r == '_' {
			if start < 0 {
				start = offset
			}
		} else {
			add(offset)
		}
	}
	add(len(body))

	counts := map[string]int{}
	left, right := 0, 0
	score := func(start, end int) int {
		for right < len(matches) && matches[right].end <= end {
			for _, term := range matches[right].terms {
				counts[term]++
			}
			right++
		}
		for left < right && matches[left].start < start {
			for _, term := range matches[left].terms {
				counts[term]--
				if counts[term] == 0 {
					delete(counts, term)
				}
			}
			left++
		}
		return len(counts)
	}
	best, bestScore := prefix, score(0, len(prefix))
	for _, m := range matches {
		start := max(0, m.start-40)
		if start > 0 {
			for start < m.start && !utf8.RuneStart(body[start]) {
				start++
			}
			boundary := m.start
			for offset, r := range body[start:m.start] {
				if unicode.IsSpace(r) {
					boundary = start + offset + utf8.RuneLen(r)
					break
				}
			}
			start = boundary
		}
		if start == 0 {
			continue
		}
		end := start + utf8Prefix(body[start:], limit-6)
		if end < len(prefix) {
			continue
		}
		if value := score(start, end); value > bestScore {
			best, bestScore = "..."+body[start:end], value
			span = ByteSpanRequest{Offset: start, Length: end - start}
			if end < len(body) {
				best += "..."
			}
		}
	}
	return best, span
}

// Every sealed ranking identity plus identities that fall back to generation 1.
func previewEquivalenceRankings() []string {
	rankings := []string{"", "unsupported/0", "lexical-scope-recency/99"}
	for _, spec := range AllRankingSpecs() {
		rankings = append(rankings, spec.String())
	}
	return rankings
}

func assertPreviewEquivalent(t *testing.T, body, query string, rankings []string) {
	t.Helper()
	for _, ranking := range rankings {
		wantText, wantSpan := referenceIndexPreview(body, query, ranking)
		gotText, gotSpan := indexPreview(body, query, ranking)
		if gotText != wantText || gotSpan != wantSpan {
			t.Fatalf("ranking %q query %q body %q:\nwant %q %+v\ngot  %q %+v", ranking, query, body, wantText, wantSpan, gotText, gotSpan)
		}
		// The per-pack previewer must agree with the per-call wrapper.
		packText, packSpan := newPreviewer(query, ranking).preview(body)
		if packText != wantText || packSpan != wantSpan {
			t.Fatalf("ranking %q query %q: shared previewer diverged", ranking, query)
		}
	}
}

func TestIndexPreviewMatchesReferenceOnFixtures(t *testing.T) {
	raw, err := os.ReadFile("testdata/retrieval-quality.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Notes []struct {
			Body string `json:"body"`
		} `json:"notes"`
		Queries []struct {
			Query string `json:"query"`
		} `json:"queries"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Notes) == 0 || len(fixture.Queries) == 0 {
		t.Fatal("retrieval-quality fixture has no notes or queries")
	}
	queries := []string{"", "the of and", "What is it?", `"exact phrase" storage`, "cache_key LOOKUP failure_mode", "reuse cached validation results"}
	for _, q := range fixture.Queries {
		queries = append(queries, q.Query)
	}
	rankings := previewEquivalenceRankings()
	for _, note := range fixture.Notes {
		words := strings.Fields(note.Body)
		// Queries drawn from the body exercise windows away from the prefix.
		local := append([]string{}, queries...)
		for i := 0; i+3 <= len(words); i += max(1, len(words)/4) {
			local = append(local, strings.Join(words[i:i+3], " "), `"`+strings.Join(words[i:i+2], " ")+`"`)
		}
		for _, query := range local {
			assertPreviewEquivalent(t, note.Body, query, rankings)
		}
	}
}

// Randomized bodies mix cases, underscores, stop words, digits, Unicode letters
// whose lower case changes length or category, combining marks and invalid
// UTF-8 bytes, around repeated query words.
func TestIndexPreviewMatchesReferenceOnRandomBodies(t *testing.T) {
	pieces := []string{
		"recall", "Recall", "RECALL", "latency", "compile_latency", "_recall_", "recall__latency", "candidate", "Candidate_ID",
		"the", "of", "and", "what", "How", "is", "a", "not", "must", "storage", "storage?", "STORAGE", "cache_key", "Cache_Key",
		"failure_mode", "x", "42", "v2", "٣٤", "é", "É", "straße", "STRASSE", "İstanbul", "ΣΊΣΥΦΟΣ", "Ⅷ", "ⓐ", "Ⓐ", "\u212a", "k",
		"e\u0301", "\ufffd", "\xff", "\xc3", "日本語", "_", "__", ".", ",", ";", "(", ")", "\"", "'", "-", "/", ":", "!", "?",
		" ", " ", " ", "  ", "\n", "\t", "\u00a0", "\u2003",
	}
	queries := []string{
		"recall latency", "Recall candidate storage", "compile_latency", "cache_key failure_mode", "what is the storage",
		`"recall latency" candidate`, `"Cache_Key"`, "straße STRASSE", "istanbul i̇stanbul", "σίσυφος", "k", "é e", "日本語", "the", "_",
	}
	rankings := previewEquivalenceRankings()
	for seed := uint64(1); seed <= 120; seed++ {
		rng := rand.New(rand.NewPCG(seed, 0x5eed))
		var b strings.Builder
		for n := rng.IntN(1200); b.Len() < n; {
			b.WriteString(pieces[rng.IntN(len(pieces))])
			if rng.IntN(3) == 0 {
				b.WriteByte(' ')
			}
		}
		body := b.String()
		for _, query := range queries {
			assertPreviewEquivalent(t, body, query, rankings)
		}
	}
}
