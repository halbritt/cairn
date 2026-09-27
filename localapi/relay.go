package localapi

import (
	"bytes"
	"crypto/tls"
	"io"
	"net"
	"net/http"
	"net/http/httptrace"
	"net/url"
	"strings"
	"sync/atomic"
	"time"

	"github.com/halbritt/cairn/core"
)

// Relay forwards the local protocol to one verified HTTPS origin. It does not
// select credentials, interpret operations, cache results or retry requests.
type Relay struct {
	upstream  *url.URL
	http      *http.Client
	transport *http.Transport
}

func ValidateUpstream(upstream string) error {
	_, err := relayOrigin(upstream)
	return err
}

func relayOrigin(upstream string) (*url.URL, error) {
	origin, err := url.Parse(upstream)
	if err != nil || origin.Scheme != "https" || origin.Hostname() == "" || origin.User != nil || (origin.Path != "" && origin.Path != "/") || origin.RawQuery != "" || origin.ForceQuery || origin.Fragment != "" || origin.Opaque != "" {
		return nil, &core.Error{Code: "INVALID_REQUEST", Message: "upstream must be an HTTPS origin without credentials, path, query or fragment"}
	}
	return origin, nil
}

func NewRelay(upstream string) (*Relay, error) {
	origin, err := relayOrigin(upstream)
	if err != nil {
		return nil, err
	}
	transport := &http.Transport{
		DialContext:         (&net.Dialer{Timeout: 5 * time.Second}).DialContext,
		TLSClientConfig:     &tls.Config{MinVersion: tls.VersionTLS12},
		TLSHandshakeTimeout: 5 * time.Second, ResponseHeaderTimeout: 30 * time.Second,
		MaxResponseHeaderBytes: 16 * 1024, DisableCompression: true,
		// Pool only HTTP/1 connections. ServeHTTP makes every POST non-replayable,
		// including empty bodies, before handing it to net/http.
		MaxIdleConns: 16, MaxIdleConnsPerHost: 16, MaxConnsPerHost: 32,
		IdleConnTimeout: 30 * time.Second,
		TLSNextProto:    map[string]func(string, *tls.Conn) http.RoundTripper{},
	}
	return &Relay{upstream: origin, transport: transport, http: &http.Client{Transport: transport, Timeout: 32 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}

func (r *Relay) Close() { r.transport.CloseIdleConnections() }

func (r *Relay) ServeHTTP(w http.ResponseWriter, in *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	operation, ok := strings.CutPrefix(in.URL.Path, "/v1/")
	if in.Method != "POST" || !ok || operation == "" || strings.Trim(operation, "abcdefghijklmnopqrstuvwxyz-") != "" || in.URL.RawQuery != "" || in.URL.ForceQuery || in.URL.RawPath != "" {
		writeError(w, 400, "INVALID_REQUEST", "relay requires POST to /v1/operation without query or escaped path")
		return
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, in.Body, MaxRequestBodyLimit))
	if err != nil {
		writeError(w, 413, "INVALID_REQUEST", "request exceeds relay body limit or could not be read")
		return
	}
	target := *r.upstream
	target.Path = in.URL.Path
	out, err := http.NewRequestWithContext(in.Context(), "POST", target.String(), io.NopCloser(bytes.NewReader(body)))
	if err != nil {
		writeError(w, 400, "INVALID_REQUEST", "invalid relay request")
		return
	}
	// A nil/NoBody empty request permits net/http to retry a zero-byte write
	// on a reused connection. Keep a non-nil, non-rewindable body even for
	// empty input (ContentLength 0 then means unknown, sent chunked). POST,
	// nil GetBody, and the header allowlist also prevent idempotent replay.
	out.ContentLength = int64(len(body))
	out.GetBody = nil
	for _, header := range []string{"Authorization", "Content-Type", "Cairn-Agent-ID", "Cairn-Execution-ID"} {
		if values := in.Header.Values(header); len(values) > 1 {
			writeError(w, 400, "INVALID_REQUEST", "duplicate protocol header")
			return
		} else if len(values) == 1 {
			out.Header.Set(header, values[0])
		}
	}
	// A connection handed to net/http might have received request bytes even if
	// no response arrives. Only failure before that point is known to be unsent.
	var connected atomic.Bool
	out = out.WithContext(httptrace.WithClientTrace(out.Context(), &httptrace.ClientTrace{GotConn: func(httptrace.GotConnInfo) { connected.Store(true) }}))
	reply, err := r.http.Do(out)
	if err != nil {
		if connected.Load() {
			relayUncertain(w)
		} else {
			writeError(w, 502, "UPSTREAM_UNAVAILABLE", "cannot establish the upstream connection; request was not sent")
		}
		return
	}
	defer reply.Body.Close()
	encoded, err := io.ReadAll(io.LimitReader(reply.Body, ResponseBodyLimit+1))
	if err != nil || int64(len(encoded)) > ResponseBodyLimit || (reply.StatusCode >= 300 && reply.StatusCode < 400) {
		relayUncertain(w)
		return
	}
	if contentType := reply.Header.Get("Content-Type"); contentType != "" {
		w.Header().Set("Content-Type", contentType)
	}
	w.WriteHeader(reply.StatusCode)
	_, _ = w.Write(encoded)
}

func relayUncertain(w http.ResponseWriter) {
	writeError(w, 504, "UPSTREAM_UNCERTAIN", "upstream result is unknown; reconcile or retry only the same request ID and arguments")
}
