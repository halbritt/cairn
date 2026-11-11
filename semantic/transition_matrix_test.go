package semantic

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

// Names track the ten S-05 contract rows. These fixtures qualify scheduling,
// fencing and refusal only; every real-worker row remains separately gated.
func TestSemanticTransitionDeterministicMatrix(t *testing.T) {
	cases := []struct {
		name string
		run  func(*testing.T)
	}{
		{"full_forward_reverse_rebuild", transitionRoundTrip},
		{"mid_rebuild_restart", transitionRestart},
		{"worker_death_deadline", transitionDeathDeadline},
		{"edit_during_embedding", transitionEdit},
		{"delete_forget_during_embedding", transitionDeleteForget},
		{"expired_lease_retry_backlog", transitionLeaseBacklog},
		{"partial_versus_unavailable", transitionPartial},
		{"competing_collection_writers_inactive_units", transitionCompeting},
		{"unembeddable_population_refusal", transitionUnembeddable},
		{"default_selection_whole_api_restart_rollback_handoff", transitionAPIHandoff},
	}
	for _, c := range cases {
		t.Run(c.name, c.run)
	}
}

func transitionStopIndex(i *Index) { i.model.(*CommandEmbedder).Close(); i.Close() }
func (w transitionWorker) rustConfig(t *testing.T) RustWorkerConfig {
	t.Helper()
	assets := filepath.Join(w.dir, "fixture-assets")
	if err := os.MkdirAll(assets, 0700); err != nil {
		t.Fatal(err)
	}
	library := filepath.Join(assets, "not-a-real-library")
	if err := os.WriteFile(library, []byte("deterministic process fixture only"), 0600); err != nil {
		t.Fatal(err)
	}
	return RustWorkerConfig{Mode: RustEmbed, Executable: w.path, ModelDir: assets, SourceRoot: assets, ORTRuntime: library, OpenBLAS: library, GFortran: library, Quadmath: library}
}

func transitionRoundTrip(t *testing.T) {
	d := transitionDatabase(t)
	python := transitionFixture(t, "python-like")
	rust := transitionFixture(t, "rust-like")
	for _, repo := range []string{"s05-a", "s05-b", "s05-c"} {
		d.source(t, repo, "database-wide source é in "+repo)
	}
	launcher, err := os.ReadFile(python.path)
	if err != nil {
		t.Fatal(err)
	}
	_, stopPython := transitionStart(t, d, python)
	transitionWait(t, "initial fixture-Python rebuild", func() bool { return d.coverage(t, python.hash).Complete == 3 })
	stopPython()
	python.reaped(t)
	for phase, w := range []transitionWorker{rust, python} {
		if err = transitionFence([]*transitionWriter{{name: "old combined API", joined: true}, {name: "alternate collection", joined: true}}); err != nil {
			t.Fatal(err)
		}
		d.exec(t, `UPDATE cairn.s05_phase SET phase=$1`, phase+1)
		start := time.Now()
		var stop func()
		if phase == 0 {
			_, stop, err = StartRustIndex(context.Background(), d.dsn, w.rustConfig(t), nil)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(stop)
		} else {
			_, stop = transitionStart(t, d, w)
		}
		transitionWait(t, "selected fixture generation rebuild", func() bool { return d.coverage(t, w.hash).Complete == 3 })
		transitionRequireCoverage(t, d, w.hash, 3, 3)
		stop()
		w.reaped(t)
		old := python.hash
		if phase == 1 {
			old = rust.hash
		}
		if n := d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=$1 AND model=$2`, phase+1, old); n != 0 {
			t.Fatal("old identity published after fence")
		}
		t.Logf("fixture-only phase=%d full_population=3 rebuild=%s", phase+1, time.Since(start))
	}
	after, err := os.ReadFile(python.path)
	if err != nil || string(after) != string(launcher) {
		t.Fatal("rollback launcher was not preserved")
	}
	if n := d.count(t, `SELECT count(*) FROM cairn.memory_record WHERE lifecycle='active'`); n != 3 {
		t.Fatal("rebuild changed source population")
	}
}

func transitionRestart(t *testing.T) {
	d := transitionDatabase(t)
	w := transitionFixture(t, "restart")
	d.source(t, "s05-a", "restart source é")
	w.action(t, "block")
	_, stop := transitionStart(t, d, w)
	transitionWait(t, "leased embedding", func() bool { return len(w.pids(t, "document")) > 0 })
	r := transitionRequireCoverage(t, d, w.hash, 1, 0)
	if len(r.Jobs) != 1 || !r.Jobs[0].Leased {
		t.Fatal("in-flight lease not recorded")
	}
	stop()
	w.reaped(t)
	w.action(t, "ok")
	_, stopAgain := transitionStart(t, d, w)
	// Cancellation can retain an expired lease or persisted retry. Make only the
	// fixture's scheduler due; production lease/deadline constants stay intact.
	d.exec(t, `UPDATE cairn.semantic_job SET lease_until=CASE WHEN lease_id IS NULL THEN NULL ELSE clock_timestamp()-interval '1 second' END,retry_at=clock_timestamp()`)
	transitionWait(t, "same-generation restart", func() bool { return d.coverage(t, w.hash).Complete == 1 })
	stopAgain()
	w.reaped(t)
	transitionRequireCoverage(t, d, w.hash, 1, 1)
}

func transitionDeathDeadline(t *testing.T) {
	for _, fault := range []string{"die", "caller-deadline"} {
		t.Run(fault, func(t *testing.T) {
			d := transitionDatabase(t)
			w := transitionFixture(t, fault)
			d.source(t, "s05-a", "worker failure source")
			i := transitionIndex(t, d, w)
			w.action(t, "die")
			ctx := context.Background()
			cancel := func() {}
			if fault == "caller-deadline" {
				w.action(t, "block")
				ctx, cancel = context.WithTimeout(ctx, 250*time.Millisecond)
			}
			defer cancel()
			worked, err := i.Step(ctx)
			if !worked || err == nil {
				t.Fatal("failure produced a successful publication")
			}
			if fault == "caller-deadline" && !errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("deadline changed: %v", err)
			}
			w.reapedOperation(t, "document")
			r := transitionRequireCoverage(t, d, w.hash, 1, 0)
			if len(r.Jobs) != 1 {
				t.Fatal("failure lost durable work")
			}
			// A dead worker with a live caller schedules retry; an expired caller can
			// leave its lease intact because retry uses that same expired context.
			if fault == "die" && (r.Jobs[0].Attempts != 1 || r.Jobs[0].Leased || !r.Jobs[0].RetryAt.After(time.Now())) {
				t.Fatal("missing persisted failure/backoff")
			}
			w.action(t, "ok")
			d.exec(t, `UPDATE cairn.semantic_job SET retry_at=clock_timestamp(),lease_id=NULL,lease_until=NULL`)
			transitionDrain(t, i)
			transitionRequireCoverage(t, d, w.hash, 1, 1)
		})
	}
}

func transitionEdit(t *testing.T) {
	d := transitionDatabase(t)
	w := transitionFixture(t, "edit")
	note := d.source(t, "s05-a", "old source é")
	i := transitionIndex(t, d, w)
	w.action(t, "block")
	done := make(chan error, 1)
	go func() { defer close(done); _, err := i.Step(context.Background()); done <- err }()
	t.Cleanup(func() { w.action(t, "ok"); i.model.(*CommandEmbedder).Close(); <-done })
	transitionWait(t, "edit barrier", func() bool { return len(w.pids(t, "document")) > 0 })
	if _, err := d.store.Revise(context.Background(), core.ReviseRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 1, Repo: "s05-a", Body: "current corrected source é"}); err != nil {
		t.Fatal(err)
	}
	w.action(t, "ok")
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if n := d.count(t, `SELECT count(*) FROM cairn.s05_publication`); n != 0 {
		t.Fatal("old-version attempt published")
	}
	transitionRequireCoverage(t, d, w.hash, 1, 0)
	transitionDrain(t, i)
	transitionRequireCoverage(t, d, w.hash, 1, 1)
	if n := d.count(t, `SELECT count(*) FROM cairn.semantic_document WHERE version<>2`); n != 0 {
		t.Fatal("wrong source version")
	}
}

func transitionDeleteForget(t *testing.T) {
	for _, operation := range []string{"delete", "forget"} {
		t.Run(operation, func(t *testing.T) {
			d := transitionDatabase(t)
			w := transitionFixture(t, operation)
			note := d.source(t, "s05-a", "delete during embedding é")
			i := transitionIndex(t, d, w)
			w.action(t, "block")
			done := make(chan error, 1)
			go func() { defer close(done); _, err := i.Step(context.Background()); done <- err }()
			t.Cleanup(func() { w.action(t, "ok"); i.model.(*CommandEmbedder).Close(); <-done })
			transitionWait(t, "deletion barrier", func() bool { return len(w.pids(t, "document")) > 0 })
			if operation == "delete" {
				if _, err := d.store.Delete(context.Background(), core.DeleteRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 1}); err != nil {
					t.Fatal(err)
				}
			} else {
				transitionForget(t, d, note)
			}
			w.action(t, "ok")
			if err := <-done; err != nil {
				t.Fatal(err)
			}
			transitionRequireCoverage(t, d, w.hash, 0, 0)
			if n := d.count(t, `SELECT count(*) FROM cairn.semantic_document`); n != 0 {
				t.Fatal("excluded source survived embedding")
			}
			if n := d.count(t, `SELECT count(*) FROM cairn.s05_publication`); n != 0 {
				t.Fatal("excluded source was published")
			}
		})
	}
}

func transitionForget(t *testing.T, d *transitionDB, n core.SemanticNote) {
	t.Helper()
	ctx := context.Background()
	s, err := core.Open(ctx, d.dsn, core.Channel{Principal: "s05-disposable-operator", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	g, err := s.Bootstrap(ctx, core.BootstrapRequest{RequestID: uuid.NewString(), Reason: "Owned disposable S05 forget fixture"})
	if err != nil {
		t.Fatal(err)
	}
	p, err := s.PreviewDeletion(ctx, n.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Forget(ctx, core.ForgetRequest{RequestID: uuid.NewString(), RecordID: n.RecordID, ExpectedVersion: n.Version, GrantID: g.ID, PreviewID: p.PreviewID}); err != nil {
		t.Fatal(err)
	}
}

func transitionLeaseBacklog(t *testing.T) {
	d := transitionDatabase(t)
	w := transitionFixture(t, "lease")
	note := d.source(t, "s05-a", "lease source")
	i := transitionIndex(t, d, w)
	d.exec(t, `UPDATE cairn.semantic_job SET lease_id=$1,lease_until=clock_timestamp()+interval '2 minutes'`, uuid.NewString())
	if worked, err := i.Step(context.Background()); err != nil || worked {
		t.Fatal("live lease was stolen")
	}
	transitionRequireCoverage(t, d, w.hash, 1, 0)
	d.exec(t, `UPDATE cairn.semantic_job SET lease_until=clock_timestamp()-interval '1 second'`)
	w.action(t, "block")
	done := make(chan error, 1)
	go func() { defer close(done); _, err := i.Step(context.Background()); done <- err }()
	t.Cleanup(func() { w.action(t, "ok"); i.model.(*CommandEmbedder).Close(); <-done })
	transitionWait(t, "expired lease replacement", func() bool { return len(w.pids(t, "document")) > 0 })
	// Supersede the lease while its worker is out of transaction. A late result
	// must not publish and must not delete the successor's retained retry job.
	successor := uuid.NewString()
	d.exec(t, `UPDATE cairn.semantic_job SET lease_id=$1,lease_until=clock_timestamp()+interval '2 minutes',attempts=3,retry_at=clock_timestamp()+interval '5 minutes' WHERE record_id=$2`, successor, note.RecordID)
	w.action(t, "ok")
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	r := transitionRequireCoverage(t, d, w.hash, 1, 0)
	if r.Jobs[0].Attempts != 3 {
		t.Fatal("late work erased retry backlog")
	}
	if worked, err := i.Step(context.Background()); err != nil || worked {
		t.Fatal("backoff was ignored")
	}
	d.exec(t, `UPDATE cairn.semantic_job SET retry_at=clock_timestamp(),lease_until=clock_timestamp()-interval '1 second'`)
	transitionDrain(t, i)
	transitionRequireCoverage(t, d, w.hash, 1, 1)
}

func transitionPartial(t *testing.T) {
	d := transitionDatabase(t)
	w := transitionFixture(t, "partial")
	a := d.source(t, "s05-a", "coverage source one")
	b := d.source(t, "s05-a", "coverage source two")
	i := transitionIndex(t, d, w)
	if worked, err := i.Step(context.Background()); err != nil || !worked {
		t.Fatal(err)
	}
	request := core.SemanticRankRequest{Query: "coverage", Notes: []core.SemanticNote{a, b}}
	r, err := i.Search(context.Background(), request)
	if err != nil || r.Indexed != 1 || len(r.Hits) != 1 {
		t.Fatalf("partial ready index: %+v %v", r, err)
	}
	transitionRequireCoverage(t, d, w.hash, 2, 1)
	reader, err := core.OpenWithSemanticRetriever(context.Background(), d.dsn, core.Channel{Principal: "s05-reader"}, i.Search)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	query := core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: "s05-a", TaskID: "fixture", RunID: "transition"}, Query: "coverage", Purpose: "context", AvailableTokens: 64000, Semantic: true}
	got, err := reader.Index(context.Background(), query, core.Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("partial compiler discovery=%s indexed=%d global_eligible=2 (no per-record fallback promise)", got.Package.Semantic.Discovery.State, r.Indexed)
	if got.Package.Semantic.Discovery.State != "ready" || got.Package.Semantic.Discovery.Coverage == nil || got.Package.Semantic.Discovery.Coverage.Indexed != 1 || got.Package.Semantic.Discovery.Coverage.Eligible != 2 {
		t.Fatal("compiler partial coverage label/counts differ")
	}
	// Same real supervisor, no ready index: block initial identity, so the
	// compiler must label unavailability independently of partial coverage.
	cold := transitionFixture(t, "cold")
	cold.action(t, "cold-identity")
	retrieve, stop := transitionStart(t, d, cold)
	transitionWait(t, "cold identity process", func() bool { return len(cold.pids(t, "identity")) > 0 })
	unavailable, err := core.OpenWithSemanticRetriever(context.Background(), d.dsn, core.Channel{Principal: "s05-reader"}, retrieve)
	if err != nil {
		t.Fatal(err)
	}
	defer unavailable.Close()
	query.RequestID = uuid.NewString()
	got, err = unavailable.Index(context.Background(), query, core.Destination{Name: "local", AllowLocal: true})
	if err != nil || got.Package.Semantic.Discovery.State != "unavailable" || got.Package.Semantic.Status != "DEGRADED_NO_EMBEDDINGS" {
		t.Fatalf("unavailability lost: %+v %v", got, err)
	}
	stop()
	cold.reaped(t)
	// Exercise the unchanged two-second retrieval budget with a cold query
	// lane. This is a deterministic deadline observation, not model latency.
	queryWorker := transitionFixture(t, "partial")
	queryWorker.action(t, "cold-query")
	coldRetrieve, stopQuery := transitionStart(t, d, queryWorker)
	transitionWait(t, "same-generation query owner ready", func() bool { return d.coverage(t, w.hash).Complete == 2 })
	start := time.Now()
	_, err = coldRetrieve(context.Background(), request)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("cold query deadline: %v", err)
	}
	t.Logf("fixture cold-query cancellation=%s; production StartIndex budget remains 2s", time.Since(start))
	stopQuery()
	queryWorker.reaped(t)
}

func transitionCompeting(t *testing.T) {
	d := transitionDatabase(t)
	old := transitionFixture(t, "old-shared")
	alternate := transitionFixture(t, "old-shared")
	for _, repo := range []string{"s05-a", "s05-b", "s05-c"} {
		d.source(t, repo, "shared queue "+repo)
	}
	old.action(t, "block")
	alternate.action(t, "block")
	_, stopOld := transitionStart(t, d, old)
	_, stopOther := transitionStart(t, d, alternate)
	transitionWait(t, "two collection writers share queue", func() bool { return len(old.pids(t, "document")) > 0 && len(alternate.pids(t, "document")) > 0 })
	writers := []*transitionWriter{{name: "combined API A", collection: "s05-a", model: old.hash, restartable: true, stop: stopOld}, {name: "combined API B", collection: "s05-b", model: alternate.hash, restartable: true, stop: stopOther}, {name: "inactive alternate unit", collection: "s05-c", model: old.hash, restartable: true, joined: true}}
	if transitionFence(writers) == nil {
		t.Fatal("active/restartable writers accepted")
	}
	writers[0].stop()
	writers[0].joined = true
	writers[0].restartable = false
	old.reaped(t)
	if transitionFence(writers) == nil {
		t.Fatal("other collection's writer omitted")
	}
	writers[1].stop()
	writers[1].joined = true
	writers[1].restartable = false
	alternate.reaped(t)
	if transitionFence(writers) == nil {
		t.Fatal("inactive restartable unit omitted")
	}
	writers[2].restartable = false
	if err := transitionFence(writers); err != nil {
		t.Fatal(err)
	}
	d.exec(t, `UPDATE cairn.s05_phase SET phase=1`)
	next := transitionFixture(t, "next-shared")
	i := transitionIndex(t, d, next)
	d.exec(t, `UPDATE cairn.semantic_job SET lease_id=NULL,lease_until=NULL,retry_at=clock_timestamp()`)
	transitionDrain(t, i)
	transitionRequireCoverage(t, d, next.hash, 3, 3)
	if n := d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=1 AND model=$1`, old.hash); n != 0 {
		t.Fatal("old publication after joined fence")
	}
	// Empty queue is deliberately NOT a fence: these closed old owners cannot
	// restart on a query, while an inventory would still refuse restartability.
	if n := d.count(t, `SELECT count(*) FROM cairn.semantic_job`); n != 0 {
		t.Fatal("fixture rebuild left jobs")
	}
}

func transitionUnembeddable(t *testing.T) {
	d := transitionDatabase(t)
	blank := d.source(t, "s05-a", "placeholder for retained whitespace")
	tokenEmpty := d.source(t, "s05-b", "\u200b")
	d.source(t, "s05-c", "embeddable source")
	// Construct a retained whitespace population explicitly; ordinary Create
	// rejects whitespace. Do not change eligibility to hide this negative row.
	d.exec(t, `UPDATE cairn.record_version SET body='   ' WHERE record_id=$1`, blank.RecordID)
	var failureSets [][]string
	for _, generation := range []string{"python-like", "rust-like"} {
		w := transitionFixture(t, generation)
		i := transitionIndex(t, d, w)
		d.exec(t, `UPDATE cairn.semantic_job SET retry_at=clock_timestamp(),lease_id=NULL,lease_until=NULL`)
		for n := 0; n < 3; n++ {
			if worked, _ := i.Step(context.Background()); !worked {
				t.Fatal("unembeddable population disappeared")
			}
		}
		r := transitionRequireCoverage(t, d, w.hash, 3, 1)
		if len(r.Jobs) != 2 {
			t.Fatal("failed rows removed from durable backlog")
		}
		ids := []string{}
		for _, p := range r.Problems {
			ids = append(ids, p.RecordID)
		}
		failureSets = append(failureSets, ids)
		transitionStopIndex(i)
	}
	if !reflect.DeepEqual(failureSets[0], failureSets[1]) || len(failureSets[0]) != 2 {
		t.Fatal("fixture failure populations differ")
	}
	for _, id := range []string{blank.RecordID, tokenEmpty.RecordID} {
		if n := d.count(t, `SELECT count(*) FROM cairn.memory_record WHERE record_id=$1 AND lifecycle='active'`, id); n != 1 {
			t.Fatal("unembeddable denominator was altered")
		}
	}
}

func TestSemanticTransitionCompletionRefusesMalformedDerivedState(t *testing.T) {
	d := transitionDatabase(t)
	w := transitionFixture(t, "completion")
	note := d.source(t, "s05-a", "é database source")
	i := transitionIndex(t, d, w)
	transitionDrain(t, i)
	transitionRequireCoverage(t, d, w.hash, 1, 1)
	for _, c := range []struct {
		name, sql, reason string
		args              []any
	}{
		{"document_only", `DELETE FROM cairn.semantic_passage`, "passage_count", nil},
		{"wrong_model", `UPDATE cairn.semantic_document SET model_sha256=$1`, "model_identity", []any{strings.Repeat("f", 64)}},
		{"wrong_body_hash", `UPDATE cairn.semantic_document SET body_sha256=$1`, "source_body_hash", []any{strings.Repeat("f", 64)}},
		{"ordinal_gap", `UPDATE cairn.semantic_passage SET ordinal=1`, "passage_ordinals", nil},
		{"utf8_split", `UPDATE cairn.semantic_passage SET byte_offset=1,byte_length=1`, "passage_utf8_span", nil},
		{"outside_source", `UPDATE cairn.semantic_passage SET byte_length=65536`, "passage_utf8_span", nil},
		{"zero_vector", `UPDATE cairn.semantic_passage SET embedding=$1::public.vector`, "vector_nonzero_finite", []any{transitionVector(0)}},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Cleanup(func() {
				d.exec(t, `DELETE FROM cairn.semantic_document`)
				d.exec(t, `INSERT INTO cairn.semantic_job(record_id) VALUES($1) ON CONFLICT(record_id) DO NOTHING`, note.RecordID)
				transitionDrain(t, i)
				transitionRequireCoverage(t, d, w.hash, 1, 1)
			})
			d.exec(t, c.sql, c.args...)
			if n := d.count(t, `SELECT count(*) FROM cairn.semantic_job`); n != 0 {
				t.Fatal("negative fixture must have empty queue")
			}
			r := transitionRequireCoverage(t, d, w.hash, 1, 0)
			if r.Problems[0].Reason != c.reason {
				t.Fatalf("wrong refusal: %+v", r)
			}
		})
	}
	// Version mismatch has a real prior version, not a broken foreign key.
	if _, err := d.store.Revise(context.Background(), core.ReviseRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 1, Repo: "s05-a", Body: note.Body}); err != nil {
		t.Fatal(err)
	}
	transitionDrain(t, i)
	d.exec(t, `UPDATE cairn.semantic_document SET version=1`)
	r := transitionRequireCoverage(t, d, w.hash, 1, 0)
	if r.Problems[0].Reason != "source_version" {
		t.Fatal("old version accepted")
	}
}

func TestSemanticTransitionPassageGateBounds(t *testing.T) {
	valid := transitionPassage{ordinal: 0, offset: 0, length: 2, vector: transitionVector(1)}
	if problem := transitionPassageProblem("é", []transitionPassage{valid}); problem != "" {
		t.Fatal(problem)
	}
	for _, c := range []struct {
		name   string
		ps     []transitionPassage
		reason string
	}{
		{"missing", nil, "passage_count"},
		{"too_many", make([]transitionPassage, 257), "passage_count"},
		{"dimensions", []transitionPassage{{0, 0, 2, "[1]"}}, "vector_dimensions_or_encoding"},
		{"nonfinite", []transitionPassage{{0, 0, 2, "[NaN]"}}, "vector_dimensions_or_encoding"},
		{"zero", []transitionPassage{{0, 0, 2, transitionVector(0)}}, "vector_nonzero_finite"},
	} {
		t.Run(c.name, func(t *testing.T) {
			if got := transitionPassageProblem("é", c.ps); got != c.reason {
				t.Fatalf("%s want %s", got, c.reason)
			}
		})
	}
}

// Implemented alongside the CLI disposable-service fixture in this same slice.
func transitionAPIHandoff(t *testing.T) { transitionWholeAPI(t) }
