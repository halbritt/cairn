package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/refusal"
	"github.com/halbritt/cairn/web/swarmview"
)

// The swarm view server (docs/swarm-view.md). It is a separate loopback
// process with the local operator channel. It checks the host and origin,
// uses session cookies and checks CSRF tokens. A sign-in token read from an
// owner-only file serves multiple browser sessions.

const (
	swarmDefaultPort     = 8087
	swarmSessionLifetime = 24 * time.Hour
	swarmSessionLimit    = 64
	swarmTokenFileName   = "swarm-view.token"
	swarmBodyLimit       = 256 << 10
)

var swarmTokenPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{32,256}$`)

type swarmBrowserSession struct {
	csrf    string
	expires time.Time
	// revoked is closed when the session ends before its lifetime: at
	// logout or when the session limit evicts it. An open stream ends then.
	revoked chan struct{}
}

// swarmReads is the read side of the view. The store implements it; a test
// can script it.
type swarmReads interface {
	ReadSwarmStream(ctx context.Context, req core.SwarmStreamRequest) (core.SwarmStreamRead, error)
	GetSwarmSnapshot(ctx context.Context, req core.SwarmSnapshotRequest) (core.SwarmSnapshot, error)
	GetSwarmLanding(ctx context.Context, repo string) (core.SwarmLanding, error)
}

type swarmServer struct {
	// store performs the operator actions. reads is the same store.
	store      *core.Store
	reads      swarmReads
	collection string
	port       int
	tokenHash  [sha256.Size]byte
	// planeURL is the address of a tracker item with "{id}" for the
	// identifier, or empty. Plane is linked, never read.
	planeURL string
	tick     time.Duration
	panes    *swarmLoop[swarmPaneObservation]
	windows  *swarmLoop[swarmWindowObservation]
	now      func() time.Time

	mu       sync.Mutex
	sessions map[string]swarmBrowserSession
}

func newSwarmServer(store *core.Store, collection string, port int, token string, panes swarmPaneReader) *swarmServer {
	return &swarmServer{
		store: store, reads: store, collection: collection, port: port, tokenHash: sha256.Sum256([]byte(token)),
		tick: 2 * time.Second, panes: newSwarmPaneLoop(panes, 5*time.Second, 15*time.Second),
		windows: newSwarmWindowLoop(swarmNoWindowReader, 15*time.Second, 60*time.Second),
		now:     time.Now, sessions: map[string]swarmBrowserSession{},
	}
}

// swarmNoWindowReader stands in until a transcript reader is configured.
func swarmNoWindowReader(ctx context.Context) swarmWindowObservation {
	return swarmWindowObservation{ObservedAt: time.Now(), Status: "unavailable", Error: "no transcript reader is configured", Windows: map[string]swarmWindow{}, Unknown: map[string]string{}}
}

// readOrCreateSwarmToken returns the login token. A missing file is created
// with mode 0600. An existing file is refused unless it is a regular file
// owned by this user that no one else can read.
func readOrCreateSwarmToken(path string) (string, error) {
	if !filepath.IsAbs(path) {
		return "", invalid("token file must be an absolute path")
	}
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		raw := make([]byte, 32)
		if _, err = rand.Read(raw); err != nil {
			return "", err
		}
		token := hex.EncodeToString(raw)
		file, cErr := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
		if cErr != nil {
			return "", cErr
		}
		if _, err = file.WriteString(token + "\n"); err == nil {
			err = file.Sync()
		}
		if cErr = file.Close(); err == nil {
			err = cErr
		}
		return token, err
	}
	if err != nil {
		return "", err
	}
	if !info.Mode().IsRegular() {
		return "", invalid("token file must be a regular file with mode 0600: " + path)
	}
	// Check the file that is read, not the path: open it without following a
	// link and take the mode and owner from the open descriptor.
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return "", invalid("token file cannot be opened as a regular file: " + path)
	}
	defer file.Close()
	if info, err = file.Stat(); err != nil {
		return "", err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0o077 != 0 {
		return "", invalid("token file must be a regular file with mode 0600: " + path)
	}
	if stat, ok := info.Sys().(*syscall.Stat_t); !ok || int(stat.Uid) != os.Geteuid() {
		return "", invalid("token file must be owned by this user: " + path)
	}
	raw, err := io.ReadAll(io.LimitReader(file, 4096))
	if err != nil {
		return "", err
	}
	token := strings.TrimSpace(string(raw))
	if !swarmTokenPattern.MatchString(token) {
		return "", invalid("token file does not hold a token of 32 to 256 URL-safe characters: " + path)
	}
	return token, nil
}

func swarmTokenPath(configured string) (string, error) {
	if configured != "" {
		return filepath.Abs(configured)
	}
	directory, err := dataDirectory()
	if err != nil {
		return "", err
	}
	return filepath.Join(directory, swarmTokenFileName), nil
}

func (s *swarmServer) cookieName() string {
	// Cookies on loopback are shared across ports, so the name carries the port.
	return "cairn_swarm_" + strconv.Itoa(s.port)
}

func swarmRandomHex(n int) (string, error) {
	raw := make([]byte, n)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return hex.EncodeToString(raw), nil
}

func (s *swarmServer) issueSession() (string, string, error) {
	id, err := swarmRandomHex(24)
	if err != nil {
		return "", "", err
	}
	csrf, err := swarmRandomHex(16)
	if err != nil {
		return "", "", err
	}
	now := s.now()
	s.mu.Lock()
	defer s.mu.Unlock()
	oldest, oldestAt := "", time.Time{}
	for key, session := range s.sessions {
		if !session.expires.After(now) {
			s.endSessionLocked(key)
			continue
		}
		if oldest == "" || session.expires.Before(oldestAt) {
			oldest, oldestAt = key, session.expires
		}
	}
	if len(s.sessions) >= swarmSessionLimit {
		s.endSessionLocked(oldest)
	}
	s.sessions[id] = swarmBrowserSession{csrf: csrf, expires: now.Add(swarmSessionLifetime), revoked: make(chan struct{})}
	return id, csrf, nil
}

// endSessionLocked forgets a session and ends every stream it holds open.
func (s *swarmServer) endSessionLocked(id string) {
	if session, ok := s.sessions[id]; ok {
		close(session.revoked)
		delete(s.sessions, id)
	}
}

// sessionAlive reports whether a session still stands. A stream asks before
// every read, so an expired session reads nothing more.
func (s *swarmServer) sessionAlive(id string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	session, ok := s.sessions[id]
	if ok && !session.expires.After(s.now()) {
		s.endSessionLocked(id)
		return false
	}
	return ok
}

func (s *swarmServer) session(r *http.Request) (string, swarmBrowserSession, bool) {
	cookie, err := r.Cookie(s.cookieName())
	if err != nil || cookie.Value == "" {
		return "", swarmBrowserSession{}, false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	session, ok := s.sessions[cookie.Value]
	if !ok || !session.expires.After(s.now()) {
		s.endSessionLocked(cookie.Value)
		return "", swarmBrowserSession{}, false
	}
	return cookie.Value, session, true
}

func swarmLoopbackHost(host string) bool {
	return host == "127.0.0.1" || host == "localhost"
}

// checkHostOrigin admits only requests addressed to this loopback listener
// and, when an Origin is sent, only this page's own origin.
func (s *swarmServer) checkHostOrigin(r *http.Request) bool {
	if !s.ownAuthority(r.Host) {
		return false
	}
	if origin := r.Header.Get("Origin"); origin != "" {
		u, err := url.Parse(origin)
		if err != nil || u.Scheme != "http" || u.User != nil || !s.ownAuthority(u.Host) {
			return false
		}
	}
	return true
}

// ownAuthority reports whether host[:port] names this listener. A browser
// leaves the port out when it is the default one for http.
func (s *swarmServer) ownAuthority(authority string) bool {
	host, port := authority, "80"
	if h, p, err := net.SplitHostPort(authority); err == nil {
		host, port = h, p
	} else if strings.Contains(authority, ":") {
		return false
	}
	return swarmLoopbackHost(host) && port == strconv.Itoa(s.port)
}

// swarmSameOrigin applies to every API route. A browser states where a
// request comes from in Sec-Fetch-Site; the stream is a GET with no Origin
// header, so this is the check that keeps another local page from opening it.
// A client that sends no such header is not a browser page.
func swarmSameOrigin(r *http.Request) bool {
	site := r.Header.Get("Sec-Fetch-Site")
	return site == "" || site == "same-origin"
}

func writeSwarmJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeSwarmError(w http.ResponseWriter, err error) {
	code := core.Code(err)
	status := refusal.Classify(code).HTTPStatus
	if status == 0 {
		status = http.StatusInternalServerError
	}
	if code == "" {
		code = "INTERNAL"
	}
	writeSwarmJSON(w, status, map[string]any{"error": err.Error(), "code": code})
}

func swarmRefuse(w http.ResponseWriter, status int, code, message string) {
	writeSwarmJSON(w, status, map[string]any{"error": message, "code": code})
}

func decodeSwarmBody(w http.ResponseWriter, r *http.Request, into any) bool {
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, swarmBodyLimit))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(into); err != nil {
		swarmRefuse(w, http.StatusBadRequest, "INVALID_REQUEST", "invalid JSON request body")
		return false
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		swarmRefuse(w, http.StatusBadRequest, "INVALID_REQUEST", "invalid JSON request body")
		return false
	}
	return true
}

var swarmAssets = map[string][2]string{
	"/":           {"index.html", "text/html; charset=utf-8"},
	"/app.js":     {"app.js", "application/javascript; charset=utf-8"},
	"/styles.css": {"styles.css", "text/css; charset=utf-8"},
}

func (s *swarmServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if !s.checkHostOrigin(r) {
		http.Error(w, "Forbidden: loopback access only", http.StatusForbidden)
		return
	}
	header := w.Header()
	header.Set("X-Content-Type-Options", "nosniff")
	header.Set("X-Frame-Options", "DENY")
	header.Set("Referrer-Policy", "no-referrer")
	header.Set("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

	if asset, ok := swarmAssets[r.URL.Path]; ok {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			header.Set("Allow", "GET, HEAD")
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		data, err := swarmview.FS.ReadFile(asset[0])
		if err != nil {
			http.NotFound(w, r)
			return
		}
		header.Set("Content-Type", asset[1])
		header.Set("Cache-Control", "no-cache")
		_, _ = w.Write(data)
		return
	}
	if !strings.HasPrefix(r.URL.Path, "/api/") {
		http.NotFound(w, r)
		return
	}
	if !swarmSameOrigin(r) {
		swarmRefuse(w, http.StatusForbidden, "AUTHORITY_DENIED", "cross-site request refused")
		return
	}

	switch r.URL.Path {
	case "/api/auth/session":
		if r.Method != http.MethodGet {
			swarmMethod(w, http.MethodGet)
			return
		}
		if _, session, ok := s.session(r); ok {
			writeSwarmJSON(w, http.StatusOK, map[string]any{"authenticated": true, "csrf_token": session.csrf, "collection": s.collection, "plane_url": s.planeURL})
		} else {
			writeSwarmJSON(w, http.StatusOK, map[string]any{"authenticated": false})
		}
		return
	case "/api/auth/login":
		s.handleLogin(w, r)
		return
	}

	id, session, ok := s.session(r)
	if !ok {
		swarmRefuse(w, http.StatusUnauthorized, "UNAUTHENTICATED", "sign in with the swarm view token")
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		// Every state-changing request carries the session's CSRF token.
		presented := r.Header.Get("X-CSRF-Token")
		if subtle.ConstantTimeCompare([]byte(presented), []byte(session.csrf)) != 1 {
			swarmRefuse(w, http.StatusForbidden, "AUTHORITY_DENIED", "invalid csrf token")
			return
		}
	}

	switch {
	case r.URL.Path == "/api/auth/logout":
		if r.Method != http.MethodPost {
			swarmMethod(w, http.MethodPost)
			return
		}
		s.mu.Lock()
		s.endSessionLocked(id)
		s.mu.Unlock()
		http.SetCookie(w, &http.Cookie{Name: s.cookieName(), Value: "", Path: "/", MaxAge: -1, HttpOnly: true, SameSite: http.SameSiteStrictMode})
		writeSwarmJSON(w, http.StatusOK, map[string]any{"ok": true})
	case r.URL.Path == "/api/swarm/landing":
		if r.Method != http.MethodGet {
			swarmMethod(w, http.MethodGet)
			return
		}
		landing, err := s.reads.GetSwarmLanding(r.Context(), s.collection)
		if err != nil {
			writeSwarmError(w, err)
			return
		}
		writeSwarmJSON(w, http.StatusOK, landing)
	case r.URL.Path == "/api/swarm/campaign":
		if r.Method != http.MethodGet {
			swarmMethod(w, http.MethodGet)
			return
		}
		snapshot, err := s.reads.GetSwarmSnapshot(r.Context(), core.SwarmSnapshotRequest{Repo: s.collection, Campaign: r.URL.Query().Get("campaign")})
		if err != nil {
			writeSwarmError(w, err)
			return
		}
		writeSwarmJSON(w, http.StatusOK, snapshot)
	case r.URL.Path == "/api/swarm/panes":
		if r.Method != http.MethodGet {
			swarmMethod(w, http.MethodGet)
			return
		}
		// One observation for a page with no stream open, such as the landing page.
		s.panes.acquire()
		defer s.panes.release()
		s.windows.acquire()
		defer s.windows.release()
		s.panes.first(r.Context(), 5*time.Second)
		s.windows.first(r.Context(), 5*time.Second)
		writeSwarmJSON(w, http.StatusOK, map[string]any{"herdr": s.paneView(s.now()), "window": s.windowView(s.now())})
	case r.URL.Path == "/api/swarm/stream":
		s.handleStream(w, r, id, session)
	case strings.HasPrefix(r.URL.Path, "/api/swarm/action/"), r.URL.Path == "/api/swarm/event":
		s.handleAction(w, r)
	default:
		swarmRefuse(w, http.StatusNotFound, "NOT_FOUND", "no such route")
	}
}

func swarmMethod(w http.ResponseWriter, allow string) {
	w.Header().Set("Allow", allow)
	swarmRefuse(w, http.StatusMethodNotAllowed, "INVALID_REQUEST", "method not allowed")
}

func (s *swarmServer) handleLogin(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		swarmMethod(w, http.MethodPost)
		return
	}
	var body struct {
		Token string `json:"token"`
	}
	if !decodeSwarmBody(w, r, &body) {
		return
	}
	// Both sides are hashed first, so the comparison takes the same time
	// whatever the presented length.
	presented := sha256.Sum256([]byte(strings.TrimSpace(body.Token)))
	if subtle.ConstantTimeCompare(presented[:], s.tokenHash[:]) != 1 {
		swarmRefuse(w, http.StatusUnauthorized, "UNAUTHENTICATED", "token not accepted")
		return
	}
	id, csrf, err := s.issueSession()
	if err != nil {
		swarmRefuse(w, http.StatusInternalServerError, "INTERNAL", "could not create a session")
		return
	}
	http.SetCookie(w, &http.Cookie{Name: s.cookieName(), Value: id, Path: "/", HttpOnly: true, SameSite: http.SameSiteStrictMode})
	writeSwarmJSON(w, http.StatusOK, map[string]any{"ok": true, "csrf_token": csrf})
}

func runSwarmServe(ctx context.Context, store *core.Store, args []string) (any, error) {
	fs := flags("swarm serve")
	port := fs.Int("port", swarmDefaultPort, "loopback port")
	collection := fs.String("collection", "", "collection (repository) to show")
	tokenFile := fs.String("token-file", "", "owner-only token file (default: the Cairn state directory)")
	bindings := fs.String("bindings", "", "coordination bindings directory for the Herdr read (default: the Cairn state directory)")
	planeURL := fs.String("plane-url", "", "address of a tracker item, with {id} for the identifier")
	if err := fs.Parse(args); err != nil {
		return nil, err
	}
	if fs.NArg() != 0 || strings.TrimSpace(*collection) == "" || *port < 0 || *port > 65535 {
		return nil, invalid("usage: cairn swarm serve --collection REPO [--port PORT] [--token-file PATH] [--bindings DIR] [--plane-url TEMPLATE]")
	}
	if *planeURL != "" {
		parsed, pErr := url.Parse(*planeURL)
		if pErr != nil || (parsed.Scheme != "https" && parsed.Scheme != "http") || parsed.Host == "" || !strings.Contains(*planeURL, "{id}") {
			return nil, invalid("--plane-url must be an http or https address that contains {id}")
		}
	}
	// The view never writes a schema, so it needs the store it was built for.
	if err := store.CheckCompatibilityFloor(ctx, 96); err != nil {
		return nil, err
	}
	path, err := swarmTokenPath(*tokenFile)
	if err != nil {
		return nil, err
	}
	token, err := readOrCreateSwarmToken(path)
	if err != nil {
		return nil, err
	}
	directory := *bindings
	if directory == "" {
		home, hErr := dataDirectory()
		if hErr != nil {
			return nil, hErr
		}
		directory = filepath.Join(home, "coordination", "bindings")
	}
	listener, err := net.Listen("tcp", "127.0.0.1:"+strconv.Itoa(*port))
	if err != nil {
		return nil, err
	}
	bound := listener.Addr().(*net.TCPAddr).Port
	server := newSwarmServer(store, *collection, bound, token, newHerdrPaneReader(directory, "/proc"))
	server.planeURL = *planeURL
	// The true window fill comes from the harness transcripts of this host.
	server.windows = newSwarmWindowLoop(newTranscriptWindowReader(directory, "/proc"), 15*time.Second, 60*time.Second)
	fmt.Fprintf(os.Stderr, "cairn swarm view: http://127.0.0.1:%d/ (collection %s; token file %s)\n", bound, *collection, path)
	// No write timeout: the stream stays open. Each stream write sets its own deadline.
	httpServer := &http.Server{Handler: server, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second, BaseContext: func(net.Listener) context.Context { return ctx }}
	failed := make(chan error, 1)
	go func() { failed <- httpServer.Serve(listener) }()
	select {
	case <-ctx.Done():
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(shutdown)
		_ = httpServer.Close()
		return nil, nil
	case err = <-failed:
		if errors.Is(err, http.ErrServerClosed) {
			return nil, nil
		}
		return nil, err
	}
}

// runSwarmURL prints the page address with the token in the URL fragment. A
// fragment is not sent to the server and is not written to a log; the page
// reads it, signs in and removes it from the address bar.
func runSwarmURL(args []string) (any, error) {
	fs := flags("swarm url")
	port := fs.Int("port", swarmDefaultPort, "loopback port")
	tokenFile := fs.String("token-file", "", "owner-only token file (default: the Cairn state directory)")
	if err := fs.Parse(args); err != nil {
		return nil, err
	}
	if fs.NArg() != 0 || *port < 1 || *port > 65535 {
		return nil, invalid("usage: cairn swarm url [--port PORT] [--token-file PATH]")
	}
	path, err := swarmTokenPath(*tokenFile)
	if err != nil {
		return nil, err
	}
	token, err := readOrCreateSwarmToken(path)
	if err != nil {
		return nil, err
	}
	return map[string]any{"url": fmt.Sprintf("http://127.0.0.1:%d/#token=%s", *port, token), "token_file": path}, nil
}
