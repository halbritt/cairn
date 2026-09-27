package localapi

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// Inject at the plaintext connection boundary, after a real verified TLS
// handshake. Arm only after a successful request returned the connection to
// the pool. A retry would dial a second connection and incorrectly succeed.
type failingReuseConn struct {
	net.Conn
	fail    atomic.Bool
	partial bool
	writes  atomic.Int32
}

func (c *failingReuseConn) Write(p []byte) (int, error) {
	if c.fail.Load() {
		c.writes.Add(1)
		if c.partial {
			n, err := c.Conn.Write(p[:1])
			if err != nil {
				return n, err
			}
			return n, errors.New("injected partial write")
		}
		return 0, errors.New("injected zero write")
	}
	return c.Conn.Write(p)
}

func relayCall(relay *Relay, body, token string) *httptest.ResponseRecorder {
	w := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/v1/create", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+token)
	relay.ServeHTTP(w, req)
	return w
}

func TestRelayReusedConnectionWriteFailureNeverReplays(t *testing.T) {
	for _, body := range []string{"", `{"request_id":"fixed"}`} {
		for _, partial := range []bool{false, true} {
			t.Run(strings.ReplaceAll(body, "\"", "")+map[bool]string{false: "/zero", true: "/partial"}[partial], func(t *testing.T) {
				var calls, dials atomic.Int32
				upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls.Add(1)
					_, _ = io.Copy(io.Discard, r.Body)
					_, _ = io.WriteString(w, `{}`)
				}))
				defer upstream.Close()
				relay := trustedRelay(t, upstream)
				var conn *failingReuseConn
				relay.transport.DialTLSContext = func(ctx context.Context, network, addr string) (net.Conn, error) {
					dials.Add(1)
					d := tls.Dialer{Config: relay.transport.TLSClientConfig}
					raw, err := d.DialContext(ctx, network, addr)
					if err != nil {
						return nil, err
					}
					c := &failingReuseConn{Conn: raw, partial: partial}
					conn = c
					return c, nil
				}
				if w := relayCall(relay, `{}`, "first"); w.Code != 200 {
					t.Fatal(w.Code, w.Body.String())
				}
				conn.fail.Store(true)
				w := relayCall(relay, body, "second")
				if w.Code != 504 || !strings.Contains(w.Body.String(), "UPSTREAM_UNCERTAIN") || dials.Load() != 1 || calls.Load() != 1 || conn.writes.Load() != 1 {
					t.Fatalf("status=%d body=%s dials=%d calls=%d writes=%d", w.Code, w.Body.String(), dials.Load(), calls.Load(), conn.writes.Load())
				}
			})
		}
	}
}

func TestRelayReuseAuthenticatesEachRequestAndClosesIdle(t *testing.T) {
	var connections atomic.Int32
	upstream := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		if r.Header.Get("Authorization") != "Bearer allowed" {
			w.WriteHeader(401)
			return
		}
		_, _ = io.WriteString(w, `{}`)
	}))
	upstream.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			connections.Add(1)
		}
	}
	upstream.StartTLS()
	defer upstream.Close()
	relay := trustedRelay(t, upstream)
	for i, token := range []string{"allowed", "revoked", "allowed"} {
		want := 200
		if i == 1 {
			want = 401
		}
		if w := relayCall(relay, `{}`, token); w.Code != want {
			t.Fatal(w.Code, w.Body.String())
		}
	}
	if connections.Load() != 1 {
		t.Fatalf("not reused: %d", connections.Load())
	}
	relay.Close()
	if w := relayCall(relay, `{}`, "allowed"); w.Code != 200 {
		t.Fatal(w.Code)
	}
	if connections.Load() != 2 {
		t.Fatalf("Close did not discard idle connection: %d", connections.Load())
	}
}

func TestRelayLostOrTruncatedReplyOnReusedConnection(t *testing.T) {
	for _, mode := range []string{"lost", "truncated", "cancelled"} {
		t.Run(mode, func(t *testing.T) {
			var calls, connections atomic.Int32
			upstream := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				n := calls.Add(1)
				_, _ = io.Copy(io.Discard, r.Body)
				if n == 1 {
					_, _ = io.WriteString(w, `{}`)
					return
				}
				if mode == "cancelled" {
					<-r.Context().Done()
					return
				}
				conn, buf, err := w.(http.Hijacker).Hijack()
				if err != nil {
					t.Error(err)
					return
				}
				if mode == "truncated" {
					_, _ = buf.WriteString("HTTP/1.1 200 OK\r\nContent-Length: 99\r\n\r\n{")
					_ = buf.Flush()
				}
				_ = conn.Close()
			}))
			upstream.Config.ConnState = func(_ net.Conn, state http.ConnState) {
				if state == http.StateNew {
					connections.Add(1)
				}
			}
			upstream.StartTLS()
			defer upstream.Close()
			relay := trustedRelay(t, upstream)
			if w := relayCall(relay, `{}`, "one"); w.Code != 200 {
				t.Fatal(w.Code)
			}
			if mode == "cancelled" {
				relay.http.Timeout = 20 * time.Millisecond
			}
			w := relayCall(relay, `{"request_id":"fixed"}`, "two")
			if w.Code != 504 || !strings.Contains(w.Body.String(), "UPSTREAM_UNCERTAIN") || calls.Load() != 2 || connections.Load() != 1 {
				t.Fatalf("%d %s calls=%d conns=%d", w.Code, w.Body.String(), calls.Load(), connections.Load())
			}
		})
	}
}

// The Unix client is also pooled. Its documented no-retry contract must hold
// before a request ever reaches the relay, not just on the TLS hop.
func TestClientReusedConnectionWriteFailureNeverReplays(t *testing.T) {
	for _, partial := range []bool{false, true} {
		t.Run(fmt.Sprint(partial), func(t *testing.T) {
			var calls, dials atomic.Int32
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				_, _ = io.Copy(io.Discard, r.Body)
				_, _ = io.WriteString(w, `{"schema":"cairn.response/1","ok":true,"status":"OK","data":{}}`)
			}))
			defer upstream.Close()
			token := filepath.Join(t.TempDir(), "token")
			if err := os.WriteFile(token, []byte("synthetic"), 0600); err != nil {
				t.Fatal(err)
			}
			client, err := NewClient("unused", token)
			if err != nil {
				t.Fatal(err)
			}
			defer client.Close()
			var conn *failingReuseConn
			client.transport.DialContext = func(ctx context.Context, _, _ string) (net.Conn, error) {
				dials.Add(1)
				raw, err := (&net.Dialer{}).DialContext(ctx, "tcp", upstream.Listener.Addr().String())
				if err != nil {
					return nil, err
				}
				conn = &failingReuseConn{Conn: raw, partial: partial}
				return conn, nil
			}
			if err := client.Call(context.Background(), "create", map[string]string{"request_id": "warm"}, &struct{}{}); err != nil {
				t.Fatal(err)
			}
			conn.fail.Store(true)
			err = client.Call(context.Background(), "create", map[string]string{"request_id": "fixed"}, &struct{}{})
			if err == nil || dials.Load() != 1 || calls.Load() != 1 || conn.writes.Load() != 1 {
				t.Fatalf("err=%v dials=%d calls=%d writes=%d", err, dials.Load(), calls.Load(), conn.writes.Load())
			}
		})
	}
}

func TestRelayPoolWaitCancellationIsKnownUnsent(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	var calls atomic.Int32
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			close(entered)
			<-release
		}
		_, _ = io.Copy(io.Discard, r.Body)
		_, _ = io.WriteString(w, `{}`)
	}))
	defer upstream.Close()
	relay := trustedRelay(t, upstream)
	relay.transport.MaxConnsPerHost = 1
	first := make(chan *httptest.ResponseRecorder, 1)
	go func() { first <- relayCall(relay, `{}`, "one") }()
	<-entered
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	req := httptest.NewRequest("POST", "/v1/create", strings.NewReader(`{}`)).WithContext(ctx)
	w := httptest.NewRecorder()
	relay.ServeHTTP(w, req)
	close(release)
	result := <-first
	if result.Code != 200 || w.Code != 502 || !strings.Contains(w.Body.String(), "UPSTREAM_UNAVAILABLE") || calls.Load() != 1 {
		t.Fatalf("first=%d second=%d body=%s calls=%d", result.Code, w.Code, w.Body.String(), calls.Load())
	}
}
