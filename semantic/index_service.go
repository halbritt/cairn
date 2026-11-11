package semantic

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/halbritt/cairn/core"
)

// StartIndex keeps index/model availability off the API's startup path. Before
// readiness and on query failure, the compiler returns labelled lexical fallback.
// The returned close cancels workers, joins indexing and active calls, then closes
// the index pool. No detached goroutine or child outlives that close.
func StartIndex(owner context.Context, dsn, path string, onError func(error)) (core.SemanticRetriever, func(), error) {
	return startIndex(owner, dsn, func(ctx context.Context) (*CommandEmbedder, error) {
		return EmbeddingCommand(ctx, path)
	}, onError)
}

// StartRustIndex uses the same asynchronous index owner with an explicitly
// selected Rust embed worker. It does not change schema or readiness policy.
func StartRustIndex(owner context.Context, dsn string, config RustWorkerConfig, onError func(error)) (core.SemanticRetriever, func(), error) {
	return startIndex(owner, dsn, func(ctx context.Context) (*CommandEmbedder, error) {
		return RustEmbeddingCommand(ctx, config)
	}, onError)
}

func startIndex(owner context.Context, dsn string, create func(context.Context) (*CommandEmbedder, error), onError func(error)) (core.SemanticRetriever, func(), error) {
	ctx, cancel := context.WithCancel(owner)
	model, err := create(ctx)
	if err != nil {
		cancel()
		return nil, nil, err
	}
	var mu sync.Mutex
	var active sync.WaitGroup
	var index *Index
	closed := false
	done := make(chan struct{})
	go func() {
		defer close(done)
		for ctx.Err() == nil {
			i, err := OpenIndex(ctx, dsn, model)
			if err == nil {
				mu.Lock()
				index = i
				mu.Unlock()
				i.Run(ctx, onError)
				return
			}
			if onError != nil && ctx.Err() == nil {
				onError(err)
			}
			timer := time.NewTimer(5 * time.Second)
			select {
			case <-ctx.Done():
				timer.Stop()
				return
			case <-timer.C:
			}
		}
	}()
	retrieve := func(call context.Context, request core.SemanticRankRequest) (core.SemanticRetrievalResult, error) {
		mu.Lock()
		i := index
		if closed || i == nil {
			mu.Unlock()
			return core.SemanticRetrievalResult{}, fmt.Errorf("passage index unavailable")
		}
		active.Add(1)
		mu.Unlock()
		defer active.Done()
		call, stop := context.WithTimeout(call, 2*time.Second)
		defer stop()
		return i.Search(call, request)
	}
	var once sync.Once
	closeIndex := func() {
		once.Do(func() {
			mu.Lock()
			closed = true
			mu.Unlock()
			cancel()
			model.Close()
			<-done
			active.Wait()
			mu.Lock()
			i := index
			index = nil
			mu.Unlock()
			if i != nil {
				i.Close()
			}
		})
	}
	return retrieve, closeIndex, nil
}
