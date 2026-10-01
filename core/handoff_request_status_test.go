package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func wireObject(t *testing.T, value any) map[string]any {
	t.Helper()
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var out map[string]any
	if err = json.Unmarshal(encoded, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func requireKeys(t *testing.T, got map[string]any, keys ...string) {
	t.Helper()
	if len(got) != len(keys) {
		t.Fatalf("fields %v, want exactly %v", got, keys)
	}
	for _, key := range keys {
		if _, ok := got[key]; !ok {
			t.Fatalf("missing %q in %v", key, got)
		}
	}
}

func hexSHA256(body string) string {
	sum := sha256.Sum256([]byte(body))
	return hex.EncodeToString(sum[:])
}

// coordinationDigest hashes the persisted rows a status read must leave alone:
// events, deliveries (leases, attempts, states), response groups and their
// members/observations, and the repository's notes with their history.
func coordinationDigest(t *testing.T, s *Store, repo string) string {
	t.Helper()
	var digest string
	err := s.pool.QueryRow(context.Background(), `SELECT md5(concat_ws('#',
 (SELECT string_agg(e::text,'|' ORDER BY e.event_id) FROM cairn.agent_event e WHERE e.repo=$1),
 (SELECT string_agg(d::text,'|' ORDER BY d.delivery_id) FROM cairn.agent_delivery d JOIN cairn.agent_event e ON e.event_id=d.event_id WHERE e.repo=$1),
 (SELECT string_agg(g::text,'|' ORDER BY g.event_id) FROM cairn.agent_response_group g JOIN cairn.agent_event e ON e.event_id=g.event_id WHERE e.repo=$1),
 (SELECT string_agg(m::text,'|' ORDER BY m.event_id,m.consumer) FROM cairn.agent_response_group_member m JOIN cairn.agent_event e ON e.event_id=m.event_id WHERE e.repo=$1),
 (SELECT string_agg(o::text,'|' ORDER BY o.response_event_id) FROM cairn.agent_response_observation o JOIN cairn.agent_event e ON e.event_id=o.group_event_id WHERE e.repo=$1),
 (SELECT string_agg(v::text,'|' ORDER BY v.record_id,v.version) FROM cairn.record_version v WHERE v.repo=$1),
 (SELECT string_agg(r::text,'|' ORDER BY r.record_id) FROM cairn.memory_record r WHERE r.record_id IN (SELECT record_id FROM cairn.record_version WHERE repo=$1))))`, repo).Scan(&digest)
	if err != nil {
		t.Fatal(err)
	}
	return digest
}

func handoffNote(t *testing.T, s *Store, repo, sensitivity, body string) Record {
	t.Helper()
	draft := projectNote(repo)
	draft.Sensitivity, draft.Body = sensitivity, body
	record, err := s.Create(context.Background(), CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	return record
}

func publishRequest(t *testing.T, s *Store, source Record, dest Destination, to EventDestination, group *ResponseGroupSpec) AgentEvent {
	t.Helper()
	event, err := s.PublishEvent(context.Background(), PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version},
		Destination: to, ResponseGroup: group,
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	return event
}

func allDeliveries(t *testing.T, publisher *Store, event AgentEvent, dest Destination) map[string]string {
	t.Helper()
	status, err := publisher.AgentEventStatus(context.Background(), EventStatusRequest{EventID: event.EventID, Limit: 100}, dest)
	if err != nil || status.More {
		t.Fatalf("publisher delivery listing: %+v %v", status, err)
	}
	byConsumer := map[string]string{}
	for _, d := range status.Deliveries {
		byConsumer[d.Consumer] = d.DeliveryID
	}
	return byConsumer
}

func hourGroup(policy string) *ResponseGroupSpec {
	return &ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: policy}
}

// Two handled requests with unresolved replies leave both handoff items with an
// unknown task outcome, and reading the status changes nothing.
func TestHandoffRequestStatusReportsHandlingNotTaskOutcome(t *testing.T) {
	ctx := context.Background()
	publisher, worker, source, dest := eventFixture(t)
	repo := source.Scope.Repo
	events := []AgentEvent{
		publishRequest(t, publisher, source, dest, EventDestination{"agent", worker.channel.Principal}, hourGroup("all")),
		publishRequest(t, publisher, source, dest, EventDestination{"agent", worker.channel.Principal}, hourGroup("all")),
	}
	body := fmt.Sprintf("Handoff: fixture\nOpen: review %s; deliver %s", events[0].EventID, events[1].EventID)
	handoff := handoffNote(t, publisher, repo, "shareable", body)
	deliveries := map[string]string{}
	for range events {
		delivery := nextFixture(t, worker, dest)
		if _, err := worker.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.DeliveryID, LeaseID: delivery.LeaseID, Disposition: "handled"}, dest); err != nil {
			t.Fatal(err)
		}
		deliveries[delivery.Event.EventID] = delivery.DeliveryID
	}
	req := HandoffRequestStatusRequest{
		Handoff: RecordVersionRef{handoff.RecordID, 1},
		Items:   []HandoffItemLink{{"review", events[0].EventID}, {"deliver", events[1].EventID}},
	}
	before := coordinationDigest(t, publisher, repo)
	status, err := publisher.HandoffRequestStatus(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	if status.Schema != "cairn.handoff-request-status/1" || status.Links != "caller_asserted" ||
		status.Handoff != (HandoffSource{handoff.RecordID, 1, hexSHA256(body)}) || len(status.Items) != 2 {
		t.Fatalf("status attribution: %+v", status)
	}
	for i, item := range status.Items {
		link := req.Items[i]
		if item.ItemID != link.ItemID || item.EventID != link.EventID || item.Availability != "available" || item.TaskOutcome != "unknown" || item.HandoffRequestObservation == nil {
			t.Fatalf("item %d: %+v", i, item)
		}
		if want := []HandoffDelivery{{deliveries[link.EventID], "handled"}}; !reflect.DeepEqual(item.Deliveries, want) || item.DeliveriesMore {
			t.Fatalf("item %d deliveries: %+v", i, item.HandoffRequestObservation)
		}
		if item.ResponseGroup == nil || *item.ResponseGroup != (HandoffResponseGroup{"open", 1, 0}) {
			t.Fatalf("handling closed the unresolved reply collection: %+v", item.ResponseGroup)
		}
	}
	// The wire shape is closed: no result, lease, consumer, outcome or member data.
	wire := wireObject(t, status)
	requireKeys(t, wire, "schema", "handoff", "links", "items")
	requireKeys(t, wire["handoff"].(map[string]any), "record_id", "version", "body_sha256")
	for _, raw := range wire["items"].([]any) {
		row := raw.(map[string]any)
		requireKeys(t, row, "item_id", "event_id", "availability", "task_outcome", "deliveries", "deliveries_more", "response_group")
		requireKeys(t, row["response_group"].(map[string]any), "state", "expected", "responded")
		for _, d := range row["deliveries"].([]any) {
			requireKeys(t, d.(map[string]any), "delivery_id", "state")
		}
	}
	if after := coordinationDigest(t, publisher, repo); after != before {
		t.Fatal("status read changed coordination or note state")
	}
	history, err := publisher.History(ctx, RecordHistoryRequest{RecordID: handoff.RecordID, Limit: 20}, dest)
	if err != nil || history.CurrentVersion != 1 || len(history.Versions) != 1 {
		t.Fatalf("status read rewrote handoff history: %+v %v", history, err)
	}
	// The same assertion twice is a harmless repeat of a read.
	again, err := publisher.HandoffRequestStatus(ctx, req, dest)
	if err != nil || !reflect.DeepEqual(again, status) {
		t.Fatalf("repeat: %+v %v", again, err)
	}
}

// Delivery failure, ignoring, replies and an elapsed deadline are reported as
// observations. None of them can say the handoff item is complete.
func TestHandoffRequestStatusKeepsFailureRepliesAndDeadlineAsObservations(t *testing.T) {
	ctx := context.Background()
	publisher, first, source, dest := eventFixture(t)
	repo := source.Scope.Repo
	consumers := []*Store{first}
	for i := 1; i < 4; i++ {
		consumers = append(consumers, testStore(t, Channel{Principal: fmt.Sprintf("consumer-%d-%s", i, repo), Repo: repo}))
	}
	for _, c := range consumers {
		if _, err := c.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "review", Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	fanout := publishRequest(t, publisher, source, dest, EventDestination{"topic", "review"}, hourGroup("partial"))
	direct := publishRequest(t, publisher, source, dest, EventDestination{"agent", first.channel.Principal}, hourGroup("all"))
	byConsumer := allDeliveries(t, publisher, fanout, dest)
	// consumers[0] stays pending, [1] holds a lease, [2] fails and [3] ignores.
	if next, err := consumers[1].NextEvent(ctx, NextEventRequest{}, dest); err != nil || next.Delivery == nil {
		t.Fatalf("lease: %+v %v", next, err)
	}
	for i, disposition := range map[int]struct{ state, code string }{2: {"failed", "processing_failed"}, 3: {"ignored", "unsupported_kind"}} {
		delivery, err := consumers[i].NextEvent(ctx, NextEventRequest{}, dest)
		if err != nil || delivery.Delivery == nil {
			t.Fatalf("claim %d: %+v %v", i, delivery, err)
		}
		if _, err = consumers[i].CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: delivery.Delivery.DeliveryID, LeaseID: delivery.Delivery.LeaseID, Disposition: disposition.state, Code: disposition.code}, dest); err != nil {
			t.Fatal(err)
		}
	}
	wantStates := map[string]string{}
	for i, state := range []string{"pending", "leased", "failed", "ignored"} {
		wantStates[byConsumer[consumers[i].channel.Principal]] = state
	}
	handoff := handoffNote(t, publisher, repo, "shareable", "Handoff: failure observations")
	req := HandoffRequestStatusRequest{
		Handoff: RecordVersionRef{handoff.RecordID, 1},
		Items:   []HandoffItemLink{{"fanout", fanout.EventID}, {"direct", direct.EventID}},
	}
	read := func() (HandoffItemStatus, HandoffItemStatus) {
		t.Helper()
		status, err := publisher.HandoffRequestStatus(ctx, req, dest)
		if err != nil || len(status.Items) != 2 {
			t.Fatalf("status: %+v %v", status, err)
		}
		for _, item := range status.Items {
			if item.TaskOutcome != "unknown" || item.HandoffRequestObservation == nil || item.ResponseGroup == nil {
				t.Fatalf("task outcome or group missing: %+v", item)
			}
		}
		return status.Items[0], status.Items[1]
	}
	fan, one := read()
	var want []HandoffDelivery
	for id, state := range wantStates {
		want = append(want, HandoffDelivery{id, state})
	}
	sort.Slice(want, func(i, j int) bool { return want[i].DeliveryID < want[j].DeliveryID })
	if !reflect.DeepEqual(fan.Deliveries, want) || fan.DeliveriesMore || *fan.ResponseGroup != (HandoffResponseGroup{"open", 4, 0}) {
		t.Fatalf("failure/ignore/lease/pending: %+v group %+v want %+v", fan.Deliveries, fan.ResponseGroup, want)
	}
	if len(one.Deliveries) != 1 || one.Deliveries[0].State != "pending" || *one.ResponseGroup != (HandoffResponseGroup{"open", 1, 0}) {
		t.Fatalf("unclaimed request: %+v", one.HandoffRequestObservation)
	}
	// The failed member explicitly replies with a failure report; a reply fills
	// the member but still says nothing about the work.
	reply := PublishEventRequest{RequestID: uuid.NewString(), Kind: "response", Ref: RecordVersionRef{source.RecordID, 1}, Destination: EventDestination{"agent", publisher.channel.Principal}, CausationID: fanout.EventID, CorrelationID: fanout.CorrelationID}
	if _, err := consumers[2].PublishEvent(ctx, reply, dest); err != nil {
		t.Fatal(err)
	}
	if fan, _ = read(); *fan.ResponseGroup != (HandoffResponseGroup{"open", 4, 1}) {
		t.Fatalf("reply: %+v", fan.ResponseGroup)
	}
	// An elapsed deadline is not persisted by reading: the stored state stays open.
	for _, id := range []string{fanout.EventID, direct.EventID} {
		if _, err := publisher.pool.Exec(ctx, `UPDATE cairn.agent_response_group SET deadline=clock_timestamp()-interval '1 second' WHERE event_id=$1`, id); err != nil {
			t.Fatal(err)
		}
	}
	before := coordinationDigest(t, publisher, repo)
	if fan, one = read(); fan.ResponseGroup.State != "open" || one.ResponseGroup.State != "open" {
		t.Fatalf("a read closed an overdue group: %+v %+v", fan.ResponseGroup, one.ResponseGroup)
	}
	if coordinationDigest(t, publisher, repo) != before {
		t.Fatal("reading an overdue group persisted closure")
	}
	op := testStore(t, Channel{Principal: "handoff-sweeper", Operator: true})
	if _, err := op.SweepRequestControls(ctx, RequestControlSweep{Repo: repo}); err != nil {
		t.Fatal(err)
	}
	fan, one = read()
	if *fan.ResponseGroup != (HandoffResponseGroup{"partial", 4, 1}) || *one.ResponseGroup != (HandoffResponseGroup{"incomplete", 1, 0}) {
		t.Fatalf("closed groups: %+v %+v", fan.ResponseGroup, one.ResponseGroup)
	}
	if fan.TaskOutcome != "unknown" || one.TaskOutcome != "unknown" {
		t.Fatal("collection state became a task assertion")
	}
}

// A readable handoff is not a capability: absent, inaccessible, other-collection,
// destination-hidden and non-request links look identical, and recipients see
// only their own delivery and no publisher-only group.
func TestHandoffRequestStatusKeepsEventVisibilityIndependentOfHandoffReadability(t *testing.T) {
	ctx := context.Background()
	publisher, worker, source, dest := eventFixture(t)
	repo := source.Scope.Repo
	later := testStore(t, Channel{Principal: "later-" + repo, Repo: repo})
	second := testStore(t, Channel{Principal: "second-" + repo, Repo: repo})
	for _, s := range []*Store{worker, second} {
		if _, err := s.SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: "review", Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	request := publishRequest(t, publisher, source, dest, EventDestination{"topic", "review"}, hourGroup("all"))
	notice, err := publisher.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice", Ref: RecordVersionRef{source.RecordID, 1}, Destination: EventDestination{"agent", worker.channel.Principal}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	localSource := handoffNote(t, publisher, repo, "local", "private source")
	localDest := Destination{"local", true}
	private := publishRequest(t, publisher, localSource, localDest, EventDestination{"agent", worker.channel.Principal}, nil)
	otherRepo := uuid.NewString()
	foreign := testStore(t, Channel{Principal: publisher.channel.Principal, Repo: otherRepo})
	foreignSource := handoffNote(t, foreign, otherRepo, "shareable", "foreign source")
	foreignRequest := publishRequest(t, foreign, foreignSource, dest, EventDestination{"agent", "foreign-consumer"}, nil)
	handoff := handoffNote(t, publisher, repo, "shareable", "Handoff: visibility")
	absent := uuid.NewString()
	items := []HandoffItemLink{{"live", request.EventID}, {"notice", notice.EventID}, {"private", private.EventID}, {"foreign", foreignRequest.EventID}, {"absent", absent}}
	req := HandoffRequestStatusRequest{Handoff: RecordVersionRef{handoff.RecordID, 1}, Items: items}
	unavailable := func(t *testing.T, item HandoffItemStatus, link HandoffItemLink) {
		t.Helper()
		encoded, err := json.Marshal(item)
		want := fmt.Sprintf(`{"item_id":%q,"event_id":%q,"availability":"unavailable","task_outcome":"unknown"}`, link.ItemID, link.EventID)
		if err != nil || string(encoded) != want {
			t.Fatalf("unavailable item %s, want %s (%v)", encoded, want, err)
		}
	}
	byConsumer := allDeliveries(t, publisher, request, dest)

	// A third principal reads the shareable handoff but none of its linked events.
	status, err := later.HandoffRequestStatus(ctx, req, dest)
	if err != nil || len(status.Items) != len(items) {
		t.Fatalf("later: %+v %v", status, err)
	}
	for i, item := range status.Items {
		unavailable(t, item, items[i])
	}

	// The publisher reads the live request with every delivery and its group, but
	// not a notice, a local-only event under a hosted destination, another
	// collection's event under the same principal name, or an absent UUID.
	status, err = publisher.HandoffRequestStatus(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	live := status.Items[0]
	if live.Availability != "available" || len(live.Deliveries) != 2 || live.ResponseGroup == nil || *live.ResponseGroup != (HandoffResponseGroup{"open", 2, 0}) {
		t.Fatalf("publisher view: %+v", live)
	}
	for _, i := range []int{1, 2, 3, 4} {
		unavailable(t, status.Items[i], items[i])
	}
	local, err := publisher.HandoffRequestStatus(ctx, req, localDest)
	if err != nil || local.Items[2].Availability != "available" || len(local.Items[2].Deliveries) != 1 {
		t.Fatalf("local destination view: %+v %v", local.Items[2], err)
	}
	unavailable(t, local.Items[3], items[3])

	// Each recipient reads only its own delivery and never the publisher's group.
	for _, recipient := range []*Store{worker, second} {
		status, err = recipient.HandoffRequestStatus(ctx, req, dest)
		if err != nil {
			t.Fatal(err)
		}
		live = status.Items[0]
		if live.Availability != "available" || !reflect.DeepEqual(live.Deliveries, []HandoffDelivery{{byConsumer[recipient.channel.Principal], "pending"}}) || live.ResponseGroup != nil {
			t.Fatalf("recipient %s view: %+v", recipient.channel.Principal, live)
		}
		if encoded, _ := json.Marshal(live); !strings.Contains(string(encoded), `"response_group":null`) {
			t.Fatalf("hidden group must be an explicit null: %s", encoded)
		}
		// A notice addressed to the recipient is not a request: it stays unavailable.
		unavailable(t, status.Items[1], items[1])
		unavailable(t, status.Items[4], items[4])
	}

	// Absent, other-collection and destination-hidden handoffs refuse alike.
	hidden := handoffNote(t, publisher, repo, "local", "Handoff: local only")
	foreignReader := testStore(t, Channel{Principal: "foreign-" + repo, Repo: otherRepo})
	absentReq := HandoffRequestStatusRequest{Handoff: RecordVersionRef{uuid.NewString(), 1}, Items: items[:1]}
	_, absentErr := publisher.HandoffRequestStatus(ctx, absentReq, dest)
	requireCode(t, absentErr, "NOT_FOUND")
	for name, read := range map[string]func() error{
		"local handoff under hosted destination": func() error {
			_, err := publisher.HandoffRequestStatus(ctx, HandoffRequestStatusRequest{Handoff: RecordVersionRef{hidden.RecordID, 1}, Items: items[:1]}, dest)
			return err
		},
		"other collection": func() error {
			_, err := foreignReader.HandoffRequestStatus(ctx, req, dest)
			return err
		},
	} {
		if err := read(); err == nil || err.Error() != absentErr.Error() {
			t.Fatalf("%s distinguishes itself from an absent handoff: %v vs %v", name, err, absentErr)
		}
	}
	if status, err = publisher.HandoffRequestStatus(ctx, HandoffRequestStatusRequest{Handoff: RecordVersionRef{hidden.RecordID, 1}, Items: items[:1]}, localDest); err != nil || status.Handoff.RecordID != hidden.RecordID {
		t.Fatalf("local destination could not read its own handoff: %+v %v", status, err)
	}
}

// The status names the exact handoff version it read. A stale or future version
// refuses, reads never change history, and a deleted handoff is not found.
func TestHandoffRequestStatusRefusesStaleOrAbsentHandoffVersion(t *testing.T) {
	ctx := context.Background()
	publisher, worker, source, dest := eventFixture(t)
	repo := source.Scope.Repo
	event := publishFixture(t, publisher, worker, source, dest)
	handoff := handoffNote(t, publisher, repo, "shareable", "Handoff: version one")
	read := func(version int) (HandoffRequestStatus, error) {
		return publisher.HandoffRequestStatus(ctx, HandoffRequestStatusRequest{Handoff: RecordVersionRef{handoff.RecordID, version}, Items: []HandoffItemLink{{"item", event.EventID}}}, dest)
	}
	first, err := read(1)
	if err != nil || first.Handoff.Version != 1 || first.Handoff.BodySHA256 != hexSHA256("Handoff: version one") {
		t.Fatalf("version one: %+v %v", first, err)
	}
	revised := "Handoff: version two"
	if revision, err := publisher.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: handoff.RecordID, ExpectedVersion: 1, Repo: repo, Body: revised}); err != nil || revision.Version != 2 {
		t.Fatalf("revise: %+v %v", revision, err)
	}
	for _, version := range []int{1, 3} {
		out, err := read(version)
		requireCode(t, err, "VERSION_CONFLICT")
		if out.Items != nil || out.Handoff.RecordID != "" {
			t.Fatalf("stale version %d returned status: %+v", version, out)
		}
	}
	second, err := read(2)
	if err != nil || second.Handoff.Version != 2 || second.Handoff.BodySHA256 != hexSHA256(revised) || second.Handoff.BodySHA256 == first.Handoff.BodySHA256 {
		t.Fatalf("current version: %+v %v", second, err)
	}
	history, err := publisher.History(ctx, RecordHistoryRequest{RecordID: handoff.RecordID, Limit: 20}, dest)
	if err != nil || history.CurrentVersion != 2 || len(history.Versions) != 2 {
		t.Fatalf("reads changed handoff history: %+v %v", history, err)
	}
	if _, err = publisher.Delete(ctx, DeleteRequest{RequestID: uuid.NewString(), RecordID: handoff.RecordID, ExpectedVersion: 2}); err != nil {
		t.Fatal(err)
	}
	_, err = read(2)
	requireCode(t, err, "NOT_FOUND")
}

// Invalid input refuses before any status work; a database failure is an
// explicit error, never an unavailable row or a success.
func TestHandoffRequestStatusValidatesBeforeReadingAndFailsExplicitly(t *testing.T) {
	publisher, worker, source, dest := eventFixture(t)
	event := publishFixture(t, publisher, worker, source, dest)
	valid := HandoffRequestStatusRequest{Handoff: RecordVersionRef{source.RecordID, 1}, Items: []HandoffItemLink{{"review", event.EventID}}}
	sixteen := make([]HandoffItemLink, 16)
	seventeen := make([]HandoffItemLink, 17)
	for i := range seventeen {
		seventeen[i] = HandoffItemLink{fmt.Sprintf("item-%d", i), uuid.NewString()}
	}
	copy(sixteen, seventeen)
	other := uuid.NewString()
	with := func(items ...HandoffItemLink) HandoffRequestStatusRequest {
		return HandoffRequestStatusRequest{Handoff: valid.Handoff, Items: items}
	}
	invalid := map[string]HandoffRequestStatusRequest{
		"no items":            with(),
		"seventeen items":     with(seventeen...),
		"empty item id":       with(HandoffItemLink{"", other}),
		"long item id":        with(HandoffItemLink{strings.Repeat("a", 65), other}),
		"item id with space":  with(HandoffItemLink{"two words", other}),
		"item id with dot":    with(HandoffItemLink{"item.one", other}),
		"duplicate item id":   with(HandoffItemLink{"one", other}, HandoffItemLink{"one", uuid.NewString()}),
		"duplicate event id":  with(HandoffItemLink{"one", other}, HandoffItemLink{"two", other}),
		"uppercase event id":  with(HandoffItemLink{"one", strings.ToUpper(other)}),
		"nil event id":        with(HandoffItemLink{"one", uuid.Nil.String()}),
		"malformed event id":  with(HandoffItemLink{"one", "not-a-uuid"}),
		"bad handoff id":      {Handoff: RecordVersionRef{"not-a-uuid", 1}, Items: valid.Items},
		"zero version":        {Handoff: RecordVersionRef{source.RecordID, 0}, Items: valid.Items},
		"negative version":    {Handoff: RecordVersionRef{source.RecordID, -1}, Items: valid.Items},
		"oversized version":   {Handoff: RecordVersionRef{source.RecordID, 2147483648}, Items: valid.Items},
		"empty handoff":       {Items: valid.Items},
		"nil item list":       {Handoff: valid.Handoff},
		"empty handoff items": with([]HandoffItemLink{}...),
	}
	// A canceled context would surface as a store failure if any read began, so
	// INVALID_REQUEST proves validation ran first.
	canceled, cancel := context.WithCancel(context.Background())
	cancel()
	for name, req := range invalid {
		out, err := publisher.HandoffRequestStatus(canceled, req, dest)
		if Code(err) != "INVALID_REQUEST" || out.Items != nil {
			t.Errorf("%s: %+v %v", name, out, err)
		}
	}
	for name, req := range map[string]HandoffRequestStatusRequest{"one item": valid, "sixteen items": with(sixteen...)} {
		out, err := publisher.HandoffRequestStatus(canceled, req, dest)
		if err == nil || Code(err) != "STORE_ERROR" || out.Items != nil || out.Handoff.RecordID != "" {
			t.Errorf("%s: unavailable service must be an explicit failure, got %+v %v", name, out, err)
		}
	}
	_, err := publisher.HandoffRequestStatus(context.Background(), valid, Destination{"hosted", true})
	requireCode(t, err, "INVALID_REQUEST")
}

// A request's delivery list is capped at 16 in delivery UUID order and says
// when more exist; exactly 16 is complete.
func TestHandoffRequestStatusBoundsDeliveriesAndReportsMore(t *testing.T) {
	ctx := context.Background()
	publisher, _, source, dest := eventFixture(t)
	repo := source.Scope.Repo
	subscribers := make([]*Store, 17)
	for i := range subscribers {
		subscribers[i] = testStore(t, Channel{Principal: fmt.Sprintf("subscriber-%02d-%s", i, repo), Repo: repo})
		topics := []string{"seventeen"}
		if i < 16 {
			topics = append(topics, "sixteen")
		}
		for _, topic := range topics {
			if _, err := subscribers[i].SetSubscription(ctx, SubscriptionRequest{RequestID: uuid.NewString(), Topic: topic, Active: true}); err != nil {
				t.Fatal(err)
			}
		}
	}
	events := map[string]AgentEvent{
		"seventeen": publishRequest(t, publisher, source, dest, EventDestination{"topic", "seventeen"}, hourGroup("partial")),
		"sixteen":   publishRequest(t, publisher, source, dest, EventDestination{"topic", "sixteen"}, nil),
	}
	handoff := handoffNote(t, publisher, repo, "shareable", "Handoff: fanout")
	status, err := publisher.HandoffRequestStatus(ctx, HandoffRequestStatusRequest{Handoff: RecordVersionRef{handoff.RecordID, 1}, Items: []HandoffItemLink{{"seventeen", events["seventeen"].EventID}, {"sixteen", events["sixteen"].EventID}}}, dest)
	if err != nil || len(status.Items) != 2 {
		t.Fatalf("status: %+v %v", status, err)
	}
	var all []string
	for _, id := range allDeliveries(t, publisher, events["seventeen"], dest) {
		all = append(all, id)
	}
	sort.Strings(all)
	over, exact := status.Items[0], status.Items[1]
	if len(all) != 17 || len(over.Deliveries) != 16 || !over.DeliveriesMore || over.ResponseGroup == nil || over.ResponseGroup.Expected != 17 {
		t.Fatalf("17 recipients: %d deliveries more=%v group=%+v", len(over.Deliveries), over.DeliveriesMore, over.ResponseGroup)
	}
	for i, d := range over.Deliveries {
		if d.DeliveryID != all[i] {
			t.Fatalf("delivery %d is %s, want the UUID-ordered prefix %s", i, d.DeliveryID, all[i])
		}
	}
	if len(exact.Deliveries) != 16 || exact.DeliveriesMore || exact.ResponseGroup != nil {
		t.Fatalf("16 recipients: %d deliveries more=%v group=%+v", len(exact.Deliveries), exact.DeliveriesMore, exact.ResponseGroup)
	}
}
