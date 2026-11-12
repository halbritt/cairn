package semantic

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
)

const transitionCompetingLock = 328008

type transitionCompetingRegistry struct {
	directory  string
	owners     map[string]*transitionCompetingOwner
	names      []string
	generation string
}

func (r *transitionCompetingRegistry) inventory(t *testing.T) ([]string, map[string]transitionCompetingState, []map[string]any) {
	t.Helper()
	if len(r.owners) != len(r.names) {
		t.Fatal("registered start methods do not match the complete fixture definition set")
	}
	entries, err := os.ReadDir(r.directory)
	if err != nil {
		t.Fatal(err)
	}
	var observed []string
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			t.Fatal("unknown fixture writer configuration entry: " + entry.Name())
		}
		name := strings.TrimSuffix(entry.Name(), ".json")
		owner, ok := r.owners[name]
		if !ok {
			t.Fatal("unregistered fixture writer configuration: " + name)
		}
		info, err := os.Lstat(owner.configPath)
		if err != nil || !info.Mode().IsRegular() {
			t.Fatalf("nonregular writer configuration: %s: %v", name, err)
		}
		data, err := os.ReadFile(owner.configPath)
		if err != nil || transitionHash(string(data)) != owner.configSHA {
			t.Fatalf("writer configuration digest mismatch: %s: %v", name, err)
		}
		observed = append(observed, name)
	}
	states := map[string]transitionCompetingState{}
	rows := []map[string]any{}
	for _, name := range r.names {
		owner := r.owners[name]
		states[name] = owner.state
		rows = append(rows, map[string]any{"state": owner.state, "configuration": owner.config,
			"configuration_sha256": owner.configSHA, "configuration_path": owner.configPath,
			"worker_pids": append([]int{}, owner.children...), "joined_at": owner.joinedAt})
	}
	return observed, states, rows
}

func (r *transitionCompetingRegistry) fence(t *testing.T) error {
	t.Helper()
	observed, states, _ := r.inventory(t)
	return transitionCompetingFence(r.names, observed, states)
}

func (r *transitionCompetingRegistry) start(t *testing.T, name string) {
	t.Helper()
	owner, ok := r.owners[name]
	if !ok || owner.state.Model != r.generation {
		t.Fatal("unknown or unselected fixture writer start: " + name)
	}
	observed, _, _ := r.inventory(t)
	expected := append([]string(nil), r.names...)
	sort.Strings(expected)
	sort.Strings(observed)
	if !reflect.DeepEqual(expected, observed) {
		t.Fatal("cannot start with an omitted writer configuration")
	}
	owner.start(t)
}

func (r *transitionCompetingRegistry) selectNext(t *testing.T, name string) {
	t.Helper()
	if err := r.fence(t); err != nil {
		t.Fatal(err)
	}
	owner, ok := r.owners[name]
	if !ok || owner.state.Started {
		t.Fatal("unknown or reused selected-generation definition")
	}
	r.generation = owner.state.Model
	owner.state.Restartable = true
}

func transitionCompetingAuditSetup(t *testing.T, d *transitionDB) {
	t.Helper()
	d.exec(t, `CREATE TABLE cairn.s05_case8_mutation(
 sequence bigserial PRIMARY KEY, table_name text NOT NULL, operation text NOT NULL,
 phase integer NOT NULL, actor text NOT NULL, application_name text NOT NULL,
 old_row jsonb, new_row jsonb, observed_at timestamptz NOT NULL DEFAULT clock_timestamp());
 CREATE FUNCTION cairn.s05_case8_audit() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN
 INSERT INTO cairn.s05_case8_mutation(table_name,operation,phase,actor,application_name,old_row,new_row)
 SELECT TG_TABLE_NAME,TG_OP,phase,COALESCE(NULLIF(current_setting('cairn.case8_actor',true),''),'product'),
 current_setting('application_name'),CASE WHEN TG_OP='INSERT' THEN NULL ELSE to_jsonb(OLD) END,
 CASE WHEN TG_OP='DELETE' THEN NULL ELSE to_jsonb(NEW) END FROM cairn.s05_phase;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
 END $$;
 CREATE TRIGGER s05_case8_record AFTER INSERT OR UPDATE OR DELETE ON cairn.memory_record FOR EACH ROW EXECUTE FUNCTION cairn.s05_case8_audit();
 CREATE TRIGGER s05_case8_version AFTER INSERT OR UPDATE OR DELETE ON cairn.record_version FOR EACH ROW EXECUTE FUNCTION cairn.s05_case8_audit();
 CREATE TRIGGER s05_case8_job AFTER INSERT OR UPDATE OR DELETE ON cairn.semantic_job FOR EACH ROW EXECUTE FUNCTION cairn.s05_case8_audit();
 CREATE TRIGGER s05_case8_document AFTER INSERT OR UPDATE OR DELETE ON cairn.semantic_document FOR EACH ROW EXECUTE FUNCTION cairn.s05_case8_audit();
 CREATE FUNCTION cairn.s05_case8_hold_publication() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN IF current_setting('application_name') LIKE 'case8-%-old-%' THEN PERFORM pg_advisory_xact_lock(328008); END IF; RETURN NEW; END $$;
 CREATE TRIGGER s05_case8_hold_publication BEFORE INSERT ON cairn.semantic_document FOR EACH ROW EXECUTE FUNCTION cairn.s05_case8_hold_publication();`)
}

func transitionCompetingAdvanceDue(t *testing.T, d *transitionDB) map[string]any {
	t.Helper()
	before := d.restartJobs(t)
	tx, err := d.pool.Begin(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(context.Background())
	if _, err = tx.Exec(context.Background(), `SELECT set_config('cairn.case8_actor','fixture_due_after_joined_fence',true)`); err != nil {
		t.Fatal(err)
	}
	command, err := tx.Exec(context.Background(), `UPDATE cairn.semantic_job SET
 lease_until=CASE WHEN lease_until>clock_timestamp() THEN clock_timestamp()-interval '1 second' ELSE lease_until END,
 retry_at=CASE WHEN retry_at>clock_timestamp() THEN clock_timestamp() ELSE retry_at END
 WHERE lease_until>clock_timestamp() OR retry_at>clock_timestamp()`)
	if err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(context.Background()); err != nil {
		t.Fatal(err)
	}
	after := d.restartJobs(t)
	if len(before) != len(after) {
		t.Fatal("fixture due advancement added or removed jobs")
	}
	for n, old := range before {
		var a, b map[string]any
		if json.Unmarshal([]byte(old.FullRow), &a) != nil || json.Unmarshal([]byte(after[n].FullRow), &b) != nil {
			t.Fatal("invalid complete job row")
		}
		for _, key := range []string{"lease_until", "retry_at"} {
			delete(a, key)
			delete(b, key)
		}
		if !reflect.DeepEqual(a, b) {
			t.Fatal("fixture due advancement changed job identity, attempts or other metadata")
		}
	}
	return map[string]any{"operation": "fixture-only timestamp advancement after joined fence", "rows": command.RowsAffected(), "before": before, "after": after}
}

func transitionCompetingIdentity(t *testing.T, worker transitionRealWorker, notes []core.SemanticNote, real bool) {
	t.Helper()
	if real {
		worker.identities(t, notes)
		return
	}
	model, err := EmbeddingCommand(context.Background(), worker.Embed)
	if err != nil {
		t.Fatal(err)
	}
	defer model.Close()
	identity, err := model.Identity(context.Background())
	if err != nil || identity.ModelSHA256 != worker.EmbedIdentity || identity.Algorithm != "deterministic-transition-fixture/1" || identity.Dimensions != 384 {
		t.Fatalf("non-model controller fixture identity: %+v: %v", identity, err)
	}
}

func TestSemanticRealTransitionCase8(t *testing.T) {
	if os.Getenv("CAIRN_TRANSITION_REAL_CASE") != "8" {
		t.Skip("requires explicit --real-case8 retained selections")
	}
	var selected transitionRealSelection
	data, err := os.ReadFile(os.Getenv("CAIRN_TRANSITION_REAL_SELECTION"))
	if err != nil || json.Unmarshal(data, &selected) != nil || selected.SamplePIDs == "" {
		t.Fatal("case8 requires canonical selections and full mapped-runtime receipts")
	}
	transitionCompetingRoundTrip(t, selected, true)
}

func TestSemanticCompetingControlSequence(t *testing.T) {
	if os.Getenv("CAIRN_TRANSITION_CASE8_CONTROLS") != "1" {
		t.Skip("requires explicit --case8-controls disposable protocol-worker run")
	}
	fixture := func(name string) transitionRealWorker {
		w := transitionFixture(t, name)
		data, err := os.ReadFile(w.path)
		if err != nil {
			t.Fatal(err)
		}
		return transitionRealWorker{Rank: w.path, Embed: w.path, RankIdentity: w.hash,
			EmbedIdentity: w.hash, LauncherSHA: map[string]string{w.path: transitionHash(string(data))}}
	}
	transitionCompetingRoundTrip(t, transitionRealSelection{Python: fixture("case8-python-control"), Rust: fixture("case8-rust-control")}, false)
}

func transitionCompetingRoundTrip(t *testing.T, selected transitionRealSelection, real bool) {
	d := transitionDatabase(t)
	transitionCompetingAuditSetup(t, d)
	directory := t.TempDir()
	cli, err := os.ReadFile(os.Getenv("CAIRN_TRANSITION_CLI"))
	if err != nil {
		t.Fatal(err)
	}
	cliSHA := transitionHash(string(cli))
	database := transitionCompetingDatabaseName(t, d)
	r := &transitionCompetingRegistry{directory: directory, owners: map[string]*transitionCompetingOwner{}, generation: selected.Python.EmbedIdentity}
	directions := []struct {
		name      string
		old, next transitionRealWorker
	}{{"python_to_rust", selected.Python, selected.Rust}, {"rust_to_python", selected.Rust, selected.Python}}
	for _, direction := range directions {
		for _, def := range []struct {
			role, collection string
			worker           transitionRealWorker
		}{{"old-a", "s05-a", direction.old}, {"old-b", "s05-b", direction.old}, {"inactive", "s05-c", direction.old}, {"selected", "s05-a", direction.next}} {
			name := direction.name + "-" + def.role
			owner := transitionCompetingOwnerConfig(t, d, directory, name, def.collection, database, cliSHA, def.worker)
			r.owners[name] = owner
			r.names = append(r.names, name)
			data, err := os.ReadFile(owner.configPath)
			if err != nil {
				t.Fatal(err)
			}
			if err = os.WriteFile(filepath.Join(os.Getenv("CAIRN_TRANSITION_REPORT_DIR"), "case8-definition-"+name+".json"), data, 0600); err != nil {
				t.Fatal(err)
			}
		}
	}
	sort.Strings(r.names)
	var notes []core.SemanticNote
	addSources := func(batch string) {
		if err := r.fence(t); err != nil {
			t.Fatal("ordinary source creation requires the joined fixture fence: " + err.Error())
		}
		for _, collection := range []string{"s05-a", "s05-b", "s05-c"} {
			notes = append(notes, d.source(t, collection, "database-wide source é "+batch+" "+collection))
		}
	}
	addSources("forward")
	original := transitionEditHistory(t, d)
	cases := []map[string]any{}
	for number, direction := range directions {
		if number == 1 {
			if !reflect.DeepEqual(original, transitionEditHistory(t, d)) {
				t.Fatal("forward transition changed original source rows")
			}
			addSources("reverse ordinary addition")
		}
		if !t.Run(direction.name, func(t *testing.T) {
			cases = append(cases, transitionCompetingOne(t, d, r, selected, direction.name, direction.old, direction.next, notes, number*10, real))
		}) {
			return
		}
	}
	final := transitionEditHistory(t, d)
	for _, before := range original {
		found := false
		for _, after := range final {
			if before.RecordID == after.RecordID && before.Version == after.Version {
				found = true
				if !reflect.DeepEqual(before, after) {
					t.Fatal("original source changed across forward and reverse transition")
				}
			}
		}
		if !found {
			t.Fatal("original source disappeared across transition")
		}
	}
	name := "case8-controls.json"
	if real {
		name = "real-case8.json"
	}
	transitionRealFile(t, name, map[string]any{"schema": "cairn.semantic-transition-case8/1", "real_workers": real,
		"cases": cases, "required_phases": transitionCompetingPhases, "initial_population": original,
		"final_population": final, "same_database": database, "original_python": selected.Python,
		"scope": "closed local fixture; source additions before reverse are ordinary Create calls; no host inventory or installed service claim"})
}

func transitionCompetingOne(t *testing.T, d *transitionDB, r *transitionCompetingRegistry, selected transitionRealSelection, direction string, old, next transitionRealWorker, notes []core.SemanticNote, phaseBase int, real bool) map[string]any {
	before := transitionEditHistory(t, d)
	sourceWrites := d.count(t, `SELECT count(*) FROM cairn.s05_case8_mutation WHERE table_name IN ('memory_record','record_version')`)
	a, b, inactive, target := r.owners[direction+"-old-a"], r.owners[direction+"-old-b"], r.owners[direction+"-inactive"], r.owners[direction+"-selected"]
	if r.generation != old.EmbedIdentity {
		t.Fatal("old generation does not equal the previously selected generation")
	}
	report := map[string]any{"direction": direction, "real_workers": real, "database": transitionCompetingDatabaseName(t, d),
		"old_selection": old, "selected_selection": next, "complete_history_before": before,
		"eligible_sources": len(notes), "inventory_completeness": "all eight registered concrete fixture start paths and sealed configuration files; no host inventory claim",
		"barrier":            "real validated worker result held at PostgreSQL BEFORE document INSERT, not inside inference",
		"stdout_disposition": "per-owner API logs retained; normal worker stdout prefix limits remain",
		"fixture_writes":     "ordinary source Create calls, phase/audit state and session advisory lock; only retained job due timestamps advanced after joined fence"}
	phases := []map[string]any{}
	defer func() {
		report["phases"], report["test_failed"] = phases, t.Failed()
		report["status"] = "PASS"
		if t.Failed() {
			report["status"] = "FAIL"
		}
		report["complete_history_after"] = transitionEditHistory(t, d)
		report["mutation_audit"] = transitionEditAudit(t, d, `SELECT to_jsonb(m)::text FROM cairn.s05_case8_mutation m ORDER BY sequence`)
		prefix := "case8-controls-"
		if real {
			prefix = "real-case8-"
		}
		transitionRealFile(t, prefix+direction+".json", report)
	}()
	transitionCompetingIdentity(t, old, notes, real)
	control, err := d.pool.Acquire(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if _, err = control.Exec(context.Background(), `SELECT pg_advisory_lock($1)`, transitionCompetingLock); err != nil {
		control.Release()
		t.Fatal(err)
	}
	barrierHeld := true
	defer func() {
		if barrierHeld {
			if _, err := control.Exec(context.Background(), `SELECT pg_advisory_unlock($1)`, transitionCompetingLock); err != nil {
				t.Error(err)
			}
			control.Release()
		}
	}()
	oldFencePhase := phaseBase + 5
	for number, name := range transitionCompetingPhases {
		phaseNumber := phaseBase + number + 1
		d.exec(t, `UPDATE cairn.s05_phase SET phase=$1`, phaseNumber)
		phase := map[string]any{"name": name, "phase": phaseNumber, "started_at": time.Now(), "status": "STARTED"}
		phases = append(phases, phase)
		switch name {
		case "old_owners_results_held":
			a.state.Restartable, b.state.Restartable, inactive.state.Restartable = true, true, true
			r.start(t, a.state.Name)
			r.start(t, b.state.Name)
			if a.state.PID == b.state.PID {
				t.Fatal("two active writer definitions share a fabricated owner PID")
			}
			transitionRestartWait(t, "both actual old-owner publication calls and own full worker samples", 90*time.Second, func() bool {
				return a.publicationWait(t, d) && b.publicationWait(t, d) && a.sampledChildren(t, selected, real) && b.sampledChildren(t, selected, real)
			})
			if d.count(t, `SELECT count(*) FROM cairn.semantic_job WHERE lease_id IS NOT NULL`) != 2 {
				t.Fatal("both real writer calls must hold distinct visible leases")
			}
			phase["blocked_publication_calls"] = transitionEditAudit(t, d, `SELECT jsonb_build_object('pid',pid,'application_name',application_name,'state',state,'wait_event_type',wait_event_type,'wait_event',wait_event,'query',query)::text FROM pg_stat_activity WHERE datname=current_database() AND application_name LIKE 'case8-%' ORDER BY application_name,pid`)
		case "both_active_refused":
			err := r.fence(t)
			if err == nil || !strings.Contains(err.Error(), a.state.Name) || !strings.Contains(err.Error(), b.state.Name) || !strings.Contains(err.Error(), inactive.state.Name) {
				t.Fatalf("all active/restartable definitions did not refuse: %v", err)
			}
			phase["fence_refusal"] = err.Error()
		case "other_collection_active_refused":
			a.state.Restartable, inactive.state.Restartable = false, false
			if err := a.stop(); err != nil {
				t.Fatal(err)
			}
			transitionRestartWait(t, "first owner backend calls joined", 10*time.Second, func() bool { return a.noBackendCalls(t, d) })
			if !b.publicationWait(t, d) {
				t.Fatal("other collection's real writer call was not still active")
			}
			err := r.fence(t)
			if err == nil || !strings.Contains(err.Error(), b.state.Name) || strings.Contains(err.Error(), a.state.Name) || strings.Contains(err.Error(), inactive.state.Name) {
				t.Fatalf("the other collection's active owner was not the isolated refusal: %v", err)
			}
			phase["fence_refusal"] = err.Error()
		case "inactive_restartable_refused":
			b.state.Restartable = false
			if err := b.stop(); err != nil {
				t.Fatal(err)
			}
			transitionRestartWait(t, "second owner backend calls joined", 10*time.Second, func() bool { return b.noBackendCalls(t, d) })
			inactive.state.Restartable = true
			err := r.fence(t)
			if err == nil || !strings.Contains(err.Error(), inactive.state.Name) || strings.Contains(err.Error(), a.state.Name) || strings.Contains(err.Error(), b.state.Name) {
				t.Fatalf("inactive concrete restartable definition did not refuse alone: %v", err)
			}
			if inactive.state.Started || inactive.state.PID != 0 {
				t.Fatal("inactive definition was represented as a live process")
			}
			phase["fence_refusal"] = err.Error()
		case "old_generation_joined_fence":
			inactive.state.Restartable = false
			if err := r.fence(t); err != nil {
				t.Fatal(err)
			}
			if !a.noBackendCalls(t, d) || !b.noBackendCalls(t, d) {
				t.Fatal("old API backend calls outlived the fence")
			}
			phase["fence_passed_at"] = time.Now()
		case "retained_jobs_due_advanced":
			if err := r.fence(t); err != nil {
				t.Fatal(err)
			}
			if _, err := control.Exec(context.Background(), `SELECT pg_advisory_unlock($1)`, transitionCompetingLock); err != nil {
				t.Fatal(err)
			}
			control.Release()
			barrierHeld = false
			phase["fixture_due_advancement"] = transitionCompetingAdvanceDue(t, d)
		case "selected_identity_verified":
			transitionCompetingIdentity(t, next, notes, real)
			phase["rank_identity"], phase["embed_identity"] = next.RankIdentity, next.EmbedIdentity
		case "selected_generation_ready":
			r.selectNext(t, target.state.Name)
			r.start(t, target.state.Name)
			transitionRestartWait(t, "selected generation's own full worker samples", 90*time.Second, func() bool { return target.sampledChildren(t, selected, real) })
		case "selected_generation_complete":
			transitionRestartWait(t, "entire declared database-wide current population", 120*time.Second, func() bool {
				coverage := d.coverage(t, next.EmbedIdentity)
				return coverage.Eligible == len(notes) && coverage.Complete == len(notes) && len(coverage.Jobs) == 0 && len(coverage.Problems) == 0
			})
			for _, note := range notes {
				if n := d.count(t, `SELECT count(*) FROM cairn.s05_case8_mutation WHERE table_name='semantic_document' AND operation='INSERT' AND application_name=$1 AND new_row->>'record_id'=$2 AND (new_row->>'version')::integer=$3 AND new_row->>'body_sha256'=$4 AND new_row->>'model_sha256'=$5`, target.application, note.RecordID, note.Version, note.BodySHA256, next.EmbedIdentity); n != 1 {
					t.Fatalf("selected publication for %s has count %d, want exactly one", note.RecordID, n)
				}
			}
			if n := d.count(t, `SELECT count(*) FROM cairn.s05_case8_mutation WHERE table_name='semantic_document' AND operation='INSERT' AND application_name=$1`, target.application); n != len(notes) {
				t.Fatal("selected publication observer missed or added sources")
			}
		case "selected_generation_joined_fence":
			target.state.Restartable = false
			if err := target.stop(); err != nil {
				t.Fatal(err)
			}
			transitionRestartWait(t, "selected API backend calls joined", 10*time.Second, func() bool { return target.noBackendCalls(t, d) })
			if err := r.fence(t); err != nil {
				t.Fatal(err)
			}
			coverage := transitionRequireCoverage(t, d, next.EmbedIdentity, len(notes), len(notes))
			if len(coverage.Jobs) != 0 {
				t.Fatal("final joined snapshot retains unresolved jobs")
			}
		}
		if number >= 4 && d.count(t, `SELECT count(*) FROM cairn.s05_case8_mutation WHERE table_name='semantic_document' AND operation='INSERT' AND phase>=$1 AND application_name IN ($2,$3)`, oldFencePhase, a.application, b.application) != 0 {
			t.Fatal("old owner published after its disabled/joined fence")
		}
		if !reflect.DeepEqual(before, transitionEditHistory(t, d)) || d.count(t, `SELECT count(*) FROM cairn.s05_case8_mutation WHERE table_name IN ('memory_record','record_version')`) != sourceWrites {
			t.Fatal("source/version/producer population mutated during a direction")
		}
		if d.count(t, `SELECT count(*) FROM cairn.record_use`) != 0 {
			t.Fatal("unexpected compiler exposure during writer-only fixture")
		}
		_, _, inventory := r.inventory(t)
		phase["writers"], phase["jobs"], phase["coverage"] = inventory, d.restartJobs(t), d.coverage(t, next.EmbedIdentity)
		phase["complete_source_history"] = transitionEditHistory(t, d)
		phase["status"], phase["completed_at"] = "PASS", time.Now()
	}
	return report
}
