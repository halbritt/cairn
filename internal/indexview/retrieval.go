package indexview

import (
	"context"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

// Transport is the narrow seam for capability negotiation and index execution.
type Transport interface {
	ResolveInputs(ctx context.Context) (localapi.InputCapabilityChoice, error)
	Index(ctx context.Context, request core.CompileRequest) (core.IndexResult, error)
}

type clientTransport struct {
	client *localapi.Client
	choice *localapi.PreviewCapabilityChoice
}

func NewClientTransport(client *localapi.Client, choice *localapi.PreviewCapabilityChoice) Transport {
	if choice == nil {
		choice = new(localapi.PreviewCapabilityChoice)
	}
	return clientTransport{client: client, choice: choice}
}

func (t clientTransport) ResolveInputs(ctx context.Context) (localapi.InputCapabilityChoice, error) {
	return t.choice.ResolveInputs(ctx, t.client)
}

func (t clientTransport) Index(ctx context.Context, request core.CompileRequest) (core.IndexResult, error) {
	var result core.IndexResult
	err := t.client.Call(ctx, "index", request, &result)
	return result, err
}

func ResolveAllowance(defaultTokens, maxTokens int, available, memory *int) (room, allowance int, err error) {
	room = defaultTokens
	if available != nil {
		if *available < 256 || (maxTokens > 0 && *available > maxTokens) {
			return 0, 0, invalid(fmt.Sprintf("available_tokens must be between 256 and configured ceiling %d", maxTokens))
		}
		room = *available
	}
	allowance = room
	if memory != nil {
		if *memory < 256 || *memory > room {
			return 0, 0, invalid(fmt.Sprintf("memory_budget_bytes must be between 256 and available_tokens %d", room))
		}
		allowance = *memory
	}
	return room, allowance, nil
}

func MergeContextPins(fixed, supplied *core.ContextPins) (*core.ContextPins, error) {
	if supplied == nil {
		return fixed, nil
	}
	merged := core.ContextPins{}
	if fixed != nil {
		merged = *fixed
	}
	for _, field := range []struct {
		name   string
		value  string
		target *string
	}{
		{"revision", supplied.Revision, &merged.Revision},
		{"workspace_sha256", supplied.WorkspaceSHA256, &merged.WorkspaceSHA256},
		{"task_class", supplied.TaskClass, &merged.TaskClass},
		{"task_phase", supplied.TaskPhase, &merged.TaskPhase},
		{"binding_id", supplied.BindingID, &merged.BindingID},
		{"capability_id", supplied.CapabilityID, &merged.CapabilityID},
	} {
		if field.value == "" {
			continue
		}
		if *field.target != "" && *field.target != field.value {
			return nil, invalid(fmt.Sprintf("context.%s conflicts with configured context", field.name))
		}
		*field.target = field.value
	}
	return &merged, nil
}

type SearchInput struct {
	Scope                core.Scope
	RequireExplicitScope bool
	Query                string
	Semantic             bool
	SemanticQuery        string
	Browse               bool
	Offset               *int
	AvailableTokens      int
	MemoryBudgetBytes    *int
	MinPullBytes         *int
	InspectionPolicy     string
	AdvisoryConflicts    bool
	Entities             []core.EntityRef
	ErrorSignature       string
	Kinds                []string
	RequestID            string
	FixedContext         *core.ContextPins
	Context              *core.ContextPins
	GuidanceBytes        int
}

type PreparedSearch struct {
	Request     core.CompileRequest
	MemoryRoom  int
	ReceiptRoom int
}

func PrepareSearch(in SearchInput) (PreparedSearch, error) {
	_, memoryRoom, err := ResolveAllowance(in.AvailableTokens, 0, nil, in.MemoryBudgetBytes)
	if err != nil {
		return PreparedSearch{}, err
	}
	receiptRoom := memoryRoom
	compiledBudget := in.MemoryBudgetBytes
	if in.GuidanceBytes > 0 {
		receiptRoom -= in.GuidanceBytes
		if receiptRoom < 256 {
			return PreparedSearch{}, &core.Error{Code: "BUDGET_REFUSED", Message: "note preparation needs room for guidance and a search receipt"}
		}
		compiledBudget = &receiptRoom
	}
	if in.MinPullBytes != nil && (in.MemoryBudgetBytes == nil || *in.MinPullBytes < 1 || *in.MinPullBytes > min(24000, receiptRoom)) {
		bound := "memory_budget_bytes"
		if in.GuidanceBytes > 0 {
			bound = fmt.Sprintf("memory_budget_bytes-%d", in.GuidanceBytes)
		}
		return PreparedSearch{}, invalid(fmt.Sprintf("min_pull_bytes requires memory_budget_bytes and 1..min(24000, %s)", bound))
	}
	if in.InspectionPolicy != "" && ((in.InspectionPolicy != "first-fitting-whole/1" && in.InspectionPolicy != "first-fitting-whole/2") || in.MemoryBudgetBytes == nil || in.MinPullBytes != nil) {
		return PreparedSearch{}, invalid("inspection_policy requires first-fitting-whole/1 or /2, memory_budget_bytes and no min_pull_bytes")
	}
	if in.SemanticQuery != "" && (!in.Semantic || in.Browse || strings.TrimSpace(in.Query) == "") {
		return PreparedSearch{}, invalid("distinct semantic input requires --semantic, a lexical query and no browsing")
	}
	if in.Semantic && in.Browse {
		return PreparedSearch{}, invalid("semantic discovery cannot be combined with browsing")
	}
	if in.RequireExplicitScope && (strings.TrimSpace(in.Scope.TaskID) == "" || strings.TrimSpace(in.Scope.RunID) == "" || in.Scope.TaskID == "*" || in.Scope.RunID == "*") {
		return PreparedSearch{}, invalid("agent search requires explicit --task and --run")
	}
	if (in.Browse && (in.Query != "" || in.ErrorSignature != "" || len(in.Entities) > 0)) || (!in.Browse && strings.TrimSpace(in.Query) == "" && in.ErrorSignature == "" && len(in.Entities) == 0) {
		return PreparedSearch{}, invalid("search requires a query, entities, error_signature_sha256, or browse=true without search hints")
	}
	if in.Offset != nil && (*in.Offset < 0 || *in.Offset > 10000) {
		return PreparedSearch{}, invalid("offset must be 0-10000")
	}
	var browseOffset, pageOffset *int
	if in.Browse {
		browseOffset = in.Offset
		if browseOffset == nil {
			browseOffset = new(int)
		}
	} else {
		pageOffset = in.Offset
	}
	requestID := in.RequestID
	if requestID == "" {
		requestID = uuid.NewString()
	}
	contextPins := in.Context
	if in.FixedContext != nil {
		contextPins, err = MergeContextPins(in.FixedContext, in.Context)
		if err != nil {
			return PreparedSearch{}, err
		}
	}
	return PreparedSearch{
		Request: core.CompileRequest{
			MemoryBudgetBytes: compiledBudget,
			MinPullBytes:      in.MinPullBytes,
			InspectionPolicy:  in.InspectionPolicy,
			AdvisoryConflicts: in.AdvisoryConflicts,
			Entities:          in.Entities,
			ErrorSignature:    in.ErrorSignature,
			Kinds:             in.Kinds,
			RequestID:         requestID,
			BrowseOffset:      browseOffset,
			PageOffset:        pageOffset,
			Semantic:          in.Semantic,
			SemanticQuery:     in.SemanticQuery,
			Scope:             in.Scope,
			Query:             in.Query,
			Purpose:           "context",
			AvailableTokens:   in.AvailableTokens,
			Context:           contextPins,
		},
		MemoryRoom:  memoryRoom,
		ReceiptRoom: receiptRoom,
	}, nil
}

func ExecuteSearch(ctx context.Context, transport Transport, request core.CompileRequest) (core.IndexResult, error) {
	choice, err := transport.ResolveInputs(ctx)
	if err != nil {
		return core.IndexResult{}, err
	}
	if request.SemanticQuery != "" {
		if err = choice.RequireDistinctInput(false); err != nil {
			return core.IndexResult{}, err
		}
	}
	request.CompactPreviewEntities = choice.CompactPreview
	result, err := transport.Index(ctx, request)
	if err != nil {
		return core.IndexResult{}, err
	}
	if request.SemanticQuery != "" {
		if err = localapi.ConfirmDistinctQuerySchema(result.Package.Semantic.Schema); err != nil {
			return core.IndexResult{}, err
		}
	}
	return result, nil
}

func ResolveViewRoom(result core.IndexResult, minPull *int, inspectionPolicy string, allowance, receiptCap int) (int, error) {
	if minPull != nil {
		allowance -= *minPull
	}
	if inspectionPolicy != "" {
		return InspectionViewRoom(result, inspectionPolicy, allowance, receiptCap)
	}
	return allowance, nil
}

func RetrieveCLI(ctx context.Context, transport Transport, prepared PreparedSearch, command []string) (View, error) {
	result, err := ExecuteSearch(ctx, transport, prepared.Request)
	if err != nil {
		return View{}, err
	}
	room, err := ResolveViewRoom(result, prepared.Request.MinPullBytes, prepared.Request.InspectionPolicy, prepared.MemoryRoom, prepared.ReceiptRoom)
	if err != nil {
		return View{}, err
	}
	return Present(result, prepared.Request.RequestID, command, room)
}
