package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"slices"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/fxamacker/cbor/v2"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type IndexEntry struct {
	Conflicts  []AdvisoryConflict `json:"conflicts,omitempty" cbor:"conflicts,omitempty"`
	Entities   []EntityRef        `json:"entities,omitempty" cbor:"entities,omitempty"`
	Category   string             `json:"category,omitempty" cbor:"category,omitempty"`
	RecordID   string             `json:"record_id"`
	Version    int                `json:"version"`
	Class      string             `json:"class"`
	Kind       string             `json:"kind"`
	Summary    string             `json:"summary"`
	BodySHA256 string             `json:"body_sha256"`
	// SummarySpan excludes the summary's synthetic omission markers.
	SummarySpan *ByteSpanRequest `json:"summary_span,omitempty" cbor:"summary_span,omitempty"`
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
	ExpansionReader  string        `json:"expansion_reader,omitempty"`
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
	return IndexEntry{Entities: r.Entities, RecordID: r.RecordID, Version: r.Version, Class: r.Class, Kind: r.Kind, Summary: summary, BodySHA256: hex.EncodeToString(sum[:])}
}
func packIndex(p SemanticPackage, candidates []candidate, evaluations map[string]*CandidateEvaluation, query string) (SemanticPackage, error) {
	if p.AdvisoryConflicts {
		candidates = allocationUnits(candidates)
	} else {
		sortCandidates(candidates)
	}
	instructions := newInstructionBudget(&p)
	p.Index = []IndexEntry{}
	p.Selected = []Selection{}
	optionalCost := 0
	seen := map[string]bool{}
	positions := map[string]int{}
	optionalPosition, pageStopped := 0, false
	page, offsetReason := p.Browse, "BROWSE_OFFSET"
	if p.Page != nil {
		page, offsetReason = p.Page, "PAGE_OFFSET"
	}
	if page != nil {
		page = &BrowsePage{Offset: page.Offset}
		if p.Page != nil {
			p.Page = page
		} else {
			p.Browse = page
		}
		p.Omitted[offsetReason] = 0
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
		members := unitMembers(c)
		omit := func(reason string) {
			for _, member := range members {
				evaluations[member.selection.Record.RecordID].Reason = reason
				p.Omitted[reason]++
			}
		}
		if page != nil {
			for _, member := range members {
				positions[member.selection.Record.RecordID] = optionalPosition
			}
			optionalPosition++
			if optionalPosition <= page.Offset {
				omit(offsetReason)
				continue
			}
			if pageStopped {
				omit("OPTIONAL_BUDGET")
				continue
			}
		}
		entries := make([]IndexEntry, 0, len(members))
		cost := 0
		for _, member := range members {
			entry := indexEntry(member.selection.Record)
			entry.Conflicts = member.selection.Conflicts
			if p.Schema == "cairn.semantic/8" || p.Schema == "cairn.semantic/9" || p.Schema == "cairn.semantic/10" || p.Schema == "cairn.semantic/11" || p.Schema == "cairn.semantic/12" || p.Schema == "cairn.semantic/13" || p.Schema == "cairn.semantic/14" {
				var span ByteSpanRequest
				entry.Summary, span = indexPreview(member.selection.Record.Body, query, p.Ranking)
				if member.selection.Record.Class != "C" && span.Length > 0 && len(entry.Conflicts) == 0 {
					entry.SummarySpan = &span
				}
			} else if p.Schema == "cairn.semantic/5" || p.Schema == "cairn.semantic/6" || p.Schema == "cairn.semantic/7" {
				entry.Summary = indexSummary(member.selection.Record.Body, query, p.Ranking)
			}
			entry.Category = member.selection.Category
			encoded, err := json.Marshal(entry)
			if err != nil {
				return p, err
			}
			// Each position gets its own opaque handle outside the semantic seal.
			e := evaluations[entry.RecordID]
			e.Rank, e.Cost = rank+1, len(encoded)+160
			cost += e.Cost
			entries = append(entries, entry)
		}
		// Equal text does not erase the independently recorded dissent.
		if len(c.group) == 0 && seen[entries[0].BodySHA256] {
			omit("REDUNDANT")
			continue
		}
		if len(p.Index)+len(entries) > 100 || optionalCost+cost > p.OptionalLimit {
			omit("OPTIONAL_BUDGET")
			if page != nil && cost <= p.OptionalLimit && len(entries) <= 100 {
				position := positions[entries[0].RecordID]
				page.NextOffset = &position
				pageStopped = true
			}
			continue
		}
		// Conflict groups contain only A/B positions; instruction policy still
		// applies to standalone optional C records.
		if admitted, err := instructions.admit(c.selection, e, p.Omitted); err != nil {
			return p, err
		} else if !admitted {
			continue
		}
		p.Index = append(p.Index, entries...)
		optionalCost += cost
		for _, entry := range entries {
			seen[entry.BodySHA256] = true
			evaluations[entry.RecordID].Reason = "INDEXED"
		}
	}
	p = withEntitySchema(p)
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
		if page != nil {
			position := positions[e.RecordID]
			page.NextOffset = &position
		}
		key := conflictKey(e.Conflicts)
		for {
			last := p.Index[len(p.Index)-1]
			evaluations[last.RecordID].Reason = "TOTAL_BUDGET"
			p.Index = p.Index[:len(p.Index)-1]
			p.Omitted["TOTAL_BUDGET"]++
			if key == "" || len(p.Index) == 0 || conflictKey(p.Index[len(p.Index)-1].Conflicts) != key {
				break
			}
		}
	}
	if page != nil && page.NextOffset != nil && len(p.Index) == 0 {
		return p, failure("BUDGET_REFUSED", "index page cannot fit a preview; increase available input room")
	}
	return p, nil
}
func createIndexSession(ctx context.Context, tx pgx.Tx, id string, p SemanticPackage, reader string) error {
	rendered, err := (Package{Semantic: p}).Render()
	if err != nil {
		return err
	}
	budget := min(24000, max(0, p.AvailableTokens-len(rendered)-160*len(p.Index)-512))
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.index_session(receipt_id,remaining_bytes,expansion_reader) VALUES($1,$2,NULLIF($3,''))`, id, budget, reader); err != nil {
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
	result := IndexResult{}
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
	result, err = readIndex(ctx, tx, p)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}

func readIndex(ctx context.Context, tx pgx.Tx, p Package) (IndexResult, error) {
	result := IndexResult{Package: p, Handles: []IndexHandle{}}
	if err := tx.QueryRow(ctx, `SELECT expires_at,credits,remaining_bytes,COALESCE(expansion_reader,'') FROM cairn.index_session WHERE receipt_id=$1`, p.ReceiptID).Scan(&result.ExpiresAt, &result.CreditsRemaining, &result.BytesRemaining, &result.ExpansionReader); err != nil {
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
	return result, nil
}

type ExpandRequest struct {
	RequestID string           `json:"request_id"`
	ReceiptID string           `json:"receipt_id"`
	Handle    string           `json:"handle"`
	Span      *ByteSpanRequest `json:"span,omitempty"`
}

// NoteSpan is partial source text; SourceSHA256 identifies the full indexed body.
type NoteSpan struct {
	ByteSpan
	SourceSHA256 string `json:"source_sha256"`
}

type Expansion struct {
	Competing        []Selection `json:"competing,omitempty"`
	Selection        Selection   `json:"selection"`
	CreditsRemaining int         `json:"credits_remaining"`
	BytesRemaining   int         `json:"bytes_remaining"`
	Span             *NoteSpan   `json:"span,omitempty"`
}

type expansionState struct {
	competing          []Selection
	selection          Selection
	credits, remaining int
}

// Both body and evidence pulls recheck this state before idempotency lookup.
func (s *Store) prepareExpansion(ctx context.Context, tx pgx.Tx, req ExpandRequest, dest Destination, state *expansionState) error {
	var selection Selection
	var credits, remaining int
	if err := s.expansionAccess(ctx, tx, req.ReceiptID); err != nil {
		return err
	}
	if err := receiptDeliveryCurrent(ctx, tx, req.ReceiptID); err != nil {
		return err
	}
	var expires, now time.Time
	err := tx.QueryRow(ctx, `SELECT expires_at,credits,remaining_bytes,clock_timestamp() FROM cairn.index_session WHERE receipt_id=$1 FOR UPDATE`, req.ReceiptID).Scan(&expires, &credits, &remaining, &now)
	if errors.Is(err, pgx.ErrNoRows) {
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
	if err = tx.QueryRow(ctx, `SELECT record_id::text,version FROM cairn.index_handle WHERE receipt_id=$1 AND handle=$2`, req.ReceiptID, req.Handle).Scan(&id, &version); errors.Is(err, pgx.ErrNoRows) {
		return failure("STALE_HANDLE", "handle is not part of this index")
	}
	if err != nil {
		return err
	}
	evaluations := map[string]*CandidateEvaluation{}
	_, candidates, err := s.collectCandidates(ctx, tx, CompileRequest{AdvisoryConflicts: original.AdvisoryConflicts, Context: original.Context, Scope: original.Scope, Purpose: original.Purpose, AvailableTokens: original.AvailableTokens}, dest, evaluations)
	if err != nil {
		return err
	}
	currentMandatory := []Selection{}
	current := map[string]Selection{}
	found := false
	for _, c := range candidates {
		current[c.selection.Record.RecordID] = c.selection
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
	indexed := map[string]IndexEntry{}
	for _, entry := range original.Index {
		indexed[entry.RecordID] = entry
	}
	check := func(position Selection) error {
		entry, ok := indexed[position.Record.RecordID]
		if !ok || position.Record.Version != entry.Version || indexEntry(position.Record).BodySHA256 != entry.BodySHA256 {
			return failure("INTEGRITY_FAILURE", "expanded body differs from indexed version")
		}
		if !sameAdvisoryConflicts(position.Conflicts, entry.Conflicts) {
			return failure("STALE_HANDLE", "competing positions changed; retrieve a fresh index")
		}
		return nil
	}
	if err = check(selection); err != nil {
		return err
	}
	companions := []Selection{}
	seen := map[string]bool{id: true}
	for _, group := range selection.Conflicts {
		for _, ref := range group.Members {
			if seen[ref.RecordID] {
				continue
			}
			seen[ref.RecordID] = true
			other, ok := current[ref.RecordID]
			if !ok || other.Record.Version != ref.Version || !sameAdvisoryConflicts(selection.Conflicts, other.Conflicts) {
				return failure("STALE_HANDLE", "competing positions changed; retrieve a fresh index")
			}
			if err = check(other); err != nil {
				return err
			}
			other.Reason = "competing indexed position; current eligibility revalidated"
			companions = append(companions, other)
		}
	}
	sortSelections(companions)
	// The queryless recheck establishes eligibility, not the original ranking.
	selection.Reason = "indexed record; current eligibility revalidated"
	*state = expansionState{selection: selection, competing: companions, credits: credits, remaining: remaining}
	return nil
}

// A designated ordinary reader can expand this index, but receipt ownership
// and all observation, inspection and execution operations remain unchanged.
func (s *Store) expansionAccess(ctx context.Context, tx pgx.Tx, id string) error {
	if err := validID(id); err != nil {
		return err
	}
	var owner, repo, reader string
	err := tx.QueryRow(ctx, `SELECT r.caller,r.scope->>'repo',COALESCE(i.expansion_reader,'')
		FROM cairn.retrieval_receipt r LEFT JOIN cairn.index_session i USING(receipt_id)
		WHERE r.receipt_id=$1`, id).Scan(&owner, &repo, &reader)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", "receipt not found")
	}
	if err != nil {
		return err
	}
	if owner != s.channel.Principal && (reader != s.channel.Principal || s.channel.Instrumented || s.channel.Operator) {
		return failure("AUTHORITY_DENIED", "receipt does not authorize this expansion reader")
	}
	return s.checkRepo(repo)
}

func (s *Store) Expand(ctx context.Context, req ExpandRequest, dest Destination) (Expansion, error) {
	if req.Span != nil && (req.Span.Offset < 0 || req.Span.Offset >= 65536 || req.Span.Length <= 0 || req.Span.Length > 65536) {
		return Expansion{}, failure("INVALID_REQUEST", "note span requires byte offset 0-65535 and length 1-65536")
	}
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
		method := "authorized-body-pull/1"
		var span *NoteSpan
		var payload any = selection
		if len(state.competing) > 0 {
			payload = struct {
				Selection Selection   `json:"selection"`
				Competing []Selection `json:"competing"`
			}{selection, state.competing}
			method = "authorized-advisory-group-pull/1"
		}
		if req.Span != nil {
			if selection.Record.Class == "C" || len(selection.Conflicts) > 0 {
				return Expansion{}, failure("INVALID_REQUEST", "instructions and competing positions require whole-body delivery; omit span")
			}
			body := []byte(selection.Record.Body)
			if req.Span.Offset >= len(body) {
				return Expansion{}, failure("INVALID_REQUEST", "span offset is outside the indexed note")
			}
			digest := sha256.Sum256(body)
			span = &NoteSpan{ByteSpan: selectByteSpan(body, *req.Span), SourceSHA256: hex.EncodeToString(digest[:])}
			selection.Record.Body = ""
			payload = struct {
				Selection Selection `json:"selection"`
				Span      *NoteSpan `json:"span"`
			}{selection, span}
			method = "authorized-body-span-pull/1"
		}
		encoded, err := json.Marshal(payload)
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

		for _, position := range append([]Selection{selection}, state.competing...) {
			if _, err = tx.Exec(ctx, `INSERT INTO cairn.usage_observation(observation_id,receipt_id,record_id,version,signal,witness,method) VALUES($1,$2,$3,$4,'expanded','instrumented',$5)`, uuid.NewString(), req.ReceiptID, position.Record.RecordID, position.Record.Version, method); err != nil {
				return Expansion{}, err
			}
		}
		return Expansion{Selection: selection, Competing: state.competing, CreditsRemaining: credits - 1, BytesRemaining: remaining - cost, Span: span}, nil
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

// Missing sessions are body packages. Every index receipt has a session created
// in its compilation transaction; expiry also prevents a new launch claim.
func indexSessionCurrent(ctx context.Context, tx pgx.Tx, id string) error {
	var expired bool
	err := tx.QueryRow(ctx, `SELECT expires_at<=clock_timestamp() FROM cairn.index_session WHERE receipt_id=$1`, id).Scan(&expired)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}
	if expired {
		return failure("STALE_HANDLE", "index expired; retrieve a fresh index")
	}
	return nil
}
