package semantic

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
)

// Protocol fixtures exercise Go launch/ownership; real Rust/model tests are
// separate and never use these empty library files as model evidence.
func rustTestConfig(t *testing.T, mode RustMode, executable string) RustWorkerConfig {
	t.Helper()
	dir := filepath.Join(t.TempDir(), "paths with spaces")
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	files := make([]string, 4)
	for i := range files {
		files[i] = filepath.Join(dir, fmt.Sprintf("library-%d.so", i))
		if err := os.WriteFile(files[i], nil, 0600); err != nil {
			t.Fatal(err)
		}
	}
	return RustWorkerConfig{Mode: mode, Executable: executable, ModelDir: dir,
		ORTRuntime: files[0], OpenBLAS: files[1], GFortran: files[2], Quadmath: files[3], SourceRoot: dir}
}

func TestRustSupervisorLaunchContractAndLegacyExecutableCompatibility(t *testing.T) {
	for _, mode := range []RustMode{RustRank, RustStream, RustEmbed} {
		for _, legacy := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/legacy=%t", mode, legacy), func(t *testing.T) {
				for _, name := range []string{"LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT", "CAIRN_DATABASE_URL", "CAIRN_SECRET"} {
					t.Setenv(name, "must-not-reach-worker")
				}
				path := filepath.Join(t.TempDir(), "protocol worker")
				config := rustTestConfig(t, mode, path)
				args := []string{}
				telemetry := ""
				if !legacy {
					args = []string{"--model-dir", config.ModelDir, "--ort-runtime", config.ORTRuntime,
						"--openblas", config.OpenBLAS, "--gfortran", config.GFortran,
						"--quadmath", config.Quadmath, "--source-root", config.SourceRoot}
					if mode == RustStream {
						args = append(args, "--stream")
					}
					telemetry = "1"
				}
				raw, _ := json.Marshal(args)
				program := fmt.Sprintf(`#!/usr/bin/python3
import hashlib,json,os,sys
assert sys.argv[1:] == json.loads(%q), sys.argv[1:]
assert os.environ.get("ORT_DISABLE_TELEMETRY", "") == %q
assert os.environ["PATH"] == "/usr/bin:/bin"
assert all(name not in os.environ for name in ("LD_LIBRARY_PATH","LD_PRELOAD","LD_AUDIT","CAIRN_DATABASE_URL","CAIRN_SECRET"))
mode = %q
identity = dict(model_sha256="a"*64, algorithm="fixture/1", dimensions=384)
result = dict(model_sha256="a"*64, algorithm="fixture/1", scores=[])
if mode == "rank":
    json.load(sys.stdin)
    print(json.dumps(result))
else:
    for line in sys.stdin:
        r = json.loads(line)
        if mode == "stream":
            reply = dict(id=r["id"],result=result)
        else:
            reply = dict(id=r["id"],identity=identity)
            if r["operation"] == "query":
                text=r["text"].encode()
                reply.update(vector=[1.0]+[0.0]*383,query_projection=dict(method="original-prefix/1",truncated=False,original_tokens=11,embedded_tokens=11,prefix_bytes=len(text),prefix_sha256=hashlib.sha256(text).hexdigest()))
            if r["operation"] == "document":
                reply["passages"]=[dict(span=dict(offset=0,length=len(r["text"].encode())),vector=[1.0]+[0.0]*383)]
        print(json.dumps(reply),flush=True)
`, string(raw), telemetry, mode)
				if err := os.WriteFile(path, []byte(program), 0700); err != nil {
					t.Fatal(err)
				}
				ctx := context.Background()
				if mode == RustEmbed {
					var model *CommandEmbedder
					var err error
					if legacy {
						model, err = EmbeddingCommand(ctx, path)
					} else {
						model, err = RustEmbeddingCommand(ctx, config)
					}
					if err != nil {
						t.Fatal(err)
					}
					defer model.Close()
					identity, err := model.Identity(ctx)
					if err != nil || identity.Dimensions != 384 {
						t.Fatalf("identity: %+v %v", identity, err)
					}
					vector, projection, err := model.QueryWithProjection(ctx, "abc")
					if err != nil || len(vector) != 384 || projection == nil {
						t.Fatalf("query: %v", err)
					}
					passages, err := model.Document(ctx, "héllo🙂")
					if err != nil || len(passages) != 1 || passages[0].Span.Length != len("héllo🙂") {
						t.Fatalf("document: %+v %v", passages, err)
					}
					return
				}
				var rank core.SemanticRanker
				var closeWorker func()
				var err error
				if mode == RustRank {
					if legacy {
						rank, err = Command(path)
					} else {
						rank, err = RustCommand(config)
					}
				} else {
					if legacy {
						rank, closeWorker, err = StreamCommand(ctx, path)
					} else {
						rank, closeWorker, err = RustStreamCommand(ctx, config, time.Second)
					}
				}
				if err != nil {
					t.Fatal(err)
				}
				if closeWorker != nil {
					defer closeWorker()
				}
				for range 2 {
					result, err := rank(ctx, core.SemanticRankRequest{Query: "contract"})
					if err != nil || result.Algorithm != "fixture/1" {
						t.Fatalf("rank: %+v %v", result, err)
					}
				}
			})
		}
	}
}

func TestRustConfigurationRefusesUnboundOrMalformedSelection(t *testing.T) {
	valid := rustTestConfig(t, RustRank, script(t, "exit 0\n"))
	for _, change := range []func(*RustWorkerConfig){
		func(c *RustWorkerConfig) { c.Mode = "" },
		func(c *RustWorkerConfig) { c.Mode = RustEmbed },
		func(c *RustWorkerConfig) { c.Executable = "relative" },
		func(c *RustWorkerConfig) { c.ModelDir = c.ORTRuntime },
		func(c *RustWorkerConfig) { c.ORTRuntime = filepath.Join(t.TempDir(), "missing") },
		func(c *RustWorkerConfig) { c.OpenBLAS = c.ModelDir },
		func(c *RustWorkerConfig) { c.GFortran = "relative" },
		func(c *RustWorkerConfig) { c.Quadmath = "" },
		func(c *RustWorkerConfig) { c.SourceRoot = "relative" },
	} {
		config := valid
		change(&config)
		if rank, err := RustCommand(config); err == nil || rank != nil {
			t.Fatalf("invalid configuration produced ranker: %+v %v", config, err)
		}
	}
	raw, err := json.Marshal(valid)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "worker.json")
	for _, data := range []string{string(raw) + `{}`, strings.TrimSuffix(string(raw), "}") + `,"env":{}}`, strings.Repeat(" ", 32769), `null`} {
		if err := os.WriteFile(path, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := ReadRustWorkerConfig(path); err == nil {
			t.Fatalf("invalid JSON configuration accepted: %.100s", data)
		}
	}
	if err := os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	if config, err := ReadRustWorkerConfig(path); err != nil || config != valid {
		t.Fatalf("valid configuration not preserved: %+v %v", config, err)
	}
}

func TestRustStreamFailuresDiscardChildAndNextRequestStartsFresh(t *testing.T) {
	testStreamFailureRecovery(t, func(t *testing.T) (core.SemanticRanker, func(), error) {
		config := rustTestConfig(t, RustStream, streamWorker(t))
		return RustStreamCommand(context.Background(), config, 30*time.Second)
	})
}
