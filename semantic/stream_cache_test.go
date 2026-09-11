package semantic

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
)

func cacheWorker(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "worker")
	source := `#!/usr/bin/python3
import json,os,sys,time
assert os.environ.get("CAIRN_SEMANTIC_STREAM_CACHE")=="1"
for line in sys.stdin:
 r=json.loads(line); q=r["request"]["query"]
 if q=="crash": sys.exit(7)
 if q.startswith("sleep:"):
  open(q[6:],"w").write(str(os.getpid()))
  time.sleep(30)
 result=dict(model_sha256=str(os.getpid()),algorithm="test/1",scores=[dict(record_id=r.get("cache",{}).get("value","cold"),version=1,body_sha256="",score=0)])
 response=dict(id=r["id"],result=result,cache=dict(value=q))
 if q=="large-cache": response["cache"]="x"*(600*1024)
 if q=="large-frame": response["cache"]="x"*(700*1024)
 if q=="large-score": result["scores"][0]["record_id"]="x"*65536
 if q=="wrong-id": response["id"]="other"
 if q=="omit": del response["cache"]
 print(json.dumps(response),flush=True)
`
	if err := os.WriteFile(path, []byte(source), 0700); err != nil {
		t.Fatal(err)
	}
	return path
}

func awaitIdleExit(t *testing.T, result core.SemanticRankResult) {
	t.Helper()
	pid, err := strconv.Atoi(result.ModelSHA256)
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(3 * time.Second)
	for syscall.Kill(pid, 0) != syscall.ESRCH {
		if time.Now().After(deadline) {
			t.Fatal("idle child remained alive")
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func TestStreamRestoresSnapshotAfterActualIdleExit(t *testing.T) {
	rank, stop, err := StreamCommandWithIdleTimeout(context.Background(), cacheWorker(t), 30*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	defer stop()
	first, err := rank(context.Background(), core.SemanticRankRequest{Query: "first"})
	if err != nil {
		t.Fatal(err)
	}
	if first.Scores[0].RecordID != "cold" {
		t.Fatal("new owner inherited a snapshot")
	}
	awaitIdleExit(t, first)
	second, err := rank(context.Background(), core.SemanticRankRequest{Query: "second"})
	if err != nil {
		t.Fatal(err)
	}
	if second.ModelSHA256 == first.ModelSHA256 || second.Scores[0].RecordID != "first" {
		t.Fatalf("snapshot not restored in fresh child: %+v", second)
	}
	awaitIdleExit(t, second)
	third, err := rank(context.Background(), core.SemanticRankRequest{Query: "third"})
	if err != nil || third.Scores[0].RecordID != "second" {
		t.Fatalf("snapshot not replaced: %+v %v", third, err)
	}
}

func TestStreamCacheFailureAndOmissionDiscardState(t *testing.T) {
	for _, query := range []string{"crash", "large-cache", "large-frame", "large-score", "wrong-id", "omit"} {
		t.Run(query, func(t *testing.T) {
			rank, stop, err := StreamCommandWithIdleTimeout(context.Background(), cacheWorker(t), 30*time.Millisecond)
			if err != nil {
				t.Fatal(err)
			}
			defer stop()
			first, err := rank(context.Background(), core.SemanticRankRequest{Query: "saved"})
			if err != nil {
				t.Fatal(err)
			}
			awaitIdleExit(t, first)
			result, err := rank(context.Background(), core.SemanticRankRequest{Query: query})
			if query == "omit" {
				if err != nil {
					t.Fatal(err)
				}
				awaitIdleExit(t, result)
			} else if err == nil {
				t.Fatal("invalid exchange succeeded")
			}
			next, err := rank(context.Background(), core.SemanticRankRequest{Query: "next"})
			if err != nil || next.Scores[0].RecordID != "cold" {
				t.Fatalf("failed/omitted state retained: %+v %v", next, err)
			}
		})
	}
}

func TestStreamCacheCancellationAndOwnerClose(t *testing.T) {
	path := cacheWorker(t)
	rank, stop, err := StreamCommandWithIdleTimeout(context.Background(), path, 30*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	defer stop()
	first, err := rank(context.Background(), core.SemanticRankRequest{Query: "saved"})
	if err != nil {
		t.Fatal(err)
	}
	awaitIdleExit(t, first)
	ready := filepath.Join(t.TempDir(), "ready")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := rank(ctx, core.SemanticRankRequest{Query: "sleep:" + ready}); done <- err }()
	deadline := time.Now().Add(3 * time.Second)
	for {
		if _, err := os.Stat(ready); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("worker not ready")
		}
		time.Sleep(5 * time.Millisecond)
	}
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancelled request succeeded")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("cache restoration survived cancellation")
	}
	next, err := rank(context.Background(), core.SemanticRankRequest{Query: "next"})
	if err != nil || next.Scores[0].RecordID != "cold" {
		t.Fatalf("cancelled state survived: %+v %v", next, err)
	}
	awaitIdleExit(t, next)
	stop()
	fresh, closeFresh, err := StreamCommand(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer closeFresh()
	result, err := fresh(context.Background(), core.SemanticRankRequest{Query: "new-owner"})
	if err != nil || result.Scores[0].RecordID != "cold" {
		t.Fatalf("state crossed owners: %+v %v", result, err)
	}
}
