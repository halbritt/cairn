package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
)

func TestWorkServerHostOriginValidation(t *testing.T) {
	srv, err := newWorkServer(nil, "default", "", 8085)
	if err != nil {
		t.Fatalf("failed to create work server: %v", err)
	}

	tests := []struct {
		name       string
		host       string
		origin     string
		wantStatus int
	}{
		{
			name:       "valid 127.0.0.1 with exact port",
			host:       "127.0.0.1:8085",
			origin:     "",
			wantStatus: http.StatusOK,
		},
		{
			name:       "valid localhost with exact port",
			host:       "localhost:8085",
			origin:     "",
			wantStatus: http.StatusOK,
		},
		{
			name:       "mismatched host port",
			host:       "127.0.0.1:9999",
			origin:     "",
			wantStatus: http.StatusForbidden,
		},
		{
			name:       "host prefix spoofing attempt",
			host:       "127.0.0.1.attacker.com:8085",
			origin:     "",
			wantStatus: http.StatusForbidden,
		},
		{
			name:       "arbitrary external host",
			host:       "evil.com:8085",
			origin:     "",
			wantStatus: http.StatusForbidden,
		},
		{
			name:       "valid host and valid 127.0.0.1 origin",
			host:       "127.0.0.1:8085",
			origin:     "http://127.0.0.1:8085",
			wantStatus: http.StatusOK,
		},
		{
			name:       "valid host and valid localhost origin",
			host:       "localhost:8085",
			origin:     "http://localhost:8085",
			wantStatus: http.StatusOK,
		},
		{
			name:       "valid host but origin with wrong port",
			host:       "127.0.0.1:8085",
			origin:     "http://127.0.0.1:9000",
			wantStatus: http.StatusForbidden,
		},
		{
			name:       "valid host but non-http origin scheme",
			host:       "127.0.0.1:8085",
			origin:     "https://127.0.0.1:8085",
			wantStatus: http.StatusForbidden,
		},
		{
			name:       "valid host but origin prefix spoof",
			host:       "127.0.0.1:8085",
			origin:     "http://127.0.0.1.attacker.com:8085",
			wantStatus: http.StatusForbidden,
		},
		{
			name:       "valid host but external origin",
			host:       "127.0.0.1:8085",
			origin:     "http://evil.com:8085",
			wantStatus: http.StatusForbidden,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "http://"+tc.host+"/", nil)
			req.Host = tc.host
			if tc.origin != "" {
				req.Header.Set("Origin", tc.origin)
			}
			rec := httptest.NewRecorder()
			srv.ServeHTTP(rec, req)
			if rec.Code != tc.wantStatus {
				t.Fatalf("expected status %d, got %d (body: %s)", tc.wantStatus, rec.Code, rec.Body.String())
			}
		})
	}
}

func TestWorkServerStaticAssetServing(t *testing.T) {
	srv, err := newWorkServer(nil, "default", "", 8085)
	if err != nil {
		t.Fatalf("failed to create work server: %v", err)
	}

	assets := []struct {
		path        string
		contentType string
	}{
		{"/", "text/html"},
		{"/styles.css", "text/css"},
		{"/app.js", "application/javascript"},
	}

	for _, a := range assets {
		t.Run(a.path, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8085"+a.path, nil)
			req.Host = "127.0.0.1:8085"
			rec := httptest.NewRecorder()
			srv.ServeHTTP(rec, req)
			if rec.Code != http.StatusOK {
				t.Fatalf("path %s returned %d", a.path, rec.Code)
			}
			ct := rec.Header().Get("Content-Type")
			if !strings.Contains(ct, a.contentType) {
				t.Fatalf("path %s expected content type containing %q, got %q", a.path, a.contentType, ct)
			}
		})
	}
}

func TestWorkServerLaunchCodeAuthAndReplay(t *testing.T) {
	srv, err := newWorkServer(nil, "default", "", 8085)
	if err != nil {
		t.Fatalf("failed to create work server: %v", err)
	}

	// 1. Unauthenticated API request returns 401
	unauthReq := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8085/api/work/projects", nil)
	unauthReq.Host = "127.0.0.1:8085"
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, unauthReq)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 for unauthenticated request, got %d", rec.Code)
	}

	// 2. Query param ?token= or ?code= does NOT authenticate
	tokenParamReq := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8085/api/work/projects?token="+srv.launchCode, nil)
	tokenParamReq.Host = "127.0.0.1:8085"
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, tokenParamReq)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 when token passed as query param, got %d", rec.Code)
	}

	// 3. Login with invalid code returns 403
	badLoginPayload := []byte(`{"code":"wrong-code"}`)
	badLoginReq := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8085/api/auth/login", bytes.NewReader(badLoginPayload))
	badLoginReq.Host = "127.0.0.1:8085"
	badLoginReq.Header.Set("Content-Type", "application/json")
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, badLoginReq)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for wrong code, got %d", rec.Code)
	}

	// 4. Login with correct code succeeds, sets HttpOnly SameSite=Strict cookie and returns CSRF token
	goodPayload, _ := json.Marshal(map[string]string{"code": srv.launchCode})
	loginReq := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8085/api/auth/login", bytes.NewReader(goodPayload))
	loginReq.Host = "127.0.0.1:8085"
	loginReq.Header.Set("Content-Type", "application/json")
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, loginReq)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 for valid launch code login, got %d (body: %s)", rec.Code, rec.Body.String())
	}

	cookies := rec.Result().Cookies()
	var sessCookie *http.Cookie
	for _, c := range cookies {
		if c.Name == "work_session" {
			sessCookie = c
			break
		}
	}
	if sessCookie == nil {
		t.Fatalf("expected work_session cookie to be set")
	}
	if !sessCookie.HttpOnly {
		t.Fatalf("expected work_session cookie to be HttpOnly")
	}
	if sessCookie.SameSite != http.SameSiteStrictMode {
		t.Fatalf("expected work_session cookie to be SameSite=Strict")
	}

	var loginResp struct {
		OK        bool   `json:"ok"`
		CSRFToken string `json:"csrf_token"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &loginResp); err != nil || !loginResp.OK || loginResp.CSRFToken == "" {
		t.Fatalf("invalid login response: %s", rec.Body.String())
	}

	// 5. Replay attempt with same launch code fails with 403 (single-use)
	replayReq := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8085/api/auth/login", bytes.NewReader(goodPayload))
	replayReq.Host = "127.0.0.1:8085"
	replayReq.Header.Set("Content-Type", "application/json")
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, replayReq)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected 403 on launch code replay, got %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "already used") {
		t.Fatalf("expected 'already used' error message on replay, got %s", rec.Body.String())
	}
}

func TestWorkServerLaunchCodeExpiration(t *testing.T) {
	srv, err := newWorkServer(nil, "default", "", 8085)
	if err != nil {
		t.Fatalf("failed to create work server: %v", err)
	}
	// Expire the code
	srv.launchExpiresAt = time.Now().Add(-1 * time.Minute)

	goodPayload, _ := json.Marshal(map[string]string{"code": srv.launchCode})
	loginReq := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8085/api/auth/login", bytes.NewReader(goodPayload))
	loginReq.Host = "127.0.0.1:8085"
	loginReq.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, loginReq)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for expired launch code, got %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "expired") {
		t.Fatalf("expected 'expired' error message, got %s", rec.Body.String())
	}
}

func TestWorkServerCSRFEnforcement(t *testing.T) {
	srv, err := newWorkServer(nil, "default", "", 8085)
	if err != nil {
		t.Fatalf("failed to create work server: %v", err)
	}

	// Issue a valid session
	sess, csrf := srv.issueSession()

	// 1. Mutating POST without CSRF token fails with 403
	postReq := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8085/api/work/refresh?project=demo", nil)
	postReq.Host = "127.0.0.1:8085"
	postReq.AddCookie(&http.Cookie{Name: "work_session", Value: sess})
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, postReq)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for missing CSRF token on POST, got %d", rec.Code)
	}

	// 2. Mutating POST with invalid CSRF token fails with 403
	postReqBadCSRF := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8085/api/work/refresh?project=demo", nil)
	postReqBadCSRF.Host = "127.0.0.1:8085"
	postReqBadCSRF.AddCookie(&http.Cookie{Name: "work_session", Value: sess})
	postReqBadCSRF.Header.Set("X-CSRF-Token", "invalid-token")
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, postReqBadCSRF)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for invalid CSRF token on POST, got %d", rec.Code)
	}

	// 3. Mutating POST with valid CSRF token passes CSRF check (route reaches handler, not 403)
	postReqGoodCSRF := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8085/api/work/refresh?project=demo", nil)
	postReqGoodCSRF.Host = "127.0.0.1:8085"
	postReqGoodCSRF.AddCookie(&http.Cookie{Name: "work_session", Value: sess})
	postReqGoodCSRF.Header.Set("X-CSRF-Token", csrf)
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, postReqGoodCSRF)
	// Because /api/work/refresh is not implemented in handleAPI (or 404), it MUST NOT be 403
	if rec.Code == http.StatusForbidden {
		t.Fatalf("did not expect 403 when valid CSRF token is provided, got %d", rec.Code)
	}
}

func TestWorkServerAuthSessionEndpoint(t *testing.T) {
	srv, err := newWorkServer(nil, "default", "", 8085)
	if err != nil {
		t.Fatalf("failed to create work server: %v", err)
	}

	// 1. Unauthenticated GET /api/auth/session returns 200 {"authenticated": false}
	unauthReq := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8085/api/auth/session", nil)
	unauthReq.Host = "127.0.0.1:8085"
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, unauthReq)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 for unauthenticated /api/auth/session, got %d", rec.Code)
	}
	var unauthResp struct {
		Authenticated bool   `json:"authenticated"`
		CSRFToken     string `json:"csrf_token"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &unauthResp); err != nil {
		t.Fatalf("failed to parse JSON: %v", err)
	}
	if unauthResp.Authenticated {
		t.Fatalf("expected authenticated=false, got true")
	}

	// 2. Unauthenticated GET /api/auth/status returns 200 {"authenticated": false}
	statusReq := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8085/api/auth/status", nil)
	statusReq.Host = "127.0.0.1:8085"
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, statusReq)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 for unauthenticated /api/auth/status, got %d", rec.Code)
	}
	var statusResp struct {
		Authenticated bool `json:"authenticated"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &statusResp); err != nil || statusResp.Authenticated {
		t.Fatalf("expected authenticated=false for status endpoint")
	}

	// 3. GET /api/auth/session with non-loopback host fails with 403
	forbiddenReq := httptest.NewRequest(http.MethodGet, "http://evil.com:8085/api/auth/session", nil)
	forbiddenReq.Host = "evil.com:8085"
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, forbiddenReq)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected 403 on non-loopback host for /api/auth/session, got %d", rec.Code)
	}

	// 4. Authenticated GET /api/auth/session returns 200 {"authenticated": true, "csrf_token": ...} and X-CSRF-Token header
	sess, csrf := srv.issueSession()
	authReq := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8085/api/auth/session", nil)
	authReq.Host = "127.0.0.1:8085"
	authReq.AddCookie(&http.Cookie{Name: "work_session", Value: sess})
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, authReq)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 for authenticated /api/auth/session, got %d", rec.Code)
	}
	if gotHeader := rec.Header().Get("X-CSRF-Token"); gotHeader != csrf {
		t.Fatalf("expected X-CSRF-Token header %q, got %q", csrf, gotHeader)
	}
	var authResp struct {
		Authenticated bool   `json:"authenticated"`
		CSRFToken     string `json:"csrf_token"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &authResp); err != nil {
		t.Fatalf("failed to parse JSON: %v", err)
	}
	if !authResp.Authenticated || authResp.CSRFToken != csrf {
		t.Fatalf("expected authenticated=true and csrf_token=%q, got %+v", csrf, authResp)
	}

	// 5. Authenticated GET /api/auth/status returns 200 {"authenticated": true, "csrf_token": ...} and X-CSRF-Token header
	statusAuthReq := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8085/api/auth/status", nil)
	statusAuthReq.Host = "127.0.0.1:8085"
	statusAuthReq.AddCookie(&http.Cookie{Name: "work_session", Value: sess})
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, statusAuthReq)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 for authenticated /api/auth/status, got %d", rec.Code)
	}
	if gotHeader := rec.Header().Get("X-CSRF-Token"); gotHeader != csrf {
		t.Fatalf("expected X-CSRF-Token header %q, got %q", csrf, gotHeader)
	}

	// 6. Expired cookie returns 200 {"authenticated": false}
	srv.sessionMu.Lock()
	srv.sessionTokens[sess] = time.Now().Add(-1 * time.Hour)
	srv.sessionMu.Unlock()

	expReq := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8085/api/auth/session", nil)
	expReq.Host = "127.0.0.1:8085"
	expReq.AddCookie(&http.Cookie{Name: "work_session", Value: sess})
	rec = httptest.NewRecorder()
	srv.ServeHTTP(rec, expReq)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 for expired session /api/auth/session, got %d", rec.Code)
	}
	var expResp struct {
		Authenticated bool `json:"authenticated"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &expResp); err != nil || expResp.Authenticated {
		t.Fatalf("expected authenticated=false for expired session")
	}
}

func TestWorkServerGuardedMutationsNegativeValidation(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("skipping test requiring database; run make test-integration")
	}
	ctx := context.Background()
	store, err := core.Open(ctx, dsn, core.Channel{Principal: "operator", Operator: true, Repo: "test-repo"})
	if err != nil {
		t.Fatalf("failed to open store: %v", err)
	}
	defer store.Close()

	srv, err := newWorkServer(store, "default", "test-repo", 8085)
	if err != nil {
		t.Fatalf("failed to create work server: %v", err)
	}
	sess, csrf := srv.issueSession()

	endpoints := []string{
		"/api/work/progress/edit",
		"/api/work/blocker/resolve",
		"/api/work/unassociate",
	}

	for _, ep := range endpoints {
		// Test 1: missing expected_revision
		t.Run(ep+"_missing_revision", func(t *testing.T) {
			payload := map[string]any{
				"project":        "default",
				"item":           "item-123",
				"annotation_id":  "00000000-0000-0000-0000-000000000001",
				"association_id": "00000000-0000-0000-0000-000000000001",
				"text":           "update",
			}
			body, _ := json.Marshal(payload)
			req := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8085"+ep, bytes.NewReader(body))
			req.Host = "127.0.0.1:8085"
			req.AddCookie(&http.Cookie{Name: "work_session", Value: sess})
			req.Header.Set("X-CSRF-Token", csrf)
			rec := httptest.NewRecorder()
			srv.ServeHTTP(rec, req)
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("expected 400 for missing expected_revision, got %d: %s", rec.Code, rec.Body.String())
			}
		})

		// Test 2: non-positive expected_revision (0)
		t.Run(ep+"_zero_revision", func(t *testing.T) {
			rev := int64(0)
			payload := map[string]any{
				"project":           "default",
				"item":              "item-123",
				"annotation_id":     "00000000-0000-0000-0000-000000000001",
				"association_id":    "00000000-0000-0000-0000-000000000001",
				"expected_revision": rev,
				"text":              "update",
			}
			body, _ := json.Marshal(payload)
			req := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8085"+ep, bytes.NewReader(body))
			req.Host = "127.0.0.1:8085"
			req.AddCookie(&http.Cookie{Name: "work_session", Value: sess})
			req.Header.Set("X-CSRF-Token", csrf)
			rec := httptest.NewRecorder()
			srv.ServeHTTP(rec, req)
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("expected 400 for expected_revision=0, got %d: %s", rec.Code, rec.Body.String())
			}
		})

		// Test 3: missing item
		t.Run(ep+"_missing_item", func(t *testing.T) {
			rev := int64(1)
			payload := map[string]any{
				"project":           "default",
				"annotation_id":     "00000000-0000-0000-0000-000000000001",
				"association_id":    "00000000-0000-0000-0000-000000000001",
				"expected_revision": rev,
				"text":              "update",
			}
			body, _ := json.Marshal(payload)
			req := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8085"+ep, bytes.NewReader(body))
			req.Host = "127.0.0.1:8085"
			req.AddCookie(&http.Cookie{Name: "work_session", Value: sess})
			req.Header.Set("X-CSRF-Token", csrf)
			rec := httptest.NewRecorder()
			srv.ServeHTTP(rec, req)
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("expected 400 for missing item, got %d: %s", rec.Code, rec.Body.String())
			}
		})
	}
}
