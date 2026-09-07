package core

import (
	"context"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type PromoteRequest struct {
	RequestID       string   `json:"request_id"`
	RecordID        string   `json:"record_id"`
	ExpectedVersion int      `json:"expected_version"`
	GrantID         string   `json:"grant_id"`
	EvidenceIDs     []string `json:"evidence_ids"`
	Reason          string   `json:"reason"`
}
type IssueRequest struct {
	RequestID       string `json:"request_id"`
	Draft           Draft  `json:"draft"`
	GrantID         string `json:"grant_id"`
	Mandatory       bool   `json:"mandatory"`
	RequiresRuntime bool   `json:"requires_runtime"`
	PolicyKey       string `json:"policy_key"`
	Reason          string `json:"reason"`
}
type CorrectRequest struct {
	RequestID       string   `json:"request_id"`
	RecordID        string   `json:"record_id"`
	ExpectedVersion int      `json:"expected_version"`
	GrantID         string   `json:"grant_id"`
	Draft           Draft    `json:"draft"`
	EvidenceIDs     []string `json:"evidence_ids"`
	Reason          string   `json:"reason"`
}
type RetractRequest struct {
	RequestID       string `json:"request_id"`
	RecordID        string `json:"record_id"`
	ExpectedVersion int    `json:"expected_version"`
	GrantID         string `json:"grant_id"`
	Reason          string `json:"reason"`
}

func (s *Store) Promote(ctx context.Context, req PromoteRequest) (Record, error) {
	if err := reasonValid(req.Reason); err != nil {
		return Record{}, err
	}
	if err := validID(req.RecordID); err != nil {
		return Record{}, err
	}
	return privileged(ctx, s, "promote", req.RequestID, req, func(tx pgx.Tx) (Record, error) {
		current, err := readRecord(ctx, tx, req.RecordID)
		if err != nil {
			return Record{}, err
		}
		chain, err := s.authorize(ctx, tx, req.GrantID, "promote", current.Scope.Repo)
		if err != nil {
			return Record{}, err
		}
		current, err = lockRecord(ctx, tx, req.RecordID, req.ExpectedVersion)
		if err != nil {
			return Record{}, err
		}
		if current.Class != "A" || current.Lifecycle != "active" {
			return Record{}, failure("AUTHORITY_DENIED", "promotion requires active A material")
		}
		if current.AttributionState != "self" && current.AttributionState != "reconciled" {
			return Record{}, failure("ATTRIBUTION_"+strings.ToUpper(current.AttributionState), "delegated attribution cannot support promotion")
		}
		var produced bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.record_version WHERE record_id=$1 AND observed_writer=$2)`, req.RecordID, s.channel.Principal).Scan(&produced); err != nil {
			return Record{}, err
		}
		if produced {
			return Record{}, failure("SELF_PROMOTION_DENIED", "a producing or editing principal cannot promote this record")
		}
		next, err := advanceRecord(ctx, tx, current, current.Draft, "B", "active")
		if err != nil {
			return Record{}, err
		}
		if err = linkEvidence(ctx, tx, next, req.EvidenceIDs); err != nil {
			return Record{}, err
		}
		if err = recordAuthority(ctx, tx, "promote", current.Version, next, chain, req.Reason, IssueRequest{}); err != nil {
			return Record{}, err
		}
		return next, nil
	})
}

func (s *Store) Issue(ctx context.Context, req IssueRequest) (Record, error) {
	if err := reasonValid(req.Reason); err != nil {
		return Record{}, err
	}
	if req.Draft.Kind != "instruction" && req.Draft.Kind != "policy" {
		return Record{}, failure("INVALID_REQUEST", "direct authority requires instruction or policy kind")
	}
	ordinary := req.Draft
	ordinary.Kind = "note"
	if err := ordinary.validate(); err != nil {
		return Record{}, err
	}
	if req.Draft.ClaimType != "self" || strings.TrimSpace(req.PolicyKey) == "" || len(req.PolicyKey) > 128 {
		return Record{}, failure("INVALID_REQUEST", "direct authority requires self authoring and a bounded policy_key")
	}
	if req.Draft.Sensitivity == "" {
		req.Draft.Sensitivity = "local"
	}
	if req.Draft.Sensitivity != "local" && req.Draft.Sensitivity != "shareable" {
		return Record{}, failure("INVALID_REQUEST", "unknown sensitivity")
	}
	return privileged(ctx, s, "issue", req.RequestID, req, func(tx pgx.Tx) (Record, error) {
		chain, err := s.authorize(ctx, tx, req.GrantID, "issue", req.Draft.Scope.Repo)
		if err != nil {
			return Record{}, err
		}
		id := uuid.NewString()
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version,class,sensitivity) VALUES($1,1,'C',$2)`, id, req.Draft.Sensitivity); err != nil {
			return Record{}, err
		}
		record, err := insertVersion(ctx, tx, id, 1, req.Draft)
		if err != nil {
			return Record{}, err
		}
		if err = recordAuthority(ctx, tx, "issue", 0, record, chain, req.Reason, req); err != nil {
			return Record{}, err
		}
		if err = openPolicyConflicts(ctx, tx, record, req.PolicyKey); err != nil {
			return Record{}, err
		}
		return record, nil
	})
}

func openPolicyConflicts(ctx context.Context, tx pgx.Tx, record Record, key string) error {
	rows, err := tx.Query(ctx, `SELECT m.record_id::text,m.current_version,a.grant_id::text FROM cairn.memory_record m
	 JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
	 JOIN cairn.record_authority a ON a.record_id=m.record_id AND a.version=m.current_version
	 WHERE m.record_id<>$1 AND m.class='C' AND m.lifecycle='active' AND a.policy_key=$2 AND v.repo=$3 AND v.body<>$4
	 AND (v.task_id='*' OR v.task_id=$5 OR $5='*') AND (v.run_id='*' OR v.run_id=$6 OR $6='*') ORDER BY m.record_id`, record.RecordID, key, record.Scope.Repo, record.Body, record.Scope.TaskID, record.Scope.RunID)
	if err != nil {
		return err
	}
	type member struct {
		ID      string
		Version int
		GrantID string
	}
	members := []member{}
	for rows.Next() {
		var m member
		if err = rows.Scan(&m.ID, &m.Version, &m.GrantID); err != nil {
			rows.Close()
			return err
		}
		members = append(members, m)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for _, m := range members {
		if _, err = grantChain(ctx, tx, m.GrantID, false); Code(err) == "AUTHORITY_DENIED" {
			continue
		}
		if err != nil {
			return err
		}
		id := uuid.NewString()
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.conflict_group(conflict_id,repo,reason) VALUES($1,$2,'Different active instructions overlap on a policy key')`, id, record.Scope.Repo); err != nil {
			return err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.conflict_member(conflict_id,record_id,version) VALUES($1,$2,$3),($1,$4,$5)`, id, record.RecordID, record.Version, m.ID, m.Version); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) Correct(ctx context.Context, req CorrectRequest) (Record, error) {
	if err := reasonValid(req.Reason); err != nil {
		return Record{}, err
	}
	if err := req.Draft.validate(); err != nil {
		return Record{}, err
	}
	if err := validID(req.RecordID); err != nil {
		return Record{}, err
	}
	return privileged(ctx, s, "correct", req.RequestID, req, func(tx pgx.Tx) (Record, error) {
		current, err := readRecord(ctx, tx, req.RecordID)
		if err != nil {
			return Record{}, err
		}
		chain, err := s.authorize(ctx, tx, req.GrantID, "correct", current.Scope.Repo)
		if err != nil {
			return Record{}, err
		}
		current, err = lockRecord(ctx, tx, req.RecordID, req.ExpectedVersion)
		if err != nil {
			return Record{}, err
		}
		if current.Class != "B" || current.Lifecycle != "active" {
			return Record{}, failure("AUTHORITY_DENIED", "correction requires active B; C changes require retraction and a new issuance")
		}
		if current.Scope != req.Draft.Scope || (req.Draft.Sensitivity != "" && current.Sensitivity != req.Draft.Sensitivity) {
			return Record{}, failure("AUTHORITY_DENIED", "correction cannot change scope or sensitivity")
		}
		next, err := advanceRecord(ctx, tx, current, req.Draft, "B", "active")
		if err != nil {
			return Record{}, err
		}
		if next.AttributionState != "self" && next.AttributionState != "reconciled" {
			return Record{}, failure("ATTRIBUTION_UNRECONCILED", "corrected claim attribution must reconcile")
		}
		if err = linkEvidence(ctx, tx, next, req.EvidenceIDs); err != nil {
			return Record{}, err
		}
		if err = recordAuthority(ctx, tx, "correct", current.Version, next, chain, req.Reason, IssueRequest{}); err != nil {
			return Record{}, err
		}
		return next, nil
	})
}

func (s *Store) Retract(ctx context.Context, req RetractRequest) (Record, error) {
	if err := reasonValid(req.Reason); err != nil {
		return Record{}, err
	}
	if err := validID(req.RecordID); err != nil {
		return Record{}, err
	}
	return privileged(ctx, s, "retract", req.RequestID, req, func(tx pgx.Tx) (Record, error) {
		current, err := readRecord(ctx, tx, req.RecordID)
		if err != nil {
			return Record{}, err
		}
		chain, err := s.authorize(ctx, tx, req.GrantID, "retract", current.Scope.Repo)
		if err != nil {
			return Record{}, err
		}
		current, err = lockRecord(ctx, tx, req.RecordID, req.ExpectedVersion)
		if err != nil {
			return Record{}, err
		}
		if current.Lifecycle != "active" {
			return Record{}, failure("VERSION_CONFLICT", "record is already inactive")
		}
		var conflicted bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.conflict_member m JOIN cairn.conflict_group g USING(conflict_id) WHERE m.record_id=$1 AND g.resolved_event IS NULL)`, req.RecordID).Scan(&conflicted); err != nil {
			return Record{}, err
		}
		if conflicted {
			return Record{}, failure("OPEN_CONFLICT", "resolve the conflict before retracting")
		}
		next, err := advanceRecord(ctx, tx, current, current.Draft, current.Class, "retracted")
		if err != nil {
			return Record{}, err
		}
		if err = recordAuthority(ctx, tx, "retract", current.Version, next, chain, req.Reason, IssueRequest{}); err != nil {
			return Record{}, err
		}
		return next, nil
	})
}

func advanceRecord(ctx context.Context, tx pgx.Tx, old Record, draft Draft, class, lifecycle string) (Record, error) {
	_, err := tx.Exec(ctx, `UPDATE cairn.memory_record SET current_version=current_version+1,class=$2,lifecycle=$3 WHERE record_id=$1`, old.RecordID, class, lifecycle)
	if err != nil {
		return Record{}, err
	}
	return insertVersion(ctx, tx, old.RecordID, old.Version+1, draft)
}

func recordAuthority(ctx context.Context, tx pgx.Tx, kind string, previous int, record Record, chain []Grant, reason string, policy IssueRequest) error {
	event, err := audit(ctx, tx, kind, record.RecordID, previous, record.Version, chain, reason)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO cairn.record_authority(record_id,version,event_id,grant_id,mandatory,requires_runtime,policy_key) VALUES($1,$2,$3,$4,$5,$6,$7)`, record.RecordID, record.Version, event, chain[len(chain)-1].ID, policy.Mandatory, policy.RequiresRuntime, policy.PolicyKey)
	if err != nil {
		return err
	}
	if previous > 0 {
		_, err = tx.Exec(ctx, `INSERT INTO cairn.record_correction(record_id,old_version,new_version,event_id) VALUES($1,$2,$3,$4)`, record.RecordID, previous, record.Version, event)
	}
	return err
}
