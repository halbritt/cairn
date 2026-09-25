package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"path"
	"slices"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
)

// EntityRef is an explicit, fallible association within the record's repository.
// Names are case-sensitive; Cairn does not resolve aliases, renames or symbols.
type EntityRef struct {
	Kind string `json:"kind"`
	Name string `json:"name"`
}

func NormalizeEntities(refs []EntityRef) ([]EntityRef, error) {
	for _, ref := range refs {
		if ref.Kind != "file" && ref.Kind != "symbol" {
			return nil, failure("INVALID_REQUEST", "entity kind must be file or symbol")
		}
		if ref.Name == "" || len(ref.Name) > 512 || !utf8.ValidString(ref.Name) || strings.TrimSpace(ref.Name) != ref.Name || strings.ContainsFunc(ref.Name, unicode.IsControl) {
			return nil, failure("INVALID_REQUEST", "entity name requires 1-512 valid UTF-8 bytes without surrounding whitespace or control characters")
		}
		if ref.Kind == "file" && (path.IsAbs(ref.Name) || path.Clean(ref.Name) != ref.Name || ref.Name == "." || ref.Name == ".." || strings.HasPrefix(ref.Name, "../") || strings.ContainsAny(ref.Name, "\\:")) {
			return nil, failure("INVALID_REQUEST", "file entity requires a canonical repository-relative path using forward slashes")
		}
	}
	if len(refs) == 0 {
		return nil, nil
	}
	refs = slices.Clone(refs)
	slices.SortFunc(refs, func(a, b EntityRef) int {
		if c := strings.Compare(a.Kind, b.Kind); c != 0 {
			return c
		}
		return strings.Compare(a.Name, b.Name)
	})
	refs = slices.Compact(refs)
	if len(refs) > 16 {
		return nil, failure("INVALID_REQUEST", "entities permits at most 16 distinct file or symbol associations")
	}
	return refs, nil
}

func entitiesDigest(refs []EntityRef) string {
	if len(refs) == 0 {
		return ""
	}
	b, _ := json.Marshal(refs) // fixed string fields cannot fail
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// EntityIntentSHA256 identifies a normalized hint set without retaining its names.
// Empty hints have an empty digest for compatibility with earlier requests.
func EntityIntentSHA256(refs []EntityRef) (string, error) {
	refs, err := NormalizeEntities(refs)
	if err != nil {
		return "", err
	}
	return entitiesDigest(refs), nil
}

func readEntities(ctx context.Context, tx pgx.Tx, id string, version int) ([]EntityRef, error) {
	var refs []EntityRef
	err := tx.QueryRow(ctx, `SELECT entities FROM cairn.record_entities WHERE record_id=$1 AND version=$2`, id, version).Scan(&refs)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	return refs, err
}

func matchesEntities(record, query []EntityRef) bool {
	for _, ref := range query {
		if slices.Contains(record, ref) {
			return true
		}
	}
	return false
}

func hasEntityRanking(version string) bool {
	return version == "lexical-scope-recency/7" || version == "semantic-scope-recency/4"
}

func entityReason(reason string, matched bool) string {
	if matched {
		return "explicit entity association match; " + reason
	}
	return reason
}

func withEntitySchema(p SemanticPackage) SemanticPackage {
	if p.AdvisoryConflicts {
		p.Schema = "cairn.semantic/14"
		return p
	}
	for _, selection := range p.Selected {
		if len(selection.Record.Entities) > 0 {
			p.Schema = "cairn.semantic/13"
			return p
		}
	}
	for _, entry := range p.Index {
		if len(entry.Entities) > 0 {
			p.Schema = "cairn.semantic/13"
			return p
		}
	}
	return p
}
