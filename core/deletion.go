package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"github.com/jackc/pgx/v5/pgconn"
	"sort"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type ForgetRequest struct {
	RequestID       string `json:"request_id"`
	RecordID        string `json:"record_id"`
	ExpectedVersion int    `json:"expected_version"`
	GrantID         string `json:"grant_id"`
	PreviewID       string `json:"preview_id"`
}
type DeletionTarget struct {
	TargetType string `json:"target_type"`
	TargetID   string `json:"target_id"`
	Status     string `json:"status"`
	Residual   string `json:"residual,omitempty"`
}
type DeletionEffect struct {
	DeletionTarget
	Attempts    int        `json:"attempts"`
	LastError   string     `json:"last_error,omitempty"`
	CompletedAt *time.Time `json:"completed_at,omitempty"`
}
type Deletion struct {
	DeletionID string           `json:"deletion_id"`
	RecordID   string           `json:"record_id"`
	EventID    string           `json:"event_id"`
	State      string           `json:"state"`
	Effects    []DeletionEffect `json:"effects"`
}

func (s *Store) PreviewDeletion(ctx context.Context, id string) (RetractionPreview, error) {
	if !s.channel.Operator {
		return RetractionPreview{}, failure("AUTHORITY_DENIED", "deletion inventory requires an operator channel")
	}
	return s.previewRetraction(ctx, id, true)
}

// Forget excludes all retained record bodies and packages that exposed them.
// It does not infer that a related record or evidence object is a copy.
func (s *Store) Forget(ctx context.Context, req ForgetRequest) (Deletion, error) {
	if !s.channel.Operator {
		return Deletion{}, failure("AUTHORITY_DENIED", "forget requires an operator channel")
	}
	if err := validID(req.RecordID); err != nil {
		return Deletion{}, err
	}
	var scope Scope
	result, err := privileged(ctx, s, "forget", req.RequestID, req, func(tx pgx.Tx) (Deletion, error) {
		current, err := readRecord(ctx, tx, req.RecordID)
		if err != nil {
			return Deletion{}, err
		}
		scope = current.Scope
		return s.forgetRecord(ctx, tx, req, current)
	})
	if durablePolicyRefusal(err) {
		err = s.retainRefusal(ctx, req, Refusal{RequestID: req.RequestID, Operation: "forget", Scope: scope, Considered: []RecordVersionRef{{req.RecordID, req.ExpectedVersion}}, TraceComplete: false}, err)
	}
	return result, err
}

func (s *Store) checkDeletionPreview(ctx context.Context, tx pgx.Tx, req ForgetRequest) ([]DeletionTarget, error) {
	if err := s.checkRetractionPreview(ctx, tx, RetractRequest{RecordID: req.RecordID, PreviewID: req.PreviewID}); err != nil {
		return nil, err
	}
	refs, err := dependentVersions(ctx, tx, req.RecordID)
	if err != nil {
		return nil, err
	}
	var conflict bool
	err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.conflict_member m JOIN cairn.conflict_group g USING(conflict_id) JOIN jsonb_to_recordset($1) AS d(record_id uuid,version integer) ON m.record_id=d.record_id AND m.version=d.version WHERE g.resolved_event IS NULL)`, refs).Scan(&conflict)
	if err != nil {
		return nil, err
	}
	if conflict {
		return nil, failure("OPEN_CONFLICT", "resolve the record or dependent conflict before forgetting; emergency conflict redaction is not implemented")
	}
	targets, err := deletionInventory(ctx, tx, req.RecordID, refs)
	if err != nil {
		return nil, err
	}
	actual, err := deletionInventoryDigest(targets)
	if err != nil {
		return nil, err
	}
	var expected []byte
	err = tx.QueryRow(ctx, `SELECT deletion_inventory_digest FROM cairn.retraction_preview WHERE preview_id=$1`, req.PreviewID).Scan(&expected)
	if err != nil {
		return nil, err
	}
	if len(expected) == 0 {
		return nil, failure("IMPACT_PREVIEW_REQUIRED", "obtain a deletion preview with copy inventory")
	}
	if !bytes.Equal(expected, actual) {
		return nil, failure("STALE_PREVIEW", "deletion copy inventory changed; obtain a new preview")
	}
	return targets, nil
}

func deletionInventory(ctx context.Context, tx pgx.Tx, id string, refs []RecordVersionRef) ([]DeletionTarget, error) {
	targets := []DeletionTarget{
		{"db_record_bodies", id, "pending", ""},
		{"db_mutation_responses", id, "pending", ""},
		{"storage_and_backups", id, "not_possible", "SQL purge does not erase PostgreSQL dead tuples, WAL, snapshots or exported backups. Backup inventory and rotation are not yet integrated."},
		{"unmanaged_copies", id, "not_possible", "Previously returned content, user files, exports and unregistered copies cannot be recalled by this store."},
		{"retained_metadata", id, "not_possible", "Scope, attribution, relations, digests, audit reasons and observation metadata remain. This operation purges record bodies and their package copies, not arbitrary sensitive metadata."},
	}
	rows, err := tx.Query(ctx, `SELECT DISTINCT r.receipt_id::text,r.launch_claimed,r.destination,EXISTS(SELECT 1 FROM cairn.managed_context c WHERE c.receipt_id=r.receipt_id) FROM cairn.record_use u JOIN cairn.retrieval_receipt r USING(receipt_id) WHERE u.record_id=$1 ORDER BY r.receipt_id::text LIMIT 1001`, id)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var receipt, destination string
		var claimed, managed bool
		if err = rows.Scan(&receipt, &claimed, &destination, &managed); err != nil {
			rows.Close()
			return nil, err
		}
		targets = append(targets, DeletionTarget{"db_retrieval_package", receipt, "pending", ""})
		if managed {
			targets = append(targets, DeletionTarget{"managed_context", receipt, "pending", ""})
		} else if claimed {
			targets = append(targets, DeletionTarget{"run_artifacts", receipt, "not_possible", "This launch may have a context file or in-process copy; historical run paths are not registered for controlled deletion."})
		}
		if destination == "hosted" {
			targets = append(targets, DeletionTarget{"provider_delivery", receipt, "not_possible", "Context was made available to a hosted channel; provider retention or erasure is not observable here."})
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	rows, err = tx.Query(ctx, `SELECT DISTINCT evidence_id::text FROM cairn.evidence_ref WHERE record_id=$1 ORDER BY evidence_id::text LIMIT 1001`, id)
	if err != nil {
		return nil, err
	}
	evidence, err := pgx.CollectRows(rows, pgx.RowTo[string])
	if err != nil {
		return nil, err
	}
	for _, e := range evidence {
		targets = append(targets, DeletionTarget{"supporting_evidence", e, "not_possible", "Separately captured evidence may contain overlapping content and is not removed by record forgetting."})
	}
	seen := map[string]bool{}
	for _, ref := range refs {
		if ref.RecordID != id && !seen[ref.RecordID] {
			targets = append(targets, DeletionTarget{"related_record", ref.RecordID, "not_possible", "Known dependent record retained; a relation does not establish which bytes are copies. Review its content and support separately."})
			seen[ref.RecordID] = true
		}
	}
	if len(targets) > 1000 {
		return nil, failure("BUDGET_REFUSED", "deletion inventory exceeds 1000 targets; no partial preview authorized")
	}
	sort.Slice(targets, func(i, j int) bool {
		if targets[i].TargetType == targets[j].TargetType {
			return targets[i].TargetID < targets[j].TargetID
		}
		return targets[i].TargetType < targets[j].TargetType
	})
	return targets, nil
}
func deletionInventoryDigest(targets []DeletionTarget) ([]byte, error) {
	encoded, err := json.Marshal(targets)
	if err != nil {
		return nil, err
	}
	digest := sha256.Sum256(encoded)
	return digest[:], nil
}

func excludeDeletionPayloads(ctx context.Context, tx pgx.Tx, id, recordID string) error {
	if _, err := tx.Exec(ctx, `UPDATE cairn.record_version SET payload_deleted_by=$1 WHERE record_id=$2`, id, recordID); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `UPDATE cairn.retrieval_receipt r SET payload_deleted_by=COALESCE(payload_deleted_by,$1) WHERE EXISTS(SELECT 1 FROM cairn.record_use u WHERE u.receipt_id=r.receipt_id AND u.record_id=$2)`, id, recordID); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, `UPDATE cairn.mutation_request SET payload_deleted_by=COALESCE(payload_deleted_by,$1) WHERE operation IN ('create','edit','promote','demote','issue','correct','retract','expand') AND jsonb_path_exists(response,'$.**.record_id ? (@ == $id)',jsonb_build_object('id',$2::text))`, id, recordID)
	return err
}

func receiptPayloadAvailable(ctx context.Context, tx pgx.Tx, id string) error {
	var excluded bool
	if err := tx.QueryRow(ctx, `SELECT payload_deleted_by IS NOT NULL FROM cairn.retrieval_receipt WHERE receipt_id=$1`, id).Scan(&excluded); err != nil {
		return err
	}
	if excluded {
		return failure("PAYLOAD_UNAVAILABLE", "retrieval payload was excluded by deletion; its seal and use references are historical metadata only")
	}
	return nil
}

func (s *Store) DeletionStatus(ctx context.Context, id string) (Deletion, error) {
	if !s.channel.Operator {
		return Deletion{}, failure("AUTHORITY_DENIED", "deletion status requires an operator channel")
	}
	if err := validID(id); err != nil {
		return Deletion{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return Deletion{}, err
	}
	defer tx.Rollback(ctx)
	if err = s.deletionAccess(ctx, tx, id); err != nil {
		return Deletion{}, err
	}
	result, err := readDeletion(ctx, tx, id)
	if err != nil {
		return Deletion{}, err
	}
	return result, tx.Commit(ctx)
}
func (s *Store) deletionAccess(ctx context.Context, tx pgx.Tx, id string) error {
	var repo string
	err := tx.QueryRow(ctx, `SELECT repo FROM cairn.deletion_request WHERE deletion_id=$1`, id).Scan(&repo)
	if err == pgx.ErrNoRows {
		return failure("NOT_FOUND", "deletion request not found")
	}
	if err != nil {
		return err
	}
	return s.checkRepo(repo)
}
func readDeletion(ctx context.Context, tx pgx.Tx, id string) (Deletion, error) {
	d := Deletion{DeletionID: id, State: "completed", Effects: []DeletionEffect{}}
	if err := tx.QueryRow(ctx, `SELECT record_id::text,event_id::text FROM cairn.deletion_request WHERE deletion_id=$1`, id).Scan(&d.RecordID, &d.EventID); err != nil {
		return d, err
	}
	rows, err := tx.Query(ctx, `SELECT target_type,target_id,status,residual,attempts,last_error,completed_at FROM cairn.deletion_effect WHERE deletion_id=$1 ORDER BY target_type,target_id`, id)
	if err != nil {
		return d, err
	}
	defer rows.Close()
	partial, limited := false, false
	for rows.Next() {
		var e DeletionEffect
		if err = rows.Scan(&e.TargetType, &e.TargetID, &e.Status, &e.Residual, &e.Attempts, &e.LastError, &e.CompletedAt); err != nil {
			return d, err
		}
		d.Effects = append(d.Effects, e)
		if e.Status == "not_possible" {
			limited = true
		} else if e.Status != "completed" {
			partial = true
		}
	}
	if partial {
		d.State = "partial"
	} else if limited {
		d.State = "limited"
	}
	return d, rows.Err()
}

// Each database effect and its completion commit together. A crash rolls back
// the current effect; a retry resumes pending work without rerunning completed
// effects. External effects require their own ownership and recovery contracts.
func (s *Store) PurgeDeletion(ctx context.Context, id string) (Deletion, error) {
	status, err := s.DeletionStatus(ctx, id)
	if err != nil {
		return Deletion{}, err
	}
	for _, effect := range status.Effects {
		if effect.TargetType == "managed_context" {
			continue
		}
		if effect.Status != "pending" && effect.Status != "failed" {
			continue
		}
		if err = s.purgeDatabaseEffect(ctx, id, effect); err != nil {
			if observationErr := s.recordPurgeFailure(ctx, id, effect, err); observationErr != nil {
				return Deletion{}, &Error{Code: "PURGE_UNRECORDED", Message: "purge failed and its failure observation could not be retained; inspect deletion-status and retry", Cause: errors.Join(err, observationErr)}
			}
			return Deletion{}, err
		}
	}
	return s.DeletionStatus(ctx, id)
}
func (s *Store) purgeDatabaseEffect(ctx context.Context, id string, effect DeletionEffect) error {
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = s.deletionAccess(ctx, tx, id); err != nil {
		return err
	}
	var status string
	err = tx.QueryRow(ctx, `SELECT status FROM cairn.deletion_effect WHERE deletion_id=$1 AND target_type=$2 AND target_id=$3 FOR UPDATE`, id, effect.TargetType, effect.TargetID).Scan(&status)
	if err != nil {
		return err
	}
	if status != "pending" && status != "failed" {
		return tx.Commit(ctx)
	}
	switch effect.TargetType {
	case "db_record_bodies":
		_, err = tx.Exec(ctx, `UPDATE cairn.record_version SET body='' WHERE record_id=$1 AND payload_deleted_by=$2`, effect.TargetID, id)
	case "db_retrieval_package":
		_, err = tx.Exec(ctx, `UPDATE cairn.retrieval_receipt SET semantic_body=NULL WHERE receipt_id=$1 AND payload_deleted_by IS NOT NULL`, effect.TargetID)
	case "db_mutation_responses":
		_, err = tx.Exec(ctx, `UPDATE cairn.mutation_request SET response=NULL WHERE payload_deleted_by=$1`, id)
	default:
		return failure("INVALID_REQUEST", "no database purge handler for effect type")
	}
	if err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `UPDATE cairn.deletion_effect SET status='completed',attempts=attempts+1,last_error='',completed_at=clock_timestamp() WHERE deletion_id=$1 AND target_type=$2 AND target_id=$3`, id, effect.TargetType, effect.TargetID); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) recordPurgeFailure(ctx context.Context, id string, effect DeletionEffect, cause error) error {
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	code := Code(cause)
	var pgErr *pgconn.PgError
	if errors.As(cause, &pgErr) {
		code = "SQLSTATE_" + pgErr.Code
	}
	var status string
	var attempts int
	if err = tx.QueryRow(ctx, `UPDATE cairn.deletion_effect SET status=CASE WHEN status='completed' THEN status ELSE 'failed' END,attempts=attempts+1,last_error=CASE WHEN status='completed' THEN last_error ELSE $4 END WHERE deletion_id=$1 AND target_type=$2 AND target_id=$3 RETURNING status,attempts`, id, effect.TargetType, effect.TargetID, code).Scan(&status, &attempts); err != nil {
		return err
	}
	// Another worker can finish between rollback and observation. Preserve that
	// completion while still retaining this attempt's failure as an event.
	if status == "completed" {
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.deletion_effect_event(deletion_id,target_type,target_id,status,attempts,error_code) VALUES($1,$2,$3,'failed',$4,$5)`, id, effect.TargetType, effect.TargetID, attempts, code); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}

// forgetRecord runs inside the caller's privileged transaction.
func (s *Store) forgetRecord(ctx context.Context, tx pgx.Tx, req ForgetRequest, current Record) (Deletion, error) {
	chain, err := s.authorize(ctx, tx, req.GrantID, "redact", current.Scope.Repo)
	if err != nil {
		return Deletion{}, err
	}
	current, err = lockRecord(ctx, tx, req.RecordID, req.ExpectedVersion)
	if err != nil {
		return Deletion{}, err
	}
	if current.Lifecycle == "tombstoned" {
		return Deletion{}, failure("VERSION_CONFLICT", "record is already forgotten")
	}
	targets, err := s.checkDeletionPreview(ctx, tx, req)
	if err != nil {
		return Deletion{}, err
	}
	draft := current.Draft
	draft.Body = "[forgotten]"
	draft.Relations = nil
	draft.AttributedProducer = ""
	draft.AttemptID = ""
	draft.ResultRef = ""
	draft.ClaimType = "self"
	next, err := advanceRecord(ctx, tx, current, draft, current.Class, "tombstoned")
	if err != nil {
		return Deletion{}, err
	}
	if err = recordAuthority(ctx, tx, "forget", current.Version, next, chain, "Forget retained record payloads after impact preview", IssueRequest{}); err != nil {
		return Deletion{}, err
	}
	id := uuid.NewString()
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.deletion_request(deletion_id,record_id,event_id,repo) SELECT $1,$2,event_id,$3 FROM cairn.record_authority WHERE record_id=$2 AND version=$4`, id, req.RecordID, current.Scope.Repo, next.Version); err != nil {
		return Deletion{}, err
	}
	for _, target := range targets {
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.deletion_effect(deletion_id,target_type,target_id,status,residual) VALUES($1,$2,$3,$4,$5)`, id, target.TargetType, target.TargetID, target.Status, target.Residual); err != nil {
			return Deletion{}, err
		}
	}
	refs, err := dependentVersions(ctx, tx, req.RecordID)
	if err != nil {
		return Deletion{}, err
	}
	// Relation writers lock and advance their direct target. Touch every
	// affected target so concurrent new descendants either precede this
	// closure or observe its exclusions; old snapshots must retry.
	ids := map[string]bool{}
	for _, ref := range refs {
		if ref.RecordID != req.RecordID {
			ids[ref.RecordID] = true
		}
	}
	ordered := make([]string, 0, len(ids))
	for id := range ids {
		ordered = append(ordered, id)
	}
	sort.Strings(ordered)
	for _, id := range ordered {
		if _, err = tx.Exec(ctx, `UPDATE cairn.memory_record SET use_generation=use_generation+1 WHERE record_id=$1`, id); err != nil {
			return Deletion{}, err
		}
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.deletion_dependency(record_id,version,deletion_id) SELECT record_id,version,$1 FROM jsonb_to_recordset($2) AS d(record_id uuid,version integer) WHERE record_id<>$3::uuid`, id, refs, req.RecordID); err != nil {
		return Deletion{}, err
	}
	if err = excludeDeletionPayloads(ctx, tx, id, req.RecordID); err != nil {
		return Deletion{}, err
	}
	return readDeletion(ctx, tx, id)
}
