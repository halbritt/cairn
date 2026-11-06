package core

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
)

const eventTokenScale int64 = 1000000000

type eventRateCharge struct {
	Dimension string
	Kind      string
	Identity  string
	Unit      string
	Rate      EventAdmissionRate
	Cost      int64
}
type eventRateBucket struct {
	Tokens         int64
	RefillAt       time.Time
	PolicyRevision int64
	Generation     int64
}

func eventRateCharges(identity eventProducerIdentity, limits *EventAdmissionLimits, publications, deliveries int64) []eventRateCharge {
	return []eventRateCharge{
		{"producer_publications", "producer", identity.Principal, "publication", limits.ProducerPublications, publications},
		{"producer_deliveries", "producer", identity.Principal, "delivery", limits.ProducerDeliveries, deliveries},
		{"owner_publications", "owner", identity.Owner, "publication", limits.OwnerPublications, publications},
		{"owner_deliveries", "owner", identity.Owner, "delivery", limits.OwnerDeliveries, deliveries},
		{"collection_publications", "collection", "", "publication", limits.CollectionPublications, publications},
		{"collection_deliveries", "collection", "", "delivery", limits.CollectionDeliveries, deliveries},
	}
}
func refillEventRateBucket(bucket eventRateBucket, rate EventAdmissionRate, policy EventAdmissionPolicyState, now time.Time) eventRateBucket {
	capacity := rate.Burst * eventTokenScale
	if bucket.Tokens > capacity {
		bucket.Tokens = capacity
	}
	if bucket.PolicyRevision != policy.Revision && policy.UpdatedAt != nil && bucket.RefillAt.Before(*policy.UpdatedAt) {
		bucket.RefillAt = *policy.UpdatedAt
	}
	if now.After(bucket.RefillAt) {
		room := capacity - bucket.Tokens
		// Compare before multiplying: elapsed*rate may otherwise overflow after a
		// long idle period even though a bucket can hold at most 10^18 nanounits.
		elapsed := now.Sub(bucket.RefillAt).Nanoseconds()
		fullAfter := room / rate.PerSecond
		if room%rate.PerSecond != 0 {
			fullAfter++
		}
		if elapsed >= fullAfter {
			bucket.Tokens = capacity
		} else {
			bucket.Tokens += elapsed * rate.PerSecond
		}
		bucket.RefillAt = now
	}
	bucket.PolicyRevision = policy.Revision
	return bucket
}
func eventRateRefusal(charge eventRateCharge, available int64, revision int64) *Error {
	details := &EventAdmissionRefusal{Dimension: charge.Dimension, PolicyRevision: revision, Retryable: true}
	message := "rate budget is exhausted; retry unchanged intent after backoff"
	if charge.Cost > charge.Rate.Burst {
		details.Retryable = false
		message = "publication exceeds the configured rate burst; policy or workload must change"
	} else {
		deficit := charge.Cost*eventTokenScale - available
		nanos := deficit / charge.Rate.PerSecond
		if deficit%charge.Rate.PerSecond != 0 {
			nanos++
		}
		seconds := nanos / eventTokenScale
		if nanos%eventTokenScale != 0 {
			seconds++
		}
		if seconds < 1 {
			seconds = 1
		}
		if seconds > 3600 {
			seconds = 3600
		}
		details.RetryAfterSeconds = int(seconds)
	}
	return &Error{Code: "EVENT_ADMISSION_REFUSED", Message: fmt.Sprintf("event admission dimension %s at policy revision %d; %s", charge.Dimension, revision, message), Admission: details}
}
func readEventRateBucket(ctx context.Context, tx pgx.Tx, repo string, charge eventRateCharge, update bool) (eventRateBucket, bool, error) {
	var bucket eventRateBucket
	rowLock := ""
	if update {
		rowLock = " FOR UPDATE"
	}
	err := tx.QueryRow(ctx, `SELECT tokens_nano,refill_at,policy_revision,restore_generation FROM cairn.agent_event_rate_bucket WHERE repo=$1 AND identity_kind=$2 AND identity=$3 AND unit=$4`+rowLock, repo, charge.Kind, charge.Identity, charge.Unit).Scan(&bucket.Tokens, &bucket.RefillAt, &bucket.PolicyRevision, &bucket.Generation)
	if errors.Is(err, pgx.ErrNoRows) {
		return bucket, false, nil
	}
	return bucket, err == nil, err
}
func validateEventRateCharge(charge eventRateCharge) error {
	if charge.Cost < 0 || charge.Cost > 1000000000 || charge.Rate.PerSecond < 1 || charge.Rate.PerSecond > 1000000000 || charge.Rate.Burst < 1 || charge.Rate.Burst > 1000000000 {
		return failure("FEATURE_UNAVAILABLE", "event rate admission requires finite validated units, rates and bursts")
	}
	return nil
}

// All bucket decisions and writes are inside the caller's collection-locked
// mutation. A later refusal rolls back every earlier debit and any new bucket.
func chargeEventRatesTx(ctx context.Context, tx pgx.Tx, repo string, identity eventProducerIdentity, policy EventAdmissionPolicyState, publications, deliveries int64) error {
	if policy.Policy.Limits == nil {
		return failure("FEATURE_UNAVAILABLE", "event rate admission requires explicit limits")
	}
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return err
	}
	var now time.Time
	if err = tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&now); err != nil {
		return err
	}
	for _, charge := range eventRateCharges(identity, policy.Policy.Limits, publications, deliveries) {
		if err = validateEventRateCharge(charge); err != nil {
			return err
		}
		if charge.Cost == 0 {
			continue
		}
		if charge.Cost > charge.Rate.Burst {
			return eventRateRefusal(charge, 0, policy.Revision)
		}
		bucket, found, err := readEventRateBucket(ctx, tx, repo, charge, true)
		if err != nil {
			return err
		}
		if !found {
			bucket = eventRateBucket{Tokens: charge.Rate.Burst * eventTokenScale, RefillAt: now, PolicyRevision: policy.Revision, Generation: generation}
		}
		if bucket.Generation != generation {
			return failure("RATE_UNRECONCILED", "event rate budgets require current restore-generation reconciliation")
		}
		bucket = refillEventRateBucket(bucket, charge.Rate, policy, now)
		cost := charge.Cost * eventTokenScale
		if bucket.Tokens < cost {
			return eventRateRefusal(charge, bucket.Tokens, policy.Revision)
		}
		bucket.Tokens -= cost
		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_event_rate_bucket(repo,identity_kind,identity,unit,tokens_nano,refill_at,policy_revision,restore_generation) VALUES($1,$2,$3,$4,$5,$6,$7,$8)
 ON CONFLICT(repo,identity_kind,identity,unit) DO UPDATE SET tokens_nano=EXCLUDED.tokens_nano,refill_at=EXCLUDED.refill_at,policy_revision=EXCLUDED.policy_revision,restore_generation=EXCLUDED.restore_generation`, repo, charge.Kind, charge.Identity, charge.Unit, bucket.Tokens, bucket.RefillAt, bucket.PolicyRevision, bucket.Generation)
		if err != nil {
			return err
		}
	}
	return nil
}

// Preview reads retained real budgets only. Observe mode does not create or
// debit buckets, and averages are not substituted for a token-bucket balance.
func previewEventRatesTx(ctx context.Context, tx pgx.Tx, repo string, identity eventProducerIdentity, policy EventAdmissionPolicyState, deliveries int64) ([]string, bool, error) {
	dimensions := []string{}
	initialized := true
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return nil, false, err
	}
	var now time.Time
	if err = tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&now); err != nil {
		return nil, false, err
	}
	for _, charge := range eventRateCharges(identity, policy.Policy.Limits, 1, deliveries) {
		if err = validateEventRateCharge(charge); err != nil {
			return nil, false, err
		}
		if charge.Cost == 0 {
			continue
		}
		bucket, found, err := readEventRateBucket(ctx, tx, repo, charge, false)
		if err != nil {
			return nil, false, err
		}
		if !found || bucket.Generation != generation {
			initialized = false
		}
		if charge.Cost > charge.Rate.Burst {
			dimensions = append(dimensions, charge.Dimension)
			continue
		}
		if !found || bucket.Generation != generation {
			continue
		}
		bucket = refillEventRateBucket(bucket, charge.Rate, policy, now)
		if bucket.Tokens < charge.Cost*eventTokenScale {
			dimensions = append(dimensions, charge.Dimension)
		}
	}
	return dimensions, initialized, nil
}
func resetEventRatesAfterRestoreTx(ctx context.Context, tx pgx.Tx, generation int64) error {
	var exists bool
	if err := tx.QueryRow(ctx, `SELECT to_regclass('cairn.agent_event_rate_bucket') IS NOT NULL`).Scan(&exists); err != nil {
		return err
	}
	if !exists {
		return nil
	}
	_, err := tx.Exec(ctx, `UPDATE cairn.agent_event_rate_bucket SET tokens_nano=0,refill_at=clock_timestamp(),restore_generation=$1`, generation)
	return err
}

func (s *Store) chargeNewWaitRatesTx(ctx context.Context, tx pgx.Tx, repo string, agent AgentInstance) error {
	policy, err := admissionPolicyTx(ctx, tx, repo)
	if err != nil {
		return err
	}
	if policy.Policy.Mode != "enforce" {
		return nil
	}
	if !policy.EnforcementReady {
		return failure("FEATURE_UNAVAILABLE", "event admission enforcement requires every delivery writer")
	}
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return err
	}
	reconciled, err := closureReconciledTx(ctx, tx, repo, generation)
	if err != nil {
		return err
	}
	if !reconciled {
		return failure("CLOSURE_UNRECONCILED", "new wait admission waits for complete closure reconciliation in the current restore generation")
	}
	identity := eventProducerIdentity{Principal: "agent/" + agent.AgentID, Owner: agent.ProfileOwner}
	return chargeEventRatesTx(ctx, tx, repo, identity, policy, 1, 1)
}

func eventRateTablesPresentTx(ctx context.Context, tx pgx.Tx) (bool, error) {
	var exists bool
	err := tx.QueryRow(ctx, `SELECT to_regclass('cairn.agent_event_rate_bucket') IS NOT NULL`).Scan(&exists)
	return exists, err
}
