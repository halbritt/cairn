package core

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestCitedPassageSurvivesCorrectionAndHistoricalRecompile(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "citation-writer"})
	r, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	body := strings.Repeat("background\n", 3000) + "use the explicit override"
	e, err := op.CaptureEvidence(ctx, EvidenceRequest{uuid.NewString(), repo, body, "passage fixture", "local"})
	if err != nil {
		t.Fatal(err)
	}
	span := ByteSpanRequest{Offset: len(body) - len("use the explicit override"), Length: len("use the explicit override")}
	var req PromoteRequest
	encoded, err := json.Marshal(map[string]any{"request_id": uuid.NewString(), "record_id": r.RecordID, "expected_version": r.Version, "grant_id": root.ID, "reason": "Qualify the claim using the precise retained passage", "evidence_citations": []any{map[string]any{"evidence_id": e.ID, "expected_sha256": e.Digest, "spans": []ByteSpanRequest{span}}}})
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(encoded, &req); err != nil {
		t.Fatal(err)
	}
	r, err = op.Promote(ctx, req)
	if err != nil {
		t.Fatalf("precise citation promotion: %v", err)
	}
	index, err := op.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil || len(index.Handles) != 1 {
		t.Fatalf("index: %+v %v", index, err)
	}
	pull, err := op.Expand(ctx, ExpandRequest{uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, nil}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	encoded, err = json.Marshal(pull.Selection.Evidence)
	if err != nil {
		t.Fatal(err)
	}
	var refs []struct {
		Citation *struct {
			SHA256   string            `json:"sha256"`
			Relation string            `json:"relation"`
			Spans    []ByteSpanRequest `json:"spans"`
		} `json:"citation"`
	}
	if err = json.Unmarshal(encoded, &refs); err != nil {
		t.Fatal(err)
	}
	if len(refs) != 1 || refs[0].Citation == nil || refs[0].Citation.SHA256 != e.Digest || refs[0].Citation.Relation != "supports" || len(refs[0].Citation.Spans) != 1 || refs[0].Citation.Spans[0] != span {
		t.Fatalf("citation lost: %s", encoded)
	}
	source, err := op.ExpandEvidence(ctx, ExpandEvidenceRequest{e.Digest, uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle, e.ID, &refs[0].Citation.Spans[0]}, Destination{"local", true})
	if err != nil || source.Span == nil || source.Span.Body != "use the explicit override" {
		t.Fatalf("cited source pull: %+v %v", source, err)
	}
	_, err = op.Correct(ctx, CorrectRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: r.Version, GrantID: root.ID, Draft: r.Draft, EvidenceIDs: []string{e.ID}, Reason: "Retain full source support on a new claim version"})
	if err != nil {
		t.Fatal(err)
	}
	replayed, err := op.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID})
	if err != nil || replayed.Seal != index.Package.Seal {
		t.Fatalf("frozen citation changed: %+v %v", replayed, err)
	}
}

func TestCitationRefusalsAreAtomicAndIntentBound(t *testing.T) {
	ctx := context.Background()
	op, root, r, e, _, dest := evidenceExpansionFixtureWithBody(t, "local", "aéz")
	good := EvidenceCitationRequest{EvidenceID: e.ID, ExpectedSHA256: e.Digest, Spans: []ByteSpanRequest{{Offset: 1, Length: 2}, {Offset: 3, Length: 1}}}
	req := CorrectRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: r.Version, GrantID: root, Draft: r.Draft, Reason: "Correct the precise supporting passage", EvidenceCitations: []EvidenceCitationRequest{good}}
	for _, tc := range []struct {
		name      string
		citations []EvidenceCitationRequest
		ids       []string
		code      string
	}{
		{"mixed", []EvidenceCitationRequest{good}, []string{e.ID}, "INVALID_REQUEST"},
		{"duplicate", []EvidenceCitationRequest{good, good}, nil, "INVALID_REQUEST"},
		{"missing digest", []EvidenceCitationRequest{{EvidenceID: e.ID}}, nil, "INVALID_REQUEST"},
		{"wrong digest", []EvidenceCitationRequest{{EvidenceID: e.ID, ExpectedSHA256: strings.Repeat("0", 64)}}, nil, "EVIDENCE_UNAVAILABLE"},
		{"negative", []EvidenceCitationRequest{{EvidenceID: e.ID, ExpectedSHA256: e.Digest, Spans: []ByteSpanRequest{{Offset: -1, Length: 1}}}}, nil, "INVALID_REQUEST"},
		{"empty", []EvidenceCitationRequest{{EvidenceID: e.ID, ExpectedSHA256: e.Digest, Spans: []ByteSpanRequest{{Offset: 0, Length: 0}}}}, nil, "INVALID_REQUEST"},
		{"past EOF", []EvidenceCitationRequest{{EvidenceID: e.ID, ExpectedSHA256: e.Digest, Spans: []ByteSpanRequest{{Offset: 3, Length: 2}}}}, nil, "INVALID_REQUEST"},
		{"overflow", []EvidenceCitationRequest{{EvidenceID: e.ID, ExpectedSHA256: e.Digest, Spans: []ByteSpanRequest{{Offset: int(^uint(0) >> 1), Length: 1}}}}, nil, "INVALID_REQUEST"},
		{"too many", []EvidenceCitationRequest{{EvidenceID: e.ID, ExpectedSHA256: e.Digest, Spans: make([]ByteSpanRequest, 33)}}, nil, "INVALID_REQUEST"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			bad := req
			bad.EvidenceCitations, bad.EvidenceIDs = tc.citations, tc.ids
			_, err := op.Correct(ctx, bad)
			requireCode(t, err, tc.code)
			var versions int
			if err = op.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.record_version WHERE record_id=$1`, r.RecordID).Scan(&versions); err != nil || versions != r.Version {
				t.Fatalf("failed citation advanced claim: %d %v", versions, err)
			}
		})
	}
	corrected, err := op.Correct(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	again, err := op.Correct(ctx, req)
	if err != nil || again.Version != corrected.Version {
		t.Fatalf("same intent retry: %+v %v", again, err)
	}
	req.EvidenceCitations = []EvidenceCitationRequest{{EvidenceID: e.ID, ExpectedSHA256: e.Digest, Spans: []ByteSpanRequest{{Offset: 0, Length: 1}}}}
	_, err = op.Correct(ctx, req)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	p, err := op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{r.Scope.Repo, "task", "run"}, Purpose: "planning", AvailableTokens: 64000}, dest)
	if err != nil || len(p.Semantic.Selected) != 1 {
		t.Fatalf("current citation: %+v %v", p, err)
	}
	got := p.Semantic.Selected[0].Evidence[0].Citation
	if got == nil || len(got.Spans) != 2 || got.Spans[0] != good.Spans[0] || got.Spans[1] != good.Spans[1] {
		t.Fatalf("multiple byte passages lost: %+v", got)
	}
}

func TestCitedDigestDetectsWholeObjectReplacementAndPreservesHistory(t *testing.T) {
	ctx := context.Background()
	op, _, r, e, _, dest := evidenceExpansionFixture(t, "local")
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{r.Scope.Repo, "task", "run"}, Purpose: "planning", AvailableTokens: 64000}
	before, err := op.Compile(ctx, req, dest)
	if err != nil || len(before.Semantic.Selected) != 1 || before.Semantic.Selected[0].Evidence[0].Citation == nil {
		t.Fatalf("new ID-only link must pin source: %+v %v", before, err)
	}
	changed := []byte("replaced body and internally consistent stored digest")
	hash := sha256.Sum256(changed)
	if _, err = op.pool.Exec(ctx, `UPDATE cairn.evidence SET body=$2,digest=$3 WHERE evidence_id=$1`, e.ID, changed, hash[:]); err != nil {
		t.Fatal(err)
	}
	check, err := op.CheckEvidence(ctx, EvidenceCheckRequest{uuid.NewString(), e.ID})
	if err != nil || check.State != "resolvable" {
		t.Fatalf("object-level check: %+v %v", check, err)
	}
	req.RequestID = uuid.NewString()
	after, err := op.Compile(ctx, req, dest)
	if err != nil || len(after.Semantic.Selected) != 0 || after.Semantic.Omitted["EVIDENCE_UNAVAILABLE"] != 1 {
		t.Fatalf("replaced source still qualifies earlier citation: %+v %v", after, err)
	}
	req.RequestID, req.Purpose = uuid.NewString(), "context"
	contextOnly, err := op.Compile(ctx, req, dest)
	if err != nil || len(contextOnly.Semantic.Selected) != 1 {
		t.Fatalf("degraded context: %+v %v", contextOnly, err)
	}
	ref := contextOnly.Semantic.Selected[0].Evidence[0]
	if ref.State != "divergent" || ref.Digest != e.Digest || ref.Citation.SHA256 != e.Digest {
		t.Fatalf("earlier cited identity lost: %+v", ref)
	}
	replay, err := op.Recompile(ctx, RecompileRequest{ReceiptID: before.ReceiptID})
	if err != nil || replay.Seal != before.Seal || replay.Semantic.Selected[0].Evidence[0].State != "resolvable" {
		t.Fatalf("historical citation rewritten: %+v %v", replay, err)
	}
}

func TestLegacyCitationMetadataRemainsAbsent(t *testing.T) {
	ctx := context.Background()
	op, _, r, _, _, dest := evidenceExpansionFixture(t, "local")
	// The migration leaves pre-existing references null rather than reconstructing
	// supposed historical facts from the current evidence object.
	if _, err := op.pool.Exec(ctx, `UPDATE cairn.evidence_ref SET cited_digest=NULL,cited_spans=NULL WHERE record_id=$1`, r.RecordID); err != nil {
		t.Fatal(err)
	}
	p, err := op.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{r.Scope.Repo, "task", "run"}, Purpose: "planning", AvailableTokens: 64000}, dest)
	if err != nil || len(p.Semantic.Selected) != 1 {
		t.Fatalf("legacy availability changed: %+v %v", p, err)
	}
	ref := p.Semantic.Selected[0].Evidence[0]
	encoded, err := json.Marshal(ref)
	if err != nil || ref.Citation != nil || strings.Contains(string(encoded), "citation") {
		t.Fatalf("legacy reference manufactured citation metadata: %s %v", encoded, err)
	}
	replay, err := op.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID})
	if err != nil || replay.Seal != p.Seal {
		t.Fatalf("legacy recompile: %+v %v", replay, err)
	}
}
