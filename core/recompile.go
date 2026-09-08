package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"github.com/fxamacker/cbor/v2"
	"github.com/jackc/pgx/v5"
	"strings"
)

type RecompileRequest struct {
	ReceiptID string `json:"receipt_id"`
	Query     string `json:"query"`
}

// Recompile reconstructs ranking/packing from the original read set and retained
// versions. Its cutoff is the named receipt, not a PostgreSQL snapshot handle or
// an arbitrary timestamp. It creates no new delivery authorization or exposure.
func (s *Store) Recompile(ctx context.Context, req RecompileRequest) (Package, error) {
	ctx = s.recoveryContext(ctx)
	if len(req.Query) > 4096 {
		return Package{}, failure("INVALID_REQUEST", "query exceeds limit")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return Package{}, err
	}
	defer tx.Rollback(ctx)
	result, err := s.recompileTx(ctx, tx, req)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}
func historicalRecord(ctx context.Context, tx pgx.Tx, e *CandidateEvaluation) (Record, error) {
	var deleted bool
	if err := tx.QueryRow(ctx, `SELECT payload_deleted_by IS NOT NULL FROM cairn.record_version WHERE record_id=$1 AND version=$2`, e.RecordID, e.Version).Scan(&deleted); err != nil && err != pgx.ErrNoRows {
		return Record{}, err
	}
	if deleted {
		return Record{}, failure("PAYLOAD_UNAVAILABLE", "historical candidate payload was excluded by deletion")
	}
	r := Record{RecordID: e.RecordID, Version: e.Version, Class: e.Class, Lifecycle: "active", Sensitivity: e.Facts.Sensitivity, WrittenAt: e.WrittenAt, AttributionState: e.Facts.AttributionState}
	err := tx.QueryRow(ctx, `SELECT kind,body,repo,task_id,run_id,attributed_producer,COALESCE(attempt_id::text,''),result_ref,claim_type,observed_writer,witness FROM cairn.record_version WHERE record_id=$1 AND version=$2`, e.RecordID, e.Version).Scan(&r.Kind, &r.Body, &r.Scope.Repo, &r.Scope.TaskID, &r.Scope.RunID, &r.AttributedProducer, &r.AttemptID, &r.ResultRef, &r.ClaimType, &r.ObservedWriter, &r.Witness)
	if err == pgx.ErrNoRows {
		return r, failure("REPLAY_INCOMPLETE", "historical record version is missing")
	}
	if err != nil {
		return r, err
	}
	r.Relations, err = readRelations(ctx, tx, r.RecordID, r.Version)
	if err != nil {
		return r, err
	}
	r.Draft.Sensitivity = r.Sensitivity
	err = tx.QueryRow(ctx, `SELECT pins FROM cairn.record_applicability WHERE record_id=$1 AND version=$2`, e.RecordID, e.Version).Scan(&r.Pins)
	if err != nil && err != pgx.ErrNoRows {
		return r, err
	}
	return r, nil
}

func (s *Store) recompileTx(ctx context.Context, tx pgx.Tx, req RecompileRequest) (Package, error) {
	var err error
	if err = s.receiptAccess(ctx, tx, req.ReceiptID); err != nil {
		return Package{}, err
	}
	if err = receiptPayloadAvailable(ctx, tx, req.ReceiptID); err != nil {
		return Package{}, err
	}
	var original Package
	original.ReceiptID = req.ReceiptID
	var encoded []byte
	var version int
	if err = tx.QueryRow(ctx, `SELECT semantic_body,seal,nonce::text,explanation_version FROM cairn.retrieval_receipt WHERE receipt_id=$1`, req.ReceiptID).Scan(&encoded, &original.Seal, &original.Nonce, &version); err != nil {
		return Package{}, err
	}
	if version != 2 {
		return Package{}, failure("REPLAY_INCOMPLETE", "receipt lacks frozen candidate facts; saved-byte replay remains available")
	}
	if err = cbor.Unmarshal(encoded, &original.Semantic); err != nil {
		return Package{}, err
	}
	_, storedSeal, err := sealPackage(original.Semantic)
	if err != nil {
		return Package{}, err
	}
	if storedSeal != original.Seal {
		return Package{}, failure("INTEGRITY_FAILURE", "retained semantic bytes do not reproduce their seal")
	}
	digest := sha256.Sum256([]byte(req.Query))
	if original.Semantic.Query != "sha256:"+hex.EncodeToString(digest[:]) {
		return Package{}, failure("INVALID_REQUEST", "query does not match the historical intent digest")
	}
	if (original.Semantic.Schema != "cairn.semantic/3" && original.Semantic.Schema != "cairn.semantic/4" && original.Semantic.Schema != "cairn.semantic/5") || (original.Semantic.Ranking != "lexical-scope-recency/1" && original.Semantic.Ranking != "lexical-scope-recency/2" && original.Semantic.Ranking != "lexical-scope-recency/3" && original.Semantic.Ranking != "lexical-scope-recency/4") {
		return Package{}, failure("REPLAY_INCOMPLETE", "historical compiler version is not supported")
	}
	switch original.Semantic.Policy {
	case "local-loop/1":
		if original.Semantic.PolicyRevision != nil {
			return Package{}, failure("INTEGRITY_FAILURE", "legacy policy cannot carry an explicit revision")
		}
	case "local-loop/2", "local-loop/3":
		pin := original.Semantic.PolicyRevision
		if pin == nil || pin.Rules.validate() != nil || original.Semantic.Policy != pin.Rules.engine() || original.Semantic.OptionalLimit != min(original.Semantic.AvailableTokens*pin.Rules.OptionalPercent/100, pin.Rules.OptionalMaxTokens) {
			return Package{}, failure("INTEGRITY_FAILURE", "historical policy revision or budget is invalid")
		}
	default:
		return Package{}, failure("REPLAY_INCOMPLETE", "historical policy engine is not supported")
	}
	rows, err := tx.Query(ctx, `SELECT detail FROM cairn.retrieval_candidate WHERE receipt_id=$1 ORDER BY record_id,version`, req.ReceiptID)
	if err != nil {
		return Package{}, err
	}
	evaluations := map[string]*CandidateEvaluation{}
	for rows.Next() {
		var e CandidateEvaluation
		if err = rows.Scan(&e); err != nil {
			rows.Close()
			return Package{}, err
		}
		evaluations[e.RecordID] = &e
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return Package{}, err
	}
	p := original.Semantic
	p.Status = "READY"
	p.Selected = []Selection{}
	p.Omitted = omissionCensus()
	candidates := []candidate{}
	terms := rankingTerms(req.Query, p.Ranking)
	for _, e := range evaluations {
		if e.Facts == nil {
			switch e.Reason {
			case "CLASS_NOT_CONSEQUENTIAL", "AUTHORITY_INACTIVE", "SCOPE_AUTHORITY_INACTIVE", "POLICY_UNENFORCEABLE", "OPEN_CONFLICT", "ATTRIBUTION_UNRECONCILED", "EVIDENCE_UNAVAILABLE", "CONTEXT_MISSING", "CURRENTNESS_MISMATCH", "OUTSIDE_VALIDITY":
				p.Omitted[e.Reason]++
				continue
			default:
				return Package{}, failure("REPLAY_INCOMPLETE", "eligible candidate facts are missing")
			}
		}
		record, err := historicalRecord(ctx, tx, e)
		if err != nil {
			return Package{}, err
		}
		bodyDigest := sha256.Sum256([]byte(record.Body))
		if hex.EncodeToString(bodyDigest[:]) != e.Facts.BodySHA256 {
			return Package{}, failure("INTEGRITY_FAILURE", "historical candidate bytes changed")
		}
		words := rankingTerms(record.Body, p.Ranking)
		score := 0
		for word := range terms {
			if words[word] {
				score++
			}
		}
		specificity := 0
		if record.Scope.TaskID != "*" {
			specificity++
		}
		if record.Scope.RunID != "*" {
			specificity++
		}
		if score != e.LexicalMatches || specificity != e.ScopeSpecificity {
			return Package{}, failure("INTEGRITY_FAILURE", "historical ranking features changed")
		}
		selection := Selection{Category: e.Facts.Category, Record: record, Evidence: e.Facts.Evidence, Authority: e.Facts.Authority, Mandatory: e.Mandatory, Reason: fmt.Sprintf("lexical matches=%d; scope specificity=%d", score, specificity)}
		nonemptyQuery := len(terms) > 0
		if p.Ranking == "lexical-scope-recency/2" || p.Ranking == "lexical-scope-recency/3" || p.Ranking == "lexical-scope-recency/4" {
			nonemptyQuery = strings.TrimSpace(req.Query) != ""
		}
		if !selection.Mandatory && nonemptyQuery && score == 0 {
			p.Omitted["NO_LEXICAL_MATCH"]++
			continue
		}
		candidates = append(candidates, candidate{selection, score, specificity})
	}
	if p.Mode == "index" {
		p, err = packIndex(p, candidates, evaluations, req.Query)
	} else {
		p, err = packCandidates(p, candidates, evaluations)
	}
	if err != nil {
		return Package{}, err
	}
	_, seal, err := sealPackage(p)
	if err != nil {
		return Package{}, err
	}
	if seal != original.Seal {
		return Package{}, failure("INTEGRITY_FAILURE", "recompiled historical selection differs from retained seal")
	}
	original.Semantic = p
	return original, nil
}
