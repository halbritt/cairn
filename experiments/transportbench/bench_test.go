// Package transportbench is an isolated experiment, not a Cairn API server.
package transportbench

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
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

	pb "github.com/halbritt/cairn/experiments/transportbench/proto"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

const wireLimit = 8 << 20
const bearer = "Bearer synthetic-benchmark-only"

// Identical fields/semantics for JSON and typed generated protobuf messages.
// Text (not bytes/base64) models selected memory body text for both codecs.
type request struct {
	RequestID  string `json:"request_id"`
	Collection string `json:"collection"`
	Body       string `json:"body"`
}
type reply struct {
	RequestID string `json:"request_id"`
	Body      string `json:"body"`
	SHA256    string `json:"sha256"`
}

func exchange(q request) reply {
	sum := sha256.Sum256([]byte(q.Body))
	return reply{q.RequestID, q.Body, hex.EncodeToString(sum[:])}
}
func validate(q request) error {
	if q.RequestID == "" || q.Collection != "synthetic" || len(q.Body) > wireLimit/2 {
		return fmt.Errorf("invalid request")
	}
	return nil
}

type service struct {
	pb.UnimplementedTransportBenchServer
	calls atomic.Int64
	fail  atomic.Bool
}

func (s *service) Exchange(ctx context.Context, q *pb.ExchangeRequest) (*pb.ExchangeReply, error) {
	s.calls.Add(1)
	md, _ := metadata.FromIncomingContext(ctx)
	if v := md.Get("authorization"); len(v) != 1 || v[0] != bearer {
		return nil, status.Error(codes.Unauthenticated, "token refused")
	}
	if err := validate(request{q.RequestId, q.Collection, q.Body}); err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}
	// Commit retry state before application work. The experiment has no DB.
	if err := grpc.SendHeader(ctx, metadata.Pairs("x-bench", "1")); err != nil {
		return nil, err
	}
	if s.fail.Load() {
		return nil, status.Error(codes.Unavailable, "injected after handler admission")
	}
	r := exchange(request{q.RequestId, q.Collection, q.Body})
	return &pb.ExchangeReply{RequestId: r.RequestID, Body: r.Body, Sha256: r.SHA256}, nil
}

type counters struct{ connections, rx, tx atomic.Int64 }
type measuredListener struct {
	net.Listener
	c *counters
}

func (l measuredListener) Accept() (net.Conn, error) {
	c, err := l.Listener.Accept()
	if err != nil {
		return nil, err
	}
	l.c.connections.Add(1)
	return measuredConn{c, l.c}, nil
}

type measuredConn struct {
	net.Conn
	c *counters
}

func (c measuredConn) Read(p []byte) (int, error) {
	n, e := c.Conn.Read(p)
	c.c.rx.Add(int64(n))
	return n, e
}
func (c measuredConn) Write(p []byte) (int, error) {
	n, e := c.Conn.Write(p)
	c.c.tx.Add(int64(n))
	return n, e
}

type fixture struct {
	server    *httptest.Server
	http      *http.Client
	grpc      *grpc.ClientConn
	service   *service
	counts    counters
	httpCalls atomic.Int64
	kind      string
}

func newFixture(t testing.TB, kind string) *fixture {
	t.Helper()
	f := &fixture{kind: kind, service: &service{}}
	var handler http.Handler
	var gs *grpc.Server
	if kind == "grpc-proto" || kind == "grpc-native" {
		gs = grpc.NewServer(grpc.MaxRecvMsgSize(wireLimit), grpc.MaxSendMsgSize(wireLimit), grpc.MaxConcurrentStreams(32))
		pb.RegisterTransportBenchServer(gs, f.service)
		handler = gs
	} else {
		handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			f.httpCalls.Add(1)
			if r.Method != "POST" || r.URL.Path != "/exchange" {
				http.Error(w, "route", 404)
				return
			}
			if v := r.Header.Values("Authorization"); len(v) != 1 || v[0] != bearer {
				http.Error(w, "token refused", 401)
				return
			}
			raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, wireLimit))
			if err != nil {
				http.Error(w, "bound", 413)
				return
			}
			var q request
			if err := json.Unmarshal(raw, &q); err != nil {
				http.Error(w, "json", 400)
				return
			}
			if err := validate(q); err != nil {
				http.Error(w, "invalid", 400)
				return
			}
			rpl := exchange(q)
			w.Header().Set("Content-Type", "application/json")
			if err := json.NewEncoder(w).Encode(rpl); err != nil {
				t.Errorf("encode: %v", err)
			}
		})
	}
	s := httptest.NewUnstartedServer(handler)
	s.Listener = measuredListener{s.Listener, &f.counts}
	s.EnableHTTP2 = kind == "grpc-proto"
	s.TLS = &tls.Config{MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13}
	// Cancelled speculative HTTP pool dials are expected at teardown;
	// suppress their server diagnostics, not returned call failures.
	s.Config.ErrorLog = log.New(io.Discard, "", 0)
	s.StartTLS()
	f.server = s
	roots := x509.NewCertPool()
	roots.AddCert(s.Certificate())
	tc := &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13}
	if kind == "grpc-native" {
		// Use the native gRPC HTTP/2 transport as well as ServeHTTP. Reuse the
		// same test TLS identity; fixture setup is outside measurement.
		serverTLS := s.TLS.Clone()
		s.Close()
		gs.Stop()
		listener, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		f.counts.connections.Store(0)
		f.counts.rx.Store(0)
		f.counts.tx.Store(0)
		gs = grpc.NewServer(grpc.Creds(credentials.NewTLS(serverTLS)), grpc.MaxRecvMsgSize(wireLimit), grpc.MaxSendMsgSize(wireLimit), grpc.MaxConcurrentStreams(32))
		pb.RegisterTransportBenchServer(gs, f.service)
		done := make(chan error, 1)
		go func() { done <- gs.Serve(measuredListener{listener, &f.counts}) }()
		s.URL = "https://" + listener.Addr().String()
		t.Cleanup(func() {
			gs.Stop()
			if err := <-done; err != nil {
				t.Errorf("native Serve: %v", err)
			}
		})
	}
	if kind == "grpc-proto" || kind == "grpc-native" {
		var err error
		f.grpc, err = grpc.NewClient(strings.TrimPrefix(s.URL, "https://"), grpc.WithTransportCredentials(credentials.NewTLS(tc)), grpc.WithDisableRetry(), grpc.WithDisableServiceConfig(), grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(wireLimit), grpc.MaxCallSendMsgSize(wireLimit), grpc.MaxRetryRPCBufferSize(0)))
		if err != nil {
			t.Fatal(err)
		}
	} else {
		tr := &http.Transport{TLSClientConfig: tc, TLSNextProto: map[string]func(string, *tls.Conn) http.RoundTripper{}, DisableCompression: true, DisableKeepAlives: kind == "http-fresh", MaxIdleConns: 16, MaxIdleConnsPerHost: 16, MaxConnsPerHost: 32, IdleConnTimeout: 30 * time.Second}
		f.http = &http.Client{Transport: tr, Timeout: 5 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	}
	t.Cleanup(func() {
		if f.grpc != nil {
			_ = f.grpc.Close()
		}
		if f.http != nil {
			f.http.CloseIdleConnections()
		}
		s.Close()
		if gs != nil {
			gs.Stop()
		}
	})
	return f
}
func (f *fixture) call(q request, token string) (reply, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if f.grpc != nil {
		ctx = metadata.NewOutgoingContext(ctx, metadata.Pairs("authorization", token))
		p, err := pb.NewTransportBenchClient(f.grpc).Exchange(ctx, &pb.ExchangeRequest{RequestId: q.RequestID, Collection: q.Collection, Body: q.Body})
		if err != nil {
			return reply{}, err
		}
		return reply{p.RequestId, p.Body, p.Sha256}, nil
	}
	raw, err := json.Marshal(q)
	if err != nil {
		return reply{}, err
	}
	r, err := http.NewRequestWithContext(ctx, "POST", f.server.URL+"/exchange", io.NopCloser(bytes.NewReader(raw)))
	if err != nil {
		return reply{}, err
	}
	r.ContentLength = int64(len(raw))
	r.GetBody = nil
	r.Header.Set("Authorization", token)
	r.Header.Set("Content-Type", "application/json")
	out, err := f.http.Do(r)
	if err != nil {
		return reply{}, err
	}
	defer out.Body.Close()
	body, err := io.ReadAll(io.LimitReader(out.Body, wireLimit+1))
	if err != nil {
		return reply{}, err
	}
	if len(body) > wireLimit || out.StatusCode != 200 {
		return reply{}, fmt.Errorf("HTTP %d or oversized body", out.StatusCode)
	}
	var result reply
	err = json.Unmarshal(body, &result)
	return result, err
}

func TestEquivalentPayloadAndPerCallAuth(t *testing.T) {
	q := request{"11111111-1111-4111-8111-111111111111", "synthetic", strings.Repeat("selected text α\n", 100)}
	for _, kind := range []string{"http-fresh", "http-reuse", "grpc-proto", "grpc-native"} {
		t.Run(kind, func(t *testing.T) {
			f := newFixture(t, kind)
			for _, token := range []string{bearer, "Bearer revoked", bearer} {
				r, err := f.call(q, token)
				if token != bearer {
					if err == nil {
						t.Fatal("accepted bad token")
					}
					continue
				}
				if err != nil || r != exchange(q) {
					t.Fatalf("reply mismatch: %v", err)
				}
			}
			if f.grpc != nil {
				f.service.fail.Store(true)
				before := f.service.calls.Load()
				_, err := f.call(q, bearer)
				if status.Code(err) != codes.Unavailable || f.service.calls.Load() != before+1 {
					t.Fatal("RPC replay or missing failure", err)
				}
			}
		})
	}
}

func BenchmarkExchange(b *testing.B) {
	for _, size := range []int{256, 16 << 10, 256 << 10} {
		for _, concurrency := range []int{1, 8} {
			for _, kind := range []string{"http-fresh", "http-reuse", "grpc-proto", "grpc-native"} {
				b.Run(fmt.Sprintf("%s/bytes=%d/c=%d", kind, size, concurrency), func(b *testing.B) {
					f := newFixture(b, kind)
					q := request{"11111111-1111-4111-8111-111111111111", "synthetic", strings.Repeat("x", size)}
					expected := exchange(q)
					call := func() error {
						r, err := f.call(q, bearer)
						if err != nil {
							return err
						}
						if r != expected {
							return fmt.Errorf("mismatch")
						}
						return nil
					}
					cold := time.Now()
					if err := call(); err != nil {
						b.Fatal(err)
					}
					coldElapsed := time.Since(cold)
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
					baselineC := f.counts.connections.Load()
					baselineRx := f.counts.rx.Load()
					baselineTx := f.counts.tx.Load()
					var index atomic.Int64
					var wg sync.WaitGroup
					durations := make([]int64, b.N)
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
					b.ReportMetric(float64(coldElapsed.Nanoseconds())/1000, "first-us")
					b.ReportMetric(float64(f.counts.connections.Load()-baselineC)/float64(b.N), "conns/op")
					b.ReportMetric(float64(f.counts.rx.Load()-baselineRx+f.counts.tx.Load()-baselineTx)/float64(b.N), "TLS-B/op")
					calls := f.httpCalls.Load() + f.service.calls.Load()
					if calls != int64(1+concurrency+b.N) {
						b.Fatalf("unexpected handler attempts %d", calls)
					}
				})
			}
		}
	}
}

func TestEquivalentApplicationBounds(t *testing.T) {
	for _, kind := range []string{"http-fresh", "http-reuse", "grpc-proto", "grpc-native"} {
		t.Run(kind, func(t *testing.T) {
			f := newFixture(t, kind)
			for _, q := range []request{
				{"", "synthetic", "body"},
				{"id", "wrong-collection", "body"},
				{"id", "synthetic", strings.Repeat("x", wireLimit/2+1)},
			} {
				if _, err := f.call(q, bearer); err == nil {
					t.Fatal("invalid application input accepted")
				}
			}
			// Both encodings must also refuse a request above the 8 MiB wire cap.
			if _, err := f.call(request{"id", "synthetic", strings.Repeat("x", wireLimit+1)}, bearer); err == nil {
				t.Fatal("wire bound not enforced")
			}
		})
	}
}
