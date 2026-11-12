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

	"github.com/halbritt/cairn/core"
)

func TestSemanticRealTransitionCase3(t *testing.T) {
	if os.Getenv("CAIRN_TRANSITION_REAL_CASE") != "3" {
		t.Skip("requires explicit --real-case3 retained selections")
	}
	transitionDeathDeadline(t)
}

func TestSemanticFailureOwnedPID(t *testing.T) {
	for _, fault := range []syscall.Signal{syscall.SIGSTOP, syscall.SIGKILL} {
		for _, pair := range [][2]int{{0, 40}, {1, 40}, {-42, 40}, {42, 0}, {42, 1}, {42, -40}, {40, 40}, {43, 40}} {
			calls := 0
			err := transitionSignalOwnedWorker(pair[0], pair[1], fault, func(int) (int, error) { return 1, nil }, func(int, syscall.Signal) error { calls++; return nil })
			if err == nil || calls != 0 {
				t.Fatalf("unsafe PID pair %v reached process control", pair)
			}
		}
		calls := 0
		if err := transitionSignalOwnedWorker(42, 40, fault, func(pid int) (int, error) { return pid - 1, nil }, func(pid int, signal syscall.Signal) error {
			calls++
			if pid != 42 || signal != fault {
				t.Fatal("wrong owned PID/signal")
			}
			return nil
		}); err != nil || calls != 1 {
			t.Fatalf("owned worker signal: %v, calls=%d", err, calls)
		}
	}
	for _, parent := range []func(int) (int, error){
		func(int) (int, error) { return 42, nil },
		func(int) (int, error) { return 0, errors.New("missing ownership") },
	} {
		if err := transitionSignalOwnedWorker(42, 40, syscall.SIGKILL, parent, func(int, syscall.Signal) error { t.Fatal("unproven ownership reached signal"); return nil }); err == nil {
			t.Fatal("unproven ownership accepted")
		}
	}
	if err := transitionSignalOwnedWorker(42, 40, syscall.SIGTERM, func(int) (int, error) { t.Fatal("unsupported signal reached ownership lookup"); return 40, nil }, func(int, syscall.Signal) error { t.Fatal("unsupported signal reached control"); return nil }); err == nil {
		t.Fatal("unsupported fault signal accepted")
	}
}

func transitionRealDeathDeadline(t *testing.T) {
	var selected transitionRealSelection
	data, err := os.ReadFile(os.Getenv("CAIRN_TRANSITION_REAL_SELECTION"))
	if err != nil || json.Unmarshal(data, &selected) != nil || selected.SamplePIDs == "" {
		t.Fatal("real failures require exact selections and mapped-runtime PID receipt")
	}
	cases := []map[string]any{}
	for _, entry := range []struct {
		label  string
		worker transitionRealWorker
	}{{"python", selected.Python}, {"rust", selected.Rust}} {
		for _, fault := range []string{"death", "caller_deadline"} {
			name := entry.label + "_" + fault
			if !t.Run(name, func(t *testing.T) {
				cases = append(cases, transitionRealFailureOne(t, selected, entry.label, fault, entry.worker))
			}) {
				return
			}
		}
	}
	transitionRealFile(t, "real-case3.json", map[string]any{"schema": "cairn.semantic-real-transition-case3/1", "cases": cases,
		"scope": "four local death/caller-deadline subcases; seven other real cases and full S05 remain open"})
}

func transitionRealFailureOne(t *testing.T, selected transitionRealSelection, label, fault string, w transitionRealWorker) map[string]any {
	report := map[string]any{"implementation": label, "fault": fault, "embed_identity": w.EmbedIdentity, "rank_identity": w.RankIdentity,
		"fault_phase":      "cold document-lane startup after verified native runtime/environment mapping under a live lease; not an inference-frame observation",
		"lane_attribution": "L1: OpenIndex identity opens query lane first; second live embed child is inferred document lane; no query issued during Step",
		"scope":            "actual Go Index.Step/CommandEmbedder, three ordinary sources, private database and test-owned writer inventory"}
	t.Cleanup(func() {
		report["test_failed"] = t.Failed()
		transitionRealFile(t, "real-case3-"+label+"-"+fault+".json", report)
	})
	d := transitionDatabase(t)
	d.exec(t, `CREATE TABLE cairn.s05_tuple_publication (phase integer,record_id uuid,version integer,body_sha256 text,model text);
 CREATE FUNCTION cairn.s05_observe_tuple() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN INSERT INTO cairn.s05_tuple_publication SELECT phase,NEW.record_id,NEW.version,NEW.body_sha256,NEW.model_sha256 FROM cairn.s05_phase; RETURN NEW; END $$;
 CREATE TRIGGER s05_observe_tuple AFTER INSERT ON cairn.semantic_document FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_tuple();
 CREATE TABLE cairn.s05_job_audit (sequence bigserial PRIMARY KEY,phase integer,operation text,record_id uuid,old_row jsonb,new_row jsonb);
 CREATE FUNCTION cairn.s05_observe_job() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN INSERT INTO cairn.s05_job_audit(phase,operation,record_id,old_row,new_row)
 SELECT phase,TG_OP,COALESCE(NEW.record_id,OLD.record_id),to_jsonb(OLD),to_jsonb(NEW) FROM cairn.s05_phase; RETURN NULL; END $$;
 CREATE TRIGGER s05_observe_job AFTER INSERT OR UPDATE OR DELETE ON cairn.semantic_job FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_job();`)
	var notes []core.SemanticNote
	for _, repo := range []string{"s05-a", "s05-b", "s05-c"} {
		notes = append(notes, d.source(t, repo, "ordinary worker failure source é in "+repo))
	}
	before := d.realPopulation(t)
	report["source_population_before"], report["jobs_before_start"] = before, d.restartJobs(t)
	w.identities(t, notes)
	e, err := EmbeddingCommand(context.Background(), w.Embed)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(e.Close)
	i, err := OpenIndex(context.Background(), d.dsn, e)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(i.Close)
	caller, cancel := context.WithCancel(context.Background())
	if fault == "caller_deadline" {
		cancel()
		caller, cancel = context.WithTimeout(context.Background(), 30*time.Second)
	}
	type stepResult struct {
		worked bool
		err    error
	}
	done := make(chan stepResult, 1)
	joined := false
	t.Cleanup(func() {
		cancel()
		e.Close()
		if !joined {
			<-done
		}
	})
	started := time.Now()
	if deadline, ok := caller.Deadline(); ok {
		report["caller_deadline"] = deadline
	}
	go func() { worked, err := i.Step(caller); done <- stepResult{worked, err} }()
	definitions := []*transitionWriter{{name: "direct Go indexer", collection: "all three collections", model: w.EmbedIdentity, restartable: true},
		{name: "inactive alternate definition", collection: "s05-b", model: w.EmbedIdentity, restartable: true, joined: true}}
	report["writers_before_fence"] = transitionRealWriters(definitions)
	var leased transitionRestartJob
	transitionRestartWait(t, "case3 live document lease", 10*time.Second, func() bool {
		for _, job := range d.restartJobs(t) {
			if job.LeaseID != nil && job.Until != nil {
				leased = job
				return true
			}
		}
		return false
	})
	report["observed_lease"] = leased
	owner, documentPID := os.Getpid(), 0
	var roots []int
	transitionRestartWait(t, "case3 verified document runtime receipt", 20*time.Second, func() bool {
		roots = nil
		for _, pid := range transitionRealRoots(t, w, selected.TracePrefix) {
			parent, state, err := transitionProcessStatus(pid)
			if os.IsNotExist(err) {
				continue
			}
			if err != nil {
				t.Fatal(err)
			}
			if parent == owner && state != "Z" && state != "X" {
				roots = append(roots, pid)
			}
		}
		if len(roots) != 2 {
			return false
		}
		documentPID = roots[1]
		var sampled map[string][]int
		data, err := os.ReadFile(selected.SamplePIDs)
		if os.IsNotExist(err) {
			return false
		}
		if err != nil || json.Unmarshal(data, &sampled) != nil {
			t.Fatalf("invalid runtime sample receipt: %v", err)
		}
		for _, pid := range sampled[label] {
			if pid == documentPID {
				report["verified_runtime_sample_pids"] = sampled
				return true
			}
		}
		return false
	})
	jobsAtFault := d.restartJobs(t)
	retained := transitionFailureJob(t, jobsAtFault, leased.RecordID)
	if !reflect.DeepEqual(retained, leased) || d.count(t, `SELECT count(*) FROM cairn.s05_publication`) != 0 || caller.Err() != nil {
		t.Fatal("fault missed the live unpublished lease")
	}
	report["jobs_before_fault"], report["owner_pid"], report["document_pid"], report["initial_worker_pids"] = jobsAtFault, owner, documentPID, roots
	signal := syscall.SIGKILL
	if fault == "caller_deadline" {
		signal = syscall.SIGSTOP
	}
	if err := transitionSignalOwnedWorker(documentPID, owner, signal, func(pid int) (int, error) { parent, _, err := transitionProcessStatus(pid); return parent, err }, syscall.Kill); err != nil {
		t.Fatal(err)
	}
	report["fault_signal"], report["fault_applied_at"] = signal.String(), time.Now()
	if fault == "caller_deadline" {
		transitionRestartWait(t, "case3 worker stopped before caller deadline", 2*time.Second, func() bool {
			_, state, err := transitionProcessStatus(documentPID)
			return err == nil && (state == "T" || state == "t")
		})
	}
	var result stepResult
	select {
	case result = <-done:
		joined = true
	case <-time.After(40 * time.Second):
		t.Fatal("outer case3 Step join expired")
	}
	finished := time.Now()
	report["step"] = map[string]any{"worked": result.worked, "error": fmt.Sprint(result.err), "is_deadline_exceeded": errors.Is(result.err, context.DeadlineExceeded), "caller_error": fmt.Sprint(caller.Err()), "started_at": started, "completed_at": finished}
	if !result.worked || result.err == nil {
		t.Fatal("failed worker produced successful Step")
	}
	if fault == "caller_deadline" && (!errors.Is(result.err, context.DeadlineExceeded) || !errors.Is(caller.Err(), context.DeadlineExceeded)) {
		t.Fatalf("earlier caller deadline changed: %v", result.err)
	}
	if fault == "death" && (caller.Err() != nil || errors.Is(result.err, context.DeadlineExceeded)) {
		t.Fatal("death did not fail with a live caller")
	}
	transitionGone(t, []int{documentPID})
	e.document.mu.Lock()
	discarded := e.document.worker == nil
	e.document.mu.Unlock()
	if !discarded {
		t.Fatal("failed document worker remained cached in its lane")
	}
	report["document_lane_worker_discarded"] = discarded
	report["cache_disposition"] = "CommandEmbedder has no embedding cache protocol; failed process/reader discarded, no successful passages/publication; separate query lane retained until owner Close"
	tracePath := fmt.Sprintf("%s.%d", selected.TracePrefix, documentPID)
	var terminal string
	var stdout []string
	transitionRestartWait(t, "case3 terminal trace", 2*time.Second, func() bool {
		data, err := os.ReadFile(tracePath)
		if err != nil {
			t.Fatal(err)
		}
		stdout = nil
		for _, line := range strings.Split(string(data), "\n") {
			if strings.Contains(line, "write(1<") || strings.Contains(line, "writev(1<") {
				stdout = append(stdout, line)
			}
			if strings.Contains(line, "+++ killed by SIGKILL +++") {
				terminal = line
			}
		}
		return terminal != ""
	})
	report["process_exit_reap"] = map[string]any{"trace": tracePath, "terminal": terminal, "proc_absent_after_Step": true, "stdout_fd1_writes": stdout, "stdout_scope": "raw strace write prefixes (256 bytes); not a reconstruction of full stdout"}
	failedJobs := d.restartJobs(t)
	report["jobs_after_failed_Step"] = failedJobs
	coverage := transitionRequireCoverage(t, d, w.EmbedIdentity, len(before), 0)
	report["coverage_after_failure"] = coverage
	if len(failedJobs) != len(before) || d.count(t, `SELECT count(*) FROM cairn.s05_publication`) != 0 || !reflect.DeepEqual(before, d.realPopulation(t)) {
		t.Fatal("failure published or lost durable work/source population")
	}
	for n := range failedJobs {
		if failedJobs[n].RecordID != jobsAtFault[n].RecordID || (failedJobs[n].RecordID != leased.RecordID && !reflect.DeepEqual(failedJobs[n], jobsAtFault[n])) {
			t.Fatal("failed operation changed another durable job")
		}
	}
	retained = transitionFailureJob(t, failedJobs, leased.RecordID)
	if fault == "death" {
		if retained.Attempts != leased.Attempts+1 || retained.LeaseID != nil || retained.Until != nil || !retained.RetryAt.After(finished) {
			t.Fatal("death lost expected persisted attempt/backoff")
		}
	} else if retained.LeaseID != nil {
		if retained.Attempts != leased.Attempts || !reflect.DeepEqual(retained.LeaseID, leased.LeaseID) || !reflect.DeepEqual(retained.Until, leased.Until) || !retained.RetryAt.Equal(leased.RetryAt) {
			t.Fatal("expired caller changed retained lease")
		}
	} else if retained.Attempts != leased.Attempts+1 || retained.Until != nil || !retained.RetryAt.After(finished) {
		t.Fatal("expired caller retained neither original lease nor persisted retry")
	}
	if transitionFence(definitions) == nil {
		t.Fatal("active/restartable indexer falsely fenced")
	}
	for _, def := range definitions {
		def.restartable = false
	}
	e.Close()
	i.Close()
	definitions[0].joined = true
	transitionGone(t, roots)
	if err := transitionFence(definitions); err != nil {
		t.Fatal(err)
	}
	report["writers_at_joined_fence"] = transitionRealWriters(definitions)
	stopped := d.restartJobs(t)
	if !reflect.DeepEqual(failedJobs, stopped) {
		t.Fatal("joined close changed retained scheduler rows")
	}
	// Only the failed row needs scheduling advancement; retain its lease ID and
	// attempts. Other due source jobs are left byte-for-byte unchanged.
	d.exec(t, `UPDATE cairn.s05_phase SET phase=1`)
	d.exec(t, `UPDATE cairn.semantic_job SET lease_until=CASE WHEN lease_id IS NULL THEN NULL ELSE clock_timestamp()-interval '1 second' END,retry_at=clock_timestamp() WHERE record_id=$1`, leased.RecordID)
	due := d.restartJobs(t)
	if len(due) != len(stopped) {
		t.Fatal("private scheduling update changed job population")
	}
	for n := range due {
		if due[n].RecordID != stopped[n].RecordID || due[n].Attempts != stopped[n].Attempts || !reflect.DeepEqual(due[n].LeaseID, stopped[n].LeaseID) || (due[n].RecordID != leased.RecordID && !reflect.DeepEqual(due[n], stopped[n])) {
			t.Fatal("private recovery altered non-scheduling state")
		}
	}
	report["jobs_at_joined_stop"], report["explicit_private_timestamp_advancement"] = stopped, due
	w.verify(t)
	again, err := EmbeddingCommand(context.Background(), w.Embed)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(again.Close)
	recovery, err := OpenIndex(context.Background(), d.dsn, again)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(recovery.Close)
	if recovery.identity != i.identity {
		t.Fatal("recovery selected a different generation")
	}
	definitions[0].joined, definitions[0].restartable = false, true
	steps := []map[string]any{}
	for n := 0; n < len(before); n++ {
		worked, err := recovery.Step(context.Background())
		steps = append(steps, map[string]any{"worked": worked, "error": fmt.Sprint(err)})
		report["recovery_steps"] = steps
		if !worked || err != nil {
			t.Fatalf("same-generation recovery failed: worked=%v err=%v", worked, err)
		}
	}
	coverage = transitionRequireCoverage(t, d, w.EmbedIdentity, len(before), len(before))
	report["coverage_after_recovery"] = coverage
	if len(coverage.Jobs) != 0 || d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=0`) != 0 || d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=1 AND model=$1`, w.EmbedIdentity) != len(before) || d.count(t, `SELECT count(*) FROM (SELECT record_id FROM cairn.s05_publication GROUP BY record_id HAVING count(*)<>1) q`) != 0 {
		t.Fatal("missing/stale/duplicate publication after recovery")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_tuple_publication p LEFT JOIN cairn.memory_record m USING(record_id) LEFT JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version WHERE p.model<>$1 OR v.version IS NULL OR p.version<>v.version OR p.body_sha256<>encode(sha256(convert_to(v.body,'UTF8')),'hex')`, w.EmbedIdentity) != 0 {
		t.Fatal("stale model/version/body hash published")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE phase=1 AND operation='INSERT'`) != 0 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE phase=1 AND operation='UPDATE' AND record_id=$1 AND new_row->>'lease_id' IS NOT NULL AND new_row->>'lease_id' IS DISTINCT FROM old_row->>'lease_id'`, leased.RecordID) != 1 {
		t.Fatal("recovery did not reclaim retained job with exactly one new lease")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE phase=1 AND operation='UPDATE' AND new_row->>'attempts' IS DISTINCT FROM old_row->>'attempts'`) != 0 {
		t.Fatal("recovery erased or incremented retained attempts")
	}
	rows, err := d.pool.Query(context.Background(), `SELECT to_jsonb(a)::text FROM cairn.s05_job_audit a ORDER BY sequence`)
	if err != nil {
		t.Fatal(err)
	}
	audit := []json.RawMessage{}
	for rows.Next() {
		var row string
		if err := rows.Scan(&row); err != nil {
			rows.Close()
			t.Fatal(err)
		}
		audit = append(audit, json.RawMessage(row))
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	report["job_lease_retry_audit"] = audit
	for _, def := range definitions {
		def.restartable = false
	}
	again.Close()
	recovery.Close()
	definitions[0].joined = true
	transitionGone(t, transitionRealRoots(t, w, selected.TracePrefix))
	if err := transitionFence(definitions); err != nil {
		t.Fatal(err)
	}
	after := d.realPopulation(t)
	if !reflect.DeepEqual(before, after) {
		t.Fatal("recovery changed operational source rows")
	}
	report["source_population_after"], report["writers_at_final_fence"] = after, transitionRealWriters(definitions)
	report["restart_count"], report["same_launcher_bytes_and_generation"] = 1, true
	report["timing_scope"] = "ordinary-source failure/recovery observations only, not document-limit or latency qualification"
	w.verify(t)
	report["status"] = "PASS"
	return report
}

func transitionFailureJob(t *testing.T, jobs []transitionRestartJob, recordID string) transitionRestartJob {
	t.Helper()
	for _, job := range jobs {
		if job.RecordID == recordID {
			return job
		}
	}
	t.Fatalf("retained source job %s missing", recordID)
	return transitionRestartJob{}
}
