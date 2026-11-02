package core

import (
	"context"
	"fmt"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Retention and rewrites act on the whole store, so each test owns a database.
func retentionStore(t *testing.T) *Store {
	t.Helper()
	ctx := context.Background()
	host := testStore(t, Channel{Principal: "retention:host"})
	name := "cairn_retention_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, err := host.pool.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{name}.Sanitize()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := host.pool.Exec(context.Background(), "DROP DATABASE "+pgx.Identifier{name}.Sanitize()+" WITH (FORCE)"); err != nil {
			t.Error(err)
		}
	})
	config, err := pgxpool.ParseConfig(os.Getenv("CAIRN_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	config.ConnConfig.Database = name
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	s := &Store{pool: pool, channel: Channel{Principal: "retention:operator", Operator: true, Instrumented: true}}
	t.Cleanup(s.Close)
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	return s
}

var retentionLocal = Destination{"local", true}

// Test limits ignore host disk use: a nearly full CI machine is not a test failure.
var retentionTestLimits = CandidateMaintenanceLimits{MinFreeBytes: 1}

func retentionNote(t *testing.T, s *Store, repo, body string) Record {
	t.Helper()
	draft := projectNote(repo)
	draft.Body = body
	r, err := s.Create(context.Background(), CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func retentionIndex(t *testing.T, s *Store, repo, query string) CompileRequest {
	t.Helper()
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 32000}
	if _, err := s.Index(context.Background(), req, retentionLocal); err != nil {
		t.Fatal(err)
	}
	return req
}

func retentionReceipt(t *testing.T, s *Store, req CompileRequest) string {
	t.Helper()
	var id string
	if err := s.pool.QueryRow(context.Background(), `SELECT receipt_id::text FROM cairn.retrieval_receipt WHERE caller=$1 AND request_id=$2`, s.channel.Principal, req.RequestID).Scan(&id); err != nil {
		t.Fatal(err)
	}
	return id
}

func backdateReceipt(t *testing.T, s *Store, id string, days int) {
	t.Helper()
	if _, err := s.pool.Exec(context.Background(), `UPDATE cairn.retrieval_receipt SET created_at=now()-make_interval(days => $2) WHERE receipt_id=$1`, id, days); err != nil {
		t.Fatal(err)
	}
}

func candidateRows(t *testing.T, s *Store, receipt string) int {
	t.Helper()
	var rows int
	if err := s.pool.QueryRow(context.Background(), `SELECT count(*) FROM cairn.retrieval_candidate WHERE receipt_id=$1`, receipt).Scan(&rows); err != nil {
		t.Fatal(err)
	}
	return rows
}

func expiredAt(t *testing.T, s *Store, receipt string) *time.Time {
	t.Helper()
	var at *time.Time
	if err := s.pool.QueryRow(context.Background(), `SELECT candidate_detail_expired_at FROM cairn.retrieval_receipt WHERE receipt_id=$1`, receipt).Scan(&at); err != nil {
		t.Fatal(err)
	}
	return at
}

func expire(t *testing.T, s *Store, req CandidateRetentionRequest) CandidateRetentionResult {
	t.Helper()
	if req.Limits == (CandidateMaintenanceLimits{}) {
		req.Limits = retentionTestLimits
	}
	result, err := s.ExpireCandidateDetail(context.Background(), req)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func TestExpiredCandidateDetailRefusesInsteadOfLookingEmpty(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	retentionNote(t, s, repo, "alpha fixture_error first")
	retentionNote(t, s, repo, "alpha fixture_error second")
	retentionNote(t, s, repo, "entirely unrelated advice")
	query := "alpha fixture_error"
	body, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 64000}, retentionLocal)
	if err != nil || len(body.Semantic.Selected) == 0 {
		t.Fatalf("selecting fixture: %+v %v", body, err)
	}
	oldIndexReq := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 32000}
	oldIndex, err := s.Index(ctx, oldIndexReq, retentionLocal)
	if err != nil || len(oldIndex.Package.Semantic.Index) == 0 {
		t.Fatalf("index fixture: %+v %v", oldIndex, err)
	}
	inWindow, err := s.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 32000}, retentionLocal)
	if err != nil {
		t.Fatal(err)
	}
	recentBody, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 64000}, retentionLocal)
	if err != nil {
		t.Fatal(err)
	}
	backdateReceipt(t, s, body.ReceiptID, 30)
	backdateReceipt(t, s, oldIndex.Package.ReceiptID, 30)
	uses := func(id string) (n int) {
		if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.record_use WHERE receipt_id=$1`, id).Scan(&n); err != nil {
			t.Fatal(err)
		}
		return n
	}
	usesBefore := uses(oldIndex.Package.ReceiptID)
	rowsBefore := candidateRows(t, s, oldIndex.Package.ReceiptID)

	dry := expire(t, s, CandidateRetentionRequest{WindowDays: 14, DryRun: true, KeepSelectedPerRepo: 1})
	if dry.EligibleReceipts != 2 || dry.ExpiredReceipts != 0 || dry.StoppedBy != "dry_run" || candidateRows(t, s, body.ReceiptID) == 0 {
		t.Fatalf("dry run: %+v", dry)
	}
	result := expire(t, s, CandidateRetentionRequest{WindowDays: 14, KeepSelectedPerRepo: 1})
	if result.ExpiredReceipts != 2 || result.DeletedRows != int64(rowsBefore)*2 || result.RemainingReceipts != 0 || result.StoppedBy != "complete" {
		t.Fatalf("expiry: %+v", result)
	}

	// All three detail readers refuse explicitly, including through the destination paths.
	for _, id := range []string{body.ReceiptID, oldIndex.Package.ReceiptID} {
		if candidateRows(t, s, id) != 0 || expiredAt(t, s, id) == nil {
			t.Fatalf("receipt %s not fully expired", id)
		}
		explanation, err := s.Explain(ctx, id)
		requireCode(t, err, "CANDIDATE_DETAIL_EXPIRED")
		if explanation.Candidates != nil {
			t.Fatalf("expired explanation carried a candidate list: %+v", explanation)
		}
		_, err = s.ExplainPageForDestination(ctx, ExplainPageRequest{ReceiptID: id}, retentionLocal)
		requireCode(t, err, "CANDIDATE_DETAIL_EXPIRED")
		_, err = s.Recompile(ctx, RecompileRequest{ReceiptID: id, Query: query})
		requireCode(t, err, "CANDIDATE_DETAIL_EXPIRED")
		_, err = s.RecompileForDestination(ctx, RecompileRequest{ReceiptID: id, Query: query}, retentionLocal)
		requireCode(t, err, "CANDIDATE_DETAIL_EXPIRED")
	}
	// Saved bytes, seal, identity and selected-use history survive.
	replay, err := s.Replay(ctx, body.ReceiptID)
	if err != nil || replay.Seal != body.Seal || len(replay.Semantic.Selected) != len(body.Semantic.Selected) {
		t.Fatalf("replay after expiry: %+v %v", replay, err)
	}
	if got := uses(oldIndex.Package.ReceiptID); got != usesBefore || got == 0 {
		t.Fatalf("record_use changed: %d -> %d", usesBefore, got)
	}
	retry, err := s.Index(ctx, oldIndexReq, retentionLocal)
	if err != nil || retry.Package.ReceiptID != oldIndex.Package.ReceiptID || retry.Package.Seal != oldIndex.Package.Seal {
		t.Fatalf("idempotent compile retry after expiry: %+v %v", retry, err)
	}
	if candidateRows(t, s, oldIndex.Package.ReceiptID) != 0 {
		t.Fatal("compile retry recreated expired candidate rows")
	}
	expansion, err := s.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: oldIndex.Package.ReceiptID, Handle: oldIndex.Handles[0].Handle}, retentionLocal)
	if err != nil || expansion.Selection.Record.Body == "" {
		t.Fatalf("source pull after expiry: %+v %v", expansion, err)
	}

	// Receipts inside the window, and the newest selecting receipt, keep every path.
	for _, id := range []string{inWindow.Package.ReceiptID, recentBody.ReceiptID} {
		explanation, err := s.Explain(ctx, id)
		if err != nil || len(explanation.Candidates) != rowsBefore {
			t.Fatalf("retained explanation: %+v %v", explanation, err)
		}
		page, err := s.ExplainPageForDestination(ctx, ExplainPageRequest{ReceiptID: id}, retentionLocal)
		if err != nil || len(page.Candidates) != rowsBefore {
			t.Fatalf("retained page: %+v %v", page, err)
		}
		recompiled, err := s.Recompile(ctx, RecompileRequest{ReceiptID: id, Query: query})
		if err != nil || recompiled.ReceiptID != id {
			t.Fatalf("retained recompile: %+v %v", recompiled, err)
		}
	}
	// A repeated run is a no-op.
	if again := expire(t, s, CandidateRetentionRequest{WindowDays: 14, KeepSelectedPerRepo: 1}); again.ExpiredReceipts != 0 || again.EligibleReceipts != 0 {
		t.Fatalf("repeat: %+v", again)
	}
}

func TestCandidateRetentionAccessAndWindowContract(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	for _, window := range []int{0, -1, 36501} {
		_, err := s.ExpireCandidateDetail(ctx, CandidateRetentionRequest{WindowDays: window})
		requireCode(t, err, "INVALID_REQUEST")
	}
	_, err := s.ExpireCandidateDetail(ctx, CandidateRetentionRequest{WindowDays: 14, MaxReceipts: -1})
	requireCode(t, err, "INVALID_REQUEST")
	for _, channel := range []Channel{{Principal: "agent:plain"}, {Principal: "operator:scoped", Operator: true, Repo: "some-repo"}} {
		caller := &Store{pool: s.pool, channel: channel}
		_, err = caller.ExpireCandidateDetail(ctx, CandidateRetentionRequest{WindowDays: 14})
		requireCode(t, err, "AUTHORITY_DENIED")
		_, err = caller.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "status"})
		requireCode(t, err, "AUTHORITY_DENIED")
	}
}

func TestRunBoundAndFixtureReceiptsKeepDetail(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	retentionNote(t, s, repo, "gamma fixture_error")
	bindings := map[string]string{
		"launch":   `UPDATE cairn.retrieval_receipt SET launch_claimed=true WHERE receipt_id=$1`,
		"binding":  `INSERT INTO cairn.run_binding(receipt_id,task_class,binding_id,capability_id,command_sha256,revision,workspace_sha256) VALUES($1,'t','b','c',repeat('0',64),'r',repeat('0',64))`,
		"outcome":  `INSERT INTO cairn.run_outcome(outcome_id,receipt_id,duration_ms,process_state,task_outcome,stdout_sha256,stderr_sha256) VALUES(gen_random_uuid(),$1,1,'exited','unknown','','')`,
		"delivery": `INSERT INTO cairn.delivery_receipt(delivery_id,receipt_id,adapter,carrier,assurance,rendered_sha256,blind_spots) VALUES(gen_random_uuid(),$1,'a','file','available','','')`,
	}
	protected := map[string]string{}
	for name, statement := range bindings {
		id := retentionReceipt(t, s, retentionIndex(t, s, repo, "gamma"))
		if _, err := s.pool.Exec(ctx, statement, id); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		backdateReceipt(t, s, id, 40)
		protected[name] = id
	}
	// Selecting receipts: the newest two per repository remain restore fixtures.
	selecting := []string{}
	for i := 0; i < 3; i++ {
		p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "gamma", Purpose: "context", AvailableTokens: 64000}, retentionLocal)
		if err != nil || len(p.Semantic.Selected) == 0 {
			t.Fatalf("selecting: %+v %v", p, err)
		}
		backdateReceipt(t, s, p.ReceiptID, 40-i)
		selecting = append(selecting, p.ReceiptID)
	}
	plain := retentionReceipt(t, s, retentionIndex(t, s, repo, "gamma"))
	backdateReceipt(t, s, plain, 40)

	result := expire(t, s, CandidateRetentionRequest{WindowDays: 14, KeepSelectedPerRepo: 2})
	if result.ExpiredReceipts != 2 || result.ProtectedReceipts != len(bindings)+2 {
		t.Fatalf("protection census: %+v", result)
	}
	for name, id := range protected {
		if expiredAt(t, s, id) != nil || candidateRows(t, s, id) == 0 {
			t.Fatalf("%s receipt lost detail", name)
		}
	}
	if expiredAt(t, s, selecting[0]) == nil || expiredAt(t, s, plain) == nil {
		t.Fatal("unprotected receipts retained detail")
	}
	for _, id := range selecting[1:] {
		if _, err := s.Recompile(ctx, RecompileRequest{ReceiptID: id, Query: "gamma"}); err != nil {
			t.Fatalf("selecting fixture %s: %v", id, err)
		}
	}
}

func TestExpiryKeepsBlockedDemandAndGateRefusals(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	blocked := retentionNote(t, s, repo, "delta fixture_error demand")
	retentionNote(t, s, repo, "unrelated body")
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "delta fixture_error", Purpose: "planning", AvailableTokens: 64000}, retentionLocal)
	if err != nil {
		t.Fatal(err)
	}
	if candidateRows(t, s, p.ReceiptID) != 2 {
		t.Fatalf("fixture rows = %d", candidateRows(t, s, p.ReceiptID))
	}
	backdateReceipt(t, s, p.ReceiptID, 30)
	result := expire(t, s, CandidateRetentionRequest{WindowDays: 14})
	if result.ExpiredReceipts != 1 || result.RetainedRows != 2 {
		t.Fatalf("gate refusals must survive: %+v", result)
	}
	_, err = s.Explain(ctx, p.ReceiptID)
	requireCode(t, err, "CANDIDATE_DETAIL_EXPIRED")
	docket, err := s.Docket(ctx, repo)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, item := range docket.Items {
		found = found || (item.Reason == "ESCALATION_BLOCKED" && item.RecordID == blocked.RecordID && item.ReceiptID == p.ReceiptID)
	}
	if !found {
		t.Fatalf("blocked demand left the docket: %+v", docket)
	}
}

func TestOrdinaryDeleteFollowsRetainedReferences(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	considered := retentionNote(t, s, repo, "zzqx unmatched considered note")
	indexed := retentionNote(t, s, repo, "epsilon fixture_error indexed")
	remove := func(record Record) error {
		_, err := s.Delete(ctx, DeleteRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version})
		return err
	}
	oldest := retentionReceipt(t, s, retentionIndex(t, s, repo, "epsilon fixture_error"))
	newer := retentionReceipt(t, s, retentionIndex(t, s, repo, "epsilon fixture_error"))
	requireCode(t, remove(considered), "FORGET_REQUIRED")
	// A retained read set elsewhere still protects the note.
	backdateReceipt(t, s, oldest, 30)
	expire(t, s, CandidateRetentionRequest{WindowDays: 14})
	if expiredAt(t, s, oldest) == nil || expiredAt(t, s, newer) != nil {
		t.Fatal("wrong receipts expired")
	}
	requireCode(t, remove(considered), "FORGET_REQUIRED")
	// Once every read set referencing it has expired, the reference has ended too.
	backdateReceipt(t, s, newer, 30)
	expire(t, s, CandidateRetentionRequest{WindowDays: 14})
	if err := remove(considered); err != nil {
		t.Fatalf("an expired read set must not block ordinary deletion: %v", err)
	}
	// record_use is not candidate detail: what a receipt exposed stays protected.
	requireCode(t, remove(indexed), "FORGET_REQUIRED")
	if _, err := s.Replay(ctx, oldest); err != nil {
		t.Fatalf("replay after deleting a considered note: %v", err)
	}
}

func TestExpiryInterruptionLeavesEachReceiptWholeAndResumes(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	retentionNote(t, s, repo, "eta fixture_error")
	retentionNote(t, s, repo, "eta other")
	ids := []string{}
	for i := 0; i < 4; i++ {
		id := retentionReceipt(t, s, retentionIndex(t, s, repo, "eta"))
		backdateReceipt(t, s, id, 40-i)
		ids = append(ids, id)
	}
	// A bounded slice stops at its limit and reports what remains.
	first := expire(t, s, CandidateRetentionRequest{WindowDays: 14, MaxReceipts: 1})
	if first.ExpiredReceipts != 1 || first.RemainingReceipts != 3 || first.StoppedBy != "max_receipts" || expiredAt(t, s, ids[0]) == nil {
		t.Fatalf("bounded slice: %+v", first)
	}
	// A failure inside one receipt's transaction leaves that receipt unexpired and intact.
	fault := `CREATE FUNCTION cairn.test_retention_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF OLD.receipt_id='` + ids[2] + `'::uuid THEN RAISE EXCEPTION 'synthetic retention failure'; END IF; RETURN OLD; END; $$;
 CREATE TRIGGER test_retention_fault BEFORE DELETE ON cairn.retrieval_candidate FOR EACH ROW EXECUTE FUNCTION cairn.test_retention_fault()`
	if _, err := s.pool.Exec(ctx, fault); err != nil {
		t.Fatal(err)
	}
	result, err := s.ExpireCandidateDetail(ctx, CandidateRetentionRequest{WindowDays: 14, Limits: retentionTestLimits})
	if err == nil || result.ExpiredReceipts != 1 {
		t.Fatalf("fault not surfaced: %+v %v", result, err)
	}
	if expiredAt(t, s, ids[1]) == nil || expiredAt(t, s, ids[2]) != nil || candidateRows(t, s, ids[2]) != 2 {
		t.Fatal("failed receipt was left partially expired")
	}
	if _, err := s.Explain(ctx, ids[2]); err != nil {
		t.Fatalf("unexpired receipt after failed expiry: %v", err)
	}
	if _, err := s.pool.Exec(ctx, `DROP TRIGGER test_retention_fault ON cairn.retrieval_candidate; DROP FUNCTION cairn.test_retention_fault()`); err != nil {
		t.Fatal(err)
	}
	// Cancellation returns an error and no half-done receipt.
	canceled, cancel := context.WithCancel(ctx)
	cancel()
	if _, err = s.ExpireCandidateDetail(canceled, CandidateRetentionRequest{WindowDays: 14, Limits: retentionTestLimits}); err == nil {
		t.Fatal("canceled run reported success")
	}
	resumed := expire(t, s, CandidateRetentionRequest{WindowDays: 14})
	if resumed.ExpiredReceipts != 2 || resumed.RemainingReceipts != 0 {
		t.Fatalf("resume: %+v", resumed)
	}
	for _, id := range ids {
		if expiredAt(t, s, id) == nil || candidateRows(t, s, id) != 0 {
			t.Fatalf("receipt %s after resume", id)
		}
	}
}

func TestExpiryStopsAtClusterLimitsAndRunsAlone(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	retentionNote(t, s, repo, "theta fixture_error")
	id := retentionReceipt(t, s, retentionIndex(t, s, repo, "theta"))
	backdateReceipt(t, s, id, 30)
	for name, limits := range map[string]CandidateMaintenanceLimits{
		"min_free_bytes":    {MinFreeBytes: 1 << 62},
		"max_wal_dir_bytes": {MinFreeBytes: 1, MaxWALDirBytes: 1},
	} {
		result := expire(t, s, CandidateRetentionRequest{WindowDays: 14, Limits: limits})
		if result.StoppedBy != name || result.ExpiredReceipts != 0 || expiredAt(t, s, id) != nil {
			t.Fatalf("%s: %+v", name, result)
		}
	}
	release, err := s.holdCandidateMaintenance(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.ExpireCandidateDetail(ctx, CandidateRetentionRequest{WindowDays: 14, Limits: retentionTestLimits})
	requireCode(t, err, "MAINTENANCE_ACTIVE")
	_, err = s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "plan", WindowDays: 14})
	requireCode(t, err, "MAINTENANCE_ACTIVE")
	release()
	if result := expire(t, s, CandidateRetentionRequest{WindowDays: 14}); result.ExpiredReceipts != 1 {
		t.Fatalf("after release: %+v", result)
	}
}

// A locked receipt is skipped, not waited on; a reference that commits first wins.
func TestExpirySerializesWithNewReceiptReferences(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	retentionNote(t, s, repo, "iota fixture_error")
	id := retentionReceipt(t, s, retentionIndex(t, s, repo, "iota"))
	backdateReceipt(t, s, id, 30)
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `SELECT set_config('cairn.caller','observer',true)`); err != nil {
		t.Fatal(err)
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.delivery_receipt(delivery_id,receipt_id,adapter,carrier,assurance,rendered_sha256,blind_spots) VALUES(gen_random_uuid(),$1,'a','file','available','','')`, id); err != nil {
		t.Fatal(err)
	}
	locked := expire(t, s, CandidateRetentionRequest{WindowDays: 14})
	if locked.SkippedLocked != 1 || locked.ExpiredReceipts != 0 || expiredAt(t, s, id) != nil {
		t.Fatalf("referencing transaction ignored: %+v", locked)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	after := expire(t, s, CandidateRetentionRequest{WindowDays: 14})
	if after.ExpiredReceipts != 0 || after.ProtectedReceipts != 1 || expiredAt(t, s, id) != nil || candidateRows(t, s, id) == 0 {
		t.Fatalf("committed reference did not protect the receipt: %+v", after)
	}
}

func TestExpiryWithConcurrentCompilesAndExplanations(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	for i := 0; i < 40; i++ {
		retentionNote(t, s, repo, fmt.Sprintf("kappa fixture_error note %d", i))
	}
	old := []string{}
	for i := 0; i < 40; i++ {
		id := retentionReceipt(t, s, retentionIndex(t, s, repo, "kappa"))
		backdateReceipt(t, s, id, 40)
		old = append(old, id)
	}
	var fresh sync.Map
	var stop atomic.Bool
	var writers sync.WaitGroup
	errs := make(chan error, 8)
	for w := 0; w < 3; w++ {
		writers.Add(1)
		go func() {
			defer writers.Done()
			for !stop.Load() {
				req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "kappa", Purpose: "context", AvailableTokens: 32000}
				index, err := s.Index(ctx, req, retentionLocal)
				// Competing compiles can exhaust their own bounded retries, retention or not.
				if Code(err) == "VERSION_CONFLICT" {
					continue
				}
				if err != nil {
					errs <- err
					return
				}
				fresh.Store(index.Package.ReceiptID, true)
				explanation, err := s.Explain(ctx, index.Package.ReceiptID)
				if err != nil || len(explanation.Candidates) != 40 {
					errs <- fmt.Errorf("fresh explanation: %d rows, %v", len(explanation.Candidates), err)
					return
				}
			}
		}()
	}
	// Readers of expiring receipts see the complete set or the refusal, never part of it.
	writers.Add(1)
	go func() {
		defer writers.Done()
		for !stop.Load() {
			for _, id := range old {
				explanation, err := s.Explain(ctx, id)
				if err == nil && len(explanation.Candidates) != 40 {
					errs <- fmt.Errorf("partial explanation of %s: %d rows", id, len(explanation.Candidates))
					return
				}
				if err != nil && Code(err) != "CANDIDATE_DETAIL_EXPIRED" {
					errs <- err
					return
				}
			}
		}
	}()
	result := expire(t, s, CandidateRetentionRequest{WindowDays: 14})
	stop.Store(true)
	writers.Wait()
	close(errs)
	for err := range errs {
		t.Error(err)
	}
	if result.ExpiredReceipts != len(old) {
		t.Fatalf("concurrent expiry: %+v", result)
	}
	count := 0
	fresh.Range(func(key, _ any) bool {
		count++
		if rows := candidateRows(t, s, key.(string)); rows != 40 {
			t.Errorf("fresh receipt lost rows: %d", rows)
		}
		return true
	})
	if count == 0 {
		t.Fatal("no concurrent receipts were written")
	}
}

// Repeated cycles of writes and expiry must plateau: this is the growth bound.
func TestCandidateStoragePlateausUnderRetention(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	for i := 0; i < 30; i++ {
		retentionNote(t, s, repo, fmt.Sprintf("lambda fixture_error %d", i))
	}
	const windowDays, perDay, days = 3, 4, 20
	sizes := make([]int64, 0, days)
	for day := 0; day < days; day++ {
		if _, err := s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET created_at=created_at-interval '1 day'`); err != nil {
			t.Fatal(err)
		}
		for i := 0; i < perDay; i++ {
			retentionIndex(t, s, repo, "lambda")
		}
		expire(t, s, CandidateRetentionRequest{WindowDays: windowDays, KeepSelectedPerRepo: 1})
		if _, err := s.pool.Exec(ctx, `VACUUM cairn.retrieval_candidate`); err != nil {
			t.Fatal(err)
		}
		var size, rows int64
		if err := s.pool.QueryRow(ctx, `SELECT pg_table_size('cairn.retrieval_candidate'),(SELECT count(*) FROM cairn.retrieval_candidate)`).Scan(&size, &rows); err != nil {
			t.Fatal(err)
		}
		sizes = append(sizes, size)
		if wantMax := int64((windowDays + 1) * perDay * 30); rows > wantMax {
			t.Fatalf("day %d retained %d rows, more than the window allows (%d)", day, rows, wantMax)
		}
	}
	settled := sizes[windowDays+2]
	for day, size := range sizes[windowDays+2:] {
		if float64(size) > float64(settled)*1.25 {
			t.Fatalf("storage kept growing on day %d: %d after settling at %d (%v)", day, size, settled, sizes)
		}
	}
}

func TestVerifyRestoreNamesExpiredFixtures(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	verify, _, _ := restoreVerificationFixture(t, s)
	report, err := s.VerifyRestore(ctx, verify)
	if err != nil || !report.Ready {
		t.Fatalf("intact restore: %+v %v", report, err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET candidate_detail_expired_at=now() WHERE receipt_id=$1`, verify.Fixtures[0].ReceiptID); err != nil {
		t.Fatal(err)
	}
	report, err = s.VerifyRestore(ctx, verify)
	if err != nil || report.Ready {
		t.Fatalf("expired fixture accepted: %+v %v", report, err)
	}
	want := "FIXTURE_CANDIDATE_DETAIL_EXPIRED:" + verify.Fixtures[0].ReceiptID
	found := false
	for _, problem := range report.Problems {
		found = found || problem == want
	}
	if !found {
		t.Fatalf("expired fixture not named: %+v", report.Problems)
	}
}
