package core

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"

	"github.com/fxamacker/cbor/v2"
	"github.com/google/uuid"
	"github.com/zeebo/blake3"
)

func withPullReserve(t *testing.T, req CompileRequest, reserve int) CompileRequest {
	t.Helper()
	raw, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	var input map[string]any
	if err = json.Unmarshal(raw, &input); err != nil {
		t.Fatal(err)
	}
	input["min_pull_bytes"] = reserve
	raw, err = json.Marshal(input)
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, &req); err != nil {
		t.Fatal(err)
	}
	return req
}

func TestPullReservePreservesRoomForCheckedSource(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	bodies := map[string]string{}
	for i := 0; i < 8; i++ {
		draft := projectNote(repo)
		draft.Body = fmt.Sprintf("Allocation guidance %d. Inspect the current migration source before changing production configuration. ", i) + strings.Repeat("日本語 \"quoted\" \\path\n", 20)
		record, err := op.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
		if err != nil {
			t.Fatal(err)
		}
		bodies[record.RecordID] = record.Body
	}
	budget, reserve := 6500, 3500
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "allocation guidance", Purpose: "context", AvailableTokens: 32000, MemoryBudgetBytes: &budget}
	baseline, err := op.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	req = withPullReserve(t, req, reserve)
	result, err := op.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if result.BytesRemaining < reserve || baseline.BytesRemaining >= reserve || len(result.Handles) == 0 || len(result.Handles) >= len(baseline.Handles) {
		t.Fatalf("reserve must exchange previews for real pull room: baseline entries=%d remaining=%d; reserved entries=%d remaining=%d need=%d", len(baseline.Handles), baseline.BytesRemaining, len(result.Handles), result.BytesRemaining, reserve)
	}
	cost, err := indexMemoryCost(result.Package.Semantic)
	if err != nil || result.BytesRemaining != min(24000, budget-cost) {
		t.Fatalf("reserve was added or double-subtracted: cost=%d remaining=%d %v", cost, result.BytesRemaining, err)
	}
	wire, err := json.Marshal(result)
	if err != nil || len(wire)+reserve > budget {
		t.Fatalf("serialized core index consumed reserve: %d + %d > %d: %v", len(wire), reserve, budget, err)
	}
	if result.Package.Semantic.OptionalLimit != baseline.Package.Semantic.OptionalLimit {
		t.Fatal("reserve changed owner optional policy")
	}
	pulled, err := op.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: result.Package.ReceiptID, Handle: result.Handles[0].Handle}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if pulled.Selection.Record.Body != bodies[result.Handles[0].RecordID] || pulled.BytesRemaining >= result.BytesRemaining {
		t.Fatal("checked source body or accounting changed")
	}
	pullWire, err := json.Marshal(pulled)
	if err != nil || len(wire)+len(pullWire) > budget {
		t.Fatalf("actual index+pull serialization exceeds total: %d+%d > %d %v", len(wire), len(pullWire), budget, err)
	}
	if pulled.BytesRemaining >= reserve {
		t.Fatal("reserve must be spendable, not a permanent floor")
	}
	t.Logf("baseline previews=%d remaining=%d; reserve previews=%d remaining=%d; checked pull remaining=%d", len(baseline.Handles), baseline.BytesRemaining, len(result.Handles), result.BytesRemaining, pulled.BytesRemaining)
}

func TestPullReserveValidationBeforeStoreAccess(t *testing.T) {
	for _, tc := range []struct {
		name          string
		budget        *int
		reserve       int
		mode, purpose string
	}{
		{"no explicit budget", nil, 1, "index", "context"},
		{"zero", ptrInt(6000), 0, "index", "context"},
		{"negative", ptrInt(6000), -1, "index", "context"},
		{"above budget", ptrInt(6000), 6001, "index", "context"},
		{"above expansion ceiling", ptrInt(32000), 24001, "index", "context"},
		{"body compile", ptrInt(6000), 1000, "", "context"},
		{"other purpose", ptrInt(6000), 1000, "index", "planning"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{"repo", "task", "run"}, Purpose: tc.purpose, Mode: tc.mode, AvailableTokens: 32000, MemoryBudgetBytes: tc.budget}
			req = withPullReserve(t, req, tc.reserve)
			_, err := (&Store{}).Compile(context.Background(), req, Destination{"local", true})
			requireCode(t, err, "INVALID_REQUEST")
		})
	}
}

func ptrInt(v int) *int { return &v }

func TestPullReserveReceiptRetryReplayAndPayloadCost(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Body = "Reserve source. " + strings.Repeat("日本語 \"quoted\" \\path\n", 1200)
	record, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "reserve source", Purpose: "context", AvailableTokens: 32000, MemoryBudgetBytes: ptrInt(6500), MinPullBytes: ptrInt(3500)}
	index, err := op.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(index.Handles) != 1 || index.BytesRemaining < 3500 {
		t.Fatalf("missing reserved handle: %+v", index)
	}
	other := testStore(t, Channel{Principal: "agent:reserve-foreign"})
	_, err = other.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: index.Handles[0].Handle}, Destination{"local", true})
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = op.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: index.Handles[0].Handle}, Destination{"hosted", false})
	requireCode(t, err, "AUTHORITY_DENIED")
	retry, err := op.Index(ctx, req, Destination{"local", true})
	if err != nil || retry.Package.ReceiptID != index.Package.ReceiptID {
		t.Fatalf("identical retry: %v", err)
	}
	for _, value := range []*int{nil, ptrInt(3499)} {
		changed := req
		changed.MinPullBytes = value
		_, err = op.Index(ctx, changed, Destination{"local", true})
		requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	}
	pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: index.Handles[0].Handle, Span: &ByteSpanRequest{Length: index.BytesRemaining}}
	// Enough source bytes exist, but R/remaining count charged payload, not raw
	// bytes. Escaping, provenance and framing must still fit the same allowance.
	_, err = op.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "BUDGET_REFUSED")
	unchanged, err := op.Index(ctx, req, Destination{"local", true})
	if err != nil || unchanged.BytesRemaining != index.BytesRemaining || unchanged.CreditsRemaining != index.CreditsRemaining {
		t.Fatalf("refusal spent credit/room: %v", err)
	}
	pull.RequestID = uuid.NewString()
	pull.Span = &ByteSpanRequest{Offset: 0, Length: 80}
	got, err := op.Expand(ctx, pull, Destination{"local", true})
	if err != nil || got.Span == nil || got.Span.Body != record.Body[:80] {
		t.Fatalf("checked span: %+v %v", got, err)
	}
	replayed, err := op.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
	if err != nil || replayed.Seal != index.Package.Seal {
		t.Fatalf("reserve replay: %v", err)
	}
	changed := record.Draft
	changed.Body = "new current correction"
	if _, err = op.Edit(ctx, EditRequest{uuid.NewString(), record.RecordID, record.Version, changed}); err != nil {
		t.Fatal(err)
	}
	pull.RequestID = uuid.NewString()
	_, err = op.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "STALE_HANDLE")
	replayed, err = op.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
	if err != nil || replayed.Seal != index.Package.Seal {
		t.Fatalf("historical reserve after edit: %v", err)
	}
}

func TestPullReserveKeepsMandatoryWholeAndOwnerPolicy(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Body = "reserve optional guidance"
	if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
		t.Fatal(err)
	}
	mandatory, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: "instruction", Body: strings.Repeat("Keep complete required policy. ", 50), Scope: Scope{repo, "*", "*"}, ClaimType: "self", Sensitivity: "shareable"}, GrantID: root.ID, Mandatory: true, PolicyKey: "reserve-test", Reason: "Whole selected context must coexist with the pull reserve"})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "reserve", Purpose: "context", AvailableTokens: 32000, MemoryBudgetBytes: ptrInt(6500), MinPullBytes: ptrInt(5000)}
	_, err = op.Index(ctx, req, Destination{"local", true})
	requireCode(t, err, "BUDGET_REFUSED")
	var receipts int
	if err = op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.retrieval_receipt WHERE request_id=$1`, req.RequestID).Scan(&receipts); err != nil || receipts != 0 {
		t.Fatalf("impossible reserve committed receipt: %d %v", receipts, err)
	}
	req.RequestID = uuid.NewString()
	req.MinPullBytes = ptrInt(2000)
	index, err := op.Index(ctx, req, Destination{"local", true})
	if err != nil || len(index.Package.Semantic.Selected) != 1 || index.Package.Semantic.Selected[0].Record.Body != mandatory.Body || index.BytesRemaining < 2000 {
		t.Fatalf("whole required delivery: %v", err)
	}
	_, err = op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: &PolicyRules{}, Reason: "No optional guidance"})
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	index, err = op.Index(ctx, req, Destination{"local", true})
	if err != nil || index.Package.Semantic.OptionalLimit != 0 || len(index.Handles) != 0 || len(index.Package.Semantic.Selected) != 1 || index.BytesRemaining < 2000 {
		t.Fatalf("owner policy bypass: %v", err)
	}
}

func TestPullReserveDropsWholeCompetingGroup(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	ids := []string{}
	for i := 0; i < 2; i++ {
		draft := projectNote(repo)
		draft.Sensitivity = "shareable"
		draft.Body = fmt.Sprintf("reserve connection policy position %d", i)
		r, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, r.RecordID)
	}
	if _, err := op.Dispute(ctx, DisputeRequest{uuid.NewString(), ids, "competing procedures"}); err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "reserve connection", Purpose: "context", AvailableTokens: 64000, MemoryBudgetBytes: ptrInt(8000), MinPullBytes: ptrInt(2000), AdvisoryConflicts: true}
	full, err := op.Index(ctx, req, Destination{"hosted", false})
	if err != nil || len(full.Handles) != 2 {
		t.Fatalf("whole group fixture: %v", err)
	}
	one := full.Package.Semantic
	one.Index = one.Index[:1]
	cost, err := indexMemoryCost(one)
	if err != nil {
		t.Fatal(err)
	}
	// Leave enough delivery room for one member but not both; packing must omit
	// the entire conflict group rather than returning a misleading single side.
	req.RequestID = uuid.NewString()
	req.MinPullBytes = ptrInt(8000 - cost)
	none, err := op.Index(ctx, req, Destination{"hosted", false})
	if err != nil || len(none.Handles) != 0 || none.BytesRemaining < *req.MinPullBytes {
		t.Fatalf("split group or lost reserve: %+v %v", none, err)
	}
}

func TestPullReserveLegacyBytesAndOldReaderRefusal(t *testing.T) {
	// Reproduce the exact old nested budget type, retaining every unrelated field
	// and tag. Omitted reserve must encode identically; new v2 cannot lose R and
	// still pass the canonical seal verification performed by old readers.
	fields := []reflect.StructField{}
	typ := reflect.TypeOf(MemoryBudget{})
	for i := 0; i < typ.NumField(); i++ {
		f := typ.Field(i)
		if f.Name != "MinPullBytes" {
			fields = append(fields, f)
		}
	}
	oldBudget := reflect.StructOf(fields)
	fields = nil
	typ = reflect.TypeOf(SemanticPackage{})
	for i := 0; i < typ.NumField(); i++ {
		f := typ.Field(i)
		if f.Name == "MemoryBudget" {
			f.Type = reflect.PointerTo(oldBudget)
		}
		fields = append(fields, f)
	}
	oldPackage := reflect.StructOf(fields)
	options := cbor.CanonicalEncOptions()
	options.Time = cbor.TimeRFC3339Nano
	encoder, err := options.EncMode()
	if err != nil {
		t.Fatal(err)
	}
	for _, reserve := range []int{0, 3500} {
		p := SemanticPackage{Schema: "cairn.semantic/17", Mode: "index", Purpose: "context", AvailableTokens: 32000, MemoryBudget: &MemoryBudget{Schema: "cairn.memory-budget/1", Bytes: 6500}}
		if reserve > 0 {
			p.MemoryBudget.Schema = "cairn.memory-budget/2"
			p.MemoryBudget.MinPullBytes = reserve
		}
		encoded, seal, err := sealPackage(p)
		if err != nil {
			t.Fatal(err)
		}
		old := reflect.New(oldPackage)
		if err = cbor.Unmarshal(encoded, old.Interface()); err != nil {
			t.Fatal(err)
		}
		restored, err := encoder.Marshal(old.Elem().Interface())
		if err != nil {
			t.Fatal(err)
		}
		sum := blake3.Sum256(restored)
		oldSeal := "blake3:" + hex.EncodeToString(sum[:])
		if (seal == oldSeal) != (reserve == 0) || bytes.Equal(encoded, restored) != (reserve == 0) {
			t.Fatalf("old reader bytes/seal: reserve=%d", reserve)
		}
	}
	fields = nil
	typ = reflect.TypeOf(CompileRequest{})
	for i := 0; i < typ.NumField(); i++ {
		f := typ.Field(i)
		if f.Name != "MinPullBytes" {
			fields = append(fields, f)
		}
	}
	oldRequest := reflect.New(reflect.StructOf(fields))
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{"repo", "task", "run"}, Mode: "index", Purpose: "context", AvailableTokens: 32000, MemoryBudgetBytes: ptrInt(6500)}
	current, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(current, oldRequest.Interface()); err != nil {
		t.Fatal(err)
	}
	old, err := json.Marshal(oldRequest.Elem().Interface())
	if err != nil || !bytes.Equal(current, old) {
		t.Fatal("absent reserve changed request identity bytes")
	}
}

func TestPullReserveRejectsResealedInvalidContract(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "reserve", Purpose: "context", AvailableTokens: 32000, MemoryBudgetBytes: ptrInt(6500), MinPullBytes: ptrInt(3500)}
	index, err := op.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	for _, bad := range []MemoryBudget{
		{Schema: "cairn.memory-budget/1", Bytes: 6500, MinPullBytes: 1},
		{Schema: "cairn.memory-budget/2", Bytes: 6500},
		{Schema: "cairn.memory-budget/2", Bytes: 6500, MinPullBytes: -1},
		{Schema: "cairn.memory-budget/2", Bytes: 6500, MinPullBytes: 6501},
		{Schema: "cairn.memory-budget/2", Bytes: 32000, MinPullBytes: 24001},
	} {
		altered := index.Package.Semantic
		altered.MemoryBudget = &bad
		encoded, seal, err := sealPackage(altered)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = op.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET semantic_body=$2,seal=$3 WHERE receipt_id=$1`, index.Package.ReceiptID, encoded, seal); err != nil {
			t.Fatal(err)
		}
		_, err = op.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
		requireCode(t, err, "INTEGRITY_FAILURE")
		_, err = op.Replay(ctx, index.Package.ReceiptID)
		requireCode(t, err, "INTEGRITY_FAILURE")
	}
}

func TestPullReservePreventsBoundaryExpansionSpendingReservedRoom(t *testing.T) {
	body := strings.Repeat("Background context. ", 12) + "Do not under any circumstances in the production environment reuse cached validation results. More details follow."
	record := Record{RecordID: "qualifier", Version: 1, Class: "A", Draft: Draft{Kind: "note", Body: body}}
	entry := indexEntry(record)
	summary, span := indexPreview(body, "reuse cached validation results", "binary-idf-scope-recency/2")
	entry.Summary, entry.SummarySpan = summary, &span
	p := SemanticPackage{Presentation: previewBoundariesV1, Mode: "index", Purpose: "context", Schema: "cairn.semantic/17", Ranking: "binary-idf-scope-recency/2", AvailableTokens: 32000, OptionalLimit: 3200, Omitted: omissionCensus(), Index: []IndexEntry{entry}, MemoryBudget: &MemoryBudget{Schema: "cairn.memory-budget/2", Bytes: 6500, MinPullBytes: 3500}}
	cost, err := indexMemoryCost(p)
	if err != nil {
		t.Fatal(err)
	}
	p.MemoryBudget.MinPullBytes = 6500 - cost
	// Both values retain four decimal digits, so the only changed cost below is
	// the candidate's sentence-boundary enrichment, not the metadata width.
	exact, err := indexMemoryCost(p)
	if err != nil || exact != cost {
		t.Fatalf("fixture cost width changed: %d/%d %v", cost, exact, err)
	}
	candidates := []candidate{{selection: Selection{Record: record}}}
	got, err := expandPreviewBoundaries(p, candidates, map[string]*CandidateEvaluation{"qualifier": {}})
	if err != nil || got.Index[0].Summary != summary {
		t.Fatalf("expansion spent reserved bytes: %+v %v", got.Index, err)
	}
	p.Index = []IndexEntry{entry}
	p.MemoryBudget.MinPullBytes = 1
	got, err = expandPreviewBoundaries(p, candidates, map[string]*CandidateEvaluation{"qualifier": {}})
	if err != nil || !strings.Contains(got.Index[0].Summary, "Do not") {
		t.Fatalf("fixture must expand with unreserved room: %+v %v", got.Index, err)
	}
}

func TestPullReservePagedSemanticAndLexicalPacking(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	for i := 0; i < 6; i++ {
		draft := projectNote(repo)
		draft.Sensitivity = "shareable"
		draft.Body = fmt.Sprintf("Reserve source %d. Keep the full migration condition attached to the procedure.", i)
		if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
			t.Fatal(err)
		}
	}
	private := projectNote(repo)
	private.Body = "PRIVATE must never enter the hosted semantic pool"
	if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), private}); err != nil {
		t.Fatal(err)
	}
	op.semanticRetriever = func(_ context.Context, req SemanticRankRequest) (SemanticRetrievalResult, error) {
		if len(req.Notes) != 6 {
			t.Fatalf("wrong hosted candidate set: %d", len(req.Notes))
		}
		result := SemanticRetrievalResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "reserve-fixture/1", Indexed: len(req.Notes)}
		for i, n := range req.Notes {
			if strings.Contains(n.Body, "PRIVATE") {
				t.Fatal("private body reached semantic retriever")
			}
			result.Hits = append(result.Hits, SemanticPassageHit{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, 900000 - i}, Span: ByteSpanRequest{Length: len(n.Body)}})
		}
		return result, nil
	}
	for _, semantic := range []bool{false, true} {
		req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "reserve source", Purpose: "context", AvailableTokens: 32000, MemoryBudgetBytes: ptrInt(6500), MinPullBytes: ptrInt(3500), PageOffset: ptrInt(0), Semantic: semantic}
		seen := map[string]bool{}
		for page := 0; page < 2; page++ {
			result, err := op.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			if result.BytesRemaining < 3500 || len(result.Handles) == 0 || result.Package.Semantic.OptionalLimit != 3200 {
				t.Fatalf("paged reserve/policy failed semantic=%v: %+v", semantic, result)
			}
			if semantic && (result.Package.Semantic.Discovery == nil || result.Package.Semantic.Discovery.State != "ready") {
				t.Fatal("semantic fixture not used")
			}
			for _, h := range result.Handles {
				if seen[h.RecordID] {
					t.Fatal("page repeated previously admitted candidate")
				}
				seen[h.RecordID] = true
			}
			replay, err := op.Recompile(ctx, RecompileRequest{ReceiptID: result.Package.ReceiptID, Query: req.Query})
			if err != nil || replay.Seal != result.Package.Seal {
				t.Fatalf("paged reserve replay: %v", err)
			}
			if result.Package.Semantic.Page.NextOffset == nil {
				if len(seen) != 6 {
					t.Fatal("page ended before eligible notes")
				}
				break
			}
			req.RequestID = uuid.NewString()
			req.PageOffset = result.Package.Semantic.Page.NextOffset
		}
	}
}
