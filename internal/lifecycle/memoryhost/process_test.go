package memoryhost_test

import (
	"bytes"
	"context"
	"io"
	"os"
	"path/filepath"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/memoryhost"
)

type brokenInput struct{}

func (brokenInput) Read([]byte) (int, error) { panic("private process input fault") }

func TestPublicPanicIsUnexpectedExitOne(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, []byte(`{"harness":"opencode"}`), 0600); err != nil {
		t.Fatal(err)
	}
	var diagnostic bytes.Buffer
	code := memoryhost.Run(context.Background(), []string{"--config", path}, brokenInput{}, io.Discard, &diagnostic)
	if code != 1 || diagnostic.String() != "Cairn lifecycle diagnostic: phase=input error_class=UnexpectedError\n" {
		t.Fatalf("panic escaped public boundary: code=%d stderr=%q", code, diagnostic.String())
	}
}

type brokenDiagnostic struct{}

func (brokenDiagnostic) Write([]byte) (int, error) { panic("private diagnostic sink fault") }

func TestPublicPanicWithBrokenDiagnosticStillReturnsOne(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, []byte(`{"harness":"opencode"}`), 0600); err != nil {
		t.Fatal(err)
	}
	code := memoryhost.Run(context.Background(), []string{"--config", path}, brokenInput{}, io.Discard, brokenDiagnostic{})
	if code != 1 {
		t.Fatalf("secondary diagnostic fault changed public failure: %d", code)
	}
}
