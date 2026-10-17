package mcpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type publicationBackend struct {
	search func(context.Context, core.CompileRequest) (core.IndexResult, SearchPublication, error)
	pull   func(context.Context, core.ExpandRequest) (core.Expansion, error)
}

func (b *publicationBackend) Search(ctx context.Context, req core.CompileRequest) (core.IndexResult, SearchPublication, error) {
	return b.search(ctx, req)
}

func (b *publicationBackend) Pull(ctx context.Context, req core.ExpandRequest) (core.Expansion, error) {
	return b.pull(ctx, req)
}

type observedPublication struct {
	prepared func(context.Context, string) error
	cancel   func(SearchPublicationFailure) error
}

func (p *observedPublication) Prepared(ctx context.Context, encoded string) error {
	return p.prepared(ctx, encoded)
}

func (p *observedPublication) Cancel(failure SearchPublicationFailure) error {
	return p.cancel(failure)
}

func publicationIndex(label string) core.IndexResult {
	return core.IndexResult{
		Package: core.Package{ReceiptID: "receipt-" + label, Seal: "seal-" + label,
			Semantic: core.SemanticPackage{Schema: "cairn.semantic/19",
				Index: []core.IndexEntry{{RecordID: "source-" + label, Version: 3, Summary: "source 日本語 \\\""}}}},
		Handles:          []core.IndexHandle{{RecordID: "source-" + label, Version: 3, Handle: "handle-" + label}},
		CreditsRemaining: 4, BytesRemaining: 3000,
	}
}

func publicationConfig() Config {
	return Config{Scope: core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}, AvailableTokens: 9500}
}

func TestAttemptSearchPublishesExactEncodedResultAndPreservesPull(t *testing.T) {
	const requestID = "b2ae2453-2a52-4109-87db-e3856cab33ed"
	published := make(chan string, 1)
	pulled := make(chan core.ExpandRequest, 1)
	backend := &publicationBackend{
		search: func(_ context.Context, req core.CompileRequest) (core.IndexResult, SearchPublication, error) {
			if req.RequestID != requestID || req.Scope != publicationConfig().Scope || req.Query != "source" {
				t.Errorf("changed request: %+v", req)
			}
			return publicationIndex("one"), &observedPublication{
				prepared: func(_ context.Context, encoded string) error { published <- encoded; return nil },
				cancel:   func(SearchPublicationFailure) error { t.Error("successful preparation cancelled"); return nil },
			}, nil
		},
		pull: func(_ context.Context, req core.ExpandRequest) (core.Expansion, error) {
			pulled <- req
			return core.Expansion{CreditsRemaining: 3, BytesRemaining: 2000}, nil
		},
	}
	server, err := NewAttemptReadOnlyServer(backend, publicationConfig())
	if err != nil {
		t.Fatal(err)
	}
	session := connect(t, context.Background(), server)
	result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_search",
		Arguments: searchArgs{Query: "source", RequestID: requestID}})
	if err != nil || result.IsError {
		t.Fatalf("search: %+v %v", result, err)
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		t.Fatal(err)
	}
	select {
	case exact := <-published:
		if exact != string(encoded) {
			t.Fatalf("publication was not the exact final encoded tool result: published %s returned %s", exact, encoded)
		}
	default:
		t.Fatal("result returned without successful preparation callback")
	}
	var view searchResult
	if err := json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &view); err != nil {
		t.Fatal(err)
	}
	if view.SourceSeal != "seal-one" || view.ReceiptID != "receipt-one" || view.Index[0].Version != 3 {
		t.Fatalf("raw correlation lost: %+v", view)
	}
	want := view.Index[0].PullArguments
	want.OpeningBytes = 96
	want.Span = &core.ByteSpanRequest{Offset: 1, Length: 20}
	result, err = session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_pull", Arguments: want})
	if err != nil || result.IsError || !reflect.DeepEqual(<-pulled, want) {
		t.Fatalf("typed pull changed: %+v %v", result, err)
	}
	// The exact complete encoded size is admissible, with no invented reserve.
	config := publicationConfig()
	config.AvailableTokens = len(encoded)
	exactServer, err := NewAttemptReadOnlyServer(backend, config)
	if err != nil {
		t.Fatal(err)
	}
	result, err = connect(t, context.Background(), exactServer).CallTool(context.Background(),
		&mcp.CallToolParams{Name: "cairn_search", Arguments: searchArgs{Query: "source", RequestID: requestID}})
	if err != nil || result.IsError {
		t.Fatalf("exact encoded budget refused: %+v %v", result, err)
	}
	exact, err := json.Marshal(result)
	if err != nil || len(exact) != config.AvailableTokens || <-published != string(exact) {
		t.Fatal("exact encoded budget or publication drifted")
	}
}

func TestAttemptSearchFailureCancelsAndSuppressesResult(t *testing.T) {
	for _, name := range []string{
		"backend-error", "backend-error-without-publication", "missing-publication", "typed-nil-publication",
		"missing-handle", "empty-handle", "duplicate-handle", "encoding-error", "final-encoded-budget", "server-fields-budget",
		"reserved-budget", "ignored-inspection", "inspection-reserved-budget", "publisher-error",
		"publisher-panic", "cancel-error", "cancel-panic", "publisher-and-cancel-error", "backend-panic",
	} {
		t.Run(name, func(t *testing.T) {
			const requestID = "b2ae2453-2a52-4109-87db-e3856cab33ed"
			raw := publicationIndex("private-result-canary")
			args := searchArgs{Query: "source", RequestID: requestID}
			config := publicationConfig()
			wantFailure := SearchPreparationFailed
			wantCalls, wantCancels := int32(0), int32(1)
			wantMessage := errSearchPreparation.Error()
			switch name {
			case "backend-error-without-publication", "missing-publication", "typed-nil-publication", "backend-panic":
				wantCancels = 0
			case "missing-handle":
				raw.Handles = nil
			case "empty-handle":
				raw.Handles[0].Handle = ""
			case "duplicate-handle":
				raw.Handles = append(raw.Handles, raw.Handles[0])
			case "encoding-error":
				raw.ExpiresAt = time.Date(10000, 1, 1, 0, 0, 0, 0, time.UTC)
			case "final-encoded-budget", "server-fields-budget", "reserved-budget":
				view, err := presentSearch(raw, requestID)
				if err != nil {
					t.Fatal(err)
				}
				body, _ := json.Marshal(view)
				encoded, _ := json.Marshal(&mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: string(body)}}})
				if len(encoded) <= len(body)+1 {
					t.Fatal("fixture does not distinguish inner JSON from final encoded budget")
				}
				config.AvailableTokens = len(body) + 1
				if name == "server-fields-budget" {
					// Content envelope fits exactly; server metadata/resultType do not.
					config.AvailableTokens = len(encoded)
				}
				if name == "reserved-budget" {
					config.AvailableTokens = len(encoded) + 999
					args.MemoryBudgetBytes, args.MinPullBytes = ptrInt(config.AvailableTokens), ptrInt(1000)
				}
			case "ignored-inspection", "inspection-reserved-budget":
				args.MemoryBudgetBytes, args.InspectionPolicy = ptrInt(4300), "first-fitting-whole/1"
				if name == "inspection-reserved-budget" {
					raw.Package.Semantic.MemoryBudget = &core.MemoryBudget{Schema: "cairn.memory-budget/3", Bytes: 4300,
						InspectionPolicy: args.InspectionPolicy, InspectionStatus: "ready", MinPullBytes: 4290}
					raw.BytesRemaining = 4290
				}
			case "publisher-error", "publisher-panic", "publisher-and-cancel-error":
				wantCalls, wantFailure, wantMessage = 1, SearchPublicationUnknown, errSearchPublication.Error()
			case "cancel-error", "cancel-panic":
				raw.Handles = nil
				wantMessage = errSearchPublication.Error()
			}
			var calls, cancels atomic.Int32
			publication := &observedPublication{
				prepared: func(context.Context, string) error {
					calls.Add(1)
					if name == "publisher-panic" {
						panic("private-publisher-canary")
					}
					return errors.New("private-publisher-canary")
				},
				cancel: func(failure SearchPublicationFailure) error {
					cancels.Add(1)
					if failure != wantFailure {
						t.Errorf("lost uncertainty: got %s want %s", failure, wantFailure)
					}
					if name == "cancel-panic" {
						panic("private-cancel-canary")
					}
					if name == "cancel-error" || name == "publisher-and-cancel-error" {
						return errors.New("private-cancel-canary")
					}
					return nil
				},
			}
			backend := &publicationBackend{search: func(context.Context, core.CompileRequest) (core.IndexResult, SearchPublication, error) {
				switch name {
				case "backend-error":
					return raw, publication, &core.Error{Code: "private-code", Message: "private-backend-canary"}
				case "backend-error-without-publication":
					return raw, nil, errors.New("private-backend-canary")
				case "missing-publication":
					return raw, nil, nil
				case "typed-nil-publication":
					var missing *observedPublication
					return raw, missing, nil
				case "backend-panic":
					panic("private-backend-canary")
				}
				return raw, publication, nil
			}}
			server, err := NewAttemptReadOnlyServer(backend, config)
			if err != nil {
				t.Fatal(err)
			}
			result, err := connect(t, context.Background(), server).CallTool(context.Background(),
				&mcp.CallToolParams{Name: "cairn_search", Arguments: args})
			if err != nil || !result.IsError || len(result.Content) != 1 {
				t.Fatalf("expected fixed refusal: %+v %v", result, err)
			}
			message := result.Content[0].(*mcp.TextContent).Text
			if message != wantMessage || strings.Contains(message, "private") || calls.Load() != wantCalls || cancels.Load() != wantCancels {
				t.Fatalf("message %q, prepared/cancel %d/%d; want %q %d/%d", message, calls.Load(), cancels.Load(), wantMessage, wantCalls, wantCancels)
			}
		})
	}
}

func TestAttemptFacadeRequiresBackendAndKeepsToolContracts(t *testing.T) {
	var missing *publicationBackend
	for _, backend := range []AttemptRetrievalBackend{nil, missing} {
		if server, err := NewAttemptReadOnlyServer(backend, publicationConfig()); err == nil || server != nil {
			t.Fatal("missing backend became a callable facade")
		}
	}
	var calls atomic.Int32
	backend := &publicationBackend{search: func(context.Context, core.CompileRequest) (core.IndexResult, SearchPublication, error) {
		calls.Add(1)
		return core.IndexResult{}, nil, errors.New("must not be called")
	}}
	server, err := NewAttemptReadOnlyServer(backend, publicationConfig())
	if err != nil {
		t.Fatal(err)
	}
	ordinary, err := NewReadOnlyServer(localRetrievalBackend{}, publicationConfig())
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	session := connect(t, ctx, server)
	got, err := session.ListTools(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	want, err := connect(t, ctx, ordinary).ListTools(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	a, _ := json.Marshal(got.Tools)
	b, _ := json.Marshal(want.Tools)
	if string(a) != string(b) || len(got.Tools) != 2 {
		t.Fatal("ordinary retrieval tool contracts changed")
	}
	result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "cairn_search", Arguments: searchArgs{Query: "q", AvailableTokens: ptrInt(9501)}})
	if err != nil || !result.IsError || calls.Load() != 0 {
		t.Fatal("invalid request reached attempt backend")
	}
}

func TestAttemptSearchCancellationAtBackendAndPublication(t *testing.T) {
	for _, stage := range []string{"backend", "publisher-error", "publisher-nil", "cancel-error"} {
		t.Run(stage, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			entered := make(chan struct{})
			cancelled := make(chan SearchPublicationFailure, 1)
			var publications atomic.Int32
			publication := &observedPublication{
				prepared: func(ctx context.Context, _ string) error {
					publications.Add(1)
					close(entered)
					<-ctx.Done()
					if stage == "publisher-nil" {
						return nil // Cancellation still suppresses this successful return.
					}
					return errors.New("private-cancelled-publisher")
				},
				cancel: func(failure SearchPublicationFailure) error {
					cancelled <- failure
					if stage == "cancel-error" {
						return errors.New("private-cancel-failure")
					}
					return nil
				},
			}
			backend := &publicationBackend{search: func(ctx context.Context, _ core.CompileRequest) (core.IndexResult, SearchPublication, error) {
				if stage == "backend" {
					close(entered)
					<-ctx.Done()
				}
				return publicationIndex("cancel"), publication, nil
			}}
			server, err := NewAttemptReadOnlyServer(backend, publicationConfig())
			if err != nil {
				t.Fatal(err)
			}
			session := connect(t, context.Background(), server)
			done := make(chan bool, 1)
			go func() {
				result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "cairn_search", Arguments: searchArgs{Query: "source"}})
				done <- err != nil || result == nil || result.IsError
			}()
			select {
			case <-entered:
			case <-time.After(5 * time.Second):
				t.Fatal("call never entered selected boundary")
			}
			cancel()
			select {
			case failure := <-cancelled:
				want := SearchPublicationUnknown
				wantCalls := int32(1)
				if stage == "backend" {
					want, wantCalls = SearchPreparationFailed, 0
				}
				if failure != want || publications.Load() != wantCalls {
					t.Fatalf("cancel outcome %s calls %d", failure, publications.Load())
				}
			case <-time.After(5 * time.Second):
				t.Fatal("request cancellation skipped publication cleanup")
			}
			select {
			case suppressed := <-done:
				if !suppressed {
					t.Fatal("cancelled call returned a successful result")
				}
			case <-time.After(5 * time.Second):
				t.Fatal("cancelled call did not return")
			}
		})
	}
}

func TestAttemptSearchSameRequestIDOverlapsWithoutCrossPublication(t *testing.T) {
	const requestID = "b2ae2453-2a52-4109-87db-e3856cab33ed"
	var ordinal atomic.Int32
	entered := make(chan string, 2)
	release := make(chan struct{})
	backend := &publicationBackend{search: func(_ context.Context, req core.CompileRequest) (core.IndexResult, SearchPublication, error) {
		if req.RequestID != requestID {
			t.Error("request identity changed")
		}
		label := fmt.Sprint(ordinal.Add(1))
		raw := publicationIndex(label)
		return raw, &observedPublication{
			prepared: func(_ context.Context, encoded string) error {
				if !strings.Contains(encoded, "receipt-"+label) || !strings.Contains(encoded, "handle-"+label) {
					t.Error("another invocation's result reached publication")
				}
				// Parent mutation after encoding cannot change the frozen MCP text.
				raw.Package.Semantic.Index[0].Summary = "mutated-after-preparation"
				entered <- encoded
				<-release
				return nil
			},
			cancel: func(SearchPublicationFailure) error { t.Error("unexpected cancellation"); return nil },
		}, nil
	}}
	server, err := NewAttemptReadOnlyServer(backend, publicationConfig())
	if err != nil {
		t.Fatal(err)
	}
	session := connect(t, context.Background(), server)
	results := make(chan string, 2)
	for range 2 {
		go func() {
			result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_search",
				Arguments: searchArgs{Query: "source", RequestID: requestID}})
			if err != nil || result.IsError {
				t.Errorf("overlapping call: %+v %v", result, err)
			}
			encoded, _ := json.Marshal(result)
			results <- string(encoded)
		}()
	}
	want := map[string]bool{}
	for range 2 {
		select {
		case encoded := <-entered:
			want[encoded] = true
		case <-time.After(5 * time.Second):
			close(release)
			t.Fatal("calls did not overlap at publication")
		}
	}
	close(release)
	if len(want) != 2 {
		t.Fatal("same request ID collapsed distinct publications")
	}
	for range 2 {
		select {
		case encoded := <-results:
			if !want[encoded] || strings.Contains(encoded, "mutated-after-preparation") {
				t.Fatal("returned result lost exact immutable correlation")
			}
			delete(want, encoded)
		case <-time.After(5 * time.Second):
			t.Fatal("call did not finish")
		}
	}
}
