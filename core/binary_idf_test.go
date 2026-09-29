package core

import (
	"context"
	"math"
	"slices"
	"strings"
	"testing"

	"github.com/fxamacker/cbor/v2"
	"github.com/google/uuid"
)

func TestIDFWeightBoundsAndBinaryTerms(t *testing.T) {
	for _, tc := range []struct {
		n, df int
		want  int64
		valid bool
	}{
		{0, 0, 0, true}, {1, 1, 0, true}, {3, 0, 0, true},
		{2, 1, 693147, true}, {3, 1, 1098612, true}, {10000, 1, 9210340, true},
		{10001, 1, 0, false}, {2, 3, 0, false}, {-1, 0, 0, false},
	} {
		got, valid := idfWeight(tc.n, tc.df)
		if got != tc.want || valid != tc.valid {
			t.Fatalf("weight(%d,%d) = %d,%v", tc.n, tc.df, got, valid)
		}
	}
	if math.Round(1_000_000*math.Log(2)) != 693147 {
		t.Fatal("golden log changed")
	}
	terms := rankingTerms("what DATABASE directory provenance provenance", "binary-idf-scope-recency/1")
	if len(terms) != 3 || terms["what"] || !terms["provenance"] {
		t.Fatalf("IDF terms changed: %v", terms)
	}
}

func TestIDFSnapshotRejectsCorruptionAndCountsZeroMatches(t *testing.T) {
	query := "rare common"
	members := []idfMember{
		{ID: "one", Version: 1, BodySHA256: "a", Matches: []string{"rare", "common"}},
		{ID: "two", Version: 1, BodySHA256: "b", Matches: []string{"common"}},
		{ID: "three", Version: 1, BodySHA256: "c"},
	}
	evaluations := map[string]*CandidateEvaluation{}
	for _, m := range members {
		evaluations[m.ID] = &CandidateEvaluation{Facts: &CandidateFacts{BodySHA256: m.BodySHA256}}
	}
	snapshot, err := buildIDF(query, members, evaluations)
	if err != nil || snapshot.N != 3 || len(snapshot.Terms) != 2 {
		t.Fatalf("snapshot: %+v %v", snapshot, err)
	}
	scores := idfScores(snapshot, members)
	if scores["one"] <= scores["two"] || scores["three"] != 0 {
		t.Fatalf("scores: %v", scores)
	}
	for id, score := range scores {
		value := score
		evaluations[id].IDFScore = &value
	}
	if err := verifyFrozenIDF(snapshot, query, members, evaluations); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*IDFSnapshot){
		func(s *IDFSnapshot) { s.N++ },
		func(s *IDFSnapshot) { s.CohortSHA256 = "invalid" },
		func(s *IDFSnapshot) { s.Terms[0].DF++ },
		func(s *IDFSnapshot) { s.Terms[0].Weight = -1 },
		func(s *IDFSnapshot) { s.Terms[0].Weight = 9_210_342 },
		func(s *IDFSnapshot) { slices.Reverse(s.Terms) },
	} {
		copy := *snapshot
		copy.Terms = slices.Clone(snapshot.Terms)
		mutate(&copy)
		if Code(verifyFrozenIDF(&copy, query, members, evaluations)) != "INTEGRITY_FAILURE" {
			t.Fatalf("accepted corrupted snapshot: %+v", copy)
		}
	}
	all := []idfMember{{ID: "one", Version: 1, BodySHA256: "a", Matches: []string{"common"}}, {ID: "two", Version: 1, BodySHA256: "b", Matches: []string{"common"}}}
	allEval := map[string]*CandidateEvaluation{"one": {Facts: &CandidateFacts{}}, "two": {Facts: &CandidateFacts{}}}
	allSnapshot, err := buildIDF("common", all, allEval)
	if err != nil || allSnapshot.N != 2 || allSnapshot.Terms[0].DF != 2 || allSnapshot.Terms[0].Weight != 0 {
		t.Fatalf("ubiquitous term: %+v %v", allSnapshot, err)
	}
	allEval["two"].Facts = nil
	filtered, err := buildIDF("common", all, allEval)
	if err != nil || filtered.N != 1 || filtered.Terms[0].DF != 1 {
		t.Fatalf("ineligible term counted: %+v %v", filtered, err)
	}
}

func TestOrdinaryIDFSearchAndFrozenReplay(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "provenance provenance"
	rare, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = "database directory"
	first, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = "unrelated fruit"
	zero, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Sensitivity = "local"
	d.Body = "provenance"
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	d.Sensitivity = "shareable"
	d.Scope.TaskID = "other"
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	d.Scope.TaskID = "*"
	d.Pins = &Applicability{Revision: strings.Repeat("b", 40)}
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	d.Pins = nil
	d.Kind, d.Body = "instruction", "Always retain required context."
	required, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, PolicyKey: "idf-test", Reason: "Required context"})
	if err != nil {
		t.Fatal(err)
	}
	query := "database directory provenance"
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 32000, Context: &ContextPins{Revision: strings.Repeat("a", 40)}}
	index, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	p := index.Package
	if p.Semantic.Schema != "cairn.semantic/17" || p.Semantic.Ranking != "binary-idf-scope-recency/1" || p.Semantic.IDF == nil || p.Semantic.IDF.N != 5 {
		t.Fatalf("IDF contract: %+v", p.Semantic)
	}
	if len(p.Semantic.Index) < 2 || p.Semantic.Index[0].RecordID != rare.RecordID {
		t.Fatalf("weighted ranking: %+v", p.Semantic.Index)
	}
	if len(p.Semantic.Selected) != 1 || p.Semantic.Selected[0].Record.RecordID != required.RecordID {
		t.Fatalf("mandatory context: %+v", p.Semantic.Selected)
	}
	for _, e := range p.Semantic.Index {
		if e.RecordID == zero.RecordID {
			t.Fatal("zero-match source was admitted")
		}
	}
	explanation, err := s.Explain(ctx, p.ReceiptID)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range explanation.Candidates {
		if e.RecordID == rare.RecordID && (e.IDFScore == nil || e.LexicalMatches != 1) {
			t.Fatalf("rare feature: %+v", e)
		}
		if e.RecordID == zero.RecordID && (e.IDFScore == nil || *e.IDFScore != 0 || e.Reason != "NO_LEXICAL_MATCH") {
			t.Fatalf("zero feature: %+v", e)
		}
	}
	if _, err = s.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: first.RecordID, ExpectedVersion: 1, Repo: repo, Body: "revised body"}); err != nil {
		t.Fatal(err)
	}
	d.Kind, d.Body = "note", "new corpus note"
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: query})
	if err != nil || replay.Seal != p.Seal {
		t.Fatalf("historical replay: %v", err)
	}
	var originalBytes []byte
	if err = s.pool.QueryRow(ctx, `SELECT semantic_body FROM cairn.retrieval_receipt WHERE receipt_id=$1`, p.ReceiptID).Scan(&originalBytes); err != nil {
		t.Fatal(err)
	}
	var altered SemanticPackage
	if err = cbor.Unmarshal(originalBytes, &altered); err != nil {
		t.Fatal(err)
	}
	altered.IDF.Terms[0].Weight++
	corruptBytes, err := cbor.Marshal(altered)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET semantic_body=$2 WHERE receipt_id=$1`, p.ReceiptID, corruptBytes); err != nil {
		t.Fatal(err)
	}
	_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: query})
	if Code(err) != "INTEGRITY_FAILURE" {
		t.Fatalf("tampered sealed weight accepted: %v", err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET semantic_body=$2 WHERE receipt_id=$1`, p.ReceiptID, originalBytes); err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_candidate SET detail=jsonb_set(detail,'{idf_score}','42') WHERE receipt_id=$1 AND record_id=$2`, p.ReceiptID, rare.RecordID); err != nil {
		t.Fatal(err)
	}
	_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: query})
	if Code(err) != "INTEGRITY_FAILURE" {
		t.Fatalf("tampered score accepted: %v", err)
	}

	// A positive raw match remains an ordinary lexical hit.
	req.RequestID, req.Query = uuid.NewString(), "database"
	zeroWeight, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	if len(zeroWeight.Package.Semantic.Index) == 0 {
		t.Fatal("positive raw match was dropped")
	}
}
