package indexview

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

type fakeTransport struct {
	choice      localapi.InputCapabilityChoice
	resolveErr  error
	indexResult core.IndexResult
	indexErr    error
	resolves    int
	indexes     int
	lastRequest core.CompileRequest
}

func (f *fakeTransport) ResolveInputs(context.Context) (localapi.InputCapabilityChoice, error) {
	f.resolves++
	return f.choice, f.resolveErr
}

func (f *fakeTransport) Index(_ context.Context, req core.CompileRequest) (core.IndexResult, error) {
	f.indexes++
	f.lastRequest = req
	return f.indexResult, f.indexErr
}

func TestPrepareSearchValidationAndNormalization(t *testing.T) {
	intPtr := func(v int) *int { return &v }
	validScope := core.Scope{Repo: "repo", TaskID: "task", RunID: "run"}

	cases := []struct {
		name     string
		in       SearchInput
		wantCode string
	}{
		{
			name:     "memory-budget-too-small",
			in:       SearchInput{Scope: validScope, Query: "q", AvailableTokens: 8000, MemoryBudgetBytes: intPtr(255)},
			wantCode: "INVALID_REQUEST",
		},
		{
			name:     "guidance-exceeds-receipt-room",
			in:       SearchInput{Scope: validScope, Query: "q", AvailableTokens: 8000, MemoryBudgetBytes: intPtr(500), GuidanceBytes: 300},
			wantCode: "BUDGET_REFUSED",
		},
		{
			name:     "min-pull-without-memory-budget",
			in:       SearchInput{Scope: validScope, Query: "q", AvailableTokens: 8000, MinPullBytes: intPtr(100)},
			wantCode: "INVALID_REQUEST",
		},
		{
			name:     "inspection-conflicts-with-min-pull",
			in:       SearchInput{Scope: validScope, Query: "q", AvailableTokens: 8000, MemoryBudgetBytes: intPtr(4000), MinPullBytes: intPtr(500), InspectionPolicy: "first-fitting-whole/1"},
			wantCode: "INVALID_REQUEST",
		},
		{
			name:     "distinct-semantic-requires-semantic-flag",
			in:       SearchInput{Scope: validScope, Query: "q", SemanticQuery: "natural", AvailableTokens: 8000},
			wantCode: "INVALID_REQUEST",
		},
		{
			name:     "semantic-and-browse-conflict",
			in:       SearchInput{Scope: validScope, Browse: true, Semantic: true, AvailableTokens: 8000},
			wantCode: "INVALID_REQUEST",
		},
		{
			name:     "explicit-scope-required",
			in:       SearchInput{Scope: core.Scope{Repo: "repo", TaskID: "*"}, RequireExplicitScope: true, Query: "q", AvailableTokens: 8000},
			wantCode: "INVALID_REQUEST",
		},
		{
			name:     "context-pin-conflict",
			in:       SearchInput{Scope: validScope, Query: "q", AvailableTokens: 8000, FixedContext: &core.ContextPins{Revision: "a"}, Context: &core.ContextPins{Revision: "b"}},
			wantCode: "INVALID_REQUEST",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := PrepareSearch(tc.in); core.Code(err) != tc.wantCode {
				t.Fatalf("got %v, want code %s", err, tc.wantCode)
			}
		})
	}
}

func TestExecuteSearchAndRetrieveWithFakeTransportSeam(t *testing.T) {
	ctx := context.Background()
	recordID, receiptID, handle := uuid.NewString(), uuid.NewString(), uuid.NewString()
	baseResult := core.IndexResult{
		Package: core.Package{
			ReceiptID: receiptID,
			Seal:      "seal",
			Semantic: core.SemanticPackage{
				Schema: core.DistinctQuerySchema,
				Index:  []core.IndexEntry{{RecordID: recordID, Version: 1, Class: "A", Kind: "note", Summary: "s", BodySHA256: strings.Repeat("a", 64)}},
			},
		},
		Handles: []core.IndexHandle{{RecordID: recordID, Version: 1, Handle: handle}},
	}

	t.Run("distinct-query-preflight-refused", func(t *testing.T) {
		ft := &fakeTransport{choice: localapi.InputCapabilityChoice{CompactPreview: true, DistinctCompile: false}}
		_, err := ExecuteSearch(ctx, ft, core.CompileRequest{Query: "q", Semantic: true, SemanticQuery: "distinct"})
		if core.Code(err) != "UNSUPPORTED_QUERY_INPUT" || ft.indexes != 0 {
			t.Fatalf("expected preflight refusal without index call, got %v indexes=%d", err, ft.indexes)
		}
	})

	t.Run("distinct-query-rollback-refused", func(t *testing.T) {
		rolledBack := baseResult
		rolledBack.Package.Semantic.Schema = "cairn.semantic/19"
		ft := &fakeTransport{
			choice:      localapi.InputCapabilityChoice{CompactPreview: true, DistinctCompile: true},
			indexResult: rolledBack,
		}
		_, err := ExecuteSearch(ctx, ft, core.CompileRequest{Query: "q", Semantic: true, SemanticQuery: "distinct"})
		if core.Code(err) != "UNSUPPORTED_QUERY_INPUT" || ft.indexes != 1 {
			t.Fatalf("expected postflight schema refusal, got %v indexes=%d", err, ft.indexes)
		}
	})

	t.Run("probe-error-stops-before-index", func(t *testing.T) {
		ft := &fakeTransport{resolveErr: errors.New("probe failed")}
		if _, err := ExecuteSearch(ctx, ft, core.CompileRequest{Query: "q"}); err == nil || ft.indexes != 0 {
			t.Fatalf("expected probe error without index call, got %v indexes=%d", err, ft.indexes)
		}
	})

	t.Run("end-to-end-retrieve-cli", func(t *testing.T) {
		budget := 4000
		reserve := 500
		prepared, err := PrepareSearch(SearchInput{
			Scope:             core.Scope{Repo: "repo", TaskID: "task", RunID: "run"},
			Query:             "q",
			Semantic:          true,
			SemanticQuery:     "distinct",
			AvailableTokens:   8000,
			MemoryBudgetBytes: &budget,
			MinPullBytes:      &reserve,
		})
		if err != nil {
			t.Fatal(err)
		}
		ft := &fakeTransport{
			choice:      localapi.InputCapabilityChoice{CompactPreview: true, DistinctCompile: true},
			indexResult: baseResult,
		}
		view, err := RetrieveCLI(ctx, ft, prepared, []string{"cairn", "agent"})
		if err != nil {
			t.Fatal(err)
		}
		if !ft.lastRequest.CompactPreviewEntities || len(view.Index) != 1 || view.Index[0].PullArguments.Handle != handle {
			t.Fatalf("unexpected view or request: req=%+v view=%+v", ft.lastRequest, view)
		}
	})
}
