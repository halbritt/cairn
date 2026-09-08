package core

import (
	"context"

	"github.com/jackc/pgx/v5"
)

type RunPackageRequest struct {
	ReceiptID string `json:"receipt_id"`
	Seal      string `json:"seal"`
}

// RunPackage loads an exact, eligible package for a trusted host. This read
// neither claims a launch nor creates another retrieval or binding.
func (s *Store) RunPackage(ctx context.Context, req RunPackageRequest, dest Destination) (Package, error) {
	if !s.channel.Instrumented {
		return Package{}, failure("AUTHORITY_DENIED", "retained execution requires an observing host")
	}
	if req.Seal == "" {
		return Package{}, failure("INVALID_REQUEST", "retained execution requires the expected seal")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return Package{}, err
	}
	defer tx.Rollback(ctx)
	if err = s.receiptAccess(ctx, tx, req.ReceiptID); err != nil {
		return Package{}, err
	}
	if err = receiptDeliveryCurrent(ctx, tx, req.ReceiptID); err != nil {
		return Package{}, err
	}
	if err = receiptPayloadAvailable(ctx, tx, req.ReceiptID); err != nil {
		return Package{}, err
	}
	pkg, err := readPackage(ctx, tx, req.ReceiptID)
	if err != nil {
		return Package{}, err
	}
	if pkg.Seal != req.Seal {
		return Package{}, failure("INTEGRITY_FAILURE", "retained package differs from the expected seal")
	}
	if pkg.Semantic.Destination != dest {
		return Package{}, failure("AUTHORITY_DENIED", "retained package destination differs from the host profile")
	}
	if pkg.Semantic.Mode != "" {
		return Package{}, failure("INVALID_REQUEST", "retained execution requires a body package")
	}
	if err = s.receiptSelectionCurrent(ctx, tx, req.ReceiptID); err != nil {
		return Package{}, err
	}
	return pkg, tx.Commit(ctx)
}
