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
