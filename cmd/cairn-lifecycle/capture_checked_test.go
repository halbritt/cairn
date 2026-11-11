package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestCheckedUnboundCaptureHasNoBareConfigurationRoute(t *testing.T) {
	for _, args := range [][]string{{"memory-capture"}, {"memory-capture", "--config", "/nonexistent.json"}} {
		var out, diagnostic bytes.Buffer
		code := run(args, strings.NewReader("{}"), &out, &diagnostic)
		if code != 1 || out.Len() != 0 || diagnostic.String() != "Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n" {
			t.Fatalf("unchecked capture route: code=%d out=%q err=%q", code, out.String(), diagnostic.String())
		}
	}
}
