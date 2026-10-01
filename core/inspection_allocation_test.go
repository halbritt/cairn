package core

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// JSON exercises the public request field, including the pre-implementation RED.
func inspectionRequest(t *testing.T, req CompileRequest) CompileRequest {
	t.Helper()
	raw, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]any
	if err = json.Unmarshal(raw, &fields); err != nil {
		t.Fatal(err)
	}
	fields["inspection_policy"] = "first-fitting-whole/1"
	raw, err = json.Marshal(fields)
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, &req); err != nil {
		t.Fatal(err)
	}
	return req
}

func TestInspectionAllocationReservesWholeSourceInsteadOfExtraPreviews(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	var target Record
	for i := range 3 {
		d := projectNote(repo)
		d.Sensitivity = "shareable"
		d.Body = fmt.Sprintf("Inspect source %d. ", i) + strings.Repeat("日本語 \"quoted\" \\path\n", 25)
		if i == 0 {
			d.Body = "priority-source " + d.Body
		}
		r, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
		if err != nil {
			t.Fatal(err)
		}
		if i == 0 {
			target = r
		}
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: `inspect source "priority-source"`, Purpose: "context", AvailableTokens: 32000, MemoryBudgetBytes: ptrInt(4300)}
	old, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	if len(old.Handles) < 2 {
		t.Fatalf("fixture needs competing previews: %+v", old)
	}
	_, err = s.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: old.Package.ReceiptID, Handle: old.Handles[0].Handle}, Destination{"hosted", false})
	requireCode(t, err, "BUDGET_REFUSED")
	req.RequestID = uuid.NewString()
	req = inspectionRequest(t, req)
	index, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	b := index.Package.Semantic.MemoryBudget
	if b == nil || b.Schema != "cairn.memory-budget/3" || b.MinPullBytes <= 0 || len(index.Package.Semantic.Index) == 0 || index.Package.Semantic.Index[0].RecordID != target.RecordID {
		t.Fatalf("whole inspection not reserved: %+v", index)
	}
	if index.BytesRemaining < b.MinPullBytes || len(index.Handles) >= len(old.Handles) {
		t.Fatalf("reservation did not exchange previews: old=%+v new=%+v", old, index)
	}
	var handle string
	for _, h := range index.Handles {
		if h.RecordID == target.RecordID {
			handle = h.Handle
		}
	}
	whole, err := s.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: handle}, Destination{"hosted", false})
	if err != nil || whole.Selection.Record.Body != target.Body {
		t.Fatalf("whole source: %+v %v", whole, err)
	}
	if index.BytesRemaining-whole.BytesRemaining != b.MinPullBytes {
		t.Fatalf("reservation differs from actual charge: reserved=%d charged=%d", b.MinPullBytes, index.BytesRemaining-whole.BytesRemaining)
	}
	_, err = s.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: handle}, Destination{"hosted", false})
	requireCode(t, err, "BUDGET_REFUSED")
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
	if err != nil || replay.Seal != index.Package.Seal {
		t.Fatalf("frozen inspection replay: %v", err)
	}
	for _, available := range []bool{true, false} {
		t.Run(fmt.Sprintf("semantic-ready=%v", available), func(t *testing.T) {
			s.semanticRetriever = func(_ context.Context, r SemanticRankRequest) (SemanticRetrievalResult, error) {
				if !available {
					return SemanticRetrievalResult{}, errors.New("fixture worker unavailable")
				}
				result := SemanticRetrievalResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "inspection-fixture/1", Indexed: len(r.Notes)}
				for _, n := range r.Notes {
					result.Hits = append(result.Hits, SemanticPassageHit{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, 900000}, Span: ByteSpanRequest{Length: len(n.Body)}})
				}
				return result, nil
			}
			semanticReq := req
			semanticReq.RequestID = uuid.NewString()
			semanticReq.Semantic = true
			semanticReq.MemoryBudgetBytes = ptrInt(6000)
			got, err := s.Index(ctx, semanticReq, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			if got.Package.Semantic.MemoryBudget.InspectionStatus != "ready" || got.Package.Semantic.Index[0].RecordID != target.RecordID || (got.Package.Semantic.Discovery.State == "ready") != available {
				t.Fatalf("semantic/fallback ordering or reserve lost: %+v", got.Package.Semantic)
			}
			for _, h := range got.Handles {
				if h.RecordID == target.RecordID {
					handle = h.Handle
				}
			}
			pulled, err := s.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: got.Package.ReceiptID, Handle: handle}, Destination{"hosted", false})
			if err != nil || got.BytesRemaining-pulled.BytesRemaining != got.Package.Semantic.MemoryBudget.MinPullBytes {
				t.Fatalf("semantic whole pull: %+v %v", pulled, err)
			}
			rebuilt, err := s.Recompile(ctx, RecompileRequest{ReceiptID: got.Package.ReceiptID, Query: semanticReq.Query})
			if err != nil || rebuilt.Seal != got.Package.Seal {
				t.Fatalf("semantic replay: %v", err)
			}
		})
	}
}

func TestInspectionAllocationSkipsOversizedUnitsWithoutLosingPagePositions(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	records := []Record{}
	for i, prefix := range []string{"alpha beta gamma ", "alpha beta ", "alpha "} {
		d := projectNote(repo)
		d.Sensitivity = "shareable"
		d.Body = prefix + strings.Repeat("z ", 450)
		if i == 0 {
			d.Body = prefix + strings.Repeat("large ", 6000)
		}
		r, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
		if err != nil {
			t.Fatal(err)
		}
		records = append(records, r)
	}
	req := inspectionRequest(t, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "alpha beta gamma", Purpose: "context", AvailableTokens: 32000, MemoryBudgetBytes: ptrInt(4300), PageOffset: ptrInt(0)})
	index, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	p := index.Package.Semantic
	if p.MemoryBudget.InspectionStatus != "ready" || p.MemoryBudget.SkippedUnits != 1 || p.Omitted["WHOLE_PULL_BUDGET"] != 1 || len(p.Index) != 1 || p.Index[0].RecordID != records[1].RecordID || p.Page.NextOffset == nil || *p.Page.NextOffset != 2 {
		t.Fatalf("first fitting unit/page: %+v", p)
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
	if err != nil || replay.Seal != index.Package.Seal {
		t.Fatalf("skipped replay: %v", err)
	}
	req.RequestID = uuid.NewString()
	req.PageOffset = p.Page.NextOffset
	next, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	if len(next.Package.Semantic.Index) != 1 || next.Package.Semantic.Index[0].RecordID != records[2].RecordID || next.Package.Semantic.Page.NextOffset != nil {
		t.Fatalf("page duplicated or skipped an unconsumed unit: %+v", next.Package.Semantic)
	}
}

func TestInspectionAllocationNoFitKeepsRequiredAndDistinguishesNoCandidates(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "oversized " + strings.Repeat("large ", 6000)
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	d.Kind = "instruction"
	d.Body = "Preserve this complete required instruction."
	required, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, PolicyKey: "whole-inspection", Reason: "fixture required instruction"})
	if err != nil {
		t.Fatal(err)
	}
	req := inspectionRequest(t, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "oversized", Purpose: "context", AvailableTokens: 32000, MemoryBudgetBytes: ptrInt(6000)})
	for _, query := range []string{"oversized", "unmatchedquery"} {
		req.RequestID = uuid.NewString()
		req.Query = query
		index, err := s.Index(ctx, req, Destination{"hosted", false})
		if err != nil {
			t.Fatal(err)
		}
		p := index.Package.Semantic
		want := "no_candidates"
		skipped := 0
		if query == "oversized" {
			want = "no_whole_fits"
			skipped = 1
		}
		if p.MemoryBudget.InspectionStatus != want || p.MemoryBudget.SkippedUnits != skipped || p.MemoryBudget.MinPullBytes != 0 || len(p.Index) != 0 || len(p.Selected) != 1 || p.Selected[0].Record.Body != required.Body {
			t.Fatalf("no-fit/required: %+v", p)
		}
		replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
		if err != nil || replay.Seal != index.Package.Seal {
			t.Fatalf("no-fit replay: %v", err)
		}
	}
	req.RequestID = uuid.NewString()
	req.MemoryBudgetBytes = ptrInt(256)
	_, err = s.Index(ctx, req, Destination{"hosted", false})
	requireCode(t, err, "BUDGET_REFUSED")
}

func TestInspectionAllocationReservesWholeCompetingGroupAndRevalidates(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	records := []Record{}
	for i := range 2 {
		d := projectNote(repo)
		d.Sensitivity = "shareable"
		d.Body = fmt.Sprintf("connection policy %d: 日本語 \"quote\" \\path", i)
		r, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
		if err != nil {
			t.Fatal(err)
		}
		records = append(records, r)
	}
	if _, err := s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{records[0].RecordID, records[1].RecordID}, "fixture disagreement"}); err != nil {
		t.Fatal(err)
	}
	req := inspectionRequest(t, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "connection", Purpose: "context", AvailableTokens: 64000, MemoryBudgetBytes: ptrInt(9000), AdvisoryConflicts: true})
	index, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	if len(index.Package.Semantic.Index) != 2 || index.Package.Semantic.MemoryBudget.InspectionStatus != "ready" {
		t.Fatalf("complete target group: %+v", index)
	}
	indexCost, err := indexMemoryCost(index.Package.Semantic)
	if err != nil {
		t.Fatal(err)
	}
	tight := req
	tight.MemoryBudgetBytes = ptrInt(indexCost + index.Package.Semantic.MemoryBudget.MinPullBytes)
	tight.RequestID = uuid.NewString()
	exact, err := s.Index(ctx, tight, Destination{"hosted", false})
	if err != nil || len(exact.Handles) != 2 || exact.BytesRemaining != exact.Package.Semantic.MemoryBudget.MinPullBytes {
		t.Fatalf("exact whole-group fit: %+v %v", exact, err)
	}
	tight.RequestID = uuid.NewString()
	tight.MemoryBudgetBytes = ptrInt(*tight.MemoryBudgetBytes - 1)
	tooSmall, err := s.Index(ctx, tight, Destination{"hosted", false})
	if err != nil || len(tooSmall.Handles) != 0 || tooSmall.Package.Semantic.MemoryBudget.InspectionStatus != "no_whole_fits" || tooSmall.Package.Semantic.MemoryBudget.SkippedUnits != 1 || tooSmall.Package.Semantic.Omitted["WHOLE_PULL_BUDGET"] != 2 {
		t.Fatalf("one byte short split/retained group: %+v %v", tooSmall, err)
	}
	var handle string
	for _, h := range index.Handles {
		if h.RecordID == index.Package.Semantic.Index[0].RecordID {
			handle = h.Handle
		}
	}
	pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: handle}
	whole, err := s.Expand(ctx, pull, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	if len(whole.Competing) != 1 || index.BytesRemaining-whole.BytesRemaining != index.Package.Semantic.MemoryBudget.MinPullBytes {
		t.Fatalf("whole group cost drift: %+v", whole)
	}
	retry, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil || retry.BytesRemaining != whole.BytesRemaining || retry.CreditsRemaining != whole.CreditsRemaining {
		t.Fatalf("retry replenished: %+v %v", retry, err)
	}
	changed := req
	changed.InspectionPolicy = ""
	_, err = s.Index(ctx, changed, Destination{"hosted", false})
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	req.RequestID = uuid.NewString()
	fresh, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	for _, h := range fresh.Handles {
		if h.RecordID == fresh.Package.Semantic.Index[0].RecordID {
			pull.Handle = h.Handle
		}
	}
	pull.RequestID = uuid.NewString()
	pull.ReceiptID = fresh.Package.ReceiptID
	if _, err := s.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: records[0].RecordID, ExpectedVersion: 1, Repo: repo, Body: "connection policy changed"}); err != nil {
		t.Fatal(err)
	}
	_, err = s.Expand(ctx, pull, Destination{"hosted", false})
	requireCode(t, err, "STALE_HANDLE")
}

func TestInspectionAllocationRejectsUnsupportedIntentBeforeStorage(t *testing.T) {
	for _, tc := range []struct {
		name, mode, purpose, policy string
		budget, reserve             *int
	}{
		{"unknown", "index", "context", "future", ptrInt(4300), nil},
		{"missing cap", "index", "context", firstFittingWhole, nil, nil},
		{"numeric reserve", "index", "context", firstFittingWhole, ptrInt(4300), ptrInt(1)},
		{"body compile", "", "context", firstFittingWhole, ptrInt(4300), nil},
		{"planning", "index", "planning", firstFittingWhole, ptrInt(4300), nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, err := (&Store{}).Compile(context.Background(), CompileRequest{RequestID: uuid.NewString(), Scope: Scope{"repo", "task", "run"}, Query: "x", Mode: tc.mode, Purpose: tc.purpose, AvailableTokens: 32000, MemoryBudgetBytes: tc.budget, MinPullBytes: tc.reserve, InspectionPolicy: tc.policy}, Destination{"hosted", false})
			requireCode(t, err, "INVALID_REQUEST")
		})
	}
}

func TestInspectionAllocationNoFitIsNotEmptyScope(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "oversized " + strings.Repeat("large ", 6000)
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	for _, query := range []string{"oversized", "missing"} {
		req := inspectionRequest(t, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 32000, MemoryBudgetBytes: ptrInt(4300)})
		got, err := s.Index(ctx, req, Destination{"hosted", false})
		if err != nil {
			t.Fatal(err)
		}
		wantStatus, wantInspection := "READY", "no_whole_fits"
		if query == "missing" {
			wantStatus, wantInspection = "SCOPE_EMPTY", "no_candidates"
		}
		if got.Package.Semantic.Status != wantStatus || got.Package.Semantic.MemoryBudget.InspectionStatus != wantInspection {
			t.Fatalf("budget refusal misrepresented as absence: %+v", got.Package.Semantic)
		}
	}
}

// A focused cost-boundary measurement, not a production latency claim. All units
// have equal encoded size; final omission-counter growth invalidates the first
// optimistic exact fit. There are no database, embedding or provider calls.
func BenchmarkInspectionAllocationEqualBoundary(b *testing.B) {
	for _, count := range []int{100, 1000} {
		b.Run(fmt.Sprint(count), func(b *testing.B) {
			candidates := make([]candidate, count)
			makeEvaluations := func() map[string]*CandidateEvaluation {
				m := map[string]*CandidateEvaluation{}
				for _, c := range candidates {
					m[c.selection.Record.RecordID] = &CandidateEvaluation{}
				}
				return m
			}
			for i := range count {
				candidates[i] = candidate{score: 1, selection: Selection{Record: Record{RecordID: fmt.Sprintf("%036d", i), Version: 1, Class: "A", Lifecycle: "active", Sensitivity: "shareable", Draft: Draft{Body: strings.Repeat("inspect source ", 20), Kind: "note", Scope: Scope{"repo", "*", "*"}, ClaimType: "self"}}, Evidence: []Evidence{}, Authority: []Grant{}}}
			}
			p := SemanticPackage{Schema: "cairn.semantic/17", Mode: "index", Purpose: "context", Status: "READY", Ranking: "binary-idf-scope-recency/1", AvailableTokens: 32000, OptionalLimit: 3200, Omitted: omissionCensus(), MemoryBudget: &MemoryBudget{Schema: "cairn.memory-budget/3", Bytes: 6000, InspectionPolicy: firstFittingWhole}}
			one, err := packIndex(p, candidates[:1], makeEvaluations(), "inspect")
			if err != nil {
				b.Fatal(err)
			}
			cost, err := indexMemoryCost(one)
			if err != nil {
				b.Fatal(err)
			}
			p.MemoryBudget.Bytes = cost + one.MemoryBudget.MinPullBytes
			b.ResetTimer()
			for range b.N {
				trial := p
				trial.Omitted = omissionCensus()
				got, err := packIndex(trial, candidates, makeEvaluations(), "inspect")
				if err != nil {
					b.Fatal(err)
				}
				if len(got.Index) != 0 || got.MemoryBudget.InspectionStatus != "no_whole_fits" {
					b.Fatalf("boundary target incorrectly survived: %+v", got.MemoryBudget)
				}
			}
		})
	}
}
