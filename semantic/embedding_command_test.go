package semantic

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"syscall"
	"testing"
	"time"
)

func embeddingFixture(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "worker")
	source := `#!/usr/bin/python3
import json,os,sys,time
assert not os.environ.get("CAIRN_DATABASE_URL")
for line in sys.stdin:
 r=json.loads(line)
 if r["operation"]=="document":
  open(r["text"],"w").write(str(os.getpid()))
  time.sleep(30)
 identity=dict(model_sha256="a"*64,algorithm="fixture/1",dimensions=384)
 if r["text"]=="wrong-model": identity["model_sha256"]="b"*64
 out=dict(id=r["id"],identity=identity)
 if r["operation"]=="query":out["vector"]=[1]+[0]*383
 print(json.dumps(out),flush=True)
`
	if err := os.WriteFile(path, []byte(source), 0700); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestEmbeddingQueriesDoNotWaitForIndexingAndCloseReapsWorkers(t *testing.T) {
	t.Setenv("CAIRN_DATABASE_URL", "must-not-reach-model")
	e, err := EmbeddingCommand(context.Background(), embeddingFixture(t))
	if err != nil {
		t.Fatal(err)
	}
	defer e.Close()
	if _, err = e.Identity(context.Background()); err != nil {
		t.Fatal(err)
	}
	ready := filepath.Join(t.TempDir(), "ready")
	done := make(chan error, 1)
	go func() { _, err := e.Document(context.Background(), ready); done <- err }()
	deadline := time.Now().Add(3 * time.Second)
	for {
		if _, err := os.Stat(ready); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("document worker did not start")
		}
		time.Sleep(5 * time.Millisecond)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	vector, err := e.Query(ctx, "storage")
	if err != nil || len(vector) != 384 {
		t.Fatalf("query waited for indexing: %v", err)
	}
	e.Close()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("closed document completed successfully")
		}
	case <-time.After(time.Second):
		t.Fatal("close left active document call")
	}
	b, err := os.ReadFile(ready)
	if err != nil {
		t.Fatal(err)
	}
	pid, err := strconv.Atoi(string(b))
	if err != nil {
		t.Fatal(err)
	}
	if err = syscall.Kill(pid, 0); err != syscall.ESRCH {
		t.Fatalf("worker survived close: %v", err)
	}
	if _, err = e.Query(context.Background(), "storage"); err == nil {
		t.Fatal("closed embedder accepted query")
	}
}

func TestEmbeddingModelIdentityCannotChangeAcrossRestart(t *testing.T) {
	e, err := EmbeddingCommand(context.Background(), embeddingFixture(t))
	if err != nil {
		t.Fatal(err)
	}
	defer e.Close()
	if _, err = e.Identity(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err = e.Query(context.Background(), "wrong-model"); err == nil {
		t.Fatal("changed model accepted")
	}
	if _, err = e.Query(context.Background(), "storage"); err != nil {
		t.Fatalf("same-model restart failed: %v", err)
	}
}
