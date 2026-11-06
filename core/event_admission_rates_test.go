package core

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func TestEventRatesIntegerRefillAndPolicyTransition(t *testing.T) {
	at := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)
	policy := EventAdmissionPolicyState{Revision: 1, UpdatedAt: &at}
	bucket := eventRateBucket{RefillAt: at, PolicyRevision: 1}
	before := refillEventRateBucket(bucket, EventAdmissionRate{PerSecond: 3, Burst: 2}, policy, at.Add(333333333*time.Nanosecond))
	after := refillEventRateBucket(bucket, EventAdmissionRate{PerSecond: 3, Burst: 2}, policy, at.Add(333333334*time.Nanosecond))
	if before.Tokens != 999999999 || after.Tokens != 1000000002 {
		t.Fatalf("fractional refill rounded into extra token: %d %d", before.Tokens, after.Tokens)
	}
	huge := refillEventRateBucket(bucket, EventAdmissionRate{PerSecond: 1000000000, Burst: 1000000000}, policy, at.Add(100*365*24*time.Hour))
	if huge.Tokens != 1000000000000000000 {
		t.Fatalf("long refill overflow: %d", huge.Tokens)
	}
	future := refillEventRateBucket(eventRateBucket{Tokens: 1, RefillAt: at, PolicyRevision: 1}, EventAdmissionRate{1, 2}, policy, at.Add(-time.Hour))
	if future.Tokens != 1 || !future.RefillAt.Equal(at) {
		t.Fatalf("clock rewind minted tokens: %+v", future)
	}
	changedAt := at.Add(10 * time.Second)
	changed := EventAdmissionPolicyState{Revision: 5, UpdatedAt: &changedAt}
	clamped := refillEventRateBucket(eventRateBucket{Tokens: 3 * eventTokenScale, RefillAt: at, PolicyRevision: 1}, EventAdmissionRate{1, 1}, changed, changedAt)
	if clamped.Tokens != eventTokenScale {
		t.Fatalf("lowered burst did not clamp: %+v", clamped)
	}
	lazy := refillEventRateBucket(eventRateBucket{Tokens: eventTokenScale, RefillAt: at, PolicyRevision: 1}, EventAdmissionRate{1, 4}, changed, changedAt.Add(time.Second))
	if lazy.Tokens != 2*eventTokenScale {
		t.Fatalf("revision change banked old refill or reset burst: %+v", lazy)
	}
}

func testChargeRates(ctx context.Context, s *Store, repo string, identity eventProducerIdentity, policy EventAdmissionPolicyState, requestID string, publications, deliveries int64) error {
	_, err := mutate(ctx, s, "rate-fixture", requestID, struct{ Publications, Deliveries int64 }{publications, deliveries}, func(tx pgx.Tx) (bool, error) {
		if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
			return false, err
		}
		if err := chargeEventRatesTx(ctx, tx, repo, identity, policy, publications, deliveries); err != nil {
			return false, err
		}
		return true, nil
	})
	return err
}
func largeRatePolicy() EventAdmissionPolicy {
	policy := admissionFixturePolicy()
	rate := EventAdmissionRate{PerSecond: 1, Burst: 100}
	policy.Limits.ProducerPublications = rate
	policy.Limits.ProducerDeliveries = rate
	policy.Limits.OwnerPublications = rate
	policy.Limits.OwnerDeliveries = rate
	policy.Limits.CollectionPublications = rate
	policy.Limits.CollectionDeliveries = rate
	return policy
}
func TestEventRatesAtomicRollbackOwnerAndCommittedRetry(t *testing.T) {
	ctx := context.Background()
	a, _, source, _ := eventFixture(t)
	state := testAdmissionState(t, a, source.Scope.Repo)
	state.Policy = largeRatePolicy()
	state.Policy.Limits.OwnerPublications = EventAdmissionRate{1, 1}
	identity := eventProducerIdentity{Principal: "session-one", Owner: "validated-owner"}
	requestID := uuid.NewString()
	if err := testChargeRates(ctx, a, source.Scope.Repo, identity, state, requestID, 1, 1); err != nil {
		t.Fatal(err)
	}
	var tokens int64
	if err := a.pool.QueryRow(ctx, `SELECT tokens_nano FROM cairn.agent_event_rate_bucket WHERE repo=$1 AND identity_kind='owner' AND identity=$2 AND unit='publication'`, source.Scope.Repo, identity.Owner).Scan(&tokens); err != nil || tokens != 0 {
		t.Fatalf("owner debit: %d %v", tokens, err)
	}
	// Retrying a committed UUID must not evaluate or debit the now-empty budget.
	if err := testChargeRates(ctx, a, source.Scope.Repo, identity, state, requestID, 1, 1); err != nil {
		t.Fatalf("committed retry charged again: %v", err)
	}
	// A future refill timestamp is a clock-rewind fixture; it freezes this one
	// exhausted bucket without relying on scheduler timing or sleeping.
	if _, err := a.pool.Exec(ctx, `UPDATE cairn.agent_event_rate_bucket SET refill_at=clock_timestamp()+interval '1 hour' WHERE repo=$1 AND identity_kind='owner' AND identity=$2 AND unit='publication'`, source.Scope.Repo, identity.Owner); err != nil {
		t.Fatal(err)
	}
	identity.Principal = "fresh-session-two"
	err := testChargeRates(ctx, a, source.Scope.Repo, identity, state, uuid.NewString(), 1, 1)
	requireAdmissionDimension(t, err, "owner_publications")
	var fresh int64
	if err := a.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event_rate_bucket WHERE repo=$1 AND identity_kind='producer' AND identity=$2`, source.Scope.Repo, identity.Principal).Scan(&fresh); err != nil || fresh != 0 {
		t.Fatalf("partial producer debit survived refusal: %d %v", fresh, err)
	}
	var collection int64
	if err := a.pool.QueryRow(ctx, `SELECT tokens_nano FROM cairn.agent_event_rate_bucket WHERE repo=$1 AND identity_kind='collection' AND unit='publication'`, source.Scope.Repo).Scan(&collection); err != nil || collection != 99*eventTokenScale {
		t.Fatalf("refusal retained collection debit: %d %v", collection, err)
	}
}
func TestEventRatesFanoutExceedingBurstDoesNotSpinOrLeaveBuckets(t *testing.T) {
	ctx := context.Background()
	a, _, source, _ := eventFixture(t)
	state := testAdmissionState(t, a, source.Scope.Repo)
	state.Policy = largeRatePolicy()
	state.Policy.Limits.ProducerDeliveries = EventAdmissionRate{PerSecond: 1, Burst: 2}
	err := testChargeRates(ctx, a, source.Scope.Repo, eventProducerIdentity{Principal: "fanout-producer", Owner: "fanout-owner"}, state, uuid.NewString(), 1, 3)
	var refusal *Error
	if !errors.As(err, &refusal) || refusal.Admission == nil || refusal.Admission.Dimension != "producer_deliveries" || refusal.Admission.Retryable || refusal.Admission.RetryAfterSeconds != 0 {
		t.Fatalf("impossible burst advertised retry timer: %v", err)
	}
	var rows int64
	if err := a.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event_rate_bucket WHERE repo=$1`, source.Scope.Repo).Scan(&rows); err != nil || rows != 0 {
		t.Fatalf("rejected fanout left partial bucket state: %d %v", rows, err)
	}
}
func TestEventRatesConcurrentOwnerBudgetAndValidatedSessionIdentity(t *testing.T) {
	ctx := context.Background()
	profile, _, source, dest := eventFixture(t)
	state := testAdmissionState(t, profile, source.Scope.Repo)
	state.Policy = largeRatePolicy()
	state.Policy.Limits.OwnerPublications = EventAdmissionRate{1, 1}
	views := []*Store{}
	for i := 0; i < 2; i++ {
		agent, err := profile.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "rate-fixture", NativeSessionID: uuid.NewString(), Metadata: AgentMetadata{Harness: "codex", Project: "fixture", Workspace: "/fixture", State: "idle", DeliveryMode: "existing-session"}}, dest)
		if err != nil {
			t.Fatal(err)
		}
		view, err := profile.ForAgentSession(AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}, dest)
		if err != nil {
			t.Fatal(err)
		}
		views = append(views, view)
	}
	var wg sync.WaitGroup
	results := make(chan error, 2)
	for _, view := range views {
		wg.Add(1)
		go func(s *Store) {
			defer wg.Done()
			_, err := mutate(ctx, s, "session-rate-fixture", uuid.NewString(), struct{}{}, func(tx pgx.Tx) (bool, error) {
				if err := lock(ctx, tx, "agent-events:"+source.Scope.Repo); err != nil {
					return false, err
				}
				identity, err := s.eventProducerIdentityTx(ctx, tx)
				if err != nil {
					return false, err
				}
				if identity.Owner != profile.channel.Principal || identity.Principal == identity.Owner {
					return false, failure("INVALID_REQUEST", "session did not use registered profile owner")
				}
				err = chargeEventRatesTx(ctx, tx, source.Scope.Repo, identity, state, 1, 0)
				if err != nil {
					return false, err
				}
				_, err = tx.Exec(ctx, `UPDATE cairn.agent_event_rate_bucket SET refill_at=clock_timestamp()+interval '1 hour' WHERE repo=$1 AND identity_kind='owner' AND identity=$2 AND unit='publication'`, source.Scope.Repo, identity.Owner)
				return true, err
			})
			results <- err
		}(view)
	}
	wg.Wait()
	close(results)
	accepted, refused := 0, 0
	for err := range results {
		if err == nil {
			accepted++
		} else {
			requireAdmissionDimension(t, err, "owner_publications")
			refused++
		}
	}
	if accepted != 1 || refused != 1 {
		t.Fatalf("new sessions reset owner budget: %d accepted %d refused", accepted, refused)
	}
}
func TestEventRatesObserveDoesNotCreateBudgetsOrPretendForecast(t *testing.T) {
	ctx := context.Background()
	a, b, source, dest := eventFixture(t)
	state := testAdmissionState(t, a, source.Scope.Repo)
	publishFixture(t, a, b, source, dest)
	preview, err := a.PreviewEventAdmission(ctx, EventAdmissionPreviewRequest{Destination: EventDestination{Type: "topic", Name: "empty"}}, dest)
	if err != nil || preview.RateEvaluation != "uninitialized" {
		t.Fatalf("observe preview invented a retained balance: %+v %v", preview, err)
	}
	var rows int64
	if err = a.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event_rate_bucket WHERE repo=$1`, source.Scope.Repo).Scan(&rows); err != nil || rows != 0 {
		t.Fatalf("observe publication or preview mutated budgets: %d %v", rows, err)
	}
	if state.Policy.Mode != "observe" {
		t.Fatal("fixture did not preserve observe policy")
	}
}

func TestEventRatesReadOnlyPreviewUsesRetainedBalances(t *testing.T) {
	ctx := context.Background()
	a, _, source, dest := eventFixture(t)
	state := testAdmissionState(t, a, source.Scope.Repo)
	identity := eventProducerIdentity{Principal: a.channel.Principal, Owner: a.channel.Principal}
	if err := testChargeRates(ctx, a, source.Scope.Repo, identity, state, uuid.NewString(), 1, 0); err != nil {
		t.Fatal(err)
	}
	if _, err := a.pool.Exec(ctx, `UPDATE cairn.agent_event_rate_bucket SET tokens_nano=0,refill_at=clock_timestamp()+interval '1 hour' WHERE repo=$1 AND identity_kind='producer' AND unit='publication'`, source.Scope.Repo); err != nil {
		t.Fatal(err)
	}
	var before time.Time
	if err := a.pool.QueryRow(ctx, `SELECT refill_at FROM cairn.agent_event_rate_bucket WHERE repo=$1 AND identity_kind='producer' AND unit='publication'`, source.Scope.Repo).Scan(&before); err != nil {
		t.Fatal(err)
	}
	preview, err := a.PreviewEventAdmission(ctx, EventAdmissionPreviewRequest{Destination: EventDestination{Type: "topic", Name: "empty"}}, dest)
	if err != nil || preview.RateEvaluation != "retained_buckets" || len(preview.WouldRefuse) != 1 || preview.WouldRefuse[0] != "producer_publications" {
		t.Fatalf("retained rate forecast: %+v %v", preview, err)
	}
	var after time.Time
	var tokens int64
	if err := a.pool.QueryRow(ctx, `SELECT refill_at,tokens_nano FROM cairn.agent_event_rate_bucket WHERE repo=$1 AND identity_kind='producer' AND unit='publication'`, source.Scope.Repo).Scan(&after, &tokens); err != nil || !after.Equal(before) || tokens != 0 {
		t.Fatalf("preview changed budget: %d %v %v", tokens, after, err)
	}
}

func TestEventRatesPersistAcrossStoreRestartAndFence(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	repo := "rates-restore:" + uuid.NewString()
	state, err := s.SetEventAdmissionPolicy(ctx, SetEventAdmissionPolicyRequest{RequestID: uuid.NewString(), Repo: repo, Policy: largeRatePolicy()})
	if err != nil {
		t.Fatal(err)
	}
	identity := eventProducerIdentity{Principal: "restored-producer", Owner: "restored-owner"}
	if err = testChargeRates(ctx, s, repo, identity, state, uuid.NewString(), 1, 1); err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.agent_event_rate_bucket SET tokens_nano=0,refill_at=clock_timestamp()+interval '1 hour' WHERE repo=$1 AND identity_kind='producer' AND unit='publication'`, repo); err != nil {
		t.Fatal(err)
	}
	restartedPool, err := pgxpool.NewWithConfig(ctx, s.pool.Config())
	if err != nil {
		t.Fatal(err)
	}
	restarted := &Store{pool: restartedPool, channel: s.channel}
	t.Cleanup(restarted.Close)
	err = testChargeRates(ctx, restarted, repo, identity, state, uuid.NewString(), 1, 1)
	requireAdmissionDimension(t, err, "producer_publications")
	fenced, err := s.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Disposable rate restore-generation verification"})
	if err != nil {
		t.Fatal(err)
	}
	var rows, zeroed, current int64
	var anchor time.Time
	err = s.pool.QueryRow(ctx, `SELECT count(*),count(*) FILTER(WHERE tokens_nano=0),count(*) FILTER(WHERE restore_generation=$2),min(refill_at) FROM cairn.agent_event_rate_bucket WHERE repo=$1`, repo, fenced.Generation).Scan(&rows, &zeroed, &current, &anchor)
	if err != nil || rows != 6 || zeroed != rows || current != rows {
		t.Fatalf("fence did not zero restored budgets atomically: %d/%d/%d %v", rows, zeroed, current, err)
	}
	err = testChargeRates(ctx, restarted, repo, identity, state, uuid.NewString(), 1, 1)
	requireAdmissionDimension(t, err, "producer_publications")
	var retained time.Time
	if err = s.pool.QueryRow(ctx, `SELECT min(refill_at) FROM cairn.agent_event_rate_bucket WHERE repo=$1`, repo).Scan(&retained); err != nil || !retained.Equal(anchor) {
		t.Fatalf("rolled-back refusal moved restore refill anchor: %v %v", retained, err)
	}
	// One bounded wait verifies real database-time refill after a rejected call;
	// a lazy reset on each rejection would keep this budget empty forever.
	time.Sleep(1100 * time.Millisecond)
	if err = testChargeRates(ctx, restarted, repo, identity, state, uuid.NewString(), 1, 1); err != nil {
		t.Fatalf("restored budget never refilled after rejection: %v", err)
	}
}

func TestEventRatesRequireCompleteDatabaseSchemaBeforeEnforcement(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	repo := "rate-floor:" + uuid.NewString()
	state, err := s.SetEventAdmissionPolicy(ctx, SetEventAdmissionPolicyRequest{RequestID: uuid.NewString(), Repo: repo, Policy: largeRatePolicy()})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	source, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	// Migration084 contains the compatibility barrier and ledger marker only.
	// Removing its row in this isolated fixture represents an 083 database.
	if _, err = s.pool.Exec(ctx, `DELETE FROM public.cairn_migration WHERE version=84`); err != nil {
		t.Fatal(err)
	}
	inspected, err := s.EventAdmissionPolicy(ctx, EventAdmissionPolicyQuery{Repo: repo})
	if err != nil || inspected.EnforcementReady {
		t.Fatalf("partial database advertised enforcement readiness: %+v %v", inspected, err)
	}
	state.Policy.Mode = "enforce"
	_, err = s.SetEventAdmissionPolicy(ctx, SetEventAdmissionPolicyRequest{RequestID: uuid.NewString(), Repo: repo, ExpectedRevision: state.Revision, Policy: state.Policy})
	requireCode(t, err, "FEATURE_UNAVAILABLE")
	// Even an unsupported stored mode cannot bypass the schema floor.
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.agent_event_admission_policy SET policy=jsonb_set(policy,'{mode}','"enforce"') WHERE repo=$1`, repo); err != nil {
		t.Fatal(err)
	}
	_, err = s.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Repo: repo, Kind: "notice", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "topic", Name: "empty"}}, Destination{Name: "local", AllowLocal: true})
	requireCode(t, err, "FEATURE_UNAVAILABLE")
}
