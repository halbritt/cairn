package localapi

import (
	"context"
	"crypto/sha256"
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

func TestRefusalStatsCountsByOperationAndCode(t *testing.T) {
	localTok := "local-refusal-token"
	hostedTok := "hosted-refusal-token"
	srv := &Server{
		clients: map[[32]byte]client{
			sha256.Sum256([]byte(localTok)):  {principal: "local-op", role: "agent", destination: core.Destination{Name: "local", AllowLocal: true}},
			sha256.Sum256([]byte(hostedTok)): {principal: "hosted-ag", role: "agent", destination: core.Destination{Name: "hosted", AllowLocal: false}},
		},
	}
	callHTTP := func(tok, path, body string, headers ...string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", path, strings.NewReader(body))
		if tok != "" {
			req.Header.Set("Authorization", "Bearer "+tok)
		}
		for i := 0; i+1 < len(headers); i += 2 {
			req.Header.Set(headers[i], headers[i+1])
		}
		rec := httptest.NewRecorder()
		srv.ServeHTTP(rec, req)
		return rec
	}
	// Trigger two decode refusals on index, one local-destination refusal on preview-retract, and one unsupported protocol refusal for hostedTok.
	if rec := callHTTP(hostedTok, "/v1/index", `{"unknown_field":1}`); rec.Code != 400 {
		t.Fatalf("expected 400, got %d", rec.Code)
	}
	if rec := callHTTP(hostedTok, "/v1/index", `{"unknown_field":2}`); rec.Code != 400 {
		t.Fatalf("expected 400, got %d", rec.Code)
	}
	if rec := callHTTP(hostedTok, "/v1/preview-retract", `{}`); rec.Code != 403 {
		t.Fatalf("expected 403 on hosted preview-retract, got %d", rec.Code)
	}
	if rec := callHTTP(hostedTok, "/v1/index", `{}`, ProtocolHeader, "9999"); rec.Code != 426 {
		t.Fatalf("expected 426 on unsupported protocol, got %d", rec.Code)
	}
	// Unauthenticated and unknown routes must not pollute counters.
	_ = callHTTP("", "/v1/index", `{}`)
	_ = callHTTP(hostedTok, "/v1/unknown-nonexistent-op", `{}`)

	rec := callHTTP(hostedTok, "/v1/refusal-stats", `{}`)
	if rec.Code != 200 {
		t.Fatalf("hosted principal refusal-stats failed: %d %s", rec.Code, rec.Body.String())
	}
	var env struct {
		OK   bool                 `json:"ok"`
		Data RefusalStatsResponse `json:"data"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil || !env.OK {
		t.Fatalf("unmarshal refusal-stats: %v %+v", err, env)
	}
	if env.Data.Schema != RefusalStatsSchema || env.Data.Principal != "hosted-ag" || env.Data.TotalRefusals != 4 || env.Data.MatchedRefusals != 4 || len(env.Data.Rows) != 3 {
		t.Fatalf("unexpected hosted refusal-stats: %+v", env.Data)
	}
	if env.Data.Rows[0].Operation != "index" || env.Data.Rows[0].Code != "INVALID_REQUEST" || env.Data.Rows[0].Count != 2 {
		t.Fatalf("expected sorted top refusal row index/INVALID_REQUEST=2, got %+v", env.Data.Rows[0])
	}
	// Cross-principal isolation: localTok must see 0 refusals of its own, never hostedTok's refusals.
	recLocal := callHTTP(localTok, "/v1/refusal-stats", `{}`)
	var envLocal struct {
		OK   bool                 `json:"ok"`
		Data RefusalStatsResponse `json:"data"`
	}
	if err := json.Unmarshal(recLocal.Body.Bytes(), &envLocal); err != nil || !envLocal.OK || envLocal.Data.TotalRefusals != 0 || len(envLocal.Data.Rows) != 0 {
		t.Fatalf("cross-principal leak in refusal-stats: %+v %v", envLocal, err)
	}
	filtered, err := srv.RefusalStats("hosted-ag", "", RefusalStatsRequest{Operation: "index", Code: "PROTOCOL_UNSUPPORTED"})
	if err != nil || filtered.TotalRefusals != 4 || filtered.MatchedRefusals != 1 || len(filtered.Rows) != 1 || filtered.Rows[0].Count != 1 {
		t.Fatalf("filtered RefusalStats failed: %+v %v", filtered, err)
	}
}
