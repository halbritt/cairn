package semantic

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

type transitionRealWorker struct {
	Rank          string            `json:"rank"`
	Embed         string            `json:"embed"`
	RankIdentity  string            `json:"rank_identity"`
	EmbedIdentity string            `json:"embed_identity"`
	LauncherSHA   map[string]string `json:"launcher_sha256"`
}

type transitionRealSelection struct {
	Python      transitionRealWorker `json:"python"`
	Rust        transitionRealWorker `json:"rust"`
	TracePrefix string               `json:"trace_prefix"`
}

func TestSemanticRealTransitionCase1(t *testing.T) {
	if os.Getenv("CAIRN_TRANSITION_REAL_SELECTION") == "" {
		t.Skip("requires explicit retained selections in check_semantic_transition.py --real-case1")
	}
	transitionRoundTrip(t)
}

type transitionRealSource struct {
	RecordID    string `json:"record_id"`
	Version     int    `json:"version"`
	BodySHA     string `json:"body_sha256"`
	VersionJSON string `json:"complete_version_row"`
}

func (d *transitionDB) realPopulation(t *testing.T) []transitionRealSource {
	t.Helper()
	tx, err := d.pool.Begin(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(context.Background())
	if _, err = tx.Exec(context.Background(), `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY`); err != nil {
		t.Fatal(err)
	}
	rows, err := tx.Query(context.Background(), `SELECT m.record_id::text,v.version,v.body,to_jsonb(v)::text
 FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
 WHERE m.lifecycle='active' AND v.payload_deleted_by IS NULL ORDER BY m.record_id`)
	if err != nil {
		t.Fatal(err)
	}
	var population []transitionRealSource
	for rows.Next() {
		var source transitionRealSource
		var body string
		if err = rows.Scan(&source.RecordID, &source.Version, &body, &source.VersionJSON); err != nil {
			t.Fatal(err)
		}
		source.BodySHA = transitionHash(body)
		population = append(population, source)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(context.Background()); err != nil {
		t.Fatal(err)
	}
	return population
}

func transitionRealFile(t *testing.T, name string, value any) {
	t.Helper()
	path := filepath.Join(os.Getenv("CAIRN_TRANSITION_REPORT_DIR"), name)
	if err := os.WriteFile(path, []byte(transitionJSON(value)+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
}

func (w transitionRealWorker) verify(t *testing.T) {
	t.Helper()
	for _, path := range []string{w.Rank, w.Embed} {
		resolved, err := filepath.EvalSymlinks(path)
		if err != nil || resolved != path || !filepath.IsAbs(path) {
			t.Fatalf("noncanonical retained entry: %s: %v", path, err)
		}
		content, err := os.ReadFile(path)
		if err != nil || transitionHash(string(content)) != w.LauncherSHA[path] {
			t.Fatalf("retained launcher binding changed: %s: %v", path, err)
		}
	}
}

func (w transitionRealWorker) identities(t *testing.T, notes []core.SemanticNote) {
	t.Helper()
	w.verify(t)
	rank, err := Command(w.Rank)
	if err != nil {
		t.Fatal(err)
	}
	r, err := rank(context.Background(), core.SemanticRankRequest{Query: "database-wide source", Notes: notes})
	if err != nil || r.ModelSHA256 != w.RankIdentity || r.Algorithm != "bge-max-chunk/1" {
		t.Fatalf("retained rank identity: %+v: %v", r, err)
	}
	embed, err := EmbeddingCommand(context.Background(), w.Embed)
	if err != nil {
		t.Fatal(err)
	}
	defer embed.Close()
	identity, err := embed.Identity(context.Background())
	if err != nil || identity.ModelSHA256 != w.EmbedIdentity || identity.Algorithm != "bge-original-passages/1" || identity.Dimensions != 384 {
		t.Fatalf("retained embed identity: %+v: %v", identity, err)
	}
	t.Logf("S05 real identities rank=%s embed=%s", r.ModelSHA256, identity.ModelSHA256)
}

func (w transitionRealWorker) reaped(t *testing.T, prefix string) {
	t.Helper()
	paths, err := filepath.Glob(prefix + ".*")
	if err != nil {
		t.Fatal(err)
	}
	seen := map[int]bool{}
	for _, path := range paths {
		content, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(content), "execve("+strconv.Quote(w.Rank)+",") && !strings.Contains(string(content), "execve("+strconv.Quote(w.Embed)+",") {
			continue
		}
		pid, err := strconv.Atoi(strings.TrimPrefix(path, prefix+"."))
		if err != nil || pid <= 1 {
			t.Fatalf("invalid owned worker PID: %s", path)
		}
		seen[pid] = true
		if err := syscall.Kill(pid, 0); err != syscall.ESRCH {
			t.Fatalf("retained worker %d not reaped: %v", pid, err)
		}
		if err := syscall.Kill(-pid, 0); err != syscall.ESRCH {
			t.Fatalf("retained worker group %d remains: %v", pid, err)
		}
	}
	if len(seen) == 0 {
		t.Fatal("no retained worker executions observed")
	}
}

func transitionRealQuery(t *testing.T, query func(any) (json.RawMessage, error)) map[string]any {
	t.Helper()
	start := time.Now()
	raw, err := query(core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: "s05-a", TaskID: "fixture", RunID: "real-transition"}, Query: "database-wide source", Purpose: "context", AvailableTokens: 64000, Semantic: true})
	if err != nil {
		t.Fatal(err)
	}
	var reply struct {
		OK   bool             `json:"ok"`
		Data core.IndexResult `json:"data"`
	}
	if err = json.Unmarshal(raw, &reply); err != nil || !reply.OK {
		t.Fatalf("actual API index envelope: %s: %v", raw, err)
	}
	return map[string]any{"elapsed_ns": time.Since(start).Nanoseconds(), "state": reply.Data.Package.Semantic.Discovery.State, "reply": json.RawMessage(raw)}
}

func transitionRealWriters(writers []*transitionWriter) []map[string]any {
	rows := []map[string]any{}
	for _, writer := range writers {
		rows = append(rows, map[string]any{"name": writer.name, "collection": writer.collection, "model": writer.model, "restartable": writer.restartable, "joined": writer.joined, "scope": "complete test-owned inventory, not a managed-host roster"})
	}
	return rows
}

func transitionRealRoundTrip(t *testing.T) {
	var selected transitionRealSelection
	raw, err := os.ReadFile(os.Getenv("CAIRN_TRANSITION_REAL_SELECTION"))
	if err != nil || json.Unmarshal(raw, &selected) != nil {
		t.Fatal("missing real selection")
	}
	d := transitionDatabase(t)
	var notes []core.SemanticNote
	for _, repo := range []string{"s05-a", "s05-b", "s05-c"} {
		notes = append(notes, d.source(t, repo, "database-wide source é in "+repo))
	}
	before := d.realPopulation(t)
	transitionRealFile(t, "source-population-before.json", before)
	d.exec(t, `CREATE TABLE cairn.s05_tuple_publication (phase integer,record_id uuid,version integer,body_sha256 text,model text);
 CREATE FUNCTION cairn.s05_observe_tuple() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN INSERT INTO cairn.s05_tuple_publication SELECT phase,NEW.record_id,NEW.version,NEW.body_sha256,NEW.model_sha256 FROM cairn.s05_phase; RETURN NEW; END $$;
 CREATE TRIGGER s05_observe_tuple AFTER INSERT ON cairn.semantic_document FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_tuple();`)
	start, probe, query := transitionAPIProcess(t, d)
	stop := start("real-default-no-semantic")
	definitions := []*transitionWriter{{name: "combined API", collection: "all three collections", restartable: true, stop: stop},
		{name: "inactive alternate collection definition", collection: "s05-b", restartable: true, joined: true},
		{name: "inactive restartable reverse definition", collection: "s05-c", restartable: true, joined: true}}
	phases := []map[string]any{}
	for number, w := range []transitionRealWorker{selected.Python, selected.Rust, selected.Python} {
		phaseStarted := time.Now()
		beforeFence := transitionRealWriters(definitions)
		if !probe() {
			t.Fatal("last whole-API probe failed before transition")
		}
		if transitionFence(definitions) == nil {
			t.Fatal("active/restartable writer inventory falsely fenced")
		}
		for _, definition := range definitions {
			definition.restartable = false
		}
		stop()
		definitions[0].joined = true
		if probe() {
			t.Fatal("whole API remained available after joined stop")
		}
		if number > 0 {
			previous := selected.Python
			if number == 2 {
				previous = selected.Rust
			}
			previous.reaped(t, selected.TracePrefix)
		}
		if err := transitionFence(definitions); err != nil {
			t.Fatal(err)
		}
		joinedFence := transitionRealWriters(definitions)
		if !reflect.DeepEqual(before, d.realPopulation(t)) {
			t.Fatal("operational source population changed at fence")
		}
		d.exec(t, `UPDATE cairn.s05_phase SET phase=$1`, number)
		identityStart := time.Now()
		w.identities(t, notes)
		identityElapsed := time.Since(identityStart)
		name := fmt.Sprintf("real-phase-%d", number)
		stop = start(name, "--embedding-command", w.Embed)
		definitions[0].joined = false
		definitions[0].restartable = true
		definitions[0].stop = stop
		definitions[0].model = w.EmbedIdentity
		wholeOutage := time.Since(phaseStarted)
		cold := transitionRealQuery(t, query)
		rebuildStart := time.Now()
		deadline := time.Now().Add(180 * time.Second)
		var coverage transitionCoverage
		for {
			coverage = d.coverage(t, w.EmbedIdentity)
			if coverage.Eligible != len(before) {
				t.Fatal("rebuild denominator changed")
			}
			if coverage.Complete == len(before) {
				break
			}
			for _, job := range coverage.Jobs {
				if job.Attempts > 0 {
					transitionRealFile(t, name+"-failure.json", coverage)
					t.Fatalf("actual worker failed; retained population %+v", coverage)
				}
			}
			if time.Now().After(deadline) {
				transitionRealFile(t, name+"-failure.json", coverage)
				t.Fatalf("outer rebuild observation expired: %+v", coverage)
			}
			time.Sleep(100 * time.Millisecond)
		}
		transitionRequireCoverage(t, d, w.EmbedIdentity, len(before), len(before))
		rebuildElapsed := time.Since(rebuildStart)
		if len(coverage.Jobs) != 0 {
			t.Fatal("full audit still has retained queue work")
		}
		post := transitionRealQuery(t, query)
		if post["state"] != "ready" {
			t.Fatalf("post-rebuild actual API semantic availability: %s", transitionJSON(post))
		}
		if !reflect.DeepEqual(before, d.realPopulation(t)) {
			t.Fatal("full current source population/hash changed")
		}
		if d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=$1 AND model<>$2`, number, w.EmbedIdentity) != 0 {
			t.Fatal("stale generation published after fence")
		}
		if d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=$1 AND model=$2`, number, w.EmbedIdentity) != len(before) {
			t.Fatal("publication positive control incomplete")
		}
		if d.count(t, `SELECT count(*) FROM cairn.s05_tuple_publication p LEFT JOIN cairn.memory_record m USING(record_id) LEFT JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
 WHERE p.phase=$1 AND (p.model<>$2 OR v.version IS NULL OR p.version<>v.version OR p.body_sha256<>encode(sha256(convert_to(v.body,'UTF8')),'hex'))`, number, w.EmbedIdentity) != 0 {
			t.Fatal("stale source/model/hash/version publication")
		}
		w.verify(t)
		phase := map[string]any{"phase": number, "rank_identity": w.RankIdentity, "embed_identity": w.EmbedIdentity, "population": coverage, "source_population_sha256": transitionHash(transitionJSON(before)), "identity_probe_ns": identityElapsed.Nanoseconds(), "whole_api_observed_outage_ns": wholeOutage.Nanoseconds(), "rebuild_observed_ns": rebuildElapsed.Nanoseconds(), "cold_api_query": cold, "post_rebuild_query": post, "writers_before_fence": beforeFence, "writers_at_joined_fence": joinedFence, "active_writer_definitions": transitionRealWriters(definitions), "timing_scope": "local transition including identity probes; first API query can precede index readiness, not a forced query-worker restart", "no_stale_publications": true}
		phases = append(phases, phase)
		transitionRealFile(t, name+".json", phase)
	}
	stop()
	selected.Python.reaped(t, selected.TracePrefix)
	for _, definition := range definitions {
		definition.restartable = false
		definition.joined = true
	}
	if err := transitionFence(definitions); err != nil {
		t.Fatal(err)
	}
	after := d.realPopulation(t)
	if !reflect.DeepEqual(before, after) {
		t.Fatal("reverse transition changed source population")
	}
	transitionRealFile(t, "source-population-after.json", after)
	transitionRealFile(t, "real-case1.json", map[string]any{"schema": "cairn.semantic-real-transition-case1/1", "phases": phases, "source_population": len(before), "reverse_python_selection_identical": true, "scope": "local case1 only; other nine real cases and full S05 acceptance remain open"})
}
