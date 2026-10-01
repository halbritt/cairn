package core

import (
	"context"
	"encoding/json"
	"strings"

	"github.com/jackc/pgx/v5"
)

const explanationPageBytes = 30 * 1024

// ExplainPageRequest addresses only the immutable membership of one owned receipt.
// Zero Limit uses 20; the cursor is the last returned record/version pair.
type ExplainPageRequest struct {
	ReceiptID     string `json:"receipt_id"`
	AfterRecordID string `json:"after_record_id,omitempty"`
	AfterVersion  int    `json:"after_version,omitempty"`
	Limit         int    `json:"limit,omitempty"`
}

// CandidateReference is a fixed diagnostic projection, never an authority or
// expansion capability. Null scores/costs were not computed by the old reader.
type CandidateReference struct {
	RecordID         string  `json:"record_id"`
	Version          int     `json:"version"`
	Reason           string  `json:"reason"`
	Rank             int     `json:"rank"`
	Cost             *int    `json:"cost_bytes"`
	LexicalMatches   int     `json:"lexical_matches"`
	ScopeSpecificity int     `json:"scope_specificity"`
	Mandatory        bool    `json:"mandatory"`
	ExactTextMatch   bool    `json:"exact_text_match"`
	EntityMatch      bool    `json:"entity_match"`
	FailureMatch     bool    `json:"failure_match"`
	IDFScore         *int64  `json:"idf_score"`
	SemanticScore    *int    `json:"semantic_score"`
	BodySHA256       *string `json:"body_sha256"`
	FactsPresent     bool    `json:"facts_present"`
}

type ExplanationPage struct {
	Schema             string               `json:"schema"`
	Historical         bool                 `json:"historical"`
	ReceiptID          string               `json:"receipt_id"`
	ExplanationVersion int                  `json:"explanation_version"`
	Seal               string               `json:"seal"`
	Coverage           string               `json:"coverage"`
	Candidates         []CandidateReference `json:"candidates"`
	More               bool                 `json:"more"`
	NextAfterRecordID  string               `json:"next_after_record_id,omitempty"`
	NextAfterVersion   int                  `json:"next_after_version,omitempty"`
}

func candidateInspectionUnavailable() error {
	return failure("PAYLOAD_UNAVAILABLE", "historical candidate inspection unavailable")
}

// ExplainPageForDestination reports retained diagnostics, without reading source
// bodies or recompiling the package. Every referenced identity must still be
// disclosable, even when it was unranked or is outside this page.
func (s *Store) ExplainPageForDestination(ctx context.Context, req ExplainPageRequest, dest Destination) (ExplanationPage, error) {
	empty := ExplanationPage{}
	if err := validID(req.ReceiptID); err != nil {
		return empty, err
	}
	if (dest.Name != "local" && dest.Name != "hosted") || (dest.Name == "hosted" && dest.AllowLocal) {
		return empty, failure("INVALID_REQUEST", "invalid inspection destination")
	}
	if req.Limit < 0 || req.Limit > 100 || req.AfterVersion < 0 || req.AfterVersion > 2147483647 || (req.AfterRecordID == "") != (req.AfterVersion == 0) {
		return empty, failure("INVALID_REQUEST", "inspection requires limit 0-100 and a complete record/version cursor")
	}
	if req.AfterRecordID != "" {
		if err := validID(req.AfterRecordID); err != nil {
			return empty, err
		}
	}
	if req.Limit == 0 {
		req.Limit = 20
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return empty, err
	}
	defer tx.Rollback(ctx)
	if err = s.receiptAccess(ctx, tx, req.ReceiptID); err != nil {
		return empty, err
	}
	out := ExplanationPage{Schema: "cairn.receipt-candidates/1", Historical: true, ReceiptID: req.ReceiptID, Coverage: "retained_evaluated_set", Candidates: []CandidateReference{}}
	var repo, destination string
	if err = tx.QueryRow(ctx, `SELECT scope->>'repo',destination,explanation_version,CASE WHEN octet_length(seal)=71 THEN seal ELSE '' END FROM cairn.retrieval_receipt WHERE receipt_id=$1`, req.ReceiptID).Scan(&repo, &destination, &out.ExplanationVersion, &out.Seal); err != nil {
		return empty, err
	}
	if destination != dest.Name {
		return empty, failure("AUTHORITY_DENIED", "inspection destination differs from the receipt destination")
	}
	if err = receiptPayloadAvailable(ctx, tx, req.ReceiptID); err != nil {
		if Code(err) == "PAYLOAD_UNAVAILABLE" {
			return empty, candidateInspectionUnavailable()
		}
		return empty, err
	}
	if !strings.HasPrefix(out.Seal, "blake3:") || !digestValid(strings.TrimPrefix(out.Seal, "blake3:")) {
		return empty, failure("INTEGRITY_FAILURE", "retained receipt metadata is invalid")
	}
	if out.ExplanationVersion != 2 {
		return empty, failure("REPLAY_INCOMPLETE", "receipt lacks supported candidate metadata")
	}
	var count int
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM (SELECT 1 FROM cairn.retrieval_candidate WHERE receipt_id=$1 LIMIT 10001) bounded`, req.ReceiptID).Scan(&count); err != nil {
		return empty, err
	}
	if count > 10000 {
		return empty, failure("REPLAY_INCOMPLETE", "retained candidate set exceeds supported bound")
	}
	// Inner joins deliberately detect missing identities/versions via the count.
	// Stable row locks protect privacy and forgetting through this inspection transaction.
	rows, err := tx.Query(ctx, `SELECT m.record_id::text,
 cv.repo=$2 AND v.repo=$2 AND m.lifecycle<>'tombstoned'
 AND v.payload_deleted_by IS NULL
 AND ($3 OR m.sensitivity='shareable')
 AND (NOT(c.detail ? 'facts') OR
      (jsonb_typeof(c.detail->'facts')='object'
       AND c.detail#>>'{facts,sensitivity}' IN ('local','shareable')
       AND ($3 OR c.detail#>>'{facts,sensitivity}'='shareable')))
 FROM cairn.retrieval_candidate c
 JOIN cairn.memory_record m ON m.record_id=c.record_id
 JOIN cairn.record_version cv ON cv.record_id=m.record_id AND cv.version=m.current_version
 JOIN cairn.record_version v ON v.record_id=c.record_id AND v.version=c.version
 WHERE c.receipt_id=$1 ORDER BY m.record_id,c.version FOR SHARE OF m`, req.ReceiptID, repo, dest.AllowLocal)
	if err != nil {
		return empty, err
	}
	scanned := 0
	allowed := true
	for rows.Next() {
		var id string
		var ok *bool
		if err = rows.Scan(&id, &ok); err != nil {
			rows.Close()
			return empty, err
		}
		scanned++
		if ok == nil || !*ok {
			allowed = false
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return empty, err
	}
	if scanned != count || !allowed {
		return empty, candidateInspectionUnavailable()
	}
	if req.AfterRecordID != "" {
		var exists bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.retrieval_candidate WHERE receipt_id=$1 AND record_id=$2 AND version=$3)`, req.ReceiptID, req.AfterRecordID, req.AfterVersion).Scan(&exists); err != nil {
			return empty, err
		}
		if !exists {
			return empty, failure("INVALID_REQUEST", "cursor is not a member of this receipt")
		}
	}
	// Project in SQL so arbitrary nested Facts, evidence and hostile extra keys
	// never cross the database boundary. The per-row cap also bounds malformed data.
	rows, err = tx.Query(ctx, `WITH page AS (
 SELECT c.record_id,c.version,c.reason,c.detail FROM cairn.retrieval_candidate c
 WHERE c.receipt_id=$1 AND ($2='' OR (c.record_id,c.version)>(NULLIF($2,'')::uuid,$3))
 ORDER BY c.record_id,c.version LIMIT $4
 ), projected AS (
 SELECT record_id,version,reason,jsonb_build_object(
 'record_id',detail->'record_id','version',detail->'version','reason',detail->'reason',
 'rank',detail->'rank','cost_bytes',detail->'cost_bytes',
 'lexical_matches',detail->'lexical_matches','scope_specificity',detail->'scope_specificity',
 'mandatory',detail->'mandatory',
 'exact_text_match',CASE WHEN detail ? 'exact_text_match' THEN detail->'exact_text_match' ELSE 'false'::jsonb END,
 'entity_match',CASE WHEN detail ? 'entity_match' THEN detail->'entity_match' ELSE 'false'::jsonb END,
 'failure_match',detail ? 'failure_match',
 'idf_score',detail->'idf_score',
 'semantic_score',CASE WHEN detail ? 'passage_hit' THEN detail#>'{passage_hit,score}' ELSE detail->'semantic_score' END,
 'body_sha256',detail#>'{facts,body_sha256}', 'facts_present',detail ? 'facts',
 'failure_type_valid',NOT(detail ? 'failure_match') OR jsonb_typeof(detail->'failure_match')='object',
 'passage_type_valid',NOT(detail ? 'passage_hit') OR (jsonb_typeof(detail->'passage_hit')='object' AND jsonb_typeof(detail#>'{passage_hit,score}')='number')
 ) AS projection FROM page
 ) SELECT record_id::text,version,CASE WHEN octet_length(reason)<=64 THEN reason ELSE '' END,
 CASE WHEN octet_length(projection::text)<=4096 THEN projection ELSE NULL END
 FROM projected ORDER BY record_id,version`, req.ReceiptID, req.AfterRecordID, req.AfterVersion, req.Limit+1)
	if err != nil {
		return empty, err
	}
	for rows.Next() {
		var id, reason string
		var version int
		var raw []byte
		if err = rows.Scan(&id, &version, &reason, &raw); err != nil {
			rows.Close()
			return empty, err
		}
		if len(out.Candidates) == req.Limit {
			out.More = true
			break
		}
		entry, decodeErr := decodeCandidateReference(raw, id, version, reason)
		if decodeErr != nil {
			rows.Close()
			return empty, decodeErr
		}
		out.Candidates = append(out.Candidates, entry)
		// Reserve the actual continuation fields during whole-row admission.
		out.More = true
		out.NextAfterRecordID = id
		out.NextAfterVersion = version
		encoded, encodeErr := json.Marshal(out)
		if encodeErr != nil {
			rows.Close()
			return empty, encodeErr
		}
		if len(encoded) > explanationPageBytes {
			out.Candidates = out.Candidates[:len(out.Candidates)-1]
			if len(out.Candidates) == 0 {
				rows.Close()
				return empty, failure("BUDGET_REFUSED", "candidate page exceeds its byte bound")
			}
			break
		}
		out.More = false
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return empty, err
	}
	out.NextAfterRecordID = ""
	out.NextAfterVersion = 0
	if out.More {
		last := out.Candidates[len(out.Candidates)-1]
		out.NextAfterRecordID = last.RecordID
		out.NextAfterVersion = last.Version
	}
	if err = tx.Commit(ctx); err != nil {
		return empty, err
	}
	return out, nil
}

func decodeCandidateReference(raw []byte, id string, version int, reason string) (CandidateReference, error) {
	bad := func() (CandidateReference, error) {
		return CandidateReference{}, failure("INTEGRITY_FAILURE", "retained candidate metadata is invalid")
	}
	var fields map[string]json.RawMessage
	if len(raw) == 0 || json.Unmarshal(raw, &fields) != nil {
		return bad()
	}
	for _, key := range []string{"record_id", "version", "reason", "rank", "cost_bytes", "lexical_matches", "scope_specificity", "mandatory", "exact_text_match", "entity_match", "failure_match", "facts_present", "failure_type_valid", "passage_type_valid"} {
		if v, ok := fields[key]; !ok || string(v) == "null" {
			return bad()
		}
	}
	var validity struct {
		Failure bool `json:"failure_type_valid"`
		Passage bool `json:"passage_type_valid"`
	}
	var e CandidateReference
	if json.Unmarshal(raw, &e) != nil || json.Unmarshal(raw, &validity) != nil || !validity.Failure || !validity.Passage {
		return bad()
	}
	if e.RecordID != id || e.Version != version || e.Reason != reason || version < 1 || e.Rank < 0 || e.Rank > 10000 || e.LexicalMatches < 0 || e.LexicalMatches > 4096 || e.ScopeSpecificity < 0 || e.ScopeSpecificity > 2 || e.Cost == nil || *e.Cost < 0 || *e.Cost > 2147483647 {
		return bad()
	}
	switch e.Reason {
	case "SELECTED", "INDEXED", "NO_LEXICAL_MATCH", "NO_RETRIEVAL_MATCH", "NO_ENTITY_MATCH", "NO_FAILURE_MATCH", "KIND_FILTERED", "CONTEXT_MISSING", "CURRENTNESS_MISMATCH", "OUTSIDE_VALIDITY", "CLASS_NOT_CONSEQUENTIAL", "AUTHORITY_INACTIVE", "SCOPE_AUTHORITY_INACTIVE", "POLICY_UNENFORCEABLE", "OPEN_CONFLICT", "ATTRIBUTION_UNRECONCILED", "EVIDENCE_UNAVAILABLE", "REDUNDANT", "OPTIONAL_BUDGET", "TOTAL_BUDGET", "BROWSE_OFFSET", "PAGE_OFFSET", "CATEGORY_BUDGET", "WHOLE_PULL_BUDGET":
	default:
		return CandidateReference{}, failure("REPLAY_INCOMPLETE", "retained candidate reason is unsupported")
	}
	if e.IDFScore != nil && *e.IDFScore < 0 {
		return bad()
	}
	if e.SemanticScore != nil && (*e.SemanticScore < -1000000 || *e.SemanticScore > 1000000) {
		return bad()
	}
	if e.FactsPresent {
		if e.BodySHA256 == nil || !digestValid(*e.BodySHA256) {
			return bad()
		}
	} else if e.BodySHA256 != nil {
		return bad()
	} else {
		// Explanation v2 writers omit Facts only when the structured gate
		// rejected the candidate before the frozen eligible facts were built.
		switch e.Reason {
		case "CLASS_NOT_CONSEQUENTIAL", "AUTHORITY_INACTIVE", "SCOPE_AUTHORITY_INACTIVE", "POLICY_UNENFORCEABLE", "OPEN_CONFLICT", "ATTRIBUTION_UNRECONCILED", "EVIDENCE_UNAVAILABLE", "CONTEXT_MISSING", "CURRENTNESS_MISMATCH", "OUTSIDE_VALIDITY":
		default:
			return CandidateReference{}, failure("REPLAY_INCOMPLETE", "eligible candidate facts are missing")
		}
	}
	if *e.Cost == 0 {
		e.Cost = nil
	}
	return e, nil
}
