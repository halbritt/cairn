package core

import (
	"context"
	"github.com/jackc/pgx/v5"
	"strings"
)

type TaskStateRequest struct {
	RequestID       string `json:"request_id"`
	Scope           Scope  `json:"scope"`
	ExpectedVersion int    `json:"expected_version"`
	State           string `json:"state"`
	Method          string `json:"method"`
}
type TaskState struct {
	Version int    `json:"version"`
	State   string `json:"state"`
}

func (s *Store) ObserveTask(ctx context.Context, req TaskStateRequest) (TaskState, error) {
	if !s.channel.Instrumented {
		return TaskState{}, failure("AUTHORITY_DENIED", "task state requires service observation")
	}
	if err := req.Scope.validate(); err != nil {
		return TaskState{}, err
	}
	if err := s.checkRepo(req.Scope.Repo); err != nil {
		return TaskState{}, err
	}
	if req.Scope.TaskID == "*" || req.Scope.RunID == "*" || req.ExpectedVersion < 0 || strings.TrimSpace(req.Method) == "" || len(req.Method) > 256 {
		return TaskState{}, failure("INVALID_REQUEST", "exact task/run and bounded observation method required")
	}
	if req.State != "completed" && req.State != "cancelled" && req.State != "reopened" {
		return TaskState{}, failure("INVALID_REQUEST", "unknown task state")
	}
	return mutate(ctx, s, "task-state", req.RequestID, req, func(tx pgx.Tx) (TaskState, error) {
		if err := lock(ctx, tx, "task:"+s.channel.Principal+":"+req.Scope.Repo+":"+req.Scope.TaskID); err != nil {
			return TaskState{}, err
		}
		var version int
		if err := tx.QueryRow(ctx, `SELECT COALESCE(max(version),0) FROM cairn.task_state WHERE observer=$1 AND repo=$2 AND task_id=$3`, s.channel.Principal, req.Scope.Repo, req.Scope.TaskID).Scan(&version); err != nil {
			return TaskState{}, err
		}
		if version != req.ExpectedVersion {
			return TaskState{}, failure("VERSION_CONFLICT", "task state version changed")
		}
		_, err := tx.Exec(ctx, `INSERT INTO cairn.task_state(repo,task_id,version,run_id,state,method) VALUES($1,$2,$3,$4,$5,$6)`, req.Scope.Repo, req.Scope.TaskID, version+1, req.Scope.RunID, req.State, req.Method)
		return TaskState{version + 1, req.State}, err
	})
}
