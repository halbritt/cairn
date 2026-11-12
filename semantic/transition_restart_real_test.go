package semantic

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
)

func TestSemanticRealTransitionCase2(t *testing.T) {
	if os.Getenv("CAIRN_TRANSITION_REAL_CASE") != "2" {
		t.Skip("requires explicit --real-case2 retained selections")
	}
	transitionRestart(t)
}

// All fault signals target a verified descendant PID, never a process group.
// The injected signal function lets safety tests intercept every side effect.
func transitionPauseWorker(pid, owner int, parent func(int) (int, error), signal func(int, syscall.Signal) error) error {
	if pid <= 1 || owner <= 1 || pid == owner {
		return fmt.Errorf("unsafe worker/owner PID: %d/%d", pid, owner)
	}
	seen := map[int]bool{}
	for current := pid; current != owner; {
		if current <= 1 || seen[current] {
			return fmt.Errorf("worker %d is not owned by API %d", pid, owner)
		}
		seen[current] = true
		next, err := parent(current)
		if err != nil {
			return err
		}
		current = next
	}
	return signal(pid, syscall.SIGSTOP)
}

func TestSemanticRestartOwnedPID(t *testing.T) {
	for _, pair := range [][2]int{{0, 40}, {1, 40}, {-42, 40}, {42, 1}, {40, 40}, {43, 40}} {
		calls := 0
		err := transitionPauseWorker(pair[0], pair[1], func(int) (int, error) { return 1, nil }, func(int, syscall.Signal) error { calls++; return nil })
		if err == nil || calls != 0 {
			t.Fatalf("unsafe PID pair %v reached process control", pair)
		}
	}
	var gotPID int
	var gotSignal syscall.Signal
	if err := transitionPauseWorker(42, 40, func(pid int) (int, error) { return pid - 1, nil }, func(pid int, signal syscall.Signal) error {
		gotPID, gotSignal = pid, signal
		return nil
	}); err != nil || gotPID != 42 || gotSignal != syscall.SIGSTOP {
		t.Fatalf("owned signal: %d/%v: %v", gotPID, gotSignal, err)
	}
}

func transitionProcessStatus(pid int) (int, string, error) {
	data, err := os.ReadFile(fmt.Sprintf("/proc/%d/status", pid))
	if err != nil {
		return 0, "", err
	}
	parent, state := -1, ""
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 && fields[0] == "PPid:" {
			parent, err = strconv.Atoi(fields[1])
		}
		if len(fields) >= 2 && fields[0] == "State:" {
			state = fields[1]
		}
	}
	if err != nil || parent < 0 || state == "" {
		return 0, "", fmt.Errorf("unreadable process ownership/state for %d", pid)
	}
	return parent, state, nil
}

func transitionRealRoots(t *testing.T, w transitionRealWorker, prefix string) []int {
	t.Helper()
	paths, err := filepath.Glob(prefix + ".*")
	if err != nil {
		t.Fatal(err)
	}
	var roots []int
	for _, path := range paths {
		content, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(content), "execve("+strconv.Quote(w.Embed)+",") && !strings.Contains(string(content), "execve("+strconv.Quote(w.Rank)+",") {
			continue
		}
		pid, err := strconv.Atoi(strings.TrimPrefix(path, prefix+"."))
		if err != nil || pid <= 1 {
			t.Fatalf("invalid traced worker PID: %s", path)
		}
		roots = append(roots, pid)
	}
	sort.Ints(roots)
	return roots
}

func transitionGone(t *testing.T, pids []int) {
	t.Helper()
	if len(pids) == 0 {
		t.Fatal("empty owned-process reap proof")
	}
	for _, pid := range pids {
		if pid <= 1 {
			t.Fatal("unsafe owned PID in reap proof")
		}
		if _, err := os.Stat(fmt.Sprintf("/proc/%d", pid)); !os.IsNotExist(err) {
			t.Fatalf("owned PID %d remains after joined stop: %v", pid, err)
		}
	}
}

func transitionRestartWait(t *testing.T, what string, limit time.Duration, ready func() bool) {
	t.Helper()
	deadline := time.Now().Add(limit)
	for !ready() {
		if time.Now().After(deadline) {
			t.Fatal("outer restart observation expired: " + what)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

type transitionRestartJob struct {
	RecordID string     `json:"record_id"`
	Attempts int        `json:"attempts"`
	RetryAt  time.Time  `json:"retry_at"`
	LeaseID  *string    `json:"lease_id"`
	Until    *time.Time `json:"lease_until"`
	FullRow  string     `json:"complete_job_row"`
}

func (d *transitionDB) restartJobs(t *testing.T) []transitionRestartJob {
	t.Helper()
	rows, err := d.pool.Query(context.Background(), `SELECT record_id::text,attempts,retry_at,lease_id::text,lease_until,to_jsonb(j)::text FROM cairn.semantic_job j ORDER BY record_id`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	jobs := []transitionRestartJob{}
	for rows.Next() {
		var job transitionRestartJob
		if err := rows.Scan(&job.RecordID, &job.Attempts, &job.RetryAt, &job.LeaseID, &job.Until, &job.FullRow); err != nil {
			t.Fatal(err)
		}
		jobs = append(jobs, job)
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	return jobs
}

func transitionRealRestart(t *testing.T) {
	var selected transitionRealSelection
	data, err := os.ReadFile(os.Getenv("CAIRN_TRANSITION_REAL_SELECTION"))
	if err != nil || json.Unmarshal(data, &selected) != nil || selected.SamplePIDs == "" {
		t.Fatal("real restart requires exact selections and mapped-runtime PID receipt")
	}
	cases := []map[string]any{}
	for _, entry := range []struct {
		label  string
		worker transitionRealWorker
	}{{"python", selected.Python}, {"rust", selected.Rust}} {
		if !t.Run(entry.label, func(t *testing.T) {
			cases = append(cases, transitionRealRestartOne(t, selected, entry.label, entry.worker))
		}) {
			return
		}
	}
	if !t.Failed() {
		transitionRealFile(t, "real-case2.json", map[string]any{"schema": "cairn.semantic-real-transition-case2/1", "cases": cases,
			"scope": "two local same-generation restart subcases; other eight real cases and full S05 remain open"})
	}
}

func transitionRealRestartOne(t *testing.T, selected transitionRealSelection, label string, w transitionRealWorker) map[string]any {
	report := map[string]any{"implementation": label, "embed_identity": w.EmbedIdentity, "rank_identity": w.RankIdentity,
		"fault": "owned positive document-worker PID SIGSTOP during cold lane startup, after verified native runtime mapping under an observed lease",
		"scope": "complete test-owned writer inventory only; not installed or fleet inventory"}
	t.Cleanup(func() { report["test_failed"] = t.Failed(); transitionRealFile(t, "real-case2-"+label+".json", report) })
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
		notes = append(notes, d.source(t, repo, "database-wide source é restart in "+repo))
	}
	before := d.realPopulation(t)
	report["source_population_before"] = before
	w.identities(t, notes)
	start, probe, query, apiPID := transitionAPIProcess(t, d)
	started := time.Now()
	stop := start("real-restart-"+label+"-initial", "--embedding-command", w.Embed)
	owner := apiPID()
	definitions := []*transitionWriter{{name: "combined API", collection: "all three collections", model: w.EmbedIdentity, restartable: true, stop: stop},
		{name: "inactive alternate collection", collection: "s05-b", model: w.EmbedIdentity, restartable: true, joined: true},
		{name: "inactive restartable definition", collection: "s05-c", model: w.EmbedIdentity, restartable: true, joined: true}}
	report["writers_before_fence"] = transitionRealWriters(definitions)
	var leased transitionRestartJob
	transitionRestartWait(t, "real leased document", 60*time.Second, func() bool {
		for _, job := range d.restartJobs(t) {
			if job.LeaseID != nil && job.Until != nil {
				leased = job
				return true
			}
		}
		return false
	})
	report["observed_lease"] = leased
	var roots []int
	documentPID := 0
	transitionRestartWait(t, "verified document-lane runtime sample", 20*time.Second, func() bool {
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
		// Identity opens the query lane before OpenIndex can lease a document.
		// The second actual embed child is the document lane; no query is issued here.
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
				return true
			}
		}
		return false
	})
	if err := transitionPauseWorker(documentPID, owner, func(pid int) (int, error) { parent, _, err := transitionProcessStatus(pid); return parent, err }, syscall.Kill); err != nil {
		t.Fatal(err)
	}
	transitionRestartWait(t, "owned worker stopped", 5*time.Second, func() bool {
		_, state, err := transitionProcessStatus(documentPID)
		return err == nil && (state == "T" || state == "t")
	})
	pausedJobs := d.restartJobs(t)
	found := false
	for _, job := range pausedJobs {
		if job.RecordID == leased.RecordID && job.LeaseID != nil && *job.LeaseID == *leased.LeaseID {
			found = true
		}
	}
	if !found || d.count(t, `SELECT count(*) FROM cairn.s05_publication`) != 0 {
		t.Fatal("fault missed the observed leased document before publication")
	}
	report["fault_applied_at"] = time.Now()
	report["old_api_pid"], report["paused_document_pid"], report["old_worker_pids"] = owner, documentPID, roots
	report["jobs_while_paused"] = pausedJobs
	report["coverage_while_paused"] = transitionRequireCoverage(t, d, w.EmbedIdentity, len(before), 0)
	if transitionFence(definitions) == nil {
		t.Fatal("active/restartable inventory falsely fenced")
	}
	for _, definition := range definitions {
		definition.restartable = false
	}
	stop()
	definitions[0].joined = true
	if probe() {
		t.Fatal("old API still responds after joined stop")
	}
	transitionGone(t, append([]int{owner}, roots...))
	transitionGone(t, transitionRealRoots(t, w, selected.TracePrefix))
	if err := transitionFence(definitions); err != nil {
		t.Fatal(err)
	}
	report["writers_at_joined_fence"] = transitionRealWriters(definitions)
	report["old_api_joined_at"] = time.Now()
	stoppedJobs := d.restartJobs(t)
	if len(stoppedJobs) != len(before) || !reflect.DeepEqual(before, d.realPopulation(t)) {
		t.Fatal("joined stop lost queued work or changed operational sources")
	}
	var retained transitionRestartJob
	for _, job := range stoppedJobs {
		if job.RecordID == leased.RecordID {
			retained = job
		}
	}
	if retained.RecordID == "" || retained.Attempts < leased.Attempts {
		t.Fatal("interrupted job/retry state lost")
	}
	if retained.LeaseID != nil {
		if *retained.LeaseID != *leased.LeaseID || !retained.Until.Equal(*leased.Until) {
			t.Fatal("retained live lease changed during joined cancellation")
		}
	} else if retained.Attempts <= leased.Attempts || !retained.RetryAt.After(time.Now()) {
		t.Fatal("interruption retained neither lease nor persisted retry")
	}
	report["jobs_after_joined_stop"] = stoppedJobs
	// Explicitly expire only this owned fixture's scheduler; preserve lease IDs,
	// attempt counts and source rows. No production deadline or timer is changed.
	d.exec(t, `UPDATE cairn.s05_phase SET phase=1;
 UPDATE cairn.semantic_job SET lease_until=CASE WHEN lease_id IS NULL THEN NULL ELSE clock_timestamp()-interval '1 second' END,retry_at=clock_timestamp()`)
	due := d.restartJobs(t)
	if len(due) != len(stoppedJobs) {
		t.Fatal("private timestamp advancement changed the job population")
	}
	for n := range due {
		if due[n].RecordID != stoppedJobs[n].RecordID || due[n].Attempts != stoppedJobs[n].Attempts || !reflect.DeepEqual(due[n].LeaseID, stoppedJobs[n].LeaseID) {
			t.Fatal("private recovery altered non-timestamp job state")
		}
	}
	report["explicit_private_timestamp_advancement"] = due
	report["timing_scope"] = "local rebuild observations include harness interruption and explicit private scheduler advancement; not latency qualification"
	w.verify(t)
	restarted := time.Now()
	stopAgain := start("real-restart-"+label+"-same-generation", "--embedding-command", w.Embed)
	report["new_api_pid"] = apiPID()
	definitions[0].stop, definitions[0].joined, definitions[0].restartable = stopAgain, false, true
	expectedAttempts := map[string]int{}
	for _, job := range due {
		expectedAttempts[job.RecordID] = job.Attempts
	}
	transitionRestartWait(t, "same-generation full population", 180*time.Second, func() bool {
		coverage := d.coverage(t, w.EmbedIdentity)
		if coverage.Eligible != len(before) {
			t.Fatal("same-generation restart changed completion denominator")
		}
		for _, job := range coverage.Jobs {
			if job.Attempts > expectedAttempts[job.RecordID] {
				report["unexpected_recovery_failure"] = coverage
				t.Fatal("restarted model failed; retained failure remains in the denominator")
			}
		}
		return coverage.Complete == len(before)
	})
	report["rebuild_after_restart_ns"] = time.Since(restarted).Nanoseconds()
	report["initial_start_to_recovered_ns"] = time.Since(started).Nanoseconds()
	coverage := transitionRequireCoverage(t, d, w.EmbedIdentity, len(before), len(before))
	if len(coverage.Jobs) != 0 {
		t.Fatal("completed source audit still has retained queue work")
	}
	report["coverage_after_restart"] = coverage
	post := transitionRealQuery(t, query)
	if post["state"] != "ready" {
		t.Fatal("recovered real API query unavailable")
	}
	var recovered struct {
		Data core.IndexResult `json:"data"`
	}
	if err := json.Unmarshal(post["reply"].(json.RawMessage), &recovered); err != nil || recovered.Data.Package.Semantic.Discovery.ModelSHA256 != w.EmbedIdentity {
		t.Fatal("same-generation restart changed the actual API embedding identity")
	}
	report["post_rebuild_query"] = post
	if d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=0`) != 0 || d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=1 AND model=$1`, w.EmbedIdentity) != len(before) || d.count(t, `SELECT count(*) FROM (SELECT record_id FROM cairn.s05_publication GROUP BY record_id HAVING count(*)<>1) q`) != 0 {
		t.Fatal("stale or duplicate publication across same-generation fence")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_tuple_publication p LEFT JOIN cairn.memory_record m USING(record_id) LEFT JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
 WHERE p.model<>$1 OR v.version IS NULL OR p.version<>v.version OR p.body_sha256<>encode(sha256(convert_to(v.body,'UTF8')),'hex')`, w.EmbedIdentity) != 0 {
		t.Fatal("stale model/version/body hash published")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE phase=1 AND operation='INSERT' AND record_id=$1`, leased.RecordID) != 0 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE phase=1 AND operation='UPDATE' AND record_id=$1 AND new_row->>'lease_id' IS NOT NULL AND new_row->>'lease_id' IS DISTINCT FROM old_row->>'lease_id'`, leased.RecordID) != 1 {
		t.Fatal("restart did not reclaim the retained job with exactly one new lease")
	}
	rows, err := d.pool.Query(context.Background(), `SELECT to_jsonb(a)::text FROM cairn.s05_job_audit a ORDER BY sequence`)
	if err != nil {
		t.Fatal(err)
	}
	audit := []json.RawMessage{}
	for rows.Next() {
		var row string
		if err := rows.Scan(&row); err != nil {
			t.Fatal(err)
		}
		audit = append(audit, json.RawMessage(row))
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	report["job_lease_retry_audit"] = audit
	for _, definition := range definitions {
		definition.restartable = false
	}
	newOwner := apiPID()
	stopAgain()
	definitions[0].joined = true
	transitionGone(t, []int{newOwner})
	transitionGone(t, transitionRealRoots(t, w, selected.TracePrefix))
	if err := transitionFence(definitions); err != nil {
		t.Fatal(err)
	}
	report["writers_at_final_fence"] = transitionRealWriters(definitions)
	after := d.realPopulation(t)
	if !reflect.DeepEqual(before, after) {
		t.Fatal("same-generation restart changed full operational source rows")
	}
	report["source_population_after"] = after
	w.verify(t)
	report["same_launcher_bytes_and_generation"] = true
	report["status"] = "PASS"
	return report
}
