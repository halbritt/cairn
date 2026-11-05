package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
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
