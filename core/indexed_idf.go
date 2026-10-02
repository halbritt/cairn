package core

import "fmt"

// Versions 5–8 change only the ready indexed lexical channel. Their outer
// preference tiers and preview policy remain those of versions 1–4.
func legacyIndexedRanking(ranking string) string {
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
func hasIndexedIDFRanking(ranking string) bool { return legacyIndexedRanking(ranking) != ranking }
func indexedIDFRanking(ranking string) string {
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

// The ready index's complete optional cohort includes zero-match sources and
// qualified conflict companions. No rejected, private or mandatory body enters
// its document frequencies. Fallback never calls this function.
func indexedIDFMembers(query, ranking string, candidates []candidate, evaluations map[string]*CandidateEvaluation, observe bool) []idfMember {
	lexicon := newRankingLexicon(query, ranking)
	members := make([]idfMember, 0, len(candidates))
	for i := range candidates {
		c := &candidates[i]
		if c.selection.Mandatory {
			continue
		}
		r := c.selection.Record
		words := lexicon.bodyTerms(r.Body)
		members = append(members, makeIDFMember(r, words, lexicon.terms))
		if !observe {
			continue
		}
		count := 0
		for term := range lexicon.terms {
			if words[term] {
				count++
			}
		}
		e := evaluations[r.RecordID]
		e.LexicalMatches = count
		c.score = count
		c.selection.Reason = entityReason(failureReason(literalReason(fmt.Sprintf("lexical matches=%d; scope specificity=%d", count, c.specificity), c.literal), c.failure), e.EntityMatch)
	}
	return members
}
