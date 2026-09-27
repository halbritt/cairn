//go:build cairn_protocol_min2

package localapi

import (
	"context"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

// A client whose minimum exceeds 1 must fail closed against a server that
// predates it, including one swapped in after a successful preflight.
func TestRaisedClientFailsClosedAgainstLegacyServer(t *testing.T) {
	directory := t.TempDir()
	socket := filepath.Join(directory, "api.sock")
	token := filepath.Join(directory, "token")
	if err := os.WriteFile(token, []byte("synthetic"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	legacy := true
	bodies := make(chan string, 4)
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		bodies <- string(body)
		if legacy {
			// b46a0e1 strict decoding: the unknown guard is refused, no protocol field.
			w.WriteHeader(400)
			_, _ = io.WriteString(w, `{"schema":"cairn.response/1","ok":false,"status":"INVALID_REQUEST","message":"invalid bounded JSON request"}`)
			return
		}
		_, _ = io.WriteString(w, `{"schema":"cairn.response/1","ok":true,"status":"OK","protocol":{"min":1,"current":2},"data":{}}`)
	})}
	go server.Serve(listener)
	defer server.Close()
	c, err := NewClient(socket, token)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	legacy = false
	var out struct{}
	if err = c.Call(context.Background(), "create", map[string]string{"request_id": "r"}, &out); err != nil {
		t.Fatal(err)
	}
	if body := <-bodies; !strings.Contains(body, `"cairn_protocol":2`) {
		t.Fatalf("guard not sent: %s", body)
	}
	legacy = true
	err = c.Call(context.Background(), "create", map[string]string{"request_id": "r"}, &out)
	if core.Code(err) != "PROTOCOL_UNSUPPORTED" || !strings.Contains(err.Error(), "predates protocol 2") {
		t.Fatalf("legacy server not refused: %v", err)
	}
	if body := <-bodies; !strings.Contains(body, `"cairn_protocol":2`) {
		t.Fatalf("guard not sent after swap: %s", body)
	}
}
