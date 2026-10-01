package localapi_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

// handoffHost is a real API server over a disposable database with one hosted
// agent profile per principal, plus direct stores used only to arrange state.
type handoffHost struct {
	server *localapi.Server
	repo   string
	tokens map[string]string
	stores map[string]*core.Store
}

var hostedDestination = core.Destination{Name: "hosted"}

func newHandoffHost(t *testing.T, principals ...string) *handoffHost {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	h := &handoffHost{repo: uuid.NewString(), tokens: map[string]string{}, stores: map[string]*core.Store{}}
	var identities []localapi.Identity
	for i, principal := range principals {
		store := h.openStore(t, principal)
		if i == 0 {
			if err := store.Migrate(ctx); err != nil {
				t.Fatal(err)
			}
		}
		token := "handoff-status-" + uuid.NewString()
		digest := sha256.Sum256([]byte(token))
		h.tokens[principal] = token
		identities = append(identities, localapi.Identity{TokenSHA256: hex.EncodeToString(digest[:]), Principal: principal, Repo: h.repo, Role: "agent", Destination: "hosted"})
	}
	server, err := localapi.New(ctx, dsn, identities)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.Close)
	h.server = server
	return h
}

func (h *handoffHost) openStore(t *testing.T, principal string) *core.Store {
	t.Helper()
	store, err := core.Open(context.Background(), os.Getenv("CAIRN_TEST_DATABASE_URL"), core.Channel{Principal: principal, Repo: h.repo})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(store.Close)
	h.stores[principal] = store
	return store
}

func (h *handoffHost) note(t *testing.T, principal, body string) core.Record {
	t.Helper()
	record, err := h.stores[principal].Create(context.Background(), core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{
		Kind: "note", Body: body, ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: h.repo, TaskID: "*", RunID: "*"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	return record
}

func (h *handoffHost) request(t *testing.T, principal string, source core.Record, to core.EventDestination, group bool) core.AgentEvent {
	t.Helper()
	req := core.PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: core.RecordVersionRef{RecordID: source.RecordID, Version: source.Version}, Destination: to}
	if group {
		req.ResponseGroup = &core.ResponseGroupSpec{Deadline: time.Now().Add(time.Hour), PartialPolicy: "partial"}
	}
	event, err := h.stores[principal].PublishEvent(context.Background(), req, hostedDestination)
	if err != nil {
		t.Fatal(err)
	}
	return event
}

// post sends one raw request as principal ("" sends no credentials) and returns
// the HTTP status and the complete response bytes, newline included.
func (h *handoffHost) post(t *testing.T, principal, body string) (int, []byte) {
	t.Helper()
	request := httptest.NewRequest("POST", "/v1/handoff-request-status", strings.NewReader(body))
	if principal != "" {
		request.Header.Set("Authorization", "Bearer "+h.tokens[principal])
	}
	response := httptest.NewRecorder()
	h.server.ServeHTTP(response, request)
	return response.Code, response.Body.Bytes()
}

type statusEnvelope struct {
	OK      bool            `json:"ok"`
	Status  string          `json:"status"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data"`
}

func linksRequest(t *testing.T, handoff core.Record, links ...core.HandoffItemLink) string {
	t.Helper()
	body, err := json.Marshal(core.HandoffRequestStatusRequest{Handoff: core.RecordVersionRef{RecordID: handoff.RecordID, Version: handoff.Version}, Items: links})
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

func (h *handoffHost) status(t *testing.T, principal, body string) core.HandoffRequestStatus {
	t.Helper()
	code, raw := h.post(t, principal, body)
	var envelope statusEnvelope
	if err := json.Unmarshal(raw, &envelope); err != nil || code != 200 || !envelope.OK {
		t.Fatalf("status call: HTTP %d %s (%v)", code, raw, err)
	}
	var out core.HandoffRequestStatus
	if err := json.Unmarshal(envelope.Data, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func (h *handoffHost) refusal(t *testing.T, principal, body string, wantHTTP int, wantCode string) statusEnvelope {
	t.Helper()
	code, raw := h.post(t, principal, body)
	var envelope statusEnvelope
	if err := json.Unmarshal(raw, &envelope); err != nil || code != wantHTTP || envelope.OK || envelope.Status != wantCode || len(envelope.Data) != 0 {
		t.Fatalf("want HTTP %d %s: HTTP %d %s (%v)", wantHTTP, wantCode, code, raw, err)
	}
	return envelope
}

// Two handled-by-nobody requests, a third principal and a recipient: the API
// returns exactly the caller's authorized view and never a task conclusion.
func TestHandoffRequestStatusAPIKeepsEventAccessAndAttribution(t *testing.T) {
	h := newHandoffHost(t, "agent/publisher", "agent/worker", "agent/third")
	source := h.note(t, "agent/publisher", "source note")
	first := h.request(t, "agent/publisher", source, core.EventDestination{Type: "agent", Name: "agent/worker"}, true)
	second := h.request(t, "agent/publisher", source, core.EventDestination{Type: "agent", Name: "agent/worker"}, false)
	handoff := h.note(t, "agent/publisher", fmt.Sprintf("Handoff\nreview %s\ndeliver %s", first.EventID, second.EventID))
	absent := uuid.NewString()
	body := linksRequest(t, handoff, core.HandoffItemLink{ItemID: "review", EventID: first.EventID}, core.HandoffItemLink{ItemID: "deliver", EventID: second.EventID}, core.HandoffItemLink{ItemID: "absent", EventID: absent})

	publisher := h.status(t, "agent/publisher", body)
	if publisher.Schema != "cairn.handoff-request-status/1" || publisher.Links != "caller_asserted" || publisher.Handoff.RecordID != handoff.RecordID || publisher.Handoff.Version != 1 || len(publisher.Handoff.BodySHA256) != 64 {
		t.Fatalf("attribution: %+v", publisher)
	}
	review, deliver, missing := publisher.Items[0], publisher.Items[1], publisher.Items[2]
	if review.Availability != "available" || review.TaskOutcome != "unknown" || len(review.Deliveries) != 1 || review.Deliveries[0].State != "pending" ||
		review.ResponseGroup == nil || *review.ResponseGroup != (core.HandoffResponseGroup{State: "open", Expected: 1, Responded: 0}) {
		t.Fatalf("grouped request: %+v", review)
	}
	if deliver.Availability != "available" || deliver.ResponseGroup != nil || len(deliver.Deliveries) != 1 {
		t.Fatalf("ungrouped request: %+v", deliver)
	}
	if missing.Availability != "unavailable" || missing.HandoffRequestObservation != nil {
		t.Fatalf("absent link: %+v", missing)
	}

	// The third principal reads the shareable handoff but none of its events,
	// and nothing in the reply tells absent from private.
	code, raw := h.post(t, "agent/third", body)
	var envelope statusEnvelope
	var third struct {
		Items []json.RawMessage `json:"items"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil || code != 200 || json.Unmarshal(envelope.Data, &third) != nil || len(third.Items) != 3 {
		t.Fatalf("third principal: HTTP %d %s (%v)", code, raw, err)
	}
	for i, link := range []core.HandoffItemLink{{ItemID: "review", EventID: first.EventID}, {ItemID: "deliver", EventID: second.EventID}, {ItemID: "absent", EventID: absent}} {
		if want := fmt.Sprintf(`{"item_id":%q,"event_id":%q,"availability":"unavailable","task_outcome":"unknown"}`, link.ItemID, link.EventID); string(third.Items[i]) != want {
			t.Fatalf("third principal item %d: %s want %s", i, third.Items[i], want)
		}
	}
	for _, secret := range []string{review.Deliveries[0].DeliveryID, deliver.Deliveries[0].DeliveryID, "agent/worker", "agent/publisher", `"response_group"`} {
		if strings.Contains(string(raw), secret) {
			t.Fatalf("third principal response leaks %q: %s", secret, raw)
		}
	}

	// The recipient reads its own delivery and never the publisher-only group.
	worker := h.status(t, "agent/worker", body)
	if got := worker.Items[0]; got.Availability != "available" || got.ResponseGroup != nil || len(got.Deliveries) != 1 || got.Deliveries[0] != review.Deliveries[0] {
		t.Fatalf("recipient view: %+v", got)
	}
	if worker.Items[2].Availability != "unavailable" {
		t.Fatalf("recipient absent link: %+v", worker.Items[2])
	}
}

// Whole-envelope bound: 16 single-delivery items fit, a long delivery list is
// cut at its 16-row prefix with deliveries_more, and an oversize combined set
// is refused as a whole instead of dropping or changing any item.
func TestHandoffRequestStatusBoundsTheWholeEnvelope(t *testing.T) {
	ctx := context.Background()
	h := newHandoffHost(t, "agent/publisher")
	source := h.note(t, "agent/publisher", "source note")
	for i := 0; i < 17; i++ {
		store := h.openStore(t, fmt.Sprintf("agent/subscriber-%02d", i))
		if _, err := store.SetSubscription(ctx, core.SubscriptionRequest{RequestID: uuid.NewString(), Topic: "wide", Active: true}); err != nil {
			t.Fatal(err)
		}
	}
	var narrow, wide []core.HandoffItemLink
	for i := 0; i < 16; i++ {
		id := fmt.Sprintf("%s%02d", strings.Repeat("x", 62), i) // longest legal item ID
		narrow = append(narrow, core.HandoffItemLink{ItemID: id, EventID: h.request(t, "agent/publisher", source, core.EventDestination{Type: "agent", Name: "agent/lone-worker"}, true).EventID})
		wide = append(wide, core.HandoffItemLink{ItemID: id, EventID: h.request(t, "agent/publisher", source, core.EventDestination{Type: "topic", Name: "wide"}, true).EventID})
	}
	handoff := h.note(t, "agent/publisher", "Handoff with sixteen items")

	code, raw := h.post(t, "agent/publisher", linksRequest(t, handoff, narrow...))
	if code != 200 || len(raw) > core.HandoffRequestStatusEnvelopeLimit || raw[len(raw)-1] != '\n' {
		t.Fatalf("16 single-delivery items: HTTP %d, %d bytes", code, len(raw))
	}
	t.Logf("16 items with one delivery each: %d of %d bytes", len(raw), core.HandoffRequestStatusEnvelopeLimit)
	var envelope statusEnvelope
	var out core.HandoffRequestStatus
	if err := json.Unmarshal(raw, &envelope); err != nil || json.Unmarshal(envelope.Data, &out) != nil || len(out.Items) != 16 {
		t.Fatalf("narrow set: %s (%v)", raw, err)
	}
	for i, item := range out.Items {
		if item.ItemID != narrow[i].ItemID || item.Availability != "available" || len(item.Deliveries) != 1 || item.ResponseGroup == nil {
			t.Fatalf("item %d lost required status: %+v", i, item)
		}
	}

	single := h.status(t, "agent/publisher", linksRequest(t, handoff, wide[0]))
	if got := single.Items[0]; len(got.Deliveries) != 16 || !got.DeliveriesMore || got.ResponseGroup == nil || got.ResponseGroup.Expected != 17 {
		t.Fatalf("one request with 17 recipients: %d deliveries more=%v group=%+v", len(got.Deliveries), got.DeliveriesMore, got.ResponseGroup)
	}

	// A smaller set of the same wide requests still fits and keeps every item.
	if five := h.status(t, "agent/publisher", linksRequest(t, handoff, wide[:5]...)); len(five.Items) != 5 {
		t.Fatalf("five wide items: %+v", five)
	}
	// Sixteen wide items cannot fit: the whole operation refuses and returns
	// neither a prefix, a changed availability nor any event identity.
	refused := h.refusal(t, "agent/publisher", linksRequest(t, handoff, wide...), 422, "BUDGET_REFUSED")
	_, raw = h.post(t, "agent/publisher", linksRequest(t, handoff, wide...))
	if len(raw) > 1024 || strings.Contains(string(raw), wide[0].EventID) || strings.Contains(refused.Message, "agent/") {
		t.Fatalf("budget refusal discloses or is unbounded: %s", raw)
	}
}

// Refusals use coded errors and no request field can supply identity or scope.
func TestHandoffRequestStatusRefusals(t *testing.T) {
	h := newHandoffHost(t, "agent/publisher")
	source := h.note(t, "agent/publisher", "source note")
	event := h.request(t, "agent/publisher", source, core.EventDestination{Type: "agent", Name: "agent/worker"}, false)
	handoff := h.note(t, "agent/publisher", "Handoff version one")
	link := core.HandoffItemLink{ItemID: "review", EventID: event.EventID}
	good := linksRequest(t, handoff, link)
	if _, err := h.stores["agent/publisher"].Revise(context.Background(), core.ReviseRequest{RequestID: uuid.NewString(), RecordID: handoff.RecordID, ExpectedVersion: 1, Repo: h.repo, Body: "Handoff version two"}); err != nil {
		t.Fatal(err)
	}
	h.refusal(t, "agent/publisher", good, 409, "VERSION_CONFLICT")
	absent := core.Record{RecordID: uuid.NewString(), Version: 1}
	h.refusal(t, "agent/publisher", linksRequest(t, absent, link), 404, "NOT_FOUND")
	h.refusal(t, "", good, 401, "AUTHORITY_DENIED")

	current := core.Record{RecordID: handoff.RecordID, Version: 2}
	other := core.HandoffItemLink{ItemID: "other", EventID: uuid.NewString()}
	for name, body := range map[string]string{
		"duplicate item":  linksRequest(t, current, link, core.HandoffItemLink{ItemID: "review", EventID: other.EventID}),
		"duplicate event": linksRequest(t, current, link, core.HandoffItemLink{ItemID: "other", EventID: link.EventID}),
		"no items":        linksRequest(t, current),
		"bad item id":     linksRequest(t, current, core.HandoffItemLink{ItemID: "no spaces", EventID: other.EventID}),
		"zero version":    linksRequest(t, core.Record{RecordID: handoff.RecordID}, link),
		"null body":       `null`,
		"empty body":      `{}`,
	} {
		if envelope := h.refusal(t, "agent/publisher", body, 400, "INVALID_REQUEST"); envelope.Message == "invalid bounded JSON request" {
			t.Errorf("%s was refused as undecodable instead of by validation", name)
		}
	}
	// Caller-supplied identity, collection, destination or lease are not fields.
	valid := linksRequest(t, current, link)
	for _, extra := range []string{`"principal":"agent/publisher"`, `"repo":"` + h.repo + `"`, `"destination":"local"`, `"agent":"agent/publisher"`, `"request_id":"` + uuid.NewString() + `"`} {
		h.refusal(t, "agent/publisher", strings.Replace(valid, "{", "{"+extra+",", 1), 400, "INVALID_REQUEST")
	}
	nested := strings.Replace(valid, `"item_id"`, `"consumer":"agent/worker","item_id"`, 1)
	h.refusal(t, "agent/publisher", nested, 400, "INVALID_REQUEST")
	if ok := h.status(t, "agent/publisher", valid); ok.Handoff.Version != 2 || ok.Items[0].TaskOutcome != "unknown" {
		t.Fatalf("current version refused: %+v", ok)
	}
}
