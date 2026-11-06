package core

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/advice"
)

// This boundary supplies errors, not a native process. Process termination and
// provider authentication have separate executable fixtures.
type errorReviewAdviceProvider struct {
	workflowParsedAdviceProvider
	err error
}

func (p *errorReviewAdviceProvider) GenerateAdvice(context.Context, *advice.Snapshot, time.Duration, func(advice.ProcessIdentity) error) (*advice.Proposal, *advice.ProcessResult, error) {
	p.calls++
	return nil, nil, p.err
}

func TestWorkAdviceReviewProviderFailureCodeSurvivesStoreAndRetry(t *testing.T) {
	privateCause := errors.New("private provider diagnostic")
	cases := []struct {
		name, code, state string
		err               error
	}{
		{"quota", advice.ErrQuotaExhausted, "failed", advice.NewAdviceError(advice.ErrQuotaExhausted, privateCause.Error(), privateCause)},
		{"timeout", advice.ErrModelTimeout, "failed", advice.NewAdviceError(advice.ErrModelTimeout, "model deadline", context.DeadlineExceeded)},
		{"malformed", advice.ErrInvalidProposal, "failed", advice.NewAdviceError(advice.ErrInvalidProposal, privateCause.Error(), privateCause)},
		{"cleanup", advice.ErrCleanupUnconfirmed, "cleanup_unconfirmed", &advice.CleanupUnconfirmedError{Cause: privateCause}},
		{"launch_gate", "STALE_OBSERVATION", "failed", advice.NewAdviceError(advice.ErrModelExecutionFailed, "onLaunch gate failed", failure("STALE_OBSERVATION", "input changed before launch"))},
		{"unknown", advice.ErrModelExecutionFailed, "failed", privateCause},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			f := newAdviceReviewCase(t, ctx, restoreTestStore(t), "Selected synthetic provider error input")
			provider := &errorReviewAdviceProvider{err: tc.err}
			job := &WorkAdviceJob{BindingID: f.project.BindingID, PlaneItemUUID: f.item, ProfileName: advice.RouteCodexSubscription,
				ModelRoute: "fixture-model", TemplateVersion: 1, RequestUUID: uuid.New()}
			proposal, actions, first := f.store.GenerateWorkAdvice(ctx, job, f.spec, provider)
			if Code(first) != tc.code || proposal != nil || len(actions) != 0 || !errors.Is(first, tc.err) {
				t.Fatalf("fresh failure lost its code/cause or returned output: code=%s error=%v", Code(first), first)
			}
			if tc.name == "cleanup" {
				var cleanup *advice.CleanupUnconfirmedError
				if !errors.As(first, &cleanup) || cleanup != tc.err {
					t.Fatal("Store wrapper lost typed cleanup error")
				}
			}
			if strings.Contains(first.Error(), privateCause.Error()) {
				t.Fatal("Store error message exposed its private cause")
			}
			retained, err := f.store.GetAdviceJob(ctx, job.JobUUID)
			if err != nil || retained.Status != tc.state || retained.ErrorCode != tc.code {
				t.Fatalf("retained outcome differs: job=%+v error=%v", retained, err)
			}
			proposal, actions, retry := f.store.GenerateWorkAdvice(ctx, job, f.spec, provider)
			if Code(retry) != tc.code || proposal != nil || len(actions) != 0 || provider.calls != 1 {
				t.Fatalf("retry changed code or relaunched provider: code=%s calls=%d error=%v", Code(retry), provider.calls, retry)
			}
		})
	}
}
