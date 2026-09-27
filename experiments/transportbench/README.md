# Isolated transport comparison

This nested module does not participate in Cairn's build or change its Go
module graph. It uses pinned gRPC 1.75.1 / Protobuf 1.36.8 to compare an actual
unary generated Protobuf service (native and ServeHTTP variants) with JSON over fresh and reused HTTP/1.1 TLS
connections. It is a synthetic selected-text verification workload, not a
replacement Cairn API, storage implementation, or authorization model.

```sh
# From this directory; downloads may require GOPROXY=https://proxy.golang.org.
go test -race ./...
go vet ./...
go test -run '^$' -bench BenchmarkExchange -benchtime=500x -count=3 -cpu=4 -benchmem
# Machine-readable output is also available; retain outside the repository.
go test -json -run '^$' -bench BenchmarkExchange -benchtime=500x -count=3 -cpu=4
```

Keep other builds and benchmark processes idle during timing. Each case runs
500 calls with a fixed 1 or 8 workers, three repeats, with 256 B / 16 KiB /
256 KiB of identical text in each direction. All variants send a request ID,
collection and body; authenticate a synthetic bearer token on each call;
validate inputs; compute SHA-256; and return request ID, body and hex digest.
Every measured response is checked and the total handler count must match the
number of calls. There is no compression or TLS session resumption. All use
verified TLS 1.3 with the same test certificate and loopback topology.

The HTTP variants share the same handler and client policy except connection
reuse. `grpc-proto` uses generated messages and the gRPC server's HTTP/2
`ServeHTTP` integration under Go's TLS test server. `grpc-native` uses the
separate native grpc.Server.Serve HTTP/2 implementation with verified TLS,
the same certificate, service and client options. Both are experimental.
HTTP allows at most 32 open / 16 idle connections; gRPC multiplexes over one
HTTP/2 connection. `grpc-native` uses grpc.Server.Serve with 32 maximum streams;
`grpc-proto` uses ServeHTTP with Go HTTP/2 defaults (grpc.MaxConcurrentStreams
does not configure that path). Fixed worker counts limit offered load,
not equal connection counts. No goroutine-per-call benchmark fan-out is used.

`first-us` includes the initial connection/channel establishment but excludes
fixture construction. Steady metrics exclude that call and a concurrent warmup
round. A warmup may create fewer than eight HTTP connections; any additional
dials during measurement remain included in `conns/op` and TLS bytes. `ns/op`
is elapsed time divided by completed calls (throughput inverse), **not** call
latency under concurrency. `p50-us`, `p95-us`, `p99-us` are observed per-call
latency order statistics; `calls/s` is throughput. `TLS-B/op` counts both read
and written TLS bytes at the server's TCP connection boundary, including
handshakes in the measured interval, excluding TCP/IP framing. It can include
asynchronous HTTP/2 control traffic; it is not just payload size. `-benchmem`
reports process-wide Go allocations in the measured interval, including client
and server. These are loopback host observations; do not extrapolate to WAN or
tailnet latency, packet loss, persistent deployment, or database throughput.

HTTP calls use non-rewindable POST bodies and no idempotency headers. gRPC uses
`WithDisableRetry`, `WithDisableServiceConfig`, zero retry buffer and response
headers before application work. gRPC's general transparent retry semantics
are different from the relay contract; this experiment is **not** qualified
for production mutations. Tests check equivalent Unicode payloads, per-call
auth and no handler replay on an injected admitted `Unavailable` error. The
production relay's separate fault suite is the adoption gate for HTTP reuse.

To regenerate checked-in schema-derived bindings (protoc 3.21.12 used):

```sh
GOBIN=/tmp/cairn-protoc-tools go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.8
GOBIN=/tmp/cairn-protoc-tools go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1
PATH=/tmp/cairn-protoc-tools:$PATH protoc --go_out=. --go_opt=paths=source_relative \
  --go-grpc_out=. --go-grpc_opt=paths=source_relative proto/exchange.proto
```

The companion production-relay benchmark lives in
`../../localapi/relay_bench_test.go`. It keeps relay validation, forwarding and
copying identical while changing only upstream connection policy. Its input
and output are opaque bounded bodies, with a real TLS upstream and an
in-process relay entry; it excludes the local Unix client hop and the database.
See `../../docs/transport-comparison.md` for the measured decision and limits.
