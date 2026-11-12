package semantic

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"reflect"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestSemanticRealTransitionCase4(t *testing.T) {
	if os.Getenv("CAIRN_TRANSITION_REAL_CASE") != "4" {
		t.Skip("requires explicit --real-case4 retained selections")
	}
	transitionEdit(t)
}

type transitionEditReceipt struct {
	Body       string            `json:"body"`
	BodySHA    string            `json:"body_sha256"`
	Passages   []EmbeddedPassage `json:"actual_decoded_passages"`
	ResultSHA  string            `json:"decoded_passages_sha256"`
	Identity   EmbeddingIdentity `json:"actual_identity"`
	WorkerPID  int               `json:"canonical_document_worker_pid"`
	ObtainedAt time.Time         `json:"actual_Document_returned_at"`
}

// The fixture holds a real result at the Index's Embedder return seam. It never
// substitutes a worker, identity, launcher, model result or production hook.
type transitionEditBarrier struct {
	*CommandEmbedder
	oldBody string
	held    chan transitionEditReceipt
	release chan struct{}
	calls   []transitionEditReceipt
}

func (b *transitionEditBarrier) Document(ctx context.Context, text string) ([]EmbeddedPassage, error) {
	passages, err := b.CommandEmbedder.Document(ctx, text)
	if err != nil {
		return nil, err
	}
	b.document.mu.Lock()
	if b.document.worker == nil {
		b.document.mu.Unlock()
		return nil, fmt.Errorf("real document worker missing after successful result")
	}
	pid := b.document.worker.command.Process.Pid
	b.document.mu.Unlock()
	b.identityMu.Lock()
	identity := b.identity
	b.identityMu.Unlock()
	r := transitionEditReceipt{text, transitionHash(text), passages, transitionHash(transitionJSON(passages)), identity, pid, time.Now()}
	b.calls = append(b.calls, r)
	if text == b.oldBody {
		select {
		case b.held <- r:
		case <-ctx.Done():
			return nil, ctx.Err()
		}
		select {
		case <-b.release:
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	return passages, nil
}

type transitionEditSource struct {
	RecordID   string `json:"record_id"`
	Version    int    `json:"version"`
	BodySHA    string `json:"body_sha256"`
	VersionRow string `json:"complete_version_row"`
	RecordRow  string `json:"complete_current_record_row"`
}

func transitionEditHistory(t *testing.T, d *transitionDB) []transitionEditSource {
	t.Helper()
	rows, err := d.pool.Query(context.Background(), `SELECT m.record_id::text,v.version,v.body,to_jsonb(v)::text,to_jsonb(m)::text FROM cairn.memory_record m JOIN cairn.record_version v USING(record_id) ORDER BY m.record_id,v.version`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	result := []transitionEditSource{}
	for rows.Next() {
		var row transitionEditSource
		var body string
		if err := rows.Scan(&row.RecordID, &row.Version, &body, &row.VersionRow, &row.RecordRow); err != nil {
			t.Fatal(err)
		}
		row.BodySHA = transitionHash(body)
		result = append(result, row)
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	return result
}

func transitionRealEdit(t *testing.T) {
	var selected transitionRealSelection
	data, err := os.ReadFile(os.Getenv("CAIRN_TRANSITION_REAL_SELECTION"))
	if err != nil || json.Unmarshal(data, &selected) != nil || selected.SamplePIDs == "" {
		t.Fatal("real edit requires exact selections and runtime receipt")
	}
	cases := []map[string]any{}
	for _, entry := range []struct {
		label  string
		worker transitionRealWorker
	}{{"python", selected.Python}, {"rust", selected.Rust}} {
		if !t.Run(entry.label, func(t *testing.T) {
			cases = append(cases, transitionRealEditOne(t, selected, entry.label, entry.worker))
		}) {
			return
		}
	}
	transitionRealFile(t, "real-case4.json", map[string]any{"schema": "cairn.semantic-real-transition-case4/1", "cases": cases, "scope": "two local held-old-result edit subcases; six other real cases and full S05 remain open"})
}

func transitionRealEditOne(t *testing.T, selected transitionRealSelection, label string, w transitionRealWorker) map[string]any {
	report := map[string]any{"implementation": label, "embed_identity": w.EmbedIdentity, "rank_identity": w.RankIdentity,
		"barrier_scope": "fixture adapter holds actual canonical CommandEmbedder result before Index receives it; real computation completed before Revise; no in-inference edit claim",
		"causal_order":  "actual old-body Document return -> held channel acknowledgement -> committed store.Revise and independent v2 read -> release channel -> Index receives old passages and returns stale Step"}
	t.Cleanup(func() { report["test_failed"] = t.Failed(); transitionRealFile(t, "real-case4-"+label+".json", report) })
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
	oldBody, newBody := "ordinary editable source é version one", "ordinary revised source é version two"
	target := d.source(t, "s05-a", oldBody)
	notes := []core.SemanticNote{target, d.source(t, "s05-b", "unchanged control é collection b"), d.source(t, "s05-c", "unchanged control é collection c")}
	before, historyBefore := d.realPopulation(t), transitionEditHistory(t, d)
	initialJobs := d.restartJobs(t)
	if len(before) != 3 || len(historyBefore) != 3 || len(initialJobs) != 3 {
		t.Fatal("wrong declared initial population")
	}
	first := transitionFailureJob(t, initialJobs, target.RecordID)
	for _, job := range initialJobs {
		if job.Attempts != 0 || job.LeaseID != nil || job.Until != nil || job.RetryAt.Before(first.RetryAt) || (job.RetryAt.Equal(first.RetryAt) && job.RecordID < first.RecordID) {
			t.Fatal("edited target is not the first ordinary due job")
		}
	}
	report["source_population_before"], report["complete_history_before"], report["jobs_before_start"] = before, historyBefore, initialJobs
	w.identities(t, notes)
	e, err := EmbeddingCommand(context.Background(), w.Embed)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(e.Close)
	b := &transitionEditBarrier{CommandEmbedder: e, oldBody: oldBody, held: make(chan transitionEditReceipt, 1), release: make(chan struct{})}
	i, err := OpenIndex(context.Background(), d.dsn, b)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(i.Close)
	ctx, cancel := context.WithCancel(context.Background())
	type stepResult struct {
		worked bool
		err    error
	}
	done := make(chan stepResult, 1)
	joined, released := false, false
	t.Cleanup(func() {
		if !released {
			close(b.release)
		}
		cancel()
		e.Close()
		if !joined {
			<-done
		}
	})
	go func() { worked, err := i.Step(ctx); done <- stepResult{worked, err} }()
	var held transitionEditReceipt
	select {
	case held = <-b.held:
	case result := <-done:
		joined = true
		report["early_Step_result"] = fmt.Sprint(result)
		t.Fatal("Step returned before old real result barrier")
	case <-time.After(60 * time.Second):
		t.Fatal("outer old-result barrier expired")
	}
	if held.Body != oldBody || held.BodySHA != target.BodySHA256 || held.Identity != i.identity || held.Identity.ModelSHA256 != w.EmbedIdentity || held.WorkerPID <= 1 || len(held.Passages) == 0 || len(held.Passages) > 256 {
		t.Fatal("held result is not the actual expected old request/identity")
	}
	for _, passage := range held.Passages {
		span := passage.Span
		if span.Offset < 0 || span.Length <= 0 || span.Offset > len(oldBody) || span.Length > len(oldBody)-span.Offset || !utf8.ValidString(oldBody[span.Offset:span.Offset+span.Length]) || len(passage.Vector) != 384 {
			t.Fatal("invalid held old-body passage")
		}
		norm := float64(0)
		for _, v := range passage.Vector {
			if math.IsNaN(float64(v)) || math.IsInf(float64(v), 0) {
				t.Fatal("nonfinite old vector")
			}
			norm += float64(v) * float64(v)
		}
		if norm == 0 {
			t.Fatal("zero old vector")
		}
	}
	report["held_old_real_result"], report["held_receipt_observed_at"] = held, time.Now()
	transitionRestartWait(t, "case4 held canonical runtime receipt", 20*time.Second, func() bool {
		var sampled map[string][]int
		data, err := os.ReadFile(selected.SamplePIDs)
		if os.IsNotExist(err) {
			return false
		}
		if err != nil || json.Unmarshal(data, &sampled) != nil {
			t.Fatal("invalid runtime receipt")
		}
		for _, pid := range sampled[label] {
			if pid == held.WorkerPID {
				report["verified_runtime_sample_pids"] = sampled
				return true
			}
		}
		return false
	})
	parent, state, err := transitionProcessStatus(held.WorkerPID)
	if err != nil || parent != os.Getpid() || state == "Z" || state == "X" {
		t.Fatal("held canonical worker is not the live direct supervisor child")
	}
	found := false
	for _, pid := range transitionRealRoots(t, w, selected.TracePrefix) {
		if pid == held.WorkerPID {
			found = true
		}
	}
	if !found {
		t.Fatal("held worker absent from canonical trace roots")
	}
	leasedJobs := d.restartJobs(t)
	leased := transitionFailureJob(t, leasedJobs, target.RecordID)
	if leased.LeaseID == nil || leased.Until == nil || !leased.Until.After(time.Now()) || leased.Attempts != 0 || !reflect.DeepEqual(before, d.realPopulation(t)) || d.count(t, `SELECT count(*) FROM cairn.s05_publication`) != 0 {
		t.Fatal("old result barrier missed live v1 work")
	}
	report["jobs_while_old_result_held"] = leasedJobs
	select {
	case <-done:
		joined = true
		t.Fatal("Index received old result before release")
	default:
	}
	d.exec(t, `UPDATE cairn.s05_phase SET phase=1`)
	revision, err := d.store.Revise(ctx, core.ReviseRequest{RequestID: uuid.NewString(), RecordID: target.RecordID, ExpectedVersion: 1, Repo: "s05-a", Body: newBody})
	if err != nil || revision.RecordID != target.RecordID || revision.Version != 2 {
		t.Fatalf("actual Revise: %+v: %v", revision, err)
	}
	report["Revise_returned_at"], report["actual_Revise_result"] = time.Now(), revision
	afterRevision, historyAfter := d.realPopulation(t), transitionEditHistory(t, d)
	if len(afterRevision) != 3 || len(historyAfter) != 4 {
		t.Fatal("revision changed current/historical denominator")
	}
	oldRetained, newVisible := false, false
	for _, row := range historyAfter {
		if row.RecordID == target.RecordID {
			if row.Version == 1 {
				for _, old := range historyBefore {
					if old.RecordID == row.RecordID {
						oldRetained = row.VersionRow == old.VersionRow && row.BodySHA == old.BodySHA
					}
				}
			}
			if row.Version == 2 {
				newVisible = row.BodySHA == transitionHash(newBody)
			}
		} else {
			matched := false
			for _, old := range historyBefore {
				if reflect.DeepEqual(row, old) {
					matched = true
				}
			}
			if !matched {
				t.Fatal("revision changed a control source/history row")
			}
		}
	}
	if !oldRetained || !newVisible {
		t.Fatal("old historical row missing/changed or committed version2 invisible")
	}
	currentV2 := false
	for _, row := range afterRevision {
		if row.RecordID == target.RecordID {
			currentV2 = row.Version == 2 && row.BodySHA == transitionHash(newBody)
		}
	}
	if !currentV2 {
		t.Fatal("version2 is not the independently observed current eligible source")
	}
	jobsRevised := d.restartJobs(t)
	newJob := transitionFailureJob(t, jobsRevised, target.RecordID)
	if len(jobsRevised) != 3 || newJob.Attempts != 0 || newJob.LeaseID != nil || newJob.Until != nil {
		t.Fatal("real revision did not leave current work pending")
	}
	for n := range jobsRevised {
		if jobsRevised[n].RecordID != leasedJobs[n].RecordID || (jobsRevised[n].RecordID != target.RecordID && !reflect.DeepEqual(jobsRevised[n], leasedJobs[n])) {
			t.Fatal("revision changed another queued source")
		}
	}
	report["source_population_after_Revise"], report["complete_history_after_Revise"], report["jobs_after_Revise"] = afterRevision, historyAfter, jobsRevised
	report["v2_independently_visible_before_release_at"] = time.Now()
	close(b.release)
	released = true
	report["old_result_release_at"] = time.Now()
	var stale stepResult
	select {
	case stale = <-done:
		joined = true
	case <-time.After(20 * time.Second):
		t.Fatal("stale Step did not join")
	}
	report["stale_Step"] = map[string]any{"worked": stale.worked, "error": fmt.Sprint(stale.err), "completed_at": time.Now()}
	if !stale.worked || stale.err != nil || d.count(t, `SELECT count(*) FROM cairn.s05_publication`) != 0 || !reflect.DeepEqual(jobsRevised, d.restartJobs(t)) {
		t.Fatal("stale result published or consumed current durable work")
	}
	report["coverage_after_stale_result"] = transitionRequireCoverage(t, d, w.EmbedIdentity, 3, 0)
	report["jobs_after_stale_result"] = d.restartJobs(t)
	report["scheduler_disposition"] = "no fixture scheduling changes; actual Revise trigger clears old lease/resets attempts/requeues current version, audited phase1"
	d.exec(t, `UPDATE cairn.s05_phase SET phase=2`)
	steps := []map[string]any{}
	for n := 0; n < 3; n++ {
		worked, err := i.Step(ctx)
		steps = append(steps, map[string]any{"worked": worked, "error": fmt.Sprint(err)})
		report["current_generation_steps"] = steps
		if !worked || err != nil {
			t.Fatalf("current recovery: %v/%v", worked, err)
		}
	}
	coverage := transitionRequireCoverage(t, d, w.EmbedIdentity, 3, 3)
	if len(coverage.Jobs) != 0 {
		t.Fatal("complete recovery left durable jobs")
	}
	report["coverage_after_recovery"] = coverage
	if !reflect.DeepEqual(afterRevision, d.realPopulation(t)) || !reflect.DeepEqual(historyAfter, transitionEditHistory(t, d)) {
		t.Fatal("recovery changed revised source population/history")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_tuple_publication WHERE phase<>2`) != 0 || d.count(t, `SELECT count(*) FROM cairn.s05_tuple_publication`) != 3 || d.count(t, `SELECT count(*) FROM (SELECT record_id FROM cairn.s05_tuple_publication GROUP BY record_id HAVING count(*)<>1) q`) != 0 {
		t.Fatal("stale/duplicate publication")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_tuple_publication p LEFT JOIN cairn.memory_record m USING(record_id) LEFT JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version WHERE p.model<>$1 OR v.version IS NULL OR p.version<>v.version OR p.body_sha256<>encode(sha256(convert_to(v.body,'UTF8')),'hex')`, w.EmbedIdentity) != 0 {
		t.Fatal("wrong published model/version/body hash")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_tuple_publication WHERE record_id=$1 AND version=2 AND body_sha256=$2`, target.RecordID, transitionHash(newBody)) != 1 {
		t.Fatal("version2 did not publish exactly once")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE operation='INSERT' AND phase>0`) != 0 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE phase=2 AND operation='UPDATE' AND old_row->>'attempts' IS DISTINCT FROM new_row->>'attempts'`) != 0 {
		t.Fatal("recovery replaced durable jobs or changed retry attempts")
	}
	report["job_audit"] = transitionEditAudit(t, d, `SELECT to_jsonb(a)::text FROM cairn.s05_job_audit a ORDER BY sequence`)
	report["publication_audit"] = transitionEditAudit(t, d, `SELECT to_jsonb(a)::text FROM cairn.s05_tuple_publication a ORDER BY record_id`)
	e.document.mu.Lock()
	same := e.document.worker != nil && e.document.worker.command.Process.Pid == held.WorkerPID
	e.document.mu.Unlock()
	if !same || len(b.calls) != 4 {
		t.Fatal("healthy old-result lane unexpectedly restarted or changed request count")
	}
	report["actual_Document_calls"], report["document_worker_reused"], report["restart_count"] = b.calls, same, 0
	report["stdout_cache_disposition"] = "actual canonical replies decoded unchanged by CommandEmbedder; old decoded passages held then rejected by Index; healthy worker/reader reused; no embedding-cache protocol; strace retains only write prefixes"
	writers := []*transitionWriter{{name: "direct Go indexer", collection: "all three collections", model: w.EmbedIdentity, restartable: true}, {name: "inactive alternate definition", collection: "s05-b", model: w.EmbedIdentity, restartable: true, joined: true}}
	if transitionFence(writers) == nil {
		t.Fatal("active writer falsely fenced")
	}
	for _, writer := range writers {
		writer.restartable = false
	}
	e.Close()
	i.Close()
	writers[0].joined = true
	transitionGone(t, transitionRealRoots(t, w, selected.TracePrefix))
	if err := transitionFence(writers); err != nil {
		t.Fatal(err)
	}
	report["writers_at_final_fence"], report["source_population_after"], report["complete_history_after"] = transitionRealWriters(writers), d.realPopulation(t), transitionEditHistory(t, d)
	w.verify(t)
	report["same_launcher_bytes_and_generation"], report["status"] = true, "PASS"
	return report
}

func transitionEditAudit(t *testing.T, d *transitionDB, query string) []json.RawMessage {
	t.Helper()
	rows, err := d.pool.Query(context.Background(), query)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	result := []json.RawMessage{}
	for rows.Next() {
		var row string
		if err := rows.Scan(&row); err != nil {
			t.Fatal(err)
		}
		result = append(result, json.RawMessage(row))
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	return result
}
