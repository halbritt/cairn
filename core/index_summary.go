package core

import (
	"strings"
	"unicode"
	"unicode/utf8"
)

// indexSummary extracts source bytes, never a generated paraphrase. Prefer a
// passage containing more distinct query terms than the ordinary body prefix.
func indexSummary(body, query, ranking string) string {
	summary, _ := indexPreview(body, query, ranking)
	return summary
}

func indexPreview(body, query, ranking string) (string, ByteSpanRequest) {
	return newPreviewer(query, ranking).preview(body)
}

// previewer holds the query-side preview inputs of one pack. A pack previews
// thousands of candidates with the same query and ranking, so the query terms,
// literals and tokenizer generation are derived once instead of per body.
type previewer struct {
	generation int
	terms      map[string]bool
	literal    bool
	literals   []string
}

func newPreviewer(query, ranking string) *previewer {
	// New IDF term evidence does not redefine historical preview selection.
	ranking = legacyIndexedRanking(legacyIDFRanking(ranking))
	p := &previewer{generation: rankingGeneration(ranking), terms: rankingTerms(query, ranking), literal: hasLiteralRanking(ranking)}
	if p.literal {
		p.literals = queryLiterals(query)
	}
	return p
}

// wordMatches appends the query terms in rankingTerms(word, ranking) to dst,
// without building that set: fields of the lower-cased word split as lexical
// splits them, plus underscore parts from generation 3. Each term is appended
// once; order is not significant.
func (p *previewer) wordMatches(word string, dst []string) []string {
	lower := strings.ToLower(word)
	start := -1
	for offset, r := range lower {
		if unicode.IsLetter(r) || unicode.IsDigit(r) || r == '_' {
			if start < 0 {
				start = offset
			}
			continue
		}
		if start >= 0 {
			dst = p.fieldMatches(lower[start:offset], dst)
			start = -1
		}
	}
	if start >= 0 {
		dst = p.fieldMatches(lower[start:], dst)
	}
	return dst
}

func (p *previewer) fieldMatches(field string, dst []string) []string {
	dst = p.addMatch(field, dst)
	if p.generation < 3 || strings.IndexByte(field, '_') < 0 {
		return dst
	}
	for rest := field; ; {
		cut := strings.IndexByte(rest, '_')
		part := rest
		if cut >= 0 {
			part = rest[:cut]
		}
		if part != "" {
			dst = p.addMatch(part, dst)
		}
		if cut < 0 {
			return dst
		}
		rest = rest[cut+1:]
	}
}

// The query terms already exclude this generation's stop words, so membership
// alone decides whether a body term matches.
func (p *previewer) addMatch(term string, dst []string) []string {
	if !p.terms[term] {
		return dst
	}
	for _, have := range dst {
		if have == term {
			return dst
		}
	}
	return append(dst, term)
}

func (p *previewer) preview(body string) (string, ByteSpanRequest) {
	const limit = 160
	prefix := body[:utf8Prefix(body, limit)]
	span := ByteSpanRequest{Length: len(prefix)}
	if p.literal && len(body) > limit {
		if preview, matchedSpan, ok := literalPreview(body, p.literals); ok {
			return preview, matchedSpan
		}
	}
	if len(body) <= limit || len(p.terms) == 0 {
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
		m := match{start: start, end: end, terms: p.wordMatches(body[start:end], nil)}
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
		// Leave some preceding context, starting after a whitespace boundary.
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
			continue // The full prefix already has at least as much room.
		}
		// Reserve both omission markers so even a multibyte excerpt fits.
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

func utf8Prefix(text string, limit int) int {
	end := min(len(text), limit)
	for end > 0 && !utf8.ValidString(text[:end]) {
		end--
	}
	return end
}

// Show the earliest literal occurrence; offsets always address the original body.
func literalPreview(body string, literals []string) (string, ByteSpanRequest, bool) {
	position, size := len(body), 0
	for _, literal := range literals {
		at := strings.Index(body, literal)
		if at >= 0 && at < position {
			position, size = at, len(literal)
		}
	}
	if size == 0 {
		return "", ByteSpanRequest{}, false
	}
	if position+size <= utf8Prefix(body, 160) {
		end := utf8Prefix(body, 160)
		return body[:end], ByteSpanRequest{Length: end}, true
	}
	start := max(0, position-min(40, max(0, 154-size)))
	for start < position && !utf8.RuneStart(body[start]) {
		start++
	}
	end := start + utf8Prefix(body[start:], 154)
	preview := body[start:end]
	if start > 0 {
		preview = "..." + preview
	}
	if end < len(body) {
		preview += "..."
	}
	return preview, ByteSpanRequest{Offset: start, Length: end - start}, true
}
