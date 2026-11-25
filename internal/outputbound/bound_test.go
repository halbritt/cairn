package outputbound

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"unicode/utf8"
)

func TestResolveMaxBytes(t *testing.T) {
	t.Setenv(EnvUnboundedToolOutput, "")
	t.Setenv(EnvToolOutputMaxBytes, "")
	t.Setenv("CAIRN_MCP_OUTPUT_MAX_BYTES", "")

	if got := ResolveMaxBytes(-1, "CAIRN_MCP_OUTPUT_MAX_BYTES"); got != DefaultOutputMaxBytes {
		t.Fatalf("default = %d, want %d", got, DefaultOutputMaxBytes)
	}
	t.Setenv(EnvToolOutputMaxBytes, "9000")
	if got := ResolveMaxBytes(-1, "CAIRN_MCP_OUTPUT_MAX_BYTES"); got != 9000 {
		t.Fatalf("fallback env = %d, want 9000", got)
	}
	t.Setenv("CAIRN_MCP_OUTPUT_MAX_BYTES", "7500")
	if got := ResolveMaxBytes(-1, "CAIRN_MCP_OUTPUT_MAX_BYTES"); got != 7500 {
		t.Fatalf("specific env = %d, want 7500", got)
	}
	if got := ResolveMaxBytes(4000, "CAIRN_MCP_OUTPUT_MAX_BYTES"); got != 4000 {
		t.Fatalf("explicit flag = %d, want 4000", got)
	}
	if got := ResolveMaxBytes(0, "CAIRN_MCP_OUTPUT_MAX_BYTES"); got != 0 {
		t.Fatalf("explicit zero flag = %d, want 0", got)
	}
	t.Setenv("CAIRN_MCP_OUTPUT_MAX_BYTES", "0")
	if got := ResolveMaxBytes(-1, "CAIRN_MCP_OUTPUT_MAX_BYTES"); got != 0 {
		t.Fatalf("zero env = %d, want 0", got)
	}
	t.Setenv("CAIRN_MCP_OUTPUT_MAX_BYTES", "bad-int")
	if got := ResolveMaxBytes(-1, "CAIRN_MCP_OUTPUT_MAX_BYTES"); got != 9000 {
		t.Fatalf("invalid specific env should fall back = %d, want 9000", got)
	}
	for _, val := range []string{"1", "true", "YES"} {
		t.Setenv(EnvUnboundedToolOutput, val)
		if got := ResolveMaxBytes(4000, "CAIRN_MCP_OUTPUT_MAX_BYTES"); got != 0 {
			t.Fatalf("unbounded env %q = %d, want 0", val, got)
		}
	}
}

func TestDefaultSpoolDirNeverUsesTmp(t *testing.T) {
	t.Setenv(EnvToolOutputDir, "")
	t.Setenv("XDG_STATE_HOME", "/var/state/test-user")
	if got := DefaultSpoolDir(); got != "/var/state/test-user/cairn/tool-output" {
		t.Fatalf("xdg spool dir = %q", got)
	}
	t.Setenv(EnvToolOutputDir, "/var/custom/spool")
	if got := DefaultSpoolDir(); got != "/var/custom/spool" {
		t.Fatalf("explicit spool dir = %q", got)
	}
	t.Setenv(EnvToolOutputDir, "")
	t.Setenv("XDG_STATE_HOME", "")
	t.Setenv("HOME", "/home/example")
	if got := DefaultSpoolDir(); got != "/home/example/.local/share/cairn/tool-output" || strings.HasPrefix(got, "/tmp") {
		t.Fatalf("home spool dir = %q", got)
	}
}

func TestExcerptWithSpoolSmallAndOptOut(t *testing.T) {
	spool := t.TempDir()
	raw := []byte("hello world\nexit 0\n")
	code := 0
	res, err := ExcerptWithSpool(raw, 12000, spool, "log", ExecMeta{ExitCode: &code})
	if err != nil {
		t.Fatal(err)
	}
	if res.Truncated || res.Output != string(raw) || res.FullOutputPath != "" || res.TotalBytes != len(raw) || res.ExitCode == nil || *res.ExitCode != 0 {
		t.Fatalf("unexpected small result: %+v", res)
	}
	entries, err := os.ReadDir(spool)
	if err != nil || len(entries) != 0 {
		t.Fatalf("small output spooled unexpectedly: %v %v", entries, err)
	}

	large := []byte(strings.Repeat("line ok\n", 3000))
	res, err = ExcerptWithSpool(large, 0, spool, "log", ExecMeta{ExitCode: &code})
	if err != nil {
		t.Fatal(err)
	}
	if res.Truncated || res.Output != string(large) || res.FullOutputPath != "" {
		t.Fatalf("opt-out 0 truncated large output: truncated=%v len=%d", res.Truncated, len(res.Output))
	}
}

func TestExcerptWithSpoolPreservesHeadDiagnosticsTailUTF8AndFile(t *testing.T) {
	spool := t.TempDir()
	var b strings.Builder
	b.WriteString("HEAD_START: build started\n")
	for i := range 200 {
		b.WriteString("noise line padding 日本語 ")
		b.WriteString(strings.Repeat("x", 40))
		b.WriteByte('\n')
		if i == 90 {
			b.WriteString("ERROR: compiler failed at widget.go:42\n")
			b.WriteString("ERROR: compiler failed at widget.go:42\n") // duplicate should appear once
		}
		if i == 110 {
			b.WriteString("panic: nil pointer dereference in worker\n")
		}
	}
	b.WriteString("TAIL_END: finished with status 2\n")
	raw := []byte(b.String())
	if len(raw) <= 12000 {
		t.Fatalf("fixture too small: %d", len(raw))
	}
	exitCode := 2
	res, err := ExcerptWithSpool(raw, 4000, spool, "log", ExecMeta{ExitCode: &exitCode, ErrorCode: "BUILD_FAILED"})
	if err != nil {
		t.Fatal(err)
	}
	if !res.Truncated || res.TotalBytes != len(raw) || res.OmittedBytes <= 0 {
		t.Fatalf("expected truncated metadata: %+v", res)
	}
	if len(res.Output) > 4000 {
		t.Fatalf("excerpt exceeded maxBytes: %d > 4000", len(res.Output))
	}
	if !utf8.ValidString(res.Output) {
		t.Fatal("excerpt split a multi-byte UTF-8 rune")
	}
	if res.ExitCode == nil || *res.ExitCode != 2 || res.ErrorCode != "BUILD_FAILED" {
		t.Fatalf("lost execution metadata: %+v", res)
	}
	for _, want := range []string{"HEAD_START: build started", "ERROR: compiler failed at widget.go:42", "panic: nil pointer dereference", "TAIL_END: finished with status 2", res.FullOutputPath} {
		if !strings.Contains(res.Output, want) {
			t.Fatalf("excerpt missing %q:\n%s", want, res.Output)
		}
	}
	if strings.Count(res.Output, "ERROR: compiler failed at widget.go:42") != 1 {
		t.Fatalf("duplicate diagnostic line was not deduplicated:\n%s", res.Output)
	}
	info, err := os.Stat(res.FullOutputPath)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("spool file permissions = %o, want 0600", info.Mode().Perm())
	}
	if filepath.Dir(res.FullOutputPath) != spool || !strings.HasSuffix(res.FullOutputPath, ".log") {
		t.Fatalf("unexpected spool path: %q", res.FullOutputPath)
	}
	saved, err := os.ReadFile(res.FullOutputPath)
	if err != nil || string(saved) != string(raw) {
		t.Fatalf("spooled file content mismatch: err=%v len=%d/%d", err, len(saved), len(raw))
	}
}

func TestExcerptWithSpoolHandlesSingleLineAndSymlinkSpoolDir(t *testing.T) {
	spool := t.TempDir()
	singleLine := []byte(`{"error":"` + strings.Repeat("a", 20000) + `"}`)
	res, err := ExcerptWithSpool(singleLine, 2000, spool, "json", ExecMeta{})
	if err != nil {
		t.Fatal(err)
	}
	if !res.Truncated || len(res.Output) > 2000 || !utf8.ValidString(res.Output) {
		t.Fatalf("single-line excerpt exceeded bound: len=%d", len(res.Output))
	}

	outside := t.TempDir()
	link := filepath.Join(t.TempDir(), "spool-link")
	if err := os.Symlink(outside, link); err != nil {
		t.Fatal(err)
	}
	if _, err := SpoolRaw([]byte("secret"), link, "log"); err == nil {
		t.Fatal("accepted symlink spool directory")
	}
}
