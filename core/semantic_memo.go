package core

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"io"
	"slices"
	"strings"
)

// One slot owned by one Compile call. Only provider outcomes are retained;
// eligibility, authority, result validation and packing run on every snapshot.
type semanticMemo struct {
	key     [32]byte
	ready   bool
	failed  bool
	indexed SemanticRetrievalResult
	ranked  SemanticRankResult
}

var errSemanticMemoUnavailable = errors.New("semantic provider unavailable during this compile")

func semanticInputKey(kind byte, request SemanticRankRequest) [32]byte {
	h := sha256.New()
	number := func(n uint64) {
		var b [8]byte
		binary.BigEndian.PutUint64(b[:], n)
		h.Write(b[:])
	}
	text := func(s string) { number(uint64(len(s))); io.WriteString(h, s) }
	h.Write([]byte{kind})
	text(request.Query)
	if request.Notes == nil {
		h.Write([]byte{0})
	} else {
		h.Write([]byte{1})
	}
	number(uint64(len(request.Notes)))
	for _, note := range request.Notes {
		text(note.RecordID)
		number(uint64(note.Version))
		text(note.BodySHA256)
		text(note.Body)
	}
	var key [32]byte
	copy(key[:], h.Sum(nil))
	return key
}

func boundedMemoScore(score SemanticScore) bool {
	return len(score.RecordID) <= 36 && len(score.BodySHA256) <= 64
}

// Bounds precede cloning. Malformed oversized output still goes through the
// existing validator, but cannot enlarge the memo or change its disposition.
func boundedMemoIndexed(r SemanticRetrievalResult) bool {
	if len(r.Hits) > 100 || len(r.ModelSHA256) > 64 || len(r.Algorithm) > 128 {
		return false
	}
	if p := r.QueryProjection; p != nil && (len(p.Method) > 64 || len(p.PrefixSHA256) > 64) {
		return false
	}
	for _, hit := range r.Hits {
		if !boundedMemoScore(hit.SemanticScore) {
			return false
		}
	}
	return true
}

func cloneMemoScore(s SemanticScore) SemanticScore {
	s.RecordID = strings.Clone(s.RecordID)
	s.BodySHA256 = strings.Clone(s.BodySHA256)
	return s
}

func cloneMemoRanked(r SemanticRankResult) SemanticRankResult {
	r.ModelSHA256, r.Algorithm = strings.Clone(r.ModelSHA256), strings.Clone(r.Algorithm)
	if len(r.Scores) == 0 && r.Scores != nil {
		r.Scores = []SemanticScore{}
	} else {
		r.Scores = slices.Clone(r.Scores)
	}
	for i := range r.Scores {
		r.Scores[i] = cloneMemoScore(r.Scores[i])
	}
	return r
}

func cloneMemoIndexed(r SemanticRetrievalResult) SemanticRetrievalResult {
	r.ModelSHA256, r.Algorithm = strings.Clone(r.ModelSHA256), strings.Clone(r.Algorithm)
	if len(r.Hits) == 0 && r.Hits != nil {
		r.Hits = []SemanticPassageHit{}
	} else {
		r.Hits = slices.Clone(r.Hits)
	}
	for i := range r.Hits {
		r.Hits[i].SemanticScore = cloneMemoScore(r.Hits[i].SemanticScore)
	}
	if r.QueryProjection != nil {
		p := *r.QueryProjection
		p.Method, p.PrefixSHA256 = strings.Clone(p.Method), strings.Clone(p.PrefixSHA256)
		r.QueryProjection = &p
	}
	return r
}

func (m *semanticMemo) retrieve(ctx context.Context, provider SemanticRetriever, request SemanticRankRequest) (SemanticRetrievalResult, error) {
	if err := ctx.Err(); err != nil {
		return SemanticRetrievalResult{}, err
	}
	key := semanticInputKey('i', request)
	if err := ctx.Err(); err != nil {
		return SemanticRetrievalResult{}, err
	}
	if m.ready && m.key == key {
		if m.failed {
			return SemanticRetrievalResult{}, errSemanticMemoUnavailable
		}
		return cloneMemoIndexed(m.indexed), nil
	}
	*m = semanticMemo{}
	r, err := provider(ctx, request)
	if canceled := ctx.Err(); canceled != nil {
		return SemanticRetrievalResult{}, canceled
	}
	if err != nil {
		*m = semanticMemo{key: key, ready: true, failed: true}
	} else if boundedMemoIndexed(r) {
		*m = semanticMemo{key: key, ready: true, indexed: cloneMemoIndexed(r)}
	}
	return r, err
}

func (m *semanticMemo) rank(ctx context.Context, provider SemanticRanker, request SemanticRankRequest) (SemanticRankResult, error) {
	if err := ctx.Err(); err != nil {
		return SemanticRankResult{}, err
	}
	key := semanticInputKey('r', request)
	if err := ctx.Err(); err != nil {
		return SemanticRankResult{}, err
	}
	if m.ready && m.key == key {
		if m.failed {
			return SemanticRankResult{}, errSemanticMemoUnavailable
		}
		return cloneMemoRanked(m.ranked), nil
	}
	*m = semanticMemo{}
	r, err := provider(ctx, request)
	if canceled := ctx.Err(); canceled != nil {
		return SemanticRankResult{}, canceled
	}
	if err != nil {
		*m = semanticMemo{key: key, ready: true, failed: true}
	} else {
		bounded := len(r.Scores) <= 64 && len(r.ModelSHA256) <= 64 && len(r.Algorithm) <= 128
		if bounded {
			for _, score := range r.Scores {
				if !boundedMemoScore(score) {
					bounded = false
					break
				}
			}
		}
		if bounded {
			retained := cloneMemoRanked(r)
			*m = semanticMemo{key: key, ready: true, ranked: retained}
		}
	}
	return r, err
}
