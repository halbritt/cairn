package core

import (
	"context"
	"errors"
	"fmt"
	"testing"
)

func TestDraftValidation(t *testing.T) {
	for _, scenario := range []string{"instruction", "empty", "wildcard", "unknown-claim", "bad-attempt", "self-proxy", "long-body"} {
		t.Run(scenario, func(t *testing.T) {
			draft := note()
			switch scenario {
			case "instruction":
				draft.Kind = "instruction"
			case "empty":
				draft.Scope.Repo = ""
			case "wildcard":
				draft.Scope.Repo = "*"
			case "unknown-claim":
				draft.ClaimType = "instrumented"
			case "bad-attempt":
				draft.ClaimType = "completion"
				draft.AttributedProducer = "someone"
				draft.AttemptID = "invalid"
			case "self-proxy":
				draft.AttributedProducer = "someone"
			case "long-body":
				draft.Body = string(make([]byte, 65537))
			}
			requireCode(t, draft.validate(), "INVALID_REQUEST")
		})
	}
}

// An untyped ended-context failure is the retryable TIMEOUT; a typed error keeps
// its own code even when it wraps a context error; other untyped failures stay
// STORE_ERROR.
func TestCodeMapsEndedContextToTimeout(t *testing.T) {
	for _, tc := range []struct {
		err   error
		code  string
		ended bool
	}{
		{context.DeadlineExceeded, "TIMEOUT", true},
		{context.Canceled, "TIMEOUT", true},
		{fmt.Errorf("copy retrieval_candidate: %w", context.DeadlineExceeded), "TIMEOUT", true},
		{&Error{Code: "NOT_FOUND", Message: "m", Cause: context.Canceled}, "NOT_FOUND", true},
		{&Error{Code: "STORE_ERROR", Message: "m"}, "STORE_ERROR", false},
		{errors.New("conn closed"), "STORE_ERROR", false},
	} {
		if got := Code(tc.err); got != tc.code {
			t.Fatalf("Code(%v) = %s, want %s", tc.err, got, tc.code)
		}
		if got := ContextEnded(tc.err); got != tc.ended {
			t.Fatalf("ContextEnded(%v) = %v, want %v", tc.err, got, tc.ended)
		}
	}
}
