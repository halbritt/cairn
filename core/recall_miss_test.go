package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func promptHex(text string) string {
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:])
}

func TestRecallMissCandidatesLoggedAndDeliveredExcluded(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "recall-miss:modes"})
	repo := uuid.NewString()

	// Create two high-overlap notes (>= 3 lexical matches) and one low-overlap note.
	makeNote := func(body string) Record {
		t.Helper()
		draft := projectNote(repo)
		draft.Body = body
		rec, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
		if err != nil {
			t.Fatal(err)
		}
		return rec
	}
	delivered := makeNote("alpha bravo charlie delta delivered guidance")
	omitted := makeNote("alpha bravo charlie echo omitted guidance " + strings.Repeat("padding ", 80))
	_ = makeNote("alpha unrelated guidance")

	// 1. OPTIONAL_BUDGET mode: tight AvailableTokens admits the shorter note and omits the longer one.
	pkg, err := s.Compile(ctx, CompileRequest{
		RequestID:       uuid.NewString(),
		Scope:           Scope{Repo: repo, TaskID: "t1", RunID: "r1"},
		Query:           "alpha bravo charlie delta",
		Purpose:         "context",
		AvailableTokens: 8000,
	}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	if len(pkg.Semantic.Selected) != 1 || pkg.Semantic.Selected[0].Record.RecordID != delivered.RecordID || pkg.Semantic.Omitted["OPTIONAL_BUDGET"] != 2 {
		t.Fatalf("expected 1 selected and 2 OPTIONAL_BUDGET omissions, got selected=%d omitted=%+v", len(pkg.Semantic.Selected), pkg.Semantic.Omitted)
	}
	obsReq := recallBase(repo, pkg.ReceiptID)
	obsReq.PromptSHA256 = promptHex("prompt-budget-1")
	if _, err := s.ObserveRecall(ctx, obsReq); err != nil {
		t.Fatal(err)
	}
	report, err := s.RecallMissReport(ctx, repo, 7)
	if err != nil {
		t.Fatal(err)
	}
	if report.CandidateCount != 1 || report.Candidates[0].RecordID != omitted.RecordID || report.Candidates[0].LexicalMatches < DefaultRecallMissMinLexicalMatches {
		t.Fatalf("expected only omitted high-overlap note in recall_miss_candidate, got %+v", report)
	}

	// 2. agent_tools & ambient modes with Index + Expand: expanded note is excluded, unexpanded note is logged.
	idx, err := s.Index(ctx, CompileRequest{
		RequestID:       uuid.NewString(),
		Scope:           Scope{Repo: repo, TaskID: "t2", RunID: "r2"},
		Query:           "alpha bravo charlie",
		Purpose:         "context",
		AvailableTokens: 64000,
	}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	var deliveredHandle string
	for _, h := range idx.Handles {
		if h.RecordID == delivered.RecordID {
			deliveredHandle = h.Handle
		}
	}
	if _, err := s.Expand(ctx, ExpandRequest{
		RequestID: uuid.NewString(),
		ReceiptID: idx.Package.ReceiptID,
		Handle:    deliveredHandle,
	}, Destination{Name: "local", AllowLocal: true}); err != nil {
		t.Fatal(err)
	}
	for _, method := range []string{"agent_tools", "ambient"} {
		mReq := recallBase(repo, idx.Package.ReceiptID)
		mReq.Method = method
		mReq.PromptSHA256 = promptHex("prompt-" + method)
		if _, err := s.ObserveRecall(ctx, mReq); err != nil {
			t.Fatal(err)
		}
	}
	report, err = s.RecallMissReport(ctx, repo, 7)
	if err != nil {
		t.Fatal(err)
	}
	if report.CandidateCount != 3 || report.DistinctNotes != 1 {
		t.Fatalf("expected 3 total candidates all for the single unexpanded/omitted note, got %+v", report)
	}
	for _, c := range report.Candidates {
		if c.RecordID == delivered.RecordID {
			t.Fatalf("delivered/expanded note %s must be excluded from recall_miss_candidate: %+v", delivered.RecordID, c)
		}
	}
}

func TestRecallMissIdempotencyAndDocketSevenDayReport(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "recall-miss:docket"})
	repo := uuid.NewString()

	notes := make([]Record, 0, 3)
	for i := range 3 {
		draft := projectNote(repo)
		draft.Body = fmt.Sprintf("reexplain_one reexplain_two reexplain_three note_%d", i)
		rec, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
		if err != nil {
			t.Fatal(err)
		}
		notes = append(notes, rec)
	}
	idx, err := s.Index(ctx, CompileRequest{
		RequestID:       uuid.NewString(),
		Scope:           Scope{Repo: repo, TaskID: "task-idem", RunID: "run-idem"},
		Query:           "reexplain_one reexplain_two reexplain_three",
		Purpose:         "context",
		AvailableTokens: 64000,
	}, Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	digest := promptHex("owner-reexplained-same-prompt")
	req := recallBase(repo, idx.Package.ReceiptID)
	req.Method = "agent_tools"
	req.PromptSHA256 = digest
	if _, err := s.ObserveRecall(ctx, req); err != nil {
		t.Fatal(err)
	}
	// Exact request replay AND repeated observations with new RequestIDs for the same prompt digest must stay at 3 (never 3 -> 6 -> 9).
	if _, err := s.ObserveRecall(ctx, req); err != nil {
		t.Fatal(err)
	}
	for range 2 {
		retry := req
		retry.RequestID = uuid.NewString()
		if _, err := s.ObserveRecall(ctx, retry); err != nil {
			t.Fatal(err)
		}
	}
	var rawCount int
	if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.recall_miss_candidate WHERE repo=$1`, repo).Scan(&rawCount); err != nil || rawCount != 3 {
		t.Fatalf("expected 3 deduplicated candidates (never 3->6->9), got %d (err=%v)", rawCount, err)
	}
	docket, err := s.Docket(ctx, repo)
	if err != nil || len(docket.Items) != 3 {
		t.Fatalf("expected 3 RECALL_MISS_CANDIDATE items in docket, got %+v (err=%v)", docket, err)
	}
	for _, item := range docket.Items {
		if item.Reason != "RECALL_MISS_CANDIDATE" || item.ReceiptID != idx.Package.ReceiptID || item.Action != RecallMissDocketAction {
			t.Fatalf("unexpected docket item: %+v", item)
		}
	}

	// Confirm one candidate by expanding it via s.Expand and check the 7-day report precision summary.
	if _, err := s.Expand(ctx, ExpandRequest{
		RequestID: uuid.NewString(),
		ReceiptID: idx.Package.ReceiptID,
		Handle:    idx.Handles[0].Handle,
	}, Destination{Name: "local", AllowLocal: true}); err != nil {
		t.Fatal(err)
	}
	summary, err := s.RecallMissReport(ctx, repo, 0)
	if err != nil {
		t.Fatal(err)
	}
	if summary.WindowDays != 7 || summary.Observations != 3 || summary.ObservationsWithCandidates != 1 ||
		summary.CandidateCount != 3 || summary.DistinctNotes != 3 || summary.ConfirmedCount != 1 ||
		summary.UnconfirmedCount != 2 || summary.PendingCount != 2 || summary.Precision == nil || math.Abs(*summary.Precision-1.0/3.0) > 1e-9 {
		t.Fatalf("unexpected 7-day recall miss summary: %+v", summary)
	}
}
