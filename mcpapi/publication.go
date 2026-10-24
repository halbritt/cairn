package mcpapi

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// AttemptRetrievalBackend returns the raw result and its invocation-owned
// publication together. Even overlapping searches with equal request IDs must
// return distinct held publications. The trusted backend owns failed/uncertain
// operations, including failures before it can return a publication. No ordinary
// backend or local API fallback is provided. Pull retains its existing contract.
type AttemptRetrievalBackend interface {
	Search(context.Context, core.CompileRequest) (core.IndexResult, SearchPublication, error)
	Pull(context.Context, core.ExpandRequest) (core.Expansion, error)
}

// SearchPublicationFailure describes preparation eligibility, not backend
// effects, transport delivery or an authenticated native observation.
type SearchPublicationFailure string

const (
	// SearchPreparationFailed means Prepared was never entered.
	SearchPreparationFailed SearchPublicationFailure = "preparation_failed"
	// SearchPublicationUnknown remains unknown even if subsequent Cancel succeeds.
	SearchPublicationUnknown SearchPublicationFailure = "publication_unknown"
)

// SearchPublication is trusted parent-owned custody for one actual raw operation.
// Prepared receives immutable, complete CallToolResult JSON after projection and
// encoded-budget validation. It must validate the exact request/handle/version/
// seal correlation against its retained operation and honor the original context.
// Nil confirms only local preparation, never transport write or native delivery.
// Cancel is one local cleanup attempt, also when the request context is cancelled;
// it must not block on external effects, retry, or restore uncertain eligibility.
// The parent retains the object and its outcome after the facade returns.
type SearchPublication interface {
	Prepared(context.Context, string) error
	Cancel(SearchPublicationFailure) error
}

var (
	errSearchPreparation = errors.New("SEARCH_PREPARATION_FAILED: search result suppressed")
	errSearchPublication = errors.New("SEARCH_PUBLICATION_UNKNOWN: search result suppressed")
	errSearchCancelled   = errors.New("SEARCH_CANCELLED: search result suppressed")
)

func absentPublicationDependency(value any) bool {
	if value == nil {
		return true
	}
	v := reflect.ValueOf(value)
	switch v.Kind() {
	case reflect.Chan, reflect.Func, reflect.Interface, reflect.Map, reflect.Pointer, reflect.Slice:
		return v.IsNil()
	}
	return false
}

// NewAttemptReadOnlyServer explicitly selects successful-search preparation
// custody. It starts no runtime and registers only the existing search/pull tools.
// Backend and per-invocation publication are required, with no fallback.
func NewAttemptReadOnlyServer(backend AttemptRetrievalBackend, config Config) (*mcp.Server, error) {
	if absentPublicationDependency(backend) {
		return nil, errors.New("MCP attempt retrieval backend is required")
	}
	config, err := validatedConfig(config)
	if err != nil {
		return nil, err
	}
	facadeBuild := buildinfo.Read()
	server := mcp.NewServer(&mcp.Implementation{Name: "cairn", Version: facadeBuild.Label()}, nil)
	addRetrievalTools(server, memoryTools{attempt: backend, config: config, facadeBuild: facadeBuild})
	return server, nil
}

func cancelSearchPublication(publication SearchPublication, failure SearchPublicationFailure) (err error) {
	defer func() {
		if recover() != nil {
			err = errSearchPublication
		}
	}()
	return publication.Cancel(failure)
}

func (t memoryTools) attemptSearch(ctx context.Context, request core.CompileRequest, args searchArgs, room, receiptRoom int) (result *mcp.CallToolResult, structured any, err error) {
	var publication SearchPublication
	failure := SearchPreparationFailed
	complete := false
	defer func() {
		if recover() != nil {
			err = errSearchPreparation
		}
		if complete {
			return
		}
		result, structured = nil, nil
		if failure == SearchPublicationUnknown {
			err = errSearchPublication
		}
		if !absentPublicationDependency(publication) {
			if cancelSearchPublication(publication, failure) != nil {
				err = errSearchPublication
			}
		}
	}()
	if ctx.Err() != nil {
		return nil, nil, errSearchCancelled
	}
	index, publication, err := t.attempt.Search(ctx, request)
	if err != nil || absentPublicationDependency(publication) {
		return nil, nil, errSearchPreparation
	}
	if ctx.Err() != nil {
		return nil, nil, errSearchCancelled
	}
	view, room, err := searchView(index, args, t.config.SearchView, false, room, receiptRoom)
	if err != nil {
		return nil, nil, errSearchPreparation
	}
	// The installed MCP facade adds these fields after a typed handler returns.
	// Seed the public JSON representation first so the SAME complete result is
	// budgeted and handed off, not just its text-content projection. The boundary
	// tests pin equality with the real server's returned CallToolResult encoding.
	result = new(mcp.CallToolResult)
	if err := json.Unmarshal([]byte(`{"content":[],"resultType":"complete"}`), result); err != nil {
		return nil, nil, errSearchPreparation
	}
	result.SetMeta(map[string]any{"io.modelcontextprotocol/serverInfo": &mcp.Implementation{
		Name: "cairn", Version: t.facadeBuild.Label(),
	}})
	result, encoded, err := encodeToolResultInto(result, view, room)
	if err != nil {
		return nil, nil, errSearchPreparation
	}
	if ctx.Err() != nil {
		return nil, nil, errSearchCancelled
	}
	// From entry through the final context check, a failure cannot prove that
	// publication had no effect. Cancellation never upgrades that uncertainty.
	failure = SearchPublicationUnknown
	if err := publication.Prepared(ctx, string(encoded)); err != nil {
		return nil, nil, errSearchPublication
	}
	if ctx.Err() != nil {
		return nil, nil, errSearchPublication
	}
	complete = true
	return result, nil, nil
}
