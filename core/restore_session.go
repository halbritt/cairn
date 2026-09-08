package core

import (
	"context"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type BeginRestoreRequest struct {
	RequestID string `json:"request_id"`
	Target    string `json:"target"`
	Reason    string `json:"reason"`
}

type RestoreSession struct {
	SessionID  string `json:"session_id"`
	Generation int64  `json:"generation"`
}

// The key is private: request payloads and external consumers cannot opt out of
// admission. Call sites are limited to administrative recovery and observation
// methods that retain their own identity and access checks.
type recoveryTransactionKey struct{}
type restoreTransitionKey struct{}

func (s *Store) recoveryContext(ctx context.Context) context.Context {
	if s.channel.Operator && s.channel.Repo == "" {
		return context.WithValue(ctx, recoveryTransactionKey{}, true)
	}
	return ctx
}

func restoreAdmission(ctx context.Context, tx pgx.Tx) error {
	var paused bool
	query := `SELECT paused FROM cairn.restore_admission WHERE singleton FOR SHARE`
	if ctx.Value(restoreTransitionKey{}) == true {
		query = `SELECT paused FROM cairn.restore_admission WHERE singleton FOR UPDATE`
	}
	if err := tx.QueryRow(ctx, query).Scan(&paused); err != nil {
		return err
	}
	if paused && ctx.Value(recoveryTransactionKey{}) != true {
		return failure("RESTORE_PAUSED", "restore reconciliation is incomplete; normal service is paused")
	}
	return nil
}

// BeginRestore is called only after external execution is isolated. It drains
// ordinary transactions, fences prior receipts and pauses fresh work atomically.
// It cannot detect a restore performed outside this explicit procedure.
func (s *Store) BeginRestore(ctx context.Context, req BeginRestoreRequest) (RestoreSession, error) {
	if err := s.checkpointAccess(); err != nil {
		return RestoreSession{}, err
	}
	if err := reasonValid(req.Reason); err != nil {
		return RestoreSession{}, err
	}
	if strings.TrimSpace(req.Target) == "" || len(req.Target) > 512 {
		return RestoreSession{}, failure("INVALID_REQUEST", "bounded target restore point required")
	}
	ctx = context.WithValue(s.recoveryContext(ctx), restoreTransitionKey{}, true)
	guard := func(tx pgx.Tx) error {
		var root string
		if err := tx.QueryRow(ctx, `SELECT grant_id::text FROM cairn.authority_grant WHERE parent_id IS NULL`).Scan(&root); err != nil {
			return err
		}
		if _, err := s.authorize(ctx, tx, root, "redact", "*"); err != nil {
			return err
		}
		var cachedCurrent bool
		err := tx.QueryRow(ctx, `SELECT a.paused AND a.session_id::text=m.response->>'session_id'
 FROM cairn.mutation_request m CROSS JOIN cairn.restore_admission a
 WHERE m.caller=$1 AND m.operation='begin-restore' AND m.request_id=$2 AND a.singleton`, s.channel.Principal, req.RequestID).Scan(&cachedCurrent)
		if err == nil && !cachedCurrent {
			return failure("STALE_RESTORE", "begin request belongs to a completed or superseded session; use a fresh request UUID for a new restore")
		}
		if err == pgx.ErrNoRows {
			return nil
		}
		return err
	}
	return privileged(ctx, s, "begin-restore", req.RequestID, req, func(tx pgx.Tx) (RestoreSession, error) {
		result := RestoreSession{SessionID: uuid.NewString()}
		var paused bool
		if err := tx.QueryRow(ctx, `SELECT paused FROM cairn.restore_admission WHERE singleton FOR UPDATE`).Scan(&paused); err != nil {
			return result, err
		}
		if paused {
			return result, failure("RESTORE_IN_PROGRESS", "finish the active restore session before beginning another")
		}
		fence, err := fenceRestore(ctx, tx, req.Reason)
		if err != nil {
			return result, err
		}
		result.Generation = fence.Generation
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.restore_session(session_id,fence_id,root_grant_id,target,reason) SELECT $1,$2,grant_id,$3,$4 FROM cairn.authority_grant WHERE parent_id IS NULL`, result.SessionID, fence.FenceID, req.Target, req.Reason); err != nil {
			return result, err
		}
		_, err = tx.Exec(ctx, `UPDATE cairn.restore_admission SET session_id=$1,paused=true WHERE singleton`, result.SessionID)
		return result, err
	}, guard)
}

type ResumeRestoreRequest struct {
	RequestID    string               `json:"request_id"`
	Verification VerifyRestoreRequest `json:"verification"`
	Policy       string               `json:"policy"`
	Reason       string               `json:"reason"`
}

type RestoreResume struct {
	ResumeID     string              `json:"resume_id"`
	EventID      string              `json:"event_id"`
	Verification RestoreVerification `json:"verification"`
}

// ResumeRestore is an explicit operator decision under local-restore/1, not an
// automatic consequence of a previously green report. Verification reruns while
// the exclusive admission lock excludes ordinary work and recovery mutations.
func (s *Store) ResumeRestore(ctx context.Context, req ResumeRestoreRequest) (RestoreResume, error) {
	if err := s.checkpointAccess(); err != nil {
		return RestoreResume{}, err
	}
	if err := reasonValid(req.Reason); err != nil {
		return RestoreResume{}, err
	}
	if err := req.Verification.validate(); err != nil {
		return RestoreResume{}, err
	}
	if req.Policy != "local-restore/1" {
		return RestoreResume{}, failure("INVALID_REQUEST", "explicit supported restore operations policy required")
	}
	ctx = context.WithValue(s.recoveryContext(ctx), restoreTransitionKey{}, true)
	return privileged(ctx, s, "resume-restore", req.RequestID, req, func(tx pgx.Tx) (RestoreResume, error) {
		result := RestoreResume{ResumeID: uuid.NewString()}
		var err error
		result.Verification, err = s.verifyRestore(ctx, tx, req.Verification)
		if err != nil {
			return result, err
		}
		if !result.Verification.Ready {
			return result, failure("RESTORE_INCOMPLETE", "restore verification failed; service remains paused")
		}
		var root string
		if err = tx.QueryRow(ctx, `SELECT root_grant_id::text FROM cairn.restore_session WHERE session_id=$1`, req.Verification.SessionID).Scan(&root); err != nil {
			return result, err
		}
		chain, err := s.authorize(ctx, tx, root, "redact", "*")
		if err != nil {
			return result, err
		}
		result.EventID, err = audit(ctx, tx, "resume_restore", result.ResumeID, 0, 1, chain, req.Reason)
		if err != nil {
			return result, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.restore_resume(resume_id,session_id,event_id,policy,verification) VALUES($1,$2,$3,$4,$5)`, result.ResumeID, req.Verification.SessionID, result.EventID, req.Policy, result.Verification); err != nil {
			return result, err
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.restore_admission SET paused=false WHERE singleton`); err != nil {
			return result, err
		}
		// Admission metadata must remain capturable under the same recovery bounds.
		if _, err = captureRecoveryTx(ctx, tx); err != nil {
			return result, err
		}
		return result, nil
	}, func(tx pgx.Tx) error { return s.restoreOwner(ctx, tx, req.Verification.SessionID) })
}

type RestoreStatus struct {
	SessionID  string `json:"session_id,omitempty"`
	Paused     bool   `json:"paused"`
	Generation int64  `json:"generation"`
}

func (s *Store) RestoreStatus(ctx context.Context) (RestoreStatus, error) {
	if err := s.checkpointAccess(); err != nil {
		return RestoreStatus{}, err
	}
	ctx = s.recoveryContext(ctx)
	tx, err := s.begin(ctx)
	if err != nil {
		return RestoreStatus{}, err
	}
	defer tx.Rollback(ctx)
	var result RestoreStatus
	err = tx.QueryRow(ctx, `SELECT COALESCE(a.session_id::text,''),a.paused,g.generation FROM cairn.restore_admission a CROSS JOIN cairn.retrieval_generation g WHERE a.singleton AND g.singleton`).Scan(&result.SessionID, &result.Paused, &result.Generation)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}
