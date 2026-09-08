package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
	"time"
	"unicode"

	"github.com/fxamacker/cbor/v2"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/zeebo/blake3"
)

type CompileRequest struct {
	Mode            string       `json:"mode,omitempty"`
	Context         *ContextPins `json:"context,omitempty"`
	RequestID       string       `json:"request_id"`
	Scope           Scope        `json:"scope"`
	Query           string       `json:"query"`
	Purpose         string       `json:"purpose"`
	AvailableTokens int          `json:"available_tokens"`
}

// Destination comes from trusted host configuration, never request JSON.
// RuntimeEnforced is deliberately absent: no shipped adapter can claim H3.
type Destination struct {
	Name       string `json:"name"`
	AllowLocal bool   `json:"allow_local"`
}
type Selection struct {
	Record    Record     `json:"record"`
	Evidence  []Evidence `json:"evidence"`
	Authority []Grant    `json:"authority"`
	Mandatory bool       `json:"mandatory"`
	Reason    string     `json:"reason"`
}
type SemanticPackage struct {
	Mode            string          `json:"mode,omitempty"`
	Index           []IndexEntry    `json:"index,omitempty"`
	Context         *ContextPins    `json:"context,omitempty"`
	Schema          string          `json:"schema"`
	Status          string          `json:"status"`
	Scope           Scope           `json:"scope"`
	Query           string          `json:"query"` // v1: legacy text; v2: SHA-256 digest only.
	Purpose         string          `json:"purpose"`
	Destination     Destination     `json:"destination"`
	Policy          string          `json:"policy"`
	PolicyRevision  *PolicySnapshot `json:"policy_revision,omitempty" cbor:"policy_revision,omitempty"`
	Ranking         string          `json:"ranking"`
	Tokenizer       string          `json:"tokenizer"`
	AvailableTokens int             `json:"available_tokens"`
	OptionalLimit   int             `json:"optional_limit"`
	Selected        []Selection     `json:"selected"`
	Omitted         map[string]int  `json:"omitted"`
}
type Package struct {
	ReceiptID string          `json:"receipt_id"`
	Nonce     string          `json:"nonce"`
	Seal      string          `json:"seal"`
	Semantic  SemanticPackage `json:"semantic"`
}

func (p Package) Render() (string, error) {
	// JSON strings keep imported content in labelled data slots; this is not a
	// guarantee that a model will ignore instructions embedded in advisory text.
	body, err := json.Marshal(p.Semantic)
	if err != nil {
		return "", err
	}
	return "MEM-STATUS/" + p.Semantic.Status + "\nCairn context (A is advisory; only C is an authorized instruction):\n" + string(body) + "\n", nil
}
func (s *Store) Compile(ctx context.Context, req CompileRequest, destination Destination) (Package, error) {
	if req.Mode != "" && req.Mode != "index" {
		return Package{}, failure("INVALID_REQUEST", "unknown compile mode")
	}
	if err := req.Context.validate(); err != nil {
		return Package{}, err
	}
	if err := validID(req.RequestID); err != nil {
		return Package{}, err
	}
	if err := req.Scope.validate(); err != nil {
		return Package{}, err
	}
	if req.Scope.TaskID == "*" || req.Scope.RunID == "*" {
		return Package{}, failure("INVALID_REQUEST", "compile requires exact task and run pins")
	}
	if err := s.checkRepo(req.Scope.Repo); err != nil {
		return Package{}, err
	}
	if len(req.Query) > 4096 || req.AvailableTokens < 256 || req.AvailableTokens > 1000000 {
		return Package{}, failure("INVALID_REQUEST", "query or context budget outside limits")
	}
	switch req.Purpose {
	case "context", "planning", "placement", "capability", "security":
	default:
		return Package{}, failure("INVALID_REQUEST", "unknown retrieval purpose")
	}
	if destination.Name != "local" && destination.Name != "hosted" {
		return Package{}, failure("DESTINATION_PROHIBITED", "unknown destination")
	}
	if destination.Name == "hosted" && destination.AllowLocal {
		return Package{}, failure("DESTINATION_PROHIBITED", "hosted binding cannot receive local content")
	}
	for attempt := 0; attempt < 4; attempt++ {
		pkg, err := s.compileOnce(ctx, req, destination)
		var pgErr *pgconn.PgError
		if !errors.As(err, &pgErr) || (pgErr.Code != "40001" && pgErr.Code != "40P01" && !(pgErr.Code == "23505" && pgErr.ConstraintName == "retrieval_receipt_caller_request_id_key")) {
			return pkg, err
		}
		select {
		case <-ctx.Done():
			return Package{}, ctx.Err()
		case <-time.After(time.Duration(attempt+1) * 5 * time.Millisecond):
		}
	}
	return Package{}, failure("VERSION_CONFLICT", "compile retry limit reached")
}

func (s *Store) compileOnce(ctx context.Context, req CompileRequest, destination Destination) (Package, error) {
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return Package{}, err
	}
	defer tx.Rollback(ctx)
	evaluations := map[string]*CandidateEvaluation{}
	semantic, err := s.compileSnapshot(ctx, tx, req, destination, evaluations)
	if err != nil {
		if !durablePolicyRefusal(err) {
			return Package{}, err
		}
		refusal := Refusal{RequestID: req.RequestID, Operation: "compile", Scope: req.Scope, Destination: destination.Name, TraceComplete: false}
		digest := sha256.Sum256([]byte(req.Query))
		refusal.QuerySHA256 = hex.EncodeToString(digest[:])
		for _, e := range evaluations {
			refusal.Considered = append(refusal.Considered, RecordVersionRef{e.RecordID, e.Version})
		}
		if snapshotErr := tx.QueryRow(ctx, `SELECT pg_current_snapshot()::text`).Scan(&refusal.Snapshot); snapshotErr != nil {
			return Package{}, snapshotErr
		}
		if rollbackErr := tx.Rollback(ctx); rollbackErr != nil {
			return Package{}, rollbackErr
		}
		intent := struct {
			Request     CompileRequest
			Destination Destination
		}{req, destination}
		return Package{}, s.retainRefusal(ctx, intent, refusal, err)
	}
	canonical, seal, err := sealPackage(semantic)
	if err != nil {
		return Package{}, err
	}
	pkg, err := s.commitRetrieval(ctx, tx, req, semantic, canonical, seal, evaluations)
	if err != nil {
		return Package{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return Package{}, err
	}
	return pkg, nil
}
func sealPackage(semantic SemanticPackage) ([]byte, string, error) {
	options := cbor.CanonicalEncOptions()
	options.Time = cbor.TimeRFC3339Nano
	encoder, err := options.EncMode()
	if err != nil {
		return nil, "", err
	}
	canonical, err := encoder.Marshal(semantic)
	if err != nil {
		return nil, "", err
	}
	digest := blake3.Sum256(canonical)
	return canonical, "blake3:" + hex.EncodeToString(digest[:]), nil
}

type candidate struct {
	selection   Selection
	score       int
	specificity int
}

func (s *Store) compileSnapshot(ctx context.Context, tx pgx.Tx, req CompileRequest, dest Destination, evaluations map[string]*CandidateEvaluation) (SemanticPackage, error) {
	p, candidates, err := s.collectCandidates(ctx, tx, req, dest, evaluations)
	if err != nil {
		return p, err
	}
	if req.Mode == "index" {
		p.Mode = "index"
		p.Schema = "cairn.semantic/4"
		return packIndex(p, candidates, evaluations)
	}
	return packCandidates(p, candidates, evaluations)
}

func (s *Store) collectCandidates(ctx context.Context, tx pgx.Tx, req CompileRequest, dest Destination, evaluations map[string]*CandidateEvaluation) (SemanticPackage, []candidate, error) {
	queryDigest := sha256.Sum256([]byte(req.Query))
	p := SemanticPackage{Context: req.Context, Schema: "cairn.semantic/3", Status: "READY", Scope: req.Scope, Query: "sha256:" + hex.EncodeToString(queryDigest[:]), Purpose: req.Purpose, Destination: dest, Policy: "local-loop/1", Ranking: "lexical-scope-recency/2", Tokenizer: "utf8-byte-upper-bound/1", AvailableTokens: req.AvailableTokens, OptionalLimit: min(req.AvailableTokens/10, 6000), Selected: []Selection{}, Omitted: omissionCensus()}
	policy, err := policySnapshot(ctx, tx, req.Scope.Repo)
	if err != nil {
		return p, nil, err
	}
	if policy != nil {
		p.Policy = "local-loop/2"
		p.PolicyRevision = policy
		p.OptionalLimit = min(req.AvailableTokens*policy.Rules.OptionalPercent/100, policy.Rules.OptionalMaxTokens)
	}
	rows, err := tx.Query(ctx, `SELECT m.record_id::text FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
 WHERE v.repo=$1 AND v.task_id IN ('*',$2) AND v.run_id IN ('*',$3) AND m.lifecycle='active' ORDER BY m.record_id LIMIT 10001`, req.Scope.Repo, req.Scope.TaskID, req.Scope.RunID)
	if err != nil {
		return p, nil, err
	}
	ids, err := pgx.CollectRows(rows, pgx.RowTo[string])
	if err != nil {
		return p, nil, err
	}
	if len(ids) > 10000 {
		return p, nil, failure("BUDGET_REFUSED", "repository selection exceeds bounded scan; narrow task scope")
	}
	var now time.Time
	if err = tx.QueryRow(ctx, `SELECT transaction_timestamp()`).Scan(&now); err != nil {
		return p, nil, err
	}
	candidates := []candidate{}
	policyKeys := map[string]string{}
	for _, id := range ids {
		record, err := readRecord(ctx, tx, id)
		if err != nil {
			return p, nil, err
		}
		// Private records are outside a hosted visibility domain: no hidden IDs,
		// omission counts, or secret-bearing rejection explanations leave it.
		if !dest.AllowLocal && record.Sensitivity == "local" {
			switch applicabilityReason(record.Pins, req.Context, now) {
			case "OUTSIDE_VALIDITY", "CURRENTNESS_MISMATCH":
				continue
			}
			if record.Class == "C" {
				var mandatory bool
				var grantID string
				if err = tx.QueryRow(ctx, `SELECT mandatory,grant_id::text FROM cairn.record_authority WHERE record_id=$1 AND version=$2`, id, record.Version).Scan(&mandatory, &grantID); err != nil {
					return p, nil, err
				}
				if mandatory {
					_, scopeErr := scopeAuthority(ctx, tx, id)
					if Code(scopeErr) == "AUTHORITY_DENIED" {
						continue
					}
					if scopeErr != nil {
						return p, nil, scopeErr
					}
					_, chainErr := grantChain(ctx, tx, grantID, false)
					if Code(chainErr) == "AUTHORITY_DENIED" {
						continue
					}
					if chainErr != nil {
						return p, nil, chainErr
					}
					return p, nil, failure("POLICY_UNENFORCEABLE", "destination cannot satisfy required policy")
				}
			}
			continue
		}
		terms := rankingTerms(req.Query, p.Ranking)
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
		evaluation := &CandidateEvaluation{RecordID: id, Version: record.Version, Class: record.Class, LexicalMatches: score, ScopeSpecificity: specificity, WrittenAt: record.WrittenAt}
		evaluations[id] = evaluation
		if reason := applicabilityReason(record.Pins, req.Context, now); reason != "" {
			if record.Class == "C" && reason == "CONTEXT_MISSING" {
				entry, gate, err := eligible(ctx, tx, record, req.Purpose)
				if err != nil {
					return p, nil, err
				}
				if gate == "" && entry.Mandatory {
					return p, nil, failure("POLICY_UNENFORCEABLE", "required instruction applicability cannot be established without context pins")
				}
			}
			evaluation.Reason = reason
			p.Omitted[reason]++
			continue
		}
		selection, reason, err := eligible(ctx, tx, record, req.Purpose)
		if err != nil {
			return p, nil, err
		}
		evaluation.Mandatory = selection.Mandatory
		evaluation.Reason = reason
		evaluation.EscalationBlocked = reason == "CLASS_NOT_CONSEQUENTIAL" && (strings.TrimSpace(req.Query) == "" || score > 0)
		if reason != "" {
			p.Omitted[reason]++
			continue
		}
		if record.Class == "C" {
			var key string
			if err = tx.QueryRow(ctx, `SELECT policy_key FROM cairn.record_authority WHERE record_id=$1 AND version=$2`, id, record.Version).Scan(&key); err != nil {
				return p, nil, err
			}
			if previous, ok := policyKeys[key]; ok && previous != record.Body {
				return p, nil, failure("OPEN_CONFLICT", "applicable instructions disagree on a policy key")
			}
			policyKeys[key] = record.Body
		}

		digest := sha256.Sum256([]byte(record.Body))
		evaluation.Facts = &CandidateFacts{BodySHA256: hex.EncodeToString(digest[:]), Sensitivity: record.Sensitivity, AttributionState: record.AttributionState, Evidence: selection.Evidence, Authority: selection.Authority}
		selection.Reason = fmt.Sprintf("lexical matches=%d; scope specificity=%d", score, specificity)
		if !selection.Mandatory && strings.TrimSpace(req.Query) != "" && score == 0 {
			evaluation.Reason = "NO_LEXICAL_MATCH"
			p.Omitted["NO_LEXICAL_MATCH"]++
			continue
		}
		candidates = append(candidates, candidate{selection, score, specificity})
	}
	return p, candidates, nil
}

func packCandidates(p SemanticPackage, candidates []candidate, evaluations map[string]*CandidateEvaluation) (SemanticPackage, error) {
	sortCandidates(candidates)
	optionalCost := 0
	seenBodies := map[string]bool{}
	for rank, candidate := range candidates {
		entry := candidate.selection
		evaluation := evaluations[entry.Record.RecordID]
		evaluation.Rank = rank + 1
		encoded, err := json.Marshal(entry)
		if err != nil {
			return p, err
		}
		cost := len(encoded) + 1
		evaluation.Cost = cost
		if !entry.Mandatory && seenBodies[entry.Record.Body] {
			evaluation.Reason = "REDUNDANT"
			p.Omitted["REDUNDANT"]++
			continue
		}
		if !entry.Mandatory && optionalCost+cost > p.OptionalLimit {
			evaluation.Reason = "OPTIONAL_BUDGET"
			p.Omitted["OPTIONAL_BUDGET"]++
			continue
		}
		evaluation.Reason = "SELECTED"
		p.Selected = append(p.Selected, entry)
		seenBodies[entry.Record.Body] = true
		if !entry.Mandatory {
			optionalCost += cost
		}
	}
	if len(p.Selected) == 0 {
		p.Status = "SCOPE_EMPTY"
	}
	for {
		rendered, err := (Package{Semantic: p}).Render()
		if err != nil {
			return p, err
		}
		if len(rendered) <= p.AvailableTokens {
			break
		}
		n := len(p.Selected)
		if n == 0 || p.Selected[n-1].Mandatory {
			return p, failure("BUDGET_REFUSED", "mandatory context and envelope exceed available input room")
		}
		evaluations[p.Selected[n-1].Record.RecordID].Reason = "TOTAL_BUDGET"
		p.Selected = p.Selected[:n-1]
		p.Omitted["TOTAL_BUDGET"]++
		if len(p.Selected) == 0 {
			p.Status = "SCOPE_EMPTY"
		}
	}
	return p, nil
}

func eligible(ctx context.Context, tx pgx.Tx, r Record, purpose string) (Selection, string, error) {
	chain, err := scopeAuthority(ctx, tx, r.RecordID)
	if Code(err) == "AUTHORITY_DENIED" {
		return Selection{Record: r}, "SCOPE_AUTHORITY_INACTIVE", nil
	}
	if err != nil {
		return Selection{}, "", err
	}
	entry, reason, err := eligibleWithoutScope(ctx, tx, r, purpose)
	if err != nil || reason != "" {
		return entry, reason, err
	}
	// Include each live prerequisite grant once in the retained candidate facts.
	for _, grant := range chain {
		found := false
		for _, existing := range entry.Authority {
			found = found || existing.ID == grant.ID
		}
		if !found {
			entry.Authority = append(entry.Authority, grant)
		}
	}
	return entry, "", nil
}

func eligibleWithoutScope(ctx context.Context, tx pgx.Tx, r Record, purpose string) (Selection, string, error) {
	entry := Selection{Record: r, Evidence: []Evidence{}, Authority: []Grant{}}
	if r.Class == "A" && purpose != "context" {
		return entry, "CLASS_NOT_CONSEQUENTIAL", nil
	}
	if r.Class != "A" {
		var grantID string
		var runtime bool
		err := tx.QueryRow(ctx, `SELECT grant_id::text,mandatory,requires_runtime FROM cairn.record_authority WHERE record_id=$1 AND version=$2`, r.RecordID, r.Version).Scan(&grantID, &entry.Mandatory, &runtime)
		if err != nil {
			return entry, "", err
		}
		chain, err := grantChain(ctx, tx, grantID, false)
		if Code(err) == "AUTHORITY_DENIED" {
			return entry, "AUTHORITY_INACTIVE", nil
		}
		if err != nil {
			return entry, "", err
		}
		entry.Authority = chain
		if runtime && entry.Mandatory {
			return entry, "", failure("POLICY_UNENFORCEABLE", "required runtime mediation is unavailable")
		}
		if runtime {
			return entry, "POLICY_UNENFORCEABLE", nil
		}
	}
	var dependencyMissing bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.deletion_dependency WHERE record_id=$1 AND version=$2)`, r.RecordID, r.Version).Scan(&dependencyMissing); err != nil {
		return entry, "", err
	}
	if dependencyMissing {
		if entry.Mandatory {
			return entry, "", failure("POLICY_UNENFORCEABLE", "required instruction depends on forgotten content; review and revise its support")
		}
		return entry, "EVIDENCE_UNAVAILABLE", nil
	}
	var disputed bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.conflict_member m JOIN cairn.conflict_group g USING(conflict_id) WHERE m.record_id=$1 AND g.resolved_event IS NULL)`, r.RecordID).Scan(&disputed); err != nil {
		return entry, "", err
	}
	if disputed {
		if r.Class == "C" {
			return entry, "", failure("OPEN_CONFLICT", "binding instruction has an unresolved dispute")
		}
		return entry, "OPEN_CONFLICT", nil
	}
	if r.Class == "B" {
		if r.AttributionState != "self" && r.AttributionState != "reconciled" {
			return entry, "ATTRIBUTION_UNRECONCILED", nil
		}
		evidence, err := supportingEvidence(ctx, tx, r.RecordID, r.Version)
		if err != nil {
			return entry, "", err
		}
		entry.Evidence = evidence
		resolvable := false
		for _, e := range evidence {
			if e.State == "resolvable" {
				resolvable = true
			}
		}
		if !resolvable && purpose != "context" {
			return entry, "EVIDENCE_UNAVAILABLE", nil
		}
	}
	return entry, "", nil
}

func lexical(text string) map[string]bool {
	words := strings.FieldsFunc(strings.ToLower(text), func(r rune) bool { return !unicode.IsLetter(r) && !unicode.IsDigit(r) && r != '_' })
	result := map[string]bool{}
	for _, word := range words {
		result[word] = true
	}
	return result
}

func (s *Store) commitRetrieval(ctx context.Context, tx pgx.Tx, req CompileRequest, semantic SemanticPackage, canonical []byte, seal string, evaluations map[string]*CandidateEvaluation) (Package, error) {
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return Package{}, err
	}
	encoded, err := json.Marshal(struct {
		Request     CompileRequest
		Destination Destination
	}{req, semantic.Destination})
	if err != nil {
		return Package{}, err
	}
	digest := sha256.Sum256(encoded)
	var oldDigest []byte
	var oldSeal, id, nonce string
	err = tx.QueryRow(ctx, `SELECT receipt_id::text,nonce::text,request_digest,seal FROM cairn.retrieval_receipt WHERE caller=$1 AND request_id=$2`, s.channel.Principal, req.RequestID).Scan(&id, &nonce, &oldDigest, &oldSeal)
	if err == nil {
		if !bytes.Equal(oldDigest, digest[:]) {
			return Package{}, failure("IDEMPOTENCY_CONFLICT", "compile request ID has different intent")
		}
		if err = receiptDeliveryCurrent(ctx, tx, id); err != nil {
			return Package{}, err
		}
		if err = receiptPayloadAvailable(ctx, tx, id); err != nil {
			return Package{}, err
		}
		if oldSeal != seal {
			return Package{}, failure("STALE_PACKAGE", "source state changed; use a new compile request")
		}
		return Package{id, nonce, seal, semantic}, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return Package{}, err
	}
	id = uuid.NewString()
	nonce = uuid.NewString()
	_, err = tx.Exec(ctx, `INSERT INTO cairn.retrieval_receipt(receipt_id,request_id,request_digest,scope,purpose,destination,semantic_body,seal,status,nonce,explanation_version,generation) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,2,$11)`, id, req.RequestID, digest[:], req.Scope, req.Purpose, semantic.Destination.Name, canonical, seal, semantic.Status, nonce, generation)
	if err != nil {
		return Package{}, err
	}
	if semantic.PolicyRevision != nil {
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.retrieval_policy(receipt_id,revision_id) VALUES($1,$2)`, id, semantic.PolicyRevision.RevisionID); err != nil {
			return Package{}, err
		}
	}
	for _, e := range evaluations {
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.retrieval_candidate(receipt_id,record_id,version,reason,escalation_blocked,detail) VALUES($1,$2,$3,$4,$5,$6)`, id, e.RecordID, e.Version, e.Reason, e.EscalationBlocked, e); err != nil {
			return Package{}, err
		}
	}
	// Lock exposure generations in stable ID order, independent of query rank.
	uses := slices.Clone(semantic.Selected)
	kinds := map[string]string{}
	for _, e := range semantic.Index {
		uses = append(uses, Selection{Record: Record{RecordID: e.RecordID, Version: e.Version}})
		kinds[e.RecordID] = "index"
	}

	slices.SortFunc(uses, func(a, b Selection) int { return strings.Compare(a.Record.RecordID, b.Record.RecordID) })
	for _, entry := range uses {
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.record_use(receipt_id,record_id,version,purpose,exposure_kind) VALUES($1,$2,$3,$4,$5)`, id, entry.Record.RecordID, entry.Record.Version, req.Purpose, exposureKind(kinds[entry.Record.RecordID])); err != nil {
			return Package{}, err
		}
	}
	if semantic.Mode == "index" {
		if err := createIndexSession(ctx, tx, id, semantic); err != nil {
			return Package{}, err
		}
	}
	return Package{id, nonce, seal, semantic}, nil
}

// Version 1 remains available for historical receipts. Version 2 excludes a
// fixed English function-word set; domain tokens and negation remain meaningful.
func rankingTerms(text, version string) map[string]bool {
	terms := lexical(text)
	if version == "lexical-scope-recency/2" {
		for _, word := range strings.Fields("a an and are as at be by for from in is it of on or that the this to was were with") {
			delete(terms, word)
		}
	}
	return terms
}

func sortCandidates(candidates []candidate) {
	slices.SortFunc(candidates, func(a, b candidate) int {
		if a.selection.Mandatory != b.selection.Mandatory {
			if a.selection.Mandatory {
				return -1
			}
			return 1
		}
		if a.score != b.score {
			return b.score - a.score
		}
		if a.specificity != b.specificity {
			return b.specificity - a.specificity
		}
		if c := b.selection.Record.WrittenAt.Compare(a.selection.Record.WrittenAt); c != 0 {
			return c
		}
		return strings.Compare(a.selection.Record.RecordID, b.selection.Record.RecordID)
	})
}

func exposureKind(kind string) string {
	if kind == "index" {
		return kind
	}
	return "body"
}
