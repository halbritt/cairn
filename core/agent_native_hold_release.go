package core

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

type NativeHoldReleaseRequest struct {
	RequestID              string `json:"request_id"`
	Repo                   string `json:"repo"`
	AttemptID              string `json:"attempt_id"`
	Reason                 string `json:"reason"`
	AcceptUncertainEffects bool   `json:"accept_uncertain_effects"`
}

type NativeHoldRelease struct {
	AttemptID     string          `json:"attempt_id"`
	Session       AgentSessionRef `json:"session"`
	DeliveryID    string          `json:"delivery_id"`
	DeliveryState string          `json:"delivery_state"`
	Code          string          `json:"code,omitempty"`
	FinishedAt    time.Time       `json:"finished_at"`
	Reason        string          `json:"reason"`
	ReleasedBy    string          `json:"released_by"`
	ReleaseReason string          `json:"release_reason"`
}

// ReleaseNativeHold closes the hold of a native host that never returned. It
// requires the local unscoped operator channel and a session that is offline or
// stopped. Offline presence does not prove the host stopped, so the operator
// must accept that the work may have run and may still be running; a returning
// host's late completion is then refused with HOLD_RELEASED. Completion already
// recorded before the release is kept, and the hold is simply closed.
func (s *Store) ReleaseNativeHold(ctx context.Context, req NativeHoldReleaseRequest) (NativeHoldRelease, error) {
	var out NativeHoldRelease
	if !s.channel.Operator || s.channel.Repo != "" {
		return out, failure("AUTHORITY_DENIED", "native hold release requires an unscoped operator channel")
	}
	if err := validID(req.RequestID); err != nil {
		return out, err
	}
	if err := validID(req.AttemptID); err != nil {
		return out, err
	}
	if strings.TrimSpace(req.Repo) == "" || req.Repo == "*" || len(req.Repo) > 256 {
		return out, failure("INVALID_REQUEST", "event collection repository required")
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return out, err
	}
	if err := reasonValid(req.Reason); err != nil {
		return out, err
	}
	if !req.AcceptUncertainEffects {
		return out, failure("INVALID_REQUEST", "the host may still be running this work; release requires accept_uncertain_effects=true")
	}
	return mutate(ctx, s, "native-hold-release", req.RequestID, req, func(tx pgx.Tx) (NativeHoldRelease, error) {
		// Host reconciliation takes the same attempt lock, then the delivery
		// row; completion locks the delivery row. One of them commits first.
		if err := lock(ctx, tx, "session-attempt:"+req.AttemptID); err != nil {
			return out, err
		}
		var repo string
		var finished *time.Time
		var live bool
		err := tx.QueryRow(ctx, `SELECT n.agent_id::text,n.execution_id::text,n.delivery_id::text,n.finished_at,a.repo,
 NOT a.stopped AND a.expires_at>clock_timestamp()
 FROM cairn.agent_session_attempt n JOIN cairn.agent_session a USING(agent_id) WHERE n.attempt_id=$1 FOR UPDATE OF n`, req.AttemptID).Scan(
			&out.Session.AgentID, &out.Session.ExecutionID, &out.DeliveryID, &finished, &repo, &live)
		if errors.Is(err, pgx.ErrNoRows) {
			return out, failure("NOT_FOUND", "native attempt not found")
		}
		if err != nil {
			return out, err
		}
		if repo != req.Repo {
			return out, failure("INVALID_REQUEST", "native attempt does not belong to the specified repository")
		}
		if finished != nil {
			return out, failure("VERSION_CONFLICT", "native attempt is already closed")
		}
		if live {
			return out, failure("SESSION_LIVE", "the session is still present; its host must reconcile this attempt")
		}
		if err = tx.QueryRow(ctx, `SELECT state FROM cairn.agent_delivery WHERE delivery_id=$1 FOR UPDATE`, out.DeliveryID).Scan(&out.DeliveryState); err != nil {
			return out, err
		}
		if out.DeliveryState == "pending" || out.DeliveryState == "leased" {
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='failed',lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code='operator_released',control_at=clock_timestamp(),control_by=$2,control_reason=$3 WHERE delivery_id=$1`, out.DeliveryID, s.channel.Principal, req.Reason); err != nil {
				return out, err
			}
		}
		if err = tx.QueryRow(ctx, `UPDATE cairn.agent_session_attempt SET finished_at=clock_timestamp(),reason='operator_released',released_by=$2,release_reason=$3 WHERE attempt_id=$1 RETURNING finished_at,reason,released_by,release_reason`, req.AttemptID, s.channel.Principal, req.Reason).Scan(&out.FinishedAt, &out.Reason, &out.ReleasedBy, &out.ReleaseReason); err != nil {
			return out, err
		}
		out.AttemptID = req.AttemptID
		err = tx.QueryRow(ctx, `SELECT state,code FROM cairn.agent_delivery WHERE delivery_id=$1`, out.DeliveryID).Scan(&out.DeliveryState, &out.Code)
		return out, err
	})
}
