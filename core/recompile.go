package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"github.com/fxamacker/cbor/v2"
	"github.com/jackc/pgx/v5"
	"slices"
	"strings"
)

type RecompileRequest struct {
	Entities  []EntityRef `json:"entities,omitempty"`
	ReceiptID string      `json:"receipt_id"`
	Query     string      `json:"query"`
}

// Recompile reconstructs ranking/packing from the original read set and retained
// versions. Its cutoff is the named receipt, not a PostgreSQL snapshot handle or
// an arbitrary timestamp. It creates no new delivery authorization or exposure.
func (s *Store) Recompile(ctx context.Context, req RecompileRequest) (Package, error) {
	return s.recompile(ctx, req, nil)
}

// RecompileForDestination reconstructs an owned receipt for authenticated
// inspection. It preserves historical eligibility while checking present privacy.
func (s *Store) RecompileForDestination(ctx context.Context, req RecompileRequest, dest Destination) (Package, error) {
	if (dest.Name != "local" && dest.Name != "hosted") || (dest.Name == "hosted" && dest.AllowLocal) {
		return Package{}, failure("INVALID_REQUEST", "invalid historical destination")
	}
	return s.recompile(ctx, req, &dest)
}

func (s *Store) recompile(ctx context.Context, req RecompileRequest, dest *Destination) (Package, error) {
	ctx = s.recoveryContext(ctx)
	if len(req.Query) > 4096 {
		return Package{}, failure("INVALID_REQUEST", "query exceeds limit")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return Package{}, err
	}
	defer tx.Rollback(ctx)
	result, err := s.recompileTx(ctx, tx, req)
	if err != nil {
		return result, err
	}
	if dest != nil {
		if err = recompileDestination(ctx, tx, result, *dest); err != nil {
			return Package{}, err
		}
	}
	return result, tx.Commit(ctx)
}

func recompileDestination(ctx context.Context, tx pgx.Tx, pkg Package, dest Destination) error {
	if pkg.Semantic.Destination != dest {
		return failure("AUTHORITY_DENIED", "historical package destination differs from the authenticated profile")
	}
	ids := make([]string, 0, len(pkg.Semantic.Selected)+len(pkg.Semantic.Index))
	for _, entry := range pkg.Semantic.Selected {
		ids = append(ids, entry.Record.RecordID)
	}
	for _, entry := range pkg.Semantic.Index {
		ids = append(ids, entry.RecordID)
	}
	slices.Sort(ids)
	ids = slices.Compact(ids)
	// Lock current identities against edits/forgetting through this inspection.
	// Historical versions and eligibility remain those of the retained receipt.
	rows, err := tx.Query(ctx, `SELECT m.sensitivity,m.lifecycle,v.repo
 FROM cairn.memory_record m JOIN cairn.record_version v
 ON v.record_id=m.record_id AND v.version=m.current_version
 WHERE m.record_id=ANY($1::uuid[]) ORDER BY m.record_id FOR SHARE OF m`, ids)
	if err != nil {
		return err
	}
	defer rows.Close()
	count := 0
	for rows.Next() {
		var sensitivity, lifecycle, repo string
		if err = rows.Scan(&sensitivity, &lifecycle, &repo); err != nil {
			return err
		}
		if repo != pkg.Semantic.Scope.Repo || (!dest.AllowLocal && sensitivity != "shareable") {
			return failure("AUTHORITY_DENIED", "historical package is outside current destination access")
		}
		if lifecycle == "tombstoned" {
			return failure("PAYLOAD_UNAVAILABLE", "historical package contains forgotten memory")
		}
		count++
	}
	if err = rows.Err(); err != nil {
		return err
	}
	if count != len(ids) {
		return failure("PAYLOAD_UNAVAILABLE", "historical package contains unavailable memory")
	}
	return nil
}

func historicalRecord(ctx context.Context, tx pgx.Tx, e *CandidateEvaluation) (Record, error) {
	var deleted bool
	if err := tx.QueryRow(ctx, `SELECT payload_deleted_by IS NOT NULL FROM cairn.record_version WHERE record_id=$1 AND version=$2`, e.RecordID, e.Version).Scan(&deleted); err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return Record{}, err
	}
	if deleted {
		return Record{}, failure("PAYLOAD_UNAVAILABLE", "historical candidate payload was excluded by deletion")
	}
	r := Record{RecordID: e.RecordID, Version: e.Version, Class: e.Class, Lifecycle: "active", Sensitivity: e.Facts.Sensitivity, WrittenAt: e.WrittenAt, AttributionState: e.Facts.AttributionState}
	err := tx.QueryRow(ctx, `SELECT kind,body,repo,task_id,run_id,attributed_producer,COALESCE(attempt_id::text,''),result_ref,claim_type,observed_writer,witness FROM cairn.record_version WHERE record_id=$1 AND version=$2`, e.RecordID, e.Version).Scan(&r.Kind, &r.Body, &r.Scope.Repo, &r.Scope.TaskID, &r.Scope.RunID, &r.AttributedProducer, &r.AttemptID, &r.ResultRef, &r.ClaimType, &r.ObservedWriter, &r.Witness)
	if errors.Is(err, pgx.ErrNoRows) {
		return r, failure("REPLAY_INCOMPLETE", "historical record version is missing")
	}
	if err != nil {
		return r, err
	}
	r.Relations, err = readRelations(ctx, tx, r.RecordID, r.Version)
	if err != nil {
		return r, err
	}
	r.Draft.Sensitivity = r.Sensitivity
	err = tx.QueryRow(ctx, `SELECT pins FROM cairn.record_applicability WHERE record_id=$1 AND version=$2`, e.RecordID, e.Version).Scan(&r.Pins)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return r, err
	}
	r.Entities, err = readEntities(ctx, tx, r.RecordID, r.Version)
	return r, err
}

func (s *Store) recompileTx(ctx context.Context, tx pgx.Tx, req RecompileRequest) (Package, error) {
	var err error
	req.Entities, err = NormalizeEntities(req.Entities)
	if err != nil {
		return Package{}, err
	}
	if err = s.receiptAccess(ctx, tx, req.ReceiptID); err != nil {
		return Package{}, err
	}
	if err = receiptPayloadAvailable(ctx, tx, req.ReceiptID); err != nil {
		return Package{}, err
	}
	var original Package
	original.ReceiptID = req.ReceiptID
	var encoded []byte
	var version int
	if err = tx.QueryRow(ctx, `SELECT semantic_body,seal,nonce::text,explanation_version FROM cairn.retrieval_receipt WHERE receipt_id=$1`, req.ReceiptID).Scan(&encoded, &original.Seal, &original.Nonce, &version); err != nil {
		return Package{}, err
	}
	if version != 2 {
		return Package{}, failure("REPLAY_INCOMPLETE", "receipt lacks frozen candidate facts; saved-byte replay remains available")
	}
	if err = cbor.Unmarshal(encoded, &original.Semantic); err != nil {
		return Package{}, err
	}
	_, storedSeal, err := sealPackage(original.Semantic)
	if err != nil {
		return Package{}, err
	}
	if storedSeal != original.Seal {
		return Package{}, failure("INTEGRITY_FAILURE", "retained semantic bytes do not reproduce their seal")
	}
	digest := sha256.Sum256([]byte(req.Query))
	if original.Semantic.Query != "sha256:"+hex.EncodeToString(digest[:]) {
		return Package{}, failure("INVALID_REQUEST", "query does not match the historical intent digest")
	}
	if (original.Semantic.Schema != "cairn.semantic/3" && original.Semantic.Schema != "cairn.semantic/4" && original.Semantic.Schema != "cairn.semantic/5" && original.Semantic.Schema != "cairn.semantic/6" && original.Semantic.Schema != "cairn.semantic/7" && original.Semantic.Schema != "cairn.semantic/8" && original.Semantic.Schema != "cairn.semantic/9" && original.Semantic.Schema != "cairn.semantic/10" && original.Semantic.Schema != "cairn.semantic/11" && original.Semantic.Schema != "cairn.semantic/12" && original.Semantic.Schema != "cairn.semantic/13" && original.Semantic.Schema != "cairn.semantic/14") || (original.Semantic.Ranking != "lexical-scope-recency/1" && original.Semantic.Ranking != "lexical-scope-recency/2" && original.Semantic.Ranking != "lexical-scope-recency/3" && original.Semantic.Ranking != "lexical-scope-recency/4" && original.Semantic.Ranking != "semantic-scope-recency/1" && !hasLiteralRanking(original.Semantic.Ranking)) {
		return Package{}, failure("REPLAY_INCOMPLETE", "historical compiler version is not supported")
	}
	if original.Semantic.Schema == "cairn.semantic/6" || ((original.Semantic.Schema == "cairn.semantic/8" || original.Semantic.Schema == "cairn.semantic/9" || original.Semantic.Schema == "cairn.semantic/10" || original.Semantic.Schema == "cairn.semantic/13" || original.Semantic.Schema == "cairn.semantic/14") && original.Semantic.Browse != nil) {
		page := original.Semantic.Browse
		if original.Semantic.Mode != "index" || req.Query != "" || page == nil || page.Offset < 0 || page.Offset > 10000 {
			return Package{}, failure("INTEGRITY_FAILURE", "historical browse page is invalid")
		}
	} else if original.Semantic.Browse != nil {
		return Package{}, failure("INTEGRITY_FAILURE", "legacy compiler cannot carry a browse page")
	}
	advisorySchema := original.Semantic.Schema == "cairn.semantic/14"
	if advisorySchema != original.Semantic.AdvisoryConflicts || (advisorySchema && original.Semantic.Purpose != "context") {
		return Package{}, failure("INTEGRITY_FAILURE", "historical advisory conflict intent is invalid")
	}
	entitySchema := original.Semantic.Schema == "cairn.semantic/13" || advisorySchema
	entityIntent := original.Semantic.EntitiesSHA256 != ""
	if original.Semantic.EntitiesSHA256 != entitiesDigest(req.Entities) {
		return Package{}, failure("INVALID_REQUEST", "entities do not match the historical intent digest")
	}
	if (entityIntent && (!entitySchema || original.Semantic.Browse != nil)) || entityIntent != hasEntityRanking(original.Semantic.Ranking) {
		return Package{}, failure("INTEGRITY_FAILURE", "historical entity intent is invalid")
	}
	signatureSchema := original.Semantic.ErrorSignature != ""
	if (original.Semantic.Schema == "cairn.semantic/12" && !signatureSchema) || (signatureSchema && ((!entitySchema && original.Semantic.Schema != "cairn.semantic/12") || !digestValid(original.Semantic.ErrorSignature) || original.Semantic.ErrorSignature != strings.ToLower(original.Semantic.ErrorSignature) || (!hasFailureRanking(original.Semantic.Ranking) && !hasEntityRanking(original.Semantic.Ranking)))) || (!signatureSchema && hasFailureRanking(original.Semantic.Ranking)) {
		return Package{}, failure("INTEGRITY_FAILURE", "historical failure signature is invalid")
	}
	pagedSchema := original.Semantic.Schema == "cairn.semantic/11" || ((signatureSchema || entitySchema) && original.Semantic.Page != nil)
	page := original.Semantic.Page
	if pagedSchema {
		if original.Semantic.Mode != "index" || original.Semantic.Purpose != "context" || (strings.TrimSpace(req.Query) == "" && !signatureSchema && !entityIntent) || original.Semantic.Browse != nil || page == nil || page.Offset < 0 || page.Offset > 10000 {
			return Package{}, failure("INTEGRITY_FAILURE", "historical search page is invalid")
		}
	} else if page != nil {
		return Package{}, failure("INTEGRITY_FAILURE", "legacy compiler cannot carry a search page")
	}
	normalized, kindErr := NormalizeKinds(original.Semantic.Kinds)
	phaseSchema := original.Semantic.Schema == "cairn.semantic/10"
	if (!entitySchema && !signatureSchema && !pagedSchema && phaseSchema != (original.Semantic.Context != nil && original.Semantic.Context.TaskPhase != "")) || original.Semantic.Context.validate() != nil {
		return Package{}, failure("INTEGRITY_FAILURE", "historical task phase is invalid")
	}
	if kindErr != nil || !slices.Equal(normalized, original.Semantic.Kinds) || (!entitySchema && !signatureSchema && !pagedSchema && !phaseSchema && (original.Semantic.Schema == "cairn.semantic/9") != (len(normalized) > 0)) {
		return Package{}, failure("INTEGRITY_FAILURE", "historical kind filter is invalid")
	}
	switch original.Semantic.Policy {
	case "local-loop/1":
		if original.Semantic.PolicyRevision != nil {
			return Package{}, failure("INTEGRITY_FAILURE", "legacy policy cannot carry an explicit revision")
		}
	case "local-loop/2", "local-loop/3":
		pin := original.Semantic.PolicyRevision
		if pin == nil || pin.Rules.validate() != nil || original.Semantic.Policy != pin.Rules.engine() || original.Semantic.OptionalLimit != min(original.Semantic.AvailableTokens*pin.Rules.OptionalPercent/100, pin.Rules.OptionalMaxTokens) {
			return Package{}, failure("INTEGRITY_FAILURE", "historical policy revision or budget is invalid")
		}
	default:
		return Package{}, failure("REPLAY_INCOMPLETE", "historical policy engine is not supported")
	}
	rows, err := tx.Query(ctx, `SELECT detail FROM cairn.retrieval_candidate WHERE receipt_id=$1 ORDER BY record_id,version`, req.ReceiptID)
	if err != nil {
		return Package{}, err
	}
	evaluations := map[string]*CandidateEvaluation{}
	for rows.Next() {
		var e CandidateEvaluation
		if err = rows.Scan(&e); err != nil {
			rows.Close()
			return Package{}, err
		}
		evaluations[e.RecordID] = &e
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return Package{}, err
	}
	if err = validateFrozenDiscovery(original.Semantic, evaluations, req.Query); err != nil {
		return Package{}, err
	}
	p := original.Semantic
	p.Status = "READY"
	p.Selected = []Selection{}
	p.Omitted = omissionCensus()
	if signatureSchema {
		p.Omitted["NO_FAILURE_MATCH"] = 0
	}
	if entityIntent {
		p.Omitted["NO_ENTITY_MATCH"] = 0
	}
	candidates := []candidate{}
	var pool map[string]candidate
	if advisorySchema {
		pool = map[string]candidate{}
	}
	groups, err := frozenAdvisoryGroups(p, evaluations)
	if err != nil {
		return Package{}, err
	}
	terms := rankingTerms(req.Query, p.Ranking)
	var literals []string
	if hasLiteralRanking(p.Ranking) {
		literals = queryLiterals(req.Query)
	}
	for _, e := range evaluations {
		if err = validateFrozenFailure(ctx, tx, p, e); err != nil {
			return Package{}, err
		}
		if e.Facts == nil {
			switch e.Reason {
			case "CLASS_NOT_CONSEQUENTIAL", "AUTHORITY_INACTIVE", "SCOPE_AUTHORITY_INACTIVE", "POLICY_UNENFORCEABLE", "OPEN_CONFLICT", "ATTRIBUTION_UNRECONCILED", "EVIDENCE_UNAVAILABLE", "CONTEXT_MISSING", "CURRENTNESS_MISMATCH", "OUTSIDE_VALIDITY":
				p.Omitted[e.Reason]++
				continue
			default:
				return Package{}, failure("REPLAY_INCOMPLETE", "eligible candidate facts are missing")
			}
		}
		record, err := historicalRecord(ctx, tx, e)
		if err != nil {
			return Package{}, err
		}
		bodyDigest := sha256.Sum256([]byte(record.Body))
		if hex.EncodeToString(bodyDigest[:]) != e.Facts.BodySHA256 {
			return Package{}, failure("INTEGRITY_FAILURE", "historical candidate bytes changed")
		}
		if e.Facts.EntitiesSHA256 == nil {
			if entitySchema {
				return Package{}, failure("REPLAY_INCOMPLETE", "historical candidate association digest is missing")
			}
			// Earlier readers did not observe this metadata, even when a newer
			// writer had already attached it to the retained version.
			record.Entities = nil
		} else if entitiesDigest(record.Entities) != *e.Facts.EntitiesSHA256 {
			return Package{}, failure("INTEGRITY_FAILURE", "historical candidate associations changed")
		}
		entity := !e.Mandatory && matchesEntities(record.Entities, req.Entities)
		if entity != e.EntityMatch {
			return Package{}, failure("INTEGRITY_FAILURE", "historical entity match changed")
		}
		words := rankingTerms(record.Body, p.Ranking)
		score := 0
		for word := range terms {
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
		literal := !e.Mandatory && matchesLiteral(record.Body, literals)
		if literal != e.ExactTextMatch || score != e.LexicalMatches || specificity != e.ScopeSpecificity {
			return Package{}, failure("INTEGRITY_FAILURE", "historical ranking features changed")
		}
		selection := Selection{Conflicts: e.Facts.Conflicts, Category: e.Facts.Category, Record: record, Evidence: e.Facts.Evidence, Authority: e.Facts.Authority, Mandatory: e.Mandatory, Reason: fmt.Sprintf("lexical matches=%d; scope specificity=%d", score, specificity)}
		failureMatch := e.FailureMatch != nil
		selection.Reason = entityReason(failureReason(literalReason(selection.Reason, literal), failureMatch), entity)
		if (p.Ranking == "semantic-scope-recency/1" || p.Ranking == "semantic-scope-recency/2" || p.Ranking == "semantic-scope-recency/3" || p.Ranking == "semantic-scope-recency/4") && !selection.Mandatory && e.SemanticScore != nil {
			score = *e.SemanticScore
			selection.Reason = entityReason(failureReason(literalReason(semanticReason(score, specificity), literal), failureMatch), entity)
		}
		item := candidate{identity: entityIntent && (entity || failureMatch), literal: literal, failure: failureMatch, selection: selection, score: score, specificity: specificity}
		if advisorySchema {
			pool[e.RecordID] = item
			e.Reason = ""
		}
		if !kindAllowed(p.Kinds, selection) {
			p.Omitted["KIND_FILTERED"]++
			e.Reason = "KIND_FILTERED"
			continue
		}
		if entityIntent && strings.TrimSpace(req.Query) == "" && !selection.Mandatory && !entity && !failureMatch {
			p.Omitted["NO_ENTITY_MATCH"]++
			e.Reason = "NO_ENTITY_MATCH"
			continue
		}
		if !entityIntent && signatureSchema && strings.TrimSpace(req.Query) == "" && !selection.Mandatory && !failureMatch {
			p.Omitted["NO_FAILURE_MATCH"]++
			e.Reason = "NO_FAILURE_MATCH"
			continue
		}
		nonemptyQuery := len(terms) > 0
		if p.Ranking == "lexical-scope-recency/2" || p.Ranking == "lexical-scope-recency/3" || p.Ranking == "lexical-scope-recency/4" || hasLiteralRanking(p.Ranking) {
			nonemptyQuery = strings.TrimSpace(req.Query) != ""
		}
		if p.Ranking != "semantic-scope-recency/1" && p.Ranking != "semantic-scope-recency/2" && p.Ranking != "semantic-scope-recency/3" && p.Ranking != "semantic-scope-recency/4" && !selection.Mandatory && nonemptyQuery && score == 0 && !literal && !failureMatch && !entity {
			p.Omitted["NO_LEXICAL_MATCH"]++
			e.Reason = "NO_LEXICAL_MATCH"
			continue
		}
		candidates = append(candidates, item)
	}
	if advisorySchema {
		candidates = qualifyAdvisoryCandidates(&p, pool, candidates, groups, evaluations)
	}
	if p.Mode == "index" {
		p, err = packIndex(p, candidates, evaluations, req.Query)
	} else {
		p, err = packCandidates(p, candidates, evaluations)
	}
	if err != nil {
		return Package{}, err
	}
	_, seal, err := sealPackage(p)
	if err != nil {
		return Package{}, err
	}
	if seal != original.Seal {
		return Package{}, failure("INTEGRITY_FAILURE", "recompiled historical selection differs from retained seal")
	}
	original.Semantic = p
	return original, nil
}
