package core

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

// These tests use waitTestDB's separately migrated disposable database. Triggers
// and lock gates belong to that database and never enter the production schema.
func adversarialWaitOperator(t *testing.T, openStore func(Channel) *Store) (*Store, Grant) {
	t.Helper()
	op := openStore(Channel{Principal: "operator:wait-adversarial", Operator: true})
	root, err := op.Bootstrap(context.Background(), BootstrapRequest{RequestID: uuid.NewString(), Reason: "Disposable wait failure-path fixture"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.SetCoordinationWaitsEnabled(context.Background(), WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatal(err)
	}
	return op, root
}

func adversarialResolvedWait(t *testing.T, ctx context.Context, openStore func(Channel) *Store) (AgentWait, AgentEvent, *Store, Destination) {
	t.Helper()
	_, _, a, b, owner, responder, dest := waitFixture(t, openStore)
	request := publishTestRequest(t, ctx, owner, a.Repo, b.AgentID, "", dest)
	wait, err := owner.CreateWait(ctx, CreateWaitRequest{
		RequestID: uuid.NewString(), Collection: a.Repo, PredicateKind: WaitPredicateRequestResponse,
		DependencyEventUUID: request.EventID, Deadline: time.Now().Add(time.Minute),
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	publishTestResponse(t, ctx, responder, a.Repo, a.AgentID, request.EventID, "", "response", dest)
	got, err := owner.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID, Collection: a.Repo}, dest)
	if err != nil || got.State != WaitStateSatisfied || got.OutboxState != OutboxStatePending {
		t.Fatalf("real response must resolve wait with pending notice: %+v %v", got, err)
	}
	return got, request, owner, dest
}

func adversarialNoticeCounts(t *testing.T, ctx context.Context, op *Store, wait AgentWait, expected int) {
	t.Helper()
	var records, versions, events, deliveries, allNotices, allSources int
	err := op.pool.QueryRow(ctx, `SELECT
 (SELECT count(*) FROM cairn.memory_record WHERE record_id=$1),
 (SELECT count(*) FROM cairn.record_version WHERE record_id=$1),
 (SELECT count(*) FROM cairn.agent_event WHERE event_id=$2 AND record_id=$1 AND version=1),
 (SELECT count(*) FROM cairn.agent_delivery WHERE event_id=$2 AND consumer=$3),
 (SELECT count(*) FROM cairn.agent_event WHERE repo=$4 AND kind='notice'),
 (SELECT count(*) FROM cairn.record_version WHERE repo=$4 AND task_id=$5)`,
		wait.ReservedSourceRecordUUID, wait.ReservedNoticeEventUUID, "agent/"+wait.OwnerAgentUUID,
		wait.Collection, "wait-"+wait.WaitUUID).Scan(&records, &versions, &events, &deliveries, &allNotices, &allSources)
	if err != nil {
		t.Fatal(err)
	}
	if records != expected || versions != expected || events != expected || deliveries != expected || allNotices != expected || allSources != expected {
		t.Fatalf("reserved artifacts and collection totals: want each %d; record=%d version=%d event=%d delivery=%d notices=%d scoped_versions=%d",
			expected, records, versions, events, deliveries, allNotices, allSources)
	}
}

func TestWaitAdversarialOutboxRollbackReopen(t *testing.T) {
	_, openStore := waitTestDB(t)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	op, _ := adversarialWaitOperator(t, openStore)
	wait, _, owner, dest := adversarialResolvedWait(t, ctx, openStore)

	// The failing INSERT itself verifies that source and version have already
	// been inserted in its transaction. A distinct exception catches a bad gate.
	_, err := op.pool.Exec(ctx, `CREATE FUNCTION cairn.wait_test_fail_reserved_notice() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.event_id::text = TG_ARGV[0] THEN
  IF NOT EXISTS (SELECT 1 FROM cairn.memory_record m JOIN cairn.record_version v USING(record_id)
                 WHERE m.record_id=NEW.record_id AND v.version=NEW.version) THEN
   RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='wait_test_source_missing_before_event';
  END IF;
  RAISE EXCEPTION USING ERRCODE='P0001', MESSAGE='wait_test_reserved_event_insert_failure';
 END IF;
 RETURN NEW;
END $$`)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.pool.Exec(ctx, fmt.Sprintf(`CREATE TRIGGER wait_test_reserved_notice_failure BEFORE INSERT ON cairn.agent_event
FOR EACH ROW EXECUTE FUNCTION cairn.wait_test_fail_reserved_notice('%s')`, wait.ReservedNoticeEventUUID))
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.DrainWaitOutbox(ctx, wait.Collection, 50)
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "P0001" || pgErr.Message != "wait_test_reserved_event_insert_failure" {
		t.Fatalf("must reach deliberate event failure after source insertion: %v", err)
	}
	adversarialNoticeCounts(t, ctx, op, wait, 0)
	got, err := owner.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID, Collection: wait.Collection}, dest)
	if err != nil || got.State != WaitStateSatisfied || got.OutboxState != OutboxStatePending {
		t.Fatalf("failed publication must retain resolved wait and pending outbox: %+v %v", got, err)
	}
	if _, err = op.pool.Exec(ctx, `DROP TRIGGER wait_test_reserved_notice_failure ON cairn.agent_event; DROP FUNCTION cairn.wait_test_fail_reserved_notice()`); err != nil {
		t.Fatal(err)
	}
	op.Close()
	reopened := openStore(op.channel)
	n, err := reopened.DrainWaitOutbox(ctx, wait.Collection, 50)
	if err != nil || n != 1 {
		t.Fatalf("reopened notifier retry must publish one reserved notice: %d %v", n, err)
	}
	adversarialNoticeCounts(t, ctx, reopened, wait, 1)
	n, err = reopened.DrainWaitOutbox(ctx, wait.Collection, 50)
	if err != nil || n != 0 {
		t.Fatalf("committed outbox retry must publish nothing: %d %v", n, err)
	}
	adversarialNoticeCounts(t, ctx, reopened, wait, 1)
}

func adversarialNamedStore(t *testing.T, ctx context.Context, base *Store, name string) *Store {
	t.Helper()
	config := base.pool.Config()
	config.ConnConfig.RuntimeParams["application_name"] = name
	config.MaxConns = 1
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	s := &Store{pool: pool, channel: base.channel}
	t.Cleanup(s.Close)
	return s
}

func adversarialWaitFor(t *testing.T, ctx context.Context, predicate func() (bool, error), description string) {
	t.Helper()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		ready, err := predicate()
		if err != nil {
			t.Fatalf("%s: %v", description, err)
		}
		if ready {
			return
		}
		select {
		case <-ctx.Done():
			t.Fatalf("%s: %v", description, ctx.Err())
		case <-ticker.C:
		}
	}
}

func TestWaitAdversarialNotifierForgetSerialization(t *testing.T) {
	for _, forgetFirst := range []bool{true, false} {
		name := "publication_gate"
		if forgetFirst {
			name = "forget_first"
		}
		t.Run(name, func(t *testing.T) {
			_, openStore := waitTestDB(t)
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			op, root := adversarialWaitOperator(t, openStore)
			wait, request, owner, dest := adversarialResolvedWait(t, ctx, openStore)
			preview, err := op.PreviewDeletion(ctx, request.Ref.RecordID)
			if err != nil {
				t.Fatal(err)
			}
			forget := ForgetRequest{RequestID: uuid.NewString(), RecordID: request.Ref.RecordID,
				ExpectedVersion: request.Ref.Version, GrantID: root.ID, PreviewID: preview.PreviewID}
			if forgetFirst {
				if _, err = op.Forget(ctx, forget); err != nil {
					t.Fatal(err)
				}
				n, err := op.DrainWaitOutbox(ctx, wait.Collection, 50)
				if err != nil || n != 0 {
					t.Fatalf("forgotten dependency must not publish: %d %v", n, err)
				}
				got, err := owner.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID, Collection: wait.Collection}, dest)
				if err != nil || got.State != WaitStateSatisfied || got.OutboxState != OutboxStateRestricted || got.OutboxErrorReason != "source_ineligible" {
					t.Fatalf("forget winner must retain satisfaction and restrict notification: %+v %v", got, err)
				}
				adversarialNoticeCounts(t, ctx, op, wait, 0)
				return
			}

			gate, err := op.pool.Begin(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer gate.Rollback(context.Background())
			if _, err = gate.Exec(ctx, `LOCK TABLE cairn.agent_event IN SHARE MODE`); err != nil {
				t.Fatal(err)
			}
			drainName := "wait-drain-" + uuid.NewString()
			forgetName := "wait-forget-" + uuid.NewString()
			drainer := adversarialNamedStore(t, ctx, op, drainName)
			forgetter := adversarialNamedStore(t, ctx, op, forgetName)
			type drainResult struct {
				count int64
				err   error
			}
			drainDone := make(chan drainResult, 1)
			go func() {
				n, err := drainer.DrainWaitOutbox(ctx, wait.Collection, 50)
				drainDone <- drainResult{n, err}
			}()
			adversarialWaitFor(t, ctx, func() (bool, error) {
				var blocked bool
				err := op.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_locks l JOIN pg_stat_activity a USING(pid)
WHERE a.application_name=$1 AND l.relation='cairn.agent_event'::regclass
AND l.mode='RowExclusiveLock' AND NOT l.granted)`, drainName).Scan(&blocked)
				return blocked, err
			}, "notifier must reach reserved event INSERT after source eligibility checks")

			forgetDone := make(chan error, 1)
			go func() { _, err := forgetter.Forget(ctx, forget); forgetDone <- err }()
			forgotBeforePublication := false
			adversarialWaitFor(t, ctx, func() (bool, error) {
				select {
				case err := <-forgetDone:
					forgotBeforePublication = true
					return true, err
				default:
				}
				var blocked bool
				err := op.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity f JOIN pg_stat_activity d
ON d.pid=ANY(pg_blocking_pids(f.pid)) WHERE f.application_name=$1 AND d.application_name=$2)`, forgetName, drainName).Scan(&blocked)
				return blocked, err
			}, "Forget must either commit first or block on notifier's selected-source lock")
			if err = gate.Commit(ctx); err != nil {
				t.Fatal(err)
			}
			var result drainResult
			select {
			case result = <-drainDone:
			case <-ctx.Done():
				t.Fatal(ctx.Err())
			}
			if !forgotBeforePublication {
				select {
				case err = <-forgetDone:
					if err != nil {
						t.Fatal(err)
					}
				case <-ctx.Done():
					t.Fatal(ctx.Err())
				}
			}
			if result.err != nil {
				t.Fatal(result.err)
			}
			if forgotBeforePublication {
				if result.count != 0 {
					t.Fatalf("Forget committed while notifier was gated, but notifier subsequently published %d forbidden notice(s)", result.count)
				}
				adversarialNoticeCounts(t, ctx, op, wait, 0)
			} else {
				if result.count != 1 {
					t.Fatalf("notifier held source eligibility through publication; expected one notice, got %d", result.count)
				}
				adversarialNoticeCounts(t, ctx, op, wait, 1)
			}
			_, err = owner.History(ctx, RecordHistoryRequest{RecordID: request.Ref.RecordID, Repo: wait.Collection, Version: request.Ref.Version}, dest)
			requireCode(t, err, "PAYLOAD_UNAVAILABLE")
		})
	}
}

func TestWaitAdversarialExpiredGroupReconciliation(t *testing.T) {
	for _, backlog := range []int{0, 100} {
		t.Run(fmt.Sprintf("preceding_groups_%d", backlog), func(t *testing.T) {
			_, openStore := waitTestDB(t)
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			op, _ := adversarialWaitOperator(t, openStore)
			_, _, a, b, owner, _, dest := waitFixture(t, openStore)
			draft := projectNote(a.Repo)
			draft.Sensitivity = "shareable"
			source, err := owner.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
			if err != nil {
				t.Fatal(err)
			}
			var now time.Time
			if err = op.pool.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&now); err != nil {
				t.Fatal(err)
			}
			groupDeadline := now.Add(5 * time.Second)
			var target AgentEvent
			for i := 0; i <= backlog; i++ {
				deadline := groupDeadline.Add(-100 * time.Millisecond)
				if i == backlog {
					deadline = groupDeadline
				}
				target, err = owner.PublishEvent(ctx, PublishEventRequest{
					RequestID: uuid.NewString(), Repo: a.Repo, Kind: "request",
					Ref:           RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
					Destination:   EventDestination{Type: "agent", Name: "agent/" + b.AgentID},
					ResponseGroup: &ResponseGroupSpec{Deadline: deadline, PartialPolicy: "all"},
				}, dest)
				if err != nil {
					t.Fatalf("create real group %d: %v", i, err)
				}
			}
			waitDeadline := groupDeadline.Add(100 * time.Millisecond)
			wait, err := owner.CreateWait(ctx, CreateWaitRequest{
				RequestID: uuid.NewString(), Collection: a.Repo, PredicateKind: WaitPredicateResponseGroupClosed,
				DependencyEventUUID: target.EventID, Deadline: waitDeadline,
			}, dest)
			if err != nil || wait.State != WaitStateWaiting {
				t.Fatalf("register real future group wait: %+v %v", wait, err)
			}
			adversarialWaitFor(t, ctx, func() (bool, error) {
				var overdue bool
				err := op.pool.QueryRow(ctx, `SELECT clock_timestamp()>$1::timestamptz`, waitDeadline).Scan(&overdue)
				return overdue, err
			}, "both real persisted deadlines must pass")
			if backlog > 0 {
				result, err := op.SweepRequestControls(ctx, RequestControlSweep{Repo: a.Repo})
				if err != nil || result.ResponseGroups != int64(backlog) {
					t.Fatalf("preceding control sweep must consume exactly its group page: %+v %v", result, err)
				}
			}
			var before string
			if err = op.pool.QueryRow(ctx, `SELECT state FROM cairn.agent_response_group WHERE event_id=$1`, target.EventID).Scan(&before); err != nil || before != "open" {
				t.Fatalf("fixture must leave target group unclosed before wait reconciliation: %s %v", before, err)
			}
			if _, err = op.SweepWaitTimeouts(ctx, a.Repo); err != nil {
				t.Fatal(err)
			}
			got, err := owner.GetWait(ctx, GetWaitRequest{WaitUUID: wait.WaitUUID, Collection: a.Repo}, dest)
			if err != nil || got.State != WaitStateSatisfied || got.TerminalReason != "group_closed" {
				t.Fatalf("timely group deadline must satisfy after downtime, including bounded backlog: %+v %v", got, err)
			}
			var groupState, groupCode string
			if err = op.pool.QueryRow(ctx, `SELECT state,code FROM cairn.agent_response_group WHERE event_id=$1`, target.EventID).Scan(&groupState, &groupCode); err != nil || groupState != "incomplete" || groupCode != "deadline" {
				t.Fatalf("normal deadline closure must be retained: %s/%s %v", groupState, groupCode, err)
			}
			n, err := op.DrainWaitOutbox(ctx, a.Repo, 50)
			if err != nil || n != 1 {
				t.Fatalf("group reconciliation must publish one reserved notice: %d %v", n, err)
			}
			adversarialNoticeCounts(t, ctx, op, wait, 1)
		})
	}
}
