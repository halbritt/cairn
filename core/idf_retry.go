package core

import (
	"context"
	"errors"

	"github.com/fxamacker/cbor/v2"
	"github.com/jackc/pgx/v5"
)

// A retry keeps its caller-owned sealed ranking policy. This does not return a
// stored answer: the compiler still recomputes current eligibility and content,
// and commitRetrieval checks intent, current disclosure, payload and seal.
func (s *Store) retainedRetrievalContract(ctx context.Context, tx pgx.Tx, req CompileRequest) (*SemanticPackage, error) {
	var body []byte
	var seal, id string
	err := tx.QueryRow(ctx, `SELECT receipt_id::text,semantic_body,seal FROM cairn.retrieval_receipt WHERE caller=$1 AND request_id=$2`, s.channel.Principal, req.RequestID).Scan(&id, &body, &seal)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if err = receiptDeliveryCurrent(ctx, tx, id); err != nil {
		return nil, err
	}
	if err = receiptPayloadAvailable(ctx, tx, id); err != nil {
		return nil, err
	}
	var semantic SemanticPackage
	if err = cbor.Unmarshal(body, &semantic); err != nil {
		return nil, failure("INTEGRITY_FAILURE", "retained semantic bytes are invalid")
	}
	_, storedSeal, err := sealPackage(semantic)
	if err != nil {
		return nil, err
	}
	if storedSeal != seal {
		return nil, failure("INTEGRITY_FAILURE", "retained semantic bytes do not reproduce their seal")
	}
	return &semantic, nil
}

func (s *Store) retryIDFRanking(ctx context.Context, tx pgx.Tx, req CompileRequest, fresh string) (string, error) {
	retained, err := s.retainedRetrievalContract(ctx, tx, req)
	if err != nil {
		return "", err
	}
	if retained != nil && hasIDFRanking(retained.Ranking) {
		return retained.Ranking, nil
	}
	return fresh, nil
}
