package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"github.com/jackc/pgx/v5"
	"slices"
)

type RecompileRequest struct {
	SemanticQuery string      `json:"semantic_query,omitempty"`
	Entities      []EntityRef `json:"entities,omitempty"`
	ReceiptID     string      `json:"receipt_id"`
	Query         string      `json:"query"`
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
	if err := validateDistinctInputs(req.Query, req.SemanticQuery); err != nil {
		return Package{}, err
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
	if err = decodeSemanticPackage(encoded, &original.Semantic); err != nil {
		return Package{}, err
	}
	_, storedSeal, err := sealPackage(original.Semantic)
	if err != nil {
		return Package{}, err
	}
	if storedSeal != original.Seal {
		return Package{}, failure("INTEGRITY_FAILURE", "retained semantic bytes do not reproduce their seal")
	}
	if err = validateMemoryBudget(original.Semantic); err != nil {
		return Package{}, err
	}
	if err = validatePresentation(original.Semantic); err != nil {
		return Package{}, err
	}
	digest := sha256.Sum256([]byte(req.Query))
	if original.Semantic.Query != "sha256:"+hex.EncodeToString(digest[:]) {
		return Package{}, failure("INVALID_REQUEST", "query does not match the historical intent digest")
	}
	contract, err := queryContract(original.Semantic, req.Query, req.SemanticQuery)
	if err != nil {
		return Package{}, err
	}
	spec, ok := LookupSchemaSpec(contract.Schema)
	if !ok || !SupportedRanking(original.Semantic.Ranking) {
		return Package{}, failure("REPLAY_INCOMPLETE", "historical compiler version is not supported")
	}
	if err = spec.ValidateRecompileContract(original.Semantic, req); err != nil {
		return Package{}, err
	}
	advisorySchema := original.Semantic.AdvisoryConflicts
	entitySchema := spec.SupportsEntities
	entityIntent := original.Semantic.EntitiesSHA256 != ""
	signatureSchema := original.Semantic.ErrorSignature != ""
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
	semanticQuery := req.Query
	if req.SemanticQuery != "" {
		semanticQuery = req.SemanticQuery
	}
	if err = validateFrozenDiscoveryInputs(contract, evaluations, req.Query, semanticQuery); err != nil {
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
	lexicon := newRankingLexicon(req.Query, p.Ranking)
	legacyLexicon := newRankingLexicon(req.Query, legacyIndexedRanking(p.Ranking))
	idfMembers := []idfMember{}
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
		if e.PassageHit != nil && !validPassage(record.Body, e.PassageHit.Span) {
			return Package{}, failure("INTEGRITY_FAILURE", "historical passage addresses invalid source bytes")
		}
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
		candidateLexicon := lexicon
		if spec.IsIndexedIDF && (e.Mandatory || e.Reason == "KIND_FILTERED") {
			candidateLexicon = legacyLexicon
		}
		words, score, specificity := scoreCandidateLexical(record, candidateLexicon)
		features := evaluateCandidateMatchFeatures(record, e.Mandatory, words, score, specificity, literals, req.Entities, e.FailureMatch)
		if features.entity != e.EntityMatch {
			return Package{}, failure("INTEGRITY_FAILURE", "historical entity match changed")
		}
		if features.literal != e.ExactTextMatch || features.score != e.LexicalMatches || features.specificity != e.ScopeSpecificity {
			return Package{}, failure("INTEGRITY_FAILURE", "historical ranking features changed")
		}
		selection := Selection{Conflicts: e.Facts.Conflicts, Category: e.Facts.Category, Record: record, Evidence: e.Facts.Evidence, Authority: e.Facts.Authority, Mandatory: e.Mandatory}
		deferLexical := hasIndexedRanking(p.Ranking) || hasSemanticScopeRanking(p.Ranking)
		admitEvaluatedCandidate(&p, selection, e, features, candidateLexicon.terms, req.Query, entityIntent, signatureSchema, spec.IsPureIDF, deferLexical, advisorySchema, true, pool, &idfMembers, &candidates)
	}
	if hasIndexedRanking(p.Ranking) {
		// Recreate the complete qualified dispute set before scoring, just as
		// collectCandidates does. A kind-filtered companion can still be required.
		if advisorySchema {
			candidates = qualifyAdvisoryCandidates(&p, pool, candidates, groups, evaluations)
		}
		if spec.IsIndexedIDF {
			members := indexedIDFMembers(req.Query, p.Ranking, candidates, evaluations, false)
			if err = verifyFrozenIDF(p.IDF, req.Query, p.Ranking, members, evaluations); err != nil {
				return Package{}, err
			}
			applyIDF(candidates, p.IDF, members, evaluations)
		}
		candidates, err = rankIndexedCandidates(&p, candidates, evaluations)
		if err != nil {
			return Package{}, err
		}
	} else if advisorySchema {
		candidates = qualifyAdvisoryCandidates(&p, pool, candidates, groups, evaluations)
	}
	if spec.IsPureIDF {
		if err = verifyFrozenIDF(p.IDF, req.Query, p.Ranking, idfMembers, evaluations); err != nil {
			return Package{}, err
		}
		applyIDF(candidates, p.IDF, idfMembers, evaluations)
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
