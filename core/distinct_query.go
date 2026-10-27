package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"unicode/utf8"

	"github.com/fxamacker/cbor/v2"
	"github.com/jackc/pgx/v5"
)

// DistinctQuerySchema acknowledges the versioned, sealed two-input contract.
// It is not an attestation that a semantic worker ran or that a result is useful.
const DistinctQuerySchema = "cairn.semantic/20"

const distinctQuerySchema = DistinctQuerySchema

// The raw semantic input never enters a receipt or its public delivery.
type semanticQueryIntent struct {
	Schema     string `cbor:"schema"`
	SHA256     string `cbor:"sha256"`
	BaseSchema string `cbor:"base_schema"`
}

func queryInputDigest(query string) string {
	sum := sha256.Sum256([]byte(query))
	return hex.EncodeToString(sum[:])
}

func compileIntentDigest(req CompileRequest, dest Destination) ([32]byte, error) {
	encoded, err := json.Marshal(struct {
		Request     CompileRequest
		Destination Destination
	}{req, dest})
	if err != nil {
		return [32]byte{}, err
	}
	return sha256.Sum256(encoded), nil
}

// Check both retry directions before discovery. Legacy-only requests retain
// their historical commit-time comparison; a distinct receipt cannot silently
// lose its input when a caller retries through that legacy route.
func (s *Store) distinctRetryIntent(ctx context.Context, tx pgx.Tx, req CompileRequest, dest Destination) error {
	var body, retainedDigest []byte
	var receiptID string
	err := tx.QueryRow(ctx, `SELECT receipt_id,semantic_body,request_digest FROM cairn.retrieval_receipt WHERE caller=$1 AND request_id=$2`, s.channel.Principal, req.RequestID).Scan(&receiptID, &body, &retainedDigest)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}
	var retained SemanticPackage
	decodeErr := cbor.Unmarshal(body, &retained)
	if req.SemanticQuery == "" && retained.Schema != distinctQuerySchema && retained.QueryIntent == nil {
		return nil
	}
	if err := receiptPayloadAvailable(ctx, tx, receiptID); err != nil {
		return err
	}
	if decodeErr != nil {
		return failure("INTEGRITY_FAILURE", "retained semantic bytes are invalid")
	}
	digest, err := compileIntentDigest(req, dest)
	if err != nil {
		return err
	}
	if !bytes.Equal(retainedDigest, digest[:]) {
		return failure("IDEMPOTENCY_CONFLICT", "compile request ID has different intent")
	}
	_, err = queryContract(retained, req.Query, req.SemanticQuery)
	return err
}

func validateDistinctInputs(query, semanticQuery string) error {
	if semanticQuery == "" {
		return nil
	}
	if len(query)+len(semanticQuery) > 4096 || !utf8.ValidString(semanticQuery) || strings.ContainsRune(semanticQuery, 0) || strings.TrimSpace(semanticQuery) == "" {
		return failure("INVALID_REQUEST", "semantic query is invalid or combined query input exceeds limits")
	}
	return nil
}

func validateDistinctCompile(req CompileRequest, indexed bool) error {
	if err := validateDistinctInputs(req.Query, req.SemanticQuery); err != nil {
		return err
	}
	if req.SemanticQuery == "" {
		return nil
	}
	if !req.Semantic || req.Mode != "index" || req.Purpose != "context" || strings.TrimSpace(req.Query) == "" || req.BrowseOffset != nil {
		return failure("INVALID_REQUEST", "semantic query requires indexed semantic context discovery and a lexical query")
	}
	if !indexed {
		return failure("UNSUPPORTED_QUERY_INPUT", "distinct semantic query requires an indexed backend")
	}
	return nil
}

// Return a validation view only. Packing and sealing retain the wrapper schema
// and its private intent; readers must not reinterpret the underlying bytes.
func queryContract(p SemanticPackage, query, semanticQuery string) (SemanticPackage, error) {
	view, err := queryPackageContract(p)
	if err != nil {
		return p, err
	}
	if p.Schema == distinctQuerySchema {
		if semanticQuery == "" || p.QueryIntent.SHA256 != queryInputDigest(semanticQuery) {
			return p, failure("INVALID_REQUEST", "semantic query does not match historical intent")
		}
		if strings.TrimSpace(query) == "" {
			return p, failure("INVALID_REQUEST", "distinct query requires a lexical input")
		}
	} else if semanticQuery != "" {
		return p, failure("INVALID_REQUEST", "historical receipt has no distinct semantic input")
	}
	return view, nil
}

// Every sealed-package reader validates the private input binding even when it
// needs no query text (historical inspection and current-authorized expansion).
func decodeSemanticPackage(body []byte, p *SemanticPackage) error {
	if err := cbor.Unmarshal(body, p); err != nil {
		return err
	}
	_, err := queryPackageContract(*p)
	return err
}

func queryPackageContract(p SemanticPackage) (SemanticPackage, error) {
	if p.Schema != distinctQuerySchema {
		if p.QueryIntent != nil {
			return p, failure("INTEGRITY_FAILURE", "semantic input binding has the wrong package schema")
		}
		return p, nil
	}
	i := p.QueryIntent
	if i == nil || i.Schema != "cairn.semantic-query-intent/1" || !digestValid(i.SHA256) || i.SHA256 != strings.ToLower(i.SHA256) {
		return p, failure("INTEGRITY_FAILURE", "invalid semantic input binding")
	}
	if p.Mode != "index" || p.Purpose != "context" || p.Browse != nil || p.Discovery == nil || !strings.HasPrefix(p.Query, "sha256:") || !digestValid(strings.TrimPrefix(p.Query, "sha256:")) {
		return p, failure("INTEGRITY_FAILURE", "invalid distinct query package intent")
	}
	switch p.Discovery.State {
	case "ready":
	case "unavailable", "invalid_result", "not_needed":
		if *p.Discovery != (DiscoveryRanking{State: p.Discovery.State}) || !hasFallbackLexicalRanking(p.Ranking) {
			return p, failure("INTEGRITY_FAILURE", "invalid distinct query fallback contract")
		}
	default:
		return p, failure("INTEGRITY_FAILURE", "invalid distinct query discovery state")
	}
	switch i.BaseSchema {
	case "cairn.semantic/16":
		if p.IDF != nil || (p.Discovery.State == "ready" && (!hasInterleavedRanking(p.Ranking) || hasIndexedIDFRanking(p.Ranking) || p.Discovery.QueryProjection != nil)) {
			return p, failure("INTEGRITY_FAILURE", "invalid distinct query base contract")
		}
	case "cairn.semantic/18":
		if p.Discovery.State != "ready" || !hasInterleavedRanking(p.Ranking) || hasIndexedIDFRanking(p.Ranking) || p.IDF != nil || p.Discovery.QueryProjection == nil {
			return p, failure("INTEGRITY_FAILURE", "invalid distinct query projection contract")
		}
	case "cairn.semantic/19":
		if p.Discovery.State != "ready" || !hasIndexedIDFRanking(p.Ranking) || p.IDF == nil {
			return p, failure("INTEGRITY_FAILURE", "invalid distinct query IDF contract")
		}
	default:
		return p, failure("INTEGRITY_FAILURE", "unsupported distinct query base schema")
	}
	p.Schema = i.BaseSchema
	return p, nil
}
