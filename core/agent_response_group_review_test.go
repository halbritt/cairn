package core

import (
	"context"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// reviewDatabase creates a completely isolated disposable database on the test cluster,
// separate from parent test databases (cairn_group_tests_047 and cairn_group_runtime_047).
func reviewDatabase(t *testing.T) (*Store, func(Channel) *Store) {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("PostgreSQL integration test: run make test-integration")
	}
	ctx := context.Background()
	host := testStore(t, Channel{Principal: "operator:review-host", Operator: true})

	dbName := "cairn_group_rev_" + strings.ReplaceAll(uuid.NewString(), "-", "")[:16]
	if _, err := host.pool.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{dbName}.Sanitize()); err != nil {
		t.Fatalf("create review db %s: %v", dbName, err)
	}
	t.Cleanup(func() {
		if _, err := host.pool.Exec(context.Background(), "DROP DATABASE IF EXISTS "+pgx.Identifier{dbName}.Sanitize()+" WITH (FORCE)"); err != nil {
			t.Errorf("drop review db %s: %v", dbName, err)
		}
	})

	poolConfig, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	poolConfig.ConnConfig.Database = dbName

	openStore := func(ch Channel) *Store {
		pool, err := pgxpool.NewWithConfig(ctx, poolConfig)
		if err != nil {
			t.Fatal(err)
		}
		s := &Store{pool: pool, channel: ch}
		t.Cleanup(s.Close)
		return s
	}

	bootstrap := openStore(Channel{Principal: "operator:bootstrap", Operator: true})
	if err := bootstrap.Migrate(ctx); err != nil {
		t.Fatalf("migrate review db %s: %v", dbName, err)
	}
	return bootstrap, openStore
}

func reviewEventFixture(t *testing.T, openStore func(Channel) *Store) (*Store, *Store, Record, Destination) {
	t.Helper()
	ctx := context.Background()
	repo := "repo:" + uuid.NewString()
	a := openStore(Channel{Principal: "agent:publisher-" + repo, Repo: repo})
	b := openStore(Channel{Principal: "agent:worker-" + repo, Repo: repo})
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	record, err := a.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	dest := Destination{Name: "local", AllowLocal: true}
	return a, b, record, dest
}

// 1. Meaningful simultaneous duplicate / distinct-recipient response races (barrier, disposable DB).
func TestReviewSimultaneousDuplicateAndDistinctResponseRaces(t *testing.T) {
	_, openStore := reviewDatabase(t)
	ctx := context.Background()

	t.Run("ConcurrentDuplicatesFromSameRecipient", func(t *testing.T) {
		a, b, r, dest := reviewEventFixture(t, openStore)

		// Publish request event with response group targeting worker b.
		reqEvent, err := a.PublishEvent(ctx, PublishEventRequest{
			RequestID:   uuid.NewString(),
			Kind:        "request",
			Ref:         RecordVersionRef{RecordID: r.RecordID, Version: 1},
			Destination: EventDestination{Type: "agent", Name: b.channel.Principal},
			ResponseGroup: &ResponseGroupSpec{
				Deadline:      time.Now().Add(time.Hour),
				PartialPolicy: "all",
			},
		}, dest)
		if err != nil {
			t.Fatal(err)
		}

		// 5 concurrent workers attempt to publish duplicate responses simultaneously.
		const workers = 5
		start := make(chan struct{})
		var wg sync.WaitGroup
		type result struct {
			event AgentEvent
			err   error
		}
		results := make([]result, workers)

		for i := 0; i < workers; i++ {
			wg.Add(1)
			go func(idx int) {
				defer wg.Done()
				<-start
				ev, err := b.PublishEvent(ctx, PublishEventRequest{
					RequestID:     uuid.NewString(),
					Kind:          "response",
					Ref:           RecordVersionRef{RecordID: r.RecordID, Version: 1},
					Destination:   EventDestination{Type: "agent", Name: a.channel.Principal},
					CausationID:   reqEvent.EventID,
					CorrelationID: reqEvent.CorrelationID,
				}, dest)
				results[idx] = result{event: ev, err: err}
			}(i)
		}

		close(start)
		wg.Wait()

		for i, res := range results {
			if res.err != nil {
				t.Fatalf("worker %d publish failed: %v", i, res.err)
			}
		}

		// Check response group state.
		group, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, dest)
		if err != nil {
			t.Fatal(err)
		}
		if group.State != "collected" || group.Expected != 1 || group.Responded != 1 {
			t.Fatalf("unexpected group state: %+v", group)
		}
		if len(group.Members) != 1 || !group.Members[0].Responded {
			t.Fatalf("member not responded: %+v", group.Members)
		}
		winningResponseID := group.Members[0].ResponseEventID
		if winningResponseID == "" {
			t.Fatal("expected winning response event ID on member")
		}

		// Exactly 1 responded disposition, 4 duplicate dispositions.
		if len(group.Observations) != workers {
			t.Fatalf("expected %d observations, got %d", workers, len(group.Observations))
		}
		respondedCount := 0
		duplicateCount := 0
		for _, obs := range group.Observations {
			switch obs.Disposition {
			case "responded":
				respondedCount++
				if obs.EventID != winningResponseID {
					t.Fatalf("responded observation ID %s != winning ID %s", obs.EventID, winningResponseID)
				}
			case "duplicate":
				duplicateCount++
			default:
				t.Fatalf("unexpected observation disposition: %s", obs.Disposition)
			}
		}
		if respondedCount != 1 || duplicateCount != workers-1 {
			t.Fatalf("disposition count mismatch: responded=%d, duplicate=%d", respondedCount, duplicateCount)
		}
	})

	t.Run("ConcurrentDistinctRecipientsOnTopic", func(t *testing.T) {
		a, _, r, dest := reviewEventFixture(t, openStore)
		topicName := "concurrent-topic-" + uuid.NewString()

		const subscriberCount = 4
		subscribers := make([]*Store, subscriberCount)
		for i := 0; i < subscriberCount; i++ {
			s := openStore(Channel{Principal: uuid.NewString(), Repo: r.Scope.Repo})
			if _, err := s.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: topicName, Active: true}); err != nil {
				t.Fatal(err)
			}
			subscribers[i] = s
		}

		reqEvent, err := a.PublishEvent(ctx, PublishEventRequest{
			RequestID:   uuid.NewString(),
			Kind:        "request",
			Ref:         RecordVersionRef{RecordID: r.RecordID, Version: 1},
			Destination: EventDestination{Type: "topic", Name: topicName},
			ResponseGroup: &ResponseGroupSpec{
				Deadline:      time.Now().Add(time.Hour),
				PartialPolicy: "all",
			},
		}, dest)
		if err != nil {
			t.Fatal(err)
		}

		// Verify initial group.
		group, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, dest)
		if err != nil || group.Expected != subscriberCount || group.Responded != 0 || group.State != "open" {
			t.Fatalf("initial group: %+v %v", group, err)
		}

		// All 4 distinct subscribers reply simultaneously via barrier.
		start := make(chan struct{})
		var wg sync.WaitGroup
		errs := make([]error, subscriberCount)
		for i := 0; i < subscriberCount; i++ {
			wg.Add(1)
			go func(idx int) {
				defer wg.Done()
				<-start
				_, errs[idx] = subscribers[idx].PublishEvent(ctx, PublishEventRequest{
					RequestID:     uuid.NewString(),
					Kind:          "response",
					Ref:           RecordVersionRef{RecordID: r.RecordID, Version: 1},
					Destination:   EventDestination{Type: "agent", Name: a.channel.Principal},
					CausationID:   reqEvent.EventID,
					CorrelationID: reqEvent.CorrelationID,
				}, dest)
			}(i)
		}

		close(start)
		wg.Wait()

		for i, err := range errs {
			if err != nil {
				t.Fatalf("subscriber %d reply failed: %v", i, err)
			}
		}

		// Final response group check: fully collected, all 4 responded.
		group, err = a.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, dest)
		if err != nil || group.State != "collected" || group.Responded != subscriberCount || group.Expected != subscriberCount || group.Code != "all_responded" {
			t.Fatalf("final group after distinct concurrent replies: %+v %v", group, err)
		}
		for _, m := range group.Members {
			if !m.Responded || m.ResponseEventID == "" {
				t.Fatalf("member not completed: %+v", m)
			}
		}
		if len(group.Observations) != subscriberCount {
			t.Fatalf("expected %d observations, got %d", subscriberCount, len(group.Observations))
		}
		for _, obs := range group.Observations {
			if obs.Disposition != "responded" {
				t.Fatalf("unexpected observation disposition: %+v", obs)
			}
		}
	})

	t.Run("ConcurrentReplyVsDeadlineSweep", func(t *testing.T) {
		a, b, r, dest := reviewEventFixture(t, openStore)

		reqEvent, err := a.PublishEvent(ctx, PublishEventRequest{
			RequestID:   uuid.NewString(),
			Kind:        "request",
			Ref:         RecordVersionRef{RecordID: r.RecordID, Version: 1},
			Destination: EventDestination{Type: "agent", Name: b.channel.Principal},
			ResponseGroup: &ResponseGroupSpec{
				Deadline:      time.Now().Add(time.Hour),
				PartialPolicy: "all",
			},
		}, dest)
		if err != nil {
			t.Fatal(err)
		}

		// Move deadline to past.
		if _, err = a.pool.Exec(ctx, `UPDATE cairn.agent_response_group SET deadline=clock_timestamp()-interval '10 milliseconds' WHERE event_id=$1`, reqEvent.EventID); err != nil {
			t.Fatal(err)
		}

		op := openStore(Channel{Principal: "operator:sweep", Operator: true})

		start := make(chan struct{})
		var wg sync.WaitGroup
		var sweepErr, replyErr error

		wg.Add(2)
		go func() {
			defer wg.Done()
			<-start
			_, sweepErr = op.SweepRequestControls(ctx, RequestControlSweep{Repo: r.Scope.Repo})
		}()
		go func() {
			defer wg.Done()
			<-start
			_, replyErr = b.PublishEvent(ctx, PublishEventRequest{
				RequestID:     uuid.NewString(),
				Kind:          "response",
				Ref:           RecordVersionRef{RecordID: r.RecordID, Version: 1},
				Destination:   EventDestination{Type: "agent", Name: a.channel.Principal},
				CausationID:   reqEvent.EventID,
				CorrelationID: reqEvent.CorrelationID,
			}, dest)
		}()

		close(start)
		wg.Wait()

		if sweepErr != nil {
			t.Fatalf("sweep error: %v", sweepErr)
		}
		if replyErr != nil {
			t.Fatalf("reply error: %v", replyErr)
		}

		group, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, dest)
		if err != nil {
			t.Fatal(err)
		}

		// Group must be in a valid terminal closed state:
		// Either sweep expired it (state: "incomplete", code: "deadline", observation: "late")
		// OR reply committed before sweep evaluated expiry (state: "collected", code: "all_responded", observation: "responded").
		// State can NEVER be "open", and observations must match the disposition.
		if group.State == "open" || group.ClosedAt == nil {
			t.Fatalf("group remained open after sweep/reply race: %+v", group)
		}
		if group.State == "collected" {
			if group.Responded != 1 || group.Observations[0].Disposition != "responded" {
				t.Fatalf("inconsistent collected state: %+v", group)
			}
		} else if group.State == "incomplete" {
			if group.Responded != 0 || group.Observations[0].Disposition != "late" {
				t.Fatalf("inconsistent incomplete state: %+v", group)
			}
		} else {
			t.Fatalf("unexpected group state: %s", group.State)
		}
	})
}

// 2. Injected response/group transition failure proving event/fanout/member/observation rollback.
func TestReviewInjectedResponseTransitionRollback(t *testing.T) {
	_, openStore := reviewDatabase(t)
	ctx := context.Background()
	a, b, r, dest := reviewEventFixture(t, openStore)

	reqEvent, err := a.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Kind:        "request",
		Ref:         RecordVersionRef{RecordID: r.RecordID, Version: 1},
		Destination: EventDestination{Type: "agent", Name: b.channel.Principal},
		ResponseGroup: &ResponseGroupSpec{
			Deadline:      time.Now().Add(time.Hour),
			PartialPolicy: "all",
		},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}

	// Install a trigger that injects a failure during response observation insertion.
	injectSQL := `
CREATE OR REPLACE FUNCTION cairn.test_fail_observation() RETURNS trigger AS $$
BEGIN
	RAISE EXCEPTION 'injected_group_observation_failure';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER inject_review_fail
BEFORE INSERT ON cairn.agent_response_observation
FOR EACH ROW EXECUTE FUNCTION cairn.test_fail_observation();
`
	if _, err = a.pool.Exec(ctx, injectSQL); err != nil {
		t.Fatal(err)
	}

	replyReq := PublishEventRequest{
		RequestID:     uuid.NewString(),
		Kind:          "response",
		Ref:           RecordVersionRef{RecordID: r.RecordID, Version: 1},
		Destination:   EventDestination{Type: "agent", Name: a.channel.Principal},
		CausationID:   reqEvent.EventID,
		CorrelationID: reqEvent.CorrelationID,
	}

	// Attempting publication must fail due to the injected trigger.
	_, err = b.PublishEvent(ctx, replyReq, dest)
	if err == nil || !strings.Contains(err.Error(), "injected_group_observation_failure") {
		t.Fatalf("expected injected failure, got: %v", err)
	}

	// PROVE COMPLETE ROLLBACK:
	// 1. No response event was stored in cairn.agent_event for this causation.
	var eventCount int
	if err = a.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_event WHERE causation_id=$1`, reqEvent.EventID).Scan(&eventCount); err != nil {
		t.Fatal(err)
	}
	if eventCount != 0 {
		t.Fatalf("rollback leak: %d events found in agent_event", eventCount)
	}

	// 2. No response deliveries were created.
	var delivCount int
	if err = a.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.causation_id=$1`, reqEvent.EventID).Scan(&delivCount); err != nil {
		t.Fatal(err)
	}
	if delivCount != 0 {
		t.Fatalf("rollback leak: %d deliveries found in agent_delivery", delivCount)
	}

	// 3. Member response_event_id is still NULL.
	var respEventID *string
	if err = a.pool.QueryRow(ctx, `SELECT response_event_id FROM cairn.agent_response_group_member WHERE event_id=$1`, reqEvent.EventID).Scan(&respEventID); err != nil {
		t.Fatal(err)
	}
	if respEventID != nil {
		t.Fatalf("rollback leak: member response_event_id was updated to %v", *respEventID)
	}

	// 4. Response group is still open.
	group, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, dest)
	if err != nil || group.State != "open" || group.Responded != 0 {
		t.Fatalf("group corrupted after failed response: %+v %v", group, err)
	}

	// 5. No observations recorded.
	var obsCount int
	if err = a.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_response_observation WHERE group_event_id=$1`, reqEvent.EventID).Scan(&obsCount); err != nil {
		t.Fatal(err)
	}
	if obsCount != 0 {
		t.Fatalf("rollback leak: %d observations found", obsCount)
	}

	// Remove trigger and retry publication: must succeed atomically.
	if _, err = a.pool.Exec(ctx, `DROP TRIGGER inject_review_fail ON cairn.agent_response_observation; DROP FUNCTION cairn.test_fail_observation;`); err != nil {
		t.Fatal(err)
	}

	replyReq.RequestID = uuid.NewString()
	replyEv, err := b.PublishEvent(ctx, replyReq, dest)
	if err != nil {
		t.Fatalf("retry after removing trigger failed: %v", err)
	}

	group, err = a.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, dest)
	if err != nil || group.State != "collected" || group.Responded != 1 || group.Members[0].ResponseEventID != replyEv.EventID {
		t.Fatalf("expected successful collection after retry: %+v %v", group, err)
	}
}

// 3. Exact publication retry after changed topic membership.
func TestReviewPublicationRetryAfterChangedTopicMembership(t *testing.T) {
	_, openStore := reviewDatabase(t)
	ctx := context.Background()
	a, _, r, dest := reviewEventFixture(t, openStore)

	topic := "review-membership-" + uuid.NewString()

	// Initial membership: Subscriber S1.
	s1 := openStore(Channel{Principal: "agent:s1-" + uuid.NewString(), Repo: r.Scope.Repo})
	if _, err := s1.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: topic, Active: true}); err != nil {
		t.Fatal(err)
	}

	reqID := uuid.NewString()
	pubReq := PublishEventRequest{
		RequestID:   reqID,
		Kind:        "request",
		Ref:         RecordVersionRef{RecordID: r.RecordID, Version: 1},
		Destination: EventDestination{Type: "topic", Name: topic},
		ResponseGroup: &ResponseGroupSpec{
			Deadline:      time.Now().Add(time.Hour),
			PartialPolicy: "all",
		},
	}

	// First publication with S1 active.
	firstEvent, err := a.PublishEvent(ctx, pubReq, dest)
	if err != nil {
		t.Fatal(err)
	}

	group, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: firstEvent.EventID}, dest)
	if err != nil || group.Expected != 1 || len(group.Members) != 1 || group.Members[0].Consumer != s1.channel.Principal {
		t.Fatalf("initial group snapshot mismatch: %+v %v", group, err)
	}

	// Topic membership changes drastically:
	// S1 unsubscribes; S2 and S3 subscribe.
	s2 := openStore(Channel{Principal: "agent:s2-" + uuid.NewString(), Repo: r.Scope.Repo})
	s3 := openStore(Channel{Principal: "agent:s3-" + uuid.NewString(), Repo: r.Scope.Repo})
	if _, err = s1.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: topic, Active: false}); err != nil {
		t.Fatal(err)
	}
	if _, err = s2.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: topic, Active: true}); err != nil {
		t.Fatal(err)
	}
	if _, err = s3.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: topic, Active: true}); err != nil {
		t.Fatal(err)
	}

	// Exact publication retry using identical RequestID.
	retryEvent, err := a.PublishEvent(ctx, pubReq, dest)
	if err != nil {
		t.Fatalf("exact publication retry failed: %v", err)
	}
	if retryEvent.EventID != firstEvent.EventID {
		t.Fatalf("event ID mismatch on retry: got %s, want %s", retryEvent.EventID, firstEvent.EventID)
	}

	// Group membership must NOT change: still exactly S1, expected=1.
	groupAfterRetry, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: firstEvent.EventID}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if groupAfterRetry.Expected != 1 || len(groupAfterRetry.Members) != 1 || groupAfterRetry.Members[0].Consumer != s1.channel.Principal {
		t.Fatalf("retry modified response group members: %+v", groupAfterRetry)
	}

	// Verify deliveries: S2 and S3 must NOT have received any delivery for firstEvent.
	for _, s := range []*Store{s2, s3} {
		next, err := s.NextEvent(ctx, NextEventRequest{Repo: r.Scope.Repo}, dest)
		if err != nil {
			t.Fatal(err)
		}
		if next.Delivery != nil && next.Delivery.Event.EventID == firstEvent.EventID {
			t.Fatalf("unexpected delivery of cached event to new subscriber %s", s.channel.Principal)
		}
	}

	// Resubmitting same RequestID with altered payload must be rejected.
	conflictingReq := pubReq
	conflictingReq.Destination = EventDestination{Type: "agent", Name: s2.channel.Principal}
	_, err = a.PublishEvent(ctx, conflictingReq, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")

	// Publishing with a NEW RequestID under changed membership creates a new group.
	newPubReq := pubReq
	newPubReq.RequestID = uuid.NewString()
	secondEvent, err := a.PublishEvent(ctx, newPubReq, dest)
	if err != nil {
		t.Fatal(err)
	}
	secondGroup, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: secondEvent.EventID}, dest)
	if err != nil || secondGroup.Expected != 2 || len(secondGroup.Members) != 2 {
		t.Fatalf("second group should have 2 members (S2, S3): %+v %v", secondGroup, err)
	}
}

// 4. Privacy, owner, and session isolation cases.
func TestReviewPrivacyOwnerAndSessionIsolation(t *testing.T) {
	_, openStore := reviewDatabase(t)
	ctx := context.Background()

	repoA := "repo:iso-a-" + uuid.NewString()
	repoB := "repo:iso-b-" + uuid.NewString()

	// Session owner for publisher.
	publisherSessionID := uuid.NewString()
	publisherPrincipal := "agent/" + publisherSessionID
	a := openStore(Channel{Principal: publisherPrincipal, Repo: repoA})
	workerPrincipal := "agent:worker-" + uuid.NewString()
	b := openStore(Channel{Principal: workerPrincipal, Repo: repoA})

	// Draft created with shareable sensitivity.
	draft := projectNote(repoA)
	draft.Sensitivity = "shareable"
	r, err := a.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}

	destLocal := Destination{Name: "local", AllowLocal: true}
	destHosted := Destination{Name: "hosted", AllowLocal: false}

	reqEvent, err := a.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Kind:        "request",
		Ref:         RecordVersionRef{RecordID: r.RecordID, Version: 1},
		Destination: EventDestination{Type: "agent", Name: workerPrincipal},
		ResponseGroup: &ResponseGroupSpec{
			Deadline:      time.Now().Add(time.Hour),
			PartialPolicy: "all",
		},
	}, destLocal)
	if err != nil {
		t.Fatal(err)
	}

	// 1. Recipient worker cannot query owner's response group.
	_, err = b.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, destLocal)
	requireCode(t, err, "NOT_FOUND")

	// 2. Unrelated third-party agent cannot query owner's response group.
	thirdParty := openStore(Channel{Principal: "agent:unrelated-" + uuid.NewString(), Repo: repoA})
	_, err = thirdParty.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, destLocal)
	requireCode(t, err, "NOT_FOUND")

	// 3. Different session cannot query owner's response group.
	otherSession := openStore(Channel{Principal: "agent/" + uuid.NewString(), Repo: repoA})
	_, err = otherSession.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, destLocal)
	requireCode(t, err, "NOT_FOUND")

	// 4. Listing groups from third party yields 0 groups.
	listThird, err := thirdParty.ResponseGroups(ctx, ResponseGroupListRequest{Repo: repoA}, destLocal)
	if err != nil || len(listThird.Groups) != 0 {
		t.Fatalf("third party listed non-owned response groups: %+v %v", listThird, err)
	}

	// 5. Owner scoped to different repo (repoB) cannot see group published in repoA.
	ownerRepoB := openStore(Channel{Principal: publisherPrincipal, Repo: repoB})
	_, err = ownerRepoB.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, destLocal)
	requireCode(t, err, "NOT_FOUND")

	// 6. Worker replies with a LOCAL sensitivity result.
	localDraft := projectNote(repoA)
	localDraft.Body = "confidential local memory"
	localDraft.Sensitivity = "local"
	localResult, err := b.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: localDraft})
	if err != nil {
		t.Fatal(err)
	}

	replyReq := PublishEventRequest{
		RequestID:     uuid.NewString(),
		Kind:          "response",
		Ref:           RecordVersionRef{RecordID: localResult.RecordID, Version: 1},
		Destination:   EventDestination{Type: "agent", Name: a.channel.Principal},
		CausationID:   reqEvent.EventID,
		CorrelationID: reqEvent.CorrelationID,
	}
	replyEv, err := b.PublishEvent(ctx, replyReq, destLocal)
	if err != nil {
		t.Fatal(err)
	}

	// 7. Hosted destination (AllowLocal == false): local response payload/ref must be redacted.
	hostedGroup, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, destHosted)
	if err != nil {
		t.Fatal(err)
	}
	if len(hostedGroup.Members) != 1 {
		t.Fatalf("expected 1 member, got %d", len(hostedGroup.Members))
	}
	mHosted := hostedGroup.Members[0]
	if !mHosted.Responded {
		t.Fatal("expected responded=true in hosted read")
	}
	if mHosted.PayloadAvailable {
		t.Fatal("local sensitivity response leaked payload_available=true to hosted destination")
	}
	if mHosted.Ref != nil {
		t.Fatalf("local sensitivity response leaked Ref to hosted destination: %+v", mHosted.Ref)
	}
	if mHosted.ResponseEventID != "" {
		t.Fatalf("local sensitivity response leaked ResponseEventID to hosted destination: %s", mHosted.ResponseEventID)
	}

	// 8. Local destination (AllowLocal == true): local response payload/ref is disclosed to owner.
	localGroup, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, destLocal)
	if err != nil {
		t.Fatal(err)
	}
	mLocal := localGroup.Members[0]
	if !mLocal.PayloadAvailable || mLocal.Ref == nil || mLocal.ResponseEventID != replyEv.EventID {
		t.Fatalf("local destination did not reveal expected local payload: %+v", mLocal)
	}
	if mLocal.Ref.RecordID != localResult.RecordID || mLocal.Ref.Version != 1 {
		t.Fatalf("local Ref mismatch: %+v", mLocal.Ref)
	}

	// 9. Unmatched response observation isolation:
	// A response with wrong CorrelationID is classified as "unmatched".
	unmatchedReq := PublishEventRequest{
		RequestID:     uuid.NewString(),
		Kind:          "response",
		Ref:           RecordVersionRef{RecordID: localResult.RecordID, Version: 1},
		Destination:   EventDestination{Type: "agent", Name: a.channel.Principal},
		CausationID:   reqEvent.EventID,
		CorrelationID: uuid.NewString(), // mismatched correlation ID
	}
	if _, err = b.PublishEvent(ctx, unmatchedReq, destLocal); err != nil {
		t.Fatal(err)
	}

	// Group should have recorded an observation with "unmatched".
	localGroup, err = a.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, destLocal)
	if err != nil {
		t.Fatal(err)
	}
	hasUnmatched := false
	for _, obs := range localGroup.Observations {
		if obs.Disposition == "unmatched" {
			hasUnmatched = true
		}
	}
	if !hasUnmatched {
		t.Fatalf("expected unmatched observation: %+v", localGroup.Observations)
	}
}

// 5. Lock hierarchy and restore ordering review:
// Generation lock (retrieval_generation FOR SHARE / UPDATE) ->
// Collection lock (advisory "agent-events:"+repo) ->
// Group row lock (FOR UPDATE OF g).
func TestReviewLockOrderAndRestoreWaitsGeneration(t *testing.T) {
	_, openStore := reviewDatabase(t)
	ctx := context.Background()
	a, b, r, dest := reviewEventFixture(t, openStore)

	// Step 1: Open an open response group.
	reqEvent, err := a.PublishEvent(ctx, PublishEventRequest{
		RequestID:   uuid.NewString(),
		Kind:        "request",
		Ref:         RecordVersionRef{RecordID: r.RecordID, Version: 1},
		Destination: EventDestination{Type: "agent", Name: b.channel.Principal},
		ResponseGroup: &ResponseGroupSpec{
			Deadline:      time.Now().Add(time.Hour),
			PartialPolicy: "all",
		},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}

	op := openStore(Channel{Principal: "operator:restore-review", Operator: true})

	// Step 2: Concurrent publication and FenceRestore.
	// Holding retrievalGeneration FOR SHARE blocks FenceRestore from updating generation
	// until commit; once FenceRestore commits, open response groups are transitioned to 'incomplete'.
	start := make(chan struct{})
	var wg sync.WaitGroup
	var fenceResult RestoreFence
	var fenceErr error
	var replyEvent AgentEvent
	var replyErr error

	wg.Add(2)
	go func() {
		defer wg.Done()
		<-start
		fenceResult, fenceErr = op.FenceRestore(ctx, RestoreFenceRequest{
			RequestID: uuid.NewString(),
			Reason:    "Verify lock ordering and restore fencing on response groups",
		})
	}()

	go func() {
		defer wg.Done()
		<-start
		replyEvent, replyErr = b.PublishEvent(ctx, PublishEventRequest{
			RequestID:     uuid.NewString(),
			Kind:          "response",
			Ref:           RecordVersionRef{RecordID: r.RecordID, Version: 1},
			Destination:   EventDestination{Type: "agent", Name: a.channel.Principal},
			CausationID:   reqEvent.EventID,
			CorrelationID: reqEvent.CorrelationID,
		}, dest)
	}()

	close(start)
	wg.Wait()

	if fenceErr != nil {
		t.Fatalf("FenceRestore failed: %v", fenceErr)
	}
	if replyErr != nil {
		t.Fatalf("reply during restore failed: %v", replyErr)
	}
	if replyEvent.EventID == "" {
		t.Fatal("expected reply event ID to be populated")
	}
	if fenceResult.Generation < 1 {
		t.Fatalf("expected incremented generation: %+v", fenceResult)
	}

	// Check final group state:
	// If reply happened before restore: state is "incomplete" (restore fences open groups) or "collected".
	// If reply happened after restore: restore set state='incomplete', code='restore_fenced', and reply was marked 'late'.
	group, err := a.ResponseGroup(ctx, ResponseGroupQuery{EventID: reqEvent.EventID}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if group.State == "open" {
		t.Fatalf("restore failed to fence response group: %+v", group)
	}
	if group.State != "incomplete" && group.State != "collected" {
		t.Fatalf("unexpected state after fence: %+v", group)
	}
}
