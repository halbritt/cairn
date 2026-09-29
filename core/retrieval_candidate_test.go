package core

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
)

func TestCandidatePersistenceFailureRollsBackReceiptAndAllowsRetry(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "candidate-write-fault"})
	repo := uuid.NewString()
	for _, body := range []string{"First selected guidance", "Second selected guidance"} {
		draft := projectNote(repo)
		draft.Body = body
		if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
			t.Fatal(err)
		}
	}
	// Reject the second row, after persistence has begun, regardless of map order.
	_, err := s.pool.Exec(ctx, `CREATE FUNCTION cairn.test_candidate_write_failure() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN
 IF current_setting('cairn.caller',true)='candidate-write-fault' AND
 EXISTS(SELECT 1 FROM cairn.retrieval_candidate WHERE receipt_id=NEW.receipt_id) THEN
 RAISE EXCEPTION 'synthetic candidate write failure' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
 END; $$;
 CREATE TRIGGER test_candidate_write_failure BEFORE INSERT ON cairn.retrieval_candidate
 FOR EACH ROW EXECUTE FUNCTION cairn.test_candidate_write_failure()`)
	if err != nil {
		t.Fatal(err)
	}
	const removeFault = `DROP TRIGGER IF EXISTS test_candidate_write_failure ON cairn.retrieval_candidate;
 DROP FUNCTION IF EXISTS cairn.test_candidate_write_failure()`
	t.Cleanup(func() {
		if _, err := s.pool.Exec(context.Background(), removeFault); err != nil {
			t.Error(err)
		}
	})
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 32000}
	failed, err := s.Index(ctx, req, Destination{"local", true})
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "23514" || failed.Package.ReceiptID != "" {
		t.Fatalf("candidate persistence failure was not propagated: %+v %v", failed, err)
	}
	var receipts int
	if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.retrieval_receipt WHERE caller=$1 AND request_id=$2`, s.channel.Principal, req.RequestID).Scan(&receipts); err != nil {
		t.Fatal(err)
	}
	if receipts != 0 {
		t.Fatalf("failed candidate persistence retained %d receipts", receipts)
	}
	if _, err := s.pool.Exec(ctx, removeFault); err != nil {
		t.Fatal(err)
	}
	index, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	explanation, err := s.Explain(ctx, index.Package.ReceiptID)
	if err != nil {
		t.Fatal(err)
	}
	if len(explanation.Candidates) != 2 || len(index.Package.Semantic.Index) != 2 {
		t.Fatalf("retry lost candidate snapshots: %+v", explanation)
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID})
	if err != nil || replay.Seal != index.Package.Seal {
		t.Fatalf("retry did not retain a replayable receipt: %+v %v", replay, err)
	}
}
