package core

import (
	"bytes"
	"context"
	"fmt"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

func candidateTableSignature(t *testing.T, s *Store) string {
	t.Helper()
	var out bytes.Buffer
	for _, query := range []string{
		`SELECT attname||':'||format_type(atttypid,atttypmod)||':'||attnotnull::text||':'||attnum FROM pg_attribute WHERE attrelid='cairn.retrieval_candidate'::regclass AND attnum>0 AND NOT attisdropped ORDER BY attnum`,
		`SELECT conname||':'||contype::text||':'||convalidated::text||':'||pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='cairn.retrieval_candidate'::regclass ORDER BY conname`,
		`SELECT indexname||':'||indexdef FROM pg_indexes WHERE schemaname='cairn' AND tablename='retrieval_candidate' ORDER BY indexname`,
		`SELECT COALESCE(array_to_string(reloptions,','),'')||':'||relpersistence::text||':'||relowner::regrole::text||':'||relnamespace::regnamespace::text FROM pg_class WHERE oid='cairn.retrieval_candidate'::regclass`,
	} {
		rows, err := s.pool.Query(context.Background(), query)
		if err != nil {
			t.Fatal(err)
		}
		for rows.Next() {
			var line string
			if err = rows.Scan(&line); err != nil {
				t.Fatal(err)
			}
			out.WriteString(line + "\n")
		}
		rows.Close()
		if err = rows.Err(); err != nil {
			t.Fatal(err)
		}
	}
	return out.String()
}

func reclaim(t *testing.T, s *Store, req CandidateReclaimRequest) CandidateReclaimResult {
	t.Helper()
	if req.Limits == (CandidateMaintenanceLimits{}) {
		req.Limits = retentionTestLimits
	}
	result, err := s.ReclaimCandidateStorage(context.Background(), req)
	if err != nil {
		t.Fatalf("%s: %v (%+v)", req.Phase, err, result)
	}
	return result
}

func reclaimThrough(t *testing.T, s *Store, phases ...string) {
	t.Helper()
	for _, phase := range phases {
		req := CandidateReclaimRequest{Phase: phase}
		if phase == "plan" {
			req.WindowDays = 14
		}
		if result := reclaim(t, s, req); result.StoppedBy != "complete" {
			t.Fatalf("%s stopped early: %+v", phase, result)
		}
	}
}

// waitForBackends polls until enough backends are blocked on a statement.
func waitForBackends(t *testing.T, s *Store, query string, want int) {
	t.Helper()
	deadline := time.Now().Add(20 * time.Second)
	for time.Now().Before(deadline) {
		var n int
		if err := s.pool.QueryRow(context.Background(), `SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE $1`, query).Scan(&n); err != nil {
			t.Fatal(err)
		}
		if n >= want {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %d backends on %s", want, query)
}

func wideStore(t *testing.T, s *Store, maxConns int32) *Store {
	t.Helper()
	config := s.pool.Config()
	config.MaxConns = maxConns
	pool, err := pgxpool.NewWithConfig(context.Background(), config)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	return &Store{pool: pool, channel: s.channel}
}

func TestReclaimRewritesOnlyRetainedDetailUnderConcurrentWrites(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	for i := 0; i < 25; i++ {
		retentionNote(t, s, repo, fmt.Sprintf("mu fixture_error %d", i))
	}
	before := candidateTableSignature(t, s)
	expiring := []string{}
	for i := 0; i < 30; i++ {
		id := retentionReceipt(t, s, retentionIndex(t, s, repo, "mu"))
		backdateReceipt(t, s, id, 40)
		expiring = append(expiring, id)
	}
	retained := retentionReceipt(t, s, retentionIndex(t, s, repo, "mu"))
	runBound := retentionReceipt(t, s, retentionIndex(t, s, repo, "mu"))
	if _, err := s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET launch_claimed=true WHERE receipt_id=$1`, runBound); err != nil {
		t.Fatal(err)
	}
	backdateReceipt(t, s, runBound, 40)
	// A planning-purpose receipt carries gate refusals that must outlive its detail.
	gated, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "mu", Purpose: "planning", AvailableTokens: 64000}, retentionLocal)
	if err != nil {
		t.Fatal(err)
	}
	gatedRows := candidateRows(t, s, gated.ReceiptID)
	backdateReceipt(t, s, gated.ReceiptID, 40)
	// One receipt already expired by the daily path keeps its retained rows too.
	alreadyGated, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "mu", Purpose: "planning", AvailableTokens: 64000}, retentionLocal)
	if err != nil {
		t.Fatal(err)
	}
	backdateReceipt(t, s, alreadyGated.ReceiptID, 50)
	if result := expire(t, s, CandidateRetentionRequest{WindowDays: 45}); result.ExpiredReceipts != 1 {
		t.Fatalf("seed expiry: %+v", result)
	}

	var stop atomic.Bool
	var fresh sync.Map
	var writer sync.WaitGroup
	errs := make(chan error, 2)
	// Cleanup runs before the pool closes: a failed assertion must not hang the suite.
	t.Cleanup(func() {
		stop.Store(true)
		writer.Wait()
	})
	writer.Add(1)
	go func() {
		defer writer.Done()
		for !stop.Load() {
			index, err := s.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "mu", Purpose: "context", AvailableTokens: 32000}, retentionLocal)
			if Code(err) == "VERSION_CONFLICT" {
				continue
			}
			if err != nil {
				errs <- err
				return
			}
			fresh.Store(index.Package.ReceiptID, true)
			time.Sleep(2 * time.Millisecond)
		}
	}()

	_, err = s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "plan", Limits: retentionTestLimits})
	requireCode(t, err, "INVALID_REQUEST")
	_, err = s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "copy", Limits: retentionTestLimits})
	requireCode(t, err, "DEPENDENCY_CONFLICT")
	planned := reclaim(t, s, CandidateReclaimRequest{Phase: "plan", WindowDays: 14})
	if planned.State != candidateReclaimPlanned || planned.ExpiringReceipts != int64(len(expiring))+1 || planned.MarkedReceipts != 0 {
		t.Fatalf("plan: %+v", planned)
	}
	_, err = s.ExpireCandidateDetail(ctx, CandidateRetentionRequest{WindowDays: 14, Limits: retentionTestLimits})
	requireCode(t, err, "MAINTENANCE_ACTIVE")
	_, err = s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "plan", WindowDays: 14, Limits: retentionTestLimits})
	requireCode(t, err, "DEPENDENCY_CONFLICT")
	// A receipt that gains a run binding after planning keeps every row.
	late := expiring[0]
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET launch_claimed=true WHERE receipt_id=$1`, late); err != nil {
		t.Fatal(err)
	}
	// Copy stops at its budget, then resumes where it left off.
	if stopped := reclaim(t, s, CandidateReclaimRequest{Phase: "copy", Limits: CandidateMaintenanceLimits{MinFreeBytes: 1 << 62}}); stopped.StoppedBy != "min_free_bytes" || stopped.MarkedReceipts != 0 {
		t.Fatalf("headroom stop: %+v", stopped)
	}
	fault := `CREATE FUNCTION cairn.test_reclaim_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic copy failure'; END; $$;
 CREATE TRIGGER test_reclaim_fault BEFORE INSERT ON ` + candidateReclaimLock + `.copied FOR EACH ROW EXECUTE FUNCTION cairn.test_reclaim_fault()`
	if _, err = s.pool.Exec(ctx, fault); err != nil {
		t.Fatal(err)
	}
	if _, err = s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "copy", Limits: retentionTestLimits}); err == nil {
		t.Fatal("copy fault not surfaced")
	}
	var partial int64
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM `+candidateReclaimNewTable+` WHERE reason NOT IN ('CLASS_NOT_CONSEQUENTIAL')`).Scan(&partial); err != nil {
		t.Fatal(err)
	}
	if partial != 0 {
		t.Fatalf("a failed copy left %d rows without its progress record", partial)
	}
	// Marking happens first, so a marked receipt refuses even before the swap, and
	// an unmarked one is still whole: no reader sees a partial set at any point.
	if expiredAt(t, s, expiring[1]) == nil {
		t.Fatal("copy did not mark planned receipts")
	}
	_, err = s.Explain(ctx, expiring[1])
	requireCode(t, err, "CANDIDATE_DETAIL_EXPIRED")
	if explanation, err := s.Explain(ctx, late); err != nil || len(explanation.Candidates) != 25 {
		t.Fatalf("late-protected receipt: %+v %v", explanation, err)
	}
	if _, err = s.pool.Exec(ctx, `DROP TRIGGER test_reclaim_fault ON `+candidateReclaimLock+`.copied; DROP FUNCTION cairn.test_reclaim_fault()`); err != nil {
		t.Fatal(err)
	}
	copied := reclaim(t, s, CandidateReclaimRequest{Phase: "copy"})
	if copied.StoppedBy != "complete" || copied.ScanBlock != copied.ScanBlocks || copied.ResidualRows != int64(gatedRows)*2 || copied.MarkedReceipts != int64(len(expiring)) {
		t.Fatalf("copy: %+v (gated rows %d)", copied, gatedRows)
	}
	built := reclaim(t, s, CandidateReclaimRequest{Phase: "build"})
	if built.State != candidateReclaimBuilt {
		t.Fatalf("build: %+v", built)
	}
	// A long reader holds the old table: the swap gives up rather than wait unbounded.
	blocker, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = blocker.Rollback(context.Background()) })
	if _, err = blocker.Exec(ctx, `LOCK TABLE cairn.retrieval_candidate IN ACCESS SHARE MODE`); err != nil {
		t.Fatal(err)
	}
	blocked := reclaim(t, s, CandidateReclaimRequest{Phase: "swap", LockTimeoutMS: 50, MaxRuntimeSeconds: 1})
	if blocked.StoppedBy != "swap_lock_unavailable" || blocked.State != candidateReclaimBuilt {
		t.Fatalf("blocked swap: %+v", blocked)
	}
	if err = blocker.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	swapped := reclaim(t, s, CandidateReclaimRequest{Phase: "swap"})
	if swapped.State != candidateReclaimSwapped || swapped.MarkedReceipts != int64(len(expiring)) || swapped.SwapHoldMS < 0 {
		t.Fatalf("swap: %+v", swapped)
	}
	// Writes continue into the swapped table through the same connections.
	time.Sleep(50 * time.Millisecond)
	stop.Store(true)
	writer.Wait()
	close(errs)
	for err := range errs {
		t.Fatalf("concurrent compile: %v", err)
	}
	if after := candidateTableSignature(t, s); after != before {
		t.Fatalf("rewrite changed the table definition:\nbefore:\n%s\nafter:\n%s", before, after)
	}
	fresh.Range(func(key, _ any) bool {
		if rows := candidateRows(t, s, key.(string)); rows != 25 {
			t.Errorf("concurrent receipt has %d rows", rows)
		}
		return true
	})
	for _, id := range expiring[1:] {
		if expiredAt(t, s, id) == nil || candidateRows(t, s, id) != 0 {
			t.Fatalf("expiring receipt %s", id)
		}
		_, err = s.Explain(ctx, id)
		requireCode(t, err, "CANDIDATE_DETAIL_EXPIRED")
	}
	if expiredAt(t, s, late) != nil || candidateRows(t, s, late) != 25 {
		t.Fatal("late-protected receipt lost detail")
	}
	if rows := candidateRows(t, s, gated.ReceiptID); rows != gatedRows {
		t.Fatalf("gate refusals lost: %d of %d", rows, gatedRows)
	}
	if rows := candidateRows(t, s, alreadyGated.ReceiptID); rows != gatedRows {
		t.Fatalf("already-expired gate refusals lost: %d of %d", rows, gatedRows)
	}
	for _, id := range []string{retained, runBound} {
		if _, err = s.Recompile(ctx, RecompileRequest{ReceiptID: id, Query: "mu"}); err != nil {
			t.Fatalf("recompile after swap: %v", err)
		}
	}
	// Constraints are live on the new relation.
	_, err = s.pool.Exec(ctx, `INSERT INTO cairn.retrieval_candidate(receipt_id,record_id,version,reason,escalation_blocked,detail) VALUES($1,gen_random_uuid(),1,'INDEXED',false,'{}')`, retained)
	if err == nil {
		t.Fatal("record foreign key not enforced after swap")
	}
	note := retentionNote(t, s, repo, "mu fixture_error referenced")
	retentionIndex(t, s, repo, "mu")
	_, err = s.Delete(ctx, DeleteRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: note.Version})
	requireCode(t, err, "FORGET_REQUIRED")
	docket, err := s.Docket(ctx, repo)
	if err != nil {
		t.Fatal(err)
	}
	demand := false
	for _, item := range docket.Items {
		demand = demand || item.Reason == "ESCALATION_BLOCKED"
	}
	if !demand {
		t.Fatalf("blocked demand left the docket after the rewrite: %+v", docket)
	}
	// Expiry waits for the rewrite to finish, including between swap and verify.
	_, err = s.ExpireCandidateDetail(ctx, CandidateRetentionRequest{WindowDays: 14, Limits: retentionTestLimits})
	requireCode(t, err, "MAINTENANCE_ACTIVE")

	// Recovery: rows archived from the old relation restore an expired receipt.
	archive := func(id string) []byte {
		var out bytes.Buffer
		conn, err := s.pool.Acquire(ctx)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Release()
		if _, err = conn.Conn().PgConn().CopyTo(ctx, &out, `COPY (SELECT * FROM `+candidateReclaimOldTable+` WHERE receipt_id='`+id+`') TO STDOUT`); err != nil {
			t.Fatal(err)
		}
		return out.Bytes()
	}
	plainArchive, gatedArchive := archive(expiring[1]), archive(gated.ReceiptID)

	// verify, then finish only with explicit confirmation.
	_, err = s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "finish", ConfirmDropOld: true, Limits: retentionTestLimits})
	requireCode(t, err, "DEPENDENCY_CONFLICT")
	if verified := reclaim(t, s, CandidateReclaimRequest{Phase: "verify"}); verified.State != candidateReclaimVerified || verified.VerifiedReceipts == 0 {
		t.Fatalf("verify: %+v", verified)
	}
	_, err = s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "finish", Limits: retentionTestLimits})
	requireCode(t, err, "INVALID_REQUEST")
	sizeBefore := reclaim(t, s, CandidateReclaimRequest{Phase: "status"})
	finished := reclaim(t, s, CandidateReclaimRequest{Phase: "finish", ConfirmDropOld: true})
	if finished.State != candidateReclaimNone || finished.DatabaseBytes >= sizeBefore.DatabaseBytes {
		t.Fatalf("finish: %+v before %+v", finished, sizeBefore)
	}
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	if status := reclaim(t, s, CandidateReclaimRequest{Phase: "status"}); status.State != candidateReclaimNone {
		t.Fatalf("status: %+v", status)
	}
	// The documented recovery works on a receipt with no rows and on one that kept rows.
	for _, rows := range [][]byte{plainArchive, gatedArchive} {
		tx, err := s.pool.Begin(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = tx.Exec(ctx, `CREATE TEMP TABLE restored (LIKE cairn.retrieval_candidate) ON COMMIT DROP`); err != nil {
			t.Fatal(err)
		}
		if _, err = tx.Conn().PgConn().CopyFrom(ctx, bytes.NewReader(rows), `COPY restored FROM STDIN`); err != nil {
			t.Fatal(err)
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.retrieval_candidate SELECT * FROM restored ON CONFLICT DO NOTHING`); err != nil {
			t.Fatal(err)
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.retrieval_receipt SET candidate_detail_expired_at=NULL WHERE receipt_id IN (SELECT receipt_id FROM restored)`); err != nil {
			t.Fatal(err)
		}
		if err = tx.Commit(ctx); err != nil {
			t.Fatal(err)
		}
	}
	restored, err := s.Recompile(ctx, RecompileRequest{ReceiptID: expiring[1], Query: "mu"})
	if err != nil || restored.ReceiptID != expiring[1] {
		t.Fatalf("archived rows did not restore recompilation: %+v %v", restored, err)
	}
	if explanation, err := s.Explain(ctx, gated.ReceiptID); err != nil || len(explanation.Candidates) != gatedRows {
		t.Fatalf("archived rows did not restore a receipt with retained rows: %+v %v", explanation, err)
	}
	// Daily expiry works on the rewritten relation.
	again := expire(t, s, CandidateRetentionRequest{WindowDays: 14})
	if again.ExpiredReceipts != 2 || expiredAt(t, s, expiring[1]) == nil {
		t.Fatalf("expiry after rewrite: %+v", again)
	}
}

// A repeatable-read transaction takes its snapshot before it reaches the admission
// row. Waiting there through the swap must not let it read the new relation.
func TestSwapFailsStaleSnapshotsInsteadOfServingPartialLists(t *testing.T) {
	ctx := context.Background()
	s := wideStore(t, retentionStore(t), 40)
	repo := uuid.NewString()
	for i := 0; i < 25; i++ {
		retentionNote(t, s, repo, fmt.Sprintf("xi fixture_error %d", i))
	}
	expiring := retentionReceipt(t, s, retentionIndex(t, s, repo, "xi"))
	backdateReceipt(t, s, expiring, 40)
	retained := retentionReceipt(t, s, retentionIndex(t, s, repo, "xi"))
	reclaimThrough(t, s, "plan", "copy", "build")

	// The first holder keeps the swap waiting for admission after its catch-up, so a
	// receipt committed now is copied only by the swap's own tail.
	admission, err := s.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = admission.Rollback(context.Background()) })
	// The second parks the swap holding admission, so readers arrive deterministically.
	tableLock, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = tableLock.Rollback(context.Background()) })
	if _, err = tableLock.Exec(ctx, `LOCK TABLE cairn.retrieval_candidate IN ACCESS SHARE MODE`); err != nil {
		t.Fatal(err)
	}
	swapErr := make(chan error, 1)
	go func() {
		_, err := s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "swap", LockTimeoutMS: 60000, Limits: retentionTestLimits})
		swapErr <- err
	}()
	waitForBackends(t, s, `SELECT paused FROM cairn.restore_admission%FOR UPDATE`, 1)
	tail := retentionReceipt(t, s, retentionIndex(t, s, repo, "xi"))
	if err = admission.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	waitForBackends(t, s, `LOCK TABLE cairn.retrieval_candidate%`, 1)

	type outcome struct {
		label string
		rows  int
		err   error
	}
	results := make(chan outcome, 8)
	var readers sync.WaitGroup
	read := func(label, id string, call func(string) (int, error)) {
		readers.Add(1)
		go func() {
			defer readers.Done()
			rows, err := call(id)
			results <- outcome{label, rows, err}
		}()
	}
	explain := func(id string) (int, error) {
		e, err := s.Explain(ctx, id)
		return len(e.Candidates), err
	}
	page := func(id string) (int, error) {
		p, err := s.ExplainPageForDestination(ctx, ExplainPageRequest{ReceiptID: id, Limit: 100}, retentionLocal)
		return len(p.Candidates), err
	}
	read("explain expiring", expiring, explain)
	read("explain retained", retained, explain)
	read("explain tail", tail, explain)
	read("page retained", retained, page)
	read("page tail", tail, page)
	compiled := make(chan string, 1)
	readers.Add(1)
	go func() {
		defer readers.Done()
		index, err := s.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "xi", Purpose: "context", AvailableTokens: 32000}, retentionLocal)
		if err != nil {
			results <- outcome{"compile", 0, err}
			return
		}
		compiled <- index.Package.ReceiptID
	}()
	waitForBackends(t, s, `SELECT paused FROM cairn.restore_admission%FOR SHARE`, 6)
	if err = tableLock.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	if err = <-swapErr; err != nil {
		t.Fatalf("swap: %v", err)
	}
	readers.Wait()
	close(results)
	for result := range results {
		// A failed transaction is retryable; a short list would be a false answer.
		if result.err == nil && result.rows != 25 {
			t.Errorf("%s returned %d rows across the swap", result.label, result.rows)
		}
		if result.err != nil && result.label != "explain expiring" {
			t.Logf("%s: %v", result.label, result.err)
		}
	}
	// Anything begun after the swap gets the correct answer, and the compile retried.
	if rows := candidateRows(t, s, <-compiled); rows != 25 {
		t.Fatalf("receipt compiled through the swap has %d rows", rows)
	}
	for _, id := range []string{retained, tail} {
		if explanation, err := s.Explain(ctx, id); err != nil || len(explanation.Candidates) != 25 {
			t.Fatalf("after swap: %d rows, %v", len(explanation.Candidates), err)
		}
	}
	_, err = s.Explain(ctx, expiring)
	requireCode(t, err, "CANDIDATE_DETAIL_EXPIRED")
}

func TestSwapWorksAfterACompletedRestoreSession(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	verify, _, _ := restoreVerificationFixture(t, s)
	// While paused, leaving the pause without a verified resume is still refused.
	if _, err := s.pool.Exec(ctx, `UPDATE cairn.restore_admission SET paused=false WHERE singleton`); err == nil {
		t.Fatal("unpaused without a verified resume")
	}
	if _, err := s.ResumeRestore(ctx, ResumeRestoreRequest{RequestID: uuid.NewString(), Verification: verify, Policy: "local-restore/1", Reason: "Resume isolated verified fixture under approved operations policy"}); err != nil {
		t.Fatal(err)
	}
	var session *string
	if err := s.pool.QueryRow(ctx, `SELECT session_id::text FROM cairn.restore_admission`).Scan(&session); err != nil || session == nil {
		t.Fatalf("fixture did not leave a completed session: %v %v", session, err)
	}
	reclaimThrough(t, s, "plan", "copy", "build", "swap", "verify")
	if state, err := s.candidateReclaimState(ctx); err != nil || state != candidateReclaimVerified {
		t.Fatalf("state %s %v", state, err)
	}
}

func TestFixtureProtectionFollowsTheOwningChannel(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(uuid.NewString())})
	if err != nil {
		t.Fatal(err)
	}
	repo := r.Scope.Repo
	compile := func(store *Store) Package {
		p, err := store.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, retentionLocal)
		if err != nil || len(p.Semantic.Selected) == 0 {
			t.Fatalf("compile: %+v %v", p, err)
		}
		return p
	}
	operatorFixture := compile(s)
	backdateReceipt(t, s, operatorFixture.ReceiptID, 40)
	// Agents' newer selecting receipts cannot displace the operator's only fixture,
	// which is the only kind verify-restore can recompile.
	agent := &Store{pool: s.pool, channel: Channel{Principal: "machine:agent", Instrumented: true}}
	agentOld := compile(agent)
	backdateReceipt(t, s, agentOld.ReceiptID, 39)
	for i := 0; i < 2; i++ {
		compile(agent)
	}
	result := expire(t, s, CandidateRetentionRequest{WindowDays: 14, KeepSelectedPerRepo: 2})
	if result.ExpiredReceipts != 1 || expiredAt(t, s, agentOld.ReceiptID) == nil {
		t.Fatalf("expiry: %+v", result)
	}
	if expiredAt(t, s, operatorFixture.ReceiptID) != nil {
		t.Fatal("agent receipts displaced the operator's restore fixture")
	}
	cp, err := s.Checkpoint(ctx, CheckpointRequest{RequestID: uuid.NewString(), ExportID: "synthetic-backup"})
	if err != nil {
		t.Fatal(err)
	}
	recovery, err := s.CaptureRecovery(ctx)
	if err != nil {
		t.Fatal(err)
	}
	session, err := s.BeginRestore(ctx, BeginRestoreRequest{uuid.NewString(), "synthetic-backup", "Pause fixture restore before verification"})
	if err != nil {
		t.Fatal(err)
	}
	report, err := s.VerifyRestore(ctx, VerifyRestoreRequest{SessionID: session.SessionID, Checkpoint: VerifyCheckpointRequest{CheckpointID: cp.ID, ExpectedDigest: cp.Digest, ExpectedExportID: cp.ExportID}, Recovery: recovery, Fixtures: []RecompileRequest{{ReceiptID: operatorFixture.ReceiptID}}})
	if err != nil || !report.Ready {
		t.Fatalf("restore verification lost its fixture: %+v %v", report, err)
	}
}

func TestClaimRunRefusesExpiredDetail(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	retentionNote(t, s, repo, "omicron fixture_error")
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "omicron", Purpose: "context", AvailableTokens: 32000}, retentionLocal)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET candidate_detail_expired_at=now() WHERE receipt_id=$1`, p.ReceiptID); err != nil {
		t.Fatal(err)
	}
	requireCode(t, s.ClaimRun(ctx, p.ReceiptID), "CANDIDATE_DETAIL_EXPIRED")
	var claimed bool
	if err = s.pool.QueryRow(ctx, `SELECT launch_claimed FROM cairn.retrieval_receipt WHERE receipt_id=$1`, p.ReceiptID).Scan(&claimed); err != nil || claimed {
		t.Fatalf("an expired receipt became run-bound: %v %v", claimed, err)
	}
}

func TestReclaimAbortRestoresReceiptsAndPreflightRefuses(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	retentionNote(t, s, repo, "nu fixture_error")
	id := retentionReceipt(t, s, retentionIndex(t, s, repo, "nu"))
	backdateReceipt(t, s, id, 30)
	reclaimThrough(t, s, "plan", "copy", "build")
	if expiredAt(t, s, id) == nil {
		t.Fatal("copy did not mark the planned receipt")
	}
	// An ordinary read of a referenced table delays the drop but cannot wedge it.
	holder, err := s.beginLevel(ctx, "repeatable read")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = holder.Rollback(context.Background()) })
	var versions int
	if err = holder.QueryRow(ctx, `SELECT count(*) FROM cairn.record_version`).Scan(&versions); err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	_, err = s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "abort", LockTimeoutMS: 50, MaxRuntimeSeconds: 1, Limits: retentionTestLimits})
	requireCode(t, err, "MAINTENANCE_ACTIVE")
	if elapsed := time.Since(started); elapsed > 15*time.Second {
		t.Fatalf("abort held on for %v", elapsed)
	}
	if err = holder.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	// A half-aborted rewrite cannot be continued, and its receipts are already whole.
	if state, err := s.candidateReclaimState(ctx); err != nil || state != candidateReclaimAborting {
		t.Fatalf("state %s %v", state, err)
	}
	if expiredAt(t, s, id) != nil {
		t.Fatal("abort left the receipt marked")
	}
	_, err = s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "copy", Limits: retentionTestLimits})
	requireCode(t, err, "DEPENDENCY_CONFLICT")
	if aborted := reclaim(t, s, CandidateReclaimRequest{Phase: "abort"}); aborted.State != candidateReclaimNone {
		t.Fatalf("abort: %+v", aborted)
	}
	if expiredAt(t, s, id) != nil || candidateRows(t, s, id) != 1 {
		t.Fatal("aborting a rewrite changed live data")
	}
	if _, err := s.Explain(ctx, id); err != nil {
		t.Fatal(err)
	}
	// Anything a swap would silently lose stops the plan.
	const owner = `cairn_retention_other_owner`
	for name, attach := range map[string]struct{ add, drop string }{
		"view":    {`CREATE VIEW cairn.test_candidate_view AS SELECT receipt_id FROM cairn.retrieval_candidate`, `DROP VIEW cairn.test_candidate_view`},
		"index":   {`CREATE INDEX test_candidate_extra ON cairn.retrieval_candidate(record_id)`, `DROP INDEX cairn.test_candidate_extra`},
		"grant":   {`GRANT SELECT ON cairn.retrieval_candidate TO PUBLIC`, `REVOKE SELECT ON cairn.retrieval_candidate FROM PUBLIC`},
		"check":   {`ALTER TABLE cairn.retrieval_candidate ADD CONSTRAINT test_candidate_check CHECK(version>0)`, `ALTER TABLE cairn.retrieval_candidate DROP CONSTRAINT test_candidate_check`},
		"trigger": {`CREATE FUNCTION cairn.test_candidate_noop() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$; CREATE TRIGGER test_candidate_noop BEFORE INSERT ON cairn.retrieval_candidate FOR EACH ROW EXECUTE FUNCTION cairn.test_candidate_noop()`, `DROP TRIGGER test_candidate_noop ON cairn.retrieval_candidate; DROP FUNCTION cairn.test_candidate_noop()`},
		"owner":   {`CREATE ROLE ` + owner + `; GRANT USAGE ON SCHEMA cairn TO ` + owner + `; ALTER TABLE cairn.retrieval_candidate OWNER TO ` + owner, `ALTER TABLE cairn.retrieval_candidate OWNER TO CURRENT_USER; REVOKE USAGE ON SCHEMA cairn FROM ` + owner + `; DROP ROLE ` + owner},
	} {
		if _, err := s.pool.Exec(ctx, attach.add); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		_, err := s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "plan", WindowDays: 14, Limits: retentionTestLimits})
		if Code(err) != "DEPENDENCY_CONFLICT" {
			t.Errorf("%s: plan was not refused: %v", name, err)
		}
		// The same attachment arriving after planning stops the swap with nothing changed.
		if _, err = s.pool.Exec(ctx, attach.drop); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		reclaimThrough(t, s, "plan", "copy", "build")
		if _, err = s.pool.Exec(ctx, attach.add); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		_, err = s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "swap", Limits: retentionTestLimits})
		if Code(err) != "DEPENDENCY_CONFLICT" {
			t.Errorf("%s: swap was not refused: %v", name, err)
		}
		if state, serr := s.candidateReclaimState(ctx); serr != nil || state != candidateReclaimBuilt {
			t.Errorf("%s: state %s %v", name, state, serr)
		}
		if _, err = s.pool.Exec(ctx, attach.drop); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		reclaim(t, s, CandidateReclaimRequest{Phase: "abort"})
	}
	_, err = s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "bogus", Limits: retentionTestLimits})
	requireCode(t, err, "INVALID_REQUEST")
}

func TestVerifyDetectsLostRetainedRowsAndKeepsTheOldRelation(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	for i := 0; i < 5; i++ {
		retentionNote(t, s, repo, fmt.Sprintf("pi fixture_error %d", i))
	}
	gated, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "pi", Purpose: "planning", AvailableTokens: 64000}, retentionLocal)
	if err != nil {
		t.Fatal(err)
	}
	backdateReceipt(t, s, gated.ReceiptID, 40)
	retained := retentionReceipt(t, s, retentionIndex(t, s, repo, "pi"))
	reclaimThrough(t, s, "plan", "copy", "build", "swap")
	check := func(want string) {
		t.Helper()
		_, err := s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "verify", Limits: retentionTestLimits})
		if want == "" && err != nil {
			t.Fatalf("verify: %v", err)
		}
		if want != "" {
			requireCode(t, err, want)
			if state, serr := s.candidateReclaimState(ctx); serr != nil || state != candidateReclaimSwapped {
				t.Fatalf("failed verify changed state: %s %v", state, serr)
			}
			_, err = s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "finish", ConfirmDropOld: true, Limits: retentionTestLimits})
			requireCode(t, err, "DEPENDENCY_CONFLICT")
		}
	}
	stash := func(id string) func() {
		if _, err := s.pool.Exec(ctx, `CREATE TABLE public.test_stash AS SELECT * FROM cairn.retrieval_candidate WHERE receipt_id=$1`, id); err != nil {
			t.Fatal(err)
		}
		if _, err := s.pool.Exec(ctx, `DELETE FROM cairn.retrieval_candidate WHERE receipt_id=$1`, id); err != nil {
			t.Fatal(err)
		}
		return func() {
			if _, err := s.pool.Exec(ctx, `INSERT INTO cairn.retrieval_candidate SELECT * FROM public.test_stash; DROP TABLE public.test_stash`); err != nil {
				t.Fatal(err)
			}
		}
	}
	restore := stash(gated.ReceiptID)
	check("INTEGRITY_FAILURE")
	restore()
	restore = stash(retained)
	check("INTEGRITY_FAILURE")
	restore()
	// A changed byte with the right count is caught by the sampled digest.
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_candidate SET detail=detail||'{"rank":99}' WHERE receipt_id=$1`, gated.ReceiptID); err != nil {
		t.Fatal(err)
	}
	check("INTEGRITY_FAILURE")
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_candidate c SET detail=o.detail FROM `+candidateReclaimOldTable+` o WHERE c.receipt_id=$1 AND o.receipt_id=c.receipt_id AND o.record_id=c.record_id`, gated.ReceiptID); err != nil {
		t.Fatal(err)
	}
	check("")
}

// Sustained overlap of short transactions can keep a row lock's holders changing,
// so no single wait reaches lock_timeout. The attempt must still end on time, and
// ordinary work must keep flowing while it waits.
func TestSwapWaitIsBoundedUnderSustainedTraffic(t *testing.T) {
	ctx := context.Background()
	s := wideStore(t, retentionStore(t), 40)
	repo := uuid.NewString()
	retentionNote(t, s, repo, "rho fixture_error")
	id := retentionReceipt(t, s, retentionIndex(t, s, repo, "rho"))
	backdateReceipt(t, s, id, 40)
	reclaimThrough(t, s, "plan", "copy", "build")
	var stop atomic.Bool
	var completed atomic.Int64
	var traffic sync.WaitGroup
	t.Cleanup(func() {
		stop.Store(true)
		traffic.Wait()
	})
	for i := 0; i < 4; i++ {
		traffic.Add(1)
		go func(i int) {
			defer traffic.Done()
			time.Sleep(time.Duration(i) * 12 * time.Millisecond)
			for !stop.Load() {
				tx, err := s.begin(ctx)
				if err != nil {
					t.Error(err)
					return
				}
				time.Sleep(50 * time.Millisecond)
				_ = tx.Rollback(ctx)
				completed.Add(1)
			}
		}(i)
	}
	started := time.Now()
	result := reclaim(t, s, CandidateReclaimRequest{Phase: "swap", LockTimeoutMS: 200, MaxRuntimeSeconds: 2})
	elapsed := time.Since(started)
	stop.Store(true)
	traffic.Wait()
	if elapsed > 20*time.Second {
		t.Fatalf("swap waited %v past its runtime limit", elapsed)
	}
	if completed.Load() < 40 {
		t.Fatalf("ordinary transactions stalled while the swap waited: %d completed", completed.Load())
	}
	t.Logf("traffic: swap stopped=%s state=%s after %v, %d transactions completed", result.StoppedBy, result.State, elapsed, completed.Load())
	if result.State == candidateReclaimBuilt && result.StoppedBy != "swap_lock_unavailable" {
		t.Fatalf("unsuccessful swap reported %+v", result)
	}
	if result.State == candidateReclaimBuilt {
		if finished := reclaim(t, s, CandidateReclaimRequest{Phase: "swap"}); finished.State != candidateReclaimSwapped {
			t.Fatalf("swap once traffic stopped: %+v", finished)
		}
	}
}

// Hold time must not depend on how many receipts expire, and failed attempts must
// not rewrite the receipts table.
func TestSwapHoldDoesNotScaleWithExpiringReceipts(t *testing.T) {
	ctx := context.Background()
	s := wideStore(t, retentionStore(t), 40)
	repo := uuid.NewString()
	retentionNote(t, s, repo, "sigma fixture_error")
	seed := retentionReceipt(t, s, retentionIndex(t, s, repo, "sigma"))
	if _, err := s.pool.Exec(ctx, `INSERT INTO cairn.retrieval_receipt(receipt_id,caller,request_id,request_digest,scope,purpose,destination,semantic_body,seal,status,nonce,launch_claimed,snapshot,created_at,explanation_version,generation)
 SELECT gen_random_uuid(),caller,gen_random_uuid(),request_digest,scope,purpose,destination,semantic_body,seal,status,gen_random_uuid(),false,snapshot,now()-interval '40 days',explanation_version,generation
 FROM cairn.retrieval_receipt,generate_series(1,3000) WHERE receipt_id=$1`, seed); err != nil {
		t.Fatal(err)
	}
	reclaimThrough(t, s, "plan", "copy", "build")
	size := func() (n int64) {
		if err := s.pool.QueryRow(ctx, `SELECT pg_total_relation_size('cairn.retrieval_receipt')`).Scan(&n); err != nil {
			t.Fatal(err)
		}
		return n
	}
	before := size()
	holder, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = holder.Rollback(context.Background()) })
	if _, err = holder.Exec(ctx, `LOCK TABLE cairn.retrieval_candidate IN ACCESS SHARE MODE`); err != nil {
		t.Fatal(err)
	}
	blocked := reclaim(t, s, CandidateReclaimRequest{Phase: "swap", LockTimeoutMS: 100, MaxRuntimeSeconds: 30})
	if blocked.StoppedBy != "swap_lock_unavailable" || blocked.SwapAttempts != candidateReclaimMaxStalls {
		t.Fatalf("attempts holding the drain were not limited: %+v", blocked)
	}
	if err = holder.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	if after := size(); after > before+before/10 {
		t.Fatalf("failed swaps grew retrieval_receipt from %d to %d bytes", before, after)
	}
	swapped := reclaim(t, s, CandidateReclaimRequest{Phase: "swap"})
	if swapped.State != candidateReclaimSwapped || swapped.MarkedReceipts != 3000 || swapped.SwapHoldMS > 1000 {
		t.Fatalf("swap: %+v", swapped)
	}
}

func TestSwapRefusesAnOversizedTail(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	retentionNote(t, s, repo, "tau fixture_error")
	id := retentionReceipt(t, s, retentionIndex(t, s, repo, "tau"))
	backdateReceipt(t, s, id, 40)
	reclaimThrough(t, s, "plan", "copy", "build")
	signature := candidateTableSignature(t, s)
	for i := 0; i <= candidateReclaimMaxTail; i++ {
		retentionIndex(t, s, repo, "tau")
	}
	var result CandidateReclaimResult
	_, err := s.reclaimSwapOnce(ctx, CandidateReclaimRequest{LockTimeoutMS: 3000}, &result)
	requireCode(t, err, "STALE_CURSOR")
	if state, serr := s.candidateReclaimState(ctx); serr != nil || state != candidateReclaimBuilt || candidateTableSignature(t, s) != signature {
		t.Fatalf("refused swap changed state: %s %v", state, serr)
	}
	// The ordinary phase catches up outside the drain and succeeds.
	if swapped := reclaim(t, s, CandidateReclaimRequest{Phase: "swap"}); swapped.State != candidateReclaimSwapped || swapped.TailReceipts != 0 {
		t.Fatalf("swap: %+v", swapped)
	}
}

func TestBuildRespectsLimitsAndCopyPrecondition(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	retentionNote(t, s, repo, "upsilon fixture_error")
	id := retentionReceipt(t, s, retentionIndex(t, s, repo, "upsilon"))
	backdateReceipt(t, s, id, 40)
	reclaim(t, s, CandidateReclaimRequest{Phase: "plan", WindowDays: 14})
	_, err := s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "build", Limits: retentionTestLimits})
	requireCode(t, err, "DEPENDENCY_CONFLICT")
	reclaim(t, s, CandidateReclaimRequest{Phase: "copy"})
	stopped := reclaim(t, s, CandidateReclaimRequest{Phase: "build", Limits: CandidateMaintenanceLimits{MinFreeBytes: 1 << 62}})
	if stopped.StoppedBy != "min_free_bytes" || stopped.State != candidateReclaimPlanned {
		t.Fatalf("build under impossible limits: %+v", stopped)
	}
	if built := reclaim(t, s, CandidateReclaimRequest{Phase: "build"}); built.State != candidateReclaimBuilt {
		t.Fatalf("build: %+v", built)
	}
}

// Prepared statements on long-lived connections must follow the rename.
func TestCachedStatementsSurviveTheSwap(t *testing.T) {
	ctx := context.Background()
	s := wideStore(t, retentionStore(t), 8)
	repo := uuid.NewString()
	for i := 0; i < 5; i++ {
		retentionNote(t, s, repo, fmt.Sprintf("phi fixture_error %d", i))
	}
	keep := retentionReceipt(t, s, retentionIndex(t, s, repo, "phi"))
	reclaimThrough(t, s, "plan", "copy", "build")
	const query = `SELECT count(*) FROM cairn.retrieval_candidate WHERE receipt_id=$1`
	conn, err := s.pool.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(conn.Release)
	// Past the custom-plan threshold, so a generic plan is cached on this connection.
	for i := 0; i < 8; i++ {
		var n int
		if err = conn.QueryRow(ctx, query, keep).Scan(&n); err != nil || n != 5 {
			t.Fatalf("before: %d %v", n, err)
		}
	}
	reclaim(t, s, CandidateReclaimRequest{Phase: "swap"})
	var n, oid, liveOID int
	if err = conn.QueryRow(ctx, query, keep).Scan(&n); err != nil || n != 5 {
		t.Fatalf("after: %d %v", n, err)
	}
	if err = conn.QueryRow(ctx, `SELECT 'cairn.retrieval_candidate'::regclass::oid::int,'`+candidateReclaimOldTable+`'::regclass::oid::int`).Scan(&liveOID, &oid); err != nil || liveOID == oid {
		t.Fatalf("relations: %d %d %v", liveOID, oid, err)
	}
	if _, err = s.Recompile(ctx, RecompileRequest{ReceiptID: keep, Query: "phi"}); err != nil {
		t.Fatal(err)
	}
}

// A failure at any DDL statement inside the swap or build must leave the rewrite
// repeatable and every reader correct.
func TestRewriteSurvivesAFaultAtEveryDDLStatement(t *testing.T) {
	ctx := context.Background()
	s := retentionStore(t)
	repo := uuid.NewString()
	for i := 0; i < 5; i++ {
		retentionNote(t, s, repo, fmt.Sprintf("chi fixture_error %d", i))
	}
	old := retentionReceipt(t, s, retentionIndex(t, s, repo, "chi"))
	backdateReceipt(t, s, old, 40)
	gated, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "chi", Purpose: "planning", AvailableTokens: 64000}, retentionLocal)
	if err != nil {
		t.Fatal(err)
	}
	backdateReceipt(t, s, gated.ReceiptID, 40)
	keep := retentionReceipt(t, s, retentionIndex(t, s, repo, "chi"))
	signature := candidateTableSignature(t, s)
	if _, err = s.pool.Exec(ctx, `CREATE SEQUENCE public.test_fault_seq;
 CREATE TABLE public.test_fault_at(n bigint);
 CREATE FUNCTION public.test_fault() RETURNS event_trigger LANGUAGE plpgsql AS $$
 DECLARE n bigint; target bigint; BEGIN
  SELECT test_fault_at.n INTO target FROM public.test_fault_at;
  IF target IS NULL THEN RETURN; END IF;
  n := nextval('public.test_fault_seq');
  IF n = target THEN RAISE EXCEPTION 'synthetic fault at DDL %: %', n, tg_tag; END IF;
 END $$;
 CREATE EVENT TRIGGER test_fault ON ddl_command_start EXECUTE FUNCTION public.test_fault();
 INSERT INTO public.test_fault_at VALUES(NULL)`); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = s.pool.Exec(context.Background(), `DROP EVENT TRIGGER IF EXISTS test_fault`) })
	arm := func(n int) {
		if _, err := s.pool.Exec(ctx, `SELECT setval('public.test_fault_seq',1,false)`); err != nil {
			t.Fatal(err)
		}
		if _, err := s.pool.Exec(ctx, `UPDATE public.test_fault_at SET n=$1`, n); err != nil {
			t.Fatal(err)
		}
	}
	readersCorrect := func(stage string) {
		t.Helper()
		_, err := s.Explain(ctx, old)
		requireCode(t, err, "CANDIDATE_DETAIL_EXPIRED")
		if explanation, err := s.Explain(ctx, keep); err != nil || len(explanation.Candidates) != 5 {
			t.Fatalf("%s: retained receipt: %+v %v", stage, explanation, err)
		}
	}
	reclaimThrough(t, s, "plan", "copy")
	for _, phase := range []string{"build", "swap"} {
		faults := 0
		for n := 1; n < 40; n++ {
			arm(n)
			_, err := s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: phase, Limits: retentionTestLimits})
			if _, disarm := s.pool.Exec(ctx, `UPDATE public.test_fault_at SET n=NULL`); disarm != nil {
				t.Fatal(disarm)
			}
			if err == nil {
				break
			}
			faults++
			want := candidateReclaimPlanned
			if phase == "swap" {
				want = candidateReclaimBuilt
				if now := candidateTableSignature(t, s); now != signature {
					t.Fatalf("fault %d changed the live table:\n%s", n, now)
				}
			}
			if state, serr := s.candidateReclaimState(ctx); serr != nil || state != want {
				t.Fatalf("%s fault %d (%v): state %s %v", phase, n, err, state, serr)
			}
			readersCorrect(fmt.Sprintf("%s fault %d", phase, n))
			// New work between interruptions must still be picked up without duplicates.
			retentionIndex(t, s, repo, "chi")
			if phase == "build" {
				if result := reclaim(t, s, CandidateReclaimRequest{Phase: "copy"}); result.StoppedBy != "complete" {
					t.Fatalf("copy between faults: %+v", result)
				}
			}
		}
		if faults < 3 {
			t.Fatalf("%s: only %d faults were injected", phase, faults)
		}
	}
	if _, err = s.pool.Exec(ctx, `DROP EVENT TRIGGER test_fault`); err != nil {
		t.Fatal(err)
	}
	if after := candidateTableSignature(t, s); after != signature {
		t.Fatalf("final definition differs:\n%s\n%s", signature, after)
	}
	if verified := reclaim(t, s, CandidateReclaimRequest{Phase: "verify"}); verified.State != candidateReclaimVerified {
		t.Fatalf("verify after faults: %+v", verified)
	}
}

// A killed swap backend rolls back; the operator repeats the phase.
func TestSwapRecoversFromAKilledBackend(t *testing.T) {
	ctx := context.Background()
	s := wideStore(t, retentionStore(t), 40)
	repo := uuid.NewString()
	retentionNote(t, s, repo, "psi fixture_error")
	old := retentionReceipt(t, s, retentionIndex(t, s, repo, "psi"))
	backdateReceipt(t, s, old, 40)
	keep := retentionReceipt(t, s, retentionIndex(t, s, repo, "psi"))
	reclaimThrough(t, s, "plan", "copy", "build")
	signature := candidateTableSignature(t, s)
	holder, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = holder.Rollback(context.Background()) })
	if _, err = holder.Exec(ctx, `LOCK TABLE cairn.retrieval_candidate IN ACCESS SHARE MODE`); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		_, err := s.ReclaimCandidateStorage(ctx, CandidateReclaimRequest{Phase: "swap", LockTimeoutMS: 60000, Limits: retentionTestLimits})
		done <- err
	}()
	waitForBackends(t, s, `LOCK TABLE cairn.retrieval_candidate%`, 1)
	if _, err = s.pool.Exec(ctx, `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=current_database() AND query LIKE 'LOCK TABLE cairn.retrieval_candidate%' AND wait_event_type='Lock'`); err != nil {
		t.Fatal(err)
	}
	if err = <-done; err == nil {
		t.Fatal("killed swap reported success")
	}
	if err = holder.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	if state, serr := s.candidateReclaimState(ctx); serr != nil || state != candidateReclaimBuilt || candidateTableSignature(t, s) != signature {
		t.Fatalf("killed swap left state %s %v", state, serr)
	}
	if explanation, err := s.Explain(ctx, keep); err != nil || len(explanation.Candidates) != 1 {
		t.Fatalf("retained receipt after a killed swap: %+v %v", explanation, err)
	}
	if swapped := reclaim(t, s, CandidateReclaimRequest{Phase: "swap"}); swapped.State != candidateReclaimSwapped {
		t.Fatalf("retry after a killed swap: %+v", swapped)
	}
}
