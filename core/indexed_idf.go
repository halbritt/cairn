package core

import "fmt"

// Interleaved versions 5–8 change only the ready indexed lexical channel. Their
// outer preference tiers and preview policy remain those of versions 1–4.
func legacyIndexedRanking(ranking string) string {
	spec, ok := ParseRanking(ranking)
	if !ok || !spec.IndexedIDF {
		return ranking
	}
	return spec.withoutCompoundTerms().String()
}
func hasIndexedIDFRanking(ranking string) bool {
	spec, ok := ParseRanking(ranking)
	return ok && spec.IndexedIDF
}

// indexedIDFRanking adds indexed IDF evidence to a freshly resolved interleaved
// identity; any other input yields the plain indexed IDF identity.
func indexedIDFRanking(ranking string) string {
	tier := RankingTierPlain
	if spec, ok := ParseRanking(ranking); ok && spec.Channel == RankingInterleaved && !spec.IndexedIDF {
		tier = spec.Tier
	}
	return rankingSpec(RankingInterleaved, tier).withIndexedIDF().String()
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
