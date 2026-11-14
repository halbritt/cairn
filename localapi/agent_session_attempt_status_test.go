package localapi_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

func TestSessionInboxPollStatusTypedClient(t *testing.T) {
	ctx := context.Background()
	client, _, _, _ := authenticatedHost(t, "agent", "hosted", nil)
	var agent core.AgentInstance
	if err := client.Call(ctx, "agent-register", core.RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "empty-poll-read", NativeSessionID: "empty-poll-read", Metadata: core.AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, &agent); err != nil {
		t.Fatal(err)
	}
	ref := core.AgentSessionRef{AgentID: agent.AgentID, ExecutionID: agent.ExecutionID}
	id := uuid.NewString()
	var claim core.SessionInboxResult
	if err := client.Call(ctx, "session-inbox-claim", core.SessionInboxClaim{RequestID: id, Session: ref}, &claim); err != nil || claim.Attempt != nil {
		t.Fatalf("empty poll: %+v %v", claim, err)
	}
	got, err := client.SessionInboxPollStatus(ctx, ref, id)
	if err != nil || got.RequestID != id || got.Session != ref || got.AttemptID != nil {
		t.Fatalf("poll status lost empty identity: %+v %v", got, err)
	}
	raw, err := json.Marshal(got)
	if err != nil || !bytes.Contains(raw, []byte(`"attempt_id":null`)) {
		t.Fatalf("empty poll not explicit null: %s %v", raw, err)
	}
}

func TestSessionInboxAttemptStatusTypedClient(t *testing.T) {
	ctx := context.Background()
	requests := make(chan []byte, 4)
	client, admin, repo, _ := authenticatedHost(t, "agent", "hosted", func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/v1/session-inbox-attempt-status" {
				body, err := io.ReadAll(r.Body)
				if err != nil {
					t.Error(err)
					return
				}
				requests <- body
				r.Body = io.NopCloser(bytes.NewReader(body))
			}
			next.ServeHTTP(w, r)
		})
	})
	registration := core.RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "attempt-status", NativeSessionID: "attempt-status",
		Metadata: core.AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}
	var a core.AgentInstance
	if err := client.Call(ctx, "agent-register", registration, &a); err != nil {
		t.Fatal(err)
	}
	ref := core.AgentSessionRef{AgentID: a.AgentID, ExecutionID: a.ExecutionID}
	source, err := admin.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "status source", ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = admin.PublishEvent(ctx, core.PublishEventRequest{RequestID: uuid.NewString(), Repo: repo, Kind: "request", Ref: core.RecordVersionRef{RecordID: source.RecordID, Version: source.Version}, Destination: core.EventDestination{Type: "agent", Name: a.Inbox}}, hostedDestination); err != nil {
		t.Fatal(err)
	}
	var claim core.SessionInboxResult
	if err = client.Call(ctx, "session-inbox-claim", core.SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, &claim); err != nil || claim.Attempt == nil {
		t.Fatalf("claim: %+v %v", claim, err)
	}
	want := *claim.Attempt
	want.Delivery.LeaseID, want.Delivery.LeaseUntil = "", nil
	got, err := client.SessionInboxAttemptStatus(ctx, ref, want.ID)
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatalf("typed read: got %+v, want %+v: %v", got, want, err)
	}
	var encoded map[string]json.RawMessage
	if err = json.Unmarshal(<-requests, &encoded); err != nil || len(encoded) != 2 || encoded["session"] == nil || encoded["attempt_id"] == nil {
		t.Fatalf("request shape: %s %v", encoded, err)
	}
	var query core.SessionInboxAttemptQuery
	body, err := json.Marshal(encoded)
	if err != nil || json.Unmarshal(body, &query) != nil || query.Session != ref || query.AttemptID != want.ID {
		t.Fatalf("typed request lost identity: %+v %v", query, err)
	}
	var completed core.AgentDelivery
	if err = client.ForAgentSession(ref).Call(ctx, "event-complete", core.CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: want.Delivery.DeliveryID, LeaseID: claim.Attempt.Delivery.LeaseID, Disposition: "handled"}, &completed); err != nil {
		t.Fatal(err)
	}
	if err = client.Call(ctx, "session-inbox-reconcile", core.SessionInboxReconcile{RequestID: uuid.NewString(), Session: ref, AttemptID: want.ID, Reason: "delivery_completed"}, &want); err != nil || want.FinishedAt == nil {
		t.Fatalf("finished historical fixture: %+v %v", want, err)
	}
	want.Delivery.LeaseID, want.Delivery.LeaseUntil = "", nil
	registration.RequestID = uuid.NewString()
	var resumed core.AgentInstance
	if err = client.Call(ctx, "agent-register", registration, &resumed); err != nil {
		t.Fatal(err)
	}
	current := core.AgentSessionRef{AgentID: resumed.AgentID, ExecutionID: resumed.ExecutionID}
	got, err = client.SessionInboxAttemptStatus(ctx, current, want.ID)
	if err != nil || !reflect.DeepEqual(got, want) || got.Session == current {
		t.Fatalf("typed historical execution: %+v %v", got, err)
	}
	_, err = client.SessionInboxAttemptStatus(ctx, ref, want.ID)
	if core.Code(err) != "STALE_SESSION" {
		t.Fatalf("typed stale refusal: %v", err)
	}
	_, err = client.SessionInboxAttemptStatus(ctx, current, uuid.NewString())
	if core.Code(err) != "NOT_FOUND" {
		t.Fatalf("typed missing attempt is unknown: %v", err)
	}
}

func TestSessionInboxAttemptStatusHTTPAuthentication(t *testing.T) {
	ctx := context.Background()
	h := newHandoffHost(t, "status-owner", "status-foreign")
	owner := h.stores["status-owner"]
	a, err := owner.RegisterAgent(ctx, core.RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "status", NativeSessionID: "status",
		Metadata: core.AgentMetadata{Harness: "claude", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, hostedDestination)
	if err != nil {
		t.Fatal(err)
	}
	source := h.note(t, "status-owner", "status authentication canary")
	h.request(t, "status-owner", source, core.EventDestination{Type: "agent", Name: a.Inbox}, false)
	ref := core.AgentSessionRef{AgentID: a.AgentID, ExecutionID: a.ExecutionID}
	claim, err := owner.ClaimSessionInbox(ctx, core.SessionInboxClaim{RequestID: uuid.NewString(), Session: ref}, hostedDestination)
	if err != nil || claim.Attempt == nil {
		t.Fatalf("claim: %+v %v", claim, err)
	}
	body, err := json.Marshal(core.SessionInboxAttemptQuery{Session: ref, AttemptID: claim.Attempt.ID})
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		principal string
		http      int
		status    string
	}{
		{"status-owner", http.StatusOK, "OK"},
		{"", http.StatusUnauthorized, "AUTHORITY_DENIED"},
		{"status-foreign", http.StatusNotFound, "NOT_FOUND"},
	} {
		t.Run(tc.principal, func(t *testing.T) {
			r := httptest.NewRequest(http.MethodPost, "/v1/session-inbox-attempt-status", bytes.NewReader(body))
			if tc.principal != "" {
				r.Header.Set("Authorization", "Bearer "+h.tokens[tc.principal])
			}
			w := httptest.NewRecorder()
			h.server.ServeHTTP(w, r)
			var envelope statusEnvelope
			if err := json.Unmarshal(w.Body.Bytes(), &envelope); err != nil || w.Code != tc.http || envelope.Status != tc.status || envelope.OK != (tc.http == http.StatusOK) {
				t.Fatalf("HTTP response: %d %s (%v)", w.Code, w.Body.String(), err)
			}
			if tc.http != http.StatusOK {
				if len(envelope.Data) != 0 || bytes.Contains(w.Body.Bytes(), []byte(claim.Attempt.ID)) || bytes.Contains(w.Body.Bytes(), []byte(claim.Attempt.Delivery.LeaseID)) {
					t.Fatalf("refusal disclosed attempt data: %s", w.Body.String())
				}
				return
			}
			var got core.SessionInboxAttempt
			if err := json.Unmarshal(envelope.Data, &got); err != nil || got.ID != claim.Attempt.ID || got.Session != ref || got.FinishedAt != nil || got.Delivery.LeaseID != "" || got.Delivery.LeaseUntil != nil {
				t.Fatalf("HTTP attempt: %+v %v", got, err)
			}
		})
	}
}

func TestSessionInboxAttemptStatusUnavailableAPIIsUnknown(t *testing.T) {
	home := t.TempDir()
	token := filepath.Join(home, "token")
	if err := os.WriteFile(token, []byte("synthetic-status-token"), 0600); err != nil {
		t.Fatal(err)
	}
	client, err := localapi.NewClient(filepath.Join(home, "absent.sock"), token)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	got, err := client.SessionInboxAttemptStatus(context.Background(), core.AgentSessionRef{AgentID: uuid.NewString(), ExecutionID: uuid.NewString()}, uuid.NewString())
	if core.Code(err) != "API_CONNECTION_FAILED" || !reflect.DeepEqual(got, core.SessionInboxAttempt{}) {
		t.Fatalf("transport failure became a terminal result: %+v %v", got, err)
	}
}
