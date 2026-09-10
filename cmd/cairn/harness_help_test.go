package main

import (
	"bytes"
	"errors"
	"flag"
	"io"
	"strings"
	"testing"
)

func TestHarnessConfigurationHelp(t *testing.T) {
	for _, tc := range []struct {
		name  string
		write func(io.Writer, []string, string) error
		extra string
	}{
		{"opencode-config", writeOpenCodeConfig, "-memory-only"},
		{"codex-config", writeCodexConfig, "-required"},
		{"claude-config", writeClaudeConfig, "-task"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var out bytes.Buffer
			err := tc.write(&out, []string{"--help"}, "/cairn")
			if !errors.Is(err, flag.ErrHelp) {
				t.Fatalf("help signal lost: %v", err)
			}
			for _, want := range []string{"Usage: cairn " + tc.name, "-socket string", "Cairn Unix socket (required)", "-token-file string", "-tokens int", "default 32000", tc.extra} {
				if !strings.Contains(out.String(), want) {
					t.Fatalf("help omits %q: %s", want, out.String())
				}
			}
			failure := errors.New("help output unavailable")
			if err := tc.write(configFailureWriter{failure}, []string{"-h"}, "/cairn"); !errors.Is(err, failure) {
				t.Fatalf("help output failure lost: %v", err)
			}
			out.Reset()
			if err := tc.write(&out, []string{"--unknown-flag"}, "/cairn"); err == nil || errors.Is(err, flag.ErrHelp) || out.Len() != 0 {
				t.Fatalf("invalid flags became successful help: %v %s", err, out.String())
			}
		})
	}
}
