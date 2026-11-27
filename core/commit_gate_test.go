package core

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"

	"github.com/google/uuid"
)

// A commit gate decides at the commit itself: a refused transaction leaves
// nothing behind, an admitted one is released once after its commit, and a
// transaction under any other context never meets the gate.
func TestCommitGate(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "commit-gate-" + uuid.NewString(), Repo: "fixture:repo"})

	t.Run("a refused commit stores nothing", func(t *testing.T) {
		ended := errors.New("authority ended")
		var asked atomic.Int32
		gated := WithCommitGate(ctx, func() (func(), error) {
			asked.Add(1)
			return nil, ended
		})
		request := uuid.NewString()
		if _, err := s.Create(gated, CreateRequest{RequestID: request, Draft: note()}); !errors.Is(err, ended) {
			t.Fatalf("a refused commit returned %v", err)
		}
		if asked.Load() != 1 {
			t.Fatalf("the gate was asked %d times for one transaction", asked.Load())
		}
		// Nothing was stored, not even the request's receipt: the same
		// request UUID with another body is a new request.
		draft := note()
		draft.Body = "stored after the refusal"
		created, err := s.Create(ctx, CreateRequest{RequestID: request, Draft: draft})
		if err != nil || created.Body != draft.Body {
			t.Fatalf("the request UUID of a refused commit was not free: %+v %v", created, err)
		}
	})

	t.Run("an admitted commit is released once", func(t *testing.T) {
		var asked, released atomic.Int32
		gated := WithCommitGate(ctx, func() (func(), error) {
			asked.Add(1)
			return func() { released.Add(1) }, nil
		})
		created, err := s.Create(gated, CreateRequest{RequestID: uuid.NewString(), Draft: note()})
		if err != nil {
			t.Fatal(err)
		}
		if asked.Load() != 1 || released.Load() != 1 {
			t.Fatalf("gate asked %d times, released %d times", asked.Load(), released.Load())
		}
		if got, err := s.Get(ctx, created.RecordID); err != nil || got.RecordID != created.RecordID {
			t.Fatalf("the admitted record is not stored: %+v %v", got, err)
		}
	})

	t.Run("a transaction that fails before its commit does not meet the gate", func(t *testing.T) {
		var asked atomic.Int32
		gated := WithCommitGate(ctx, func() (func(), error) {
			asked.Add(1)
			return func() {}, nil
		})
		if _, err := s.Create(gated, CreateRequest{RequestID: "not-a-uuid", Draft: note()}); err == nil {
			t.Fatal("an invalid request was stored")
		}
		if asked.Load() != 0 {
			t.Fatalf("the gate was asked %d times for a transaction that did not commit", asked.Load())
		}
	})

	t.Run("a read under the gate also passes it", func(t *testing.T) {
		created, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: note()})
		if err != nil {
			t.Fatal(err)
		}
		ended := errors.New("authority ended")
		gated := WithCommitGate(ctx, func() (func(), error) { return nil, ended })
		// A read that commits its transaction is refused like a write; one
		// that only rolls back answers. Either way nothing is written.
		if _, err := s.Get(gated, created.RecordID); err != nil && !errors.Is(err, ended) {
			t.Fatalf("a gated read failed with %v", err)
		}
	})
}
