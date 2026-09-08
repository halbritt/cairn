package core

import (
	"context"
	"encoding/json"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type ExpandEvidenceRequest struct {
	ExpectedSHA256 string `json:"expected_sha256"`
	RequestID      string `json:"request_id"`
	ReceiptID      string `json:"receipt_id"`
	Handle         string `json:"handle"`
	EvidenceID     string `json:"evidence_id"`
}

type EvidenceExpansion struct {
	RecordID         string           `json:"record_id"`
	Version          int              `json:"version"`
	Evidence         EvidenceDocument `json:"evidence"`
	CreditsRemaining int              `json:"credits_remaining"`
	BytesRemaining   int              `json:"bytes_remaining"`
}

// ExpandEvidence discloses one captured supporting object through an existing
// index handle. It shares body-pull credits and rechecks authority on retries.
func (s *Store) ExpandEvidence(ctx context.Context, req ExpandEvidenceRequest, dest Destination) (EvidenceExpansion, error) {
	if !digestValid(req.ExpectedSHA256) {
		return EvidenceExpansion{}, failure("INVALID_REQUEST", "expected supporting evidence SHA256 required")
	}
	if err := validID(req.Handle); err != nil {
		return EvidenceExpansion{}, err
	}
	if err := validID(req.EvidenceID); err != nil {
		return EvidenceExpansion{}, err
	}
	var state expansionState
	var doc EvidenceDocument
	guard := func(tx pgx.Tx) error {
		if err := s.prepareExpansion(ctx, tx, ExpandRequest{req.RequestID, req.ReceiptID, req.Handle}, dest, &state); err != nil {
			return err
		}
		var attached *Evidence
		for i := range state.selection.Evidence {
			if state.selection.Evidence[i].ID == req.EvidenceID {
				attached = &state.selection.Evidence[i]
				break
			}
		}
		if attached == nil {
			return failure("EVIDENCE_UNAVAILABLE", "evidence is not attached to the indexed version")
		}
		var err error
		doc, err = s.readEvidenceTx(ctx, tx, req.EvidenceID)
		if err != nil {
			return err
		}
		if doc.Repo != state.selection.Record.Scope.Repo || (!dest.AllowLocal && doc.Sensitivity != "shareable") {
			return failure("DESTINATION_PROHIBITED", "evidence is outside the permitted destination or repository")
		}
		if doc.State != "resolvable" || doc.Digest != attached.Digest || doc.ActualSHA256 != attached.Digest || doc.Digest != req.ExpectedSHA256 {
			return failure("EVIDENCE_UNAVAILABLE", "supporting evidence is unavailable or divergent")
		}
		return nil
	}
	return privileged(ctx, s, "expand-evidence", req.RequestID, struct {
		Request     ExpandEvidenceRequest
		Destination Destination
	}{req, dest}, func(tx pgx.Tx) (EvidenceExpansion, error) {
		result := EvidenceExpansion{RecordID: state.selection.Record.RecordID, Version: state.selection.Record.Version, Evidence: doc, CreditsRemaining: state.credits - 1, BytesRemaining: state.remaining}
		encoded, err := json.Marshal(result)
		if err != nil {
			return EvidenceExpansion{}, err
		}
		cost := len(encoded) + 256
		if state.credits <= 0 || cost > state.remaining {
			return EvidenceExpansion{}, failure("BUDGET_REFUSED", "expansion credits or remaining context bytes exhausted")
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.index_session SET credits=credits-1,remaining_bytes=remaining_bytes-$2 WHERE receipt_id=$1`, req.ReceiptID, cost); err != nil {
			return EvidenceExpansion{}, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.usage_observation(observation_id,receipt_id,record_id,version,signal,witness,method) VALUES($1,$2,$3,$4,'expanded','instrumented','authorized-evidence-pull/1')`, uuid.NewString(), req.ReceiptID, result.RecordID, result.Version); err != nil {
			return EvidenceExpansion{}, err
		}
		result.BytesRemaining -= cost
		return result, nil
	}, guard)
}
