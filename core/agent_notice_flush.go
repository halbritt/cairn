package core

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
)

type NoticeFlushRequest struct {
	RequestID string `json:"request_id"`
	EventID   string `json:"event_id"`
}

// NoticeFlushResult is the first flush decision for an event. Counts describe
// delivery states at that instant; later claims and completions remain separate.
type NoticeFlushResult struct {
	EventID      string    `json:"event_id"`
	RequestID    string    `json:"request_id"`
	FlushedAt    time.Time `json:"flushed_at"`
	FlushedBy    string    `json:"flushed_by"`
	Released     int       `json:"released"`
	AlreadyReady int       `json:"already_ready"`
	Leased       int       `json:"leased"`
	Terminal     int       `json:"terminal"`
}

// FlushNotice makes a published routine notice eligible without changing its
// source, original policy, delivery identities, leases, or terminal results.
func (s *Store) FlushNotice(ctx context.Context, req NoticeFlushRequest, dest Destination) (NoticeFlushResult, error) {
	if err := validEventDestination(dest); err != nil {
		return NoticeFlushResult{}, err
	}
	if err := validID(req.EventID); err != nil {
		return NoticeFlushResult{}, err
	}
	authorize := func(tx pgx.Tx) error {
		event, err := s.readEvent(ctx, tx, req.EventID, dest)
		if err != nil {
			return err
		}
		if event.From != s.channel.Principal {
			return failure("AUTHORITY_DENIED", "notice flush requires the original publisher")
		}
		if event.NoticePolicy == nil || event.NoticePolicy.Urgency != "routine" || event.NoticePolicy.DeferSeconds == 0 {
			return failure("INVALID_REQUEST", "only deferred routine notices can be flushed")
		}
		return nil
	}
	return mutate(ctx, s, "event-notice-flush", req.RequestID, req, func(tx pgx.Tx) (NoticeFlushResult, error) {
		if err := lock(ctx, tx, "notice-flush:"+req.EventID); err != nil {
			return NoticeFlushResult{}, err
		}
		// A second request UUID observes the first durable decision. Fanout is
		// fixed at publication, so there can be no newly deferred deliveries.
		if result, err := readNoticeFlush(ctx, tx, req.EventID); err == nil {
			return result, nil
		} else if !errors.Is(err, pgx.ErrNoRows) {
			return NoticeFlushResult{}, err
		}
		rows, err := tx.Query(ctx, `SELECT d.delivery_id::text FROM cairn.agent_delivery d WHERE d.event_id=$1 ORDER BY d.delivery_id FOR UPDATE OF d`, req.EventID)
		if err != nil {
			return NoticeFlushResult{}, err
		}
		for rows.Next() {
			var id string
			if err := rows.Scan(&id); err != nil {
				rows.Close()
				return NoticeFlushResult{}, err
			}
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return NoticeFlushResult{}, err
		}
		var at time.Time
		if err := tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&at); err != nil {
			return NoticeFlushResult{}, err
		}
		rows, err = tx.Query(ctx, `SELECT state,available_at FROM cairn.agent_delivery WHERE event_id=$1`, req.EventID)
		if err != nil {
			return NoticeFlushResult{}, err
		}
		result := NoticeFlushResult{EventID: req.EventID, RequestID: req.RequestID, FlushedAt: at}
		for rows.Next() {
			var state string
			var availableAt time.Time
			if err := rows.Scan(&state, &availableAt); err != nil {
				rows.Close()
				return NoticeFlushResult{}, err
			}
			switch state {
			case "pending":
				if availableAt.After(at) {
					result.Released++
				} else {
					result.AlreadyReady++
				}
			case "leased":
				result.Leased++
			case "handled", "ignored", "failed":
				result.Terminal++
			default:
				rows.Close()
				return NoticeFlushResult{}, fmt.Errorf("unexpected delivery state %q", state)
			}
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return NoticeFlushResult{}, err
		}
		updated, err := tx.Exec(ctx, `UPDATE cairn.agent_delivery SET available_at=$2 WHERE event_id=$1 AND state='pending' AND available_at>$2`, req.EventID, at)
		if err != nil {
			return NoticeFlushResult{}, err
		}
		if updated.RowsAffected() != int64(result.Released) {
			return NoticeFlushResult{}, failure("VERSION_CONFLICT", "notice availability changed during flush")
		}
		err = tx.QueryRow(ctx, `INSERT INTO cairn.agent_notice_flush(event_id,request_id,flushed_at,released,already_ready,leased,terminal) VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING flushed_by`, req.EventID, req.RequestID, at, result.Released, result.AlreadyReady, result.Leased, result.Terminal).Scan(&result.FlushedBy)
		return result, err
	}, authorize)
}

func readNoticeFlush(ctx context.Context, tx pgx.Tx, eventID string) (NoticeFlushResult, error) {
	var result NoticeFlushResult
	err := tx.QueryRow(ctx, `SELECT event_id::text,request_id::text,flushed_at,flushed_by,released,already_ready,leased,terminal FROM cairn.agent_notice_flush WHERE event_id=$1`, eventID).Scan(&result.EventID, &result.RequestID, &result.FlushedAt, &result.FlushedBy, &result.Released, &result.AlreadyReady, &result.Leased, &result.Terminal)
	return result, err
}
