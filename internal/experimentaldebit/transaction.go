package experimentaldebit

import (
	"context"
	"errors"
)

type transactionFailure struct {
	Stage           string
	RollbackUnknown bool
}

func (e *transactionFailure) Error() string { return e.Stage }

// Failure exposes only fixed codes and cleanup facts, never storage error text.
type Failure struct {
	Code                                     string
	StorageCleanupUnknown, QuarantineUnknown bool
}

func (e *Failure) Error() string { return e.Code }
func storageCleanup(err error) bool {
	var tx *transactionFailure
	var boundary *Failure
	return (errors.As(err, &tx) && tx.RollbackUnknown) || (errors.As(err, &boundary) && boundary.StorageCleanupUnknown)
}
func operationFailure(code string, cause error) error {
	return &Failure{Code: code, StorageCleanupUnknown: storageCleanup(cause)}
}

type transactionLifecycle interface {
	Commit(context.Context) error
	Rollback(context.Context) error
}

// Used by PostgreSQL transactions and pure lifecycle faults; work owns no retries.
func finishTransaction(ctx context.Context, tx transactionLifecycle, work func() error) (err error) {
	committed := false
	defer func() {
		if recover() != nil {
			err = &transactionFailure{Stage: "transaction_panic_unknown"}
		}
		if !committed {
			if e := safe(func() error { return tx.Rollback(ctx) }); e != nil {
				err = &transactionFailure{Stage: fixed(err), RollbackUnknown: true}
			}
		}
	}()
	if err = work(); err != nil {
		return err
	}
	if e := tx.Commit(ctx); e != nil {
		return &transactionFailure{Stage: "commit_unknown"}
	}
	committed = true
	return nil
}
