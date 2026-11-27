package main

import (
	"context"
	"encoding/json"
	"maps"
	"net/http"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func swarmActionJSON[T any](t *testing.T, resp *http.Response, raw []byte, wantStatus int) T {
	t.Helper()
	var out T
	if resp.StatusCode != wantStatus {
		t.Fatalf("status %d, want %d: %s", resp.StatusCode, wantStatus, raw)
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode %s: %v", raw, err)
	}
	return out
}

func swarmActionPost[T any](t *testing.T, f *swarmStoreFixture, action string, body any) T {
	t.Helper()
	resp, raw := f.do(http.MethodPost, "/api/swarm/action/"+action, body, map[string]string{"X-CSRF-Token": f.csrf})
	return swarmActionJSON[T](t, resp, raw, http.StatusOK)
}

func swarmActionRequest(f *swarmStoreFixture) core.SwarmActionRequest {
	return core.SwarmActionRequest{Campaign: f.campaign, RequestID: uuid.NewString()}
}

func swarmActionSend(f *swarmStoreFixture, to, kind, body string) core.SwarmSendRequest {
	r := core.SwarmSendRequest{
		SwarmActionRequest: swarmActionRequest(f), To: to, Kind: kind, Body: body, Summary: "Operator action test",
	}
	if kind == "notice" {
		r.Action = "review"
	}
	return r
}

func swarmActionDeliveryRequest(f *swarmStoreFixture, id string) core.SwarmDeliveryActionRequest {
	return core.SwarmDeliveryActionRequest{SwarmActionRequest: swarmActionRequest(f), DeliveryID: id}
}

func swarmActionGetEvent(t *testing.T, f *swarmStoreFixture, id string) core.SwarmEventDetails {
	t.Helper()
	resp, raw := f.do(http.MethodGet, "/api/swarm/event?campaign="+f.campaign+"&event_id="+id, nil, nil)
	return swarmActionJSON[core.SwarmEventDetails](t, resp, raw, http.StatusOK)
}

func swarmActionPublishEvent(t *testing.T, f *swarmStoreFixture, s *core.Store, kind, to, cause, supersedes string) core.AgentEvent {
	t.Helper()
	req := core.PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: kind, CausationID: cause, Supersedes: supersedes,
		Ref:         core.RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: core.EventDestination{Type: "agent", Name: to},
	}
	if kind == "notice" {
		req.Action = "review"
	}
	e, err := s.PublishEvent(context.Background(), req, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	return e
}

func TestSwarmActionRouteTable(t *testing.T) {
	want := map[string]string{
		"/api/swarm/event":                  http.MethodGet,
		"/api/swarm/action/send":            http.MethodPost,
		"/api/swarm/action/acknowledge":     http.MethodPost,
		"/api/swarm/action/cancel-preview":  http.MethodGet,
		"/api/swarm/action/cancel-delivery": http.MethodPost,
		"/api/swarm/action/cancel-wait":     http.MethodPost,
	}
	got := make(map[string]string, len(swarmActionRoutes))
	for path, route := range swarmActionRoutes {
		got[path] = route.method
	}
	if !maps.Equal(got, want) {
		t.Fatalf("operator routes: %v, want %v", got, want)
	}
}

func TestSwarmActionHTTPAuthorityBeforeStore(t *testing.T) {
	ctx := context.Background()
	f := newSwarmStoreFixture(t, swarmNoPanes)
	member, _ := f.member("action-authority-member")
	outsider, _ := f.session("action-authority-outsider")
	path := "/api/swarm/action/send"
	req := swarmActionSend(f, member.Inbox, "request", "Must never be stored")
	for _, tc := range []struct {
		name       string
		headers    map[string]string
		signedIn   bool
		wantStatus int
	}{
		{"no session", map[string]string{"X-CSRF-Token": f.csrf}, false, http.StatusUnauthorized},
		{"missing CSRF", nil, true, http.StatusForbidden},
		{"incorrect CSRF", map[string]string{"X-CSRF-Token": "incorrect"}, true, http.StatusForbidden},
		{"cross-site", map[string]string{"X-CSRF-Token": f.csrf, "Sec-Fetch-Site": "cross-site"}, true, http.StatusForbidden},
		{"foreign origin", map[string]string{"X-CSRF-Token": f.csrf, "Origin": "http://localhost:1"}, true, http.StatusForbidden},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if !tc.signedIn {
				client := f.client
				f.client = &http.Client{}
				t.Cleanup(func() { f.client = client })
			}
			resp, raw := f.do(http.MethodPost, path, req, tc.headers)
			if resp.StatusCode != tc.wantStatus {
				t.Fatalf("status %d, want %d: %s", resp.StatusCode, tc.wantStatus, raw)
			}
		})
	}
	header := map[string]string{"X-CSRF-Token": f.csrf}
	foreign := req
	foreign.To = outsider.Inbox
	resp, raw := f.do(http.MethodPost, path, foreign, header)
	if resp.StatusCode != http.StatusForbidden || swarmErrorCode(raw) != "AUTHORITY_DENIED" {
		t.Fatalf("nonmember: %d %s", resp.StatusCode, raw)
	}
	bad := req
	bad.RequestID = "A13418CD-EF01-4344-B9E3-B210E5FCF677"
	resp, raw = f.do(http.MethodPost, path, bad, header)
	if resp.StatusCode != http.StatusBadRequest || swarmErrorCode(raw) != "INVALID_REQUEST" {
		t.Fatalf("noncanonical UUID: %d %s", resp.StatusCode, raw)
	}
	injected := map[string]string{
		"campaign": f.campaign, "request_id": req.RequestID, "kind": "request",
		"to": req.To, "body": req.Body, "principal": member.Inbox,
	}
	resp, raw = f.do(http.MethodPost, path, injected, header)
	if resp.StatusCode != http.StatusBadRequest || swarmErrorCode(raw) != "INVALID_REQUEST" {
		t.Fatalf("caller-selected principal: %d %s", resp.StatusCode, raw)
	}
	stream, err := f.op.ReadSwarmStream(ctx, core.SwarmStreamRequest{Repo: f.repo, Campaign: f.campaign})
	if err != nil || len(stream.Events) != 0 {
		t.Fatalf("a refused action published an event: %+v %v", stream.Events, err)
	}
	// Change the body but reuse the UUID. An earlier draft write would
	// make this request conflict even if no event was published.
	req.Body = "The signed-in operator authorized this body"
	sent := swarmActionPost[core.SwarmEventDetails](t, f, "send", req)
	if sent.Event.From != f.op.Channel().Principal {
		t.Fatalf("publisher: %+v", sent.Event)
	}
}

func TestSwarmActionHTTPSendEventCursorAndSupersession(t *testing.T) {
	ctx := context.Background()
	f := newSwarmStoreFixture(t, swarmNoPanes)
	member, view := f.member("action-send-member")
	first, err := f.op.ReadSwarmStream(ctx, core.SwarmStreamRequest{Repo: f.repo, Campaign: f.campaign})
	if err != nil {
		t.Fatal(err)
	}
	req := swarmActionSend(f, member.Inbox, "request", strings.Repeat("界", 6000))
	sent := swarmActionPost[core.SwarmEventDetails](t, f, "send", req)
	retry := swarmActionPost[core.SwarmEventDetails](t, f, "send", req)
	if sent.Event.Kind != "request" || sent.Event.From != f.op.Channel().Principal || sent.Event.To.Name != member.Inbox ||
		retry.Event.EventID != sent.Event.EventID || retry.Event.Ref != sent.Event.Ref ||
		len(sent.Deliveries) != 1 || sent.Deliveries[0].Seen || sent.Deliveries[0].State != "pending" {
		t.Fatalf("send and retry: %+v %+v", sent, retry)
	}
	stream, err := f.op.ReadSwarmStream(ctx, core.SwarmStreamRequest{Repo: f.repo, Campaign: f.campaign, Cursor: first.Cursor})
	if err != nil || len(stream.Events) != 1 || stream.Events[0].EventID != sent.Event.EventID {
		t.Fatalf("next cursor: %+v %v", stream.Events, err)
	}
	assertSeen := func(snapshot core.SwarmSnapshot, want bool) {
		t.Helper()
		for _, delivery := range snapshot.Flow.Deliveries {
			if delivery.DeliveryID == sent.Deliveries[0].DeliveryID {
				if delivery.Seen != want {
					t.Fatalf("snapshot seen=%t, want %t: %+v", delivery.Seen, want, delivery)
				}
				return
			}
		}
		t.Fatalf("delivery missing from snapshot: %+v", snapshot.Flow)
	}
	assertSeen(stream.Snapshot, false)
	detail := swarmActionGetEvent(t, f, sent.Event.EventID)
	if detail.Body == nil || !detail.BodyTruncated || detail.BodyUnavailable != "" ||
		len(*detail.Body) > 16<<10 || !utf8.ValidString(*detail.Body) || !strings.HasPrefix(req.Body, *detail.Body) {
		t.Fatalf("unexpected bounded body: available=%t truncated=%t unavailable=%q", detail.Body != nil, detail.BodyTruncated, detail.BodyUnavailable)
	}
	n, err := view.NextEvent(ctx, core.NextEventRequest{Repo: f.repo}, f.dest)
	if err != nil || n.Delivery == nil || n.Delivery.DeliveryID != sent.Deliveries[0].DeliveryID {
		t.Fatalf("recipient claim: %+v %v", n, err)
	}
	if got := swarmActionGetEvent(t, f, sent.Event.EventID); !got.Deliveries[0].Seen {
		t.Fatalf("claim did not update seen: %+v", got.Deliveries)
	}
	after, err := f.op.ReadSwarmStream(ctx, core.SwarmStreamRequest{Repo: f.repo, Campaign: f.campaign, Cursor: stream.Cursor})
	if err != nil {
		t.Fatal(err)
	}
	assertSeen(after.Snapshot, true)

	const topicKey = "same-key"
	other := swarmActionPublishEvent(t, f, f.rootView, "notice", member.Inbox, "", topicKey)
	notice := swarmActionSend(f, member.Inbox, "notice", "The owner's first notice")
	notice.Supersedes = topicKey
	original := swarmActionPost[core.SwarmEventDetails](t, f, "send", notice)
	notice.RequestID = uuid.NewString()
	notice.Body = "The owner's updated notice"
	replacement := swarmActionPost[core.SwarmEventDetails](t, f, "send", notice)
	if replacement.Event.Supersedes != topicKey || replacement.Event.Action != "review" {
		t.Fatalf("superseding notice: %+v", replacement.Event)
	}
	older := swarmActionGetEvent(t, f, original.Event.EventID)
	if len(older.Deliveries) != 1 || older.Deliveries[0].Display != "superseded" || older.Deliveries[0].SupersededBy != replacement.Event.EventID {
		t.Fatalf("old notice: %+v", older.Deliveries)
	}
	otherDetail := swarmActionGetEvent(t, f, other.EventID)
	if len(otherDetail.Deliveries) != 1 || otherDetail.Deliveries[0].State != "pending" {
		t.Fatalf("other publisher's notice changed: %+v", otherDetail.Deliveries)
	}
}

func TestSwarmActionHTTPAcknowledgeOrderAndReplay(t *testing.T) {
	f := newSwarmStoreFixture(t, swarmNoPanes)
	member, view := f.member("action-ack-member")
	operator := f.op.Channel().Principal
	first := swarmActionPublishEvent(t, f, view, "notice", operator, "", "")
	second := swarmActionPublishEvent(t, f, view, "notice", operator, "", "")
	firstID := swarmActionGetEvent(t, f, first.EventID).Deliveries[0].DeliveryID
	secondID := swarmActionGetEvent(t, f, second.EventID).Deliveries[0].DeliveryID
	ackSecond := swarmActionDeliveryRequest(f, secondID)
	header := map[string]string{"X-CSRF-Token": f.csrf}
	resp, raw := f.do(http.MethodPost, "/api/swarm/action/acknowledge", ackSecond, header)
	conflict := swarmActionJSON[struct {
		Code     string             `json:"code"`
		Expected string             `json:"expected"`
		Oldest   core.SwarmDelivery `json:"oldest"`
	}](t, resp, raw, http.StatusConflict)
	if conflict.Code != "VERSION_CONFLICT" || conflict.Expected != secondID || conflict.Oldest.DeliveryID != firstID ||
		conflict.Oldest.State != "pending" || conflict.Oldest.Seen {
		t.Fatalf("unexpected conflict: %+v", conflict)
	}
	firstBeforeAck := swarmActionGetEvent(t, f, first.EventID).Deliveries[0]
	if firstBeforeAck.State != "pending" || firstBeforeAck.Attempts != 0 || firstBeforeAck.Seen {
		t.Fatalf("conflict changed the oldest delivery: %+v", firstBeforeAck)
	}

	ackFirst := swarmActionDeliveryRequest(f, firstID)
	for range 2 {
		done := swarmActionPost[core.SwarmAcknowledgeResult](t, f, "acknowledge", ackFirst)
		if done.Delivery == nil || done.Delivery.DeliveryID != firstID || done.Delivery.State != "handled" ||
			done.Delivery.Attempts != 1 || !done.Delivery.Seen {
			t.Fatalf("ack and replay: %+v", done.Delivery)
		}
	}
	// Reusing a successful request UUID with another expected delivery can
	// never acknowledge the second item.
	changed := ackFirst
	changed.DeliveryID = secondID
	resp, raw = f.do(http.MethodPost, "/api/swarm/action/acknowledge", changed, header)
	if resp.StatusCode != http.StatusConflict || swarmErrorCode(raw) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("changed retry: %d %s", resp.StatusCode, raw)
	}
	done := swarmActionPost[core.SwarmAcknowledgeResult](t, f, "acknowledge", ackSecond)
	if done.Delivery == nil || done.Delivery.DeliveryID != secondID || done.Delivery.State != "handled" {
		t.Fatalf("ack second: %+v", done.Delivery)
	}
	empty := swarmActionPost[core.SwarmAcknowledgeResult](t, f, "acknowledge", swarmActionDeliveryRequest(f, secondID))
	if empty.Delivery != nil {
		t.Fatalf("empty inbox: %+v", empty.Delivery)
	}
	foreign := swarmActionPost[core.SwarmEventDetails](t, f, "send", swarmActionSend(f, member.Inbox, "request", "The member owns this delivery"))
	resp, raw = f.do(http.MethodPost, "/api/swarm/action/acknowledge", swarmActionDeliveryRequest(f, foreign.Deliveries[0].DeliveryID), header)
	if resp.StatusCode != http.StatusForbidden || swarmErrorCode(raw) != "AUTHORITY_DENIED" {
		t.Fatalf("ack of another inbox: %d %s", resp.StatusCode, raw)
	}
}

func TestSwarmActionHTTPCancelDeliveryAndCompletionRace(t *testing.T) {
	ctx := context.Background()
	f := newSwarmStoreFixture(t, swarmNoPanes)
	member, view := f.member("action-cancel-member")
	sent := swarmActionPost[core.SwarmEventDetails](t, f, "send", swarmActionSend(f, member.Inbox, "request", "Cancel before claim"))
	id := sent.Deliveries[0].DeliveryID
	preview := func(id string) core.SwarmCancelPreview {
		t.Helper()
		resp, raw := f.do(http.MethodGet, "/api/swarm/action/cancel-preview?campaign="+f.campaign+"&delivery_id="+id, nil, nil)
		return swarmActionJSON[core.SwarmCancelPreview](t, resp, raw, http.StatusOK)
	}
	p := preview(id)
	if !p.Cancellable || p.Delivery.DeliveryID != id || p.Delivery.Consumer != member.Inbox || p.DirectReplyWaits.Status != "empty" {
		t.Fatalf("cancel preview: %+v", p)
	}
	req := core.SwarmCancelDeliveryRequest{
		SwarmDeliveryActionRequest: swarmActionDeliveryRequest(f, id), Reason: "The owner called off the work",
	}
	cancel := swarmActionPost[core.SwarmCancelDeliveryResult](t, f, "cancel-delivery", req)
	retry := swarmActionPost[core.SwarmCancelDeliveryResult](t, f, "cancel-delivery", req)
	if cancel.Outcome != "cancelled" || cancel.Delivery.Code != "operator_cancelled" || retry.Outcome != "cancelled" ||
		cancel.Delivery.CompletedAt == nil || retry.Delivery.CompletedAt == nil || !retry.Delivery.CompletedAt.Equal(*cancel.Delivery.CompletedAt) {
		t.Fatalf("cancel and replay: %+v %+v", cancel, retry)
	}

	racing := swarmActionPost[core.SwarmEventDetails](t, f, "send", swarmActionSend(f, member.Inbox, "request", "Recipient finishes before confirmation"))
	id = racing.Deliveries[0].DeliveryID
	if p := preview(id); !p.Cancellable {
		t.Fatalf("race test preview: %+v", p)
	}
	claimed, err := view.NextEvent(ctx, core.NextEventRequest{Repo: f.repo}, f.dest)
	if err != nil || claimed.Delivery == nil || claimed.Delivery.DeliveryID != id {
		t.Fatalf("claim: %+v %v", claimed, err)
	}
	req.SwarmDeliveryActionRequest = swarmActionDeliveryRequest(f, id)
	header := map[string]string{"X-CSRF-Token": f.csrf}
	resp, raw := f.do(http.MethodPost, "/api/swarm/action/cancel-delivery", req, header)
	if resp.StatusCode >= 500 || swarmErrorCode(raw) != "UNSUPPORTED_CONTROL" {
		t.Fatalf("manual live lease: %d %s", resp.StatusCode, raw)
	}
	if _, err := view.CompleteEvent(ctx, core.CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: id, LeaseID: claimed.Delivery.LeaseID, Disposition: "handled",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	race := swarmActionPost[core.SwarmCancelDeliveryResult](t, f, "cancel-delivery", req)
	if race.Outcome != "already_terminal" || race.Delivery.DeliveryID != id || race.Delivery.State != "handled" {
		t.Fatalf("completion outcome: %+v", race)
	}
}

func TestSwarmActionHTTPCancelWaitAndForeignResources(t *testing.T) {
	ctx := context.Background()
	f := newSwarmStoreFixture(t, swarmNoPanes)
	member, _ := f.member("action-wait-member")
	outsider, outsiderView := f.session("action-wait-outsider")
	if _, err := f.op.SetCoordinationWaitsEnabled(ctx, core.WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := f.op.SetCoordinationWaitsEnabled(ctx, core.WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: false}); err != nil {
			t.Error(err)
		}
	})
	dependency := swarmActionPublishEvent(t, f, f.rootView, "request", member.Inbox, "", "")
	foreign := swarmActionPublishEvent(t, f, outsiderView, "request", outsider.Inbox, "", "")
	wait := func(view *core.Store, id string) core.AgentWait {
		t.Helper()
		w, err := view.CreateWait(ctx, core.CreateWaitRequest{
			RequestID: uuid.NewString(), Collection: f.repo, DependencyEventUUID: id,
			PredicateKind: core.WaitPredicateRequestResponse, Deadline: time.Now().Add(time.Hour),
		}, f.dest)
		if err != nil {
			t.Fatal(err)
		}
		return w
	}
	ownWait := wait(f.rootView, dependency.EventID)
	foreignWait := wait(outsiderView, foreign.EventID)
	req := core.SwarmCancelWaitRequest{SwarmActionRequest: swarmActionRequest(f), WaitID: ownWait.WaitUUID}
	for range 2 {
		out := swarmActionPost[core.SwarmCancelWaitResult](t, f, "cancel-wait", req)
		if out.Outcome != "cancelled" || out.Wait.State != core.WaitStateCancelled || out.Wait.WaitID != ownWait.WaitUUID {
			t.Fatalf("cancel wait and replay: %+v", out)
		}
	}
	req.RequestID = uuid.NewString()
	terminal := swarmActionPost[core.SwarmCancelWaitResult](t, f, "cancel-wait", req)
	if terminal.Outcome != "already_terminal" || terminal.Wait.State != core.WaitStateCancelled {
		t.Fatalf("terminal wait: %+v", terminal)
	}

	header := map[string]string{"X-CSRF-Token": f.csrf}
	req.WaitID, req.RequestID = foreignWait.WaitUUID, uuid.NewString()
	resp, raw := f.do(http.MethodPost, "/api/swarm/action/cancel-wait", req, header)
	if resp.StatusCode != http.StatusNotFound || swarmErrorCode(raw) != "NOT_FOUND" {
		t.Fatalf("foreign wait: %d %s", resp.StatusCode, raw)
	}
	status, err := outsiderView.AgentEventStatus(ctx, core.EventStatusRequest{EventID: foreign.EventID}, f.dest)
	if err != nil || len(status.Deliveries) != 1 {
		t.Fatalf("fixture foreign delivery: %+v %v", status, err)
	}
	foreignID := status.Deliveries[0].DeliveryID
	cancel := core.SwarmCancelDeliveryRequest{
		SwarmDeliveryActionRequest: swarmActionDeliveryRequest(f, foreignID), Reason: "Foreign delivery must not be changed",
	}
	resp, raw = f.do(http.MethodPost, "/api/swarm/action/cancel-delivery", cancel, header)
	if resp.StatusCode != http.StatusNotFound || swarmErrorCode(raw) != "NOT_FOUND" {
		t.Fatalf("foreign delivery: %d %s", resp.StatusCode, raw)
	}
	for _, path := range []string{
		"/api/swarm/action/cancel-preview?campaign=" + f.campaign + "&delivery_id=" + foreignID,
		"/api/swarm/event?campaign=" + f.campaign + "&event_id=" + foreign.EventID,
	} {
		resp, raw := f.do(http.MethodGet, path, nil, nil)
		if resp.StatusCode != http.StatusNotFound || swarmErrorCode(raw) != "NOT_FOUND" {
			t.Fatalf("foreign read %s: %d %s", path, resp.StatusCode, raw)
		}
	}
}
