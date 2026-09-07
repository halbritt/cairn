package core

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

func (s *Store) RecordSpawn(ctx context.Context, req SpawnRequest) (Attempt, error) {
	if !s.channel.Instrumented {
		return Attempt{}, failure("AUTHORITY_DENIED", "spawn requires a trusted service observation channel")
	}
	if err := validID(req.AttemptID); err != nil {
		return Attempt{}, err
	}
	if err := req.Scope.validate(); err != nil {
		return Attempt{}, err
	}
	if err := s.checkRepo(req.Scope.Repo); err != nil {
		return Attempt{}, err
	}
	for _, principal := range []string{req.Dispatcher, req.Delegate} {
		if strings.TrimSpace(principal) == "" || len(principal) > 256 {
			return Attempt{}, failure("INVALID_REQUEST", "dispatcher and delegate are required")
		}
	}
	return mutate(ctx, s, "spawn", req.RequestID, req, func(tx pgx.Tx) (Attempt, error) {
		if err := lock(ctx, tx, "attempt:"+req.AttemptID); err != nil {
			return Attempt{}, err
		}
		tag, err := tx.Exec(ctx, `INSERT INTO cairn.delegation_attempt(attempt_id,observed_by,dispatcher,delegate,repo,task_id,run_id)
            VALUES($1,current_setting('cairn.caller'),$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING`, req.AttemptID, req.Dispatcher, req.Delegate, req.Scope.Repo, req.Scope.TaskID, req.Scope.RunID)
		if err != nil {
			return Attempt{}, err
		}
		if tag.RowsAffected() != 1 {
			return Attempt{}, failure("VERSION_CONFLICT", "attempt already recorded; retry with original request UUID")
		}
		return Attempt{req.AttemptID, "running"}, nil
	})
}

func (s *Store) RecordTerminal(ctx context.Context, req TerminalRequest) (Attempt, error) {
	if !s.channel.Instrumented {
		return Attempt{}, failure("AUTHORITY_DENIED", "terminal event requires a trusted service observation channel")
	}
	if err := validID(req.AttemptID); err != nil {
		return Attempt{}, err
	}
	switch req.State {
	case "completed", "failed", "timeout", "killed", "no_result":
	default:
		return Attempt{}, failure("INVALID_REQUEST", "unknown terminal state")
	}
	if len(req.ResultRef) > 512 || (req.State == "completed" && strings.TrimSpace(req.ResultRef) == "") {
		return Attempt{}, failure("INVALID_REQUEST", "completion requires a bounded corresponding result reference")
	}
	return mutate(ctx, s, "terminal", req.RequestID, req, func(tx pgx.Tx) (Attempt, error) {
		if err := lock(ctx, tx, "attempt:"+req.AttemptID); err != nil {
			return Attempt{}, err
		}
		var owner, delegate string
		var scope Scope
		var terminal *string
		err := tx.QueryRow(ctx, `SELECT observed_by,delegate,repo,task_id,run_id,terminal_state FROM cairn.delegation_attempt WHERE attempt_id=$1 FOR UPDATE`, req.AttemptID).Scan(&owner, &delegate, &scope.Repo, &scope.TaskID, &scope.RunID, &terminal)
		if errors.Is(err, pgx.ErrNoRows) {
			return Attempt{}, failure("NOT_FOUND", "attempt not found")
		}
		if err != nil {
			return Attempt{}, err
		}
		if err := s.checkRepo(scope.Repo); err != nil {
			return Attempt{}, err
		}
		if owner != s.channel.Principal {
			return Attempt{}, failure("AUTHORITY_DENIED", "only the observing service can finalize this attempt")
		}
		if terminal != nil {
			return Attempt{}, failure("VERSION_CONFLICT", "attempt already terminal")
		}
		_, err = tx.Exec(ctx, `UPDATE cairn.delegation_attempt SET terminal_state=$2,terminal_at=transaction_timestamp(),terminal_observer=current_setting('cairn.caller'),result_ref=$3 WHERE attempt_id=$1`, req.AttemptID, req.State, req.ResultRef)
		if err != nil {
			return Attempt{}, err
		}
		if req.State != "completed" {
			if err = recoverFailure(ctx, tx, req, scope, delegate); err != nil {
				return Attempt{}, err
			}
		}
		if err = queueContradictions(ctx, tx, req.AttemptID); err != nil {
			return Attempt{}, err
		}
		return Attempt{req.AttemptID, req.State}, nil
	})
}

func recoverFailure(ctx context.Context, tx pgx.Tx, req TerminalRequest, scope Scope, delegate string) error {
	id := uuid.NewString()
	_, err := tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version,recovered_attempt_id) VALUES($1,1,$2)`, id, req.AttemptID)
	if err != nil {
		return err
	}
	// Recovery is eager: every observed failed attempt survives even when nobody
	// submits a false completion claim. Payload is service receipt metadata only.
	draft := Draft{Kind: "observation", Body: fmt.Sprintf("Delegate %q terminated %s in attempt %s.", delegate, req.State, req.AttemptID), Scope: scope, ClaimType: "self"}
	_, err = insertVersion(ctx, tx, id, 1, draft)
	return err
}
