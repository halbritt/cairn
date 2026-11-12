package semantic

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

type transitionPartialCall struct {
	StartedAt      time.Time                    `json:"started_at"`
	CompletedAt    time.Time                    `json:"completed_at"`
	Elapsed        time.Duration                `json:"elapsed_ns"`
	CallerDeadline *time.Time                   `json:"caller_deadline,omitempty"`
	Request        core.SemanticRankRequest     `json:"actual_request"`
	Result         core.SemanticRetrievalResult `json:"actual_result"`
	Error          string                       `json:"actual_error"`
	Deadline       bool                         `json:"is_deadline_exceeded"`
	Canceled       bool                         `json:"is_canceled"`
}

// This observer calls the actual StartIndex retriever and returns its result
// and error untouched. It injects no responses, readiness or worker behavior.
type transitionPartialRecorder struct {
	retrieve core.SemanticRetriever
	mu       sync.Mutex
	calls    []transitionPartialCall
}

func (r *transitionPartialRecorder) Retrieve(ctx context.Context, request core.SemanticRankRequest) (core.SemanticRetrievalResult, error) {
	call := transitionPartialCall{StartedAt: time.Now(), Request: request}
	if deadline, ok := ctx.Deadline(); ok {
		call.CallerDeadline = &deadline
	}
	result, err := r.retrieve(ctx, request)
	call.CompletedAt = time.Now()
	call.Elapsed = call.CompletedAt.Sub(call.StartedAt)
	call.Result, call.Error = result, fmt.Sprint(err)
	call.Deadline, call.Canceled = errors.Is(err, context.DeadlineExceeded), errors.Is(err, context.Canceled)
	r.mu.Lock()
	r.calls = append(r.calls, call)
	r.mu.Unlock()
	return result, err
}

func (r *transitionPartialRecorder) observed() []transitionPartialCall {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]transitionPartialCall{}, r.calls...)
}

// Public compilation legitimately advances memory_record.use_generation once
// per committed record_use (schema/005_retraction_preview.sql). All other
// current-record fields and complete historical version rows must stay exact.
func transitionPartialHistoryCheck(before, after []transitionEditSource, addedUses map[string]int64) error {
	if len(before) != len(after) {
		return fmt.Errorf("historical population changed")
	}
	for n, old := range before {
		next := after[n]
		if old.RecordID != next.RecordID || old.Version != next.Version || old.BodySHA != next.BodySHA || old.VersionRow != next.VersionRow {
			return fmt.Errorf("source/version/body/complete version row changed: %s", old.RecordID)
		}
		var oldRow, newRow map[string]json.RawMessage
		if json.Unmarshal([]byte(old.RecordRow), &oldRow) != nil || json.Unmarshal([]byte(next.RecordRow), &newRow) != nil {
			return fmt.Errorf("invalid complete current-record snapshot")
		}
		var oldGeneration, newGeneration int64
		if json.Unmarshal(oldRow["use_generation"], &oldGeneration) != nil || json.Unmarshal(newRow["use_generation"], &newGeneration) != nil || addedUses[old.RecordID] < 0 || newGeneration != oldGeneration+addedUses[old.RecordID] {
			return fmt.Errorf("use generation differs from committed exposure delta: %s", old.RecordID)
		}
		newRow["use_generation"] = oldRow["use_generation"]
		if !reflect.DeepEqual(oldRow, newRow) {
			return fmt.Errorf("current-record field other than use generation changed: %s", old.RecordID)
		}
	}
	return nil
}

func TestSemanticPartialHistoryExposureGuard(t *testing.T) {
	before := transitionEditSource{RecordID: "one", Version: 1, BodySHA: transitionHash("original"),
		RecordRow:  `{"record_id":"one","current_version":1,"sensitivity":"shareable","use_generation":0}`,
		VersionRow: `{"record_id":"one","version":1,"body":"original","observed_writer":"store-owned"}`}
	allowed := before
	allowed.RecordRow = strings.Replace(before.RecordRow, `"use_generation":0`, `"use_generation":2`, 1)
	for _, change := range []struct {
		name   string
		mutate func(*transitionEditSource)
		valid  bool
	}{
		{"exact_committed_exposure_delta", func(*transitionEditSource) {}, true},
		{"unexplained_generation", func(s *transitionEditSource) {
			s.RecordRow = strings.Replace(s.RecordRow, `"use_generation":2`, `"use_generation":3`, 1)
		}, false},
		{"current_version", func(s *transitionEditSource) {
			s.RecordRow = strings.Replace(s.RecordRow, `"current_version":1`, `"current_version":2`, 1)
		}, false},
		{"body_hash", func(s *transitionEditSource) { s.BodySHA = transitionHash("changed") }, false},
		{"producer_attribution", func(s *transitionEditSource) {
			s.VersionRow = strings.Replace(s.VersionRow, "store-owned", "caller-chosen", 1)
		}, false},
		{"other_record_metadata", func(s *transitionEditSource) { s.RecordRow = strings.Replace(s.RecordRow, "shareable", "local", 1) }, false},
	} {
		t.Run(change.name, func(t *testing.T) {
			after := allowed
			change.mutate(&after)
			err := transitionPartialHistoryCheck([]transitionEditSource{before}, []transitionEditSource{after}, map[string]int64{"one": 2})
			if (err == nil) != change.valid {
				t.Fatalf("narrow exposure exception: valid=%v err=%v", change.valid, err)
			}
		})
	}
}

var transitionPartialPhases = []string{"seed_partial", "startup_unavailable", "ready_partial", "killed_query_unavailable", "cold_query_restart", "restarted_owner_warm_control", "full_recovery"}

func TestSemanticRealTransitionCase7(t *testing.T) {
	if os.Getenv("CAIRN_TRANSITION_REAL_CASE") != "7" {
		t.Skip("requires explicit --real-case7 retained selections")
	}
	transitionPartial(t)
}

func transitionRealPartial(t *testing.T) {
	var selected transitionRealSelection
	data, err := os.ReadFile(os.Getenv("CAIRN_TRANSITION_REAL_SELECTION"))
	if err != nil || json.Unmarshal(data, &selected) != nil || selected.SamplePIDs == "" {
		t.Fatal("real partial coverage requires exact selections and runtime receipt")
	}
	cases := []map[string]any{}
	for _, entry := range []struct {
		label  string
		worker transitionRealWorker
	}{{"python", selected.Python}, {"rust", selected.Rust}} {
		if !t.Run(entry.label, func(t *testing.T) {
			cases = append(cases, transitionRealPartialOne(t, selected, entry.label, entry.worker))
		}) {
			return
		}
	}
	transitionRealFile(t, "real-case7.json", map[string]any{"schema": "cairn.semantic-real-transition-case7/1", "cases": cases,
		"required_phases": transitionPartialPhases, "scope": "two local partial/unavailable/cold-restart subcases; three other real cases and full S05 remain open"})
}

type transitionPartialPipes struct {
	PID             int       `json:"worker_pid"`
	ObservedAt      time.Time `json:"observed_at"`
	Input           string    `json:"worker_stdin"`
	Output          string    `json:"worker_stdout"`
	ParentOutputFDs []string  `json:"supervisor_reader_fds"`
}

func transitionPartialPipeFDs(output string) ([]string, error) {
	entries, err := os.ReadDir("/proc/self/fd")
	if err != nil {
		return nil, err
	}
	fds := []string{}
	for _, entry := range entries {
		link, err := os.Readlink("/proc/self/fd/" + entry.Name())
		if err == nil && link == output {
			fds = append(fds, entry.Name())
		}
	}
	return fds, nil
}

func transitionPartialObservePipes(pid int) (transitionPartialPipes, error) {
	r := transitionPartialPipes{PID: pid, ObservedAt: time.Now()}
	if pid <= 1 || pid == os.Getpid() {
		return r, fmt.Errorf("unsafe worker PID %d", pid)
	}
	parent, state, err := transitionProcessStatus(pid)
	if err != nil || parent != os.Getpid() || state == "Z" || state == "X" {
		return r, fmt.Errorf("worker %d is not a live direct owned child (parent=%d state=%s): %v", pid, parent, state, err)
	}
	r.Input, err = os.Readlink(fmt.Sprintf("/proc/%d/fd/0", pid))
	if err != nil {
		return r, err
	}
	r.Output, err = os.Readlink(fmt.Sprintf("/proc/%d/fd/1", pid))
	if err != nil {
		return r, err
	}
	r.ParentOutputFDs, err = transitionPartialPipeFDs(r.Output)
	if err != nil || !strings.HasPrefix(r.Input, "pipe:[") || !strings.HasPrefix(r.Output, "pipe:[") || len(r.ParentOutputFDs) == 0 {
		return r, fmt.Errorf("owned worker stdio/reader pipes not observed: %v", err)
	}
	return r, nil
}

func transitionRealPartialOne(t *testing.T, selected transitionRealSelection, label string, w transitionRealWorker) map[string]any {
	report := map[string]any{"implementation": label, "embed_identity": w.EmbedIdentity, "rank_identity": w.RankIdentity,
		"backend_route":  "public StartIndex with unchanged canonical zero-argument launcher for each implementation; raw Rust argv/config route not substituted",
		"scope":          "global three eligible sources in two collections; query scope contains two candidates; ready partial coverage is distinct from backend unavailable",
		"fixture_writes": "no source/job/attempt/timestamp DML; private audit/phase metadata only; row locks hold pending work and a separate SHARE table lock holds startup seeding"}
	t.Cleanup(func() { report["test_failed"] = t.Failed(); transitionRealFile(t, "real-case7-"+label+".json", report) })
	d := transitionDatabase(t)
	d.exec(t, `CREATE TABLE cairn.s05_tuple_publication (phase integer,record_id uuid,version integer,body_sha256 text,model text);
 CREATE FUNCTION cairn.s05_observe_tuple() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN INSERT INTO cairn.s05_tuple_publication SELECT phase,NEW.record_id,NEW.version,NEW.body_sha256,NEW.model_sha256 FROM cairn.s05_phase; RETURN NEW; END $$;
 CREATE TRIGGER s05_observe_tuple AFTER INSERT ON cairn.semantic_document FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_tuple();
 CREATE TABLE cairn.s05_job_audit (sequence bigserial PRIMARY KEY,phase integer,actor text NOT NULL DEFAULT 'product',operation text,record_id uuid,old_row jsonb,new_row jsonb);
 CREATE FUNCTION cairn.s05_observe_job() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN INSERT INTO cairn.s05_job_audit(phase,operation,record_id,old_row,new_row)
 SELECT phase,TG_OP,COALESCE(NEW.record_id,OLD.record_id),to_jsonb(OLD),to_jsonb(NEW) FROM cairn.s05_phase; RETURN NULL; END $$;
 CREATE TRIGGER s05_observe_job AFTER INSERT OR UPDATE OR DELETE ON cairn.semantic_job FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_job();`)
	d.exec(t, `CREATE TABLE cairn.s05_source_audit (sequence bigserial PRIMARY KEY,phase integer,table_name text,operation text,record_id uuid,old_row jsonb,new_row jsonb);
 CREATE FUNCTION cairn.s05_observe_source() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN INSERT INTO cairn.s05_source_audit(phase,table_name,operation,record_id,old_row,new_row)
 SELECT phase,TG_TABLE_NAME,TG_OP,COALESCE(NEW.record_id,OLD.record_id),to_jsonb(OLD),to_jsonb(NEW) FROM cairn.s05_phase; RETURN NULL; END $$;
 CREATE TRIGGER s05_observe_record AFTER INSERT OR UPDATE OR DELETE ON cairn.memory_record FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_source();
 CREATE TRIGGER s05_observe_version AFTER INSERT OR UPDATE OR DELETE ON cairn.record_version FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_source();`)
	t.Cleanup(func() {
		report["job_audit"] = transitionEditAudit(t, d, `SELECT to_jsonb(a)::text FROM cairn.s05_job_audit a ORDER BY sequence`)
		report["publication_audit"] = transitionEditAudit(t, d, `SELECT to_jsonb(a)::text FROM cairn.s05_tuple_publication a ORDER BY record_id`)
		report["source_audit"] = transitionEditAudit(t, d, `SELECT to_jsonb(a)::text FROM cairn.s05_source_audit a ORDER BY sequence`)
		report["committed_record_uses"] = transitionEditAudit(t, d, `SELECT to_jsonb(u)::text FROM cairn.record_use u ORDER BY used_at,receipt_id,record_id`)
	})
	a := d.source(t, "s05-a", "partialanchor ordinary indexed source one é")
	b := d.source(t, "s05-a", "partialanchor ordinary waiting source two é")
	c := d.source(t, "s05-b", "partialanchor ordinary other collection source é")
	notes := []core.SemanticNote{a, b, c}
	before, history := d.realPopulation(t), transitionEditHistory(t, d)
	if len(before) != 3 || len(history) != 3 || len(d.restartJobs(t)) != 3 {
		t.Fatal("wrong declared original population")
	}
	report["source_population_before"], report["complete_history_before"], report["jobs_before_start"] = before, history, d.restartJobs(t)
	initialUses := map[string]int64{}
	for _, note := range notes {
		initialUses[note.RecordID] = int64(d.count(t, `SELECT count(*) FROM cairn.record_use WHERE record_id=$1`, note.RecordID))
	}
	report["initial_committed_use_counts"] = initialUses
	w.identities(t, notes)
	e, err := EmbeddingCommand(context.Background(), w.Embed)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(e.Close)
	seed, err := OpenIndex(context.Background(), d.dsn, e)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(seed.Close)
	writers := []*transitionWriter{{name: "direct seed Index", collection: "both collections", model: w.EmbedIdentity, restartable: true}}
	worked, err := seed.Step(context.Background())
	if !worked || err != nil || d.count(t, `SELECT count(*) FROM cairn.semantic_document WHERE record_id=$1 AND model_sha256=$2`, a.RecordID, w.EmbedIdentity) != 1 {
		t.Fatalf("ordinary first-source seed: worked=%v err=%v", worked, err)
	}
	report["seed_Step"] = map[string]any{"worked": worked, "error": fmt.Sprint(err)}
	e.Close()
	seed.Close()
	writers[0].joined, writers[0].restartable = true, false
	transitionGone(t, transitionRealRoots(t, w, selected.TracePrefix))
	if err = transitionFence(writers); err != nil {
		t.Fatal(err)
	}
	phases := []map[string]any{}
	snapshot := func(name string, complete int, details map[string]any) {
		t.Helper()
		population, fullHistory := d.realPopulation(t), transitionEditHistory(t, d)
		addedUses := map[string]int64{}
		for _, note := range notes {
			addedUses[note.RecordID] = int64(d.count(t, `SELECT count(*) FROM cairn.record_use WHERE record_id=$1`, note.RecordID)) - initialUses[note.RecordID]
		}
		details["name"], details["status"], details["observed_at"] = name, "OBSERVED", time.Now()
		details["global_coverage"], details["committed_exposure_deltas"] = d.coverage(t, w.EmbedIdentity), addedUses
		details["global_sources"], details["complete_history"], details["jobs"] = population, fullHistory, d.restartJobs(t)
		details["publication_count"] = d.count(t, `SELECT count(*) FROM cairn.s05_publication`)
		phases = append(phases, details)
		report["phases"] = phases
		if !reflect.DeepEqual(before, population) {
			t.Fatal("partial fixture changed eligible source/version/body/complete version rows")
		}
		if err := transitionPartialHistoryCheck(history, fullHistory, addedUses); err != nil {
			t.Fatal(err)
		}
		transitionRequireCoverage(t, d, w.EmbedIdentity, 3, complete)
		details["status"] = "PASS"
	}
	snapshot("seed_partial", 1, map[string]any{"writers_at_seed_fence": transitionRealWriters(writers)})
	pending := d.restartJobs(t)
	if len(pending) != 2 {
		t.Fatal("seed lost the unindexed denominator")
	}
	lockEvents := []map[string]any{}
	pendingLock, err := d.pool.Begin(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = pendingLock.Rollback(context.Background()) })
	rows, err := pendingLock.Query(context.Background(), `SELECT record_id::text FROM cairn.semantic_job ORDER BY record_id FOR UPDATE`)
	if err != nil {
		t.Fatal(err)
	}
	lockedIDs := []string{}
	for rows.Next() {
		var id string
		if err = rows.Scan(&id); err != nil {
			t.Fatal(err)
		}
		lockedIDs = append(lockedIDs, id)
	}
	rows.Close()
	if err = rows.Err(); err != nil || len(lockedIDs) != 2 {
		t.Fatalf("pending row locks: %v, %v", lockedIDs, err)
	}
	lockEvents = append(lockEvents, map[string]any{"event": "pending job row locks acquired", "at": time.Now(), "SQL": "SELECT record_id::text FROM cairn.semantic_job ORDER BY record_id FOR UPDATE", "record_ids": lockedIDs, "unchanged_jobs": pending})
	startupLock, err := d.pool.Begin(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = startupLock.Rollback(context.Background()) })
	if _, err = startupLock.Exec(context.Background(), `LOCK TABLE cairn.semantic_job IN SHARE MODE`); err != nil {
		t.Fatal(err)
	}
	lockEvents = append(lockEvents, map[string]any{"event": "startup SHARE table lock acquired", "at": time.Now(), "SQL": "LOCK TABLE cairn.semantic_job IN SHARE MODE", "unchanged_jobs": d.restartJobs(t)})
	report["fixture_lock_events"] = lockEvents
	type backend struct {
		recorder *transitionPartialRecorder
		reader   *core.Store
		stop     func()
		writer   *transitionWriter
	}
	var errorMu sync.Mutex
	backendErrors := []map[string]any{}
	startBackend := func(name string) backend {
		t.Helper()
		production, stop, err := StartIndex(context.Background(), d.dsn, w.Embed, func(err error) {
			errorMu.Lock()
			backendErrors = append(backendErrors, map[string]any{"backend": name, "at": time.Now(), "error": fmt.Sprint(err)})
			errorMu.Unlock()
		})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(stop)
		r := &transitionPartialRecorder{retrieve: production}
		reader, err := core.OpenWithSemanticRetriever(context.Background(), d.dsn, core.Channel{Principal: "s05-partial-reader"}, r.Retrieve)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(reader.Close)
		writer := &transitionWriter{name: name, collection: "both collections/shared queue", model: w.EmbedIdentity, restartable: true}
		writers = append(writers, writer)
		return backend{r, reader, stop, writer}
	}
	request := core.SemanticRankRequest{Query: "partialanchor", Notes: []core.SemanticNote{a, b}}
	type compiled struct {
		result core.IndexResult
		err    error
		calls  []transitionPartialCall
	}
	compile := func(ctx context.Context, owner backend) compiled {
		count := len(owner.recorder.observed())
		result, err := owner.reader.Index(ctx, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: "s05-a", TaskID: "fixture", RunID: "real-case7"}, Query: "partialanchor", Purpose: "context", AvailableTokens: 64000, Semantic: true}, core.Destination{Name: "local", AllowLocal: true})
		calls := owner.recorder.observed()
		return compiled{result, err, calls[count:]}
	}
	check := func(got compiled, ready bool, indexed int) map[string]any {
		t.Helper()
		detail := map[string]any{"compiler": got.result, "compiler_error": fmt.Sprint(got.err), "actual_retrieval_calls": got.calls, "query_scope_eligible": 2}
		if got.err != nil || len(got.calls) != 1 {
			t.Fatalf("actual compiler/provider observation: %v, calls=%d", got.err, len(got.calls))
		}
		call := got.calls[0]
		if call.Request.Query != request.Query || len(call.Request.Notes) != 2 {
			t.Fatal("compiler did not pass the actual scoped eligible candidates")
		}
		candidates := map[string]bool{}
		for _, note := range call.Request.Notes {
			if (note.RecordID != a.RecordID && note.RecordID != b.RecordID) || note.Version != 1 || note.BodySHA256 != transitionHash(note.Body) || candidates[note.RecordID] {
				t.Fatal("scoped candidate identity/body/denominator changed")
			}
			candidates[note.RecordID] = true
		}
		p := got.result.Package.Semantic
		if p.Discovery == nil || len(p.Index) != 2 {
			t.Fatal("compiler lost discovery or positive lexical candidates")
		}
		visible := map[string]bool{}
		for _, entry := range p.Index {
			if !candidates[entry.RecordID] || visible[entry.RecordID] || entry.Version != 1 {
				t.Fatal("compiler exposed another collection, duplicate or stale version")
			}
			visible[entry.RecordID] = true
		}
		if ready {
			if call.Error != "<nil>" || call.Result.Indexed != indexed || len(call.Result.Hits) != indexed || call.Result.ModelSHA256 != w.EmbedIdentity || p.Discovery.State != "ready" || p.Discovery.Coverage == nil || p.Discovery.Coverage.Indexed != indexed || p.Discovery.Coverage.Eligible != 2 || p.Status == "DEGRADED_NO_EMBEDDINGS" {
				t.Fatal("ready partial/full coverage differs from observed backend")
			}
			seen := map[string]bool{}
			for _, hit := range call.Result.Hits {
				if !candidates[hit.RecordID] || seen[hit.RecordID] || hit.Version != 1 || hit.Score <= 0 || (indexed == 1 && hit.RecordID != a.RecordID) {
					t.Fatal("missing real positive scoped semantic hit")
				}
				seen[hit.RecordID] = true
			}
		} else if call.Error == "<nil>" || p.Discovery.State != "unavailable" || p.Discovery.Coverage != nil || p.Status != "DEGRADED_NO_EMBEDDINGS" {
			t.Fatal("actual backend failure lost unavailable/degraded lexical behavior")
		}
		return detail
	}
	ready := func(owner backend) {
		t.Helper()
		transitionRestartWait(t, "real StartIndex readiness with pending jobs locked", 60*time.Second, func() bool {
			r, err := owner.recorder.Retrieve(context.Background(), request)
			return err == nil && r.ModelSHA256 == w.EmbedIdentity && r.Indexed == 1 && len(r.Hits) == 1 && r.Hits[0].RecordID == a.RecordID
		})
	}
	one := startBackend("production StartIndex partial/cold owner")
	transitionRestartWait(t, "actual OpenIndex seed INSERT waiting on startup table lock", 60*time.Second, func() bool {
		return d.count(t, `SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE 'INSERT INTO cairn.semantic_job(record_id)%'`) == 1
	})
	d.exec(t, `UPDATE cairn.s05_phase SET phase=1`)
	got := compile(context.Background(), one)
	detail := check(got, false, 0)
	if got.calls[0].Error != "passage index unavailable" {
		t.Fatal("startup phase observed a different unavailability boundary")
	}
	detail["boundary"] = "OpenIndex seed INSERT blocked after real identity startup; index pointer not installed"
	snapshot("startup_unavailable", 1, detail)
	if err = startupLock.Rollback(context.Background()); err != nil {
		t.Fatal(err)
	}
	lockEvents = append(lockEvents, map[string]any{"event": "startup SHARE lock released; pending row locks retained", "at": time.Now(), "unchanged_jobs": d.restartJobs(t)})
	report["fixture_lock_events"] = lockEvents
	ready(one)
	d.exec(t, `UPDATE cairn.s05_phase SET phase=2`)
	got = compile(context.Background(), one)
	snapshot("ready_partial", 1, check(got, true, 1))
	if !reflect.DeepEqual(pending, d.restartJobs(t)) {
		t.Fatal("partial backend selected or changed locked pending jobs")
	}
	var warmPID int
	liveRoots := []int{}
	for _, pid := range transitionRealRoots(t, w, selected.TracePrefix) {
		parent, state, err := transitionProcessStatus(pid)
		if err == nil && parent == os.Getpid() && state != "Z" && state != "X" {
			liveRoots = append(liveRoots, pid)
		}
	}
	if len(liveRoots) != 1 {
		t.Fatalf("locked document queue did not isolate the identity/query lane: %v", liveRoots)
	}
	warmPID = liveRoots[0]
	transitionRestartWait(t, "warm query lane exact native/environment receipt", 20*time.Second, func() bool {
		var sampled map[string][]int
		data, err := os.ReadFile(selected.SamplePIDs)
		if err != nil || json.Unmarshal(data, &sampled) != nil {
			return false
		}
		for _, pid := range sampled[label] {
			if pid == warmPID {
				return true
			}
		}
		return false
	})
	warmPipes, err := transitionPartialObservePipes(warmPID)
	if err != nil {
		t.Fatal(err)
	}
	if err = transitionSignalOwnedWorker(warmPID, os.Getpid(), syscall.SIGKILL, func(pid int) (int, error) { parent, _, err := transitionProcessStatus(pid); return parent, err }, syscall.Kill); err != nil {
		t.Fatal(err)
	}
	d.exec(t, `UPDATE cairn.s05_phase SET phase=3`)
	got = compile(context.Background(), one)
	detail = check(got, false, 0)
	if got.calls[0].Deadline || got.calls[0].Canceled {
		t.Fatal("idle query worker death became caller cancellation")
	}
	transitionGone(t, []int{warmPID})
	if fds, err := transitionPartialPipeFDs(warmPipes.Output); err != nil || len(fds) != 0 {
		t.Fatal("failed query reader stdout FD survived joined failure")
	}
	detail["boundary"], detail["old_worker_pipes"], detail["old_process_reaped_and_reader_fd_closed"] = "actual cached query worker death and failed exchange with live caller", warmPipes, true
	snapshot("killed_query_unavailable", 1, detail)
	beforeCold := map[int]bool{}
	for _, pid := range transitionRealRoots(t, w, selected.TracePrefix) {
		beforeCold[pid] = true
	}
	d.exec(t, `UPDATE cairn.s05_phase SET phase=4`)
	coldContext, cancelCold := context.WithCancel(context.Background())
	done := make(chan compiled, 1)
	joined := false
	t.Cleanup(func() {
		cancelCold()
		one.stop()
		if !joined {
			<-done
		}
	})
	go func() { done <- compile(coldContext, one) }()
	coldPipes := map[int]transitionPartialPipes{}
	outer := time.NewTimer(10 * time.Second)
	defer outer.Stop()
	for !joined {
		select {
		case got = <-done:
			joined = true
		case <-outer.C:
			t.Fatal("cold compiler call exceeded outer observation budget; production retrieval budget unchanged")
		default:
			for _, pid := range transitionRealRoots(t, w, selected.TracePrefix) {
				if beforeCold[pid] {
					continue
				}
				if _, seen := coldPipes[pid]; !seen {
					if pipes, err := transitionPartialObservePipes(pid); err == nil {
						coldPipes[pid] = pipes
					}
				}
			}
			time.Sleep(10 * time.Millisecond)
		}
	}
	cancelCold()
	newRoots := []int{}
	for _, pid := range transitionRealRoots(t, w, selected.TracePrefix) {
		if !beforeCold[pid] {
			newRoots = append(newRoots, pid)
		}
	}
	if len(newRoots) != 1 || len(coldPipes) != 1 || coldPipes[newRoots[0]].Output == "" || coldPipes[newRoots[0]].Output == warmPipes.Output || newRoots[0] == warmPID || len(got.calls) != 1 {
		t.Fatalf("cold restart missing unique new owned process/reader observation: roots=%v pipes=%v calls=%d", newRoots, coldPipes, len(got.calls))
	}
	coldPID, call := newRoots[0], got.calls[0]
	if call.Elapsed > 3*time.Second {
		t.Fatalf("two-second retrieval exceeded one-second scheduling/cleanup margin: %s", call.Elapsed)
	}
	if call.Error == "<nil>" {
		detail = check(got, true, 1)
		detail["cold_outcome"] = "real successful new-worker query within unchanged two-second call budget"
	} else {
		if !call.Deadline || call.Canceled || call.Elapsed < 2*time.Second {
			t.Fatalf("unexpected cold query failure: %+v", call)
		}
		detail = check(got, false, 0)
		transitionGone(t, []int{coldPID})
		if fds, err := transitionPartialPipeFDs(coldPipes[coldPID].Output); err != nil || len(fds) != 0 {
			t.Fatal("cold timeout left its reader stdout FD open")
		}
		detail["cold_outcome"], detail["timeout_process_reaped_and_reader_fd_closed"] = "actual two-second deadline; compiler unavailable/degraded and lexical candidates retained", true
	}
	detail["new_worker_pipes"], detail["old_worker_pid"], detail["prewarming"] = coldPipes[coldPID], warmPID, "none for designated call; filesystem/page caches uncontrolled"
	var sampled map[string][]int
	data, err := os.ReadFile(selected.SamplePIDs)
	if err != nil || json.Unmarshal(data, &sampled) != nil {
		t.Fatal("invalid runtime receipt after cold observation")
	}
	fullNativeReady := false
	for _, pid := range sampled[label] {
		fullNativeReady = fullNativeReady || pid == coldPID
	}
	detail["full_runtime_ready_sample_at_cold_return"] = fullNativeReady
	if !fullNativeReady {
		detail["explicit_observation_gap"] = "cold worker exited or returned before a full-runtime-ready PID receipt; all actual mapped-image/environment samples and every root remain mandatory in the host observer"
	}
	snapshot("cold_query_restart", 1, detail)
	if !reflect.DeepEqual(pending, d.restartJobs(t)) {
		t.Fatal("query failures/cold observation changed locked rebuild population")
	}
	report["writers_before_cold_owner_fence"] = transitionRealWriters(writers)
	one.writer.restartable = false
	one.stop()
	one.writer.joined = true
	transitionGone(t, transitionRealRoots(t, w, selected.TracePrefix))
	if err = transitionFence(writers); err != nil {
		t.Fatal(err)
	}
	report["writers_at_cold_owner_fence"] = transitionRealWriters(writers)
	// This new owner's normal asynchronous identity startup has the production
	// query supervisor's 25-second limit, separately from retrieval's two seconds.
	two := startBackend("separate same-generation recovery StartIndex owner")
	ready(two)
	d.exec(t, `UPDATE cairn.s05_phase SET phase=5`)
	got = compile(context.Background(), two)
	detail = check(got, true, 1)
	detail["recovery_scope"] = "new owner normal asynchronous identity startup under unchanged 25-second supervisor, then warm query under unchanged two-second retrieval; does not rewrite prior cold result"
	snapshot("restarted_owner_warm_control", 1, detail)
	if !reflect.DeepEqual(pending, d.restartJobs(t)) {
		t.Fatal("owner restart replaced or reset locked pending jobs")
	}
	d.exec(t, `UPDATE cairn.s05_phase SET phase=6`)
	if err = pendingLock.Rollback(context.Background()); err != nil {
		t.Fatal(err)
	}
	lockEvents = append(lockEvents, map[string]any{"event": "pending row locks released for normal background recovery", "at": time.Now(), "unchanged_jobs_at_release": pending})
	report["fixture_lock_events"] = lockEvents
	transitionRestartWait(t, "normal background recovery of complete original population", 90*time.Second, func() bool {
		r := d.coverage(t, w.EmbedIdentity)
		return r.Eligible == 3 && r.Complete == 3 && len(r.Problems) == 0 && len(r.Jobs) == 0
	})
	got = compile(context.Background(), two)
	detail = check(got, true, 2)
	two.writer.restartable = false
	two.stop()
	two.writer.joined = true
	transitionGone(t, transitionRealRoots(t, w, selected.TracePrefix))
	if err = transitionFence(writers); err != nil {
		t.Fatal(err)
	}
	detail["writers_at_final_fence"] = transitionRealWriters(writers)
	snapshot("full_recovery", 3, detail)
	if len(phases) != len(transitionPartialPhases) {
		t.Fatal("missing declared real partial phase")
	}
	for n, phase := range phases {
		if phase["name"] != transitionPartialPhases[n] || phase["status"] != "PASS" {
			t.Fatal("real partial phase membership differs from declared contract")
		}
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_publication`) != 3 || d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=0 AND record_id=$1`, a.RecordID) != 1 || d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=6`) != 2 || d.count(t, `SELECT count(*) FROM (SELECT record_id FROM cairn.s05_publication GROUP BY record_id HAVING count(*)<>1) q`) != 0 {
		t.Fatal("missing/duplicate/publication outside normal seed and recovery phases")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_tuple_publication p LEFT JOIN cairn.memory_record m USING(record_id) LEFT JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version WHERE p.model<>$1 OR v.version IS NULL OR p.version<>v.version OR p.body_sha256<>encode(sha256(convert_to(v.body,'UTF8')),'hex')`, w.EmbedIdentity) != 0 {
		t.Fatal("published source/model tuple changed")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_job_audit`) != 9 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE operation='INSERT'`) != 3 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE operation='INSERT' AND phase<>0`) != 0 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE operation='UPDATE' AND (old_row->>'attempts' IS DISTINCT FROM new_row->>'attempts' OR new_row->>'attempts'<>'0')`) != 0 {
		t.Fatal("hidden job reinsertion/reset or discarded rebuild failure population")
	}
	if d.count(t, `SELECT count(*) FROM cairn.record_use`) != 12 || d.count(t, `SELECT count(*) FROM cairn.record_use WHERE record_id=$1 AND version=1 AND exposure_kind='index'`, a.RecordID) != 6 || d.count(t, `SELECT count(*) FROM cairn.record_use WHERE record_id=$1 AND version=1 AND exposure_kind='index'`, b.RecordID) != 6 || d.count(t, `SELECT count(*) FROM cairn.record_use WHERE record_id=$1`, c.RecordID) != 0 {
		t.Fatal("compiler exposure population differs from six actual two-source index deliveries")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_source_audit`) != 18 || d.count(t, `SELECT count(*) FROM cairn.s05_source_audit WHERE operation='INSERT' AND phase<>0`) != 0 || d.count(t, `SELECT count(*) FROM cairn.s05_source_audit WHERE table_name='record_version' AND operation<>'INSERT'`) != 0 || d.count(t, `SELECT count(*) FROM cairn.s05_source_audit WHERE table_name='memory_record' AND operation='UPDATE' AND ((new_row-'use_generation') IS DISTINCT FROM (old_row-'use_generation') OR (new_row->>'use_generation')::bigint<>(old_row->>'use_generation')::bigint+1)`) != 0 {
		t.Fatal("source audit changed more than store-owned committed-use generation")
	}
	errorMu.Lock()
	report["background_errors"] = append([]map[string]any{}, backendErrors...)
	errorMu.Unlock()
	report["first_owner_calls"], report["recovery_owner_calls"] = one.recorder.observed(), two.recorder.observed()
	report["source_population_after"], report["complete_history_after"] = d.realPopulation(t), transitionEditHistory(t, d)
	report["stdout_cache_disposition"] = "canonical replies decoded by production CommandEmbedder; query death and timeout stop/reap worker and close observed stdout/reader pipe; each new worker has new pipe; no embedding-cache protocol; raw strace retains 256-byte write prefixes"
	report["limitations"] = "partial is not per-record fallback or complete rebuild; backend restart warm control is separate from cold query; no inference-interruption/latency/OS-cache-cold or full S05 claim"
	w.verify(t)
	report["status"] = "PASS"
	return report
}
