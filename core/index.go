package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"slices"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/fxamacker/cbor/v2"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type IndexEntry struct {
	Category   string `json:"category,omitempty" cbor:"category,omitempty"`
	RecordID   string `json:"record_id"`
	Version    int    `json:"version"`
	Class      string `json:"class"`
	Kind       string `json:"kind"`
	Summary    string `json:"summary"`
	BodySHA256 string `json:"body_sha256"`
}

// BrowsePage addresses eligible optional candidates in this call's ordering.
// Each call reads current state; offsets do not identify a retained snapshot.
type BrowsePage struct {
	Offset     int  `json:"offset" cbor:"offset"`
	NextOffset *int `json:"next_offset,omitempty" cbor:"next_offset,omitempty"`
}

type IndexHandle struct {
	RecordID string `json:"record_id"`
	Version  int    `json:"version"`
	Handle   string `json:"handle"`
}
type IndexResult struct {
	Package          Package       `json:"package"`
	Handles          []IndexHandle `json:"handles"`
	ExpiresAt        time.Time     `json:"expires_at"`
	CreditsRemaining int           `json:"credits_remaining"`
	BytesRemaining   int           `json:"bytes_remaining"`
}

func indexEntry(r Record) IndexEntry {
	summary := r.Body
	if len(summary) > 160 {
		summary = summary[:160]
		for !utf8.ValidString(summary) {
			summary = summary[:len(summary)-1]
		}
	}
	sum := sha256.Sum256([]byte(r.Body))
	return IndexEntry{RecordID: r.RecordID, Version: r.Version, Class: r.Class, Kind: r.Kind, Summary: summary, BodySHA256: hex.EncodeToString(sum[:])}
}
func packIndex(p SemanticPackage, candidates []candidate, evaluations map[string]*CandidateEvaluation, query string) (SemanticPackage, error) {
	sortCandidates(candidates)
	instructions := newInstructionBudget(&p)
	p.Index = []IndexEntry{}
	p.Selected = []Selection{}
	optionalCost := 0
	seen := map[string]bool{}
	positions := map[string]int{}
	optionalPosition, pageStopped := 0, false
	if p.Browse != nil {
		p.Browse = &BrowsePage{Offset: p.Browse.Offset}
		p.Omitted["BROWSE_OFFSET"] = 0
	}
	for rank, c := range candidates {
		e := evaluations[c.selection.Record.RecordID]
		e.Rank = rank + 1
		if c.selection.Mandatory {
			if _, err := instructions.admit(c.selection, e, p.Omitted); err != nil {
				return p, err
			}
			encoded, err := json.Marshal(c.selection)
			if err != nil {
				return p, err
			}
			e.Cost = len(encoded) + 1
			seen[indexEntry(c.selection.Record).BodySHA256] = true
			p.Selected = append(p.Selected, c.selection)
			e.Reason = "SELECTED"
			continue
		}
		if p.Browse != nil {
			positions[c.selection.Record.RecordID] = optionalPosition
			optionalPosition++
			if optionalPosition <= p.Browse.Offset {
				e.Reason = "BROWSE_OFFSET"
				p.Omitted["BROWSE_OFFSET"]++
				continue
			}
			if pageStopped {
				e.Reason = "OPTIONAL_BUDGET"
				p.Omitted["OPTIONAL_BUDGET"]++
				continue
			}
		}
		entry := indexEntry(c.selection.Record)
		if p.Schema == "cairn.semantic/5" || p.Schema == "cairn.semantic/6" || p.Schema == "cairn.semantic/7" {
			entry.Summary = indexSummary(c.selection.Record.Body, query, p.Ranking)
		}
		entry.Category = c.selection.Category
		encoded, err := json.Marshal(entry)
		if err != nil {
			return p, err
		}
		// Reserve space for the separately delivered opaque handle. It is a delivery
		// identifier and deliberately stays outside the semantic seal.
		cost := len(encoded) + 160
		e.Cost = cost
		if seen[entry.BodySHA256] {
			e.Reason = "REDUNDANT"
			p.Omitted["REDUNDANT"]++
			continue
		}
		if len(p.Index) >= 100 || optionalCost+cost > p.OptionalLimit {
			e.Reason = "OPTIONAL_BUDGET"
			p.Omitted["OPTIONAL_BUDGET"]++
			if p.Browse != nil && cost <= p.OptionalLimit {
				position := positions[entry.RecordID]
				p.Browse.NextOffset = &position
				pageStopped = true
			}
			continue
		}
		if admitted, err := instructions.admit(c.selection, e, p.Omitted); err != nil {
			return p, err
		} else if !admitted {
			continue
		}
		p.Index = append(p.Index, entry)
		optionalCost += cost
		seen[entry.BodySHA256] = true
		e.Reason = "INDEXED"
	}
	for {
		if len(p.Index) == 0 && len(p.Selected) == 0 {
			p.Status = "SCOPE_EMPTY"
		}
		p = discoveryStatus(p)
		rendered, err := (Package{Semantic: p}).Render()
		if err != nil {
			return p, err
		}
		if len(rendered)+160*len(p.Index)+512 <= p.AvailableTokens {
			break
		}
		if len(p.Index) == 0 {
			return p, failure("BUDGET_REFUSED", "mandatory bootstrap and index envelope exceed input room")
		}
		e := p.Index[len(p.Index)-1]
		if p.Browse != nil {
			position := positions[e.RecordID]
			p.Browse.NextOffset = &position
		}
		evaluations[e.RecordID].Reason = "TOTAL_BUDGET"
		p.Index = p.Index[:len(p.Index)-1]
		p.Omitted["TOTAL_BUDGET"]++
	}
	if p.Browse != nil && p.Browse.NextOffset != nil && len(p.Index) == 0 {
		return p, failure("BUDGET_REFUSED", "browse page cannot fit a preview; increase available input room")
	}
	return p, nil
}
func createIndexSession(ctx context.Context, tx pgx.Tx, id string, p SemanticPackage) error {
	rendered, err := (Package{Semantic: p}).Render()
	if err != nil {
		return err
	}
	budget := min(24000, max(0, p.AvailableTokens-len(rendered)-160*len(p.Index)-512))
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.index_session(receipt_id,remaining_bytes) VALUES($1,$2)`, id, budget); err != nil {
		return err
	}
	// Match the compiler's stable lifecycle lock order, independent of rank.
	entries := append([]IndexEntry{}, p.Index...)
	sortIndexEntries(entries)
	for _, entry := range entries {

		if _, err = tx.Exec(ctx, `INSERT INTO cairn.index_handle(handle,receipt_id,record_id,version) VALUES($1,$2,$3,$4)`, uuid.NewString(), id, entry.RecordID, entry.Version); err != nil {
			return err
		}
	}
	return nil
}
func (s *Store) Index(ctx context.Context, req CompileRequest, dest Destination) (IndexResult, error) {
	req.Mode = "index"
	p, err := s.Compile(ctx, req, dest)
	if err != nil {
		return IndexResult{}, err
	}
	result := IndexResult{Package: p, Handles: []IndexHandle{}}
	tx, err := s.begin(ctx)
	if err != nil {
		return result, err
	}
	defer tx.Rollback(ctx)
	if err = s.receiptAccess(ctx, tx, p.ReceiptID); err != nil {
		return result, err
	}
	if err = receiptDeliveryCurrent(ctx, tx, p.ReceiptID); err != nil {
		return result, err
	}
	if err = tx.QueryRow(ctx, `SELECT expires_at,credits,remaining_bytes FROM cairn.index_session WHERE receipt_id=$1`, p.ReceiptID).Scan(&result.ExpiresAt, &result.CreditsRemaining, &result.BytesRemaining); err != nil {
		return result, err
	}
	rows, err := tx.Query(ctx, `SELECT record_id::text,version,handle::text FROM cairn.index_handle WHERE receipt_id=$1 ORDER BY record_id`, p.ReceiptID)
	if err != nil {
		return result, err
	}
	result.Handles, err = pgx.CollectRows(rows, func(row pgx.CollectableRow) (IndexHandle, error) {
		var h IndexHandle
		err := row.Scan(&h.RecordID, &h.Version, &h.Handle)
		return h, err
	})
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}

type ExpandRequest struct {
	RequestID string `json:"request_id"`
	ReceiptID string `json:"receipt_id"`
	Handle    string `json:"handle"`
}
type Expansion struct {
	Selection        Selection `json:"selection"`
	CreditsRemaining int       `json:"credits_remaining"`
	BytesRemaining   int       `json:"bytes_remaining"`
}

type expansionState struct {
	selection          Selection
	credits, remaining int
}

// Both body and evidence pulls recheck this state before idempotency lookup.
func (s *Store) prepareExpansion(ctx context.Context, tx pgx.Tx, req ExpandRequest, dest Destination, state *expansionState) error {
	var selection Selection
	var credits, remaining int
	if err := s.receiptAccess(ctx, tx, req.ReceiptID); err != nil {
		return err
	}
	if err := receiptDeliveryCurrent(ctx, tx, req.ReceiptID); err != nil {
		return err
	}
	var expires, now time.Time
	err := tx.QueryRow(ctx, `SELECT expires_at,credits,remaining_bytes,clock_timestamp() FROM cairn.index_session WHERE receipt_id=$1 FOR UPDATE`, req.ReceiptID).Scan(&expires, &credits, &remaining, &now)
	if err == pgx.ErrNoRows {
		return failure("STALE_HANDLE", "no expansion session for receipt")
	}
	if err != nil {
		return err
	}
	if !expires.After(now) {
		return failure("STALE_HANDLE", "index expired; retrieve a fresh index")
	}
	if err = receiptPayloadAvailable(ctx, tx, req.ReceiptID); err != nil {
		return err
	}
	var body []byte
	var original SemanticPackage
	var storedSeal string
	if err = tx.QueryRow(ctx, `SELECT semantic_body,seal FROM cairn.retrieval_receipt WHERE receipt_id=$1`, req.ReceiptID).Scan(&body, &storedSeal); err != nil {
		return err
	}
	if err = cbor.Unmarshal(body, &original); err != nil {
		return err
	}
	_, seal, err := sealPackage(original)
	if err != nil {
		return err
	}
	if seal != storedSeal {
		return failure("INTEGRITY_FAILURE", "indexed semantic bytes do not reproduce their seal")
	}
	if original.Mode != "index" || original.Destination != dest {
		return failure("AUTHORITY_DENIED", "expansion destination differs from indexed destination")
	}
	var id string
	var version int
	if err = tx.QueryRow(ctx, `SELECT record_id::text,version FROM cairn.index_handle WHERE receipt_id=$1 AND handle=$2`, req.ReceiptID, req.Handle).Scan(&id, &version); err == pgx.ErrNoRows {
		return failure("STALE_HANDLE", "handle is not part of this index")
	}
	if err != nil {
		return err
	}
	evaluations := map[string]*CandidateEvaluation{}
	_, candidates, err := s.collectCandidates(ctx, tx, CompileRequest{Context: original.Context, Scope: original.Scope, Purpose: original.Purpose, AvailableTokens: original.AvailableTokens}, dest, evaluations)
	if err != nil {
		return err
	}
	currentMandatory := []Selection{}
	found := false
	for _, c := range candidates {
		if c.selection.Mandatory {
			currentMandatory = append(currentMandatory, c.selection)
		}
		if c.selection.Record.RecordID == id && c.selection.Record.Version == version {
			selection = c.selection
			found = true
		}
	}
	if !found {
		return failure("STALE_HANDLE", "record version or eligibility changed; retrieve a fresh index")
	}
	// Reasons include query ranking, so compare only the semantic authority and
	// record facts when checking whether the mandatory bootstrap changed.
	if !sameSelectionFacts(original.Selected, currentMandatory) {
		return failure("STALE_HANDLE", "mandatory bootstrap changed; retrieve a fresh index")
	}
	indexed := false
	for _, e := range original.Index {
		if e.RecordID == id && e.Version == version {
			indexed = indexEntry(selection.Record).BodySHA256 == e.BodySHA256
		}
	}
	if !indexed {
		return failure("INTEGRITY_FAILURE", "expanded body differs from indexed version")
	}
	// The queryless recheck establishes eligibility, not the original ranking.
	selection.Reason = "indexed record; current eligibility revalidated"
	*state = expansionState{selection, credits, remaining}
	return nil
}

func (s *Store) Expand(ctx context.Context, req ExpandRequest, dest Destination) (Expansion, error) {
	if err := validID(req.Handle); err != nil {
		return Expansion{}, err
	}
	var state expansionState
	guard := func(tx pgx.Tx) error { return s.prepareExpansion(ctx, tx, req, dest, &state) }
	return privileged(ctx, s, "expand", req.RequestID, struct {
		Request     ExpandRequest
		Destination Destination
	}{req, dest}, func(tx pgx.Tx) (Expansion, error) {
		selection := state.selection
		credits, remaining := state.credits, state.remaining
		encoded, err := json.Marshal(selection)
		if err != nil {
			return Expansion{}, err
		}
		cost := len(encoded) + 256
		if credits <= 0 || cost > remaining {
			return Expansion{}, failure("BUDGET_REFUSED", "expansion credits or remaining context bytes exhausted")
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.index_session SET credits=credits-1,remaining_bytes=remaining_bytes-$2 WHERE receipt_id=$1`, req.ReceiptID, cost); err != nil {
			return Expansion{}, err
		}

		if _, err = tx.Exec(ctx, `INSERT INTO cairn.usage_observation(observation_id,receipt_id,record_id,version,signal,witness,method) VALUES($1,$2,$3,$4,'expanded','instrumented','authorized-body-pull/1')`, uuid.NewString(), req.ReceiptID, selection.Record.RecordID, selection.Record.Version); err != nil {
			return Expansion{}, err
		}
		return Expansion{selection, credits - 1, remaining - cost}, nil
	}, guard)
}
func sameSelectionFacts(a, b []Selection) bool {
	normalize := func(input []Selection) []Selection {
		out := append([]Selection{}, input...)
		for i := range out {
			out[i].Reason = ""
		}
		sortSelections(out)
		return out
	}
	aa, err := json.Marshal(normalize(a))
	if err != nil {
		return false
	}
	bb, err := json.Marshal(normalize(b))
	return err == nil && bytes.Equal(aa, bb)
}

func sortIndexEntries(entries []IndexEntry) {
	slices.SortFunc(entries, func(a, b IndexEntry) int { return strings.Compare(a.RecordID, b.RecordID) })
}
func sortSelections(entries []Selection) {
	slices.SortFunc(entries, func(a, b Selection) int { return strings.Compare(a.Record.RecordID, b.Record.RecordID) })
}

// InvalidateHandles is a restore-time fence. It must run before admitting agent
// traffic to a restored store; refreshing a retrieval creates a new session.
type InvalidateHandlesRequest struct {
	RequestID string `json:"request_id"`
	Reason    string `json:"reason"`
}
type InvalidatedHandles struct {
	Sessions int64 `json:"sessions"`
}

func (s *Store) InvalidateHandles(ctx context.Context, req InvalidateHandlesRequest) (InvalidatedHandles, error) {
	if !s.channel.Operator || s.channel.Repo != "" {
		return InvalidatedHandles{}, failure("AUTHORITY_DENIED", "restore fencing requires an unscoped operator channel")
	}
	if err := reasonValid(req.Reason); err != nil {
		return InvalidatedHandles{}, err
	}
	return mutate(ctx, s, "invalidate-handles", req.RequestID, req, func(tx pgx.Tx) (InvalidatedHandles, error) {
		tag, err := tx.Exec(ctx, `UPDATE cairn.index_session SET expires_at=clock_timestamp() WHERE expires_at>clock_timestamp()`)
		return InvalidatedHandles{tag.RowsAffected()}, err
	})
}
