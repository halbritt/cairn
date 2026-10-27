package core

import (
	"context"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// Native cleanup transitions run inside the caller's authenticated mutation and
// existing locks. They do not start transactions, release locks, or reread the
// returned attempt; Store methods retain those responsibilities.

func validToolText(value, field string, max int) error {
	value = strings.TrimSpace(value)
	if value == "" || len(value) > max || strings.ContainsRune(value, 0) {
		return failure("INVALID_REQUEST", "native tool "+field+" must be non-empty bounded text")
	}
	return nil
}

// recordNativeCancellation writes the first operator intent while the attempt
// row is locked. A clear scan from before that intent cannot prove cleanup.
func recordNativeCancellation(ctx context.Context, tx pgx.Tx, attemptID, reason string) (SessionCancel, error) {
	var cancel SessionCancel
	err := tx.QueryRow(ctx, `UPDATE cairn.agent_session_attempt SET cancel_requested_at=clock_timestamp(),cancel_by=current_setting('cairn.caller'),cancel_reason=$2,terminal_scan='' WHERE attempt_id=$1 RETURNING cancel_requested_at,cancel_by,cancel_reason`, attemptID, reason).Scan(&cancel.RequestedAt, &cancel.By, &cancel.Reason)
	return cancel, err
}

func captureNativeCleanupTools(ctx context.Context, tx pgx.Tx, attempt SessionInboxAttempt, req SessionToolCapture) error {
	var err error
	for _, item := range req.Items {
		if err := validToolText(item.ItemID, "item id", 256); err != nil {
			return err
		}
		if err := validToolText(item.ProcessID, "process id", 256); err != nil {
			return err
		}
		if len(item.Command) > 1024 || strings.ContainsRune(item.Command, 0) {
			return failure("INVALID_REQUEST", "native tool command exceeds bounded text")
		}
		if attempt.NativeTurnID != "" && item.NativeTurnID != attempt.NativeTurnID {
			return failure("INVALID_REQUEST", "tool item belongs to a different native turn")
		}
		tag, err := tx.Exec(ctx, `INSERT INTO cairn.agent_session_tool(tool_id,attempt_id,delivery_id,agent_id,native_turn_id,item_id,process_id,command) VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT (attempt_id,item_id) DO UPDATE SET command=EXCLUDED.command WHERE agent_session_tool.stop_state='captured' AND agent_session_tool.process_id=EXCLUDED.process_id`,
			uuid.NewString(), req.AttemptID, attempt.Delivery.DeliveryID, req.Session.AgentID, item.NativeTurnID, item.ItemID, item.ProcessID, item.Command)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			var processID string
			if err = tx.QueryRow(ctx, `SELECT process_id FROM cairn.agent_session_tool WHERE attempt_id=$1 AND item_id=$2`, req.AttemptID, item.ItemID).Scan(&processID); err != nil {
				return err
			}
			if processID != item.ProcessID {
				return failure("IDEMPOTENCY_CONFLICT", "tool item already holds a different process handle; use a new item ID for the restarted process")
			}
		}
	}
	if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET capture_state='attached' WHERE attempt_id=$1 AND capture_state IN ('','lost')`, req.AttemptID); err != nil {
		return err
	}
	// Every capture invalidates a prior clear scan regardless of capture state:
	// evidence must postdate the newest observation, including a repeated item.
	if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET terminal_scan='' WHERE attempt_id=$1 AND terminal_scan='clear'`, req.AttemptID); err != nil {
		return err
	}
	return nil
}

// reportNativeCleanup preserves observation order: turn and capture changes
// invalidate old scans, a supplied scan follows them, and an owner join revokes
// exclusivity and invalidates that scan before tool stop progress is recorded.
func reportNativeCleanup(ctx context.Context, tx pgx.Tx, attempt SessionInboxAttempt, req SessionToolStopReport) error {
	var err error
	if req.TurnStop != "" && attempt.TurnStopState != req.TurnStop {
		// A positive observation supersedes ambiguity; a positive stop
		// never drifts to a different positive or ambiguous value. The
		// stop is new evidence: any earlier clear scan predates it.
		if attempt.TurnStopState == "" || attempt.TurnStopState == "ambiguous" {
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET turn_stop_state=$2,terminal_scan='' WHERE attempt_id=$1`, req.AttemptID, req.TurnStop); err != nil {
				return err
			}
		}
	}
	switch req.CaptureState {
	case "attached":
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET capture_state='attached' WHERE attempt_id=$1 AND capture_state IN ('','lost')`, req.AttemptID); err != nil {
			return err
		}
	case "lost":
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET capture_state='lost',capture_gap=true,terminal_scan='' WHERE attempt_id=$1 AND capture_state='attached'`, req.AttemptID); err != nil {
			return err
		}
	case "complete":
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET capture_state='complete' WHERE attempt_id=$1 AND capture_state='attached'`, req.AttemptID); err != nil {
			return err
		}
	}
	if req.TerminalScan != "" {
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET terminal_scan=$2 WHERE attempt_id=$1`, req.AttemptID, req.TerminalScan); err != nil {
			return err
		}
	}
	if req.OwnerJoin {
		// A host-observed owner input joining the admitted turn revokes
		// the exclusivity attestation one-way and invalidates any scan
		// evidence: per-request stop authority no longer holds.
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET turn_exclusive=false,terminal_scan='' WHERE attempt_id=$1 AND turn_exclusive`, req.AttemptID); err != nil {
			return err
		}
	}
	for _, tool := range req.Tools {
		tag, err := tx.Exec(ctx, `UPDATE cairn.agent_session_tool SET stop_state=$3,stop_at=CASE WHEN $3 IN ('terminated','unavailable') THEN clock_timestamp() ELSE stop_at END WHERE attempt_id=$1 AND item_id=$2 AND stop_state NOT IN ('terminated','unavailable')`, req.AttemptID, tool.ItemID, tool.StopState)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			var exists bool
			if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_session_tool WHERE attempt_id=$1 AND item_id=$2)`, req.AttemptID, tool.ItemID).Scan(&exists); err != nil {
				return err
			}
			if !exists {
				return failure("NOT_FOUND", "tool item is not captured for this attempt")
			}
		}
	}
	return nil
}

// nativeCleanupObstacle describes the first missing cleanup observation. A
// complete capture stream never substitutes for a fresh clear terminal scan.
func nativeCleanupObstacle(attempt SessionInboxAttempt) string {
	if attempt.TurnStopState != "interrupted" && attempt.TurnStopState != "ended" {
		return "without a positive turn stop"
	}
	for _, tool := range attempt.Tools {
		if tool.StopState != "terminated" && tool.StopState != "unavailable" {
			return "while captured tools are still running"
		}
	}
	if attempt.TerminalScan != "clear" {
		return "without a fresh clear terminal scan"
	}
	return ""
}

// settleNativeCleanup handles cancellation outcomes only. It writes delivery
// and attempt settlement atomically in the existing transaction, retaining the
// original operator attribution. Revocation never confirms cancellation. A
// false result without an error leaves ordinary reconciliation to the caller.
func settleNativeCleanup(ctx context.Context, tx pgx.Tx, attempt SessionInboxAttempt, reason string) (bool, error) {
	var err error
	if reason == "exclusivity_revoked" && !(attempt.Cancel != nil && attempt.Cancel.ConfirmedAt == nil && !attempt.TurnExclusive) {
		// Revocation reconciliation is valid only for a pending
		// cancellation whose exclusivity attestation was actually
		// revoked; it never applies to ordinary attempts.
		return false, failure("INVALID_REQUEST", "exclusivity revocation requires a pending cancellation on an attestation that was revoked")
	}
	if attempt.Cancel != nil && attempt.Cancel.ConfirmedAt == nil {
		if !attempt.TurnExclusive {
			// The exclusivity attestation was revoked after the operator
			// intent: per-request stop authority no longer exists. This
			// records lost stop permission, never turn/tool cleanup, so
			// captured tools must already be terminal to close here.
			if reason == "exclusivity_revoked" {
				// Permission revocation alone establishes nothing about
				// the running work: release requires the same positive
				// evidence family as cleanup confirmation, recorded as a
				// separate outcome with the cancellation unconfirmed.
				if obstacle := nativeCleanupObstacle(attempt); obstacle != "" {
					return false, failure("CLEANUP_UNCONFIRMED", "revocation cannot close "+obstacle)
				}
				if _, err = tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='failed',lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code='operator_cancelled',control_at=clock_timestamp(),control_by=$2,control_reason=$3 WHERE delivery_id=$1 AND state IN ('pending','leased')`, attempt.Delivery.DeliveryID, attempt.Cancel.By, attempt.Cancel.Reason); err != nil {
					return false, err
				}
				if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET finished_at=clock_timestamp(),reason='exclusivity_revoked' WHERE attempt_id=$1`, attempt.ID); err != nil {
					return false, err
				}
				return true, nil
			}
			return false, failure("CLEANUP_UNCONFIRMED", "exclusive turn ownership was revoked; per-request cleanup cannot be confirmed")
		}
		// An operator cancellation keeps its hold until the binding host
		// confirms the exact turn stopped and owned tools are terminal.
		if reason != "cancel_confirmed" {
			return false, failure("CLEANUP_UNCONFIRMED", "operator cancellation is pending; confirm the native turn and owned tool cleanup first")
		}
		if nativeCleanupObstacle(attempt) != "" {
			return false, failure("CLEANUP_UNCONFIRMED", "cancellation cleanup is incomplete; the native turn stop or owned tool termination is unconfirmed")
		}
		if attempt.Delivery.State == "pending" || attempt.Delivery.State == "leased" {
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_delivery SET state='failed',lease_id=NULL,lease_until=NULL,completed_at=clock_timestamp(),code='operator_cancelled',control_at=clock_timestamp(),control_by=$2,control_reason=$3 WHERE delivery_id=$1`, attempt.Delivery.DeliveryID, attempt.Cancel.By, attempt.Cancel.Reason); err != nil {
				return false, err
			}
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET finished_at=clock_timestamp(),reason='cancel_confirmed',cancel_confirmed_at=clock_timestamp() WHERE attempt_id=$1`, attempt.ID); err != nil {
			return false, err
		}
		return true, nil
	}
	if reason == "cancel_confirmed" {
		return false, failure("INVALID_REQUEST", "cancel confirmation requires a recorded operator cancellation")
	}
	return false, nil
}
