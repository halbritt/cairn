package mcpapi

import (
	"context"
	"encoding/json"
	"sync/atomic"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type pullDedupStubBackend struct {
	searchCalls atomic.Int32
	pullCalls   atomic.Int32
	version     atomic.Int32
	totalBytes  int
}

func (b *pullDedupStubBackend) Search(_ context.Context, req core.CompileRequest) (core.IndexResult, error) {
	n := b.searchCalls.Add(1)
	ver := int(b.version.Load())
	if ver == 0 {
		ver = 1
	}
	receipt := uuid.NewString()
	handle := uuid.NewString()
	return core.IndexResult{
		Package: core.Package{
			ReceiptID: receipt,
			Semantic: core.SemanticPackage{
				Schema: "cairn.semantic/19",
				Status: "READY",
				Scope:  req.Scope,
				Index: []core.IndexEntry{{
					RecordID: "11111111-1111-4111-8111-111111111111",
					Version:  ver,
					Kind:     "note",
					Class:    "A",
					Summary:  "stub summary",
				}},
			},
		},
		Handles: []core.IndexHandle{{
			RecordID: "11111111-1111-4111-8111-111111111111",
			Version:  ver,
			Handle:   handle,
		}},
		CreditsRemaining: 4,
		BytesRemaining:   24000 - int(n),
	}, nil
}

func (b *pullDedupStubBackend) Pull(_ context.Context, req core.ExpandRequest) (core.Expansion, error) {
	b.pullCalls.Add(1)
	ver := int(b.version.Load())
	if ver == 0 {
		ver = 1
	}
	rec := core.Record{
		RecordID: "11111111-1111-4111-8111-111111111111",
		Version:  ver,
		Class:    "A",
		Draft: core.Draft{
			Kind: "note",
			Body: "full body content",
		},
	}
	var span *core.NoteSpan
	if req.Span != nil {
		rec.Body = ""
		total := b.totalBytes
		if total == 0 {
			total = 1000
		}
		end := req.Span.Offset + req.Span.Length
		if end > total {
			end = total
		}
		span = &core.NoteSpan{
			ByteSpan: core.ByteSpan{
				Offset:     req.Span.Offset,
				End:        end,
				TotalBytes: total,
				Body:       "excerpt",
			},
		}
	}
	return core.Expansion{
		Selection: core.Selection{
			Record: rec,
		},
		Span:             span,
		CreditsRemaining: 3,
		BytesRemaining:   20000,
	}, nil
}

func TestPullDedupWholeSpanVersionForceAndOptOut(t *testing.T) {
	backend := &pullDedupStubBackend{totalBytes: 500}
	backend.version.Store(1)
	server, err := NewReadOnlyServer(backend, Config{
		Scope:           core.Scope{Repo: "repo", TaskID: "task", RunID: "run"},
		AvailableTokens: 32000,
	})
	if err != nil {
		t.Fatal(err)
	}
	session := connect(t, context.Background(), server)
	call := func(name string, args any) map[string]any {
		t.Helper()
		res, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: name, Arguments: args})
		if err != nil || res.IsError {
			t.Fatalf("%s failed: %+v %v", name, res, err)
		}
		var out map[string]any
		if err := json.Unmarshal([]byte(res.Content[0].(*mcp.TextContent).Text), &out); err != nil {
			t.Fatal(err)
		}
		return out
	}

	// First search + span pull [0, 100)
	s1 := call("cairn_search", map[string]any{"query": "stub"})
	pull1 := s1["index"].([]any)[0].(map[string]any)["pull_arguments"].(map[string]any)
	pull1["span"] = map[string]any{"offset": 0, "length": 100}
	r1 := call("cairn_pull", pull1)
	if r1["already_delivered"] == true || backend.pullCalls.Load() != 1 {
		t.Fatalf("unexpected first span pull: %v calls=%d", r1, backend.pullCalls.Load())
	}

	// Repeat span pull with new request_id -> deduped (0 backend Pull calls)
	s2 := call("cairn_search", map[string]any{"query": "stub"})
	pull2 := s2["index"].([]any)[0].(map[string]any)["pull_arguments"].(map[string]any)
	pull2["span"] = map[string]any{"offset": 0, "length": 100}
	r2 := call("cairn_pull", pull2)
	if r2["already_delivered"] != true || backend.pullCalls.Load() != 1 {
		t.Fatalf("expected already_delivered for repeat span: %v calls=%d", r2, backend.pullCalls.Load())
	}

	// Distinct span [200, 300) -> misses cache, calls backend
	pull3 := s2["index"].([]any)[0].(map[string]any)["pull_arguments"].(map[string]any)
	pull3["request_id"] = uuid.NewString()
	pull3["span"] = map[string]any{"offset": 200, "length": 100}
	r3 := call("cairn_pull", pull3)
	if r3["already_delivered"] == true || backend.pullCalls.Load() != 2 {
		t.Fatalf("distinct span should not dedup: %v calls=%d", r3, backend.pullCalls.Load())
	}

	// Whole pull -> misses (spans don't cover whole), calls backend
	pull4 := s2["index"].([]any)[0].(map[string]any)["pull_arguments"].(map[string]any)
	pull4["request_id"] = uuid.NewString()
	delete(pull4, "span")
	r4 := call("cairn_pull", pull4)
	if r4["already_delivered"] == true || backend.pullCalls.Load() != 3 {
		t.Fatalf("whole pull should not dedup after span: %v calls=%d", r4, backend.pullCalls.Load())
	}

	// Whole pull already delivered -> covers BOTH whole and any span of same version
	pull5 := s2["index"].([]any)[0].(map[string]any)["pull_arguments"].(map[string]any)
	pull5["request_id"] = uuid.NewString()
	pull5["span"] = map[string]any{"offset": 350, "length": 50}
	r5 := call("cairn_pull", pull5)
	if r5["already_delivered"] != true || backend.pullCalls.Load() != 3 {
		t.Fatalf("whole should cover span of same version: %v calls=%d", r5, backend.pullCalls.Load())
	}

	// force: true bypasses dedup
	pull6 := s2["index"].([]any)[0].(map[string]any)["pull_arguments"].(map[string]any)
	pull6["request_id"] = uuid.NewString()
	delete(pull6, "span")
	pull6["force"] = true
	r6 := call("cairn_pull", pull6)
	if r6["already_delivered"] == true || backend.pullCalls.Load() != 4 {
		t.Fatalf("force=true should bypass dedup: %v calls=%d", r6, backend.pullCalls.Load())
	}

	// Version bump misses dedup
	backend.version.Store(2)
	s3 := call("cairn_search", map[string]any{"query": "stub"})
	pull7 := s3["index"].([]any)[0].(map[string]any)["pull_arguments"].(map[string]any)
	r7 := call("cairn_pull", pull7)
	if r7["already_delivered"] == true || backend.pullCalls.Load() != 5 {
		t.Fatalf("bumped version should bypass dedup: %v calls=%d", r7, backend.pullCalls.Load())
	}

	// CAIRN_RECALL_DEDUP=0 disables dedup
	t.Setenv("CAIRN_RECALL_DEDUP", "0")
	pull8 := s3["index"].([]any)[0].(map[string]any)["pull_arguments"].(map[string]any)
	pull8["request_id"] = uuid.NewString()
	r8 := call("cairn_pull", pull8)
	if r8["already_delivered"] == true || backend.pullCalls.Load() != 6 {
		t.Fatalf("CAIRN_RECALL_DEDUP=0 should disable dedup: %v calls=%d", r8, backend.pullCalls.Load())
	}
}
