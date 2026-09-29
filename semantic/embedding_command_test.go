package semantic

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestRealMaximumDocumentEmbeddingCompletes(t *testing.T) {
	path := os.Getenv("CAIRN_EMBEDDING_WORKER")
	if path == "" {
		t.Skip("requires a prepared local embedding worker")
	}
	e, err := EmbeddingCommand(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer e.Close()
	body := strings.Repeat("abc def ghi jkl mno pqr stu vwx yz. ", 2000)[:65536]
	start := time.Now()
	passages, err := e.Document(context.Background(), body)
	if err != nil {
		t.Fatalf("accepted maximum document could not be embedded: %v", err)
	}
	if len(passages) == 0 || len(passages) > 256 {
		t.Fatalf("unbounded or empty passages: %d", len(passages))
	}
	end := 0
	for _, p := range passages {
		if p.Span.Offset > end || p.Span.Length <= 0 || len(p.Vector) != 384 {
			t.Fatalf("document coverage gap or invalid vector: %+v", p.Span)
		}
		end = p.Span.Offset + p.Span.Length
	}
	// Tokenizer offsets can exclude trailing whitespace, but never source text.
	if end > len(body) || strings.TrimSpace(body[end:]) != "" {
		t.Fatalf("document tail omitted: %d of %d bytes", end, len(body))
	}
	t.Logf("embedded %d original bytes in %d passages in %s", len(body), len(passages), time.Since(start))
}

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

func TestEmbeddingDocumentHonorsEarlierCallerDeadline(t *testing.T) {
	e, err := EmbeddingCommand(context.Background(), embeddingFixture(t))
	if err != nil {
		t.Fatal(err)
	}
	defer e.Close()
	ready := filepath.Join(t.TempDir(), "ready")
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	_, err = e.Document(ctx, ready)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("document ignored caller deadline: %v", err)
	}
	b, err := os.ReadFile(ready)
	if err != nil {
		t.Fatal(err)
	}
	pid, err := strconv.Atoi(string(b))
	if err != nil {
		t.Fatal(err)
	}
	if err := syscall.Kill(pid, 0); err != syscall.ESRCH {
		t.Fatalf("document worker survived caller deadline: %v", err)
	}
}
