package main

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestExplicitQueryCLICompileReplayAndRollback(t *testing.T) {
	t.Setenv("CAIRN_HOME", t.TempDir())
	for _, operation := range []string{"search", "index", "compile", "recompile"} {
		for _, state := range []string{"unknown", "compile-disabled", "supported", "rollback"} {
			t.Run(operation+"/"+state, func(t *testing.T) {
				dir, err := os.MkdirTemp("/tmp", "cairn-query-cli-")
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { os.RemoveAll(dir) })
				token, socket := filepath.Join(dir, "token"), filepath.Join(dir, "api.sock")
				if err = os.WriteFile(token, []byte("synthetic-token"), 0600); err != nil {
					t.Fatal(err)
				}
				listener, err := net.Listen("unix", socket)
				if err != nil {
					t.Fatal(err)
				}
				var versions, operations atomic.Int32
				server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Path == "/v1/version" {
						versions.Add(1)
						data := map[string]any{"preview_capabilities": map[string]any{"schema": "cairn.preview-capabilities/1", "entities_omitted": true}}
						if state != "unknown" {
							data["query_input_capabilities"] = map[string]any{"schema": "cairn.query-input-capabilities/1", "indexed_semantic_query_v1": state != "compile-disabled"}
						}
						json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": data})
						return
					}
					operations.Add(1)
					var fields map[string]any
					if err := json.NewDecoder(r.Body).Decode(&fields); err != nil {
						t.Error(err)
					}
					if fields["query"] != "storage" || fields["semantic_query"] != "Do NOT use public storage.\n" {
						t.Error("exact CLI input changed")
					}
					schema := core.DistinctQuerySchema
					if state == "rollback" {
						schema = "cairn.semantic/19"
					}
					pkg := core.Package{Semantic: core.SemanticPackage{Schema: schema, Index: []core.IndexEntry{}}}
					var data any = pkg
					if r.URL.Path == "/v1/index" {
						data = core.IndexResult{Package: pkg, Handles: []core.IndexHandle{}}
					} else if r.URL.Path == "/v1/recompile" {
						data = struct {
							Historical bool         `json:"historical"`
							Package    core.Package `json:"package"`
						}{true, pkg}
					}
					json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": data})
				})}
				go server.Serve(listener)
				t.Cleanup(func() { server.Close() })
				args := []string{"--socket", socket, "--token-file", token, operation}
				input := ""
				if operation == "search" {
					args = append(args, "--repo", "fixture", "--task", "task", "--run", "run", "--semantic", "--semantic-query-stdin", "storage")
					input = "Do NOT use public storage.\n"
				} else {
					input = `{"request_id":"b2ae2453-2a52-4109-87db-e3856cab33ed","receipt_id":"receipt","query":"storage","semantic_query":"Do NOT use public storage.\n","semantic":true}`
				}
				_, err = agentRequest(context.Background(), args, strings.NewReader(input))
				supported := state == "supported" || (state == "compile-disabled" && operation == "recompile")
				if (err == nil) != supported {
					t.Fatalf("request acceptance: %v", err)
				}
				if !supported && core.Code(err) != "UNSUPPORTED_QUERY_INPUT" {
					t.Fatalf("wrong refusal: %v", err)
				}
				want := int32(0)
				if supported || state == "rollback" {
					want = 1
				}
				if versions.Load() != 1 || operations.Load() != want {
					t.Fatalf("probes/effects: %d/%d", versions.Load(), operations.Load())
				}
				if state == "rollback" && !strings.Contains(err.Error(), "do not retry") {
					t.Fatal("rollback lost consumed-attempt warning")
				}
				if state == "rollback" && operation == "recompile" && strings.Contains(err.Error(), "committed") {
					t.Fatal("historical replay refusal implied a new commit")
				}
			})
		}
	}
}
