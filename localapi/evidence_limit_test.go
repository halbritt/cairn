package localapi_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestAuthenticatedEvidenceCaptureReachesStoreLimit(t *testing.T) {
	client, _, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	// Each byte needs six bytes in JSON; the decoded source is within the store's
	// existing limit. This catches limits at both client and server, not just ASCII.
	body := strings.Repeat("\x00", 1048576)
	req := core.EvidenceRequest{RequestID: uuid.NewString(), Repo: repo, Body: body, Source: "selected escaped source fixture", Sensitivity: "shareable"}
	var got core.Evidence
	if err := client.Call(ctx, "evidence", req, &got); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256([]byte(body))
	if got.Digest != hex.EncodeToString(digest[:]) {
		t.Fatal("captured bytes changed")
	}
	var again core.Evidence
	if err := client.Call(ctx, "evidence", req, &again); err != nil || again.ID != got.ID {
		t.Fatalf("capture retry changed identity: %+v %v", again, err)
	}
	s, err := core.Open(ctx, os.Getenv("CAIRN_TEST_DATABASE_URL"), core.Channel{Principal: "host:test", Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	stored, err := s.ReadEvidence(ctx, got.ID)
	if err != nil || stored.Body != body {
		t.Fatalf("full retained source differs: %v", err)
	}

	// Encoding room does not raise the decoded evidence limit or bypass validation.
	req.RequestID, req.Body = uuid.NewString(), strings.Repeat("x", 1048577)
	if err = client.Call(ctx, "evidence", req, &got); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("oversized decoded evidence accepted: %v", err)
	}
	req.Body = "bounded valid retry after refusal"
	if err = client.Call(ctx, "evidence", req, &got); err != nil {
		t.Fatal(err)
	}
	// Other operation envelopes retain the old limit even with a valid note body.
	create := core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: strings.Repeat("<", 65536), ClaimType: "self", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}}
	var ignored json.RawMessage
	if err = client.Call(ctx, "create", create, &ignored); core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "128 KiB") {
		t.Fatalf("non-evidence limit widened: %v", err)
	}
}

func TestEvidenceEnvelopeLimitsAreEnforcedByServerAndClient(t *testing.T) {
	client, _, repo, home := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", filepath.Join(home, "api.sock"))
	}}
	defer transport.CloseIdleConnections()
	raw := &http.Client{Transport: transport, Timeout: 15 * time.Second}
	send := func(operation string, payload any, padding int) map[string]any {
		t.Helper()
		data, err := json.Marshal(payload)
		if err != nil {
			t.Fatal(err)
		}
		request, err := http.NewRequestWithContext(ctx, "POST", "http://cairn/v1/"+operation, io.MultiReader(bytes.NewReader(data), strings.NewReader(strings.Repeat(" ", padding))))
		if err != nil {
			t.Fatal(err)
		}
		request.Header.Set("Authorization", "Bearer synthetic-observer-token")
		response, err := raw.Do(request)
		if err != nil {
			t.Fatal(err)
		}
		defer response.Body.Close()
		var result map[string]any
		if err = json.NewDecoder(response.Body).Decode(&result); err != nil {
			t.Fatal(err)
		}
		if response.StatusCode != 400 || result["status"] != "INVALID_REQUEST" {
			t.Fatalf("server cap not enforced: %d %v", response.StatusCode, result)
		}
		return result
	}
	req := core.EvidenceRequest{RequestID: uuid.NewString(), Repo: repo, Body: "do not commit before envelope validation", Source: "bounded envelope fixture", Sensitivity: "shareable"}
	send("evidence", req, 8*1024*1024)
	req.Body = "valid different intent after refused envelope"
	var captured core.Evidence
	if err := client.Call(ctx, "evidence", req, &captured); err != nil {
		t.Fatalf("oversized envelope reserved mutation intent: %v", err)
	}
	req.Body = strings.Repeat("x", 8*1024*1024)
	if err := client.Call(ctx, "evidence", req, &captured); core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "8192 KiB") {
		t.Fatalf("client cap not enforced: %v", err)
	}

	create := core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: strings.Repeat("<", 65536), ClaimType: "self", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}}
	send("create", create, 0)
	create.Draft.Body = "valid note after refused envelope"
	var record core.Record
	if err := client.Call(ctx, "create", create, &record); err != nil {
		t.Fatalf("non-evidence cap refusal reserved intent: %v", err)
	}
}
