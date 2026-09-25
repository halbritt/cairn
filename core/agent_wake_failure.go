package core

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
)

// ProviderFailure carries selected native failure metadata. It neither contains
// raw diagnostics nor certifies provider capacity or task outcome.
type ProviderFailure struct {
	Harness string     `json:"harness"`
	Source  string     `json:"source"`
	Kind    string     `json:"kind"`
	Code    string     `json:"code"`
	Status  int        `json:"status,omitempty"`
	RetryAt *time.Time `json:"retry_at,omitempty"`
}

func (f ProviderFailure) Validate() error {
	if !eventName.MatchString(f.Harness) || (f.Source != "native-event" && f.Source != "native-hook" && f.Source != "native-diagnostic") ||
		(f.Kind != "quota" && f.Kind != "rate_limit" && f.Kind != "billing") || !eventName.MatchString(f.Code) ||
		(f.Status != 0 && (f.Status < 100 || f.Status > 599)) {
		return failure("INVALID_REQUEST", "provider failure requires bounded native source, harness, quota/rate_limit/billing kind and code")
	}
	if f.RetryAt != nil && (f.RetryAt.Year() < 1 || f.RetryAt.Year() > 9999) {
		return failure("INVALID_REQUEST", "provider retry time is out of range")
	}
	return nil
}

// Keep the durable report and admission change within the wake transaction.
// Finish remains available after restore; this does not release the attempt hold.
func (s *Store) suspendWakeBinding(ctx context.Context, tx pgx.Tx, w WakeAttempt, f ProviderFailure) error {
	if w.WorkerID == "" {
		return failure("INVALID_REQUEST", "provider failure requires a registered worker attempt")
	}
	slot, err := scanWorker(tx.QueryRow(ctx, `SELECT `+workerColumns+` FROM cairn.agent_worker_slot WHERE repo=$1 AND consumer=$2 FOR UPDATE`, w.Delivery.Event.Repo, s.channel.Principal))
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("STALE_WORKER", "wake binding is no longer registered")
	}
	if err != nil {
		return err
	}
	if slot.SupervisorID != w.WorkerID {
		return failure("STALE_WORKER", "wake belongs to an older supervisor")
	}
	if slot.Spec.Harness != f.Harness {
		return failure("INVALID_REQUEST", "provider failure harness differs from configured launcher")
	}
	if slot.Health != "available" {
		// Keep a selected pause or earlier suspension. The wake still records
		// this observation, but it must not replace the operator's health state.
		return nil
	}
	reason := fmt.Sprintf("%s observed by %s (%s) in wake %s", f.Kind, f.Harness, f.Code, w.ID)
	_, err = tx.Exec(ctx, `UPDATE cairn.agent_worker_slot SET health='unavailable',reason=$3,retry_at=$4,health_at=clock_timestamp(),revision=revision+1 WHERE repo=$1 AND consumer=$2`, slot.Repo, slot.Consumer, reason, f.RetryAt)
	return err
}

// Reconciliation can retain a locally observed failure after the report API was
// unavailable. Once recorded, an identical finish cannot reapply suspension.
func (s *Store) recordWakeProviderFailure(ctx context.Context, tx pgx.Tx, w WakeAttempt, f *ProviderFailure) error {
	if f == nil {
		return nil
	}
	if w.State != "running" {
		return failure("INVALID_REQUEST", "provider observations require an executed wake")
	}
	if w.ProviderFailure != nil {
		old := w.ProviderFailure
		sameTime := old.RetryAt == nil && f.RetryAt == nil || old.RetryAt != nil && f.RetryAt != nil && old.RetryAt.Equal(*f.RetryAt)
		if old.Harness != f.Harness || old.Source != f.Source || old.Kind != f.Kind || old.Code != f.Code || old.Status != f.Status || !sameTime {
			return failure("VERSION_CONFLICT", "wake provider observation is already recorded")
		}
		return nil
	}
	if err := s.suspendWakeBinding(ctx, tx, w, *f); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, `UPDATE cairn.agent_wake_attempt SET provider_failure=$2,provider_failure_at=clock_timestamp() WHERE attempt_id=$1`, w.ID, f)
	return err
}
