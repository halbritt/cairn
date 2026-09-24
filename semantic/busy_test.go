package semantic

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
)

// holdOpen keeps the worker open for writing, which makes exec fail with
// ETXTBSY exactly as when a concurrently forked child inherits the writer.
func holdOpen(t *testing.T, path string, release time.Duration) {
	t.Helper()
	writer, err := os.OpenFile(path, os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	if release == 0 {
		t.Cleanup(func() { writer.Close() })
		return
	}
	time.AfterFunc(release, func() { writer.Close() })
}

func TestWorkerStartRetriesWhileExecutableIsBusy(t *testing.T) {
	body := "printf '%s' '{\"model_sha256\":\"test\",\"algorithm\":\"test/1\",\"scores\":[]}'\n"
	path := script(t, body)
	rank, err := Command(path)
	if err != nil {
		t.Fatal(err)
	}
	holdOpen(t, path, 40*time.Millisecond)
	result, err := rank(context.Background(), core.SemanticRankRequest{Query: "busy"})
	if err != nil || result.Algorithm != "test/1" {
		t.Fatalf("busy worker was not retried: %+v %v", result, err)
	}

	stream := script(t, `exec /usr/bin/python3 -c '
import json,sys
for line in sys.stdin:
 request=json.loads(line)
 print(json.dumps(dict(id=request["id"],result=dict(model_sha256="test",algorithm="test/1",scores=[]))),flush=True)
'`)
	holdOpen(t, stream, 40*time.Millisecond)
	rankStream, stop, err := StreamCommand(context.Background(), stream)
	if err != nil {
		t.Fatal(err)
	}
	defer stop()
	result, err = rankStream(context.Background(), core.SemanticRankRequest{Query: "busy"})
	if err != nil || result.Algorithm != "test/1" {
		t.Fatalf("busy stream worker was not retried: %+v %v", result, err)
	}
}

func TestWorkerStartReportsPersistentBusyExecutable(t *testing.T) {
	path := script(t, "exit 0\n")
	holdOpen(t, path, 0)
	started := time.Now()
	_, err := startRetryingBusy(context.Background(), func() (*exec.Cmd, error) { return exec.Command(path), nil })
	if !errors.Is(err, syscall.ETXTBSY) {
		t.Fatalf("persistent busy start returned %v", err)
	}
	if elapsed := time.Since(started); elapsed > 2*time.Second {
		t.Fatalf("busy retries were not bounded: %v", elapsed)
	}
}
