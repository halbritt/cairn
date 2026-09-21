package core

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// Native request control. Operator cancellation fences completion immediately
// through the attempt's durable intent; the inbox hold survives until the
// binding host positively confirms the pinned turn stopped, every captured
// tool process is terminal, and either the capture stream covered the turn or
// a terminal scan observed the thread clear. Ambiguous transport observations
// never release a hold.

type SessionCancel struct {
	RequestedAt time.Time  `json:"requested_at"`
	By          string     `json:"by"`
	Reason      string     `json:"reason"`
	ConfirmedAt *time.Time `json:"confirmed_at,omitempty"`
}

type SessionTool struct {
	ItemID    string     `json:"item_id"`
	ProcessID string     `json:"process_id"`
	Command   string     `json:"command,omitempty"`
	StopState string     `json:"stop_state"`
	StopAt    *time.Time `json:"stop_at,omitempty"`
}

type SessionInboxControl struct {
	AgentID     string `json:"agent_id"`
	ExecutionID string `json:"execution_id"`
}
type SessionInboxControlStatus struct {
	Attempt *SessionInboxAttempt `json:"attempt"`
}

// SessionInboxControl reports the unfinished attempt owning this inbox,
// including any operator cancellation and captured tool ownership. It accepts
// the session's current execution so a restarted watcher can recover an
// attempt started by an earlier execution of the same conversation.
func (s *Store) SessionInboxControl(ctx context.Context, ref AgentSessionRef, dest Destination) (SessionInboxControlStatus, error) {
	var out SessionInboxControlStatus
	if err := s.nativeInboxProfile(ref, dest); err != nil {
		return out, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	a, err := currentAgentSession(ctx, tx, ref, s.channel.Principal, s.channel.Repo, true, dest)
	if err != nil {
		return out, err
	}
	if err = activeAgent(a); err != nil {
		return out, err
	}
	var attemptID string
	err = tx.QueryRow(ctx, `SELECT attempt_id::text FROM cairn.agent_session_attempt WHERE agent_id=$1 AND finished_at IS NULL LIMIT 1`, a.AgentID).Scan(&attemptID)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, tx.Commit(ctx)
	}
	if err != nil {
		return out, err
	}
	execution, err := attemptExecution(ctx, tx, attemptID)
	if err != nil {
		return out, err
	}
	attempt, err := s.readSessionInboxAttempt(ctx, tx, attemptID, AgentSessionRef{AgentID: a.AgentID, ExecutionID: execution}, dest)
	if err != nil {
		return out, err
	}
	out.Attempt = &attempt
	return out, tx.Commit(ctx)
}

func attemptExecution(ctx context.Context, tx pgx.Tx, id string) (string, error) {
	var execution string
	if err := tx.QueryRow(ctx, `SELECT execution_id::text FROM cairn.agent_session_attempt WHERE attempt_id=$1`, id).Scan(&execution); err != nil {
		return "", err
	}
	return execution, nil
}

type SessionToolItem struct {
	ItemID       string `json:"item_id"`
	ProcessID    string `json:"process_id"`
	Command      string `json:"command,omitempty"`
	NativeTurnID string `json:"native_turn_id,omitempty"`
}
type SessionToolCapture struct {
	RequestID string            `json:"request_id"`
	Session   AgentSessionRef   `json:"session"`
	AttemptID string            `json:"attempt_id"`
	Items     []SessionToolItem `json:"items"`
}

func validToolText(value, field string, max int) error {
	value = strings.TrimSpace(value)
	if value == "" || len(value) > max || strings.ContainsRune(value, 0) {
		return failure("INVALID_REQUEST", "native tool "+field+" must be non-empty bounded text")
	}
	return nil
}

// CaptureSessionTools durably records tool processes observed for the exact
// native turn pinned by the attempt. A later or earlier turn's tools cannot be
// attributed to this request, and one item's process handle is immutable while
// its stop is not terminal: a restarted tool is a different item.
func (s *Store) CaptureSessionTools(ctx context.Context, req SessionToolCapture, dest Destination) (SessionInboxAttempt, error) {
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return SessionInboxAttempt{}, err
	}
	if validID(req.RequestID) != nil || validID(req.AttemptID) != nil {
		return SessionInboxAttempt{}, failure("INVALID_REQUEST", "stable capture UUID and attempt UUID required")
	}
	if len(req.Items) == 0 || len(req.Items) > 64 {
		return SessionInboxAttempt{}, failure("INVALID_REQUEST", "capture requires between one and 64 tool items")
	}
	return mutate(ctx, s, "session-tool-capture", req.RequestID, req, func(tx pgx.Tx) (SessionInboxAttempt, error) {
		attempt, err := s.lockedSessionAttempt(ctx, tx, req.AttemptID, req.Session, dest)
		if err != nil {
			return attempt, err
		}
		if attempt.FinishedAt != nil {
			return attempt, failure("IDEMPOTENCY_CONFLICT", "attempt already finished; late tools cannot attach to it")
		}
		for _, item := range req.Items {
			if err := validToolText(item.ItemID, "item id", 256); err != nil {
				return attempt, err
			}
			if err := validToolText(item.ProcessID, "process id", 256); err != nil {
				return attempt, err
			}
			if len(item.Command) > 1024 || strings.ContainsRune(item.Command, 0) {
				return attempt, failure("INVALID_REQUEST", "native tool command exceeds bounded text")
			}
			if attempt.NativeTurnID != "" && item.NativeTurnID != attempt.NativeTurnID {
				return attempt, failure("INVALID_REQUEST", "tool item belongs to a different native turn")
			}
			tag, err := tx.Exec(ctx, `INSERT INTO cairn.agent_session_tool(tool_id,attempt_id,delivery_id,agent_id,native_turn_id,item_id,process_id,command) VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT (attempt_id,item_id) DO UPDATE SET command=EXCLUDED.command WHERE agent_session_tool.stop_state='captured' AND agent_session_tool.process_id=EXCLUDED.process_id`,
				uuid.NewString(), req.AttemptID, attempt.Delivery.DeliveryID, req.Session.AgentID, item.NativeTurnID, item.ItemID, item.ProcessID, item.Command)
			if err != nil {
				return attempt, err
			}
			if tag.RowsAffected() == 0 {
				var processID, stopState string
				if err = tx.QueryRow(ctx, `SELECT process_id,stop_state FROM cairn.agent_session_tool WHERE attempt_id=$1 AND item_id=$2`, req.AttemptID, item.ItemID).Scan(&processID, &stopState); err != nil {
					return attempt, err
				}
				if processID != item.ProcessID {
					return attempt, failure("IDEMPOTENCY_CONFLICT", "tool item already holds a different process handle; report its stop state before recapturing")
				}
			}
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET capture_state='attached' WHERE attempt_id=$1 AND capture_state IN ('','lost')`, req.AttemptID); err != nil {
			return attempt, err
		}
		// Every new captured tool invalidates a prior clear scan regardless
		// of capture state: evidence must postdate the newest observation.
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET terminal_scan='' WHERE attempt_id=$1 AND terminal_scan='clear'`, req.AttemptID); err != nil {
			return attempt, err
		}
		return s.readSessionInboxAttempt(ctx, tx, req.AttemptID, req.Session, dest)
	})
}

type SessionToolStop struct {
	ItemID    string `json:"item_id"`
	StopState string `json:"stop_state"`
}
type SessionToolStopReport struct {
	RequestID    string            `json:"request_id"`
	Session      AgentSessionRef   `json:"session"`
	AttemptID    string            `json:"attempt_id"`
	TurnStop     string            `json:"turn_stop,omitempty"`
	CaptureState string            `json:"capture_state,omitempty"`
	TerminalScan string            `json:"terminal_scan,omitempty"`
	OwnerJoin    bool              `json:"owner_join,omitempty"`
	Tools        []SessionToolStop `json:"tools"`
}

var toolStopStates = map[string]bool{"stop_issued": true, "terminated": true, "unavailable": true}
var turnStopStates = map[string]bool{"interrupted": true, "ended": true, "ambiguous": true}
var captureStates = map[string]bool{"attached": true, "lost": true, "complete": true}
var terminalScans = map[string]bool{"clear": true, "unknown_remaining": true}

// ReportSessionToolStop records bounded stop progress for the attempt's turn
// and its captured tools. It never releases the hold; only reconciliation
// after a confirmed cleanup closes a cancelled attempt. An ambiguous turn
// stop or an unknown terminal scan is durable testimony that cleanup is NOT
// confirmed: those states hold until a positive observation replaces them.
func (s *Store) ReportSessionToolStop(ctx context.Context, req SessionToolStopReport, dest Destination) (SessionInboxAttempt, error) {
	if err := s.nativeInboxProfile(req.Session, dest); err != nil {
		return SessionInboxAttempt{}, err
	}
	if validID(req.RequestID) != nil || validID(req.AttemptID) != nil {
		return SessionInboxAttempt{}, failure("INVALID_REQUEST", "stable stop report UUID and attempt UUID required")
	}
	if req.TurnStop != "" && !turnStopStates[req.TurnStop] {
		return SessionInboxAttempt{}, failure("INVALID_REQUEST", "turn stop state must be interrupted, ended or ambiguous")
	}
	if req.CaptureState != "" && !captureStates[req.CaptureState] {
		return SessionInboxAttempt{}, failure("INVALID_REQUEST", "capture state must be attached, lost or complete")
	}
	if req.TerminalScan != "" && !terminalScans[req.TerminalScan] {
		return SessionInboxAttempt{}, failure("INVALID_REQUEST", "terminal scan must be clear or unknown_remaining")
	}
	if len(req.Tools) > 64 {
		return SessionInboxAttempt{}, failure("INVALID_REQUEST", "stop report accepts at most 64 tools")
	}
	for _, tool := range req.Tools {
		if !toolStopStates[tool.StopState] {
			return SessionInboxAttempt{}, failure("INVALID_REQUEST", "tool stop state must be stop_issued, terminated or unavailable")
		}
		if err := validToolText(tool.ItemID, "item id", 256); err != nil {
			return SessionInboxAttempt{}, err
		}
	}
	return mutate(ctx, s, "session-tool-stop", req.RequestID, req, func(tx pgx.Tx) (SessionInboxAttempt, error) {
		attempt, err := s.lockedSessionAttempt(ctx, tx, req.AttemptID, req.Session, dest)
		if err != nil {
			return attempt, err
		}
		if attempt.FinishedAt != nil {
			return attempt, failure("IDEMPOTENCY_CONFLICT", "attempt already finished; its stop state is closed")
		}
		if req.TurnStop != "" && attempt.TurnStopState != req.TurnStop {
			// A positive observation supersedes ambiguity; a positive stop
			// never drifts to a different positive or ambiguous value. The
			// stop is new evidence: any earlier clear scan predates it.
			if attempt.TurnStopState == "" || attempt.TurnStopState == "ambiguous" {
				if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET turn_stop_state=$2,terminal_scan='' WHERE attempt_id=$1`, req.AttemptID, req.TurnStop); err != nil {
					return attempt, err
				}
			}
		}
		switch req.CaptureState {
		case "attached":
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET capture_state='attached' WHERE attempt_id=$1 AND capture_state IN ('','lost')`, req.AttemptID); err != nil {
				return attempt, err
			}
		case "lost":
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET capture_state='lost',capture_gap=true,terminal_scan='' WHERE attempt_id=$1 AND capture_state='attached'`, req.AttemptID); err != nil {
				return attempt, err
			}
		case "complete":
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET capture_state='complete' WHERE attempt_id=$1 AND capture_state='attached'`, req.AttemptID); err != nil {
				return attempt, err
			}
		}
		if req.TerminalScan != "" {
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET terminal_scan=$2 WHERE attempt_id=$1`, req.AttemptID, req.TerminalScan); err != nil {
				return attempt, err
			}
		}
		if req.OwnerJoin {
			// A host-observed owner input joining the admitted turn revokes
			// the exclusivity attestation one-way and invalidates any scan
			// evidence: per-request stop authority no longer holds.
			if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session_attempt SET turn_exclusive=false,terminal_scan='' WHERE attempt_id=$1 AND turn_exclusive`, req.AttemptID); err != nil {
				return attempt, err
			}
		}
		for _, tool := range req.Tools {
			tag, err := tx.Exec(ctx, `UPDATE cairn.agent_session_tool SET stop_state=$3,stop_at=CASE WHEN $3 IN ('terminated','unavailable') THEN clock_timestamp() ELSE stop_at END WHERE attempt_id=$1 AND item_id=$2 AND stop_state NOT IN ('terminated','unavailable')`, req.AttemptID, tool.ItemID, tool.StopState)
			if err != nil {
				return attempt, err
			}
			if tag.RowsAffected() == 0 {
				var exists bool
				if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_session_tool WHERE attempt_id=$1 AND item_id=$2)`, req.AttemptID, tool.ItemID).Scan(&exists); err != nil {
					return attempt, err
				}
				if !exists {
					return attempt, failure("NOT_FOUND", "tool item is not captured for this attempt")
				}
			}
		}
		return s.readSessionInboxAttempt(ctx, tx, req.AttemptID, req.Session, dest)
	})
}

// lockedSessionAttempt serializes stop reports against cancellation and
// reconciliation on the attempt's own execution and owner.
func (s *Store) lockedSessionAttempt(ctx context.Context, tx pgx.Tx, id string, ref AgentSessionRef, dest Destination) (SessionInboxAttempt, error) {
	if err := lock(ctx, tx, "session-attempt:"+id); err != nil {
		return SessionInboxAttempt{}, err
	}
	return s.readSessionInboxAttempt(ctx, tx, id, ref, dest)
}

func readSessionCancel(row pgx.Row) (*SessionCancel, string, string, string, error) {
	var requested, confirmed *time.Time
	var by, reason *string
	var turnStop, captureState, terminalScan string
	if err := row.Scan(&requested, &confirmed, &by, &reason, &turnStop, &captureState, &terminalScan); err != nil {
		return nil, "", "", "", err
	}
	if requested == nil {
		return nil, turnStop, captureState, terminalScan, nil
	}
	out := &SessionCancel{RequestedAt: *requested, By: *by, Reason: *reason, ConfirmedAt: confirmed}
	return out, turnStop, captureState, terminalScan, nil
}

func readSessionTools(ctx context.Context, tx pgx.Tx, attemptID string) ([]SessionTool, error) {
	rows, err := tx.Query(ctx, `SELECT item_id,process_id,command,stop_state,stop_at FROM cairn.agent_session_tool WHERE attempt_id=$1 ORDER BY captured_at,item_id`, attemptID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	tools := []SessionTool{}
	for rows.Next() {
		var tool SessionTool
		if err = rows.Scan(&tool.ItemID, &tool.ProcessID, &tool.Command, &tool.StopState, &tool.StopAt); err != nil {
			return nil, err
		}
		tools = append(tools, tool)
	}
	return tools, rows.Err()
}

// cancelCleanupReady reports whether a cancelled attempt's host positively
// confirmed the native turn stopped, every captured tool is terminal, and a
// final terminal scan observed the thread clear. Capture completeness alone
// can never substitute for that scan: a latched coverage gap or an explicit
// unknown_remaining observation keeps the hold until a clear scan proves
// nothing is left running.
func cancelCleanupReady(attempt SessionInboxAttempt) bool {
	if attempt.Cancel == nil {
		return false
	}
	if attempt.TurnStopState != "interrupted" && attempt.TurnStopState != "ended" {
		return false
	}
	for _, tool := range attempt.Tools {
		if tool.StopState != "terminated" && tool.StopState != "unavailable" {
			return false
		}
	}
	return attempt.TerminalScan == "clear"
}
