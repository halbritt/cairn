package main

import (
	"context"
	"encoding/json"
	"io"
	"maps"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const (
	swarmTestTailnetOrigin = "https://box.example-tailnet.ts.net:8087"
	swarmTestTailnetHost   = "box.example-tailnet.ts.net:8087"
	swarmTestTailnetLogin  = "owner@example.com"
	swarmTestTailnetCookie = "__Host-cairn_swarm_8087"
	swarmTestLoopbackHost  = "127.0.0.1:8087"
	// Two addresses of one listed device, and addresses that are not listed:
	// another device of the same login, and the serving host itself.
	swarmTestClient      = "100.64.0.10"
	swarmTestClientV6    = "fd7a:115c:a1e0::10"
	swarmTestOtherClient = "100.64.0.99"
	swarmTestServingHost = "100.64.0.1"
)

// newSwarmTailnetTestServer is a swarm server on port 8087 with the tailnet
// door configured. The tests call its handler directly.
func newSwarmTailnetTestServer(t *testing.T) *swarmServer {
	t.Helper()
	door, err := parseSwarmTailnet(swarmTestTailnetOrigin, swarmTestTailnetLogin)
	if err != nil {
		t.Fatal(err)
	}
	server := newSwarmServer(nil, "fixture", 8087, swarmTestToken, swarmNoPanes)
	door.clients = map[netip.Addr]struct{}{netip.MustParseAddr(swarmTestClient): {}, netip.MustParseAddr(swarmTestClientV6): {}}
	server.reads = &swarmScriptedReads{}
	server.tailnet = door
	return server
}

// swarmDoorRequest is one request at one door. tailnet marks it as a request
// that arrived on the tailnet socket, as swarmTailnetConn does for a real
// connection.
type swarmDoorRequest struct {
	// host is the address that the caller asked for. At the loopback door it
	// is the Host header. At the tailnet door tailscaled states it in the
	// forwarded-host header and sends "localhost" as Host. sentHost replaces
	// that Host line; asked replaces the forwarded-host lines; noAsked leaves
	// them out.
	method, path, host string
	tailnet            bool
	sentHost           string
	asked              []string
	noAsked            bool
	header             map[string][]string
	body               string
	cookies            []*http.Cookie
}

func (q swarmDoorRequest) askedHosts(host string) []string {
	if q.noAsked {
		return nil
	}
	if q.asked != nil {
		return q.asked
	}
	return []string{host}
}

func (q swarmDoorRequest) do(server *swarmServer) *httptest.ResponseRecorder {
	method := q.method
	if method == "" {
		method = http.MethodGet
	}
	var body io.Reader
	if q.body != "" {
		body = strings.NewReader(q.body)
	}
	req := httptest.NewRequest(method, "http://"+swarmTestLoopbackHost+q.path, body)
	req.Host = q.host
	if q.tailnet {
		// As tailscaled sends it to a socket: the asked-for address is in
		// the forwarded-host header and Host is "localhost".
		req.Host = "localhost"
		if q.sentHost != "" {
			req.Host = q.sentHost
		}
		for _, asked := range q.askedHosts(q.host) {
			req.Header.Add(swarmTailnetHostHeader, asked)
		}
	}
	for key, values := range q.header {
		for _, value := range values {
			req.Header.Add(key, value)
		}
	}
	for _, cookie := range q.cookies {
		req.AddCookie(cookie)
	}
	if q.tailnet {
		req = req.WithContext(swarmTailnetConn(req.Context(), nil))
	}
	rec := httptest.NewRecorder()
	server.ServeHTTP(rec, req)
	return rec
}

// swarmTailnetHeader is the header of a request that tailscaled proxied for
// the configured login from a listed device, with more entries added.
func swarmTailnetHeader(extra map[string]string) map[string][]string {
	header := swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{swarmTestClient})
	for key, value := range extra {
		header[key] = []string{value}
	}
	return header
}

// swarmTailnetIdentity is the pair of headers that tailscaled writes, with
// the given lines for each. No lines for one of them leaves that header out.
func swarmTailnetIdentity(logins, clients []string) map[string][]string {
	header := map[string][]string{}
	if len(logins) > 0 {
		header[swarmTailnetLoginHeader] = logins
	}
	if len(clients) > 0 {
		header[swarmTailnetClientHeader] = clients
	}
	return header
}

func swarmSessionCount(server *swarmServer) int {
	server.mu.Lock()
	defer server.mu.Unlock()
	return len(server.sessions)
}

func TestSwarmTailnetAddressAndLoginAreChecked(t *testing.T) {
	for origin, want := range map[string]string{
		"https://box.example-tailnet.ts.net:8087":  "box.example-tailnet.ts.net:8087",
		"https://Box.Example-Tailnet.ts.net:8087/": "box.example-tailnet.ts.net:8087",
		"https://box.example-tailnet.ts.net":       "box.example-tailnet.ts.net",
		"https://box.example-tailnet.ts.net:443":   "box.example-tailnet.ts.net",
	} {
		door, err := parseSwarmTailnet(origin, " Owner@Example.com ")
		if err != nil || door.authority != want || door.origin != "https://"+want || door.login != "owner@example.com" {
			t.Fatalf("%s: %+v %v", origin, door, err)
		}
	}
	for _, origin := range []string{
		"", "box.example-tailnet.ts.net:8087", "http://box.example-tailnet.ts.net:8087",
		"https://box.example-tailnet.ts.net:8087/page", "https://box.example-tailnet.ts.net:8087?x=1",
		"https://box.example-tailnet.ts.net:8087/#f", "https://user@box.example-tailnet.ts.net:8087",
		"https://127.0.0.1:8087", "https://localhost:8087", "https://100.64.0.1:8087", "https://[fd7a::1]:8087",
		"https://box.example-tailnet.ts.net:0", "https://box.example-tailnet.ts.net:70000",
		"https://box.example-tailnet.ts.net:", "https://:8087",
	} {
		if door, err := parseSwarmTailnet(origin, swarmTestTailnetLogin); err == nil {
			t.Fatalf("accepted the address %q as %+v", origin, door)
		}
	}
	for _, login := range []string{"", " ", "two words", "owner@example.com,other@example.com", "-owner", "owner\n@example.com", "*"} {
		if door, err := parseSwarmTailnet(swarmTestTailnetOrigin, login); err == nil {
			t.Fatalf("accepted the login %q as %+v", login, door)
		}
	}

	// The four flags come together or not at all: there is no tailnet door
	// without a list of devices. The check runs before the store is used.
	list := filepath.Join(t.TempDir(), "clients")
	if err := os.WriteFile(list, []byte(swarmTestClient+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	all := []string{"--tailnet-socket", "/run/x/t.sock", "--tailnet-origin", swarmTestTailnetOrigin, "--tailnet-login", swarmTestTailnetLogin, "--tailnet-clients", list}
	for leave := 0; leave < len(all); leave += 2 {
		args := append([]string{"--collection", "fixture"}, all[:leave]...)
		args = append(args, all[leave+2:]...)
		if _, err := runSwarmServe(t.Context(), nil, args); err == nil || !strings.Contains(err.Error(), "together") {
			t.Fatalf("without %s: %v", all[leave], err)
		}
		if _, err := runSwarmServe(t.Context(), nil, []string{"--collection", "fixture", all[leave], all[leave+1]}); err == nil || !strings.Contains(err.Error(), "together") {
			t.Fatalf("%s alone: %v", all[leave], err)
		}
	}
	bad := append([]string{"--collection", "fixture"}, all...)
	bad[5] = "http://box.example-tailnet.ts.net:8087"
	if _, err := runSwarmServe(t.Context(), nil, bad); err == nil || !strings.Contains(err.Error(), "--tailnet-origin") {
		t.Fatalf("http tailnet address: %v", err)
	}
	bad = append([]string{"--collection", "fixture"}, all...)
	bad[9] = list + ".missing"
	if _, err := runSwarmServe(t.Context(), nil, bad); err == nil || !strings.Contains(err.Error(), "--tailnet-clients") {
		t.Fatalf("missing device list: %v", err)
	}
}

func TestSwarmTailnetClientList(t *testing.T) {
	directory := t.TempDir()
	write := func(name, content string, mode os.FileMode) string {
		t.Helper()
		path := filepath.Join(directory, name)
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(path, mode); err != nil {
			t.Fatal(err)
		}
		return path
	}
	good := "# devices that may open the page\n\n" + swarmTestClient + "   # desk\n  " + swarmTestClientV6 + "\n" + swarmTestClient + "\n::ffff:100.64.0.11\n"
	clients, err := readSwarmTailnetClients(write("good", good, 0o644))
	if err != nil || len(clients) != 3 {
		t.Fatalf("good list: %v %v", clients, err)
	}
	for _, address := range []string{swarmTestClient, swarmTestClientV6, "100.64.0.11"} {
		if _, ok := clients[netip.MustParseAddr(address)]; !ok {
			t.Fatalf("good list lacks %s: %v", address, clients)
		}
	}

	refused := func(name, path string) {
		t.Helper()
		if clients, rErr := readSwarmTailnetClients(path); rErr == nil {
			t.Fatalf("%s: accepted as %v", name, clients)
		}
	}
	refused("relative path", "clients")
	refused("missing file", filepath.Join(directory, "missing"))
	refused("directory", directory)
	refused("writable by the group", write("group", swarmTestClient+"\n", 0o664))
	refused("writable by others", write("others", swarmTestClient+"\n", 0o646))
	link := filepath.Join(directory, "link")
	if err = os.Symlink(filepath.Join(directory, "good"), link); err != nil {
		t.Fatal(err)
	}
	refused("link to a good list", link)
	for name, content := range map[string]string{
		"empty":             "",
		"comments only":     "# nobody\n\n",
		"two on a line":     swarmTestClient + " " + swarmTestOtherClient + "\n",
		"comma list":        swarmTestClient + "," + swarmTestOtherClient + "\n",
		"network":           "100.64.0.0/10\n",
		"host name":         "box.example-tailnet.ts.net\n",
		"address with port": swarmTestClient + ":443\n",
		"loopback":          "127.0.0.1\n",
		"loopback v6":       "::1\n",
		"unspecified":       "0.0.0.0\n",
		"zone":              swarmTestClientV6 + "%tailscale0\n",
		"good then bad":     swarmTestClient + "\nnot-an-address\n",
	} {
		refused(name, write("case", content, 0o600))
	}
	refused("too large", write("large", strings.Repeat("# filler line of the list\n", 4000)+swarmTestClient+"\n", 0o600))
}

func TestSwarmTailnetDoorAccessChecks(t *testing.T) {
	server := newSwarmTailnetTestServer(t)
	door := func(q swarmDoorRequest) swarmDoorRequest {
		q.tailnet = true
		if q.host == "" {
			q.host = swarmTestTailnetHost
		}
		if q.header == nil {
			q.header = swarmTailnetHeader(nil)
		}
		return q
	}

	// The tailnet address, this page's origin and the one login: all three.
	for name, q := range map[string]swarmDoorRequest{
		"no login":             door(swarmDoorRequest{header: swarmTailnetIdentity(nil, []string{swarmTestClient})}),
		"other login":          door(swarmDoorRequest{header: swarmTailnetIdentity([]string{"other@example.com"}, []string{swarmTestClient})}),
		"empty login":          door(swarmDoorRequest{header: swarmTailnetIdentity([]string{""}, []string{swarmTestClient})}),
		"login twice":          door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin, swarmTestTailnetLogin}, []string{swarmTestClient})}),
		"login and another":    door(swarmDoorRequest{header: swarmTailnetIdentity([]string{"other@example.com", swarmTestTailnetLogin}, []string{swarmTestClient})}),
		"login list":           door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin + ", other@example.com"}, []string{swarmTestClient})}),
		"login prefix":         door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin[:5]}, []string{swarmTestClient})}),
		"no device address":    door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, nil)}),
		"unlisted device":      door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{swarmTestOtherClient})}),
		"the serving host":     door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{swarmTestServingHost})}),
		"loopback as device":   door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{"127.0.0.1"})}),
		"device twice":         door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{swarmTestClient, swarmTestClient})}),
		"device and another":   door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{swarmTestOtherClient, swarmTestClient})}),
		"device list":          door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{swarmTestClient + ", " + swarmTestOtherClient})}),
		"device list reversed": door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{swarmTestOtherClient + ", " + swarmTestClient})}),
		"device with a port":   door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{swarmTestClient + ":443"})}),
		"device in brackets":   door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{"[" + swarmTestClientV6 + "]"})}),
		"device with a zone":   door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{swarmTestClientV6 + "%tailscale0"})}),
		"device with a space":  door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{" " + swarmTestClient})}),
		"not an address":       door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{"box.example-tailnet.ts.net"})}),
		"loopback host":        door(swarmDoorRequest{host: swarmTestLoopbackHost}),
		"other port":           door(swarmDoorRequest{host: "box.example-tailnet.ts.net:9999"}),
		"no port":              door(swarmDoorRequest{host: "box.example-tailnet.ts.net"}),
		"default https port":   door(swarmDoorRequest{host: "box.example-tailnet.ts.net:443"}),
		"lookalike host":       door(swarmDoorRequest{host: "box.example-tailnet.ts.net.example.com:8087"}),
		"no asked-for host":    door(swarmDoorRequest{noAsked: true}),
		"asked-for twice":      door(swarmDoorRequest{asked: []string{swarmTestTailnetHost, swarmTestTailnetHost}}),
		"asked-for and other":  door(swarmDoorRequest{asked: []string{"example.com:8087", swarmTestTailnetHost}}),
		"asked-for list":       door(swarmDoorRequest{asked: []string{swarmTestTailnetHost + ", example.com"}}),
		"asked-for only Host":  door(swarmDoorRequest{noAsked: true, sentHost: swarmTestTailnetHost}),
		"foreign Host line":    door(swarmDoorRequest{sentHost: "example.com:8087"}),
		"loopback Host line":   door(swarmDoorRequest{sentHost: swarmTestLoopbackHost}),
		"localhost with port":  door(swarmDoorRequest{sentHost: "localhost:8087"}),
		"foreign origin":       door(swarmDoorRequest{header: swarmTailnetHeader(map[string]string{"Origin": "https://example.com"})}),
		"sibling service":      door(swarmDoorRequest{header: swarmTailnetHeader(map[string]string{"Origin": "https://box.example-tailnet.ts.net:10000"})}),
		"origin without port":  door(swarmDoorRequest{header: swarmTailnetHeader(map[string]string{"Origin": "https://box.example-tailnet.ts.net"})}),
		"http origin":          door(swarmDoorRequest{header: swarmTailnetHeader(map[string]string{"Origin": "http://box.example-tailnet.ts.net:8087"})}),
		"loopback origin":      door(swarmDoorRequest{header: swarmTailnetHeader(map[string]string{"Origin": "http://" + swarmTestLoopbackHost})}),
		"null origin":          door(swarmDoorRequest{header: swarmTailnetHeader(map[string]string{"Origin": "null"})}),
		"origin with a path":   door(swarmDoorRequest{header: swarmTailnetHeader(map[string]string{"Origin": swarmTestTailnetOrigin + "/"})}),
	} {
		for _, path := range []string{"/", "/app.js", "/api/auth/session", "/api/swarm/landing"} {
			q.path = path
			if rec := q.do(server); rec.Code != http.StatusForbidden || len(rec.Result().Cookies()) != 0 {
				t.Fatalf("%s %s: %d %s", name, path, rec.Code, rec.Body)
			}
		}
	}
	// A request that can change state states its origin.
	for _, method := range []string{http.MethodPost, http.MethodPut, http.MethodDelete} {
		q := door(swarmDoorRequest{method: method, path: "/api/auth/login", body: `{"token":"` + swarmTestToken + `"}`})
		if rec := q.do(server); rec.Code != http.StatusForbidden || len(rec.Result().Cookies()) != 0 {
			t.Fatalf("%s without an origin: %d %s", method, rec.Code, rec.Body)
		}
	}
	if n := swarmSessionCount(server); n != 0 {
		t.Fatalf("refused requests started %d sessions", n)
	}

	// The page is served to the login, however the names are written.
	for name, q := range map[string]swarmDoorRequest{
		"plain":               door(swarmDoorRequest{}),
		"opened from a link":  door(swarmDoorRequest{header: swarmTailnetHeader(map[string]string{"Sec-Fetch-Site": "cross-site"})}),
		"login in upper case": door(swarmDoorRequest{header: swarmTailnetIdentity([]string{strings.ToUpper(swarmTestTailnetLogin)}, []string{swarmTestClient})}),
		"second address":      door(swarmDoorRequest{header: swarmTailnetIdentity([]string{swarmTestTailnetLogin}, []string{swarmTestClientV6})}),
		"host in upper case":  door(swarmDoorRequest{host: strings.ToUpper(swarmTestTailnetHost)}),
		"own origin":          door(swarmDoorRequest{header: swarmTailnetHeader(map[string]string{"Origin": swarmTestTailnetOrigin})}),
		"Host passed through": door(swarmDoorRequest{sentHost: swarmTestTailnetHost}),
	} {
		q.path = "/"
		rec := q.do(server)
		if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "<title>") || rec.Header().Get("X-Frame-Options") != "DENY" || rec.Header().Get("Strict-Transport-Security") != "" {
			t.Fatalf("%s: %d %v", name, rec.Code, rec.Header())
		}
	}
	// Without a port in the address, the browser may write the default one.
	portless := newSwarmTailnetTestServer(t)
	var err error
	if portless.tailnet, err = parseSwarmTailnet("https://box.example-tailnet.ts.net", swarmTestTailnetLogin); err != nil {
		t.Fatal(err)
	}
	portless.tailnet.clients = server.tailnet.clients
	for host, want := range map[string]int{"box.example-tailnet.ts.net": http.StatusOK, "box.example-tailnet.ts.net:443": http.StatusOK, swarmTestTailnetHost: http.StatusForbidden} {
		if rec := door(swarmDoorRequest{host: host, path: "/"}).do(portless); rec.Code != want {
			t.Fatalf("address without a port, host %s: %d", host, rec.Code)
		}
	}

	// Another page is not this page, also for the configured login: every
	// API route refuses it, and it starts no session.
	for _, site := range []string{"same-site", "cross-site", "none"} {
		rec := door(swarmDoorRequest{path: "/api/auth/session", header: swarmTailnetHeader(map[string]string{"Sec-Fetch-Site": site})}).do(server)
		if rec.Code != http.StatusForbidden || swarmErrorCode(rec.Body.Bytes()) != "AUTHORITY_DENIED" || len(rec.Result().Cookies()) != 0 {
			t.Fatalf("Sec-Fetch-Site %s: %d %s", site, rec.Code, rec.Body)
		}
	}
	if n := swarmSessionCount(server); n != 0 {
		t.Fatalf("requests of another page started %d sessions", n)
	}

	// The loopback door does not know the tailnet address and does not trust
	// the headers of tailscaled: any local process could write them there.
	rec := swarmDoorRequest{host: swarmTestTailnetHost, path: "/", header: swarmTailnetHeader(nil)}.do(server)
	if rec.Code != http.StatusForbidden || !strings.Contains(rec.Body.String(), "loopback access only") {
		t.Fatalf("tailnet address at the loopback door: %d %s", rec.Code, rec.Body)
	}
	rec = swarmDoorRequest{host: swarmTestLoopbackHost, path: "/api/auth/session", header: swarmTailnetHeader(nil)}.do(server)
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), `"authenticated":false`) || len(rec.Result().Cookies()) != 0 || swarmSessionCount(server) != 0 {
		t.Fatalf("login header at the loopback door: %d %s", rec.Code, rec.Body)
	}

	// A server without the door refuses whatever arrives marked as tailnet.
	closed := newSwarmServer(nil, "fixture", 8087, swarmTestToken, swarmNoPanes)
	for _, host := range []string{swarmTestTailnetHost, swarmTestLoopbackHost} {
		if rec := door(swarmDoorRequest{host: host, path: "/"}).do(closed); rec.Code != http.StatusForbidden {
			t.Fatalf("no tailnet door, host %s: %d", host, rec.Code)
		}
	}
}

func TestSwarmTailnetDoorSignsInByLogin(t *testing.T) {
	server := newSwarmTailnetTestServer(t)
	type sessionBody struct {
		Authenticated bool   `json:"authenticated"`
		CSRF          string `json:"csrf_token"`
		Collection    string `json:"collection"`
	}
	sameOrigin := swarmTailnetHeader(map[string]string{"Sec-Fetch-Site": "same-origin"})
	tailnet := func(method, path string, header map[string][]string, cookies ...*http.Cookie) *httptest.ResponseRecorder {
		q := swarmDoorRequest{method: method, path: path, host: swarmTestTailnetHost, tailnet: true, header: header, cookies: cookies}
		if method == http.MethodPost {
			q.body = "{}"
		}
		return q.do(server)
	}
	loopback := func(path string, cookies ...*http.Cookie) *httptest.ResponseRecorder {
		return swarmDoorRequest{path: path, host: swarmTestLoopbackHost, cookies: cookies}.do(server)
	}

	// The first question of the page starts the session: no token.
	rec := tailnet(http.MethodGet, "/api/auth/session", sameOrigin)
	var first sessionBody
	if rec.Code != http.StatusOK || json.Unmarshal(rec.Body.Bytes(), &first) != nil || !first.Authenticated || first.CSRF == "" || first.Collection != "fixture" {
		t.Fatalf("sign-in by login: %d %s", rec.Code, rec.Body)
	}
	cookies := rec.Result().Cookies()
	if len(cookies) != 1 {
		t.Fatalf("cookies: %v", cookies)
	}
	cookie := cookies[0]
	if cookie.Name != swarmTestTailnetCookie || cookie.Value == "" || !cookie.Secure || !cookie.HttpOnly || cookie.SameSite != http.SameSiteStrictMode || cookie.Path != "/" || cookie.Domain != "" || cookie.MaxAge != 0 {
		t.Fatalf("tailnet session cookie: %+v", cookie)
	}

	// The session stands at the tailnet door, and is asked for only once.
	rec = tailnet(http.MethodGet, "/api/auth/session", sameOrigin, cookie)
	var again sessionBody
	if rec.Code != http.StatusOK || json.Unmarshal(rec.Body.Bytes(), &again) != nil || !again.Authenticated || again.CSRF != first.CSRF || len(rec.Result().Cookies()) != 0 || swarmSessionCount(server) != 1 {
		t.Fatalf("second question: %d %s, %d sessions", rec.Code, rec.Body, swarmSessionCount(server))
	}
	if rec = tailnet(http.MethodGet, "/api/swarm/landing", sameOrigin, cookie); rec.Code != http.StatusOK {
		t.Fatalf("landing with the tailnet session: %d %s", rec.Code, rec.Body)
	}
	// Without the cookie nothing but the session question answers.
	if rec = tailnet(http.MethodGet, "/api/swarm/landing", sameOrigin); rec.Code != http.StatusUnauthorized {
		t.Fatalf("landing without a session: %d %s", rec.Code, rec.Body)
	}

	// A session is good only at its own door. The loopback door refuses the
	// tailnet session under either cookie name and leaves it standing.
	for _, name := range []string{swarmTestTailnetCookie, "cairn_swarm_8087"} {
		if rec = loopback("/api/swarm/landing", &http.Cookie{Name: name, Value: cookie.Value}); rec.Code != http.StatusUnauthorized || swarmErrorCode(rec.Body.Bytes()) != "UNAUTHENTICATED" {
			t.Fatalf("tailnet session at the loopback door as %s: %d %s", name, rec.Code, rec.Body)
		}
	}
	if rec = tailnet(http.MethodGet, "/api/swarm/landing", sameOrigin, cookie); rec.Code != http.StatusOK {
		t.Fatalf("tailnet session after the loopback attempts: %d %s", rec.Code, rec.Body)
	}
	// And the tailnet door refuses a loopback session, which stays good at
	// the loopback door.
	local, _, err := server.issueSession()
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{swarmTestTailnetCookie, "cairn_swarm_8087"} {
		if rec = tailnet(http.MethodGet, "/api/swarm/landing", sameOrigin, &http.Cookie{Name: name, Value: local}); rec.Code != http.StatusUnauthorized {
			t.Fatalf("loopback session at the tailnet door as %s: %d %s", name, rec.Code, rec.Body)
		}
	}
	if rec = loopback("/api/swarm/landing", &http.Cookie{Name: "cairn_swarm_8087", Value: local}); rec.Code != http.StatusOK {
		t.Fatalf("loopback session at its own door: %d %s", rec.Code, rec.Body)
	}

	// A request that changes state carries the CSRF token of the session.
	change := swarmTailnetHeader(map[string]string{"Sec-Fetch-Site": "same-origin", "Origin": swarmTestTailnetOrigin, "Content-Type": "application/json"})
	if rec = tailnet(http.MethodPost, "/api/auth/logout", change, cookie); rec.Code != http.StatusForbidden || swarmErrorCode(rec.Body.Bytes()) != "AUTHORITY_DENIED" {
		t.Fatalf("sign-out without the csrf token: %d %s", rec.Code, rec.Body)
	}
	withToken := maps.Clone(change)
	withToken["X-Csrf-Token"] = []string{first.CSRF}
	rec = tailnet(http.MethodPost, "/api/auth/logout", withToken, cookie)
	cleared := rec.Result().Cookies()
	if rec.Code != http.StatusOK || len(cleared) != 1 || cleared[0].Name != swarmTestTailnetCookie || cleared[0].Value != "" || cleared[0].MaxAge >= 0 || !cleared[0].Secure {
		t.Fatalf("sign-out: %d %s %+v", rec.Code, rec.Body, cleared)
	}
	if rec = tailnet(http.MethodGet, "/api/swarm/landing", sameOrigin, cookie); rec.Code != http.StatusUnauthorized {
		t.Fatalf("landing after sign-out: %d %s", rec.Code, rec.Body)
	}

	// The token signs in here too, and the session belongs to this door.
	q := swarmDoorRequest{method: http.MethodPost, path: "/api/auth/login", host: swarmTestTailnetHost, tailnet: true, header: change, body: `{"token":"` + swarmTestToken + `"}`}
	rec = q.do(server)
	issued := rec.Result().Cookies()
	if rec.Code != http.StatusOK || len(issued) != 1 || issued[0].Name != swarmTestTailnetCookie || !issued[0].Secure {
		t.Fatalf("token sign-in at the tailnet door: %d %s %+v", rec.Code, rec.Body, issued)
	}
	if rec = loopback("/api/swarm/landing", &http.Cookie{Name: "cairn_swarm_8087", Value: issued[0].Value}); rec.Code != http.StatusUnauthorized {
		t.Fatalf("token session of the tailnet door at the loopback door: %d", rec.Code)
	}
}

func TestSwarmTailnetSocket(t *testing.T) {
	// A Unix socket address has little room, so the directory name is short.
	directory, err := os.MkdirTemp("", "csw")
	if err == nil && len(directory) > 80 {
		_ = os.RemoveAll(directory)
		directory, err = os.MkdirTemp("/tmp", "csw")
	}
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(directory) })
	path := filepath.Join(directory, "t.sock")

	refused := func(name, socket string) {
		t.Helper()
		if listener, lErr := listenSwarmSocket(socket); lErr == nil {
			_ = listener.Close()
			t.Fatalf("%s: a socket was created at %s", name, socket)
		}
	}
	refused("relative path", "t.sock")
	refused("missing directory", filepath.Join(directory, "missing", "t.sock"))
	if err = os.Chmod(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	refused("directory open to the group", path)
	if err = os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	link := directory + "-link"
	if err = os.Symlink(directory, link); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Remove(link) })
	refused("directory behind a link", filepath.Join(link, "t.sock"))
	if err = os.WriteFile(path, []byte("not a socket"), 0o600); err != nil {
		t.Fatal(err)
	}
	refused("a file that is not a socket", path)
	if raw, rErr := os.ReadFile(path); rErr != nil || string(raw) != "not a socket" {
		t.Fatalf("the file at the socket path was changed: %q %v", raw, rErr)
	}
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}

	// A socket that an earlier run left behind is replaced.
	stale, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	stale.(*net.UnixListener).SetUnlinkOnClose(false)
	if err = stale.Close(); err != nil {
		t.Fatal(err)
	}
	listener, err := listenSwarmSocket(path)
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Lstat(path)
	if err != nil || info.Mode()&os.ModeSocket == 0 || info.Mode().Perm() != 0o600 {
		t.Fatalf("socket mode: %v %v", info.Mode(), err)
	}

	// A connection of the socket is at the tailnet door: the login header
	// decides, and the tailnet address is the only one served.
	server := newSwarmTailnetTestServer(t)
	httpServer := &http.Server{Handler: server, ConnContext: swarmTailnetConn}
	go func() { _ = httpServer.Serve(listener) }()
	t.Cleanup(func() { _ = httpServer.Close() })
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		var dialer net.Dialer
		return dialer.DialContext(ctx, "unix", path)
	}}
	t.Cleanup(transport.CloseIdleConnections)
	client := &http.Client{Transport: transport}
	ask := func(host, login, device string) (*http.Response, string) {
		t.Helper()
		req, rErr := http.NewRequest(http.MethodGet, "http://localhost/api/auth/session", nil)
		if rErr != nil {
			t.Fatal(rErr)
		}
		req.Header.Set(swarmTailnetHostHeader, host)
		if login != "" {
			req.Header.Set(swarmTailnetLoginHeader, login)
		}
		if device != "" {
			req.Header.Set(swarmTailnetClientHeader, device)
		}
		resp, rErr := client.Do(req)
		if rErr != nil {
			t.Fatal(rErr)
		}
		defer resp.Body.Close()
		raw, rErr := io.ReadAll(resp.Body)
		if rErr != nil {
			t.Fatal(rErr)
		}
		return resp, string(raw)
	}
	if resp, raw := ask(swarmTestTailnetHost, "", swarmTestClient); resp.StatusCode != http.StatusForbidden {
		t.Fatalf("socket without a login: %d %s", resp.StatusCode, raw)
	}
	if resp, raw := ask(swarmTestTailnetHost, swarmTestTailnetLogin, ""); resp.StatusCode != http.StatusForbidden {
		t.Fatalf("socket without a device address: %d %s", resp.StatusCode, raw)
	}
	if resp, raw := ask(swarmTestTailnetHost, swarmTestTailnetLogin, swarmTestServingHost); resp.StatusCode != http.StatusForbidden {
		t.Fatalf("socket with the serving host as the device: %d %s", resp.StatusCode, raw)
	}
	if resp, raw := ask(swarmTestLoopbackHost, swarmTestTailnetLogin, swarmTestClient); resp.StatusCode != http.StatusForbidden {
		t.Fatalf("loopback address on the socket: %d %s", resp.StatusCode, raw)
	}
	resp, raw := ask(swarmTestTailnetHost, swarmTestTailnetLogin, swarmTestClient)
	if resp.StatusCode != http.StatusOK || !strings.Contains(raw, `"authenticated":true`) || len(resp.Cookies()) != 1 || !resp.Cookies()[0].Secure {
		t.Fatalf("socket with the login: %d %s %v", resp.StatusCode, raw, resp.Cookies())
	}
}
