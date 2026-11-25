package main

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/outputbound"
)

func TestExecBoundedPassThroughAndTruncateWithDiagnostics(t *testing.T) {
	spool := t.TempDir()
	var out, errOut bytes.Buffer

	// 1. Small output passes through untouched with exit code 0.
	code, err := execBoundedCommand(context.Background(), []string{"--spool-dir", spool, "--", "/bin/sh", "-c", "printf 'ok-out\\n'; printf 'ok-err\\n' >&2"}, strings.NewReader(""), &out, &errOut)
	if err != nil || code != 0 || out.String() != "ok-out\n" || errOut.String() != "ok-err\n" {
		t.Fatalf("small pass-through failed: code=%d err=%v out=%q errOut=%q", code, err, out.String(), errOut.String())
	}

	// 2. Oversized failing command (>12 KB) preserves exit code, head, middle error diagnostic, tail, and 0600 spool file.
	out.Reset()
	errOut.Reset()
	script := `printf 'HEAD_BEGIN\n'; i=0; while [ $i -lt 320 ]; do printf 'noise line %03d padding text to inflate bytes beyond limit\n' "$i"; if [ "$i" -eq 150 ]; then printf 'FATAL error: build failed at target.go:99\n' >&2; fi; i=$((i+1)); done; printf 'TAIL_FINAL\n'; exit 2`
	code, err = execBoundedCommand(context.Background(), []string{"--spool-dir", spool, "--output-max-bytes", "4000", "--", "/bin/sh", "-c", script}, strings.NewReader(""), &out, &errOut)
	if err != nil || code != 2 {
		t.Fatalf("failing bounded command: code=%d err=%v", code, err)
	}
	combined := out.String() + errOut.String()
	if len(combined) > 4000 {
		t.Fatalf("bounded output exceeded limit: %d > 4000", len(combined))
	}
	for _, want := range []string{"exit_code=2", "HEAD_BEGIN", "FATAL error: build failed at target.go:99", "TAIL_FINAL", spool} {
		if !strings.Contains(combined, want) {
			t.Fatalf("bounded output missing %q:\n%s", want, combined)
		}
	}
	entries, err := os.ReadDir(spool)
	if err != nil || len(entries) != 1 {
		t.Fatalf("expected 1 spool file, got %v %v", entries, err)
	}
	spoolPath := filepath.Join(spool, entries[0].Name())
	info, err := os.Stat(spoolPath)
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("spool permissions = %v %v", info, err)
	}
	full, err := os.ReadFile(spoolPath)
	if err != nil || len(full) <= 12000 || !strings.Contains(string(full), "noise line 150") {
		t.Fatalf("spooled file incomplete: len=%d err=%v", len(full), err)
	}

	// 3. Opt-out via --output-max-bytes 0 and CAIRN_UNBOUNDED_TOOL_OUTPUT=1 passes full output through.
	out.Reset()
	errOut.Reset()
	code, err = execBoundedCommand(context.Background(), []string{"--spool-dir", spool, "--output-max-bytes", "0", "--", "/bin/sh", "-c", script}, strings.NewReader(""), &out, &errOut)
	if err != nil || code != 2 || len(out.String())+len(errOut.String()) != len(full) {
		t.Fatalf("opt-out 0 failed: code=%d err=%v len=%d/%d", code, err, len(out.String())+len(errOut.String()), len(full))
	}

	t.Setenv(outputbound.EnvUnboundedToolOutput, "1")
	out.Reset()
	errOut.Reset()
	code, err = execBoundedCommand(context.Background(), []string{"--spool-dir", spool, "--", "/bin/sh", "-c", script}, strings.NewReader(""), &out, &errOut)
	if err != nil || code != 2 || len(out.String())+len(errOut.String()) != len(full) {
		t.Fatalf("env opt-out failed: code=%d err=%v len=%d/%d", code, err, len(out.String())+len(errOut.String()), len(full))
	}
}
