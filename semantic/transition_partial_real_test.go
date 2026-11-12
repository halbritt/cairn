package semantic

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/halbritt/cairn/core"
)

type transitionPartialCall struct {
	StartedAt      time.Time                    `json:"started_at"`
	CompletedAt    time.Time                    `json:"completed_at"`
	Elapsed        time.Duration                `json:"elapsed_ns"`
	CallerDeadline *time.Time                   `json:"caller_deadline,omitempty"`
	Request        core.SemanticRankRequest     `json:"actual_request"`
	Result         core.SemanticRetrievalResult `json:"actual_result"`
	Error          string                       `json:"actual_error"`
	Deadline       bool                         `json:"is_deadline_exceeded"`
	Canceled       bool                         `json:"is_canceled"`
}

// This observer calls the actual StartIndex retriever and returns its result
// and error untouched. It injects no responses, readiness or worker behavior.
type transitionPartialRecorder struct {
	retrieve core.SemanticRetriever
	mu       sync.Mutex
	calls    []transitionPartialCall
}

func (r *transitionPartialRecorder) Retrieve(ctx context.Context, request core.SemanticRankRequest) (core.SemanticRetrievalResult, error) {
	call := transitionPartialCall{StartedAt: time.Now(), Request: request}
	if deadline, ok := ctx.Deadline(); ok {
		call.CallerDeadline = &deadline
	}
	result, err := r.retrieve(ctx, request)
	call.CompletedAt = time.Now()
	call.Elapsed = call.CompletedAt.Sub(call.StartedAt)
	call.Result, call.Error = result, fmt.Sprint(err)
	call.Deadline, call.Canceled = errors.Is(err, context.DeadlineExceeded), errors.Is(err, context.Canceled)
	r.mu.Lock()
	r.calls = append(r.calls, call)
	r.mu.Unlock()
	return result, err
}

func (r *transitionPartialRecorder) observed() []transitionPartialCall {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]transitionPartialCall{}, r.calls...)
}
