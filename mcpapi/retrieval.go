package mcpapi

import (
	"context"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
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
