package semantic

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

const embeddingFrameLimit = 4 * 1024 * 1024

type embeddingLane struct {
	mu     sync.Mutex
	worker *streamWorkerProcess
}

// CommandEmbedder owns separate local processes for interactive queries and
// background indexing. A busy query lane refuses immediately; document work
// never delays a query behind an embedding batch. Close cancels and reaps both.
type CommandEmbedder struct {
	ctx        context.Context
	cancel     context.CancelFunc
	path       string
	query      embeddingLane
	document   embeddingLane
	identityMu sync.Mutex
	identity   EmbeddingIdentity
}

type embeddingReply struct {
	QueryProjection *core.SemanticQueryProjection `json:"query_projection,omitempty"`
	ID              string                        `json:"id"`
	Identity        EmbeddingIdentity             `json:"identity"`
	Vector          []float32                     `json:"vector,omitempty"`
	Passages        []EmbeddedPassage             `json:"passages,omitempty"`
}

func EmbeddingCommand(owner context.Context, path string) (*CommandEmbedder, error) {
	if err := validateCommand(path); err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(owner)
	return &CommandEmbedder{ctx: ctx, cancel: cancel, path: path}, nil
}
func (e *CommandEmbedder) Close() {
	e.cancel()
	for _, lane := range []*embeddingLane{&e.query, &e.document} {
		lane.mu.Lock()
		if lane.worker != nil {
			lane.worker.stop()
			lane.worker = nil
		}
		lane.mu.Unlock()
	}
}
func (e *CommandEmbedder) Identity(ctx context.Context) (EmbeddingIdentity, error) {
	r, err := e.call(ctx, &e.query, "identity", "")
	return r.Identity, err
}
func (e *CommandEmbedder) Query(ctx context.Context, text string) ([]float32, error) {
	vector, _, err := e.QueryWithProjection(ctx, text)
	return vector, err
}
func (e *CommandEmbedder) QueryWithProjection(ctx context.Context, text string) ([]float32, *core.SemanticQueryProjection, error) {
	if len(text) > 4096 {
		return nil, nil, fmt.Errorf("embedding query exceeds limit")
	}
	r, err := e.call(ctx, &e.query, "query", text)
	return r.Vector, r.QueryProjection, err
}
func (e *CommandEmbedder) Document(ctx context.Context, text string) ([]EmbeddedPassage, error) {
	if len(text) > 65536 {
		return nil, fmt.Errorf("embedding document exceeds limit")
	}
	r, err := e.call(ctx, &e.document, "document", text)
	return r.Passages, err
}
func (e *CommandEmbedder) call(ctx context.Context, lane *embeddingLane, operation, text string) (embeddingReply, error) {
	if !lane.mu.TryLock() {
		return embeddingReply{}, fmt.Errorf("embedding worker busy")
	}
	defer lane.mu.Unlock()
	limit := workerTimeout
	if operation == "document" {
		// A maximum-size note can take longer than an interactive exchange.
		// Stay below the index job's two-minute lease, leaving time to publish.
		limit = 90 * time.Second
	}
	ctx, cancel := context.WithTimeout(ctx, limit)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return embeddingReply{}, err
	}
	if err := e.ctx.Err(); err != nil {
		return embeddingReply{}, err
	}
	if lane.worker == nil {
		w, err := startStream(e.path)
		if err != nil {
			return embeddingReply{}, err
		}
		w.reader = bufio.NewReaderSize(w.output, embeddingFrameLimit+1)
		lane.worker = w
	}
	id := uuid.NewString()
	input, err := json.Marshal(struct {
		ID        string `json:"id"`
		Operation string `json:"operation"`
		Text      string `json:"text"`
	}{id, operation, text})
	if err != nil {
		return embeddingReply{}, err
	}
	type response struct {
		reply embeddingReply
		err   error
	}
	done := make(chan response, 1)
	w := lane.worker
	go func() {
		var r embeddingReply
		if _, err := w.input.Write(append(input, '\n')); err != nil {
			done <- response{r, err}
			return
		}
		line, err := w.reader.ReadSlice('\n')
		if err == nil && len(line) > embeddingFrameLimit {
			err = fmt.Errorf("embedding output exceeds limit")
		}
		if err == nil {
			err = decodeOne(bytes.NewReader(line), &r)
		}
		if err == nil && r.ID != id {
			err = fmt.Errorf("embedding response ID mismatch")
		}
		done <- response{r, err}
	}()
	var result response
	select {
	case result = <-done:
	case <-ctx.Done():
		w.stop()
		lane.worker = nil
		<-done
		return embeddingReply{}, ctx.Err()
	case <-e.ctx.Done():
		w.stop()
		lane.worker = nil
		<-done
		return embeddingReply{}, e.ctx.Err()
	}
	if result.err == nil {
		if operation == "query" {
			result.err = core.ValidateSemanticQueryProjection(result.reply.QueryProjection, text)
		} else if result.reply.QueryProjection != nil {
			result.err = fmt.Errorf("query projection on non-query reply")
		}
	}
	if result.err == nil {
		e.identityMu.Lock()
		if e.identity.ModelSHA256 == "" {
			e.identity = result.reply.Identity
		} else if e.identity != result.reply.Identity {
			result.err = fmt.Errorf("embedding worker changed model identity")
		}
		e.identityMu.Unlock()
	}
	if result.err != nil {
		w.stop()
		lane.worker = nil
		return embeddingReply{}, result.err
	}
	return result.reply, nil
}
