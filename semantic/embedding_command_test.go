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

func TestCommandQueryProjectionValidatedAndLegacyAbsentPreserved(t *testing.T) {
	for _, mode := range []string{"valid", "legacy", "missing-bool", "null-bool", "wrong-prefix", "wrong-operation"} {
		t.Run(mode, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "worker")
			script := `#!/usr/bin/python3
import json,sys,hashlib
for line in sys.stdin:
 r=json.loads(line)
 out=dict(id=r['id'],identity=dict(model_sha256='a'*64,algorithm='fixture/1',dimensions=384),vector=[1]+[0]*383)
 text=r['text'];p=dict(method='original-prefix/1',truncated=False,original_tokens=20,embedded_tokens=20,prefix_bytes=len(text.encode()),prefix_sha256=hashlib.sha256(text.encode()).hexdigest())
 mode=` + strconv.Quote(mode) + `
 if mode=='missing-bool':del p['truncated']
 if mode=='null-bool':p['truncated']=None
 if mode=='wrong-prefix':p['prefix_sha256']='b'*64
 if mode!='legacy':out['query_projection']=p
 print(json.dumps(out),flush=True)
`
			if err := os.WriteFile(path, []byte(script), 0700); err != nil {
				t.Fatal(err)
			}
			e, err := EmbeddingCommand(context.Background(), path)
			if err != nil {
				t.Fatal(err)
			}
			defer e.Close()
			if mode == "wrong-operation" {
				if _, err = e.Identity(context.Background()); err == nil {
					t.Fatal("nonquery metadata accepted")
				}
				return
			}
			v, p, err := e.QueryWithProjection(context.Background(), "é漢🙂 original")
			if mode == "valid" || mode == "legacy" {
				if err != nil || len(v) != 384 {
					t.Fatalf("query: %v", err)
				}
				if (p == nil) != (mode == "legacy") {
					t.Fatal("legacy absence synthesized or valid metadata lost")
				}
			} else if err == nil {
				t.Fatal("malformed projection accepted")
			}
		})
	}
}

func TestRealProjectedLongQuery(t *testing.T) {
	path := os.Getenv("CAIRN_EMBEDDING_WORKER")
	if path == "" {
		t.Skip("requires a prepared local embedding worker")
	}
	e, err := EmbeddingCommand(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer e.Close()
	if _, err = e.Identity(context.Background()); err != nil {
		t.Fatal(err)
	}
	query := strings.Repeat("é漢🙂 tokenize pathological constraints! ", 75)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	start := time.Now()
	vector, projection, err := e.QueryWithProjection(ctx, query)
	if err != nil || len(vector) != 384 || projection == nil || !projection.Truncated || projection.OriginalTokens <= 512 || projection.EmbeddedTokens > 512 {
		t.Fatalf("long query failed: projection=%+v error=%v", projection, err)
	}
	t.Logf("one projected query: original_tokens=%d embedded_tokens=%d prefix_bytes=%d duration=%s", projection.OriginalTokens, projection.EmbeddedTokens, projection.PrefixBytes, time.Since(start))
}
