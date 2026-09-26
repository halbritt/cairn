package localapi

import (
	"crypto/tls"
	"crypto/x509"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func trustedRelay(t *testing.T, upstream *httptest.Server) *Relay {
	t.Helper()
	relay, err := NewRelay(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	roots := x509.NewCertPool()
	roots.AddCert(upstream.Certificate())
	relay.transport.TLSClientConfig = &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}
	t.Cleanup(relay.Close)
	return relay
}

func TestRelayPreservesOnlyProtocolHeadersAndBody(t *testing.T) {
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if r.URL.Path != "/v1/event-complete" || string(body) != "{\"request_id\":\"unchanged\"}" || r.Header.Get("Authorization") != "Bearer synthetic" || r.Header.Get("Cairn-Agent-ID") != "agent" || r.Header.Get("Cairn-Execution-ID") != "execution" {
			t.Errorf("protocol changed: %s %s %v", r.URL, body, r.Header)
		}
		if r.Header.Get("X-Forwarded-For") != "" || r.Header.Get("Idempotency-Key") != "" || r.Header.Get("Cookie") != "" {
			t.Error("untrusted headers forwarded")
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(409)
		_, _ = io.WriteString(w, `{"schema":"cairn.response/1","status":"STALE_LEASE"}`)
	}))
	defer upstream.Close()
	relay := trustedRelay(t, upstream)
	req := httptest.NewRequest("POST", "http://untrusted/v1/event-complete", strings.NewReader(`{"request_id":"unchanged"}`))
	req.Header.Set("Authorization", "Bearer synthetic")
	req.Header.Set("Cairn-Agent-ID", "agent")
	req.Header.Set("Cairn-Execution-ID", "execution")
	req.Header.Set("Idempotency-Key", "spoof")
	req.Header.Set("Cookie", "secret")
	req.Header.Set("X-Forwarded-For", "spoof")
	w := httptest.NewRecorder()
	relay.ServeHTTP(w, req)
	if w.Code != 409 || !strings.Contains(w.Body.String(), "STALE_LEASE") {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
}

func TestRelayLostReplyIsExplicitAndNotRetried(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = io.Copy(io.Discard, r.Body)
		conn, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		_ = conn.Close()
	}))
	defer upstream.Close()
	relay := trustedRelay(t, upstream)
	w := httptest.NewRecorder()
	relay.ServeHTTP(w, httptest.NewRequest("POST", "/v1/create", strings.NewReader(`{}`)))
	if w.Code != 504 || calls.Load() != 1 || !strings.Contains(w.Body.String(), "UPSTREAM_UNCERTAIN") {
		t.Fatalf("calls=%d status=%d body=%s", calls.Load(), w.Code, w.Body.String())
	}
}

func TestRelayRejectsRedirectsUntrustedTLSAndInvalidTargets(t *testing.T) {
	for _, target := range []string{"http://example.test", "https://user:password@example.test", "https://example.test/path", "https://example.test?x=y", "https://example.test/#fragment", "https://"} {
		if relay, err := NewRelay(target); err == nil {
			relay.Close()
			t.Errorf("accepted %s", target)
		}
	}
	var destinationCalls atomic.Int32
	destination := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { destinationCalls.Add(1) }))
	defer destination.Close()
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, destination.URL, 307) }))
	defer upstream.Close()
	relay := trustedRelay(t, upstream)
	w := httptest.NewRecorder()
	relay.ServeHTTP(w, httptest.NewRequest("POST", "/v1/version", strings.NewReader(`{}`)))
	if w.Code != 504 || destinationCalls.Load() != 0 {
		t.Fatalf("redirect followed: %d %d", w.Code, destinationCalls.Load())
	}
	untrusted, err := NewRelay(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer untrusted.Close()
	w = httptest.NewRecorder()
	untrusted.ServeHTTP(w, httptest.NewRequest("POST", "/v1/version", strings.NewReader(`{}`)))
	if w.Code != 502 {
		t.Fatal("untrusted certificate accepted")
	}
}

func TestRelayBoundsBodiesAndTimeout(t *testing.T) {
	stop := make(chan struct{})
	var calls atomic.Int32
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.URL.Path == "/v1/slow" {
			_, _ = io.Copy(io.Discard, r.Body)
			select {
			case <-r.Context().Done():
			case <-stop:
			}
			return
		}
		_, _ = io.WriteString(w, strings.Repeat("x", int(MaxRequestBodyLimit)+1))
	}))
	defer upstream.Close()
	defer close(stop)
	relay := trustedRelay(t, upstream)
	for _, test := range []struct {
		path, body string
		status     int
	}{{"/v1/create", strings.Repeat("x", int(MaxRequestBodyLimit)+1), 413}, {"/v1/version", `{}`, 504}, {"/v1/version?target=elsewhere", `{}`, 400}, {"//evil/v1/create", `{}`, 400}} {
		w := httptest.NewRecorder()
		relay.ServeHTTP(w, httptest.NewRequest("POST", test.path, strings.NewReader(test.body)))
		if w.Code != test.status {
			t.Errorf("%s: got %d want %d", test.path, w.Code, test.status)
		}
	}
	if calls.Load() != 1 {
		t.Fatalf("unexpected forwarding: %d", calls.Load())
	}
	relay.http.Timeout = 20 * time.Millisecond
	w := httptest.NewRecorder()
	relay.ServeHTTP(w, httptest.NewRequest("POST", "/v1/slow", strings.NewReader(`{}`)))
	if w.Code != 504 {
		t.Fatalf("timeout: %d", w.Code)
	}
}

func TestRelayForwardsExactWireLimits(t *testing.T) {
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n, err := io.Copy(io.Discard, r.Body)
		if err != nil || n != MaxRequestBodyLimit {
			t.Errorf("request bytes %d: %v", n, err)
		}
		_, _ = io.WriteString(w, strings.Repeat("x", int(ResponseBodyLimit)))
	}))
	defer upstream.Close()
	relay := trustedRelay(t, upstream)
	w := httptest.NewRecorder()
	relay.ServeHTTP(w, httptest.NewRequest("POST", "/v1/evidence", strings.NewReader(strings.Repeat("x", int(MaxRequestBodyLimit)))))
	if w.Code != 200 || int64(w.Body.Len()) != ResponseBodyLimit {
		t.Fatalf("wire bound rejected %d %d", w.Code, w.Body.Len())
	}
}
