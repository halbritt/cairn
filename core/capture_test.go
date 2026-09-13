package core

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestQueryIsTransient(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "capture:test"})
	repo := uuid.NewString()
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	query := "fixture_error CAIRN_TRANSIENT_QUERY_CANARY"
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(p.Semantic.Selected) != 1 || p.Semantic.Selected[0].Record.RecordID != r.RecordID {
		t.Fatal("transient query must still select matching advice")
	}
	replay, err := s.Replay(ctx, p.ReceiptID)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(replay)
	if err != nil {
		t.Fatal(err)
	}
	var retained []byte
	if err = s.pool.QueryRow(ctx, `SELECT semantic_body FROM cairn.retrieval_receipt WHERE receipt_id=$1`, p.ReceiptID).Scan(&retained); err != nil {
		t.Fatal(err)
	}
	rendered, err := p.Render()
	if err != nil {
		t.Fatal(err)
	}
	for _, body := range []string{string(encoded), string(retained), rendered} {
		if strings.Contains(body, "CAIRN_TRANSIENT_QUERY_CANARY") {
			t.Fatal("raw query survived in retained or rendered package")
		}
	}
	if !strings.HasPrefix(p.Semantic.Query, "sha256:") {
		t.Fatal("query digest missing")
	}
}

// Frozen synthetic v1 bytes guard replay compatibility when semantic v2 changes.
func TestReplayLegacySemanticV1(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "legacy:replay"})
	body, err := os.ReadFile("testdata/semantic-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		CBORHex string `json:"cbor_hex"`
		Seal    string `json:"seal"`
	}
	if err = json.Unmarshal(body, &fixture); err != nil {
		t.Fatal(err)
	}
	canonical, err := hex.DecodeString(fixture.CBORHex)
	if err != nil {
		t.Fatal(err)
	}
	tx, err := s.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	id := uuid.NewString()
	_, err = tx.Exec(ctx, `INSERT INTO cairn.retrieval_receipt(receipt_id,request_id,request_digest,scope,purpose,destination,semantic_body,seal,status,nonce) VALUES($1,$2,$3,$4,'context','local',$5,$6,'SCOPE_EMPTY',$7)`, id, uuid.NewString(), []byte("synthetic legacy fixture"), Scope{"fixture:legacy", "task", "run"}, canonical, fixture.Seal, uuid.NewString())
	if err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	replay, err := s.Replay(ctx, id)
	if err != nil {
		t.Fatal(err)
	}
	if replay.Seal != fixture.Seal || replay.Semantic.Schema != "cairn.semantic/1" || replay.Semantic.Query != "synthetic legacy query" {
		t.Fatalf("legacy semantics changed: %+v", replay)
	}
	explanation, err := s.Explain(ctx, id)
	if err != nil || explanation.Version != 0 || len(explanation.Candidates) != 0 {
		t.Fatalf("invented legacy explanation: %+v %v", explanation, err)
	}
}
