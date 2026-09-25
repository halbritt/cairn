package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"fmt"
	"sort"

	"github.com/jackc/pgx/v5"
)

type VerifyRestoreRequest struct {
	SessionID  string                  `json:"session_id"`
	Checkpoint VerifyCheckpointRequest `json:"checkpoint"`
	Recovery   RecoveryRecord          `json:"recovery"`
	Fixtures   []RecompileRequest      `json:"fixtures"`
}

type RestoreFixture struct {
	Selected  int    `json:"selected"`
	ReceiptID string `json:"receipt_id"`
	Seal      string `json:"seal"`
}

type RestoreVerification struct {
	OperationsPolicy       string           `json:"operations_policy"`
	CheckpointSHA256       string           `json:"checkpoint_sha256"`
	ExportID               string           `json:"export_id"`
	SessionID              string           `json:"session_id"`
	RecoverySHA256         string           `json:"recovery_sha256"`
	CheckpointID           string           `json:"checkpoint_id"`
	Problems               []string         `json:"problems"`
	ReappliedMissingEvents []string         `json:"reapplied_missing_events"`
	Fixtures               []RestoreFixture `json:"fixtures"`
	ResidualEffects        int              `json:"residual_effects"`
	Ready                  bool             `json:"ready"`
	Coverage               string           `json:"coverage"`
}

func (s *Store) restoreOwner(ctx context.Context, tx pgx.Tx, sessionID string) error {
	var root, current string
	if err := tx.QueryRow(ctx, `SELECT g.grant_id::text,COALESCE(a.session_id::text,'') FROM cairn.authority_grant g CROSS JOIN cairn.restore_admission a WHERE g.parent_id IS NULL AND a.singleton`).Scan(&root, &current); err != nil {
		return err
	}
	if current != sessionID {
		return failure("STALE_RESTORE", "request does not name the current restore session")
	}
	_, err := s.authorize(ctx, tx, root, "redact", "*")
	return err
}

func (req VerifyRestoreRequest) validate() error {
	if err := validID(req.SessionID); err != nil {
		return err
	}
	if err := validID(req.Checkpoint.CheckpointID); err != nil {
		return err
	}
	if !digestValid(req.Checkpoint.ExpectedDigest) || req.Checkpoint.ExpectedExportID == "" {
		return failure("INVALID_REQUEST", "external backup checkpoint expectation required")
	}
	if len(req.Fixtures) > 100 {
		return failure("INVALID_REQUEST", "at most 100 retained compiler fixtures allowed")
	}
	seen := map[string]bool{}
	for _, f := range req.Fixtures {
		if err := validID(f.ReceiptID); err != nil {
			return err
		}
		if len(f.Query) > 4096 || seen[f.ReceiptID] {
			return failure("INVALID_REQUEST", "fixture queries must be bounded and receipts distinct")
		}
		seen[f.ReceiptID] = true
	}
	return req.Recovery.validate()
}

func (s *Store) VerifyRestore(ctx context.Context, req VerifyRestoreRequest) (RestoreVerification, error) {
	if err := s.checkpointAccess(); err != nil {
		return RestoreVerification{}, err
	}
	if err := req.validate(); err != nil {
		return RestoreVerification{}, err
	}
	ctx = s.recoveryContext(ctx)
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return RestoreVerification{}, err
	}
	defer tx.Rollback(ctx)
	if err = s.restoreOwner(ctx, tx, req.SessionID); err != nil {
		return RestoreVerification{}, err
	}
	result, err := s.verifyRestore(ctx, tx, req)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}

func (s *Store) verifyRestore(ctx context.Context, tx pgx.Tx, req VerifyRestoreRequest) (RestoreVerification, error) {
	result := RestoreVerification{OperationsPolicy: "local-restore/1", CheckpointSHA256: req.Checkpoint.ExpectedDigest, ExportID: req.Checkpoint.ExpectedExportID, SessionID: req.SessionID, RecoverySHA256: req.Recovery.SHA256, CheckpointID: req.Checkpoint.CheckpointID, Problems: []string{}, ReappliedMissingEvents: []string{}, Fixtures: []RestoreFixture{}, Coverage: "Internal schema/state/grant/evidence/deletion checks, explicit backup checkpoint, operator-selected external recovery expectations and supplied retained compiler fixtures. Source freshness and external isolation are operator responsibilities; no claim that unobserved lost commits or unrelated file contents were recovered."}
	var paused bool
	if err := tx.QueryRow(ctx, `SELECT paused FROM cairn.restore_admission WHERE singleton`).Scan(&paused); err != nil {
		return result, err
	}
	if !paused {
		result.Problems = append(result.Problems, "RESTORE_NOT_PAUSED")
	}
	if err := verifyRestoreSchema(ctx, tx); err != nil {
		return result, err
	}
	checks := []struct{ name, query string }{
		{"CURRENT_VERSION_MISMATCH", `SELECT count(*) FROM cairn.memory_record m LEFT JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version WHERE v.record_id IS NULL OR v.version_class<>m.class OR m.current_version<>(SELECT max(version) FROM cairn.record_version WHERE record_id=m.record_id)`},
		{"CURRENT_AUTHORITY_MISMATCH", `SELECT count(*) FROM cairn.memory_record m LEFT JOIN cairn.record_authority a ON a.record_id=m.record_id AND a.version=m.current_version LEFT JOIN cairn.authority_event e USING(event_id) WHERE (m.class IN ('B','C') AND a.event_id IS NULL) OR (m.lifecycle='retracted' AND (e.event_type IS NULL OR e.event_type<>'retract')) OR (m.lifecycle='tombstoned' AND (e.event_type IS NULL OR e.event_type<>'forget')) OR (m.lifecycle='active' AND e.event_type IN ('retract','supersede','forget'))`},
		{"SUPERSESSION_STATE_MISMATCH", `SELECT count(*) FROM cairn.memory_record m LEFT JOIN cairn.record_supersession r USING(record_id) WHERE (m.lifecycle='superseded' AND (r.record_id IS NULL OR r.retired_version<>m.current_version)) OR (r.retired_version=m.current_version AND m.lifecycle<>'superseded')`},
		{"VERSION_AUTHORITY_MISMATCH", `SELECT count(*) FROM cairn.record_authority a JOIN cairn.record_version v USING(record_id,version) LEFT JOIN cairn.authority_event e USING(event_id) WHERE e.event_id IS NULL OR e.subject_id<>a.record_id OR e.resulting_version<>a.version OR e.transaction_id<>v.transaction_id OR e.actor<>v.observed_writer
 OR (e.event_type IN ('promote','correct','supersede') AND v.version_class<>'B')
 OR (e.event_type='issue' AND v.version_class<>'C')
 OR (e.event_type='authorize_scope' AND v.version_class NOT IN ('B','C'))
 OR e.event_type NOT IN ('promote','correct','supersede','issue','authorize_scope','retract','redact','forget')`},
		{"GRANT_STATE_MISMATCH", `SELECT count(*) FROM cairn.authority_grant g LEFT JOIN cairn.authority_event e USING(event_id) LEFT JOIN cairn.authority_grant p ON p.grant_id=g.parent_id WHERE e.event_id IS NULL OR e.subject_id<>g.grant_id OR e.resulting_version<>g.version OR g.revoked<>(e.event_type='revoke_grant') OR e.event_type NOT IN ('bootstrap','grant','revoke_grant') OR (g.parent_id IS NULL AND (g.depth<>0 OR g.repo<>'*')) OR (g.parent_id IS NOT NULL AND (p.grant_id IS NULL OR g.depth<>p.depth+1 OR NOT g.capabilities<@p.capabilities OR (p.repo<>'*' AND g.repo<>p.repo) OR (p.expires_at IS NOT NULL AND (g.expires_at IS NULL OR g.expires_at>p.expires_at))))`},
		{"INSTRUCTION_KIND_MISMATCH", `SELECT count(*) FROM cairn.record_version WHERE version_class='C' AND kind NOT IN ('instruction','policy')`},
		{"B_SUPPORT_MISSING", `SELECT count(*) FROM cairn.memory_record m WHERE m.class='B' AND m.lifecycle='active' AND NOT EXISTS(SELECT 1 FROM cairn.evidence_ref r WHERE r.record_id=m.record_id AND r.version=m.current_version)`},
		{"UNKNOWN_DELETION_RESIDUAL", `SELECT count(*) FROM cairn.deletion_effect WHERE status='not_possible' AND target_type NOT IN ('storage_and_backups','unmanaged_copies','retained_metadata','run_artifacts','provider_delivery','supporting_evidence','related_record')`},
	}
	for _, check := range checks {
		var count int
		if err := tx.QueryRow(ctx, check.query).Scan(&count); err != nil {
			return result, err
		}
		if count > 0 {
			result.Problems = append(result.Problems, fmt.Sprintf("%s:%d", check.name, count))
		}
	}
	// Inline evidence has actual bytes; validate them rather than treating a
	// stored digest/state as an observation. Divergence must be marked first.
	// The repeatable-read snapshot stays fixed across keyset pages. Keep both
	// scanned bytes and operator-facing problem details bounded per page.
	after := ""
	divergences := 0
	for {
		query := `SELECT evidence_id::text,body,digest,state FROM cairn.evidence ORDER BY evidence_id LIMIT 256`
		var args []any
		if after != "" {
			query = `SELECT evidence_id::text,body,digest,state FROM cairn.evidence
 WHERE evidence_id > $1::uuid ORDER BY evidence_id LIMIT 256`
			args = append(args, after)
		}
		rows, err := tx.Query(ctx, query, args...)
		if err != nil {
			return result, err
		}
		count := 0
		for rows.Next() {
			var id, state string
			var body, digest []byte
			if err = rows.Scan(&id, &body, &digest, &state); err != nil {
				rows.Close()
				return result, err
			}
			count++
			after = id
			actual := sha256.Sum256(body)
			if state == "resolvable" && !bytes.Equal(actual[:], digest) {
				divergences++
				if divergences <= 100 {
					result.Problems = append(result.Problems, "EVIDENCE_DIVERGENCE_UNMARKED:"+id)
				}
			}
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return result, err
		}
		if count < 256 {
			break
		}
	}
	if divergences > 100 {
		result.Problems = append(result.Problems, fmt.Sprintf("EVIDENCE_DIVERGENCE_UNMARKED_ADDITIONAL:%d", divergences-100))
	}
	checkpoint, err := verifyCheckpoint(ctx, tx, req.Checkpoint)
	if err != nil {
		return result, err
	}
	if !checkpoint.Valid {
		result.Problems = append(result.Problems, "CHECKPOINT_MISMATCH")
	}
	combined, err := captureRecoveryTx(ctx, tx)
	if err != nil {
		return result, err
	}
	if err = mergeRecovery(&combined, req.Recovery); err != nil {
		return result, err
	}
	combined.SHA256, err = recoveryDigest(combined)
	if err != nil {
		return result, err
	}
	recovery, err := s.inspectRecoveryTx(ctx, tx, combined)
	if err != nil {
		return result, err
	}
	digests := map[string]string{}
	withdrawals := map[string]RecoveryWithdrawal{}
	for _, a := range combined.Audit {
		digests[a.EventID] = a.Digest
	}
	for _, w := range combined.Withdrawals {
		withdrawals[w.EventID] = w
	}
	for _, gap := range recovery.Gaps {
		covered := false
		if w, ok := withdrawals[gap.EventID]; ok && gap.Reason == "AUDIT_MISSING" {
			err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.recovery_application a CROSS JOIN LATERAL jsonb_array_elements(a.actions) action WHERE a.source_root=$1 AND action->>'event_id'=$2 AND action->>'source_digest'=$3 AND action->>'subject_id'=$4 AND action->>'repo'=$5 AND action->>'kind'=$6 AND action->>'outcome' IN ('reapplied','already_restricted','absent'))`, req.Recovery.RootGrantID, w.EventID, digests[w.EventID], w.SubjectID, w.Repo, w.Kind).Scan(&covered)
			if err != nil {
				return result, err
			}
		}
		if covered {
			result.ReappliedMissingEvents = append(result.ReappliedMissingEvents, gap.EventID)
		} else {
			result.Problems = append(result.Problems, gap.Reason+":"+gap.EventID+":"+gap.SubjectID)
		}
	}
	if recovery.OutstandingEffects > 0 {
		result.Problems = append(result.Problems, fmt.Sprintf("OUTSTANDING_EFFECTS:%d", recovery.OutstandingEffects))
	}
	result.ResidualEffects = recovery.ResidualEffects
	for _, fixture := range req.Fixtures {
		pkg, err := s.recompileTx(ctx, tx, fixture)
		if err != nil {
			// SQL/transport failures abort; domain integrity/unavailability failures
			// remain explicit operator-facing refusal evidence.
			if Code(err) == "STORE_ERROR" {
				return result, err
			}
			result.Problems = append(result.Problems, "FIXTURE_"+Code(err)+":"+fixture.ReceiptID)
			continue
		}
		result.Fixtures = append(result.Fixtures, RestoreFixture{ReceiptID: fixture.ReceiptID, Seal: pkg.Seal, Selected: len(pkg.Semantic.Selected)})
	}
	if len(req.Fixtures) == 0 {
		var retained bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.retrieval_receipt WHERE payload_deleted_by IS NULL)`).Scan(&retained); err != nil {
			return result, err
		}
		if retained {
			result.Problems = append(result.Problems, "RETAINED_FIXTURES_REQUIRED")
		}
	}
	positive := false
	for _, fixture := range result.Fixtures {
		positive = positive || fixture.Selected > 0
	}
	if !positive {
		var active bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.memory_record WHERE lifecycle='active')`).Scan(&active); err != nil {
			return result, err
		}
		if active {
			result.Problems = append(result.Problems, "NO_POSITIVE_RETAINED_FIXTURE")
		}
	}
	sort.Strings(result.Problems)
	sort.Strings(result.ReappliedMissingEvents)
	result.Ready = len(result.Problems) == 0
	return result, nil
}

func verifyRestoreSchema(ctx context.Context, tx pgx.Tx) error {
	files, err := schemas.ReadDir("schema")
	if err != nil {
		return err
	}
	var count int
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM public.cairn_migration`).Scan(&count); err != nil {
		return err
	}
	if count != len(files) {
		return failure("SCHEMA_MISMATCH", "restore requires exactly the current migration set")
	}
	for index, file := range files {
		contents, err := schemas.ReadFile("schema/" + file.Name())
		if err != nil {
			return err
		}
		var retained []byte
		if err = tx.QueryRow(ctx, `SELECT digest FROM public.cairn_migration WHERE version=$1`, index+1).Scan(&retained); err != nil {
			return err
		}
		digest := sha256.Sum256(contents)
		if !bytes.Equal(retained, digest[:]) {
			return failure("SCHEMA_MISMATCH", "restored migration checksum differs")
		}
	}
	return nil
}
