package localapi

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestHTTPRefusalClassification(t *testing.T) {
	cases := []struct {
		codes  []string
		status int
		scrub  bool
	}{
		{[]string{"AUTHORITY_DENIED", "SELF_PROMOTION_DENIED"}, 403, false},
		{[]string{"RESTORE_PAUSED"}, 503, false},
		{[]string{"INVALID_REQUEST"}, 400, false},
		{[]string{"NOT_FOUND"}, 404, false},
		{[]string{"PROTOCOL_UNSUPPORTED"}, 426, false},
		{[]string{"STALE_WORKER", "WORKER_UNAVAILABLE", "POOL_FULL", "POOL_PAUSED", "STALE_RESOLUTION", "STALE_SESSION", "AGENT_BUSY", "DELIVERY_ACTIVE", "STALE_LEASE", "PAYLOAD_UNAVAILABLE", "FORGET_REQUIRED", "STALE_HANDLE", "VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", "STALE_PACKAGE", "RUN_ALREADY_STARTED", "ATTEMPT_TERMINAL"}, 409, false},
		{[]string{"STORE_ERROR", "REFUSAL_UNRECORDED"}, 500, true},
		{[]string{"UNRECOGNIZED_CODE", "STALE_CURSOR", "BUDGET_REFUSED"}, 422, false},
	}
	for _, c := range cases {
		for _, code := range c.codes {
			t.Run(code, func(t *testing.T) {
				request := httptest.NewRequest("POST", "/v1/create", strings.NewReader("{}"))
				recorder := httptest.NewRecorder()
				serveJSON(recorder, request, func(context.Context, struct{}) (any, error) {
					return nil, &core.Error{Code: code, Message: "fixture diagnostic", RefusalID: "retained-refusal"}
				})
				var body response
				if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
					t.Fatal(err)
				}
				wantMessage := code + ": fixture diagnostic"
				if c.scrub {
					wantMessage = "store operation failed"
				}
				if recorder.Code != c.status || body.Status != code || body.Message != wantMessage || body.RefusalID != "retained-refusal" || body.OK {
					t.Fatalf("HTTP %d body=%+v; want %d message=%q", recorder.Code, body, c.status, wantMessage)
				}
			})
		}
	}
}
