package core

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func memoRequest() SemanticRankRequest {
	return SemanticRankRequest{Query: "fixture", Notes: []SemanticNote{{RecordID: "first", Version: 1, BodySHA256: "hash", Body: "body"}, {RecordID: "second", Version: 2, BodySHA256: "other", Body: "other body"}}}
}

func TestSemanticMemoExactInputAndSingleSlot(t *testing.T) {
	base := memoRequest()
	for _, change := range []struct {
		name string
		edit func(*SemanticRankRequest)
	}{
		{"query", func(r *SemanticRankRequest) { r.Query += " changed" }},
		{"id", func(r *SemanticRankRequest) { r.Notes[0].RecordID += " changed" }},
		{"version", func(r *SemanticRankRequest) { r.Notes[0].Version++ }},
		{"hash", func(r *SemanticRankRequest) { r.Notes[0].BodySHA256 += " changed" }},
		{"body", func(r *SemanticRankRequest) { r.Notes[0].Body += " changed" }},
		{"order", func(r *SemanticRankRequest) { r.Notes[0], r.Notes[1] = r.Notes[1], r.Notes[0] }},
		{"removal", func(r *SemanticRankRequest) { r.Notes = r.Notes[:1] }},
	} {
		t.Run(change.name, func(t *testing.T) {
			var memo semanticMemo
			calls := 0
			provider := func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
				calls++
				return SemanticRetrievalResult{}, nil
			}
			changed := memoRequest()
			change.edit(&changed)
			for _, input := range []SemanticRankRequest{base, base, changed, changed, base} {
				if _, err := memo.retrieve(context.Background(), provider, input); err != nil {
					t.Fatal(err)
				}
			}
			if calls != 3 {
				t.Fatalf("single slot calls=%d, want 3", calls)
			}
		})
	}
	if semanticInputKey('i', SemanticRankRequest{}) == semanticInputKey('i', SemanticRankRequest{Notes: []SemanticNote{}}) {
		t.Fatal("nil and empty provider inputs conflated")
	}
	if semanticInputKey('i', base) == semanticInputKey('r', base) {
		t.Fatal("provider kinds conflated")
	}
}

func TestSemanticMemoOwnsResults(t *testing.T) {
	var memo semanticMemo
	result := SemanticRetrievalResult{Indexed: 1, Hits: []SemanticPassageHit{{SemanticScore: SemanticScore{Score: 7}}}, QueryProjection: &SemanticQueryProjection{Method: "original-prefix/1"}}
	calls := 0
	provider := func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
		calls++
		return result, nil
	}
	first, _ := memo.retrieve(context.Background(), provider, memoRequest())
	first.Hits[0].Score = 9
	result.QueryProjection.Method = "changed"
	second, _ := memo.retrieve(context.Background(), provider, memoRequest())
	if second.Hits[0].Score != 7 || second.QueryProjection.Method != "original-prefix/1" {
		t.Fatal("provider/first caller mutated retained result")
	}
	second.Hits[0].Score = 10
	second.QueryProjection.Method = "changed again"
	third, _ := memo.retrieve(context.Background(), provider, memoRequest())
	if calls != 1 || third.Hits[0].Score != 7 || third.QueryProjection.Method != "original-prefix/1" {
		t.Fatal("cache hit exposed retained storage")
	}
	var rankMemo semanticMemo
	ranked := SemanticRankResult{Scores: []SemanticScore{{Score: 4}}}
	rankerCalls := 0
	ranker := func(context.Context, SemanticRankRequest) (SemanticRankResult, error) {
		rankerCalls++
		return ranked, nil
	}
	a, _ := rankMemo.rank(context.Background(), ranker, memoRequest())
	a.Scores[0].Score = 99
	b, _ := rankMemo.rank(context.Background(), ranker, memoRequest())
	b.Scores[0].Score = 88
	c, _ := rankMemo.rank(context.Background(), ranker, memoRequest())
	if rankerCalls != 1 || c.Scores[0].Score != 4 {
		t.Fatal("rank result storage shared")
	}
}

func TestSemanticMemoEmptySlicesDoNotRetainProviderCapacity(t *testing.T) {
	var memo semanticMemo
	provider := func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
		return SemanticRetrievalResult{Hits: make([]SemanticPassageHit, 0, 1000)}, nil
	}
	_, _ = memo.retrieve(context.Background(), provider, memoRequest())
	result, _ := memo.retrieve(context.Background(), provider, memoRequest())
	if result.Hits == nil || cap(result.Hits) != 0 {
		t.Fatal("empty result retained provider backing array")
	}
	var rankMemo semanticMemo
	ranker := func(context.Context, SemanticRankRequest) (SemanticRankResult, error) {
		return SemanticRankResult{Scores: make([]SemanticScore, 0, 1000)}, nil
	}
	_, _ = rankMemo.rank(context.Background(), ranker, memoRequest())
	ranked, _ := rankMemo.rank(context.Background(), ranker, memoRequest())
	if ranked.Scores == nil || cap(ranked.Scores) != 0 {
		t.Fatal("empty rank result retained provider backing array")
	}
}

func TestSemanticMemoUnavailableAndCancellation(t *testing.T) {
	for _, failure := range []string{"busy", "not ready", "internal deadline", "zero indexed"} {
		t.Run(failure, func(t *testing.T) {
			var memo semanticMemo
			calls := 0
			provider := func(ctx context.Context, _ SemanticRankRequest) (SemanticRetrievalResult, error) {
				calls++
				if failure == "zero indexed" {
					return SemanticRetrievalResult{}, nil
				}
				if failure == "internal deadline" {
					child, cancel := context.WithTimeout(ctx, time.Millisecond)
					defer cancel()
					<-child.Done()
					return SemanticRetrievalResult{}, child.Err()
				}
				return SemanticRetrievalResult{}, errors.New(failure)
			}
			for range 2 {
				_, err := memo.retrieve(context.Background(), provider, memoRequest())
				if (err == nil) != (failure == "zero indexed") {
					t.Fatal("failure disposition changed")
				}
			}
			if calls != 1 {
				t.Fatal("unavailable outcome redispatched")
			}
			ctx, cancel := context.WithCancel(context.Background())
			cancel()
			if _, err := memo.retrieve(ctx, provider, memoRequest()); !errors.Is(err, context.Canceled) {
				t.Fatal("canceled hit accepted")
			}
			if calls != 1 {
				t.Fatal("canceled hit dispatched")
			}
		})
	}
	var memo semanticMemo
	ctx, cancel := context.WithCancel(context.Background())
	calls := 0
	provider := func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
		calls++
		cancel()
		return SemanticRetrievalResult{}, nil
	}
	if _, err := memo.retrieve(ctx, provider, memoRequest()); !errors.Is(err, context.Canceled) {
		t.Fatal("cancellation during dispatch lost")
	}
	if _, err := memo.retrieve(ctx, provider, memoRequest()); !errors.Is(err, context.Canceled) || calls != 1 {
		t.Fatal("canceled dispatch retried")
	}
	// A fresh live context on this unit object must dispatch: canceled work was not retained.
	if _, err := memo.retrieve(context.Background(), provider, memoRequest()); err != nil || calls != 2 {
		t.Fatal("canceled work retained")
	}
}

func TestSemanticMemoOversizedResultsNotRetained(t *testing.T) {
	for _, result := range []SemanticRetrievalResult{
		{Hits: make([]SemanticPassageHit, 101)},
		{ModelSHA256: strings.Repeat("x", 65)}, {Algorithm: strings.Repeat("x", 129)},
		{Hits: []SemanticPassageHit{{SemanticScore: SemanticScore{RecordID: strings.Repeat("x", 37)}}}},
		{Hits: []SemanticPassageHit{{SemanticScore: SemanticScore{BodySHA256: strings.Repeat("x", 65)}}}},
		{QueryProjection: &SemanticQueryProjection{Method: strings.Repeat("x", 65)}},
		{QueryProjection: &SemanticQueryProjection{PrefixSHA256: strings.Repeat("x", 65)}},
	} {
		var memo semanticMemo
		calls := 0
		provider := func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
			calls++
			return result, nil
		}
		for range 2 {
			if _, err := memo.retrieve(context.Background(), provider, memoRequest()); err != nil {
				t.Fatal(err)
			}
		}
		if calls != 2 {
			t.Fatal("oversized result retained")
		}
	}
	for _, result := range []SemanticRankResult{{Scores: make([]SemanticScore, 65)}, {Algorithm: strings.Repeat("x", 129)}, {ModelSHA256: strings.Repeat("x", 65)}, {Scores: []SemanticScore{{RecordID: strings.Repeat("x", 37)}}}, {Scores: []SemanticScore{{BodySHA256: strings.Repeat("x", 65)}}}} {
		var memo semanticMemo
		calls := 0
		provider := func(context.Context, SemanticRankRequest) (SemanticRankResult, error) { calls++; return result, nil }
		for range 2 {
			_, _ = memo.rank(context.Background(), provider, memoRequest())
		}
		if calls != 2 {
			t.Fatal("oversized rank result retained")
		}
	}
}

func TestSemanticMemoRankFailureAndCancellation(t *testing.T) {
	var memo semanticMemo
	calls := 0
	provider := func(context.Context, SemanticRankRequest) (SemanticRankResult, error) {
		calls++
		return SemanticRankResult{}, context.DeadlineExceeded
	}
	for range 2 {
		if _, err := memo.rank(context.Background(), provider, memoRequest()); err == nil {
			t.Fatal("rank failure lost")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := memo.rank(ctx, provider, memoRequest()); !errors.Is(err, context.Canceled) || calls != 1 {
		t.Fatal("rank hit ignored caller cancellation")
	}
	var fresh semanticMemo
	live, stop := context.WithCancel(context.Background())
	canceling := func(context.Context, SemanticRankRequest) (SemanticRankResult, error) {
		stop()
		return SemanticRankResult{}, nil
	}
	if _, err := fresh.rank(live, canceling, memoRequest()); !errors.Is(err, context.Canceled) {
		t.Fatal("rank dispatch cancellation lost")
	}
	if _, err := fresh.rank(context.Background(), provider, memoRequest()); err == nil || calls != 2 {
		t.Fatal("canceled rank outcome retained")
	}
}
