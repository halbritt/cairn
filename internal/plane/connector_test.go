package plane

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestConnectorCommandAllowlist(t *testing.T) {
	c := NewConnector()
	ctx := context.Background()

	disallowed := [][]string{
		{"issue", "create", "-p", "TEST", "--name", "bad"},
		{"issue", "update", "TEST-1", "--state", "Done"},
		{"issue", "delete", "TEST-1"},
		{"project", "create", "--name", "X", "--identifier", "X"},
		{"project", "update", "TEST", "--name", "Y"},
		{"project", "delete", "TEST"},
		{"project", "archive", "TEST"},
		{"comment", "add", "TEST-1", "--text", "hello"},
		{"attachment", "upload", "TEST-1", "--file", "test.txt"},
		{"link", "add", "TEST-1", "--url", "https://example.com"},
		{"cycle", "create", "-p", "TEST"},
		{"module", "add-issues", "Mod", "TEST-1"},
		{"--api-key-file", "/etc/shadow", "project", "list"},
		{"--retry-unsafe", "issue", "list"},
		{"issue", "list", "-"},
	}

	for _, args := range disallowed {
		_, err := c.run(ctx, args)
		if err == nil {
			t.Fatalf("expected error for disallowed command %v, got nil", args)
		}
		var plErr *Error
		if !errors.As(err, &plErr) || plErr.Code != "INVALID_REQUEST" {
			t.Fatalf("expected INVALID_REQUEST for %v, got %v", args, err)
		}
	}

	allowed := [][]string{
		{"--config", "/tmp/c.toml", "--instance", "work", "--base-url", "https://plane.harm.org", "--workspace", "harm", "--project", "32043788-2217-4bf3-9aca-aba636355c0d", "--no-resolve", "project", "get", "32043788-2217-4bf3-9aca-aba636355c0d", "--fields", "id,workspace,identifier"},
		{"--base-url=https://plane.harm.org", "--workspace=harm", "issue", "list", "-p", "32043788-2217-4bf3-9aca-aba636355c0d", "--limit", "10001", "--expand", "state,assignees"},
		{"state", "list", "-p", "32043788-2217-4bf3-9aca-aba636355c0d"},
		{"me"},
		{"version"},
	}
	for _, args := range allowed {
		if err := c.validateReadCommand(args); err != nil {
			t.Fatalf("expected valid read command for %v, got %v", args, err)
		}
	}
}

func TestNormalizeOrigin(t *testing.T) {
	valid := map[string]string{
		"https://plane.harm.org/":                  "https://plane.harm.org",
		"HTTPS://Plane.Harm.Org/api/v1":            "https://plane.harm.org",
		"http://127.0.0.1:8000/some/path?foo=bar":  "http://127.0.0.1:8000",
		"https://proximal.tail0ecc2e.ts.net:10000": "https://proximal.tail0ecc2e.ts.net:10000",
	}
	for in, expected := range valid {
		norm, err := NormalizeOrigin(in)
		if err != nil || norm != expected {
			t.Fatalf("NormalizeOrigin(%q) = (%q, %v), want (%q, nil)", in, norm, err, expected)
		}
	}

	invalid := []string{
		"", "   ", "ftp://plane.example.com", "file:///etc/passwd",
		"javascript:alert(1)", "https:///no-host", "https://user:secret@plane.harm.org",
	}
	for _, in := range invalid {
		if _, err := NormalizeOrigin(in); err == nil {
			t.Fatalf("expected error for invalid origin %q, got nil", in)
		}
	}
}

func TestResolveEffectiveConfigPrecedenceAndKeyIsolation(t *testing.T) {
	xdgDir := t.TempDir()
	cfgDir := filepath.Join(xdgDir, "plane-cli")
	if err := os.MkdirAll(cfgDir, 0o755); err != nil {
		t.Fatal(err)
	}
	keyFile := filepath.Join(xdgDir, "secret.key")
	if err := os.WriteFile(keyFile, []byte("secret-v1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	cfgPath := filepath.Join(cfgDir, "config.toml")
	projUUID := "32043788-2217-4bf3-9aca-aba636355c0d"
	wsUUID := uuid.MustParse("3753821c-0d30-4f2d-955e-1f4972cc5159")

	tomlBody := fmt.Sprintf(`
base_url = "https://top.example.com"
workspace = "top-ws"
default_instance = "work"

[instance.work]
base_url = "https://work.example.com/"
workspace = "work-ws"
api_key_file = %q

[instance.Personal]
base_url = "https://plane.harm.org"
workspace = "harm"
api_key = "inline-secret-1"

[project_overrides.%q]
instance = "personal"
workspace = "harm-override"
`, keyFile, strings.ToUpper(projUUID))
	if err := os.WriteFile(cfgPath, []byte(tomlBody), 0o600); err != nil {
		t.Fatal(err)
	}

	envMap := map[string]string{"XDG_CONFIG_HOME": xdgDir}
	c := &Connector{
		BinaryPath: "/usr/bin/true",
		Getenv:     func(k string) string { return envMap[k] },
	}

	// 1. Default instance when project has no override
	effDefault, err := c.ResolveEffectiveConfig(ConfigSelector{Project: "other"})
	if err != nil {
		t.Fatalf("default instance resolve failed: %v", err)
	}
	if effDefault.PlaneOrigin != "https://work.example.com" || effDefault.WorkspaceSlug != "work-ws" || effDefault.Instance != "work" {
		t.Fatalf("unexpected default effective config: %+v", effDefault)
	}

	// 2. Case-insensitive project_overrides routes to Personal and overrides workspace
	effOverride, err := c.ResolveEffectiveConfig(ConfigSelector{Project: projUUID})
	if err != nil {
		t.Fatalf("override resolve failed: %v", err)
	}
	if effOverride.PlaneOrigin != "https://plane.harm.org" || effOverride.WorkspaceSlug != "harm-override" || effOverride.Instance != "Personal" {
		t.Fatalf("unexpected override effective config: %+v", effOverride)
	}

	// 3. Explicit --instance work discards conflicting project_override (written for personal)
	effExplicit, err := c.ResolveEffectiveConfig(ConfigSelector{Instance: "WORK", Project: projUUID})
	if err != nil {
		t.Fatalf("explicit instance resolve failed: %v", err)
	}
	if effExplicit.PlaneOrigin != "https://work.example.com" || effExplicit.WorkspaceSlug != "work-ws" || effExplicit.Instance != "work" {
		t.Fatalf("explicit instance did not discard conflicting override: %+v", effExplicit)
	}

	// 4. Env overrides beat file tiers
	envMap["PLANE_BASE_URL"] = "https://env.example.com:8443/"
	envMap["PLANE_WORKSPACE"] = "env-ws"
	effEnv, err := c.ResolveEffectiveConfig(ConfigSelector{Project: projUUID})
	if err != nil || effEnv.PlaneOrigin != "https://env.example.com:8443" || effEnv.WorkspaceSlug != "env-ws" {
		t.Fatalf("env precedence failed: %+v, err=%v", effEnv, err)
	}

	// 5. Rotating secret key file does NOT alter ComputeConfigRevision, while routing change DOES
	pUUID := uuid.MustParse(projUUID)
	revBefore := ComputeConfigRevision(c.ConnectorIdentity(), effDefault.PlaneOrigin, effDefault.WorkspaceSlug, wsUUID, pUUID)
	if err := os.WriteFile(keyFile, []byte("secret-v2-rotated\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	revAfterKeyRotate := ComputeConfigRevision(c.ConnectorIdentity(), effDefault.PlaneOrigin, effDefault.WorkspaceSlug, wsUUID, pUUID)
	if revBefore != revAfterKeyRotate {
		t.Fatalf("API key rotation must not change routing config revision: %s != %s", revBefore, revAfterKeyRotate)
	}
	revDiffSlug := ComputeConfigRevision(c.ConnectorIdentity(), effDefault.PlaneOrigin, "different-ws", wsUUID, pUUID)
	if revBefore == revDiffSlug {
		t.Fatal("changing workspace slug must change routing config revision")
	}
}

func TestResolveAndVerifyRefreshBinding(t *testing.T) {
	tempDir := t.TempDir()
	cfgPath := filepath.Join(tempDir, "config.toml")
	argsLog := filepath.Join(tempDir, "args.log")
	mockPlane := filepath.Join(tempDir, "mock_plane")

	projUUID := uuid.MustParse("32043788-2217-4bf3-9aca-aba636355c0d")
	wsUUID := uuid.MustParse("3753821c-0d30-4f2d-955e-1f4972cc5159")

	script := `#!/usr/bin/env bash
printf '%s\n' "$*" > "$MOCK_ARGS_LOG"
case "$MOCK_PROJ_MODE" in
  "ok")
    printf '{"ok":true,"data":{"id":"%s","workspace":"%s","identifier":"CAIRN"},"meta":{}}\n' "$MOCK_RET_PROJ" "$MOCK_RET_WS"
    exit 0
    ;;
  "missing_ws")
    printf '{"ok":true,"data":{"id":"%s","identifier":"CAIRN"},"meta":{}}\n' "$MOCK_RET_PROJ"
    exit 0
    ;;
  "missing_ident")
    printf '{"ok":true,"data":{"id":"%s","workspace":"%s"},"meta":{}}\n' "$MOCK_RET_PROJ" "$MOCK_RET_WS"
    exit 0
    ;;
  "auth")
    echo '{"ok":false,"error":{"code":"auth","message":"Invalid API key","http_status":401}}'
    exit 3
    ;;
esac
`
	if err := os.WriteFile(mockPlane, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	writeCfg := func(origin, ws string) {
		body := fmt.Sprintf("[instance.personal]\nbase_url = %q\nworkspace = %q\n", origin, ws)
		if err := os.WriteFile(cfgPath, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	writeCfg("https://plane.harm.org/", "harm")

	t.Setenv("MOCK_ARGS_LOG", argsLog)
	t.Setenv("MOCK_PROJ_MODE", "ok")
	t.Setenv("MOCK_RET_PROJ", projUUID.String())
	t.Setenv("MOCK_RET_WS", wsUUID.String())
	// Set conflicting ambient env vars and verify Connector scrubs them when ConfigPath is used via Getenv isolation
	c := &Connector{
		BinaryPath: mockPlane,
		ConfigPath: cfgPath,
		Timeout:    5 * time.Second,
		Getenv:     func(string) string { return "" },
	}
	t.Setenv("PLANE_BASE_URL", "https://evil-ambient.example.com")

	binding, err := c.ResolveBinding(context.Background(), ConfigSelector{Instance: "personal"}, wsUUID, projUUID)
	if err != nil {
		t.Fatalf("ResolveBinding failed: %v", err)
	}
	if binding.PlaneOrigin != "https://plane.harm.org" || binding.WorkspaceSlug != "harm" || binding.ProjectIdentifier != "CAIRN" {
		t.Fatalf("unexpected binding: %+v", binding)
	}
	loggedArgs, _ := os.ReadFile(argsLog)
	for _, want := range []string{"--instance personal", "--base-url https://plane.harm.org", "--workspace harm", "--project " + projUUID.String(), "--no-resolve"} {
		if !strings.Contains(string(loggedArgs), want) {
			t.Fatalf("expected child argv to contain %q, got %q", want, string(loggedArgs))
		}
	}

	// Arbitrary returned workspace UUID mismatch must return CONFIG_MISMATCH
	otherUUID := uuid.New()
	t.Setenv("MOCK_RET_WS", otherUUID.String())
	if _, err := c.VerifyRefreshBinding(context.Background(), "personal", binding.PlaneOrigin, wsUUID, projUUID, binding.ConfigRevision); err == nil {
		t.Fatal("expected CONFIG_MISMATCH on arbitrary workspace UUID mismatch")
	} else if plErr, ok := err.(*Error); !ok || plErr.Code != "CONFIG_MISMATCH" {
		t.Fatalf("expected CONFIG_MISMATCH, got %v", err)
	}
	t.Setenv("MOCK_RET_WS", wsUUID.String())

	// Missing workspace UUID in project get must NOT fall back to caller UUID
	t.Setenv("MOCK_PROJ_MODE", "missing_ws")
	if _, err := c.ResolveBinding(context.Background(), ConfigSelector{Instance: "personal"}, wsUUID, projUUID); err == nil {
		t.Fatal("expected error when Plane omits workspace UUID")
	}
	t.Setenv("MOCK_PROJ_MODE", "missing_ident")
	if _, err := c.ResolveBinding(context.Background(), ConfigSelector{Instance: "personal"}, wsUUID, projUUID); err == nil {
		t.Fatal("expected error when Plane omits project identifier")
	}
	t.Setenv("MOCK_PROJ_MODE", "ok")

	// Repointing config.toml workspace slug or base_url must fail VerifyRefreshBinding with CONFIG_MISMATCH
	writeCfg("https://plane.harm.org/", "repointed-ws")
	if _, err := c.VerifyRefreshBinding(context.Background(), "personal", binding.PlaneOrigin, wsUUID, projUUID, binding.ConfigRevision); err == nil {
		t.Fatal("expected CONFIG_MISMATCH when workspace slug is repointed")
	}
}

func TestRefreshIssuesStrictEnvelopeAndAssignees(t *testing.T) {
	tempDir := t.TempDir()
	mockPlane := filepath.Join(tempDir, "mock_plane")

	script := `#!/usr/bin/env bash
case "$MOCK_MODE" in
  "expanded_success")
    echo '{"ok":true,"data":[{"id":"7fb994ec-93e0-4e7f-8cea-1e9d21df2c55","name":"Task 1","priority":"high","sequence_id":42,"state":{"id":"3753821c-0d30-4f2d-955e-1f4972cc5159","name":"In Progress","group":"started"},"assignees":[{"id":"a3f369ea-d8dd-42ed-bea3-db653abd632b","display_name":"halbritt"}],"updated_at":"2026-11-05T12:00:00Z"}],"meta":{"pagination":{"drained":true,"fetched":1,"total_count":1,"pages":1}}}'
    exit 0
    ;;
  "empty_null")
    echo '{"ok":true,"data":null,"meta":{"pagination":{"drained":true,"fetched":0,"total_count":0,"pages":1}}}'
    exit 0
    ;;
  "missing_data")
    echo '{"ok":true,"meta":{"pagination":{"drained":true,"fetched":0,"total_count":0}}}'
    exit 0
    ;;
  "missing_meta")
    echo '{"ok":true,"data":[]}'
    exit 0
    ;;
  "undrained")
    echo '{"ok":true,"data":[{"id":"7fb994ec-93e0-4e7f-8cea-1e9d21df2c55","name":"Task 1","sequence_id":1}],"meta":{"pagination":{"drained":false,"fetched":1,"total_count":1}}}'
    exit 0
    ;;
  "fetched_mismatch")
    echo '{"ok":true,"data":[{"id":"7fb994ec-93e0-4e7f-8cea-1e9d21df2c55","name":"Task 1","sequence_id":1}],"meta":{"pagination":{"drained":true,"fetched":2,"total_count":2}}}'
    exit 0
    ;;
  "too_large")
    echo '{"ok":true,"data":[],"meta":{"pagination":{"drained":false,"fetched":100,"total_count":10001}}}'
    exit 0
    ;;
  "auth_error")
    echo '{"ok":false,"error":{"code":"auth","message":"Invalid token","http_status":401}}'
    exit 3
    ;;
  "malformed_uuid")
    echo '{"ok":true,"data":[{"id":"not-a-uuid","name":"Task 1","sequence_id":1}],"meta":{"pagination":{"drained":true,"fetched":1,"total_count":1}}}'
    exit 0
    ;;
esac
`
	if err := os.WriteFile(mockPlane, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	c := &Connector{BinaryPath: mockPlane, Timeout: 5 * time.Second}
	projUUID := uuid.MustParse("32043788-2217-4bf3-9aca-aba636355c0d")

	// 1. Expanded assignees & canonical identifier/URL
	t.Setenv("MOCK_MODE", "expanded_success")
	items, err := c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.harm.org", "harm", "CAIRN")
	if err != nil || len(items) != 1 {
		t.Fatalf("expanded_success failed: len=%d err=%v", len(items), err)
	}
	if items[0].Identifier != "CAIRN-42" {
		t.Fatalf("expected CAIRN-42, got %q", items[0].Identifier)
	}
	if len(items[0].AssigneeIDs) != 1 || items[0].AssigneeIDs[0] != "a3f369ea-d8dd-42ed-bea3-db653abd632b" ||
		len(items[0].AssigneeNames) != 1 || items[0].AssigneeNames[0] != "halbritt" {
		t.Fatalf("expanded assignees lost: %+v", items[0])
	}
	wantURL := fmt.Sprintf("https://plane.harm.org/harm/projects/%s/issues/%s", projUUID, items[0].PlaneItemUUID)
	if items[0].SourceURL != wantURL {
		t.Fatalf("expected source URL %q, got %q", wantURL, items[0].SourceURL)
	}

	// 2. Empty drained project with data:null must succeed with 0 items
	t.Setenv("MOCK_MODE", "empty_null")
	emptyItems, err := c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.harm.org", "harm", "CAIRN")
	if err != nil || len(emptyItems) != 0 {
		t.Fatalf("empty_null failed: items=%v err=%v", emptyItems, err)
	}

	// 3. Partial / malformed envelopes must refuse
	for _, bad := range []string{"missing_data", "missing_meta", "undrained", "fetched_mismatch", "malformed_uuid"} {
		t.Setenv("MOCK_MODE", bad)
		if _, err := c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.harm.org", "harm", "CAIRN"); err == nil {
			t.Fatalf("expected error for mode %q, got nil", bad)
		}
	}

	t.Setenv("MOCK_MODE", "too_large")
	if _, err := c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.harm.org", "harm", "CAIRN"); err != ErrTooLarge {
		t.Fatalf("expected ErrTooLarge, got %v", err)
	}

	t.Setenv("MOCK_MODE", "auth_error")
	if _, err := c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.harm.org", "harm", "CAIRN"); err != ErrAuthRejected {
		t.Fatalf("expected ErrAuthRejected, got %v", err)
	}
}

func TestProcessGroupBoundsAndCancellation(t *testing.T) {
	tempDir := t.TempDir()
	mockPlane := filepath.Join(tempDir, "mock_plane")
	pidFile := filepath.Join(tempDir, "descendant.pid")

	script := `#!/usr/bin/env bash
case "$MOCK_MODE" in
  "stdout_overflow")
    python3 -c 'import sys; chunk = "A" * 65536; [sys.stdout.write(chunk) for _ in range(300)]'
    exit 0
    ;;
  "stderr_overflow")
    python3 -c 'import sys; chunk = "E" * 65536; [sys.stderr.write(chunk) for _ in range(40)]'
    exit 0
    ;;
  "descendant_hang")
    sleep 60 &
    echo $! > "$MOCK_PID_FILE"
    wait
    ;;
esac
`
	if err := os.WriteFile(mockPlane, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("MOCK_PID_FILE", pidFile)
	projUUID := uuid.MustParse("32043788-2217-4bf3-9aca-aba636355c0d")

	// 1. Sustained stdout overflow
	c := &Connector{BinaryPath: mockPlane, Timeout: 10 * time.Second}
	t.Setenv("MOCK_MODE", "stdout_overflow")
	if _, err := c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.harm.org", "harm", "CAIRN"); err != ErrTooLarge {
		t.Fatalf("expected ErrTooLarge on stdout overflow, got %v", err)
	}

	// 2. Sustained stderr overflow
	t.Setenv("MOCK_MODE", "stderr_overflow")
	if _, err := c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.harm.org", "harm", "CAIRN"); err == nil {
		t.Fatal("expected error on stderr overflow, got nil")
	}

	// 3. Timeout terminates background descendant promptly
	_ = os.Remove(pidFile)
	t.Setenv("MOCK_MODE", "descendant_hang")
	cShort := &Connector{BinaryPath: mockPlane, Timeout: 150 * time.Millisecond}
	start := time.Now()
	_, err := cShort.RefreshIssues(context.Background(), "work", projUUID, "https://plane.harm.org", "harm", "CAIRN")
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Fatalf("timeout did not reap descendant holding pipe quickly: %v", elapsed)
	}
	var plErr *Error
	if !errors.As(err, &plErr) || plErr.Code != "MODEL_TIMEOUT" {
		t.Fatalf("expected MODEL_TIMEOUT, got %v", err)
	}
	assertDescendantDead(t, pidFile)

	// 4. Caller context cancellation terminates background descendant promptly
	_ = os.Remove(pidFile)
	cLong := &Connector{BinaryPath: mockPlane, Timeout: 30 * time.Second}
	cancelCtx, cancel := context.WithCancel(context.Background())
	go func() {
		for i := 0; i < 50; i++ {
			if b, err := os.ReadFile(pidFile); err == nil && len(strings.TrimSpace(string(b))) > 0 {
				break
			}
			time.Sleep(10 * time.Millisecond)
		}
		cancel()
	}()
	startCancel := time.Now()
	_, err = cLong.RefreshIssues(cancelCtx, "work", projUUID, "https://plane.harm.org", "harm", "CAIRN")
	if elapsed := time.Since(startCancel); elapsed > 2*time.Second {
		t.Fatalf("caller cancellation hung waiting on descendant pipe: %v", elapsed)
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context.Canceled unwrap on caller cancel, got %v", err)
	}
	assertDescendantDead(t, pidFile)
}

type faultAfterNReader struct {
	r         io.Reader
	remaining int
	err       error
}

func (f *faultAfterNReader) Read(p []byte) (int, error) {
	if f.remaining <= 0 {
		return 0, f.err
	}
	if len(p) > f.remaining {
		p = p[:f.remaining]
	}
	n, err := f.r.Read(p)
	f.remaining -= n
	if f.remaining <= 0 && err == nil {
		return n, f.err
	}
	return n, err
}

func TestPipeOwnershipQuickExitDescendantAndReadFault(t *testing.T) {
	tempDir := t.TempDir()
	mockPlane := filepath.Join(tempDir, "mock_plane_pipe")
	pidFile := filepath.Join(tempDir, "bg_pipe_holder.pid")

	script := `#!/usr/bin/env python3
import json, os, subprocess, sys, uuid

mode = os.environ.get("PIPE_MODE", "quick_burst")
pid_file = os.environ.get("PIPE_PID_FILE", "")
if mode == "burst_with_descendant" and pid_file:
    proc = subprocess.Popen(["sleep", "60"])
    with open(pid_file, "w", encoding="utf-8") as fh:
        fh.write(str(proc.pid))
elif mode == "setsid_pipe_holder" and pid_file:
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    with open(pid_file, "w", encoding="utf-8") as fh:
        fh.write(str(proc.pid))
    sys.stdout.write('{"ok":true,"data":[]')
    sys.stdout.flush()
    os._exit(0)

ns = uuid.UUID("32043788-2217-4bf3-9aca-aba636355c0d")
items = []
for i in range(1, 601):
    items.append({
        "id": str(uuid.uuid5(ns, f"item-{i}")),
        "sequence_id": i,
        "name": f"Quick exit burst item {i} " + ("x" * 780),
        "state": {"id": "741b00f1-1908-4f42-9c83-24d4f1111111", "name": "Todo", "group": "unstarted"},
        "assignees": [],
        "updated_at": "2026-11-05T12:00:00Z",
    })
sys.stdout.write(json.dumps({
    "ok": True,
    "data": items,
    "meta": {"pagination": {"drained": True, "fetched": 600, "total_count": 600, "pages": 6}},
}))
sys.stdout.flush()
os._exit(0)
`
	if err := os.WriteFile(mockPlane, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	projUUID := uuid.MustParse("32043788-2217-4bf3-9aca-aba636355c0d")
	c := &Connector{BinaryPath: mockPlane, Timeout: 10 * time.Second}

	// 1. Repeated quick-exit >512 KB stdout bursts never truncate residual pipe bytes.
	t.Setenv("PIPE_MODE", "quick_burst")
	for iter := 0; iter < 25; iter++ {
		got, err := c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.harm.org", "harm", "CAIRN")
		if err != nil {
			t.Fatalf("iter %d: unexpected quick-exit error: %v", iter, err)
		}
		if len(got) != 600 {
			t.Fatalf("iter %d: expected 600 items, got %d", iter, len(got))
		}
	}

	// 2. Quick-exit burst with background descendant inheriting stdout/stderr completes bounded and reaps child.
	_ = os.Remove(pidFile)
	t.Setenv("PIPE_MODE", "burst_with_descendant")
	t.Setenv("PIPE_PID_FILE", pidFile)
	start := time.Now()
	got, err := c.RefreshIssues(context.Background(), "work", projUUID, "https://plane.harm.org", "harm", "CAIRN")
	if err != nil {
		t.Fatalf("burst_with_descendant failed: %v", err)
	}
	if elapsed := time.Since(start); elapsed > 3*time.Second {
		t.Fatalf("burst_with_descendant hung waiting on inherited pipe: %v", elapsed)
	}
	if len(got) != 600 {
		t.Fatalf("expected 600 items with descendant, got %d", len(got))
	}
	assertDescendantDead(t, pidFile)

	// 3. Non-overflow stdout I/O read failure propagates as STORE_ERROR instead of silent truncation.
	t.Setenv("PIPE_MODE", "quick_burst")
	readBoom := errors.New("simulated mid-stream pipe read fault")
	cFault := &Connector{
		BinaryPath: mockPlane,
		Timeout:    10 * time.Second,
		wrapStdout: func(r io.Reader) io.Reader {
			return &faultAfterNReader{r: r, remaining: 4096, err: readBoom}
		},
	}
	_, err = cFault.RefreshIssues(context.Background(), "work", projUUID, "https://plane.harm.org", "harm", "CAIRN")
	var plErr *Error
	if !errors.As(err, &plErr) || plErr.Code != "STORE_ERROR" || !errors.Is(err, readBoom) {
		t.Fatalf("expected STORE_ERROR wrapping readBoom, got %v", err)
	}

	// 4. Pipe inherited by setsid process outside -pgid still fails bounded via runCtx during wg.Wait.
	setsidPidFile := filepath.Join(tempDir, "setsid_holder.pid")
	t.Cleanup(func() {
		if b, rErr := os.ReadFile(setsidPidFile); rErr == nil {
			if pid, pErr := strconv.Atoi(strings.TrimSpace(string(b))); pErr == nil && pid > 0 {
				_ = syscall.Kill(pid, syscall.SIGKILL)
			}
		}
	})
	t.Setenv("PIPE_MODE", "setsid_pipe_holder")
	t.Setenv("PIPE_PID_FILE", setsidPidFile)
	cSetsid := &Connector{BinaryPath: mockPlane, Timeout: 150 * time.Millisecond}
	startSetsid := time.Now()
	_, err = cSetsid.RefreshIssues(context.Background(), "work", projUUID, "https://plane.harm.org", "harm", "CAIRN")
	if elapsed := time.Since(startSetsid); elapsed > 2*time.Second {
		t.Fatalf("setsid pipe holder hung past runCtx deadline during wg.Wait: %v", elapsed)
	}
	if !errors.As(err, &plErr) || plErr.Code != "MODEL_TIMEOUT" {
		t.Fatalf("expected MODEL_TIMEOUT on setsid pipe holder, got %v", err)
	}
}

func assertDescendantDead(t *testing.T, pidFile string) {
	t.Helper()
	b, err := os.ReadFile(pidFile)
	if err != nil {
		t.Fatalf("descendant pid file was not written: %v", err)
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || pid <= 0 {
		t.Fatalf("invalid descendant pid %q: %v", string(b), err)
	}
	for i := 0; i < 20; i++ {
		if err := syscall.Kill(pid, 0); errors.Is(err, syscall.ESRCH) {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	_ = syscall.Kill(pid, syscall.SIGKILL)
	t.Fatalf("descendant process %d survived group cleanup", pid)
}
