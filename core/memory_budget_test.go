package core

import (
	"context"
	"encoding/hex"
	"fmt"
	"reflect"
	"strings"
	"testing"

	"github.com/fxamacker/cbor/v2"
	"github.com/google/uuid"
	"github.com/zeebo/blake3"
)

func TestIndexMemoryBudgetSeparatesContextPolicyFromReceiptRoom(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	for i := 0; i < 5; i++ {
		draft := projectNote(repo)
		draft.Body = fmt.Sprintf("Allocation needle procedure %d. Read the current source before applying this saved guidance.", i)
		if _, err := op.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft}); err != nil {
			t.Fatal(err)
		}
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "allocation needle", Purpose: "context", AvailableTokens: 6000}
	baseline, err := op.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	room := 6000
	req.RequestID, req.AvailableTokens, req.MemoryBudgetBytes = uuid.NewString(), 24000, &room
	index, err := op.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(baseline.Package.Semantic.Index) != 1 || len(index.Package.Semantic.Index) <= 1 {
		t.Fatalf("same memory room did not permit comparison: baseline=%d allocated=%d", len(baseline.Package.Semantic.Index), len(index.Package.Semantic.Index))
	}
	if index.Package.Semantic.OptionalLimit != 2400 || index.BytesRemaining <= 0 || index.BytesRemaining >= room {
		t.Fatalf("context policy or receipt room changed: %+v", index)
	}
	pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: index.Handles[0].Handle, Span: &ByteSpanRequest{Offset: 0, Length: 40}}
	got, err := op.Expand(ctx, pull, Destination{"local", true})
	if err != nil || got.BytesRemaining >= index.BytesRemaining {
		t.Fatalf("bounded current pull: %+v %v", got, err)
	}
	replayed, err := op.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
	if err != nil || replayed.Seal != index.Package.Seal {
		t.Fatalf("allocated receipt did not recompile: %v", err)
	}
	if _, err = op.Index(ctx, req, Destination{"local", true}); err != nil {
		t.Fatalf("identical retry: %v", err)
	}
	changed := 5900
	req.MemoryBudgetBytes = &changed
	_, err = op.Index(ctx, req, Destination{"local", true})
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	req.MemoryBudgetBytes = nil
	_, err = op.Index(ctx, req, Destination{"local", true})
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	// Even an explicit cap numerically equal to the legacy context room is
	// a different sealed accounting contract, not an interchangeable retry.
	equal := req.AvailableTokens
	req.RequestID = uuid.NewString()
	if _, err = op.Index(ctx, req, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	req.MemoryBudgetBytes = &equal
	_, err = op.Index(ctx, req, Destination{"local", true})
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	// Recompilation uses frozen facts and the original smaller memory cap,
	// including after newer source text would change fresh packing.
	current, err := op.Get(ctx, index.Handles[0].RecordID)
	if err != nil {
		t.Fatal(err)
	}
	draft := current.Draft
	draft.Body = "A newer unrelated record body."
	if _, err = op.Edit(ctx, EditRequest{uuid.NewString(), current.RecordID, current.Version, draft}); err != nil {
		t.Fatal(err)
	}
	replayed, err = op.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
	if err != nil || replayed.Seal != index.Package.Seal {
		t.Fatalf("source changes affected frozen memory allocation: %v", err)
	}
	pull.RequestID = uuid.NewString()
	_, err = op.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "STALE_HANDLE")
}

func TestMemoryBudgetRejectsInvalidRequestsBeforeStorage(t *testing.T) {
	for _, tc := range []struct {
		mode, purpose string
		cap           int
	}{
		{"index", "context", 0}, {"index", "context", -1}, {"index", "context", 255},
		{"index", "context", 24001}, {"", "context", 6000}, {"index", "planning", 6000},
	} {
		t.Run(fmt.Sprintf("%s-%s-%d", tc.mode, tc.purpose, tc.cap), func(t *testing.T) {
			store := &Store{}
			_, err := store.Compile(context.Background(), CompileRequest{RequestID: uuid.NewString(), Scope: Scope{"repo", "task", "run"}, Query: "allocation", Purpose: tc.purpose, Mode: tc.mode, AvailableTokens: 24000, MemoryBudgetBytes: &tc.cap}, Destination{"local", true})
			requireCode(t, err, "INVALID_REQUEST")
		})
	}
}

func TestMemoryBudgetExtensionCannotBeSilentlyReadByLegacyDecoder(t *testing.T) {
	// The exact pre-extension field set uses the same tags and field types.
	// Old cbor.Unmarshal ignores the new field, but its subsequent canonical
	// seal check must fail. A legacy package still reproduces the same bytes.
	typeOf := reflect.TypeOf(SemanticPackage{})
	fields := []reflect.StructField{}
	for i := 0; i < typeOf.NumField(); i++ {
		field := typeOf.Field(i)
		if field.Name != "MemoryBudget" {
			fields = append(fields, field)
		}
	}
	legacyType := reflect.StructOf(fields)
	options := cbor.CanonicalEncOptions()
	options.Time = cbor.TimeRFC3339Nano
	encoder, err := options.EncMode()
	if err != nil {
		t.Fatal(err)
	}
	for _, explicit := range []bool{false, true} {
		p := SemanticPackage{Schema: "cairn.semantic/17", Mode: "index", Purpose: "context", AvailableTokens: 24000}
		if explicit {
			p.MemoryBudget = &MemoryBudget{Schema: "cairn.memory-budget/1", Bytes: 6000}
		}
		encoded, seal, err := sealPackage(p)
		if err != nil {
			t.Fatal(err)
		}
		legacy := reflect.New(legacyType)
		if err = cbor.Unmarshal(encoded, legacy.Interface()); err != nil {
			t.Fatal(err)
		}
		reencoded, err := encoder.Marshal(legacy.Elem().Interface())
		if err != nil {
			t.Fatal(err)
		}
		sum := blake3.Sum256(reencoded)
		legacySeal := "blake3:" + hex.EncodeToString(sum[:])
		if (legacySeal == seal) == explicit {
			t.Fatalf("legacy seal compatibility changed: explicit=%v old=%s sealed=%s", explicit, legacySeal, seal)
		}
	}
}

func TestMemoryBudgetKeepsWholeMandatoryAndOperatorPolicy(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Body = "allocation needle optional advice"
	if _, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
		t.Fatal(err)
	}
	mandatory, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: "instruction", Body: strings.Repeat("Keep required policy whole. ", 140), Scope: Scope{repo, "*", "*"}, ClaimType: "self", Sensitivity: "shareable"}, GrantID: root.ID, Mandatory: true, PolicyKey: "memory-budget", Reason: "Verify whole instruction delivery with an allocated memory limit"})
	if err != nil {
		t.Fatal(err)
	}
	room := 3000
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "allocation needle", Purpose: "context", AvailableTokens: 24000, MemoryBudgetBytes: &room}
	_, err = op.Index(ctx, req, Destination{"local", true})
	requireCode(t, err, "BUDGET_REFUSED")
	room, req.RequestID = 12000, uuid.NewString()
	index, err := op.Index(ctx, req, Destination{"local", true})
	if err != nil || len(index.Package.Semantic.Selected) != 1 || index.Package.Semantic.Selected[0].Record.Body != mandatory.Body {
		t.Fatalf("required instruction was lost or truncated: %v", err)
	}
	_, err = op.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: repo, GrantID: root.ID, Rules: &PolicyRules{}, Reason: "Exclude optional records while preserving mandatory whole context"})
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	disabled, err := op.Index(ctx, req, Destination{"local", true})
	if err != nil || disabled.Package.Semantic.OptionalLimit != 0 || len(disabled.Package.Semantic.Index) != 0 || len(disabled.Package.Semantic.Selected) != 1 {
		t.Fatalf("memory allocation bypassed operator policy: %+v %v", disabled, err)
	}
	old, err := op.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query})
	if err != nil || old.Seal != index.Package.Seal {
		t.Fatalf("current policy changed historical budget: %v", err)
	}
}

func TestMemoryBudgetRejectsResealedUnknownOrInvalidContract(t *testing.T) {
	ctx := context.Background()
	op, _ := testOperator(t)
	repo := uuid.NewString()
	room := 6000
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "allocation", Purpose: "context", AvailableTokens: 24000, MemoryBudgetBytes: &room}
	index, err := op.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	for _, bad := range []MemoryBudget{{Schema: "future", Bytes: 6000}, {Schema: "cairn.memory-budget/1", Bytes: 24001}, {Schema: "cairn.memory-budget/1", Bytes: 0}} {
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
