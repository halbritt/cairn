package plane

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestConnectorMutatingCommandRejection(t *testing.T) {
	c := NewConnector()
	ctx := context.Background()

	mutatingCommands := [][]string{
		{"issue", "create", "-p", "TEST", "--name", "bad"},
		{"issue", "update", "TEST-1", "--state", "Done"},
		{"issue", "delete", "TEST-1"},
		{"comment", "add", "TEST-1", "--text", "hello"},
		{"attachment", "upload", "TEST-1", "--file", "test.txt"},
		{"link", "add", "TEST-1", "--url", "https://example.com"},
		{"cycle", "create", "-p", "TEST"},
		{"module", "add-issues", "Mod", "TEST-1"},
	}

	for _, args := range mutatingCommands {
		_, err := c.run(ctx, args)
		if err == nil {
			t.Fatalf("expected error for mutating command %v, got nil", args)
		}
		var plErr *Error
		if errorsAs(err, &plErr) {
			if plErr.Code != "INVALID_REQUEST" {
				t.Fatalf("expected INVALID_REQUEST for %v, got %s (%v)", args, plErr.Code, err)
			}
		}
	}
}

func errorsAs(err error, target **Error) bool {
	if e, ok := err.(*Error); ok {
		*target = e
		return true
	}
	return false
}

func TestConnectorAllowedReadCommands(t *testing.T) {
	c := NewConnector()
	readCommands := [][]string{
		{"project", "get", "32043788-2217-4bf3-9aca-aba636355c0d"},
		{"project", "list"},
		{"state", "list", "-p", "32043788-2217-4bf3-9aca-aba636355c0d"},
		{"issue", "list", "-p", "32043788-2217-4bf3-9aca-aba636355c0d"},
		{"issue", "get", "32043788-2217-4bf3-9aca-aba636355c0d"},
		{"me"},
		{"version"},
	}

	for _, args := range readCommands {
		if err := c.validateReadCommand(args); err != nil {
			t.Fatalf("expected valid read command for %v, got %v", args, err)
		}
	}
}

func TestNormalizeOrigin(t *testing.T) {
	valid := map[string]string{
		"https://plane.harm.org/":                  "https://plane.harm.org",
		"http://127.0.0.1:8000/some/path?foo=bar":  "http://127.0.0.1:8000",
		"https://proximal.tail0ecc2e.ts.net:10000": "https://proximal.tail0ecc2e.ts.net:10000",
	}

	for in, expected := range valid {
		norm, err := NormalizeOrigin(in)
		if err != nil {
			t.Fatalf("failed to normalize %q: %v", in, err)
		}
		if norm != expected {
			t.Fatalf("expected %q, got %q", expected, norm)
		}
	}

	invalid := []string{
		"ftp://plane.example.com",
		"file:///etc/passwd",
		"javascript:alert(1)",
		"",
	}

	for _, in := range invalid {
		_, err := NormalizeOrigin(in)
		if err == nil {
			t.Fatalf("expected error for invalid origin %q, got nil", in)
		}
	}
}

func TestConnectorScriptedFixture(t *testing.T) {
	tempDir := t.TempDir()
	mockPlanePath := filepath.Join(tempDir, "mock_plane")

	// Script that returns JSON based on MOCK_MODE
	script := `#!/usr/bin/env bash
if [[ "$1" == "project" && "$2" == "get" ]]; then
  echo '{"ok":true,"data":{"id":"32043788-2217-4bf3-9aca-aba636355c0d","workspace":"3753821c-0d30-4f2d-955e-1f4972cc5159","identifier":"TEST"},"meta":{}}'
  exit 0
fi

case "$MOCK_MODE" in
  "success")
    echo '{"ok":true,"data":[{"id":"7fb994ec-93e0-4e7f-8cea-1e9d21df2c55","name":"Task 1","priority":"high","state_id":"3753821c-0d30-4f2d-955e-1f4972cc5159","sequence_id":1,"state":{"id":"3753821c-0d30-4f2d-955e-1f4972cc5159","name":"In Progress","group":"started"},"assignees":["a3f369ea-d8dd-42ed-bea3-db653abd632b"],"assignee_details":[{"id":"a3f369ea-d8dd-42ed-bea3-db653abd632b","display_name":"halbritt"}],"updated_at":"2026-11-05T12:00:00Z"}],"meta":{"pagination":{"drained":true,"fetched":1,"total_count":1}}}'
    exit 0
    ;;
  "auth_error")
    echo '{"ok":false,"error":{"code":"AUTH_ERROR","message":"Invalid token","http_status":401}}'
    exit 3
    ;;
  "too_large")
    echo '{"ok":true,"data":[],"meta":{"pagination":{"drained":false,"fetched":100,"total_count":10001}}}'
    exit 0
    ;;
  "streaming_overflow")
    # Stream infinite output exceeding 16 MiB
    python3 -c 'import sys; chunk = "A" * 65536; [sys.stdout.write(chunk) for _ in range(300)]'
    exit 0
    ;;
  "timeout")
    sleep 2
    exit 0
    ;;
  "malformed_uuid")
    echo '{"ok":true,"data":[{"id":"not-a-uuid","name":"Task 1"}],"meta":{"pagination":{"drained":true,"fetched":1,"total_count":1}}}'
    exit 0
    ;;
  *)
    echo '{"ok":false,"error":{"code":"UNKNOWN","message":"Unknown mode"}}'
    exit 2
    ;;
esac
`
	if err := os.WriteFile(mockPlanePath, []byte(script), 0755); err != nil {
		t.Fatal(err)
	}

	c := &Connector{
		BinaryPath: mockPlanePath,
		Timeout:    10 * time.Second,
	}

	projUUID := uuid.MustParse("32043788-2217-4bf3-9aca-aba636355c0d")

	// 1. Success test
	t.Setenv("MOCK_MODE", "success")
	items, err := c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.example.com", "proximal", "TEST")
	if err != nil {
		t.Fatalf("refresh failed: %v", err)
	}
	if len(items) != 1 {
		t.Fatalf("expected 1 item, got %d", len(items))
	}
	if items[0].Identifier != "TEST-1" {
		t.Fatalf("expected identifier TEST-1, got %s", items[0].Identifier)
	}
	if items[0].StateName != "In Progress" || items[0].StateGroup != "started" {
		t.Fatalf("unexpected state: %+v", items[0])
	}
	if items[0].SourceURL != fmt.Sprintf("https://plane.example.com/proximal/projects/%s/issues/%s", projUUID.String(), items[0].PlaneItemUUID.String()) {
		t.Fatalf("unexpected source URL: %s", items[0].SourceURL)
	}

	// 2. Auth error test
	t.Setenv("MOCK_MODE", "auth_error")
	_, err = c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.example.com", "proximal", "TEST")
	if err != ErrAuthRejected {
		t.Fatalf("expected ErrAuthRejected, got %v", err)
	}

	// 3. Project too large test (>10,000 items)
	t.Setenv("MOCK_MODE", "too_large")
	_, err = c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.example.com", "proximal", "TEST")
	if err != ErrTooLarge {
		t.Fatalf("expected ErrTooLarge, got %v", err)
	}

	// 4. Streaming overflow test (PLANE-2)
	t.Setenv("MOCK_MODE", "streaming_overflow")
	_, err = c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.example.com", "proximal", "TEST")
	if err != ErrTooLarge {
		t.Fatalf("expected ErrTooLarge on streaming overflow, got %v", err)
	}

	// 5. Malformed UUID test (PLANE-4)
	t.Setenv("MOCK_MODE", "malformed_uuid")
	_, err = c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.example.com", "proximal", "TEST")
	if err == nil {
		t.Fatal("expected error on malformed UUID row, got nil")
	}

	// 6. Timeout test
	t.Setenv("MOCK_MODE", "timeout")
	c.Timeout = 100 * time.Millisecond
	_, err = c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.example.com", "proximal", "TEST")
	if err == nil {
		t.Fatal("expected timeout error, got nil")
	}
	var plErr *Error
	if errorsAs(err, &plErr) {
		if plErr.Code != "MODEL_TIMEOUT" {
			t.Fatalf("expected MODEL_TIMEOUT, got %s", plErr.Code)
		}
	}
}
