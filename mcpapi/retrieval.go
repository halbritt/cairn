package mcpapi

import (
	"context"
	"errors"
	"reflect"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
	"github.com/halbritt/cairn/localapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// RetrievalBackend performs the two retrieval operations after facade validation.
// Search owns capability negotiation, the capability-selected preview format and
// distinct-query response checks as part of the same invocation. Implementations
// must preserve authenticated API/current-read checks; this interface supplies no
// authority, attempt budget, native-origin evidence or delivery acknowledgment.
// The server borrows the backend; its caller owns its resources and lifetime.
type RetrievalBackend interface {
	Search(context.Context, core.CompileRequest) (core.IndexResult, error)
	Pull(context.Context, core.ExpandRequest) (core.Expansion, error)
}

// NewReadOnlyServer registers only the ordinary search and pull contracts.
// It requires a backend and never opens a client or falls back to a local API.
// The caller must bind authentication, budgets and provenance in that backend;
// constructing this facade does not establish any of them.
func NewReadOnlyServer(backend RetrievalBackend, config Config) (*mcp.Server, error) {
	if backend == nil {
		return nil, errors.New("MCP retrieval backend is required")
	}
	value := reflect.ValueOf(backend)
	switch value.Kind() {
	case reflect.Chan, reflect.Func, reflect.Interface, reflect.Map, reflect.Pointer, reflect.Slice:
		if value.IsNil() {
			return nil, errors.New("MCP retrieval backend is required")
		}
	}
	config, err := validatedConfig(config)
	if err != nil {
		return nil, err
	}
	facadeBuild := buildinfo.Read()
	server := mcp.NewServer(&mcp.Implementation{Name: "cairn", Version: facadeBuild.Label()}, nil)
	if err := addRetrievalTools(server, memoryTools{retrieval: backend, config: config, facadeBuild: facadeBuild}); err != nil {
		return nil, err
	}
	return server, nil
}

type localRetrievalBackend struct {
	client        *localapi.Client
	previewChoice *localapi.PreviewCapabilityChoice
}

func (b localRetrievalBackend) Search(ctx context.Context, request core.CompileRequest) (core.IndexResult, error) {
	choice, err := b.previewChoice.ResolveInputs(ctx, b.client)
	if err != nil {
		return core.IndexResult{}, err
	}
	if request.SemanticQuery != "" {
		if err = choice.RequireDistinctInput(false); err != nil {
			return core.IndexResult{}, err
		}
	}
	request.CompactPreviewEntities = choice.CompactPreview
	var index core.IndexResult
	if err = b.client.Call(ctx, "index", request, &index); err != nil {
		return core.IndexResult{}, err
	}
	if request.SemanticQuery != "" {
		if err = localapi.ConfirmDistinctQuerySchema(index.Package.Semantic.Schema); err != nil {
			return core.IndexResult{}, err
		}
	}
	return index, nil
}

func (b localRetrievalBackend) Pull(ctx context.Context, request core.ExpandRequest) (core.Expansion, error) {
	var result core.Expansion
	err := b.client.Call(ctx, "expand", request, &result)
	return result, err
}
