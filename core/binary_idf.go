package core

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"math"
	"slices"
	"strings"
)

// IDF weights are rounded to the nearest integer micro-nat (half away from
// zero). N is bounded by the compiler's 10,000-record scan. This contract uses
// the platform math.Log at construction; the sealed integer weights, not a
// fresh floating-point evaluation, determine historical ordering.
const idfAlgorithm = "idf-ln-micro/1"

type IDFTerm struct {
	Digest string `json:"digest"`
	DF     int    `json:"df"`
	Weight int64  `json:"weight"`
}

type IDFSnapshot struct {
	Algorithm    string    `json:"algorithm"`
	N            int       `json:"n"`
	CohortSHA256 string    `json:"cohort_sha256"`
	Terms        []IDFTerm `json:"terms"`
}

type idfMember struct {
	ID         string   `json:"id"`
	Version    int      `json:"version"`
	BodySHA256 string   `json:"body_sha256"`
	Matches    []string `json:"-"`
}

func hasIDFRanking(r string) bool {
	r = legacyIDFRanking(r)
	return strings.HasPrefix(r, "binary-idf-scope-recency/") && (r == "binary-idf-scope-recency/1" || r == "binary-idf-scope-recency/2" || r == "binary-idf-scope-recency/3" || r == "binary-idf-scope-recency/4")
}

func idfRanking(entities []EntityRef, signature string, literals []string) string {
	version := "1"
	if len(literals) > 0 {
		version = "2"
	}
	if signature != "" {
		version = "3"
	}
	if len(entities) > 0 {
		version = "4"
	}
	return "binary-idf-scope-recency/" + version
}

func makeIDFMember(record Record, words, terms map[string]bool) idfMember {
	matched := make([]string, 0)
	for term := range terms {
		if words[term] {
			matched = append(matched, term)
		}
	}
	slices.Sort(matched)
	digest := sha256.Sum256([]byte(record.Body))
	return idfMember{record.RecordID, record.Version, hex.EncodeToString(digest[:]), matched}
}

func idfWeight(n, df int) (int64, bool) {
	if n < 0 || n > 10000 || df < 0 || df > n {
		return 0, false
	}
	if n == 0 || df == 0 || df == n {
		return 0, true
	}
	w := math.Round(1_000_000 * math.Log(float64(n)/float64(df)))
	if w < 0 || w > 9_210_341 {
		return 0, false
	}
	return int64(w), true
}

func idfDigest(v any) string {
	data, _ := json.Marshal(v) // fixed data structures cannot fail
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func qualifiedIDFMembers(members []idfMember, evaluations map[string]*CandidateEvaluation) []idfMember {
	qualified := make([]idfMember, 0, len(members))
	for _, m := range members {
		if e := evaluations[m.ID]; e != nil && e.Facts != nil {
			qualified = append(qualified, m)
		}
	}
	slices.SortFunc(qualified, func(a, b idfMember) int { return strings.Compare(a.ID, b.ID) })
	return qualified
}

func idfStatistics(query, ranking string, members []idfMember, evaluations map[string]*CandidateEvaluation) (*IDFSnapshot, error) {
	qualified := qualifiedIDFMembers(members, evaluations)
	if len(qualified) > 10000 {
		return nil, failure("BUDGET_REFUSED", "IDF cohort exceeds bounded scan")
	}
	identities := make([]idfMember, len(qualified))
	for i, m := range qualified {
		identities[i] = idfMember{ID: m.ID, Version: m.Version, BodySHA256: m.BodySHA256}
	}
	terms := rankingQueryTerms(query, ranking)
	words := make([]string, 0, len(terms))
	for word := range terms {
		words = append(words, word)
	}
	slices.Sort(words)
	snapshot := &IDFSnapshot{Algorithm: idfAlgorithm, N: len(qualified), CohortSHA256: idfDigest(identities), Terms: make([]IDFTerm, 0, len(words))}
	counts := make(map[string]int, len(words))
	for _, member := range qualified {
		for _, word := range member.Matches {
			counts[word]++
		}
	}
	for _, word := range words {
		df := counts[word]
		sum := sha256.Sum256([]byte(word))
		snapshot.Terms = append(snapshot.Terms, IDFTerm{Digest: hex.EncodeToString(sum[:]), DF: df})
	}
	slices.SortFunc(snapshot.Terms, func(a, b IDFTerm) int { return strings.Compare(a.Digest, b.Digest) })
	return snapshot, nil
}

func buildIDF(query, ranking string, members []idfMember, evaluations map[string]*CandidateEvaluation) (*IDFSnapshot, error) {
	snapshot, err := idfStatistics(query, ranking, members, evaluations)
	if err != nil {
		return nil, err
	}
	for i := range snapshot.Terms {
		weight, ok := idfWeight(snapshot.N, snapshot.Terms[i].DF)
		if !ok {
			return nil, failure("INTEGRITY_FAILURE", "IDF weight is outside bounds")
		}
		snapshot.Terms[i].Weight = weight
	}
	return snapshot, nil
}

func idfScores(snapshot *IDFSnapshot, members []idfMember) map[string]int64 {
	weights := make(map[string]int64, len(snapshot.Terms))
	for _, term := range snapshot.Terms {
		weights[term.Digest] = term.Weight
	}
	scores := make(map[string]int64, len(members))
	for _, m := range members {
		var score int64
		for _, word := range m.Matches {
			sum := sha256.Sum256([]byte(word))
			score += weights[hex.EncodeToString(sum[:])]
		}
		scores[m.ID] = score
	}
	return scores
}

func applyIDF(candidates []candidate, snapshot *IDFSnapshot, members []idfMember, evaluations map[string]*CandidateEvaluation) {
	scores := idfScores(snapshot, qualifiedIDFMembers(members, evaluations))
	for id, score := range scores {
		value := score
		evaluations[id].IDFScore = &value
	}
	for i := range candidates {
		candidates[i].idf = true
		candidates[i].weighted = scores[candidates[i].selection.Record.RecordID]
	}
}

func verifyFrozenIDF(sealed *IDFSnapshot, query, ranking string, members []idfMember, evaluations map[string]*CandidateEvaluation) error {
	invalid := func() error { return failure("INTEGRITY_FAILURE", "historical IDF cohort or scoring changed") }
	if sealed == nil || sealed.Algorithm != idfAlgorithm || sealed.N < 0 || sealed.N > 10000 || len(sealed.Terms) > 4096 {
		return invalid()
	}
	expected, err := idfStatistics(query, ranking, members, evaluations)
	if err != nil || sealed.Algorithm != expected.Algorithm || sealed.N != expected.N || sealed.CohortSHA256 != expected.CohortSHA256 || len(sealed.Terms) != len(expected.Terms) {
		return invalid()
	}
	for i, term := range sealed.Terms {
		want := expected.Terms[i]
		if term.Digest != want.Digest || term.DF != want.DF || term.Weight < 0 || term.Weight > 9_210_341 || ((term.DF == 0 || term.DF == sealed.N) && term.Weight != 0) || (term.DF > 0 && term.DF < sealed.N && term.Weight == 0) {
			return invalid()
		}
	}
	scores := idfScores(sealed, qualifiedIDFMembers(members, evaluations))
	for id, e := range evaluations {
		score, eligible := scores[id]
		if eligible {
			if e.IDFScore == nil || *e.IDFScore != score {
				return invalid()
			}
		} else if e.IDFScore != nil {
			return invalid()
		}
	}
	return nil
}
