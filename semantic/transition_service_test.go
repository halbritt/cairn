package semantic

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// This is an actual disposable combined API process, not a fake HTTP server or
// an installed service. It measures only fixture restart outage on this host.
func transitionWholeAPI(t *testing.T) {
	d := transitionDatabase(t)
	start, probe, _ := transitionAPIProcess(t, d)

	d.source(t, "s05-api", "whole API source é")
	d.source(t, "other-collection", "database-wide alternate collection")
	python := transitionFixture(t, "api-python-like")
	rust := transitionFixture(t, "api-rust-like")
	stop := start("default-no-semantic")
	if len(python.pids(t, ""))+len(rust.pids(t, "")) != 0 {
		t.Fatal("default selection launched a model")
	}
	transitionRequireCoverage(t, d, python.hash, 2, 0)
	lastUp := time.Now()
	stop()
	if probe() {
		t.Fatal("whole API falsely remained available after process join")
	}
	stop = start("explicit-python-like", "--embedding-command", python.path)
	t.Logf("S05 fixture whole-API default-to-embed observed outage=%s", time.Since(lastUp))
	transitionWait(t, "first API fixture rebuild", func() bool { return d.coverage(t, python.hash).Complete == 2 })
	for n, generation := range []string{"rust-like", "python-like"} {
		lastUp = time.Now()
		stop()
		if probe() {
			t.Fatal("API remained available after fence")
		}
		if n == 0 {
			python.reaped(t)
		} else {
			rust.reaped(t)
		}
		d.exec(t, `UPDATE cairn.s05_phase SET phase=$1`, n+1)
		selected := python
		if n == 0 {
			selected = rust
			// S04 fixed launchers take zero arguments. Preserve the generic
			// embedding selection on both sides; the raw-binary Rust route is
			// exercised separately by the forward/reverse index fixture.
			stop = start(generation, "--embedding-command", rust.path)
		} else {
			stop = start(generation, "--embedding-command", python.path)
		}
		t.Logf("S05 fixture whole-API %s observed outage=%s", generation, time.Since(lastUp))
		rebuild := time.Now()
		transitionWait(t, "API selected generation rebuild", func() bool { return d.coverage(t, selected.hash).Complete == 2 })
		transitionRequireCoverage(t, d, selected.hash, 2, 2)
		old := python.hash
		if n == 1 {
			old = rust.hash
		}
		if d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=$1 AND model=$2`, n+1, old) != 0 {
			t.Fatal("old API generation published after joined fence")
		}
		t.Logf("S05 fixture %s rebuild=%s embed_identity=%s", generation, time.Since(rebuild), selected.hash)
	}
	stop()
	python.reaped(t)
	if directory := os.Getenv("CAIRN_TRANSITION_REPORT_DIR"); directory != "" {
		facts := map[string]any{"scope": "disposable combined API / deterministic protocol workers", "source_population": 2, "selected_mode": "embed", "python_fixture_identity": python.hash, "rust_fixture_identity": rust.hash, "real_worker_acceptance": false, "rank_identity": "unobserved: real rollback requires accepted S04R"}
		if err := os.WriteFile(filepath.Join(directory, "service-handoff.json"), []byte(fmt.Sprintln(transitionJSON(facts))), 0600); err != nil {
			t.Fatal(err)
		}
	}
}
