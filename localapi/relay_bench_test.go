package localapi

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// In-process relay entry, real verified TLS upstream, synthetic fixed response.
// Isolates connection policy with identical relay validation/copying. No DB or
// Unix client hop is included. Run with fixed -benchtime=500x -count=3.
func BenchmarkRelayConnectionPolicy(b *testing.B) {
	for _, size := range []int{256, 16 << 10, 256 << 10} {
		for _, concurrency := range []int{1, 8} {
			for _, fresh := range []bool{true, false} {
				name := fmt.Sprintf("bytes=%d/c=%d/fresh=%t", size, concurrency, fresh)
				b.Run(name, func(b *testing.B) {
					body := strings.Repeat("x", size)
					var connections atomic.Int64
					upstream := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						if r.Header.Get("Authorization") != "Bearer synthetic" {
							w.WriteHeader(401)
							return
						}
						if _, err := io.Copy(io.Discard, r.Body); err != nil {
							b.Error(err)
							return
						}
						_, _ = io.WriteString(w, body)
					}))
					upstream.Config.ConnState = func(_ net.Conn, s http.ConnState) {
						if s == http.StateNew {
							connections.Add(1)
						}
					}
					// Speculative pooled dials can be cancelled during teardown. Keep their
					// TLS server diagnostics out of benchmark rows; call errors still fail.
					upstream.Config.ErrorLog = log.New(io.Discard, "", 0)
					upstream.StartTLS()
					defer upstream.Close()
					relay, err := NewRelay(upstream.URL)
					if err != nil {
						b.Fatal(err)
					}
					defer relay.Close()
					roots := x509.NewCertPool()
					roots.AddCert(upstream.Certificate())
					relay.transport.TLSClientConfig = &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13}
					relay.transport.DisableKeepAlives = fresh
					call := func() error {
						r := httptest.NewRequest("POST", "/v1/create", strings.NewReader(body))
						r.Header.Set("Authorization", "Bearer synthetic")
						w := httptest.NewRecorder()
						relay.ServeHTTP(w, r)
						if w.Code != 200 || w.Body.String() != body {
							return fmt.Errorf("status %d or response mismatch", w.Code)
						}
						return nil
					}
					// Prime each concurrent lane outside timing; report measured new connections.
					var warm sync.WaitGroup
					for range concurrency {
						warm.Add(1)
						go func() {
							defer warm.Done()
							if err := call(); err != nil {
								b.Error(err)
							}
						}()
					}
					warm.Wait()
					baseline := connections.Load()
					durations := make([]int64, b.N)
					var index atomic.Int64
					var wg sync.WaitGroup
					b.ResetTimer()
					start := time.Now()
					for range concurrency {
						wg.Add(1)
						go func() {
							defer wg.Done()
							for {
								i := int(index.Add(1) - 1)
								if i >= b.N {
									return
								}
								start := time.Now()
								if err := call(); err != nil {
									b.Error(err)
								}
								durations[i] = time.Since(start).Nanoseconds()
							}
						}()
					}
					wg.Wait()
					elapsed := time.Since(start)
					b.StopTimer()
					sort.Slice(durations, func(i, j int) bool { return durations[i] < durations[j] })
					for _, p := range []int{50, 95, 99} {
						b.ReportMetric(float64(durations[(b.N-1)*p/100])/1000, fmt.Sprintf("p%d-us", p))
					}
					b.ReportMetric(float64(b.N)/elapsed.Seconds(), "calls/s")
					b.ReportMetric(float64(connections.Load()-baseline)/float64(b.N), "conns/op")
				})
			}
		}
	}
}
