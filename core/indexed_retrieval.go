package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"slices"
	"strings"
	"unicode/utf8"
)

// SemanticRetriever searches a host-owned, persistent passage index. It receives
// only currently eligible optional sources. Hits cannot add a source or authority.
// Unlike SemanticRanker, it returns a bounded shortlist instead of scoring every
// body during the query. Missing versions remain eligible for lexical discovery.
type SemanticRetriever func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error)

type SemanticPassageHit struct {
	SemanticScore
	Span ByteSpanRequest `json:"span"`
}

type SemanticRetrievalResult struct {
	ModelSHA256 string
	Algorithm   string
	Indexed     int
	Hits        []SemanticPassageHit
}

type SemanticCoverage struct {
	Indexed  int `json:"indexed"`
	Eligible int `json:"eligible"`
}

func hasHybridRanking(ranking string) bool {
	return ranking == "hybrid-scope-recency/1" || ranking == "hybrid-scope-recency/2" || ranking == "hybrid-scope-recency/3" || ranking == "hybrid-scope-recency/4"
}

func passageDigest(hits []SemanticPassageHit) string {
	hits = slices.Clone(hits)
	slices.SortFunc(hits, func(a, b SemanticPassageHit) int { return strings.Compare(a.RecordID, b.RecordID) })
	b, _ := json.Marshal(hits)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func validPassage(body string, span ByteSpanRequest) bool {
	return span.Offset >= 0 && span.Length > 0 && span.Offset <= len(body) && span.Length <= len(body)-span.Offset && utf8.ValidString(body[span.Offset:span.Offset+span.Length]) && (span.Offset == 0 || utf8.RuneStart(body[span.Offset]))
}

func (s *Store) rankIndexed(ctx context.Context, query string, p *SemanticPackage, candidates []candidate, evaluations map[string]*CandidateEvaluation) ([]candidate, error) {
	p.Schema = "cairn.semantic/15"
	p.Discovery = &DiscoveryRanking{State: "unavailable"}
	notes := []SemanticNote{}
	bodies := map[string]string{}
	for _, c := range candidates {
		if !c.selection.Mandatory {
			r := c.selection.Record
			notes = append(notes, SemanticNote{r.RecordID, r.Version, evaluations[r.RecordID].Facts.BodySHA256, r.Body})
			bodies[r.RecordID] = r.Body
		}
	}
	if len(notes) == 0 {
		p.Discovery.State = "not_needed"
		return candidates, nil
	}
	r, err := s.semanticRetriever(ctx, SemanticRankRequest{query, notes})
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if err != nil {
		return lexicalSemanticFallback(p, candidates, evaluations)
	}
	if r.Indexed == 0 && len(r.Hits) == 0 {
		return lexicalSemanticFallback(p, candidates, evaluations)
	}
	p.Discovery.State = "invalid_result"
	id := &DiscoveryRanking{State: "ready", ModelSHA256: r.ModelSHA256, Algorithm: r.Algorithm}
	valid := semanticIdentityValid(id) && r.Indexed > 0 && r.Indexed <= len(notes) && len(r.Hits) <= min(r.Indexed, 100)
	seen := map[string]bool{}
	for _, h := range r.Hits {
		e := evaluations[h.RecordID]
		body, ok := bodies[h.RecordID]
		if !ok || e == nil || e.Facts == nil || e.Mandatory || seen[h.RecordID] || h.Version != e.Version || h.BodySHA256 != e.Facts.BodySHA256 || h.Score < -1000000 || h.Score > 1000000 || !validPassage(body, h.Span) {
			valid = false
			break
		}
		seen[h.RecordID] = true
	}
	if !valid {
		return lexicalSemanticFallback(p, candidates, evaluations)
	}
	id.ScoresSHA256 = passageDigest(r.Hits)
	id.Coverage = &SemanticCoverage{Indexed: r.Indexed, Eligible: len(notes)}
	p.Discovery = id
	switch {
	case hasEntityRanking(p.Ranking):
		p.Ranking = "hybrid-scope-recency/4"
	case hasFailureRanking(p.Ranking):
		p.Ranking = "hybrid-scope-recency/3"
	case hasLiteralRanking(p.Ranking):
		p.Ranking = "hybrid-scope-recency/2"
	default:
		p.Ranking = "hybrid-scope-recency/1"
	}
	for _, h := range r.Hits {
		hit := h
		evaluations[h.RecordID].PassageHit = &hit
	}
	return rankHybrid(p, candidates, evaluations)
}

// Reciprocal ranks combine bounded lexical and dense lists without treating
// either raw score as confidence. Exact/entity/failure preferences remain the
// outer ordering. Frozen features reproduce this computation without a model.
func rankHybrid(p *SemanticPackage, candidates []candidate, evaluations map[string]*CandidateEvaluation) ([]candidate, error) {
	lexical := []candidate{}
	dense := []SemanticPassageHit{}
	pool := map[string]candidate{}
	for _, c := range candidates {
		if c.selection.Mandatory {
			continue
		}
		e := evaluations[c.selection.Record.RecordID]
		if e.LexicalMatches > 0 {
			lexical = append(lexical, c)
		}
		if e.PassageHit != nil {
			dense = append(dense, *e.PassageHit)
		}
	}
	sortCandidates(lexical)
	slices.SortFunc(dense, func(a, b SemanticPassageHit) int {
		if a.Score != b.Score {
			return b.Score - a.Score
		}
		return strings.Compare(a.RecordID, b.RecordID)
	})
	scores := map[string]int{}
	for i, c := range lexical[:min(len(lexical), 100)] {
		scores[c.selection.Record.RecordID] += 1000000 / (61 + i)
	}
	for i, h := range dense {
		scores[h.RecordID] += 1000000 / (61 + i)
	}
	p.Omitted["NO_RETRIEVAL_MATCH"] = 0
	kept := []candidate{}
	for _, c := range candidates {
		id := c.selection.Record.RecordID
		if !c.selection.Mandatory {
			c.score = scores[id]
		}
		pool[id] = c
		if !c.selection.Mandatory && c.score == 0 && !c.literal && !c.failure && !c.identity {
			evaluations[id].Reason = "NO_RETRIEVAL_MATCH"
			p.Omitted["NO_RETRIEVAL_MATCH"]++
			continue
		}
		kept = append(kept, c)
	}
	if p.AdvisoryConflicts {
		groups, err := frozenAdvisoryGroups(*p, evaluations)
		if err != nil {
			return nil, err
		}
		kept = qualifyAdvisoryCandidates(p, pool, kept, groups, evaluations)
	}
	return kept, nil
}

func passagePreview(body string, span ByteSpanRequest) (string, ByteSpanRequest) {
	end := span.Offset + utf8Prefix(body[span.Offset:span.Offset+span.Length], 154)
	text := body[span.Offset:end]
	if span.Offset > 0 {
		text = "..." + text
	}
	if end < len(body) {
		text += "..."
	}
	return text, ByteSpanRequest{Offset: span.Offset, Length: end - span.Offset}
}

func validateFrozenIndexed(p SemanticPackage, evaluations map[string]*CandidateEvaluation) error {
	invalid := func() error { return failure("INTEGRITY_FAILURE", "historical indexed retrieval metadata is invalid") }
	if p.Schema != "cairn.semantic/15" || p.Discovery == nil || p.Discovery.State != "ready" || !semanticIdentityValid(p.Discovery) || p.Discovery.Coverage == nil {
		return invalid()
	}
	coverage := p.Discovery.Coverage
	eligible := 0
	hits := []SemanticPassageHit{}
	for _, e := range evaluations {
		if e.SemanticScore != nil {
			return invalid()
		}
		if e.Facts == nil || e.Mandatory || e.Reason == "KIND_FILTERED" {
			if e.PassageHit != nil {
				return invalid()
			}
			continue
		}
		eligible++
		if h := e.PassageHit; h != nil {
			if h.RecordID != e.RecordID || h.Version != e.Version || h.BodySHA256 != e.Facts.BodySHA256 || h.Score < -1000000 || h.Score > 1000000 || h.Span.Offset < 0 || h.Span.Length <= 0 {
				return invalid()
			}
			hits = append(hits, *h)
		}
	}
	if coverage.Eligible != eligible || coverage.Indexed <= 0 || coverage.Indexed > eligible || len(hits) > min(coverage.Indexed, 100) || passageDigest(hits) != p.Discovery.ScoresSHA256 {
		return invalid()
	}
	return nil
}
