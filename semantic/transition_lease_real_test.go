package semantic

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestSemanticRealTransitionCase6(t *testing.T) {
	if os.Getenv("CAIRN_TRANSITION_REAL_CASE") != "6" {
		t.Skip("requires explicit --real-case6 retained selections")
	}
	transitionLeaseBacklog(t)
}

func transitionRealLeaseBacklog(t *testing.T) {
	var selected transitionRealSelection
	data, err := os.ReadFile(os.Getenv("CAIRN_TRANSITION_REAL_SELECTION"))
	if err != nil || json.Unmarshal(data, &selected) != nil || selected.SamplePIDs == "" {
		t.Fatal("real leases require exact selections and runtime receipt")
	}
	cases := []map[string]any{}
	for _, entry := range []struct {
		label  string
		worker transitionRealWorker
	}{{"python", selected.Python}, {"rust", selected.Rust}} {
		if !t.Run(entry.label, func(t *testing.T) {
			cases = append(cases, transitionRealLeaseOne(t, selected, entry.label, entry.worker))
		}) {
			return
		}
	}
	transitionRealFile(t, "real-case6.json", map[string]any{"schema": "cairn.semantic-real-transition-case6/1", "cases": cases,
		"scope": "two local lease/backoff subcases; four other real cases and full S05 remain open"})
}

func transitionRealLeaseOne(t *testing.T, selected transitionRealSelection, label string, w transitionRealWorker) map[string]any {
	report := map[string]any{"implementation": label, "embed_identity": w.EmbedIdentity, "rank_identity": w.RankIdentity,
		"barrier_scope":     "unchanged fixture adapter holds actual completed CommandEmbedder results before Index receives them; no inference-interruption claim",
		"failure_scope":     "verified warmed document PID killed after actual Step lease, while source read waits on a private table lock; actual exchange then fails with a live caller",
		"lease_owner_scope": "semantic_job stores a lease UUID, not an authenticated process owner; fixture receipts associate each observed UUID with its exact joined Go Step/Index and canonical worker PID"}
	t.Cleanup(func() { report["test_failed"] = t.Failed(); transitionRealFile(t, "real-case6-"+label+".json", report) })
	d := transitionDatabase(t)
	d.exec(t, `ALTER TABLE cairn.s05_phase ADD COLUMN actor text NOT NULL DEFAULT 'product', ADD COLUMN label text NOT NULL DEFAULT 'initial failure';
 CREATE TABLE cairn.s05_tuple_publication (phase integer,record_id uuid,version integer,body_sha256 text,model text);
 CREATE FUNCTION cairn.s05_observe_tuple() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN INSERT INTO cairn.s05_tuple_publication SELECT phase,NEW.record_id,NEW.version,NEW.body_sha256,NEW.model_sha256 FROM cairn.s05_phase; RETURN NEW; END $$;
 CREATE TRIGGER s05_observe_tuple AFTER INSERT ON cairn.semantic_document FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_tuple();
 CREATE TABLE cairn.s05_job_audit (sequence bigserial PRIMARY KEY,phase integer,actor text,label text,operation text,record_id uuid,old_row jsonb,new_row jsonb);
 CREATE FUNCTION cairn.s05_observe_job() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN INSERT INTO cairn.s05_job_audit(phase,actor,label,operation,record_id,old_row,new_row)
 SELECT phase,actor,label,TG_OP,COALESCE(NEW.record_id,OLD.record_id),to_jsonb(OLD),to_jsonb(NEW) FROM cairn.s05_phase; RETURN NULL; END $$;
 CREATE TRIGGER s05_observe_job AFTER INSERT OR UPDATE OR DELETE ON cairn.semantic_job FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_job();`)
	t.Cleanup(func() {
		report["job_audit"] = transitionEditAudit(t, d, `SELECT to_jsonb(a)::text FROM cairn.s05_job_audit a ORDER BY sequence`)
		report["publication_audit"] = transitionEditAudit(t, d, `SELECT to_jsonb(a)::text FROM cairn.s05_tuple_publication a ORDER BY record_id`)
	})
	body := "ordinary unchanged lease target é"
	target := d.source(t, "s05-a", body)
	notes := []core.SemanticNote{target, d.source(t, "s05-b", "ordinary lease control é collection b"), d.source(t, "s05-c", "ordinary lease control é collection c")}
	before, history := d.realPopulation(t), transitionEditHistory(t, d)
	initial := d.restartJobs(t)
	if len(before) != 3 || len(history) != 3 || len(initial) != 3 {
		t.Fatal("wrong three-collection population")
	}
	first := transitionFailureJob(t, initial, target.RecordID)
	for _, job := range initial {
		if job.Attempts != 0 || job.LeaseID != nil || job.Until != nil || job.RetryAt.Before(first.RetryAt) || (job.RetryAt.Equal(first.RetryAt) && job.RecordID < first.RecordID) {
			t.Fatal("target is not the first ordinary due job")
		}
	}
	report["source_population_before"], report["complete_history_before"], report["jobs_before_start"] = before, history, initial
	snapshots := []map[string]any{}
	snapshot := func(name string, complete int) {
		t.Helper()
		population, currentHistory := d.realPopulation(t), transitionEditHistory(t, d)
		if !reflect.DeepEqual(before, population) || !reflect.DeepEqual(history, currentHistory) {
			t.Fatal("lease observation changed source version/body/history/population")
		}
		coverage := transitionRequireCoverage(t, d, w.EmbedIdentity, 3, complete)
		snapshots = append(snapshots, map[string]any{"phase": name, "observed_at": time.Now(), "sources": population, "complete_history": currentHistory,
			"jobs": d.restartJobs(t), "coverage": coverage, "publication_count": d.count(t, `SELECT count(*) FROM cairn.s05_publication`)})
		report["phase_snapshots"] = snapshots
	}
	phaseLabel := "initial failure"
	phase := func(number int, name string) {
		phaseLabel = name
		d.exec(t, `UPDATE cairn.s05_phase SET phase=$1,label=$2`, number, name)
	}
	fixtureWrites := []map[string]any{}
	fixture := func(name, query string, args ...any) {
		t.Helper()
		old := d.restartJobs(t)
		tx, err := d.pool.Begin(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		defer tx.Rollback(context.Background())
		if _, err = tx.Exec(context.Background(), `UPDATE cairn.s05_phase SET actor='fixture',label=$1`, name); err != nil {
			t.Fatal(err)
		}
		changed, err := tx.Exec(context.Background(), query, args...)
		if err != nil || changed.RowsAffected() != 1 {
			t.Fatalf("fixture scheduler write %s changed %d rows: %v", name, changed.RowsAffected(), err)
		}
		if _, err = tx.Exec(context.Background(), `UPDATE cairn.s05_phase SET actor='product',label=$1`, phaseLabel); err != nil {
			t.Fatal(err)
		}
		if err = tx.Commit(context.Background()); err != nil {
			t.Fatal(err)
		}
		next := d.restartJobs(t)
		if len(old) != len(next) {
			t.Fatal("fixture replaced durable queue population")
		}
		for n := range old {
			if old[n].RecordID != next[n].RecordID || old[n].Attempts != next[n].Attempts || (old[n].RecordID != target.RecordID && !reflect.DeepEqual(old[n], next[n])) {
				t.Fatal("fixture changed attempts or another job")
			}
		}
		fixtureWrites = append(fixtureWrites, map[string]any{"label": name, "SQL": query, "arguments": args, "committed_at": time.Now(), "OLD_jobs": old, "NEW_jobs": next})
		report["labelled_fixture_scheduler_writes"] = fixtureWrites
	}
	job := func() transitionRestartJob { return transitionFailureJob(t, d.restartJobs(t), target.RecordID) }
	w.identities(t, notes)
	newEmbedder := func() *CommandEmbedder {
		e, err := EmbeddingCommand(context.Background(), w.Embed)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(e.Close)
		return e
	}
	newIndex := func(e Embedder) *Index {
		i, err := OpenIndex(context.Background(), d.dsn, e)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(i.Close)
		return i
	}
	sample := func(pid int) {
		t.Helper()
		parent, state, err := transitionProcessStatus(pid)
		if pid <= 1 || err != nil || parent != os.Getpid() || state == "Z" || state == "X" {
			t.Fatal("canonical document PID is not a live owned direct child")
		}
		found := false
		for _, root := range transitionRealRoots(t, w, selected.TracePrefix) {
			found = found || root == pid
		}
		if !found {
			t.Fatal("document PID absent from canonical roots")
		}
		transitionRestartWait(t, "case6 exact document native/environment sample", 20*time.Second, func() bool {
			var sampled map[string][]int
			data, err := os.ReadFile(selected.SamplePIDs)
			if os.IsNotExist(err) {
				return false
			}
			if err != nil || json.Unmarshal(data, &sampled) != nil {
				t.Fatal("invalid exact runtime receipt")
			}
			for _, root := range sampled[label] {
				if root == pid {
					return true
				}
			}
			return false
		})
	}
	type result struct {
		worked bool
		err    error
		start  time.Time
		end    time.Time
	}
	steps := []map[string]any{}
	stepResult := func(name string, r result) {
		steps = append(steps, map[string]any{"name": name, "worked": r.worked, "error": fmt.Sprint(r.err), "started_at": r.start, "completed_at": r.end})
		report["Step_results"] = steps
	}
	run := func(i *Index, ctx context.Context) result {
		start := time.Now()
		worked, err := i.Step(ctx)
		return result{worked, err, start, time.Now()}
	}
	join := func(done chan result) result {
		t.Helper()
		select {
		case r := <-done:
			return r
		case <-time.After(60 * time.Second):
			t.Fatal("outer case6 Step join expired")
			return result{}
		}
	}
	eA := newEmbedder()
	bA := &transitionEditBarrier{CommandEmbedder: eA, oldBody: body, held: make(chan transitionEditReceipt, 1), release: make(chan struct{})}
	iA := newIndex(bA)
	ctxA, cancelA := context.WithCancel(context.Background())
	var doneA chan result
	joinedA, releasedA := true, false
	t.Cleanup(func() {
		if !releasedA {
			close(bA.release)
		}
		cancelA()
		eA.Close()
		if !joinedA {
			<-doneA
		}
	})
	if warm, err := eA.Document(ctxA, "ordinary lease lane preparation é"); err != nil || len(warm) == 0 {
		t.Fatalf("actual lane warmup: %v", err)
	}
	eA.document.mu.Lock()
	if eA.document.worker == nil {
		eA.document.mu.Unlock()
		t.Fatal("successful warmup lost document worker")
	}
	faultPID := eA.document.worker.command.Process.Pid
	eA.document.mu.Unlock()
	sample(faultPID)
	lock, err := d.pool.Begin(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = lock.Rollback(context.Background()) })
	if _, err = lock.Exec(context.Background(), `LOCK TABLE cairn.record_version IN ACCESS EXCLUSIVE MODE`); err != nil {
		t.Fatal(err)
	}
	doneA, joinedA = make(chan result, 1), false
	go func() { doneA <- run(iA, ctxA) }()
	transitionRestartWait(t, "actual leased Step blocked before source read", 10*time.Second, func() bool {
		return job().LeaseID != nil && d.count(t, `SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE 'SELECT v.version,v.body FROM cairn.memory_record%'`) == 1
	})
	failedLease := job()
	if failedLease.Until == nil || !failedLease.Until.After(time.Now()) || failedLease.Attempts != 0 {
		t.Fatal("failure missed its live ordinary lease")
	}
	report["fault_lease_owner"] = map[string]any{"Step": "A initial failure", "supervisor_pid": os.Getpid(), "document_pid": faultPID, "job": failedLease}
	tracePath := fmt.Sprintf("%s.%d", selected.TracePrefix, faultPID)
	traceBefore, err := os.ReadFile(tracePath)
	if err != nil {
		t.Fatal(err)
	}
	if err = transitionSignalOwnedWorker(faultPID, os.Getpid(), syscall.SIGKILL, func(pid int) (int, error) { p, _, err := transitionProcessStatus(pid); return p, err }, syscall.Kill); err != nil {
		t.Fatal(err)
	}
	report["fault_applied_at"] = time.Now()
	if err = lock.Rollback(context.Background()); err != nil {
		t.Fatal(err)
	}
	r := join(doneA)
	joinedA = true
	stepResult("actual worker death and persisted retry", r)
	if !r.worked || r.err == nil || ctxA.Err() != nil || errors.Is(r.err, context.DeadlineExceeded) {
		t.Fatal("worker death did not fail with a live caller")
	}
	transitionGone(t, []int{faultPID})
	eA.document.mu.Lock()
	discarded := eA.document.worker == nil
	eA.document.mu.Unlock()
	if !discarded {
		t.Fatal("failed process/reader remained cached")
	}
	failed := job()
	if failed.Attempts != 1 || failed.LeaseID != nil || failed.Until != nil || !failed.RetryAt.After(r.end) || failed.RetryAt.After(r.end.Add(6*time.Second)) {
		t.Fatal("actual live-caller death did not retain ordinary attempt 1/backoff")
	}
	for _, other := range initial {
		if other.RecordID != target.RecordID && !reflect.DeepEqual(other, transitionFailureJob(t, d.restartJobs(t), other.RecordID)) {
			t.Fatal("failure changed an unrelated job")
		}
	}
	var trace []byte
	transitionRestartWait(t, "case6 killed-worker terminal trace", 2*time.Second, func() bool {
		var err error
		trace, err = os.ReadFile(tracePath)
		if err != nil || len(trace) < len(traceBefore) {
			t.Fatalf("unreadable or truncated killed-worker trace: %v", err)
		}
		return strings.Contains(string(trace), "+++ killed by SIGKILL +++")
	})
	stdoutAfter := []string{}
	for _, line := range strings.Split(string(trace[len(traceBefore):]), "\n") {
		if strings.Contains(line, "write(1<") || strings.Contains(line, "writev(1<") {
			stdoutAfter = append(stdoutAfter, line)
		}
	}
	if len(stdoutAfter) != 0 {
		t.Fatal("failed exchange emitted stdout after the fault")
	}
	report["interruption"] = map[string]any{"document_pid": faultPID, "signal": "SIGKILL", "reaped_before_restart": true, "failed_worker_discarded": discarded,
		"naturally_persisted_retry": failed, "stdout_after_fault": stdoutAfter, "trace": tracePath, "trace_scope": "raw strace write prefixes (256 bytes), not reconstructed full stdout"}
	snapshot("actual worker failure; all three eligible", 0)
	phase(1, "future retry with eligible controls")
	fixture("extend only naturally persisted retry timestamp", `UPDATE cairn.semantic_job SET retry_at=clock_timestamp()+interval '5 minutes' WHERE record_id=$1`, target.RecordID)
	backoff := job()
	for n := 0; n < 2; n++ {
		r = run(iA, ctxA)
		stepResult("eligible control during target backoff", r)
		if !r.worked || r.err != nil || !reflect.DeepEqual(backoff, job()) {
			t.Fatal("legitimate due control failed or target backoff was selected")
		}
		snapshot(fmt.Sprintf("control %d indexed; target backoff retained", n+1), n+1)
	}
	r = run(iA, ctxA)
	stepResult("only future unleased retry remains", r)
	if r.worked || r.err != nil || !reflect.DeepEqual(backoff, job()) || backoff.LeaseID != nil || backoff.Until != nil || !backoff.RetryAt.After(r.end) {
		t.Fatal("future retry refusal was not isolated from live-lease guard")
	}
	if len(bA.calls) != 2 || bA.calls[0].WorkerPID == faultPID || bA.calls[1].WorkerPID != bA.calls[0].WorkerPID {
		t.Fatal("actual document lane did not restart once and reuse the new process")
	}
	restartedPID := bA.calls[0].WorkerPID
	sample(restartedPID)
	report["same_generation_restart"] = map[string]any{"old_pid": faultPID, "new_pid": restartedPID, "same_identity": iA.identity, "ordinary_control_calls": append([]transitionEditReceipt{}, bA.calls...)}
	phase(2, "isolated live lease with retry already due")
	constructedLease := uuid.NewString()
	fixture("construct live fixture lease and advance target retry due", `UPDATE cairn.semantic_job SET lease_id=$1,lease_until=clock_timestamp()+interval '2 minutes',retry_at=clock_timestamp() WHERE record_id=$2`, constructedLease, target.RecordID)
	live := job()
	r = run(iA, ctxA)
	stepResult("live fixture lease refused while retry due", r)
	if r.worked || r.err != nil || !reflect.DeepEqual(live, job()) || live.LeaseID == nil || *live.LeaseID != constructedLease || live.Until == nil || !live.Until.After(r.end) || live.RetryAt.After(r.start) {
		t.Fatal("live-lease refusal was not isolated from future retry")
	}
	snapshot("isolated live lease refused", 2)
	phase(3, "actual expired-lease replacement A")
	fixture("expire only constructed fixture lease timestamp", `UPDATE cairn.semantic_job SET lease_until=clock_timestamp()-interval '1 second' WHERE record_id=$1`, target.RecordID)
	expired := job()
	doneA, joinedA = make(chan result, 1), false
	go func() { doneA <- run(iA, ctxA) }()
	held := func(b *transitionEditBarrier, done chan result, joined *bool) transitionEditReceipt {
		t.Helper()
		var h transitionEditReceipt
		select {
		case h = <-b.held:
		case early := <-done:
			*joined = true
			stepResult("unexpected early result", early)
			t.Fatal("actual Step returned before held-result acknowledgement")
		case <-time.After(60 * time.Second):
			t.Fatal("outer case6 real-result barrier expired")
		}
		if h.Body != body || h.BodySHA != target.BodySHA256 || h.Identity != iA.identity || h.Identity.ModelSHA256 != w.EmbedIdentity || len(h.Passages) < 1 || len(h.Passages) > 256 {
			t.Fatal("held result changed source, model or passage count")
		}
		for _, p := range h.Passages {
			span := p.Span
			if span.Offset < 0 || span.Length <= 0 || span.Offset > len(body) || span.Length > len(body)-span.Offset || !utf8.ValidString(body[span.Offset:span.Offset+span.Length]) {
				t.Fatal("invalid actual held UTF-8 source span")
			}
			if _, err := vectorText(p.Vector); err != nil {
				t.Fatal(err)
			}
		}
		sample(h.WorkerPID)
		return h
	}
	hA := held(bA, doneA, &joinedA)
	leaseA := job()
	if leaseA.LeaseID == nil || *leaseA.LeaseID == constructedLease || leaseA.Until == nil || !leaseA.Until.After(time.Now()) || leaseA.Attempts != failed.Attempts || !leaseA.RetryAt.Equal(expired.RetryAt) || hA.WorkerPID != restartedPID {
		t.Fatal("actual Step A failed to replace expired lease while retaining attempts/retry")
	}
	report["held_A_lease_owner"] = map[string]any{"Step": "A old held result", "supervisor_pid": os.Getpid(), "receipt": hA, "expired_predecessor": expired, "actual_replacement": leaseA}
	snapshot("real result A held under actual replacement lease", 2)
	phase(4, "actual expired-lease replacement B")
	fixture("expire only actual A lease timestamp", `UPDATE cairn.semantic_job SET lease_until=clock_timestamp()-interval '1 second' WHERE record_id=$1`, target.RecordID)
	expiredA := job()
	eB := newEmbedder()
	bB := &transitionEditBarrier{CommandEmbedder: eB, oldBody: body, held: make(chan transitionEditReceipt, 1), release: make(chan struct{})}
	iB := newIndex(bB)
	if iB.identity != iA.identity {
		t.Fatal("successor changed model generation")
	}
	ctxB, cancelB := context.WithCancel(context.Background())
	doneB, joinedB := make(chan result, 1), false
	t.Cleanup(func() {
		close(bB.release)
		cancelB()
		eB.Close()
		if !joinedB {
			<-doneB
		}
	})
	go func() { doneB <- run(iB, ctxB) }()
	hB := held(bB, doneB, &joinedB)
	leaseB := job()
	if leaseB.LeaseID == nil || *leaseB.LeaseID == *leaseA.LeaseID || leaseB.Until == nil || !leaseB.Until.After(time.Now()) || leaseB.Attempts != failed.Attempts || !leaseB.RetryAt.Equal(expiredA.RetryAt) || hB.WorkerPID == hA.WorkerPID {
		t.Fatal("actual Step B failed to replace expired A with its different live lease")
	}
	report["held_B_lease_owner"] = map[string]any{"Step": "B successor held result", "supervisor_pid": os.Getpid(), "receipt": hB, "expired_predecessor": expiredA, "actual_replacement": leaseB}
	snapshot("real result B held under different live lease; identical source/model", 2)
	phase(5, "late superseded A result")
	fixture("extend only successor retry timestamp; preserve actual live B lease and attempt", `UPDATE cairn.semantic_job SET retry_at=clock_timestamp()+interval '5 minutes' WHERE record_id=$1`, target.RecordID)
	successor := job()
	auditCount := d.count(t, `SELECT count(*) FROM cairn.s05_job_audit`)
	close(bA.release)
	releasedA = true
	report["A_result_released_at"] = time.Now()
	r = join(doneA)
	joinedA = true
	stepResult("superseded real A result rejected", r)
	if !r.worked || r.err != nil || !reflect.DeepEqual(successor, job()) || successor.Until == nil || !successor.Until.After(r.end) || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit`) != auditCount || d.count(t, `SELECT count(*) FROM cairn.semantic_document WHERE record_id=$1`, target.RecordID) != 0 {
		t.Fatal("late work changed successor row or published; live current B ownership not isolated")
	}
	snapshot("late A rejected without publication or successor mutation", 2)
	phase(6, "cancel and join held successor B")
	cancelB()
	r = join(doneB)
	joinedB = true
	stepResult("cancel held B; canceled-context retry is not successful persistence", r)
	if !r.worked || !errors.Is(r.err, context.Canceled) || !reflect.DeepEqual(successor, job()) || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit`) != auditCount {
		t.Fatal("canceled held successor changed its retained row")
	}
	report["cancelled_B_retry_scope"] = "caller canceled before adapter returned; retry uses same canceled context and retains actual B lease and natural attempt 1, not a successfully persisted new failure/backoff"
	snapshot("canceled successor joined; lease and natural attempt retained", 2)
	phase(7, "isolated successor backoff without lease")
	fixture("clear only fixture successor lease to isolate future retry", `UPDATE cairn.semantic_job SET lease_id=NULL,lease_until=NULL WHERE record_id=$1`, target.RecordID)
	retained := job()
	r = run(iA, ctxA)
	stepResult("future successor retry refused with no lease", r)
	if r.worked || r.err != nil || retained.LeaseID != nil || retained.Until != nil || retained.Attempts != 1 || !retained.RetryAt.After(r.end) || !reflect.DeepEqual(retained, job()) {
		t.Fatal("retained nonzero backoff was not independently refused")
	}
	snapshot("isolated future successor retry refused", 2)
	writers := []*transitionWriter{{name: "Index A old-result and control writer", collection: "all three collections", model: w.EmbedIdentity, restartable: true},
		{name: "Index B successor writer", collection: "all three collections", model: w.EmbedIdentity, restartable: true},
		{name: "inactive alternative definition", collection: "s05-c", model: w.EmbedIdentity, restartable: true, joined: true}}
	report["writers_before_fence"] = transitionRealWriters(writers)
	if transitionFence(writers) == nil {
		t.Fatal("active/restartable writers falsely fenced")
	}
	for _, writer := range writers {
		writer.restartable = false
	}
	eA.Close()
	iA.Close()
	eB.Close()
	iB.Close()
	writers[0].joined, writers[1].joined = true, true
	transitionGone(t, transitionRealRoots(t, w, selected.TracePrefix))
	if err = transitionFence(writers); err != nil {
		t.Fatal(err)
	}
	report["writers_at_joined_fence"] = transitionRealWriters(writers)
	snapshot("all writers fenced before deliberate target due advancement", 2)
	phase(9, "normal same-generation target recovery after fence")
	fixture("advance only retained target retry due after joined fence", `UPDATE cairn.semantic_job SET retry_at=clock_timestamp() WHERE record_id=$1`, target.RecordID)
	due := job()
	if due.Attempts != retained.Attempts || due.LeaseID != nil || due.Until != nil {
		t.Fatal("due advancement erased retained attempt or manufactured a lease")
	}
	w.verify(t)
	eC := newEmbedder()
	iC := newIndex(eC)
	if iC.identity != iA.identity {
		t.Fatal("fenced recovery changed generation")
	}
	writers = append(writers, &transitionWriter{name: "same-generation recovery Index C", collection: "all three collections", model: w.EmbedIdentity, restartable: true})
	r = run(iC, context.Background())
	stepResult("normal retained target recovery after fence", r)
	if !r.worked || r.err != nil {
		t.Fatalf("normal retained target recovery: worked=%v err=%v", r.worked, r.err)
	}
	r = run(iC, context.Background())
	stepResult("no extra work after full coverage", r)
	if r.worked || r.err != nil {
		t.Fatal("extra work after complete original population")
	}
	snapshot("full original population covered; two controls earlier and retained target after fence", 3)
	if len(d.restartJobs(t)) != 0 || d.count(t, `SELECT count(*) FROM cairn.s05_publication`) != 3 || d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=1`) != 2 || d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=9 AND record_id=$1`, target.RecordID) != 1 || d.count(t, `SELECT count(*) FROM (SELECT record_id FROM cairn.s05_publication GROUP BY record_id HAVING count(*)<>1) q`) != 0 {
		t.Fatal("missing/stale/duplicate publication or hidden pending work")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_tuple_publication p LEFT JOIN cairn.memory_record m USING(record_id) LEFT JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version WHERE p.model<>$1 OR v.version IS NULL OR p.version<>v.version OR p.body_sha256<>encode(sha256(convert_to(v.body,'UTF8')),'hex')`, w.EmbedIdentity) != 0 {
		t.Fatal("published tuple changed source/model")
	}
	if len(fixtureWrites) != 7 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE actor='fixture'`) != 7 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE actor='fixture' AND (operation<>'UPDATE' OR record_id<>$1 OR old_row->>'attempts' IS DISTINCT FROM new_row->>'attempts')`, target.RecordID) != 0 {
		t.Fatal("fixture audit contains an unlabelled write, reset attempt or unrelated mutation")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE actor='product' AND operation='INSERT'`) != 3 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE operation='INSERT' AND phase<>0`) != 0 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE actor='product' AND operation='UPDATE' AND old_row->>'attempts' IS DISTINCT FROM new_row->>'attempts'`) != 1 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE phase=0 AND actor='product' AND operation='UPDATE' AND record_id=$1 AND old_row->>'attempts'='0' AND new_row->>'attempts'='1' AND new_row->>'lease_id' IS NULL`, target.RecordID) != 1 {
		t.Fatal("natural retry audit lost, invented or reset attempts/work")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE phase=9 AND actor='product' AND operation='UPDATE' AND record_id=$1 AND old_row->>'attempts'='1' AND new_row->>'attempts'='1' AND new_row->>'lease_id' IS NOT NULL`, target.RecordID) != 1 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE phase=9 AND operation='DELETE' AND record_id=$1 AND old_row->>'attempts'='1'`, target.RecordID) != 1 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit`) != 20 {
		t.Fatal("normal recovery did not consume retained attempt-1 row exactly once")
	}
	for _, writer := range writers {
		writer.restartable = false
	}
	eC.Close()
	iC.Close()
	writers[len(writers)-1].joined = true
	transitionGone(t, transitionRealRoots(t, w, selected.TracePrefix))
	if err = transitionFence(writers); err != nil {
		t.Fatal(err)
	}
	report["writers_at_final_fence"], report["source_population_after"], report["complete_history_after"] = transitionRealWriters(writers), d.realPopulation(t), transitionEditHistory(t, d)
	report["actual_A_Document_calls"], report["actual_B_Document_calls"] = bA.calls, bB.calls
	report["stdout_cache_disposition"] = "actual canonical replies decoded unchanged; A late decoded result rejected, B decoded result discarded on canceled caller, failed process/reader discarded and reaped before same-generation restart; no embedding-cache protocol; raw traces retain write prefixes only"
	report["recovery_scope"] = "two legitimate controls publish during isolated target backoff, one retained target publishes after joined fence; all original three remain eligible and exactly covered; no derived-state reset/reinsertion"
	w.verify(t)
	report["same_launcher_bytes_and_generation"], report["status"] = true, "PASS"
	return report
}
