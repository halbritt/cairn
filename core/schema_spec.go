package core

import (
	"fmt"
	"slices"
	"strings"
)

type IndexSummaryMode int

const (
	IndexSummaryPrefix IndexSummaryMode = iota
	IndexSummaryExcerpt
	IndexSummaryPreviewSpan
)

type IndexedRankingKind int

const (
	IndexedRankingNone IndexedRankingKind = iota
	IndexedRankingHybrid
	IndexedRankingInterleaved
	IndexedRankingInterleavedIDF
)

const (
	SchemaSemantic3  = "cairn.semantic/3"
	SchemaSemantic4  = "cairn.semantic/4"
	SchemaSemantic5  = "cairn.semantic/5"
	SchemaSemantic6  = "cairn.semantic/6"
	SchemaSemantic7  = "cairn.semantic/7"
	SchemaSemantic8  = "cairn.semantic/8"
	SchemaSemantic9  = "cairn.semantic/9"
	SchemaSemantic10 = "cairn.semantic/10"
	SchemaSemantic11 = "cairn.semantic/11"
	SchemaSemantic12 = "cairn.semantic/12"
	SchemaSemantic13 = "cairn.semantic/13"
	SchemaSemantic14 = "cairn.semantic/14"
	SchemaSemantic15 = "cairn.semantic/15"
	SchemaSemantic16 = "cairn.semantic/16"
	SchemaSemantic17 = "cairn.semantic/17"
	SchemaSemantic18 = "cairn.semantic/18"
	SchemaSemantic19 = "cairn.semantic/19"
)

// SchemaSpec owns the declarative compiler capabilities and historical
// validation contract for each sealed semantic package schema version.
type SchemaSpec struct {
	Schema                    string
	Version                   int
	IndexSummaryMode          IndexSummaryMode
	IndexedRanking            IndexedRankingKind
	SupportsBrowse            bool
	RequiresBrowse            bool
	SupportsPage              bool
	RequiresPage              bool
	SupportsKinds             bool
	RequiresKinds             bool
	SupportsTaskPhase         bool
	RequiresTaskPhase         bool
	SupportsErrorSignature    bool
	RequiresErrorSignature    bool
	SupportsEntities          bool
	SupportsAdvisoryConflicts bool
	RequiresAdvisoryConflicts bool
	SupportsSemanticDiscovery bool
	RequiresSemanticDiscovery bool
	SupportsQueryProjection   bool
	RequiresQueryProjection   bool
	IsPureIDF                 bool
	IsIndexedIDF              bool
}

var schemaSpecTable = []SchemaSpec{
	{Schema: SchemaSemantic3, Version: 3, IndexSummaryMode: IndexSummaryPrefix},
	{Schema: SchemaSemantic4, Version: 4, IndexSummaryMode: IndexSummaryPrefix},
	{Schema: SchemaSemantic5, Version: 5, IndexSummaryMode: IndexSummaryExcerpt},
	{Schema: SchemaSemantic6, Version: 6, IndexSummaryMode: IndexSummaryExcerpt, SupportsBrowse: true, RequiresBrowse: true},
	{Schema: SchemaSemantic7, Version: 7, IndexSummaryMode: IndexSummaryExcerpt, SupportsSemanticDiscovery: true, RequiresSemanticDiscovery: true},
	{Schema: SchemaSemantic8, Version: 8, IndexSummaryMode: IndexSummaryPreviewSpan, SupportsBrowse: true, SupportsSemanticDiscovery: true},
	{Schema: SchemaSemantic9, Version: 9, IndexSummaryMode: IndexSummaryPreviewSpan, SupportsBrowse: true, SupportsKinds: true, RequiresKinds: true, SupportsSemanticDiscovery: true},
	{Schema: SchemaSemantic10, Version: 10, IndexSummaryMode: IndexSummaryPreviewSpan, SupportsBrowse: true, SupportsKinds: true, SupportsTaskPhase: true, RequiresTaskPhase: true, SupportsSemanticDiscovery: true},
	{Schema: SchemaSemantic11, Version: 11, IndexSummaryMode: IndexSummaryPreviewSpan, SupportsPage: true, RequiresPage: true, SupportsKinds: true, SupportsTaskPhase: true, SupportsSemanticDiscovery: true},
	{Schema: SchemaSemantic12, Version: 12, IndexSummaryMode: IndexSummaryPreviewSpan, SupportsPage: true, SupportsKinds: true, SupportsTaskPhase: true, SupportsErrorSignature: true, RequiresErrorSignature: true, SupportsSemanticDiscovery: true},
	{Schema: SchemaSemantic13, Version: 13, IndexSummaryMode: IndexSummaryPreviewSpan, SupportsBrowse: true, SupportsPage: true, SupportsKinds: true, SupportsTaskPhase: true, SupportsErrorSignature: true, SupportsEntities: true, SupportsSemanticDiscovery: true},
	{Schema: SchemaSemantic14, Version: 14, IndexSummaryMode: IndexSummaryPreviewSpan, SupportsBrowse: true, SupportsPage: true, SupportsKinds: true, SupportsTaskPhase: true, SupportsErrorSignature: true, SupportsEntities: true, SupportsAdvisoryConflicts: true, RequiresAdvisoryConflicts: true, SupportsSemanticDiscovery: true},
	{Schema: SchemaSemantic15, Version: 15, IndexSummaryMode: IndexSummaryPreviewSpan, IndexedRanking: IndexedRankingHybrid, SupportsPage: true, SupportsKinds: true, SupportsTaskPhase: true, SupportsErrorSignature: true, SupportsEntities: true, SupportsAdvisoryConflicts: true, SupportsSemanticDiscovery: true},
	{Schema: SchemaSemantic16, Version: 16, IndexSummaryMode: IndexSummaryPreviewSpan, IndexedRanking: IndexedRankingInterleaved, SupportsPage: true, SupportsKinds: true, SupportsTaskPhase: true, SupportsErrorSignature: true, SupportsEntities: true, SupportsAdvisoryConflicts: true, SupportsSemanticDiscovery: true},
	{Schema: SchemaSemantic17, Version: 17, IndexSummaryMode: IndexSummaryPreviewSpan, SupportsPage: true, SupportsKinds: true, SupportsTaskPhase: true, SupportsErrorSignature: true, SupportsEntities: true, SupportsAdvisoryConflicts: true, IsPureIDF: true},
	{Schema: SchemaSemantic18, Version: 18, IndexSummaryMode: IndexSummaryPreviewSpan, IndexedRanking: IndexedRankingInterleaved, SupportsPage: true, SupportsKinds: true, SupportsTaskPhase: true, SupportsErrorSignature: true, SupportsEntities: true, SupportsAdvisoryConflicts: true, SupportsQueryProjection: true, RequiresQueryProjection: true},
	{Schema: SchemaSemantic19, Version: 19, IndexSummaryMode: IndexSummaryPreviewSpan, IndexedRanking: IndexedRankingInterleavedIDF, SupportsPage: true, SupportsKinds: true, SupportsTaskPhase: true, SupportsErrorSignature: true, SupportsEntities: true, SupportsAdvisoryConflicts: true, SupportsQueryProjection: true, IsIndexedIDF: true},
}

var schemaSpecsByName = func() map[string]SchemaSpec {
	m := make(map[string]SchemaSpec, len(schemaSpecTable))
	for _, spec := range schemaSpecTable {
		m[spec.Schema] = spec
	}
	return m
}()

func LookupSchemaSpec(schema string) (SchemaSpec, bool) {
	spec, ok := schemaSpecsByName[schema]
	return spec, ok
}

func AllSchemaSpecs() []SchemaSpec {
	return slices.Clone(schemaSpecTable)
}

func hasSemanticScopeRanking(ranking string) bool {
	return ranking == "semantic-scope-recency/1" || ranking == "semantic-scope-recency/2" || ranking == "semantic-scope-recency/3" || ranking == "semantic-scope-recency/4"
}

func hasFallbackLexicalRanking(ranking string) bool {
	return ranking == "lexical-scope-recency/4" || ranking == "lexical-scope-recency/5" || ranking == "lexical-scope-recency/6" || ranking == "lexical-scope-recency/7"
}

func SupportedRanking(ranking string) bool {
	return ranking == "lexical-scope-recency/1" ||
		ranking == "lexical-scope-recency/2" ||
		ranking == "lexical-scope-recency/3" ||
		ranking == "lexical-scope-recency/4" ||
		ranking == "semantic-scope-recency/1" ||
		hasLiteralRanking(ranking) ||
		hasIndexedRanking(ranking) ||
		hasIDFRanking(ranking)
}

func resolveSemanticScopeRanking(current string) string {
	switch {
	case hasEntityRanking(current):
		return "semantic-scope-recency/4"
	case hasFailureRanking(current):
		return "semantic-scope-recency/3"
	case hasLiteralRanking(current):
		return "semantic-scope-recency/2"
	default:
		return "semantic-scope-recency/1"
	}
}

func resolveInterleavedRanking(current string) string {
	switch {
	case hasEntityRanking(current):
		return "interleaved-scope-recency/4"
	case hasFailureRanking(current):
		return "interleaved-scope-recency/3"
	case hasLiteralRanking(current):
		return "interleaved-scope-recency/2"
	default:
		return "interleaved-scope-recency/1"
	}
}

func schemaIndexSummaryMode(schema string) IndexSummaryMode {
	if schema == distinctQuerySchema {
		return IndexSummaryPreviewSpan
	}
	if spec, ok := LookupSchemaSpec(schema); ok {
		return spec.IndexSummaryMode
	}
	return IndexSummaryPrefix
}

func (s SchemaSpec) MatchesIndexedPackage(p SemanticPackage) bool {
	switch s.IndexedRanking {
	case IndexedRankingHybrid:
		return hasHybridRanking(p.Ranking)
	case IndexedRankingInterleaved:
		return hasInterleavedRanking(p.Ranking) && !hasIndexedIDFRanking(p.Ranking)
	case IndexedRankingInterleavedIDF:
		return hasIndexedIDFRanking(p.Ranking) && p.IDF != nil
	default:
		return false
	}
}

func (s SchemaSpec) ValidateRecompileContract(p SemanticPackage, req RecompileRequest) error {
	if s.RequiresBrowse || (s.SupportsBrowse && p.Browse != nil) {
		page := p.Browse
		if p.Mode != "index" || req.Query != "" || page == nil || page.Offset < 0 || page.Offset > 10000 {
			return failure("INTEGRITY_FAILURE", "historical browse page is invalid")
		}
	} else if p.Browse != nil {
		return failure("INTEGRITY_FAILURE", "legacy compiler cannot carry a browse page")
	}
	advisorySchema := s.RequiresAdvisoryConflicts || (s.SupportsAdvisoryConflicts && p.AdvisoryConflicts)
	if advisorySchema != p.AdvisoryConflicts || (advisorySchema && p.Purpose != "context") {
		return failure("INTEGRITY_FAILURE", "historical advisory conflict intent is invalid")
	}
	entityIntent := p.EntitiesSHA256 != ""
	if p.EntitiesSHA256 != entitiesDigest(req.Entities) {
		return failure("INVALID_REQUEST", "entities do not match the historical intent digest")
	}
	if (entityIntent && (!s.SupportsEntities || p.Browse != nil)) || entityIntent != hasEntityRanking(p.Ranking) {
		return failure("INTEGRITY_FAILURE", "historical entity intent is invalid")
	}
	if s.IsPureIDF && (legacyIDFRanking(p.Ranking) != idfRanking(req.Entities, p.ErrorSignature, queryLiterals(req.Query)) || p.IDF == nil) {
		return failure("INTEGRITY_FAILURE", "historical IDF ranking identity is invalid")
	}
	if s.IsIndexedIDF && p.Ranking != strings.Replace(adjacentIDFRanking(req.Entities, p.ErrorSignature, queryLiterals(req.Query)), "binary-idf-", "interleaved-", 1) {
		return failure("INTEGRITY_FAILURE", "historical indexed IDF ranking identity is invalid")
	}
	signatureSchema := p.ErrorSignature != ""
	if (s.RequiresErrorSignature && !signatureSchema) || (signatureSchema && (!s.SupportsErrorSignature || !digestValid(p.ErrorSignature) || p.ErrorSignature != strings.ToLower(p.ErrorSignature) || (!hasFailureRanking(p.Ranking) && !hasEntityRanking(p.Ranking)))) || (!signatureSchema && hasFailureRanking(p.Ranking)) {
		return failure("INTEGRITY_FAILURE", "historical failure signature is invalid")
	}
	pagedSchema := s.RequiresPage || (s.SupportsPage && p.Page != nil)
	page := p.Page
	if pagedSchema {
		if p.Mode != "index" || p.Purpose != "context" || (strings.TrimSpace(req.Query) == "" && !signatureSchema && !entityIntent) || p.Browse != nil || page == nil || page.Offset < 0 || page.Offset > 10000 {
			return failure("INTEGRITY_FAILURE", "historical search page is invalid")
		}
	} else if page != nil {
		return failure("INTEGRITY_FAILURE", "legacy compiler cannot carry a search page")
	}
	hasTaskPhase := p.Context != nil && p.Context.TaskPhase != ""
	if (s.RequiresTaskPhase && !hasTaskPhase) || (!s.SupportsTaskPhase && hasTaskPhase) || p.Context.validate() != nil {
		return failure("INTEGRITY_FAILURE", "historical task phase is invalid")
	}
	normalized, kindErr := NormalizeKinds(p.Kinds)
	hasKinds := len(normalized) > 0
	if kindErr != nil || !slices.Equal(normalized, p.Kinds) || (s.RequiresKinds && !hasKinds) || (!s.SupportsKinds && hasKinds) {
		return failure("INTEGRITY_FAILURE", "historical kind filter is invalid")
	}
	switch p.Policy {
	case "local-loop/1":
		if p.PolicyRevision != nil {
			return failure("INTEGRITY_FAILURE", "legacy policy cannot carry an explicit revision")
		}
	case "local-loop/2", "local-loop/3":
		pin := p.PolicyRevision
		if pin == nil || pin.Rules.validate() != nil || p.Policy != pin.Rules.engine() || p.OptionalLimit != min(p.AvailableTokens*pin.Rules.OptionalPercent/100, pin.Rules.OptionalMaxTokens) {
			return failure("INTEGRITY_FAILURE", "historical policy revision or budget is invalid")
		}
	default:
		return failure("REPLAY_INCOMPLETE", "historical policy engine is not supported")
	}
	return nil
}

func resolveCompileSchemaAndRanking(p *SemanticPackage, req CompileRequest, literals []string) bool {
	p.Schema = SchemaSemantic3
	p.Ranking = "lexical-scope-recency/4"
	if len(literals) > 0 {
		p.Ranking = "lexical-scope-recency/5"
	}
	if len(req.Kinds) > 0 {
		p.Kinds = req.Kinds
		p.Schema = SchemaSemantic9
	}
	if req.Context != nil && req.Context.TaskPhase != "" {
		p.Schema = SchemaSemantic10
	}
	if req.ErrorSignature != "" {
		p.Schema = SchemaSemantic12
		p.ErrorSignature = req.ErrorSignature
		p.Ranking = "lexical-scope-recency/6"
		p.Omitted["NO_FAILURE_MATCH"] = 0
	}
	if len(req.Entities) > 0 {
		p.Schema = SchemaSemantic13
		p.EntitiesSHA256 = entitiesDigest(req.Entities)
		p.Ranking = "lexical-scope-recency/7"
		p.Omitted["NO_ENTITY_MATCH"] = 0
	}
	idfSearch := req.Mode == "index" && req.Purpose == "context" && strings.TrimSpace(req.Query) != "" && req.BrowseOffset == nil && !req.Semantic
	if idfSearch {
		p.Schema = SchemaSemantic17
	}
	return idfSearch
}

func promoteIndexSchema(p *SemanticPackage, req CompileRequest) {
	spec, _ := LookupSchemaSpec(p.Schema)
	if spec.IndexSummaryMode != IndexSummaryPreviewSpan {
		p.Schema = SchemaSemantic8
		spec, _ = LookupSchemaSpec(p.Schema)
	}
	if req.BrowseOffset != nil {
		p.Browse = &BrowsePage{Offset: *req.BrowseOffset}
	}
	if req.PageOffset != nil {
		if !spec.SupportsPage {
			p.Schema = SchemaSemantic11
		}
		p.Page = &BrowsePage{Offset: *req.PageOffset}
	}
}

type candidateFeatures struct {
	words       map[string]bool
	score       int
	specificity int
	entity      bool
	literal     bool
	failure     bool
	reason      string
}

func scoreCandidateLexical(record Record, lexicon rankingLexicon) (map[string]bool, int, int) {
	words := lexicon.bodyTerms(record.Body)
	score := 0
	for word := range lexicon.terms {
		if words[word] {
			score++
		}
	}
	specificity := 0
	if record.Scope.TaskID != "*" {
		specificity++
	}
	if record.Scope.RunID != "*" {
		specificity++
	}
	return words, score, specificity
}

func evaluateCandidateMatchFeatures(record Record, mandatory bool, words map[string]bool, score, specificity int, literals []string, entities []EntityRef, failureMatch *FailureMatch) candidateFeatures {
	entity := !mandatory && matchesEntities(record.Entities, entities)
	literal := !mandatory && matchesLiteral(record.Body, literals)
	hasFailure := !mandatory && failureMatch != nil
	reason := entityReason(failureReason(literalReason(fmt.Sprintf("lexical matches=%d; scope specificity=%d", score, specificity), literal), hasFailure), entity)
	return candidateFeatures{words: words, score: score, specificity: specificity, entity: entity, literal: literal, failure: hasFailure, reason: reason}
}

func evaluateCandidateFeatures(record Record, mandatory bool, literals []string, entities []EntityRef, failureMatch *FailureMatch, lexicon rankingLexicon) candidateFeatures {
	words, score, specificity := scoreCandidateLexical(record, lexicon)
	return evaluateCandidateMatchFeatures(record, mandatory, words, score, specificity, literals, entities, failureMatch)
}

func admitEvaluatedCandidate(p *SemanticPackage, selection Selection, e *CandidateEvaluation, features candidateFeatures, terms map[string]bool, query string, entityIntent, signatureIntent, idfSearch, deferLexicalFilter, allowDisputes, resetPoolReason bool, pool map[string]candidate, idfMembers *[]idfMember, candidates *[]candidate) {
	score := features.score
	selection.Reason = features.reason
	if hasSemanticScopeRanking(p.Ranking) && !selection.Mandatory && e.SemanticScore != nil {
		score = *e.SemanticScore
		selection.Reason = entityReason(failureReason(literalReason(semanticReason(score, features.specificity), features.literal), features.failure), features.entity)
	}
	item := candidate{
		identity:    hasEntityRanking(p.Ranking) && (features.entity || features.failure),
		literal:     features.literal,
		failure:     features.failure,
		selection:   selection,
		score:       score,
		specificity: features.specificity,
	}
	if allowDisputes {
		pool[selection.Record.RecordID] = item
		if resetPoolReason {
			e.Reason = ""
		}
	}
	if !kindAllowed(p.Kinds, selection) {
		e.Reason = "KIND_FILTERED"
		p.Omitted["KIND_FILTERED"]++
		return
	}
	if entityIntent && strings.TrimSpace(query) == "" && !selection.Mandatory && !features.entity && !features.failure {
		e.Reason = "NO_ENTITY_MATCH"
		p.Omitted["NO_ENTITY_MATCH"]++
		return
	}
	if !entityIntent && signatureIntent && strings.TrimSpace(query) == "" && !selection.Mandatory && !features.failure {
		e.Reason = "NO_FAILURE_MATCH"
		p.Omitted["NO_FAILURE_MATCH"]++
		return
	}
	if idfSearch && !selection.Mandatory {
		*idfMembers = append(*idfMembers, makeIDFMember(selection.Record, features.words, terms))
	}
	nonemptyQuery := len(terms) > 0
	if p.Ranking == "lexical-scope-recency/2" || p.Ranking == "lexical-scope-recency/3" || p.Ranking == "lexical-scope-recency/4" || hasLiteralRanking(p.Ranking) || idfSearch {
		nonemptyQuery = strings.TrimSpace(query) != ""
	}
	if !deferLexicalFilter && !selection.Mandatory && nonemptyQuery && score == 0 && !features.literal && !features.failure && !features.entity {
		e.Reason = "NO_LEXICAL_MATCH"
		p.Omitted["NO_LEXICAL_MATCH"]++
		return
	}
	*candidates = append(*candidates, item)
}
