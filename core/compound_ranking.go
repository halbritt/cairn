package core

import (
	"regexp"
	"strings"
	"unicode"
)

// Adjacent-compound IDF versions retain the corresponding historical outer
// entity/failure/literal tiers and preview policy. Only their term evidence changes.
func legacyIDFRanking(version string) string {
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
func hasAdjacentCompoundRanking(version string) bool { return legacyIDFRanking(version) != version }
func adjacentIDFRanking(entities []EntityRef, signature string, literals []string) string {
	switch idfRanking(entities, signature, literals) {
	case "binary-idf-scope-recency/2":
		return "binary-idf-scope-recency/6"
	case "binary-idf-scope-recency/3":
		return "binary-idf-scope-recency/7"
	case "binary-idf-scope-recency/4":
		return "binary-idf-scope-recency/8"
	default:
		return "binary-idf-scope-recency/5"
	}
}

type rankingLexicon struct {
	ranking   string
	terms     map[string]bool
	compounds map[string]bool
	nodes     []compoundNode
}

// A token automaton shares compound prefixes and suffixes. Its failure/output
// links avoid scanning a whole body once for each query compound.
type compoundNode struct {
	next                map[string]int
	fail, output, depth int
	term                string
}

var validQueryCompound = regexp.MustCompile(`^[\pL\pN_]+(?:-[\pL\pN_]+)+$`)

// Extract whole valid query tokens independently of the matcher, so retained
// IDF statistics can verify query terms without rebuilding the automaton.
func queryCompounds(query string) map[string]bool {
	out := map[string]bool{}
	for _, term := range strings.FieldsFunc(strings.ToLower(query), func(r rune) bool { return !unicode.IsLetter(r) && !unicode.IsDigit(r) && r != '_' && r != '-' }) {
		if validQueryCompound.MatchString(term) {
			out[term] = true
		}
	}
	return out
}
func rankingQueryTerms(query, ranking string) map[string]bool {
	terms := rankingTerms(query, ranking)
	if hasAdjacentCompoundRanking(ranking) {
		for term := range queryCompounds(query) {
			terms[term] = true
		}
	}
	return terms
}

func newRankingLexicon(query, ranking string) rankingLexicon {
	l := rankingLexicon{ranking: ranking, terms: rankingTerms(query, ranking)}
	if !hasAdjacentCompoundRanking(ranking) {
		return l
	}
	l.compounds = map[string]bool{}
	l.nodes = []compoundNode{{next: map[string]int{}}}
	for term := range queryCompounds(query) {
		l.compounds[term] = true
		l.terms[term] = true
		at := 0
		for _, part := range strings.Split(term, "-") {
			next, ok := l.nodes[at].next[part]
			if !ok {
				next = len(l.nodes)
				depth := l.nodes[at].depth + 1
				l.nodes[at].next[part] = next
				l.nodes = append(l.nodes, compoundNode{next: map[string]int{}, depth: depth})
			}
			at = next
		}
		l.nodes[at].term = term
	}
	// Breadth-first failure links; output points to the nearest terminal suffix.
	queue := make([]int, 0, len(l.nodes))
	for _, child := range l.nodes[0].next {
		queue = append(queue, child)
	}
	for i := 0; i < len(queue); i++ {
		at := queue[i]
		for part, child := range l.nodes[at].next {
			failure := l.nodes[at].fail
			for failure != 0 && l.nodes[failure].next[part] == 0 {
				failure = l.nodes[failure].fail
			}
			l.nodes[child].fail = l.nodes[failure].next[part]
			suffix := l.nodes[child].fail
			if l.nodes[suffix].term != "" {
				l.nodes[child].output = suffix
			} else {
				l.nodes[child].output = l.nodes[suffix].output
			}
			queue = append(queue, child)
		}
	}
	return l
}

func (l rankingLexicon) bodyTerms(body string) map[string]bool {
	words := rankingTerms(body, l.ranking)
	if len(l.compounds) == 0 {
		return words
	}
	lower := strings.ToLower(body)
	// Body boundaries deliberately use all Unicode numbers (\pN), whereas the
	// unchanged lexical/query tokenizer uses IsDigit. No normalization is added.
	type segment struct {
		text       string
		start, end int
	}
	segments := make([]segment, 0)
	start := -1
	for i, r := range lower {
		word := unicode.IsLetter(r) || unicode.IsNumber(r) || r == '_'
		if word && start < 0 {
			start = i
		}
		if !word && start >= 0 {
			segments = append(segments, segment{lower[start:i], start, i})
			start = -1
		}
	}
	if start >= 0 {
		segments = append(segments, segment{lower[start:], start, len(lower)})
	}
	at := 0
	for i, segment := range segments {
		if i > 0 && !compoundSeparator(lower[segments[i-1].end:segment.start]) {
			at = 0
		}
		for at != 0 && l.nodes[at].next[segment.text] == 0 {
			at = l.nodes[at].fail
		}
		at = l.nodes[at].next[segment.text]
		// An adjacent hyphen is a forbidden outer boundary, even if a shorter
		// compound would otherwise be a suffix/prefix of a longer body compound.
		if segment.end < len(lower) && lower[segment.end] == '-' {
			continue
		}
		for matched := at; matched != 0; matched = l.nodes[matched].output {
			node := l.nodes[matched]
			if node.term == "" {
				continue
			}
			first := segments[i-node.depth+1].start
			if first == 0 || lower[first-1] != '-' {
				words[node.term] = true
			}
		}
	}
	return words
}
func compoundSeparator(s string) bool {
	if s == "-" {
		return true
	}
	if s == "" {
		return false
	}
	for _, r := range s {
		if r != '\t' && !unicode.Is(unicode.Zs, r) {
			return false
		}
	}
	return true
}
