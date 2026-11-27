package main

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5/pgconn"
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
	changed := req
	changed.Body = ""
	resp, raw := f.do(http.MethodPost, "/api/swarm/action/send", changed, map[string]string{"X-CSRF-Token": f.csrf})
	if resp.StatusCode != http.StatusConflict || swarmErrorCode(raw) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("successful send UUID reused with an empty body: %d %s", resp.StatusCode, raw)
	}
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
	ctx := context.Background()
	f := newSwarmStoreFixture(t, swarmNoPanes)
	member, view := f.member("action-ack-member")
	operator := f.op.Channel().Principal
	_, outside := f.session("action-ack-outsider")
	expired := time.Now().Add(-time.Minute)
	foreignExpired, err := outside.PublishEvent(ctx, core.PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "request", AdmissionExpiresAt: &expired,
		Ref:         core.RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: core.EventDestination{Type: "agent", Name: operator},
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
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
	status, err := outside.AgentEventStatus(ctx, core.EventStatusRequest{EventID: foreignExpired.EventID}, f.dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "pending" {
		t.Fatalf("scoped acknowledgement changed the foreign delivery: %+v %v", status, err)
	}
	foreignEvent := swarmActionPublishEvent(t, f, f.rootView, "request", member.Inbox, "", "")
	foreign := swarmActionGetEvent(t, f, foreignEvent.EventID)
	resp, raw = f.do(http.MethodPost, "/api/swarm/action/acknowledge", swarmActionDeliveryRequest(f, foreign.Deliveries[0].DeliveryID), header)
	if resp.StatusCode != http.StatusForbidden || swarmErrorCode(raw) != "AUTHORITY_DENIED" {
		t.Fatalf("ack of another inbox: %d %s", resp.StatusCode, raw)
	}
}

func TestSwarmActionHTTPAcknowledgeLandingScope(t *testing.T) {
	f := newSwarmStoreFixture(t, swarmNoPanes)
	_, outside := f.session("action-landing-outsider")
	operator := f.op.Channel().Principal
	first := swarmActionPublishEvent(t, f, outside, "request", operator, "", "")
	second := swarmActionPublishEvent(t, f, outside, "notice", operator, "", "")
	inside := swarmActionPublishEvent(t, f, f.rootView, "notice", operator, "", "")
	operatorNeeds := func(landing bool) map[string]core.SwarmNeed {
		t.Helper()
		var needs core.SwarmNeedsYou
		if landing {
			resp, raw := f.do(http.MethodGet, "/api/swarm/landing", nil, nil)
			page := swarmActionJSON[core.SwarmLanding](t, resp, raw, http.StatusOK)
			containsEvent := func(id string) bool {
				return slices.ContainsFunc(page.Events, func(event core.SwarmEvent) bool { return event.EventID == id })
			}
			if !containsEvent(first.EventID) || !containsEvent(second.EventID) || containsEvent(inside.EventID) {
				t.Fatalf("unexpected landing events: %+v", page.Events)
			}
			needs = page.NeedsYou
		} else {
			resp, raw := f.do(http.MethodGet, "/api/swarm/campaign?campaign="+f.campaign, nil, nil)
			needs = swarmActionJSON[core.SwarmSnapshot](t, resp, raw, http.StatusOK).NeedsYou
		}
		items := make(map[string]core.SwarmNeed)
		for _, item := range needs.Items {
			if item.Type == core.SwarmNeedOperatorDelivery {
				items[item.Refs.EventID] = item
			}
		}
		return items
	}
	needID := func(items map[string]core.SwarmNeed, e core.AgentEvent, head bool) string {
		t.Helper()
		item := items[e.EventID]
		if item.InboxHead != head || len(item.Refs.DeliveryIDs) != 1 {
			t.Fatalf("event %s: expected delivery with inbox_head=%t, got %+v", e.EventID, head, item)
		}
		return item.Refs.DeliveryIDs[0]
	}
	landing, campaign := operatorNeeds(true), operatorNeeds(false)
	if len(landing) != 2 || len(campaign) != 1 {
		t.Fatalf("unexpected operator needs: landing=%+v campaign=%+v", landing, campaign)
	}
	firstID := needID(landing, first, true)
	if item := landing[first.EventID]; item.Action.Kind != "acknowledge" {
		t.Fatalf("landing request must offer acknowledgement: %+v", item.Action)
	}
	secondID := needID(landing, second, false)
	insideID := needID(campaign, inside, false)
	firstAck := swarmActionDeliveryRequest(f, firstID)
	firstAck.Campaign = ""
	secondAck := swarmActionDeliveryRequest(f, secondID)
	secondAck.Campaign = ""
	insideAck := swarmActionDeliveryRequest(f, insideID)
	swarmActionAssertNeutralAck(t, f, secondAck, first.EventID)
	swarmActionAssertNeutralAck(t, f, insideAck, first.EventID)
	changed := firstAck
	changed.Campaign = f.campaign
	swarmActionAssertNeutralAck(t, f, changed, first.EventID)
	swarmActionAssertHandled(t, f, firstAck)
	swarmActionAssertHandled(t, f, firstAck)
	resp, raw := f.do(http.MethodPost, "/api/swarm/action/acknowledge", changed, map[string]string{"X-CSRF-Token": f.csrf})
	if resp.StatusCode != http.StatusConflict || swarmErrorCode(raw) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("retry with changed campaign: %d %s", resp.StatusCode, raw)
	}
	needID(operatorNeeds(true), second, true)
	swarmActionAssertHandled(t, f, secondAck)
	if remaining := operatorNeeds(true); len(remaining) != 0 {
		t.Fatalf("unexpected landing needs after acknowledgement: %+v", remaining)
	}
	needID(operatorNeeds(false), inside, true)
	wrong := insideAck
	wrong.Campaign = ""
	swarmActionAssertNeutralAck(t, f, wrong, inside.EventID)
	swarmActionAssertHandled(t, f, insideAck)
}

func swarmActionAssertNeutralAck(t *testing.T, f *swarmStoreFixture, req core.SwarmDeliveryActionRequest, headEventID string) {
	t.Helper()
	resp, raw := f.do(http.MethodPost, "/api/swarm/action/acknowledge", req, map[string]string{"X-CSRF-Token": f.csrf})
	conflict := swarmActionJSON[struct {
		Error    string          `json:"error"`
		Code     string          `json:"code"`
		Expected string          `json:"expected"`
		Oldest   json.RawMessage `json:"oldest"`
	}](t, resp, raw, http.StatusConflict)
	if conflict.Error != "VERSION_CONFLICT: the delivery is not the oldest in the operator's inbox" ||
		conflict.Code != "VERSION_CONFLICT" || conflict.Expected != req.DeliveryID || string(conflict.Oldest) != "null" ||
		strings.Contains(string(raw), headEventID) {
		t.Fatalf("expected neutral inbox-head conflict from %q: %s", req.Campaign, raw)
	}
}

func swarmActionAssertHandled(t *testing.T, f *swarmStoreFixture, req core.SwarmDeliveryActionRequest) {
	t.Helper()
	out := swarmActionPost[core.SwarmAcknowledgeResult](t, f, "acknowledge", req)
	if got := out.Delivery; got == nil || got.DeliveryID != req.DeliveryID || got.State != "handled" || !got.Seen || got.Attempts != 1 {
		t.Fatalf("acknowledgement for %s from %q: %+v", req.DeliveryID, req.Campaign, got)
	}
}

func TestSwarmActionHTTPInboxHasOneOwnerPagePerDelivery(t *testing.T) {
	ctx := context.Background()
	f := newSwarmStoreFixture(t, swarmNoPanes)
	operator := f.op.Channel().Principal
	const beta, closed = "beta", "closing"
	setCampaign := func(view *core.Store, name, policy string, active *bool) {
		t.Helper()
		if _, err := view.SetTeamCampaign(ctx, core.SetTeamCampaignRequest{
			RequestID: uuid.NewString(), Repo: f.repo, Campaign: name, OutOfChainPolicy: policy, Active: active,
		}, f.dest); err != nil {
			t.Fatal(err)
		}
	}
	type item struct {
		event core.AgentEvent
		id    string
		page  string
	}
	publish := func(view *core.Store, kind, page string) item {
		t.Helper()
		event := swarmActionPublishEvent(t, f, view, kind, operator, "", "")
		status, err := view.AgentEventStatus(ctx, core.EventStatusRequest{EventID: event.EventID}, f.dest)
		if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].State != "pending" {
			t.Fatalf("seed delivery: %+v %v", status, err)
		}
		return item{event, status.Deliveries[0].DeliveryID, page}
	}
	_, closingRoot := f.session("inbox-closing-root")
	setCampaign(closingRoot, closed, "warn", nil)
	fromClosed := publish(closingRoot, "notice", "")
	mover, moverView := f.member("inbox-mover")
	setCampaign(f.rootView, f.campaign, "warn", nil)
	routed := publish(moverView, "notice", f.campaign)
	setCampaign(f.rootView, f.campaign, "allow", nil)
	moved := publish(moverView, "notice", beta)
	bRoot, bView := f.session("inbox-beta-root")
	beforeBeta := publish(bView, "request", beta)
	setCampaign(bView, beta, "allow", nil)
	inactive := false
	setCampaign(closingRoot, closed, "", &inactive)
	if _, err := f.rootView.SetTeamMember(ctx, core.SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign, Member: mover.Inbox, Active: &inactive,
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	if _, err := bView.SetTeamMember(ctx, core.SetTeamMemberRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: beta, Member: mover.Inbox, Lead: bRoot.Inbox,
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	afterMove := publish(moverView, "notice", beta)
	if fromClosed.event.Routing == nil || fromClosed.event.Routing.Campaign != closed ||
		routed.event.Routing == nil || routed.event.Routing.Campaign != f.campaign || moved.event.Routing != nil {
		t.Fatal("fixture must contain routed notices for both campaigns and an unrouted notice from the moved publisher")
	}
	// The moved delivery is still readable in the former campaign's log.
	if old := swarmActionGetEvent(t, f, afterMove.event.EventID); old.Event.EventID != afterMove.event.EventID {
		t.Fatalf("former campaign lost historical event: %+v", old.Event)
	}
	resp, raw := f.do(http.MethodGet, "/api/swarm/event?campaign="+closed+"&event_id="+fromClosed.event.EventID, nil, nil)
	swarmActionJSON[core.SwarmEventDetails](t, resp, raw, http.StatusOK)
	pages := []string{"", f.campaign, beta, closed}
	checkPartition := func(pending []item) {
		t.Helper()
		for _, page := range pages {
			var needs core.SwarmNeedsYou
			if page == "" {
				resp, raw := f.do(http.MethodGet, "/api/swarm/landing", nil, nil)
				needs = swarmActionJSON[core.SwarmLanding](t, resp, raw, http.StatusOK).NeedsYou
			} else {
				resp, raw := f.do(http.MethodGet, "/api/swarm/campaign?campaign="+page, nil, nil)
				needs = swarmActionJSON[core.SwarmSnapshot](t, resp, raw, http.StatusOK).NeedsYou
			}
			want := make(map[string]bool)
			for i, item := range pending {
				if item.page == page {
					want[item.id] = i == 0
				}
			}
			for _, row := range needs.Items {
				if row.Type != core.SwarmNeedOperatorDelivery {
					continue
				}
				if len(row.Refs.DeliveryIDs) != 1 {
					t.Fatalf("page %q: invalid inbox row %+v", page, row)
				}
				id := row.Refs.DeliveryIDs[0]
				if head, ok := want[id]; !ok || row.InboxHead != head {
					t.Fatalf("page %q: unexpected delivery %s with inbox_head=%t", page, id, row.InboxHead)
				}
				delete(want, id)
			}
			if len(want) != 0 {
				t.Fatalf("page %q: missing deliveries and head flags %+v", page, want)
			}
		}
	}
	queue := []item{fromClosed, routed, moved, beforeBeta, afterMove}
	for i, head := range queue {
		checkPartition(queue[i:])
		for _, page := range pages {
			if page == head.page {
				continue
			}
			wrong := swarmActionDeliveryRequest(f, head.id)
			wrong.Campaign = page
			swarmActionAssertNeutralAck(t, f, wrong, head.event.EventID)
		}
		ack := swarmActionDeliveryRequest(f, head.id)
		ack.Campaign = head.page
		swarmActionAssertHandled(t, f, ack)
	}
	checkPartition(nil)
}

func TestSwarmActionHTTPCampaignRequiredExceptAcknowledge(t *testing.T) {
	f := newSwarmStoreFixture(t, swarmNoPanes)
	e := swarmActionPublishEvent(t, f, f.rootView, "notice", f.op.Channel().Principal, "", "")
	id := swarmActionGetEvent(t, f, e.EventID).Deliveries[0].DeliveryID
	delivery := swarmActionDeliveryRequest(f, id)
	delivery.Campaign = ""
	send := swarmActionSend(f, f.root.Inbox, "notice", "A campaign is required")
	send.Campaign = ""
	for _, tc := range []struct {
		name, method, path string
		body               any
	}{
		{"send", http.MethodPost, "/api/swarm/action/send", send},
		{"cancel-delivery", http.MethodPost, "/api/swarm/action/cancel-delivery", core.SwarmCancelDeliveryRequest{SwarmDeliveryActionRequest: delivery, Reason: "A campaign is required"}},
		{"cancel-wait", http.MethodPost, "/api/swarm/action/cancel-wait", core.SwarmCancelWaitRequest{SwarmActionRequest: delivery.SwarmActionRequest, WaitID: uuid.NewString()}},
		{"cancel-preview", http.MethodGet, "/api/swarm/action/cancel-preview?campaign=&delivery_id=" + id, nil},
		{"event", http.MethodGet, "/api/swarm/event?campaign=&event_id=" + e.EventID, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			resp, raw := f.do(tc.method, tc.path, tc.body, map[string]string{"X-CSRF-Token": f.csrf})
			if resp.StatusCode != http.StatusBadRequest || swarmErrorCode(raw) != "INVALID_REQUEST" {
				t.Fatalf("empty campaign: %d %s", resp.StatusCode, raw)
			}
		})
	}
}

func swarmActionPreview(t *testing.T, f *swarmStoreFixture, id string) core.SwarmCancelPreview {
	t.Helper()
	resp, raw := f.do(http.MethodGet, "/api/swarm/action/cancel-preview?campaign="+f.campaign+"&delivery_id="+id, nil, nil)
	return swarmActionJSON[core.SwarmCancelPreview](t, resp, raw, http.StatusOK)
}

func TestSwarmActionHTTPCancelDeliveryAndCompletionRace(t *testing.T) {
	ctx := context.Background()
	f := newSwarmStoreFixture(t, swarmNoPanes)
	member, view := f.member("action-cancel-member")
	sent := swarmActionPost[core.SwarmEventDetails](t, f, "send", swarmActionSend(f, member.Inbox, "request", "Cancel before claim"))
	id := sent.Deliveries[0].DeliveryID
	p := swarmActionPreview(t, f, id)
	if !p.Cancellable || p.Delivery.DeliveryID != id || p.Delivery.Consumer != member.Inbox || p.DirectReplyWaits.Status != "empty" {
		t.Fatalf("cancel preview: %+v", p)
	}
	req := core.SwarmCancelDeliveryRequest{
		SwarmDeliveryActionRequest: swarmActionDeliveryRequest(f, id), Reason: "The owner called off the work", PreviewToken: p.PreviewToken,
	}
	cancel := swarmActionPost[core.SwarmCancelDeliveryResult](t, f, "cancel-delivery", req)
	retry := swarmActionPost[core.SwarmCancelDeliveryResult](t, f, "cancel-delivery", req)
	if cancel.Outcome != "cancelled" || cancel.Delivery.Code != "operator_cancelled" || retry.Outcome != "cancelled" ||
		cancel.Delivery.CompletedAt == nil || retry.Delivery.CompletedAt == nil || !retry.Delivery.CompletedAt.Equal(*cancel.Delivery.CompletedAt) {
		t.Fatalf("cancel and replay: %+v %+v", cancel, retry)
	}

	racing := swarmActionPost[core.SwarmEventDetails](t, f, "send", swarmActionSend(f, member.Inbox, "request", "Recipient finishes before confirmation"))
	id = racing.Deliveries[0].DeliveryID
	p = swarmActionPreview(t, f, id)
	if !p.Cancellable {
		t.Fatalf("race test preview: %+v", p)
	}
	claimed, err := view.NextEvent(ctx, core.NextEventRequest{Repo: f.repo}, f.dest)
	if err != nil || claimed.Delivery == nil || claimed.Delivery.DeliveryID != id {
		t.Fatalf("claim: %+v %v", claimed, err)
	}
	req.SwarmDeliveryActionRequest = swarmActionDeliveryRequest(f, id)
	req.PreviewToken = p.PreviewToken
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

func swarmActionEnableWaits(t *testing.T, f *swarmStoreFixture) {
	t.Helper()
	ctx := context.Background()
	if _, err := f.op.SetCoordinationWaitsEnabled(ctx, core.WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: true}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := f.op.SetCoordinationWaitsEnabled(ctx, core.WaitFeatureSetRequest{RequestID: uuid.NewString(), Enabled: false}); err != nil {
			t.Error(err)
		}
	})
}

func TestSwarmActionHTTPCancelWaitAndForeignResources(t *testing.T) {
	ctx := context.Background()
	f := newSwarmStoreFixture(t, swarmNoPanes)
	member, _ := f.member("action-wait-member")
	outsider, outsiderView := f.session("action-wait-outsider")
	swarmActionEnableWaits(t, f)
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
	peerDependency := swarmActionPublishEvent(t, f, outsiderView, "request", member.Inbox, "", "")
	foreignWait := wait(outsiderView, peerDependency.EventID)
	snapshot, err := f.op.GetSwarmSnapshot(ctx, core.SwarmSnapshotRequest{Repo: f.repo, Campaign: f.campaign})
	if err != nil {
		t.Fatal(err)
	}
	visible := slices.ContainsFunc(snapshot.Flow.Waits, func(w core.SwarmWait) bool { return w.WaitID == foreignWait.WaitUUID })
	if !visible {
		t.Fatal("the outside wait must be visible through its dependency")
	}

	header := map[string]string{"X-CSRF-Token": f.csrf}
	req := core.SwarmCancelWaitRequest{
		SwarmActionRequest: swarmActionRequest(f), WaitID: ownWait.WaitUUID, ExpectedOwner: outsider.Inbox,
	}
	resp, raw := f.do(http.MethodPost, "/api/swarm/action/cancel-wait", req, header)
	conflict := swarmActionJSON[struct {
		Code string         `json:"code"`
		Wait core.SwarmWait `json:"wait"`
	}](t, resp, raw, http.StatusConflict)
	if conflict.Code != "VERSION_CONFLICT" || conflict.Wait.WaitID != ownWait.WaitUUID ||
		conflict.Wait.Owner != f.root.Inbox || conflict.Wait.State != core.WaitStateWaiting {
		t.Fatalf("changed owner confirmation: %+v", conflict)
	}
	req.ExpectedOwner = ownWait.OwnerPrincipal
	for range 2 {
		out := swarmActionPost[core.SwarmCancelWaitResult](t, f, "cancel-wait", req)
		if out.Outcome != "cancelled" || out.Wait.State != core.WaitStateCancelled || out.Wait.WaitID != ownWait.WaitUUID {
			t.Fatalf("cancel wait and replay: %+v", out)
		}
	}
	req.ExpectedOwner = outsider.Inbox
	resp, raw = f.do(http.MethodPost, "/api/swarm/action/cancel-wait", req, header)
	if resp.StatusCode != http.StatusConflict || swarmErrorCode(raw) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("reused wait cancellation with changed owner: %d %s", resp.StatusCode, raw)
	}
	req.RequestID, req.ExpectedOwner = uuid.NewString(), ownWait.OwnerPrincipal
	resp, raw = f.do(http.MethodPost, "/api/swarm/action/cancel-wait", req, header)
	if resp.StatusCode != http.StatusNotFound || swarmErrorCode(raw) != "NOT_FOUND" {
		t.Fatalf("terminal wait: %d %s", resp.StatusCode, raw)
	}
	terminalError := string(raw)
	req.WaitID, req.RequestID, req.ExpectedOwner = foreignWait.WaitUUID, uuid.NewString(), outsider.Inbox
	resp, raw = f.do(http.MethodPost, "/api/swarm/action/cancel-wait", req, header)
	if resp.StatusCode != http.StatusNotFound || string(raw) != terminalError {
		t.Fatalf("wait visible through its dependency: %d %s", resp.StatusCode, raw)
	}
	req.WaitID, req.RequestID = uuid.NewString(), uuid.NewString()
	resp, raw = f.do(http.MethodPost, "/api/swarm/action/cancel-wait", req, header)
	if resp.StatusCode != http.StatusNotFound || string(raw) != terminalError {
		t.Fatalf("unknown wait: %d %s", resp.StatusCode, raw)
	}
	status, err := outsiderView.AgentEventStatus(ctx, core.EventStatusRequest{EventID: foreign.EventID}, f.dest)
	if err != nil || len(status.Deliveries) != 1 {
		t.Fatalf("fixture foreign delivery: %+v %v", status, err)
	}
	foreignID := status.Deliveries[0].DeliveryID
	cancel := core.SwarmCancelDeliveryRequest{
		SwarmDeliveryActionRequest: swarmActionDeliveryRequest(f, foreignID), Reason: "Foreign delivery must not be changed", PreviewToken: strings.Repeat("0", 64),
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

func TestSwarmActionHTTPCancelRequiresFreshPreviewAfterHandoff(t *testing.T) {
	ctx := context.Background()
	f := newSwarmStoreFixture(t, swarmNoPanes)
	sent := swarmActionPost[core.SwarmEventDetails](t, f, "send",
		swarmActionSend(f, "role/"+f.campaign+"/root", "request", "Pending coordinator work"))
	id := sent.Deliveries[0].DeliveryID
	preview := swarmActionPreview(t, f, id)
	if preview.PreviewToken == "" || preview.Delivery.Consumer != f.root.Inbox {
		t.Fatalf("initial preview: %+v", preview)
	}
	req := core.SwarmCancelDeliveryRequest{
		SwarmDeliveryActionRequest: swarmActionDeliveryRequest(f, id),
		Reason:                     "The owner called off the coordinator work", PreviewToken: preview.PreviewToken,
	}
	handoff, err := f.rootView.HandoffCampaignCoordinator(ctx, core.HandoffCampaignCoordinatorRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign,
		NewSession: &core.NewCoordinatorSessionSpec{Binding: "swarm-root", NativeSessionID: "action-cancel-successor"},
		Note:       "Transfer the pending request", Shareable: true,
	}, f.dest)
	if err != nil || !handoff.Performed || handoff.ToPrincipal == f.root.Inbox || handoff.TransferredDeliveries != 1 {
		t.Fatalf("handoff fixture: %+v %v", handoff, err)
	}
	header := map[string]string{"X-CSRF-Token": f.csrf}
	path := "/api/swarm/action/cancel-delivery"
	resp, raw := f.do(http.MethodPost, path, req, header)
	conflict := swarmActionJSON[struct {
		Code    string                  `json:"code"`
		Preview core.SwarmCancelPreview `json:"preview"`
	}](t, resp, raw, http.StatusConflict)
	fresh := conflict.Preview
	if conflict.Code != "VERSION_CONFLICT" || fresh.PreviewToken == "" || fresh.PreviewToken == req.PreviewToken ||
		fresh.Delivery.DeliveryID != id || fresh.Delivery.Consumer != handoff.ToPrincipal || fresh.Delivery.State != "pending" {
		t.Fatalf("handoff confirmation: %+v", conflict)
	}
	current := swarmActionPreview(t, f, id)
	if current.PreviewToken != fresh.PreviewToken || current.Delivery.State != "pending" || current.Delivery.Seen {
		t.Fatalf("refusal changed the successor delivery: %+v", current)
	}
	old := req
	req.PreviewToken = fresh.PreviewToken
	cancelled := swarmActionPost[core.SwarmCancelDeliveryResult](t, f, "cancel-delivery", req)
	if cancelled.Outcome != "cancelled" || cancelled.Delivery.Consumer != handoff.ToPrincipal {
		t.Fatalf("confirmed cancellation: %+v", cancelled)
	}
	resp, raw = f.do(http.MethodPost, path, old, header)
	if resp.StatusCode != http.StatusConflict || swarmErrorCode(raw) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("successful UUID reused with old token: %d %s", resp.StatusCode, raw)
	}
	req.PreviewToken = ""
	resp, raw = f.do(http.MethodPost, path, req, header)
	if resp.StatusCode != http.StatusConflict || swarmErrorCode(raw) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("successful UUID reused with missing token: %d %s", resp.StatusCode, raw)
	}
}

func TestSwarmActionHTTPForeignInboxHeadConflictIsNeutral(t *testing.T) {
	f := newSwarmStoreFixture(t, swarmNoPanes)
	_, outside := f.session("action-head-outsider")
	operator := f.op.Channel().Principal
	foreign := swarmActionPublishEvent(t, f, outside, "notice", operator, "", "")
	expected := swarmActionPublishEvent(t, f, f.rootView, "notice", operator, "", "")
	id := swarmActionGetEvent(t, f, expected.EventID).Deliveries[0].DeliveryID
	resp, raw := f.do(http.MethodPost, "/api/swarm/action/acknowledge", swarmActionDeliveryRequest(f, id),
		map[string]string{"X-CSRF-Token": f.csrf})
	conflict := swarmActionJSON[struct {
		Error    string              `json:"error"`
		Code     string              `json:"code"`
		Expected string              `json:"expected"`
		Oldest   *core.SwarmDelivery `json:"oldest"`
	}](t, resp, raw, http.StatusConflict)
	if conflict.Code != "VERSION_CONFLICT" || conflict.Expected != id || conflict.Oldest != nil ||
		conflict.Error != "VERSION_CONFLICT: the delivery is not the oldest in the operator's inbox" ||
		!strings.Contains(string(raw), `"oldest":null`) || strings.Contains(string(raw), foreign.EventID) {
		t.Fatalf("foreign head conflict: %s", raw)
	}
}

func TestSwarmActionStoreErrorsUsePublicMessages(t *testing.T) {
	dbErr := &pgconn.PgError{Code: "42P01", Message: `relation "private_store_relation" does not exist`}
	for _, tc := range []struct {
		name    string
		err     error
		status  int
		code    string
		message string
	}{
		{"database error", dbErr, http.StatusInternalServerError, "STORE_ERROR", "store operation failed"},
		{"wrapped database error", fmt.Errorf("could not read preview: %w", dbErr), http.StatusInternalServerError, "STORE_ERROR", "store operation failed"},
		{"typed database error", &core.Error{Code: "VERSION_CONFLICT", Message: dbErr.Error(), Cause: dbErr}, http.StatusConflict, "VERSION_CONFLICT", "store operation failed"},
		{"timeout", context.DeadlineExceeded, http.StatusGatewayTimeout, "TIMEOUT", "store operation failed"},
		{"session ended", context.Canceled, http.StatusUnauthorized, "UNAUTHENTICATED", "the session ended before the request completed; sign in again"},
		{"public refusal", &core.Error{Code: "NOT_FOUND", Message: "delivery not found in this campaign"}, http.StatusNotFound, "NOT_FOUND", "NOT_FOUND: delivery not found in this campaign"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rec := httptest.NewRecorder()
			writeSwarmActionResult(rec, nil, tc.err)
			out := swarmActionJSON[struct {
				Code    string `json:"code"`
				Message string `json:"error"`
			}](t, rec.Result(), rec.Body.Bytes(), tc.status)
			if out.Code != tc.code || out.Message != tc.message {
				t.Fatalf("public refusal: %+v", out)
			}
		})
	}
}

func TestSwarmActionHTTPCancelPreviewTracksWaitOwnerAfterHandoff(t *testing.T) {
	ctx := context.Background()
	f := newSwarmStoreFixture(t, swarmNoPanes)
	swarmActionEnableWaits(t, f)
	worker, _ := f.member("action-preview-wait-worker")
	event := swarmActionPublishEvent(t, f, f.rootView, "request", worker.Inbox, "", "")
	wait, err := f.rootView.CreateWait(ctx, core.CreateWaitRequest{
		RequestID: uuid.NewString(), Collection: f.repo, DependencyEventUUID: event.EventID,
		PredicateKind: core.WaitPredicateRequestResponse, Deadline: time.Now().Add(time.Hour),
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	id := swarmActionGetEvent(t, f, event.EventID).Deliveries[0].DeliveryID
	before := swarmActionPreview(t, f, id)
	if len(before.DirectReplyWaits.Waits) != 1 || before.DirectReplyWaits.Waits[0].Owner != f.root.Inbox {
		t.Fatalf("original wait owner: %+v", before.DirectReplyWaits)
	}

	handoff, err := f.rootView.HandoffCampaignCoordinator(ctx, core.HandoffCampaignCoordinatorRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: f.campaign,
		NewSession: &core.NewCoordinatorSessionSpec{Binding: "swarm-root", NativeSessionID: "action-preview-wait-successor"},
		Note:       "Transfer the active wait", Shareable: true,
	}, f.dest)
	if err != nil || !handoff.Performed || handoff.ToPrincipal == f.root.Inbox || handoff.TransferredDeliveries != 0 {
		t.Fatalf("wait owner handoff: %+v %v", handoff, err)
	}
	req := core.SwarmCancelDeliveryRequest{
		SwarmDeliveryActionRequest: swarmActionDeliveryRequest(f, id),
		Reason:                     "Cancel the delivery with the reviewed waiter", PreviewToken: before.PreviewToken,
	}
	resp, raw := f.do(http.MethodPost, "/api/swarm/action/cancel-delivery", req, map[string]string{"X-CSRF-Token": f.csrf})
	conflict := swarmActionJSON[struct {
		Code    string                  `json:"code"`
		Preview core.SwarmCancelPreview `json:"preview"`
	}](t, resp, raw, http.StatusConflict)
	fresh := conflict.Preview
	if conflict.Code != "VERSION_CONFLICT" || fresh.PreviewToken == before.PreviewToken ||
		fresh.Delivery.DeliveryID != id || fresh.Delivery.Consumer != worker.Inbox ||
		fresh.Delivery.State != "pending" || fresh.Delivery.Seen ||
		len(fresh.DirectReplyWaits.Waits) != 1 || fresh.DirectReplyWaits.Waits[0].WaitID != wait.WaitUUID ||
		fresh.DirectReplyWaits.Waits[0].Owner != handoff.ToPrincipal {
		t.Fatalf("new wait owner was not reflected in the confirmation: %+v", conflict)
	}
}

func TestSwarmActionHTTPHiddenCancellationTargetsMatchUnknownIDs(t *testing.T) {
	ctx := context.Background()
	f := newSwarmStoreFixture(t, swarmNoPanes)
	swarmActionEnableWaits(t, f)
	later, laterView := f.session("action-later-root")
	outsider, outsiderView := f.session("action-later-outsider")
	event := swarmActionPublishEvent(t, f, outsiderView, "request", later.Inbox, "", "")
	lease, err := laterView.NextEvent(ctx, core.NextEventRequest{Repo: f.repo}, f.dest)
	if err != nil || lease.Delivery == nil || lease.Delivery.Event.EventID != event.EventID {
		t.Fatalf("pre-campaign lease: %+v %v", lease, err)
	}
	id := lease.Delivery.DeliveryID
	if _, err := laterView.CompleteEvent(ctx, core.CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: id, LeaseID: lease.Delivery.LeaseID, Disposition: "handled",
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	dependency := swarmActionPublishEvent(t, f, laterView, "request", outsider.Inbox, "", "")
	wait, err := laterView.CreateWait(ctx, core.CreateWaitRequest{
		RequestID: uuid.NewString(), Collection: f.repo, DependencyEventUUID: dependency.EventID,
		PredicateKind: core.WaitPredicateRequestResponse, Deadline: time.Now().Add(time.Hour),
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.op.CancelWait(ctx, core.CancelWaitRequest{
		RequestID: uuid.NewString(), Collection: f.repo, WaitUUID: wait.WaitUUID,
	}, f.dest); err != nil {
		t.Fatal(err)
	}

	const campaign = "action-created-later"
	if _, err := laterView.SetTeamCampaign(ctx, core.SetTeamCampaignRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Campaign: campaign, RootAgent: later.Inbox, Sensitivity: "shareable",
	}, f.dest); err != nil {
		t.Fatal(err)
	}
	f.campaign = campaign
	assertHidden := func(method, path, unknownPath string, existing, unknown any) {
		t.Helper()
		headers := map[string]string{"X-CSRF-Token": f.csrf}
		resp, raw := f.do(method, path, existing, headers)
		other, otherRaw := f.do(method, unknownPath, unknown, headers)
		if resp.StatusCode != http.StatusNotFound || other.StatusCode != resp.StatusCode ||
			swarmErrorCode(raw) != "NOT_FOUND" || string(raw) != string(otherRaw) {
			t.Fatalf("%s hidden %d %s differs from unknown %d %s", path, resp.StatusCode, raw, other.StatusCode, otherRaw)
		}
	}
	unknownID := uuid.NewString()
	preview := "/api/swarm/action/cancel-preview?campaign=" + campaign + "&delivery_id="
	assertHidden(http.MethodGet, preview+id, preview+unknownID, nil, nil)

	cancelPath := "/api/swarm/action/cancel-delivery"
	req := core.SwarmCancelDeliveryRequest{SwarmDeliveryActionRequest: swarmActionDeliveryRequest(f, id)}
	unknownDelivery := req
	unknownDelivery.DeliveryID = unknownID
	assertHidden(http.MethodPost, cancelPath, cancelPath, req, unknownDelivery)
	req.Reason, unknownDelivery.Reason = "Cancel the earlier work", "Cancel the earlier work"
	assertHidden(http.MethodPost, cancelPath, cancelPath, req, unknownDelivery)

	waitPath := "/api/swarm/action/cancel-wait"
	cancelWait := core.SwarmCancelWaitRequest{SwarmActionRequest: swarmActionRequest(f), WaitID: wait.WaitUUID}
	unknownWait := cancelWait
	unknownWait.WaitID = uuid.NewString()
	assertHidden(http.MethodPost, waitPath, waitPath, cancelWait, unknownWait)
}
