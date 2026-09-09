package semantic

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
)

func streamWorker(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "worker")
	source := `#!/usr/bin/python3
import json,os,subprocess,sys,time
assert not os.environ.get("CAIRN_DATABASE_URL")
for line in sys.stdin.buffer:
 r=json.loads(line); q=r["request"]["query"]
 if q=="crash": sys.exit(5)
 if q.startswith("descendant:"):
  child=subprocess.Popen(["sleep","30"])
  open(q[11:],"w").write(str(child.pid))
  time.sleep(30)
 if q.startswith("sleep:"):
  open(q[6:],"w").write(str(os.getpid()))
  time.sleep(30)
 if q=="oversize": print("x"*70000,flush=True); continue
 if q=="partial": sys.stdout.write('{'); sys.stdout.flush(); sys.exit(0)
 result=dict(model_sha256=str(os.getpid()),algorithm="test/1",scores=[])
 if q=="change-model": result["model_sha256"]="changed"
 response=dict(id=r["id"],result=result)
 if q=="null": response["result"]=None
 if q=="wrong-id": response["id"]="other"
 if q=="unknown": response["surprise"]=True
 if q=="extra": print(json.dumps(response)+"{}",flush=True); continue
 print(json.dumps(response),flush=True)
`
	if err := os.WriteFile(path, []byte(source), 0700); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestStreamReusesWorkerAndClosesIt(t *testing.T) {
	t.Setenv("CAIRN_DATABASE_URL", "must-not-reach-worker")
	rank, closeWorker, err := StreamCommand(context.Background(), streamWorker(t))
	if err != nil {
		t.Fatal(err)
	}
	defer closeWorker()
	a, err := rank(context.Background(), core.SemanticRankRequest{Query: "first"})
	if err != nil {
		t.Fatal(err)
	}
	b, err := rank(context.Background(), core.SemanticRankRequest{Query: "second"})
	if err != nil || a.ModelSHA256 != b.ModelSHA256 {
		t.Fatalf("not reused: %v %v %v", a, b, err)
	}
	closeWorker()
	closeWorker()
	pid, _ := strconv.Atoi(a.ModelSHA256)
	if err := syscall.Kill(pid, 0); err != syscall.ESRCH {
		t.Fatalf("worker survived close: %v", err)
	}
	if _, err := rank(context.Background(), core.SemanticRankRequest{}); err == nil {
		t.Fatal("closed worker accepted request")
	}
}

func TestStreamFailuresDiscardChildAndNextRequestStartsFresh(t *testing.T) {
	for _, query := range []string{"crash", "oversize", "partial", "wrong-id", "unknown", "extra", "change-model", "null"} {
		t.Run(query, func(t *testing.T) {
			rank, stop, err := StreamCommand(context.Background(), streamWorker(t))
			if err != nil {
				t.Fatal(err)
			}
			defer stop()
			first, err := rank(context.Background(), core.SemanticRankRequest{Query: "ok"})
			if err != nil {
				t.Fatal(err)
			}
			if _, err := rank(context.Background(), core.SemanticRankRequest{Query: query}); err == nil {
				t.Fatal("bad result accepted")
			}
			next, err := rank(context.Background(), core.SemanticRankRequest{Query: "ok"})
			if err != nil || next.ModelSHA256 == first.ModelSHA256 {
				t.Fatalf("failed child reused: %v %v", next, err)
			}
		})
	}
}

func TestStreamCancellationBusyAndOwnerShutdown(t *testing.T) {
	for _, ownerCancel := range []bool{false, true} {
		t.Run(fmt.Sprint(ownerCancel), func(t *testing.T) {
			owner, cancelOwner := context.WithCancel(context.Background())
			defer cancelOwner()
			rank, stop, err := StreamCommand(owner, streamWorker(t))
			if err != nil {
				t.Fatal(err)
			}
			defer stop()
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
					t.Fatal("worker never ready")
				}
				time.Sleep(5 * time.Millisecond)
			}
			if _, err := rank(context.Background(), core.SemanticRankRequest{}); err == nil || !strings.Contains(err.Error(), "busy") {
				t.Fatalf("busy caller queued: %v", err)
			}
			if ownerCancel {
				cancelOwner()
			} else {
				cancel()
			}
			select {
			case err := <-done:
				if err == nil {
					t.Fatal("cancelled request succeeded")
				}
			case <-time.After(3 * time.Second):
				t.Fatal("cancel did not finish")
			}
			raw, _ := os.ReadFile(ready)
			pid, _ := strconv.Atoi(string(raw))
			if err := syscall.Kill(pid, 0); err != syscall.ESRCH {
				t.Fatalf("worker survived cancel: %v", err)
			}
			if !ownerCancel {
				if _, err := rank(context.Background(), core.SemanticRankRequest{Query: "ok"}); err != nil {
					t.Fatal(err)
				}
			}
		})
	}
}

func TestStreamReleasesIdleChild(t *testing.T) {
	rank, stop, err := streamCommand(context.Background(), streamWorker(t), 30*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	defer stop()
	first, err := rank(context.Background(), core.SemanticRankRequest{Query: "ok"})
	if err != nil {
		t.Fatal(err)
	}
	pid, _ := strconv.Atoi(first.ModelSHA256)
	deadline := time.Now().Add(3 * time.Second)
	for syscall.Kill(pid, 0) != syscall.ESRCH {
		if time.Now().After(deadline) {
			t.Fatal("idle process retained")
		}
		time.Sleep(5 * time.Millisecond)
	}
	second, err := rank(context.Background(), core.SemanticRankRequest{Query: "ok"})
	if err != nil || second.ModelSHA256 == first.ModelSHA256 {
		t.Fatalf("fresh startup failed: %v %v", second, err)
	}
}

func TestStreamDeadlineTerminatesDescendants(t *testing.T) {
	rank, stop, err := StreamCommand(context.Background(), streamWorker(t))
	if err != nil {
		t.Fatal(err)
	}
	defer stop()
	ready := filepath.Join(t.TempDir(), "descendant")
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if _, err := rank(ctx, core.SemanticRankRequest{Query: "descendant:" + ready}); err == nil {
		t.Fatal("deadline accepted")
	}
	raw, err := os.ReadFile(ready)
	if err != nil {
		t.Fatal(err)
	}
	pid := string(raw)
	// The direct child is reaped by Close/Wait; a killed descendant can briefly
	// remain a zombie owned by init. It must no longer execute or hold our pipes.
	deadline := time.Now().Add(3 * time.Second)
	for {
		stat, err := os.ReadFile("/proc/" + pid + "/stat")
		if os.IsNotExist(err) {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		fields := strings.Fields(string(stat))
		if len(fields) > 2 && fields[2] == "Z" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("descendant survived timeout")
		}
		time.Sleep(5 * time.Millisecond)
	}
	if _, err := rank(context.Background(), core.SemanticRankRequest{Query: "ok"}); err != nil {
		t.Fatal(err)
	}
}

func TestStreamCancellationUnblocksRequestWrite(t *testing.T) {
	ready := filepath.Join(t.TempDir(), "ready")
	path := script(t, "printf '%s' \"$$\" > '"+strings.ReplaceAll(ready, "'", "'\\''")+"'\nexec sleep 30\n")
	rank, stop, err := StreamCommand(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer stop()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		_, err := rank(ctx, core.SemanticRankRequest{Notes: []core.SemanticNote{{Body: strings.Repeat("x", 1024*1024)}}})
		done <- err
	}()
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
	select {
	case err := <-done:
		t.Fatalf("write did not remain blocked: %v", err)
	default:
	}
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancelled write succeeded")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("blocked writer survived cancellation")
	}
	raw, err := os.ReadFile(ready)
	if err != nil {
		t.Fatal(err)
	}
	pid, err := strconv.Atoi(string(raw))
	if err != nil {
		t.Fatal(err)
	}
	if err := syscall.Kill(pid, 0); err != syscall.ESRCH {
		t.Fatalf("blocked child survived: %v", err)
	}
}
