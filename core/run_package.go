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
	if err := s.validateRunPackage(req); err != nil {
		return Package{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return Package{}, err
	}
	defer tx.Rollback(ctx)
	pkg, err := s.readRunPackage(ctx, tx, req, dest, "")
	if err != nil {
		return Package{}, err
	}
	return pkg, tx.Commit(ctx)
}

// RunIndex retains the original handles, expiry, reader and remaining shared
// budget. Loading never creates a fresh session or authorizes another launch.
func (s *Store) RunIndex(ctx context.Context, req RunPackageRequest, dest Destination) (IndexResult, error) {
	if err := s.validateRunPackage(req); err != nil {
		return IndexResult{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return IndexResult{}, err
	}
	defer tx.Rollback(ctx)
	pkg, err := s.readRunPackage(ctx, tx, req, dest, "index")
	if err != nil {
		return IndexResult{}, err
	}
	if err = indexSessionCurrent(ctx, tx, req.ReceiptID); err != nil {
		return IndexResult{}, err
	}
	result, err := readIndex(ctx, tx, pkg)
	if err != nil {
		return IndexResult{}, err
	}
	return result, tx.Commit(ctx)
}

func (s *Store) readRunPackage(ctx context.Context, tx pgx.Tx, req RunPackageRequest, dest Destination, mode string) (Package, error) {
	if err := s.receiptAccess(ctx, tx, req.ReceiptID); err != nil {
		return Package{}, err
	}
	if err := receiptDeliveryCurrent(ctx, tx, req.ReceiptID); err != nil {
		return Package{}, err
	}
	if err := receiptPayloadAvailable(ctx, tx, req.ReceiptID); err != nil {
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
	if pkg.Semantic.Mode != mode {
		return Package{}, failure("INVALID_REQUEST", "retained package mode differs from the requested execution mode")
	}
	if err := s.receiptSelectionCurrent(ctx, tx, req.ReceiptID); err != nil {
		return Package{}, err
	}
	return pkg, nil
}

func (s *Store) validateRunPackage(req RunPackageRequest) error {
	if !s.channel.Instrumented {
		return failure("AUTHORITY_DENIED", "retained execution requires an observing host")
	}
	if req.Seal == "" {
		return failure("INVALID_REQUEST", "retained execution requires the expected seal")
	}
	return nil
}
