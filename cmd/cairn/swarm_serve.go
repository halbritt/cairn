package main

import (
	"bytes"
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
	"net/netip"
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
//
// The server has at most two doors. The loopback listener is always there.
// The tailnet door is optional: a Unix socket that Tailscale Serve proxies
// to, for the one tailnet login and the devices that the owner names. The
// server never listens on a network address.

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
	// commits makes "an action of this session may commit" and "this
	// session has ended" exclude each other.
	commits *swarmSessionCommits
	// tailnet names the door that issued the session. A session is good
	// only at its own door.
	tailnet bool
}

// swarmTailnet is the optional tailnet door (docs/swarm-view.md, "Tailnet
// address"). Only this user and root can connect to its socket. That is why
// the login that tailscaled writes on each proxied request can be trusted
// there, and at no other door.
type swarmTailnet struct {
	origin    string // https://host[:port], lower case
	authority string // host[:port] as a browser writes it in Host
	login     string // the one tailnet login that may use the page, lower case
	// clients are the tailnet addresses of the devices that may use the
	// page. The login alone is not enough: tailscaled states the same login
	// for every device of that user, and that includes this host itself,
	// where any local user or container can call the tailnet address.
	clients map[netip.Addr]struct{}
}

// These headers are set by tailscaled on a request that it proxies: the
// tailnet login of the caller, the tailnet address of the caller's device,
// and the host[:port] that the caller asked for. tailscaled replaces values
// that the client sent. For a Unix socket target it sends "Host: localhost",
// so the address that the browser asked for is in the forwarded-host header.
const (
	swarmTailnetLoginHeader  = "Tailscale-User-Login"
	swarmTailnetClientHeader = "X-Forwarded-For"
	swarmTailnetHostHeader   = "X-Forwarded-Host"
	swarmTailnetClientsLimit = 64 << 10
)

var swarmTailnetLoginPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9._%+@-]{0,253}$`)

type swarmDoorKey struct{}

// swarmTailnetConn marks every connection of the tailnet socket.
func swarmTailnetConn(ctx context.Context, _ net.Conn) context.Context {
	return context.WithValue(ctx, swarmDoorKey{}, true)
}

// swarmViaTailnet reports whether a request arrived at the tailnet door.
func swarmViaTailnet(r *http.Request) bool {
	via, _ := r.Context().Value(swarmDoorKey{}).(bool)
	return via
}

// parseSwarmTailnet checks the tailnet address and the login of the serve
// command.
func parseSwarmTailnet(origin, login string) (*swarmTailnet, error) {
	u, err := url.Parse(origin)
	if err != nil || u.Scheme != "https" || u.Opaque != "" || u.User != nil || (u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" {
		return nil, invalid("--tailnet-origin must be an https address with no path, such as https://HOST.TAILNET.ts.net:8087")
	}
	host := strings.ToLower(u.Hostname())
	if host == "" || swarmLoopbackHost(host) || net.ParseIP(host) != nil {
		return nil, invalid("--tailnet-origin must name the host by its tailnet DNS name")
	}
	authority := host
	if port := u.Port(); port != "" && port != "443" {
		if n, pErr := strconv.Atoi(port); pErr != nil || n < 1 || n > 65535 {
			return nil, invalid("--tailnet-origin has an invalid port")
		}
		authority = net.JoinHostPort(host, port)
	} else if port == "" && strings.HasSuffix(u.Host, ":") {
		return nil, invalid("--tailnet-origin has an invalid port")
	}
	login = strings.ToLower(strings.TrimSpace(login))
	if !swarmTailnetLoginPattern.MatchString(login) {
		return nil, invalid("--tailnet-login must be the tailnet login that may open the page")
	}
	return &swarmTailnet{origin: "https://" + authority, authority: authority, login: login}, nil
}

// swarmTailnetAuthority compares host[:port] with the configured one. Names
// compare without case, and ":443" is the same as no port.
func swarmTailnetAuthority(got, want string) bool {
	got = strings.ToLower(got)
	return got == want || (!strings.Contains(want, ":") && got == want+":443")
}

// checkTailnetRequest admits a request at the tailnet door only when it was
// addressed to the page's tailnet address, comes from that page (a GET or
// HEAD may state no origin), and carries, exactly once each and as tailscaled
// wrote them, the configured login and the address of a listed device.
func (s *swarmServer) checkTailnetRequest(r *http.Request) bool {
	door := s.tailnet
	if door == nil {
		return false
	}
	// The address that the browser asked for, as tailscaled states it. Host
	// itself is what tailscaled writes for its target: "localhost" for a
	// socket. The tailnet address is accepted there too, in case a later
	// tailscaled passes it through as it does for a TCP target.
	asked := r.Header.Values(swarmTailnetHostHeader)
	if len(asked) != 1 || !swarmTailnetAuthority(asked[0], door.authority) {
		return false
	}
	if r.Host != "localhost" && !swarmTailnetAuthority(r.Host, door.authority) {
		return false
	}
	if origin := r.Header.Get("Origin"); origin == "" {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			return false
		}
	} else if u, err := url.Parse(origin); err != nil || u.Scheme != "https" || u.Opaque != "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || !swarmTailnetAuthority(u.Host, door.authority) {
		return false
	}
	logins := r.Header.Values(swarmTailnetLoginHeader)
	if len(logins) != 1 || subtle.ConstantTimeCompare([]byte(strings.ToLower(logins[0])), []byte(door.login)) != 1 {
		return false
	}
	// One address, not a list: tailscaled writes the device it accepted the
	// connection from and nothing else.
	clients := r.Header.Values(swarmTailnetClientHeader)
	if len(clients) != 1 {
		return false
	}
	client, err := netip.ParseAddr(clients[0])
	if err != nil {
		return false
	}
	_, listed := door.clients[client.Unmap()]
	return listed
}

// readSwarmTailnetClients reads the list of device addresses that may use the
// tailnet door: one tailnet address per line, with "#" comments. The list
// decides who gets operator access, so the file must belong to this user and
// be closed to writes by anyone else.
func readSwarmTailnetClients(path string) (map[netip.Addr]struct{}, error) {
	if !filepath.IsAbs(path) {
		return nil, invalid("--tailnet-clients must be an absolute path")
	}
	// Without following a link, and non-blocking so that a FIFO cannot stall
	// the open. The mode and the owner come from the open descriptor.
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, invalid("--tailnet-clients cannot be opened as a regular file: " + path)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !info.Mode().IsRegular() || info.Mode().Perm()&0o022 != 0 || !ok || int(stat.Uid) != os.Geteuid() {
		return nil, invalid("--tailnet-clients must be a regular file of this user that no one else can write: " + path)
	}
	raw, err := io.ReadAll(io.LimitReader(file, swarmTailnetClientsLimit+1))
	if err != nil {
		return nil, err
	}
	if len(raw) > swarmTailnetClientsLimit {
		return nil, invalid("--tailnet-clients is too large: " + path)
	}
	clients := map[netip.Addr]struct{}{}
	for number, line := range strings.Split(string(raw), "\n") {
		if cut := strings.IndexByte(line, '#'); cut >= 0 {
			line = line[:cut]
		}
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		address, pErr := netip.ParseAddr(line)
		if pErr != nil || address.Zone() != "" || address.IsLoopback() || address.IsUnspecified() {
			return nil, invalid("--tailnet-clients line " + strconv.Itoa(number+1) + " is not one tailnet address")
		}
		clients[address.Unmap()] = struct{}{}
	}
	if len(clients) == 0 {
		return nil, invalid("--tailnet-clients names no address: " + path)
	}
	return clients, nil
}

// listenSwarmSocket creates the socket of the tailnet door. Its directory
// must belong to this user and be closed to everyone else, so that no other
// user can reach the socket, also before its own mode is set.
func listenSwarmSocket(path string) (net.Listener, error) {
	if !filepath.IsAbs(path) {
		return nil, invalid("--tailnet-socket must be an absolute path")
	}
	directory := filepath.Dir(path)
	info, err := os.Lstat(directory)
	if err != nil {
		return nil, invalid("the directory of --tailnet-socket cannot be read: " + directory)
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !info.IsDir() || info.Mode().Perm()&0o077 != 0 || !ok || int(stat.Uid) != os.Geteuid() {
		return nil, invalid("the directory of --tailnet-socket must be owned by this user with mode 0700: " + directory)
	}
	if existing, lErr := os.Lstat(path); lErr == nil {
		if existing.Mode()&os.ModeSocket == 0 {
			return nil, invalid("--tailnet-socket names a file that is not a socket: " + path)
		}
		// A socket that an earlier run left behind. The loopback port is
		// bound before this, so a server that still runs on that port has
		// already refused this start.
		if err = os.Remove(path); err != nil {
			return nil, err
		}
	} else if !errors.Is(lErr, os.ErrNotExist) {
		return nil, lErr
	}
	listener, err := net.Listen("unix", path)
	if err != nil {
		return nil, err
	}
	if err = os.Chmod(path, 0o600); err != nil {
		_ = listener.Close()
		return nil, err
	}
	return listener, nil
}

// errSwarmSessionEnded refuses a commit for a session that has ended.
var errSwarmSessionEnded = errors.New("the session ended; sign in again")

// swarmSessionCommits counts the commits a session has in flight. A commit
// is admitted only before the session's end is marked, and whoever ends the
// session waits for the admitted ones before saying so. A cancelled context
// alone does not give that order: the cancellation and the commit run on
// different goroutines.
type swarmSessionCommits struct {
	mu       sync.Mutex
	idle     *sync.Cond
	ended    bool
	inFlight int
}

func newSwarmSessionCommits() *swarmSessionCommits {
	c := &swarmSessionCommits{}
	c.idle = sync.NewCond(&c.mu)
	return c
}

// admit lets one commit start. It returns the function that ends it.
func (c *swarmSessionCommits) admit() (func(), error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.ended {
		return nil, errSwarmSessionEnded
	}
	c.inFlight++
	return func() {
		c.mu.Lock()
		if c.inFlight--; c.inFlight == 0 {
			c.idle.Broadcast()
		}
		c.mu.Unlock()
	}, nil
}

// end refuses every later commit.
func (c *swarmSessionCommits) end() {
	if c == nil {
		return
	}
	c.mu.Lock()
	c.ended = true
	c.mu.Unlock()
}

// drain waits for the commits admitted before the end. Their contexts are
// already cancelled, so each returns promptly.
func (c *swarmSessionCommits) drain() {
	if c == nil {
		return
	}
	c.mu.Lock()
	for c.inFlight > 0 {
		c.idle.Wait()
	}
	c.mu.Unlock()
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
	// tailnet is the optional tailnet door, or nil.
	tailnet *swarmTailnet
	tick    time.Duration
	panes   *swarmLoop[swarmPaneObservation]
	windows *swarmLoop[swarmWindowObservation]
	now     func() time.Time
	// beforeCommit, when set by a test, runs as the store asks to commit.
	beforeCommit func()

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
	// Non-blocking, so that a path swapped for a FIFO cannot stall the open.
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
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

func (s *swarmServer) cookieName(tailnet bool) string {
	// Cookies on loopback are shared across ports, so the name carries the port.
	name := "cairn_swarm_" + strconv.Itoa(s.port)
	if tailnet {
		// The tailnet host name is shared by other services too. The prefix
		// makes a browser accept the cookie only from https on this host.
		return "__Host-" + name
	}
	return name
}

// sessionCookie writes the session cookie of one door, or clears it with a
// negative age. The tailnet page is always https, so its cookie is Secure.
func (s *swarmServer) sessionCookie(tailnet bool, value string, maxAge int) *http.Cookie {
	return &http.Cookie{Name: s.cookieName(tailnet), Value: value, Path: "/", MaxAge: maxAge, HttpOnly: true, Secure: tailnet, SameSite: http.SameSiteStrictMode}
}

func swarmRandomHex(n int) (string, error) {
	raw := make([]byte, n)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return hex.EncodeToString(raw), nil
}

// issueSession starts a session at the loopback door.
func (s *swarmServer) issueSession() (string, string, error) {
	return s.issueDoorSession(false)
}

// issueDoorSession starts a session that is good at one door only.
func (s *swarmServer) issueDoorSession(tailnet bool) (string, string, error) {
	id, err := swarmRandomHex(24)
	if err != nil {
		return "", "", err
	}
	csrf, err := swarmRandomHex(16)
	if err != nil {
		return "", "", err
	}
	now := s.now()
	var ended []*swarmSessionCommits
	s.mu.Lock()
	oldest, oldestAt := "", time.Time{}
	for key, session := range s.sessions {
		if !session.expires.After(now) {
			ended = append(ended, s.endSessionLocked(key))
			continue
		}
		if oldest == "" || session.expires.Before(oldestAt) {
			oldest, oldestAt = key, session.expires
		}
	}
	if len(s.sessions) >= swarmSessionLimit {
		ended = append(ended, s.endSessionLocked(oldest))
	}
	s.sessions[id] = swarmBrowserSession{csrf: csrf, expires: now.Add(swarmSessionLifetime), revoked: make(chan struct{}), commits: newSwarmSessionCommits(), tailnet: tailnet}
	s.mu.Unlock()
	// A session this sign-in pushed out has ended once this returns.
	for _, commits := range ended {
		commits.drain()
	}
	return id, csrf, nil
}

// endSessionLocked forgets a session, ends every stream it holds open,
// cancels its requests and refuses its later commits. A caller that reports
// the end to someone drains the returned commits first, outside the lock.
func (s *swarmServer) endSessionLocked(id string) *swarmSessionCommits {
	session, ok := s.sessions[id]
	if !ok {
		return nil
	}
	close(session.revoked)
	delete(s.sessions, id)
	if session.commits != nil {
		session.commits.end()
	}
	return session.commits
}

// signOut ends the session of a request that was admitted with it. When it
// returns, nothing of that session commits any more. It waits on the commits
// of the session the request carried, not on what the session table still
// holds: another sign-out or an eviction may have removed the entry already
// while a commit that was admitted earlier is still under way.
func (s *swarmServer) signOut(id string, session swarmBrowserSession) {
	s.mu.Lock()
	s.endSessionLocked(id)
	s.mu.Unlock()
	session.commits.end()
	session.commits.drain()
}

// admitCommit is the commit gate of one signed-in request: the store asks it
// immediately before a transaction commits.
func (s *swarmServer) admitCommit(id string, commits *swarmSessionCommits) (func(), error) {
	if s.beforeCommit != nil {
		s.beforeCommit()
	}
	// The lifetime is looked at here as well: an expired session ends now.
	if !s.sessionAlive(id) || commits == nil {
		return nil, errSwarmSessionEnded
	}
	return commits.admit()
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

// sessionContext returns a context that ends with the session: at logout,
// when the session limit evicts it, and at its lifetime. Work done for a
// signed-in browser runs under it, so that a store call that is still waiting
// when the session ends is cancelled and commits nothing.
func (s *swarmServer) sessionContext(parent context.Context, session swarmBrowserSession) (context.Context, context.CancelFunc) {
	ctx, cancel := context.WithCancel(parent)
	lifetime := time.NewTimer(time.Until(session.expires))
	go func() {
		defer lifetime.Stop()
		select {
		case <-session.revoked:
		case <-lifetime.C:
		case <-ctx.Done():
		}
		cancel()
	}()
	return ctx, cancel
}

func (s *swarmServer) session(r *http.Request) (string, swarmBrowserSession, bool) {
	tailnet := swarmViaTailnet(r)
	cookie, err := r.Cookie(s.cookieName(tailnet))
	if err != nil || cookie.Value == "" {
		return "", swarmBrowserSession{}, false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	session, ok := s.sessions[cookie.Value]
	if ok && session.tailnet != tailnet {
		// The session of the other door: not accepted here, and left as it is.
		return "", swarmBrowserSession{}, false
	}
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
// and only from this page's own origin. A browser sends Origin with every
// request that can change state, so such a request without one is refused; a
// GET or HEAD may leave it out.
func (s *swarmServer) checkHostOrigin(r *http.Request) bool {
	if !s.ownAuthority(r.Host) {
		return false
	}
	origin := r.Header.Get("Origin")
	if origin == "" {
		return r.Method == http.MethodGet || r.Method == http.MethodHead
	}
	u, err := url.Parse(origin)
	// The page's own origin: the same loopback name and port as the
	// request's Host, not merely another spelling of this listener.
	return err == nil && u.Scheme == "http" && u.User == nil && s.ownAuthority(u.Host) && swarmAuthority(u.Host) == swarmAuthority(r.Host)
}

// swarmAuthority writes host[:port] with the port always present.
func swarmAuthority(authority string) string {
	if _, _, err := net.SplitHostPort(authority); err != nil {
		return authority + ":80"
	}
	return authority
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

// swarmSessionWriter starts an answer only for a session that still stands.
// A read that was under way when the session ended is not sent: its answer
// becomes the request to sign in.
type swarmSessionWriter struct {
	http.ResponseWriter
	alive            func() bool
	started, refused bool
}

func (w *swarmSessionWriter) WriteHeader(status int) {
	if w.started {
		return
	}
	w.started = true
	if w.alive() {
		w.ResponseWriter.WriteHeader(status)
		return
	}
	w.refused = true
	header := w.ResponseWriter.Header()
	for key := range header {
		if key != "X-Content-Type-Options" && key != "X-Frame-Options" && key != "Referrer-Policy" && key != "Content-Security-Policy" {
			header.Del(key)
		}
	}
	header.Set("Content-Type", "application/json; charset=utf-8")
	header.Set("Cache-Control", "no-store")
	w.ResponseWriter.WriteHeader(http.StatusUnauthorized)
	_ = json.NewEncoder(w.ResponseWriter).Encode(map[string]any{"error": "the session ended; sign in again", "code": "UNAUTHENTICATED"})
}

func (w *swarmSessionWriter) Write(p []byte) (int, error) {
	if !w.started {
		w.WriteHeader(http.StatusOK)
	}
	if w.refused {
		return len(p), nil
	}
	return w.ResponseWriter.Write(p)
}

func writeSwarmJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeSwarmError(w http.ResponseWriter, err error) {
	if errors.Is(err, context.Canceled) || errors.Is(err, errSwarmSessionEnded) {
		// The request's context ends with its session. (When the browser went
		// away instead, nobody reads this answer.)
		swarmRefuse(w, http.StatusUnauthorized, "UNAUTHENTICATED", "the session ended before the request completed; sign in again")
		return
	}
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
	tailnet := swarmViaTailnet(r)
	if tailnet {
		if !s.checkTailnetRequest(r) {
			http.Error(w, "Forbidden: the tailnet address serves its own page to the configured login only", http.StatusForbidden)
			return
		}
	} else if !s.checkHostOrigin(r) {
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
		} else if tailnet {
			// The tailnet door has already admitted the login that tailscaled
			// stated, so the page is signed in without the token.
			id, csrf, err := s.issueDoorSession(true)
			if err != nil {
				swarmRefuse(w, http.StatusInternalServerError, "INTERNAL", "could not create a session")
				return
			}
			http.SetCookie(w, s.sessionCookie(true, id, 0))
			writeSwarmJSON(w, http.StatusOK, map[string]any{"authenticated": true, "csrf_token": csrf, "collection": s.collection, "plane_url": s.planeURL})
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

	// The stream reports the end of its session to the page itself; every
	// other signed-in request simply lives no longer than its session.
	stream := r
	bound, stop := s.sessionContext(r.Context(), session)
	defer stop()
	r = r.WithContext(bound)
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		// The whole body arrives before anything acts on it, and the session
		// is looked at again once it has. A request whose session ended while
		// its body was on the way changes nothing.
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, swarmBodyLimit))
		if err != nil {
			swarmRefuse(w, http.StatusBadRequest, "INVALID_REQUEST", "the request body is too large or could not be read")
			return
		}
		r.Body = io.NopCloser(bytes.NewReader(body))
		if !s.sessionAlive(id) {
			swarmRefuse(w, http.StatusUnauthorized, "UNAUTHENTICATED", "the session ended; sign in again")
			return
		}
		// Whatever this request changes commits only while the session
		// stands: the store asks at the commit itself, and the end of the
		// session waits for a commit that was admitted before it.
		commits := session.commits
		r = r.WithContext(core.WithCommitGate(r.Context(), func() (func(), error) { return s.admitCommit(id, commits) }))
	}
	// An answer is started only for a session that still stands. Sign-out
	// answers after its session has ended, and the stream guards each frame.
	plain := w
	w = &swarmSessionWriter{ResponseWriter: w, alive: func() bool { return s.sessionAlive(id) }}

	switch {
	case r.URL.Path == "/api/auth/logout":
		if r.Method != http.MethodPost {
			swarmMethod(w, http.MethodPost)
			return
		}
		s.signOut(id, session)
		http.SetCookie(plain, s.sessionCookie(tailnet, "", -1))
		writeSwarmJSON(plain, http.StatusOK, map[string]any{"ok": true})
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
		// No host reader is started for a session that has ended, and the
		// guarded writer looks again before the observation is sent.
		if !s.sessionAlive(id) {
			swarmRefuse(plain, http.StatusUnauthorized, "UNAUTHENTICATED", "the session ended; sign in again")
			return
		}
		s.panes.acquire()
		defer s.panes.release()
		s.windows.acquire()
		defer s.windows.release()
		s.panes.first(r.Context(), 5*time.Second)
		s.windows.first(r.Context(), 5*time.Second)
		writeSwarmJSON(w, http.StatusOK, map[string]any{"herdr": s.paneView(s.now()), "window": s.windowView(s.now())})
	case r.URL.Path == "/api/swarm/stream":
		s.handleStream(plain, stream, id, session)
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
	tailnet := swarmViaTailnet(r)
	id, csrf, err := s.issueDoorSession(tailnet)
	if err != nil {
		swarmRefuse(w, http.StatusInternalServerError, "INTERNAL", "could not create a session")
		return
	}
	http.SetCookie(w, s.sessionCookie(tailnet, id, 0))
	writeSwarmJSON(w, http.StatusOK, map[string]any{"ok": true, "csrf_token": csrf})
}

func runSwarmServe(ctx context.Context, store *core.Store, args []string) (any, error) {
	fs := flags("swarm serve")
	port := fs.Int("port", swarmDefaultPort, "loopback port")
	collection := fs.String("collection", "", "collection (repository) to show")
	tokenFile := fs.String("token-file", "", "owner-only token file (default: the Cairn state directory)")
	bindings := fs.String("bindings", "", "coordination bindings directory for the Herdr read (default: the Cairn state directory)")
	planeURL := fs.String("plane-url", "", "address of a tracker item, with {id} for the identifier")
	tailnetSocket := fs.String("tailnet-socket", "", "Unix socket for Tailscale Serve to proxy to (with --tailnet-origin and --tailnet-login)")
	tailnetOrigin := fs.String("tailnet-origin", "", "https address of the page on the tailnet")
	tailnetLogin := fs.String("tailnet-login", "", "the tailnet login that may open the page on the tailnet address")
	tailnetClients := fs.String("tailnet-clients", "", "file that lists the tailnet addresses of the devices that may open the page there")
	if err := fs.Parse(args); err != nil {
		return nil, err
	}
	if fs.NArg() != 0 || strings.TrimSpace(*collection) == "" || *port < 0 || *port > 65535 {
		return nil, invalid("usage: cairn swarm serve --collection REPO [--port PORT] [--token-file PATH] [--bindings DIR] [--plane-url TEMPLATE] [--tailnet-socket PATH --tailnet-origin URL --tailnet-login LOGIN --tailnet-clients PATH]")
	}
	var tailnet *swarmTailnet
	if *tailnetSocket != "" || *tailnetOrigin != "" || *tailnetLogin != "" || *tailnetClients != "" {
		if *tailnetSocket == "" || *tailnetOrigin == "" || *tailnetLogin == "" || *tailnetClients == "" {
			return nil, invalid("the tailnet address needs --tailnet-socket, --tailnet-origin, --tailnet-login and --tailnet-clients together")
		}
		door, tErr := parseSwarmTailnet(*tailnetOrigin, *tailnetLogin)
		if tErr != nil {
			return nil, tErr
		}
		if door.clients, tErr = readSwarmTailnetClients(*tailnetClients); tErr != nil {
			return nil, tErr
		}
		tailnet = door
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
	var tailnetListener net.Listener
	if tailnet != nil {
		// After the loopback port: a server that still runs has refused this
		// start by now, so a socket found at the path is a stale one.
		if tailnetListener, err = listenSwarmSocket(*tailnetSocket); err != nil {
			_ = listener.Close()
			return nil, err
		}
	}
	server := newSwarmServer(store, *collection, bound, token, newHerdrPaneReader(directory, "/proc"))
	server.planeURL = *planeURL
	server.tailnet = tailnet
	// The true window fill comes from the harness transcripts of this host.
	server.windows = newSwarmWindowLoop(newTranscriptWindowReader(directory, "/proc"), 15*time.Second, 60*time.Second)
	fmt.Fprintf(os.Stderr, "cairn swarm view: http://127.0.0.1:%d/ (collection %s; token file %s)\n", bound, *collection, path)
	// No write timeout: the stream stays open. Each stream write sets its own deadline.
	httpServer := &http.Server{Handler: server, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second, BaseContext: func(net.Listener) context.Context { return ctx }}
	servers := []*http.Server{httpServer}
	failed := make(chan error, 2)
	go func() { failed <- httpServer.Serve(listener) }()
	if tailnetListener != nil {
		// The same handler behind the second door. Every connection of the
		// socket is marked, and the handler applies the tailnet rules to it.
		tailnetServer := &http.Server{Handler: server, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second, BaseContext: func(net.Listener) context.Context { return ctx }, ConnContext: swarmTailnetConn}
		servers = append(servers, tailnetServer)
		fmt.Fprintf(os.Stderr, "cairn swarm view: tailnet address %s/ (socket %s; %d client addresses)\n", tailnet.origin, *tailnetSocket, len(tailnet.clients))
		go func() { failed <- tailnetServer.Serve(tailnetListener) }()
	}
	stop := func() {
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		for _, each := range servers {
			_ = each.Shutdown(shutdown)
			_ = each.Close()
		}
	}
	select {
	case <-ctx.Done():
		stop()
		return nil, nil
	case err = <-failed:
		// One door failed: close the other too, so that the unit restarts whole.
		stop()
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
