package core

import (
	"fmt"
	"testing"
)

func TestLookupSchemaSpecCatalogsSupportedCompilerVersions(t *testing.T) {
	for _, unsupported := range []string{"", "cairn.semantic/0", "cairn.semantic/1", "cairn.semantic/2", "cairn.semantic/20", "cairn.semantic/99", "unknown/1"} {
		if _, ok := LookupSchemaSpec(unsupported); ok {
			t.Fatalf("unexpected schema spec for %q", unsupported)
		}
	}

	specs := AllSchemaSpecs()
	if len(specs) != 17 {
		t.Fatalf("expected 17 compiler schema specs (3..19), got %d", len(specs))
	}

	for v := 3; v <= 19; v++ {
		name := fmt.Sprintf("cairn.semantic/%d", v)
		spec, ok := LookupSchemaSpec(name)
		if !ok {
			t.Fatalf("missing schema spec for %s", name)
		}
		if spec.Schema != name || spec.Version != v {
			t.Fatalf("mismatched identity for %s: %+v", name, spec)
		}

		wantSummary := IndexSummaryPrefix
		if v >= 5 && v <= 7 {
			wantSummary = IndexSummaryExcerpt
		} else if v >= 8 {
			wantSummary = IndexSummaryPreviewSpan
		}
		if spec.IndexSummaryMode != wantSummary {
			t.Fatalf("%s summary mode: got %v, want %v", name, spec.IndexSummaryMode, wantSummary)
		}

		if spec.RequiresBrowse != (v == 6) {
			t.Fatalf("%s RequiresBrowse=%v", name, spec.RequiresBrowse)
		}
		wantBrowse := v == 6 || v == 8 || v == 9 || v == 10 || v == 13 || v == 14
		if spec.SupportsBrowse != wantBrowse {
			t.Fatalf("%s SupportsBrowse=%v, want %v", name, spec.SupportsBrowse, wantBrowse)
		}
		if spec.RequiresSemanticDiscovery != (v == 7) || spec.SupportsSemanticDiscovery != (v >= 7 && v <= 16) {
			t.Fatalf("%s semantic discovery flags: %+v", name, spec)
		}
		if spec.RequiresKinds != (v == 9) || spec.SupportsKinds != (v >= 9) {
			t.Fatalf("%s kinds flags: %+v", name, spec)
		}
		if spec.RequiresTaskPhase != (v == 10) || spec.SupportsTaskPhase != (v >= 10) {
			t.Fatalf("%s task phase flags: %+v", name, spec)
		}
		if spec.RequiresPage != (v == 11) || spec.SupportsPage != (v >= 11) {
			t.Fatalf("%s search page flags: %+v", name, spec)
		}
		if spec.RequiresErrorSignature != (v == 12) || spec.SupportsErrorSignature != (v >= 12) {
			t.Fatalf("%s error signature flags: %+v", name, spec)
		}
		if spec.SupportsEntities != (v >= 13) {
			t.Fatalf("%s SupportsEntities=%v", name, spec.SupportsEntities)
		}
		if spec.RequiresAdvisoryConflicts != (v == 14) || spec.SupportsAdvisoryConflicts != (v >= 14) {
			t.Fatalf("%s advisory conflict flags: %+v", name, spec)
		}
		if spec.IsPureIDF != (v == 17) || spec.IsIndexedIDF != (v == 19) {
			t.Fatalf("%s IDF flags: %+v", name, spec)
		}
		if spec.RequiresQueryProjection != (v == 18) || spec.SupportsQueryProjection != (v == 18 || v == 19) {
			t.Fatalf("%s query projection flags: %+v", name, spec)
		}
	}
}

func TestSchemaSpecRankingAndValidationHelpers(t *testing.T) {
	supported := []string{
		"lexical-scope-recency/1", "lexical-scope-recency/2", "lexical-scope-recency/3", "lexical-scope-recency/4",
		"lexical-scope-recency/5", "lexical-scope-recency/6", "lexical-scope-recency/7",
		"semantic-scope-recency/1", "semantic-scope-recency/2", "semantic-scope-recency/3", "semantic-scope-recency/4",
		"hybrid-scope-recency/1", "hybrid-scope-recency/2", "hybrid-scope-recency/3", "hybrid-scope-recency/4",
		"interleaved-scope-recency/1", "interleaved-scope-recency/2", "interleaved-scope-recency/3", "interleaved-scope-recency/4",
		"interleaved-scope-recency/5", "interleaved-scope-recency/6", "interleaved-scope-recency/7", "interleaved-scope-recency/8",
		"binary-idf-scope-recency/1", "binary-idf-scope-recency/2", "binary-idf-scope-recency/3", "binary-idf-scope-recency/4",
		"binary-idf-scope-recency/5", "binary-idf-scope-recency/6", "binary-idf-scope-recency/7", "binary-idf-scope-recency/8",
	}
	for _, r := range supported {
		if !SupportedRanking(r) {
			t.Fatalf("expected SupportedRanking(%q) = true", r)
		}
	}
	for _, bad := range []string{"", "lexical-scope-recency/0", "lexical-scope-recency/8", "semantic-scope-recency/5", "hybrid-scope-recency/5", "interleaved-scope-recency/9", "binary-idf-scope-recency/9"} {
		if SupportedRanking(bad) {
			t.Fatalf("expected SupportedRanking(%q) = false", bad)
		}
	}

	if schemaIndexSummaryMode(DistinctQuerySchema) != IndexSummaryPreviewSpan {
		t.Fatal("distinct query wrapper must use preview-span summary mode")
	}
	if schemaIndexSummaryMode("unknown/1") != IndexSummaryPrefix {
		t.Fatal("unknown schema must default to prefix summary mode")
	}

	s15, _ := LookupSchemaSpec(SchemaSemantic15)
	s16, _ := LookupSchemaSpec(SchemaSemantic16)
	s19, _ := LookupSchemaSpec(SchemaSemantic19)
	if !s15.MatchesIndexedPackage(SemanticPackage{Schema: SchemaSemantic15, Ranking: "hybrid-scope-recency/1"}) {
		t.Fatal("schema 15 must accept hybrid ranking")
	}
	if s15.MatchesIndexedPackage(SemanticPackage{Schema: SchemaSemantic15, Ranking: "interleaved-scope-recency/1"}) {
		t.Fatal("schema 15 must reject interleaved ranking")
	}
	if !s16.MatchesIndexedPackage(SemanticPackage{Schema: SchemaSemantic16, Ranking: "interleaved-scope-recency/1"}) {
		t.Fatal("schema 16 must accept non-IDF interleaved ranking")
	}
	if s16.MatchesIndexedPackage(SemanticPackage{Schema: SchemaSemantic16, Ranking: "interleaved-scope-recency/5"}) {
		t.Fatal("schema 16 must reject indexed-IDF ranking")
	}
	if !s19.MatchesIndexedPackage(SemanticPackage{Schema: SchemaSemantic19, Ranking: "interleaved-scope-recency/5", IDF: &IDFSnapshot{}}) {
		t.Fatal("schema 19 must accept indexed-IDF ranking with IDF snapshot")
	}
	if s19.MatchesIndexedPackage(SemanticPackage{Schema: SchemaSemantic19, Ranking: "interleaved-scope-recency/5"}) {
		t.Fatal("schema 19 must require IDF snapshot")
	}
}

func TestSchemaSpecValidateRecompileContract(t *testing.T) {
	basePkg := func(schema, ranking string) SemanticPackage {
		return SemanticPackage{
			Schema:          schema,
			Mode:            "index",
			Purpose:         "context",
			Policy:          "local-loop/1",
			Ranking:         ranking,
			AvailableTokens: 8000,
			OptionalLimit:   800,
		}
	}

	// Schema 6 requires a valid browse page.
	s6, _ := LookupSchemaSpec(SchemaSemantic6)
	requireCode(t, s6.ValidateRecompileContract(basePkg(SchemaSemantic6, "lexical-scope-recency/4"), RecompileRequest{}), "INTEGRITY_FAILURE")
	validBrowse := basePkg(SchemaSemantic6, "lexical-scope-recency/4")
	validBrowse.Browse = &BrowsePage{Offset: 0}
	if err := s6.ValidateRecompileContract(validBrowse, RecompileRequest{}); err != nil {
		t.Fatalf("valid schema 6 browse failed: %v", err)
	}

	// Schema 7 cannot carry a browse page.
	s7, _ := LookupSchemaSpec(SchemaSemantic7)
	badBrowse7 := basePkg(SchemaSemantic7, "lexical-scope-recency/4")
	badBrowse7.Browse = &BrowsePage{Offset: 0}
	requireCode(t, s7.ValidateRecompileContract(badBrowse7, RecompileRequest{}), "INTEGRITY_FAILURE")

	// Schema 9 requires kinds; schema 8 forbids kinds.
	s8, _ := LookupSchemaSpec(SchemaSemantic8)
	s9, _ := LookupSchemaSpec(SchemaSemantic9)
	requireCode(t, s9.ValidateRecompileContract(basePkg(SchemaSemantic9, "lexical-scope-recency/4"), RecompileRequest{Query: "q"}), "INTEGRITY_FAILURE")
	withKinds := basePkg(SchemaSemantic8, "lexical-scope-recency/4")
	withKinds.Kinds = []string{"lesson"}
	requireCode(t, s8.ValidateRecompileContract(withKinds, RecompileRequest{Query: "q"}), "INTEGRITY_FAILURE")
	withKinds.Schema = SchemaSemantic9
	if err := s9.ValidateRecompileContract(withKinds, RecompileRequest{Query: "q"}); err != nil {
		t.Fatalf("valid schema 9 kinds failed: %v", err)
	}

	// Schema 10 requires task phase; schema 9 forbids task phase.
	s10, _ := LookupSchemaSpec(SchemaSemantic10)
	requireCode(t, s10.ValidateRecompileContract(basePkg(SchemaSemantic10, "lexical-scope-recency/4"), RecompileRequest{Query: "q"}), "INTEGRITY_FAILURE")
	withPhase := basePkg(SchemaSemantic9, "lexical-scope-recency/4")
	withPhase.Kinds = []string{"lesson"}
	withPhase.Context = &ContextPins{TaskPhase: "validation"}
	requireCode(t, s9.ValidateRecompileContract(withPhase, RecompileRequest{Query: "q"}), "INTEGRITY_FAILURE")
	withPhase.Schema = SchemaSemantic10
	if err := s10.ValidateRecompileContract(withPhase, RecompileRequest{Query: "q"}); err != nil {
		t.Fatalf("valid schema 10 task phase failed: %v", err)
	}

	// Schema 11 requires search page; schema 10 forbids search page.
	s11, _ := LookupSchemaSpec(SchemaSemantic11)
	requireCode(t, s11.ValidateRecompileContract(basePkg(SchemaSemantic11, "lexical-scope-recency/4"), RecompileRequest{Query: "q"}), "INTEGRITY_FAILURE")
	withPage := basePkg(SchemaSemantic10, "lexical-scope-recency/4")
	withPage.Context = &ContextPins{TaskPhase: "validation"}
	withPage.Page = &BrowsePage{Offset: 0}
	requireCode(t, s10.ValidateRecompileContract(withPage, RecompileRequest{Query: "q"}), "INTEGRITY_FAILURE")
	withPage.Schema = SchemaSemantic11
	if err := s11.ValidateRecompileContract(withPage, RecompileRequest{Query: "q"}); err != nil {
		t.Fatalf("valid schema 11 search page failed: %v", err)
	}
}

func TestSchemaResolutionAndSharedCandidateEvaluationPipeline(t *testing.T) {
	sig := "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	cases := []struct {
		name        string
		req         CompileRequest
		wantCollect string
		wantPromote string
		wantRanking string
		wantIDF     bool
	}{
		{
			name:        "body package default",
			req:         CompileRequest{Purpose: "context", Query: "storage"},
			wantCollect: SchemaSemantic3,
			wantPromote: SchemaSemantic8,
			wantRanking: "lexical-scope-recency/4",
		},
		{
			name:        "quoted literal index",
			req:         CompileRequest{Mode: "index", Purpose: "context", Query: `"exact" storage`, Semantic: true},
			wantCollect: SchemaSemantic3,
			wantPromote: SchemaSemantic8,
			wantRanking: "lexical-scope-recency/5",
		},
		{
			name:        "kinds and page offset",
			req:         CompileRequest{Mode: "index", Purpose: "context", Kinds: []string{"lesson"}, PageOffset: new(int), Semantic: true, Query: "storage"},
			wantCollect: SchemaSemantic9,
			wantPromote: SchemaSemantic11,
			wantRanking: "lexical-scope-recency/4",
		},
		{
			name:        "error signature",
			req:         CompileRequest{Mode: "index", Purpose: "context", ErrorSignature: sig},
			wantCollect: SchemaSemantic12,
			wantPromote: SchemaSemantic12,
			wantRanking: "lexical-scope-recency/6",
		},
		{
			name:        "entities and binary IDF",
			req:         CompileRequest{Mode: "index", Purpose: "context", Query: "storage", Entities: []EntityRef{{Kind: "file", Name: "core/compiler.go"}}},
			wantCollect: SchemaSemantic17,
			wantPromote: SchemaSemantic17,
			wantRanking: "lexical-scope-recency/7",
			wantIDF:     true,
		},
	}

	for _, tc := range cases {
		p := SemanticPackage{Omitted: omissionCensus()}
		gotIDF := resolveCompileSchemaAndRanking(&p, tc.req, queryLiterals(tc.req.Query))
		if gotIDF != tc.wantIDF || p.Schema != tc.wantCollect || p.Ranking != tc.wantRanking {
			t.Fatalf("%s collect: got schema=%s ranking=%s idf=%v", tc.name, p.Schema, p.Ranking, gotIDF)
		}
		promoteIndexSchema(&p, tc.req)
		if p.Schema != tc.wantPromote {
			t.Fatalf("%s promote: got %s, want %s", tc.name, p.Schema, tc.wantPromote)
		}
	}

	// Verify shared candidate admission parity between live and recompile modes.
	rec := Record{RecordID: "rec-1", Version: 1, Class: "A", Draft: Draft{Kind: "lesson", Body: "storage engine migration note", Scope: Scope{Repo: "r", TaskID: "*", RunID: "*"}}}
	lex := newRankingLexicon("storage", "lexical-scope-recency/4")
	features := evaluateCandidateFeatures(rec, false, nil, nil, nil, lex)
	if features.score != 1 || features.specificity != 0 {
		t.Fatalf("unexpected candidate features: %+v", features)
	}
	livePkg := SemanticPackage{Schema: SchemaSemantic8, Ranking: "lexical-scope-recency/4", Omitted: omissionCensus()}
	replayPkg := SemanticPackage{Schema: SchemaSemantic8, Ranking: "lexical-scope-recency/4", Omitted: omissionCensus()}
	var liveCandidates, replayCandidates []candidate
	admitEvaluatedCandidate(&livePkg, Selection{Record: rec}, &CandidateEvaluation{}, features, lex.terms, "storage", false, false, false, false, false, false, nil, nil, &liveCandidates)
	admitEvaluatedCandidate(&replayPkg, Selection{Record: rec}, &CandidateEvaluation{}, features, lex.terms, "storage", false, false, false, false, false, true, nil, nil, &replayCandidates)
	if len(liveCandidates) != 1 || len(replayCandidates) != 1 || liveCandidates[0].selection.Reason != replayCandidates[0].selection.Reason || liveCandidates[0].score != replayCandidates[0].score {
		t.Fatalf("live/replay candidate parity mismatch: %+v vs %+v", liveCandidates, replayCandidates)
	}
}

func TestSchemaSpecLiveCompileAndRecompileParityAcrossVersions(t *testing.T) {
	s, _ := testOperator(t)
	ctx := t.Context()
	repo := "repo-" + t.Name()
	d := projectNote(repo)
	d.Kind = "lesson"
	d.Body = "The storage engine caches validation results in the index directory."
	if _, err := s.Create(ctx, CreateRequest{RequestID: "a0000000-0000-4000-8000-000000000001", Draft: d}); err != nil {
		t.Fatal(err)
	}
	dest := Destination{Name: "local", AllowLocal: true}
	offsetZero := 0

	cases := []struct {
		name       string
		req        CompileRequest
		wantSchema string
	}{
		{
			name:       "schema 3 body without kinds",
			req:        CompileRequest{RequestID: "a0000000-0000-4000-8000-000000000002", Scope: Scope{Repo: repo, TaskID: "t", RunID: "r"}, Query: "storage", Purpose: "context", AvailableTokens: 16000},
			wantSchema: SchemaSemantic3,
		},
		{
			name:       "schema 9 body with kinds",
			req:        CompileRequest{RequestID: "a0000000-0000-4000-8000-000000000003", Scope: Scope{Repo: repo, TaskID: "t", RunID: "r"}, Query: "storage", Purpose: "context", Kinds: []string{"lesson"}, AvailableTokens: 16000},
			wantSchema: SchemaSemantic9,
		},
		{
			name:       "schema 10 index with task phase",
			req:        CompileRequest{RequestID: "a0000000-0000-4000-8000-000000000004", Mode: "index", Scope: Scope{Repo: repo, TaskID: "t", RunID: "r"}, Purpose: "context", Context: &ContextPins{TaskPhase: "validation"}, BrowseOffset: &offsetZero, AvailableTokens: 16000},
			wantSchema: SchemaSemantic10,
		},
		{
			name:       "schema 13 index with entity",
			req:        CompileRequest{RequestID: "a0000000-0000-4000-8000-000000000005", Mode: "index", Scope: Scope{Repo: repo, TaskID: "t", RunID: "r"}, Purpose: "context", Entities: []EntityRef{{Kind: "file", Name: "core/compiler.go"}}, AvailableTokens: 16000},
			wantSchema: SchemaSemantic13,
		},
		{
			name:       "schema 17 binary IDF index",
			req:        CompileRequest{RequestID: "a0000000-0000-4000-8000-000000000006", Mode: "index", Scope: Scope{Repo: repo, TaskID: "t", RunID: "r"}, Query: `"validation" storage`, Purpose: "context", PageOffset: &offsetZero, Entities: []EntityRef{{Kind: "file", Name: "core/compiler.go"}}, AvailableTokens: 16000},
			wantSchema: SchemaSemantic17,
		},
	}

	for _, tc := range cases {
		pkg, err := s.Compile(ctx, tc.req, dest)
		if err != nil {
			t.Fatalf("%s compile: %v", tc.name, err)
		}
		if pkg.Semantic.Schema != tc.wantSchema {
			t.Fatalf("%s schema: got %s, want %s", tc.name, pkg.Semantic.Schema, tc.wantSchema)
		}
		if _, ok := LookupSchemaSpec(pkg.Semantic.Schema); !ok {
			t.Fatalf("%s produced unregistered schema %s", tc.name, pkg.Semantic.Schema)
		}
		replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: pkg.ReceiptID, Query: tc.req.Query, Entities: tc.req.Entities})
		if err != nil {
			t.Fatalf("%s recompile: %v", tc.name, err)
		}
		if replay.Seal != pkg.Seal {
			t.Fatalf("%s seal mismatch: got %s, want %s", tc.name, replay.Seal, pkg.Seal)
		}
	}
}
