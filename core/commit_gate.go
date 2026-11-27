package core

import (
	"context"

	"github.com/jackc/pgx/v5"
)

// CommitGate is asked at the moment a transaction is about to commit whether
// the authority it runs under still stands. It returns a release function,
// which the store calls once the commit has returned, or an error, in which
// case the transaction is rolled back and that error is returned in place of
// the commit's result.
//
// A caller whose authority can end while a request is in flight, such as a
// browser session, uses the gate to make "this may still commit" and "this
// authority has ended" exclude each other: it refuses admission once the
// authority has ended, and it reports the authority as ended only after every
// admitted commit has been released. A cancelled context alone cannot do
// that, because the cancellation and the commit run on different goroutines.
type CommitGate func() (release func(), err error)

type commitGateKey struct{}

// WithCommitGate returns a context under which every store transaction asks
// gate before it commits. Transactions begun under any other context are not
// affected.
func WithCommitGate(ctx context.Context, gate CommitGate) context.Context {
	return context.WithValue(ctx, commitGateKey{}, gate)
}

func commitGateFrom(ctx context.Context) CommitGate {
	gate, _ := ctx.Value(commitGateKey{}).(CommitGate)
	return gate
}

// gatedTx is a transaction whose commit passes the gate of the context it
// was begun under. Savepoints inside it are not gated: only the outer commit
// makes anything durable.
type gatedTx struct {
	pgx.Tx
	gate CommitGate
}

func (t gatedTx) Commit(ctx context.Context) error {
	release, err := t.gate()
	if err != nil {
		_ = t.Tx.Rollback(context.Background())
		return err
	}
	defer release()
	return t.Tx.Commit(ctx)
}
