package memoryhook

import (
	"bytes"
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/lifecycle/memoryclient"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/localapi"
)

type testCandidateObserver struct {
	panic, lost                bool
	routes, validated, renders int
}

func (o *testCandidateObserver) ObservationLost() { o.lost = true }
func (o *testCandidateObserver) Route(_ string, group []*value.Value) {
	o.routes++
	for _, v := range group {
		v.Set("body_sha256", value.Text("mutated"))
	}
	if o.panic {
		panic("observer lost")
	}
}
func (o *testCandidateObserver) Validated(_ string, v *value.Value) {
	o.validated++
	v.Set("selection", value.Text("mutated"))
	if o.panic {
		panic("observer lost")
	}
}
func (o *testCandidateObserver) Rendered(entries, bodies []*value.Value, selected *value.Value, _ int, _ candidateMeasuredText, _ int, packing *value.Value) {
	o.renders++
	for _, v := range entries {
		v.Set("summary", value.Text("mutated"))
	}
	for _, v := range bodies {
		v.Set("response", value.Text("mutated"))
	}
	selected.Items = nil
	packing.Set("base_bytes", integer(-1))
	if o.panic {
		panic("observer lost")
	}
}

type candidateSequence struct{ n byte }

func (s *candidateSequence) Read(p []byte) (int, error) {
	for i := range p {
		p[i] = s.n
	}
	s.n++
	return len(p), nil
}

func TestCandidateObserverPreservesActualWholeSpanAndAccounting(t *testing.T) {
	for _, spanMode := range []bool{false, true} {
		var previousBody string
		var previousStatus []byte
		var previousAccounting memoryclient.Accounting
		var previousRequests []map[string]any
		for _, mode := range []string{"nil", "on", "panic"} {
			t.Run(map[bool]string{false: "whole", true: "span"}[spanMode]+"-"+mode, func(t *testing.T) {
				uuid.SetRand(&candidateSequence{})
				defer uuid.SetRand(nil)
				dir, err := os.MkdirTemp("", "candidate-observer-")
				if err != nil {
					t.Fatal(err)
				}
				defer os.RemoveAll(dir)
				if err = os.WriteFile(filepath.Join(dir, "token"), []byte("synthetic-candidate-token"), 0600); err != nil {
					t.Fatal(err)
				}
				listener, err := net.Listen("unix", filepath.Join(dir, "api.sock"))
				if err != nil {
					t.Fatal(err)
				}
				const record = "22222222-2222-4222-8222-222222222222"
				const receipt = "11111111-1111-4111-8111-111111111111"
				body := "Checked source body from the real SDK fixture."
				var mu sync.Mutex
				requests := []map[string]any{}
				expands := 0
				server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Header.Get("Authorization") != "Bearer synthetic-candidate-token" {
						w.WriteHeader(401)
						return
					}
					request := map[string]any{}
					if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
						t.Error(err)
						return
					}
					op := strings.TrimPrefix(r.URL.Path, "/v1/")
					request["fixture_operation"] = op
					mu.Lock()
					requests = append(requests, request)
					mu.Unlock()
					var data any
					switch op {
					case "version":
						data = map[string]any{"preview_capabilities": map[string]any{"schema": "cairn.preview-capabilities/1", "entities_omitted": true}}
					case "index":
						data = map[string]any{"package": map[string]any{"receipt_id": receipt, "seal": "blake3:" + strings.Repeat("a", 64), "semantic": map[string]any{"schema": "cairn.semantic/17", "mode": "index", "status": "READY", "scope": request["scope"], "purpose": "context", "destination": map[string]any{"name": "hosted", "allow_local": false}, "query": "sha256:fixture-query", "available_tokens": 32000, "selected": []any{}, "omitted": map[string]any{}, "index": []any{map[string]any{"record_id": record, "version": 1, "class": "A", "kind": "note", "summary": "Checked", "body_sha256": hashText(body), "summary_span": map[string]any{"offset": 0, "length": 7}}}}}, "handles": []any{map[string]any{"record_id": record, "version": 1, "handle": "fixture-handle"}}, "expires_at": "2030-01-01T00:00:00Z", "credits_remaining": 4, "bytes_remaining": 24000}
					case "expand":
						expands++
						if spanMode && request["span"] == nil {
							w.WriteHeader(422)
							json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": false, "status": "BUDGET_REFUSED", "message": "fixture refusal"})
							return
						}
						recordBody := body
						if spanMode {
							recordBody = ""
						}
						d := map[string]any{"selection": map[string]any{"record": map[string]any{"record_id": record, "version": 1, "class": "A", "kind": "note", "body": recordBody}, "mandatory": false, "reason": "candidate", "evidence": []any{}, "authority": []any{}, "conflicts": []any{}}, "credits_remaining": 4 - expands, "bytes_remaining": 20000}
						if spanMode {
							hint := request["span"].(map[string]any)
							start, end := int(hint["offset"].(float64)), min(len(body), int(hint["offset"].(float64)+hint["length"].(float64)))
							part := body[start:end]
							d["span"] = map[string]any{"offset": start, "end": end, "total_bytes": len(body), "sha256": hashText(part), "source_sha256": hashText(body), "body": part}
						}
						data = d
					default:
						t.Error("unexpected operation", op)
						w.WriteHeader(400)
						return
					}
					json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": data})
				})}
				served := make(chan struct{})
				go func() { defer close(served); server.Serve(listener) }()
				defer func() { server.Close(); <-served }()
				api, err := localapi.NewClient(filepath.Join(dir, "api.sock"), filepath.Join(dir, "token"))
				if err != nil {
					t.Fatal(err)
				}
				defer api.Close()
				client, err := memoryclient.New(api, memoryclient.Options{Scope: core.Scope{Repo: "fixture", TaskID: "fixture-task", RunID: "fixture-run"}})
				if err != nil {
					t.Fatal(err)
				}
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				view, err := client.Search(ctx, "fixture", memoryclient.SearchOptions{})
				if err != nil {
					t.Fatal(err)
				}
				result, err := decoded(view)
				if err != nil {
					t.Fatal(err)
				}
				original, _ := encoded(result, false)
				status := value.Mapping(value.Field("rejected", value.Mapping()))
				observer := &testCandidateObserver{panic: mode == "panic"}
				var observers []candidateObserver
				if mode != "nil" {
					observers = []candidateObserver{observer}
				}
				got, err := eagerCandidates(ctx, client, value.Mapping(), result.Get("selected"), result, view.Index, 9500, status, func(s string) int { return len(s) + 100 }, observers...)
				if err != nil {
					t.Fatal(err)
				}
				if now, _ := encoded(result, false); !bytes.Equal(now, original) {
					t.Fatal("observer mutated canonical search data")
				}
				statusBytes, _ := encoded(status, false)
				mu.Lock()
				captured := append([]map[string]any(nil), requests...)
				mu.Unlock()
				if mode == "nil" {
					previousBody = got
					previousStatus = statusBytes
					previousAccounting = client.Accounting()
					previousRequests = captured
				} else {
					if got != previousBody || !bytes.Equal(previousStatus, statusBytes) || !reflect.DeepEqual(previousAccounting, client.Accounting()) || !reflect.DeepEqual(previousRequests, captured) {
						t.Fatal("observer changed output, status, RPCs or credit accounting")
					}
					if observer.routes == 0 || observer.validated == 0 || observer.renders == 0 || observer.lost != (mode == "panic") {
						t.Fatalf("missing real observation/loss: %+v", observer)
					}
				}
				want := 1
				if spanMode {
					want = 2
				}
				if client.Accounting().OperationCalls["pull"] != want {
					t.Fatal("actual pull attempts not counted")
				}
			})
		}
	}
}
