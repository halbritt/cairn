package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"slices"
	"strings"
)

// SemanticRanker is trusted host configuration. Inputs have passed structured
// eligibility; outputs can order those notes, never add candidates or authority.
type SemanticRanker func(context.Context, SemanticRankRequest) (SemanticRankResult, error)

type SemanticNote struct {
	RecordID   string `json:"record_id"`
	Version    int    `json:"version"`
	BodySHA256 string `json:"body_sha256"`
	Body       string `json:"body"`
}
type SemanticRankRequest struct {
	Query string         `json:"query"`
	Notes []SemanticNote `json:"notes"`
}
type SemanticScore struct {
	RecordID   string `json:"record_id"`
	Version    int    `json:"version"`
	BodySHA256 string `json:"body_sha256"`
	Score      int    `json:"score"` // cosine similarity, rounded to millionths; not confidence
}
type SemanticRankResult struct {
	ModelSHA256 string          `json:"model_sha256"`
	Algorithm   string          `json:"algorithm"`
	Scores      []SemanticScore `json:"scores"`
}
type DiscoveryRanking struct {
	State        string `json:"state"`
	ModelSHA256  string `json:"model_sha256,omitempty" cbor:"model_sha256,omitempty"`
	Algorithm    string `json:"algorithm,omitempty" cbor:"algorithm,omitempty"`
	ScoresSHA256 string `json:"scores_sha256,omitempty" cbor:"scores_sha256,omitempty"`
}

func scoreDigest(scores []SemanticScore) string {
	scores = slices.Clone(scores)
	slices.SortFunc(scores, func(a, b SemanticScore) int { return strings.Compare(a.RecordID, b.RecordID) })
	encoded, _ := json.Marshal(scores) // fixed string/integer struct cannot fail
	sum := sha256.Sum256(encoded)
	return hex.EncodeToString(sum[:])
}

func semanticIdentityValid(d *DiscoveryRanking) bool {
	model, err := hex.DecodeString(d.ModelSHA256)
	if err != nil || len(model) != 32 || d.ModelSHA256 != strings.ToLower(d.ModelSHA256) || len(d.Algorithm) == 0 || len(d.Algorithm) > 128 {
		return false
	}
	for _, r := range d.Algorithm {
		if !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '-' || r == '/' || r == '.') {
			return false
		}
	}
	return true
}

func (s *Store) rankSemantic(ctx context.Context, query string, p *SemanticPackage, candidates []candidate, evaluations map[string]*CandidateEvaluation) ([]candidate, error) {
	p.Discovery = &DiscoveryRanking{State: "unavailable"}
	notes := []SemanticNote{}
	bytes := 0
	for _, c := range candidates {
		if c.selection.Mandatory {
			continue
		}
		r := c.selection.Record
		notes = append(notes, SemanticNote{r.RecordID, r.Version, evaluations[r.RecordID].Facts.BodySHA256, r.Body})
		bytes += len(r.Body)
	}
	if len(notes) == 0 {
		p.Discovery.State = "not_needed"
		return candidates, nil
	}
	if s.semanticRanker != nil && len(notes) <= 64 && bytes <= 1024*1024 {
		result, err := s.semanticRanker(ctx, SemanticRankRequest{query, notes})
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if err == nil {
			p.Discovery.State = "invalid_result"
		}
		identity := &DiscoveryRanking{State: "ready", ModelSHA256: result.ModelSHA256, Algorithm: result.Algorithm}
		if err == nil && semanticIdentityValid(identity) && len(result.Scores) == len(notes) {
			scores := map[string]SemanticScore{}
			valid := true
			for _, score := range result.Scores {
				e := evaluations[score.RecordID]
				_, duplicate := scores[score.RecordID]
				if e == nil || e.Facts == nil || e.Mandatory || duplicate || e.Version != score.Version || e.Facts.BodySHA256 != score.BodySHA256 || score.Score < -1000000 || score.Score > 1000000 {
					valid = false
					break
				}
				scores[score.RecordID] = score
			}
			// Every expected optional note must be represented exactly once.
			for _, n := range notes {
				if _, ok := scores[n.RecordID]; !ok {
					valid = false
				}
			}
			if valid {
				identity.ScoresSHA256 = scoreDigest(result.Scores)
				p.Discovery = identity
				p.Ranking = "semantic-scope-recency/1"
				for i := range candidates {
					c := &candidates[i]
					if c.selection.Mandatory {
						continue
					}
					score := scores[c.selection.Record.RecordID].Score
					c.score = score
					evaluations[c.selection.Record.RecordID].SemanticScore = &score
					c.selection.Reason = semanticReason(score, c.specificity)
				}
				return candidates, nil
			}
		}
	}
	// Model errors and bounded-work refusals degrade to the existing lexical
	// route. No partial semantic ordering or unvalidated model result survives.
	kept := candidates[:0]
	for _, c := range candidates {
		if !c.selection.Mandatory && c.score == 0 {
			evaluations[c.selection.Record.RecordID].Reason = "NO_LEXICAL_MATCH"
			p.Omitted["NO_LEXICAL_MATCH"]++
			continue
		}
		kept = append(kept, c)
	}
	return kept, nil
}

func semanticReason(score, specificity int) string {
	return fmt.Sprintf("semantic similarity millionths=%d (not confidence); scope specificity=%d", score, specificity)
}

func discoveryStatus(p SemanticPackage) SemanticPackage {
	if p.Discovery != nil && (p.Discovery.State == "unavailable" || p.Discovery.State == "invalid_result") {
		p.Status = "DEGRADED_NO_EMBEDDINGS"
	}
	return p
}

func validateFrozenDiscovery(p SemanticPackage, evaluations map[string]*CandidateEvaluation, query string) error {
	invalid := func() error { return failure("INTEGRITY_FAILURE", "historical semantic ranking metadata is invalid") }
	if p.Schema != "cairn.semantic/7" && ((p.Schema != "cairn.semantic/8" && p.Schema != "cairn.semantic/9") || p.Discovery == nil) {
		if p.Discovery != nil || p.Ranking == "semantic-scope-recency/1" {
			return invalid()
		}
		for _, e := range evaluations {
			if e.SemanticScore != nil {
				return invalid()
			}
		}
		return nil
	}
	d := p.Discovery
	if d == nil || p.Mode != "index" || p.Purpose != "context" || strings.TrimSpace(query) == "" || p.Browse != nil {
		return invalid()
	}
	if d.State != "ready" {
		if d.State != "unavailable" && d.State != "not_needed" && d.State != "invalid_result" {
			return invalid()
		}
		if *d != (DiscoveryRanking{State: d.State}) || p.Ranking != "lexical-scope-recency/4" {
			return invalid()
		}
		for _, e := range evaluations {
			if e.SemanticScore != nil || (d.State == "not_needed" && e.Facts != nil && !e.Mandatory && e.Reason != "KIND_FILTERED") {
				return invalid()
			}
		}
		return nil
	}
	if p.Ranking != "semantic-scope-recency/1" || !semanticIdentityValid(d) {
		return invalid()
	}
	scores := []SemanticScore{}
	for _, e := range evaluations {
		if e.Facts == nil || e.Mandatory || e.Reason == "KIND_FILTERED" {
			if e.SemanticScore != nil {
				return invalid()
			}
			continue
		}
		if e.SemanticScore == nil || *e.SemanticScore < -1000000 || *e.SemanticScore > 1000000 {
			return invalid()
		}
		scores = append(scores, SemanticScore{e.RecordID, e.Version, e.Facts.BodySHA256, *e.SemanticScore})
	}
	if len(scores) == 0 || len(scores) > 64 || scoreDigest(scores) != d.ScoresSHA256 {
		return invalid()
	}
	return nil
}
