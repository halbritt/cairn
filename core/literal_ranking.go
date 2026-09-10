package core

import "strings"

// Quoted literals are soft preferences, not a query filter or entity resolver.
// Keep extraction bounded and retain ordinary lexical handling for all text.
func queryLiterals(query string) []string {
	var literals []string
	for len(literals) < 8 {
		start := strings.IndexByte(query, '"')
		if start < 0 {
			break
		}
		query = query[start+1:]
		end := strings.IndexByte(query, '"')
		if end < 0 {
			break
		}
		literal := query[:end]
		query = query[end+1:]
		if len(literal) > 256 || strings.TrimSpace(literal) == "" {
			continue
		}
		duplicate := false
		for _, previous := range literals {
			duplicate = duplicate || previous == literal
		}
		if !duplicate {
			literals = append(literals, literal)
		}
	}
	return literals
}

func hasLiteralRanking(version string) bool {
	return version == "lexical-scope-recency/5" || version == "semantic-scope-recency/2"
}

func matchesLiteral(body string, literals []string) bool {
	for _, literal := range literals {
		if strings.Contains(body, literal) {
			return true
		}
	}
	return false
}

func literalReason(reason string, matched bool) string {
	if matched {
		return "exact quoted text match; " + reason
	}
	return reason
}
