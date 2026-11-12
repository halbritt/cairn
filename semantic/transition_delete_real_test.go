package semantic

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestSemanticRealTransitionCase5(t *testing.T) {
	if os.Getenv("CAIRN_TRANSITION_REAL_CASE") != "5" {
		t.Skip("requires explicit --real-case5 retained selections")
	}
	transitionDeleteForget(t)
}

// Exercise cancellation at the real adapter's held-result boundary with the
// existing deterministic protocol child, without a database or real-model claim.
func TestSemanticHeldResultCancellation(t *testing.T) {
	w := transitionFixture(t, "held-result-cancellation")
	e, err := EmbeddingCommand(context.Background(), w.path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(e.Close)
	body := "ordinary held result cancellation"
	b := &transitionEditBarrier{CommandEmbedder: e, oldBody: body, held: make(chan transitionEditReceipt, 1), release: make(chan struct{})}
	ctx, cancel := context.WithCancel(context.Background())
	type result struct {
		passages []EmbeddedPassage
		err      error
	}
	done := make(chan result, 1)
	joined := false
	t.Cleanup(func() {
		cancel()
		close(b.release)
		if !joined {
			<-done
		}
		e.Close()
	})
	go func() { p, err := b.Document(ctx, body); done <- result{p, err} }()
	var held transitionEditReceipt
	select {
	case held = <-b.held:
	case r := <-done:
		joined = true
		t.Fatalf("real adapter did not hold result: %v", r.err)
	case <-time.After(5 * time.Second):
		t.Fatal("held-result guard expired")
	}
	if held.BodySHA != transitionHash(body) || len(held.Passages) == 0 {
		t.Fatal("adapter lost actual returned data")
	}
	cancel()
	select {
	case r := <-done:
		joined = true
		if !errors.Is(r.err, context.Canceled) || len(r.passages) != 0 {
			t.Fatalf("cancellation released held passages: %v", r.err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("canceled held result did not join")
	}
	e.Close()
	transitionGone(t, []int{held.WorkerPID})
}

func transitionRealDeleteForget(t *testing.T) {
	var selected transitionRealSelection
	data, err := os.ReadFile(os.Getenv("CAIRN_TRANSITION_REAL_SELECTION"))
	if err != nil || json.Unmarshal(data, &selected) != nil || selected.SamplePIDs == "" {
		t.Fatal("real deletion requires exact selections and runtime receipt")
	}
	cases := []map[string]any{}
	for _, entry := range []struct {
		label  string
		worker transitionRealWorker
	}{{"python", selected.Python}, {"rust", selected.Rust}} {
		for _, operation := range []string{"delete", "forget"} {
			if !t.Run(entry.label+"_"+operation, func(t *testing.T) {
				cases = append(cases, transitionRealDeleteOne(t, selected, entry.label, operation, entry.worker))
			}) {
				return
			}
		}
	}
	transitionRealFile(t, "real-case5.json", map[string]any{"schema": "cairn.semantic-real-transition-case5/1", "cases": cases, "scope": "four local Delete/Forget held-result subcases; five other real cases/full S05 remain open"})
}

func transitionDeletionRows(t *testing.T, d *transitionDB, query string, args ...any) []json.RawMessage {
	t.Helper()
	rows, err := d.pool.Query(context.Background(), query, args...)
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

func transitionDeletionSnapshot(t *testing.T, d *transitionDB, target string) map[string]any {
	t.Helper()
	return map[string]any{
		"current_eligible": d.realPopulation(t), "full_history": transitionEditHistory(t, d), "jobs": d.restartJobs(t),
		"target_record":           transitionDeletionRows(t, d, `SELECT to_jsonb(m)::text FROM cairn.memory_record m WHERE record_id=$1`, target),
		"target_versions":         transitionDeletionRows(t, d, `SELECT to_jsonb(v)::text FROM cairn.record_version v WHERE record_id=$1 ORDER BY version`, target),
		"authority_events":        transitionDeletionRows(t, d, `SELECT to_jsonb(a)::text FROM cairn.authority_event a WHERE subject_id=$1 ORDER BY resulting_version`, target),
		"record_authority":        transitionDeletionRows(t, d, `SELECT to_jsonb(a)::text FROM cairn.record_authority a WHERE record_id=$1 ORDER BY version`, target),
		"deletion_requests":       transitionDeletionRows(t, d, `SELECT to_jsonb(a)::text FROM cairn.deletion_request a WHERE record_id=$1`, target),
		"deletion_effects":        transitionDeletionRows(t, d, `SELECT to_jsonb(e)::text FROM cairn.deletion_effect e JOIN cairn.deletion_request d USING(deletion_id) WHERE d.record_id=$1 ORDER BY target_type,target_id`, target),
		"deletion_effect_history": transitionDeletionRows(t, d, `SELECT to_jsonb(e)::text FROM cairn.deletion_effect_event e JOIN cairn.deletion_request d USING(deletion_id) WHERE d.record_id=$1 ORDER BY effect_event_id`, target),
		"preview_metadata":        transitionDeletionRows(t, d, `SELECT to_jsonb(p)::text FROM cairn.retraction_preview p ORDER BY preview_id`),
		"mutation_metadata":       transitionDeletionRows(t, d, `SELECT jsonb_build_object('request_id',request_id,'operation',operation,'ordinary_deleted',ordinary_deleted,'payload_deleted_by',payload_deleted_by,'response_present',response IS NOT NULL,'response_names_target',COALESCE(jsonb_path_exists(response,'$.**.record_id ? (@ == $id)',jsonb_build_object('id',$1::text)),false),'response_sha256',encode(sha256(convert_to(COALESCE(response::text,''),'UTF8')),'hex'))::text FROM cairn.mutation_request ORDER BY operation,request_id`, target),
	}
}

func transitionDeletionSearch(t *testing.T, i *Index, notes []core.SemanticNote, target, model string, want int) map[string]any {
	t.Helper()
	started := time.Now()
	r, err := i.Search(context.Background(), core.SemanticRankRequest{Query: "ordinary source control", Notes: notes})
	completed := time.Now()
	if err != nil || r.ModelSHA256 != model || r.Indexed != want || len(r.Hits) != want {
		t.Fatalf("post-commit real search: indexed=%d hits=%d want=%d error=%v", r.Indexed, len(r.Hits), want, err)
	}
	seen := map[string]bool{}
	for _, hit := range r.Hits {
		if hit.RecordID == target || seen[hit.RecordID] {
			t.Fatal("excluded/duplicate source returned by post-commit retrieval")
		}
		seen[hit.RecordID] = true
		found := false
		for _, n := range notes {
			if n.RecordID == hit.RecordID && n.RecordID != target && n.Version == hit.Version && n.BodySHA256 == hit.BodySHA256 {
				found = true
			}
		}
		if !found {
			t.Fatal("retrieval returned a stale/unexpected positive control")
		}
	}
	if want == 2 {
		for _, n := range notes {
			if n.RecordID != target && !seen[n.RecordID] {
				t.Fatal("nonempty positive control missing from actual retrieval")
			}
		}
	}
	return map[string]any{"started_at": started, "completed_at": completed, "original_candidate_count": len(notes), "candidate_set_includes_preoperation_target": true, "result": r, "error": fmt.Sprint(err), "scope": "fresh direct Index.Search after commit with stale original candidate set; no recall of already-returned content or precommit in-flight requests; no full API claim"}
}

func transitionRealDeleteOne(t *testing.T, selected transitionRealSelection, label, operation string, w transitionRealWorker) map[string]any {
	report := map[string]any{"implementation": label, "operation": operation, "embed_identity": w.EmbedIdentity, "rank_identity": w.RankIdentity,
		"barrier_scope": "unchanged case4 concrete CommandEmbedder adapter holds actual canonical result after computation while Index waits; not an in-inference operation",
		"causal_order":  "old canonical Document result held -> real Delete/Forget commit -> independently observed exclusion -> release old result -> stale Step -> normal controls recovery/post-commit retrieval"}
	t.Cleanup(func() {
		report["test_failed"] = t.Failed()
		transitionRealFile(t, "real-case5-"+label+"-"+operation+".json", report)
	})
	d := transitionDatabase(t)
	d.exec(t, `CREATE TABLE cairn.s05_tuple_publication (phase integer,record_id uuid,version integer,body_sha256 text,model text);
 CREATE FUNCTION cairn.s05_observe_tuple() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN INSERT INTO cairn.s05_tuple_publication SELECT phase,NEW.record_id,NEW.version,NEW.body_sha256,NEW.model_sha256 FROM cairn.s05_phase; RETURN NEW; END $$;
 CREATE TRIGGER s05_observe_tuple AFTER INSERT ON cairn.semantic_document FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_tuple();
 CREATE TABLE cairn.s05_job_audit (sequence bigserial PRIMARY KEY,phase integer,operation text,record_id uuid,old_row jsonb,new_row jsonb);
 CREATE FUNCTION cairn.s05_observe_job() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN INSERT INTO cairn.s05_job_audit(phase,operation,record_id,old_row,new_row) SELECT phase,TG_OP,COALESCE(NEW.record_id,OLD.record_id),to_jsonb(OLD),to_jsonb(NEW) FROM cairn.s05_phase; RETURN NULL; END $$;
 CREATE TRIGGER s05_observe_job AFTER INSERT OR UPDATE OR DELETE ON cairn.semantic_job FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_job();
 CREATE TABLE cairn.s05_source_audit (sequence bigserial PRIMARY KEY,phase integer,table_name text,operation text,record_id uuid,old_row jsonb,new_row jsonb);
 CREATE FUNCTION cairn.s05_observe_source() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN INSERT INTO cairn.s05_source_audit(phase,table_name,operation,record_id,old_row,new_row) SELECT phase,TG_TABLE_NAME,TG_OP,COALESCE(NEW.record_id,OLD.record_id),to_jsonb(OLD),to_jsonb(NEW) FROM cairn.s05_phase; RETURN NULL; END $$;
 CREATE TRIGGER s05_observe_record AFTER INSERT OR UPDATE OR DELETE ON cairn.memory_record FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_source();
 CREATE TRIGGER s05_observe_version AFTER INSERT OR UPDATE OR DELETE ON cairn.record_version FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_source();`)
	body := "ordinary excluded target source é"
	target := d.source(t, "s05-a", body)
	notes := []core.SemanticNote{target, d.source(t, "s05-b", "ordinary positive control é collection b"), d.source(t, "s05-c", "ordinary positive control é collection c")}
	before, historyBefore := d.realPopulation(t), transitionEditHistory(t, d)
	jobsBefore := d.restartJobs(t)
	first := transitionFailureJob(t, jobsBefore, target.RecordID)
	if len(before) != 3 || len(historyBefore) != 3 || len(jobsBefore) != 3 {
		t.Fatal("wrong declared original population")
	}
	for _, j := range jobsBefore {
		if j.Attempts != 0 || j.LeaseID != nil || j.Until != nil || j.RetryAt.Before(first.RetryAt) || (j.RetryAt.Equal(first.RetryAt) && j.RecordID < first.RecordID) {
			t.Fatal("target is not first ordinary due work")
		}
	}
	report["original_snapshot"] = transitionDeletionSnapshot(t, d, target.RecordID)
	report["original_target"], report["controls"] = target, notes[1:]
	var createRequestID string
	if err := d.pool.QueryRow(context.Background(), `SELECT request_id::text FROM cairn.mutation_request WHERE operation='create' AND jsonb_path_exists(response,'$.**.record_id ? (@ == $id)',jsonb_build_object('id',$1::text))`, target.RecordID).Scan(&createRequestID); err != nil {
		t.Fatal(err)
	}
	report["target_create_request_id"] = createRequestID
	w.identities(t, notes)
	e, err := EmbeddingCommand(context.Background(), w.Embed)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(e.Close)
	b := &transitionEditBarrier{CommandEmbedder: e, oldBody: body, held: make(chan transitionEditReceipt, 1), release: make(chan struct{})}
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
	case r := <-done:
		joined = true
		t.Fatalf("Step returned before canonical result held: %v", r.err)
	case <-time.After(60 * time.Second):
		t.Fatal("outer held-result observation expired")
	}
	if held.Body != body || held.BodySHA != target.BodySHA256 || held.Identity != i.identity || held.Identity.ModelSHA256 != w.EmbedIdentity || held.WorkerPID <= 1 || len(held.Passages) == 0 {
		t.Fatal("held result does not bind original canonical request/identity")
	}
	report["held_old_real_result"], report["held_receipt_observed_at"] = held, time.Now()
	transitionRestartWait(t, "case5 canonical runtime receipt", 20*time.Second, func() bool {
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
		t.Fatal("held worker is not live owned direct child")
	}
	found := false
	for _, pid := range transitionRealRoots(t, w, selected.TracePrefix) {
		if pid == held.WorkerPID {
			found = true
		}
	}
	if !found {
		t.Fatal("canonical held PID absent from trace roots")
	}
	leasedJobs := d.restartJobs(t)
	leased := transitionFailureJob(t, leasedJobs, target.RecordID)
	if leased.LeaseID == nil || leased.Until == nil || !leased.Until.After(time.Now()) || leased.Attempts != 0 || !reflect.DeepEqual(before, d.realPopulation(t)) || d.count(t, `SELECT count(*) FROM cairn.s05_publication`) != 0 {
		t.Fatal("old result did not hold live original work")
	}
	report["snapshot_while_result_held"] = transitionDeletionSnapshot(t, d, target.RecordID)
	select {
	case <-done:
		joined = true
		t.Fatal("old result escaped before exclusion")
	default:
	}
	d.exec(t, `UPDATE cairn.s05_phase SET phase=1`)
	if operation == "delete" {
		result, err := d.store.Delete(ctx, core.DeleteRequest{RequestID: uuid.NewString(), RecordID: target.RecordID, ExpectedVersion: 1})
		if err != nil || result.RecordID != target.RecordID || result.DeletedVersion != 1 {
			t.Fatalf("actual Delete: %+v: %v", result, err)
		}
		report["actual_Delete_result"] = result
	} else {
		transitionForget(t, d, target)
	}
	report["operation_returned_at"] = time.Now()
	after := d.realPopulation(t)
	historyAfter := transitionEditHistory(t, d)
	jobsAfter := d.restartJobs(t)
	if len(after) != 2 || len(jobsAfter) != 2 {
		t.Fatal("exclusion lost the declared controls or retained target work")
	}
	for _, r := range after {
		if r.RecordID == target.RecordID {
			t.Fatal("target remains eligible")
		}
	}
	for _, old := range historyBefore {
		if old.RecordID == target.RecordID {
			continue
		}
		matched := false
		for _, r := range historyAfter {
			if reflect.DeepEqual(old, r) {
				matched = true
			}
		}
		if !matched {
			t.Fatal("exclusion changed a control source/history row")
		}
	}
	for _, j := range jobsAfter {
		if j.RecordID == target.RecordID {
			t.Fatal("excluded target job remains")
		}
		matched := false
		for _, old := range leasedJobs {
			if reflect.DeepEqual(old, j) {
				matched = true
			}
		}
		if !matched {
			t.Fatal("exclusion changed control queue/retry state")
		}
	}
	_, getErr := d.store.Get(ctx, target.RecordID)
	var failure *core.Error
	if !errors.As(getErr, &failure) {
		t.Fatalf("excluded target remained publicly readable: %v", getErr)
	}
	if operation == "delete" {
		if d.count(t, `SELECT count(*) FROM cairn.mutation_request WHERE request_id=$1 AND ordinary_deleted AND response IS NULL`, createRequestID) != 1 {
			t.Fatal("ordinary Delete retained its cached create payload")
		}
		if failure.Code != "NOT_FOUND" || len(historyAfter) != 2 || d.count(t, `SELECT count(*) FROM cairn.memory_record WHERE record_id=$1`, target.RecordID) != 0 || d.count(t, `SELECT count(*) FROM cairn.record_version WHERE record_id=$1`, target.RecordID) != 0 || d.count(t, `SELECT count(*) FROM cairn.deletion_request WHERE record_id=$1`, target.RecordID) != 0 {
			t.Fatal("ordinary Delete did not remove target without a tombstone/deletion request")
		}
		report["operation_semantics"] = "ordinary active-A deletion removed target record/versions; no tombstone or Forget request; captured original source/audit retained only by this fixture; no physical-erasure claim"
	} else {
		if failure.Code != "PAYLOAD_UNAVAILABLE" || len(historyAfter) != 4 || d.count(t, `SELECT count(*) FROM cairn.memory_record WHERE record_id=$1 AND lifecycle='tombstoned' AND current_version=2`, target.RecordID) != 1 || d.count(t, `SELECT count(*) FROM cairn.record_version WHERE record_id=$1 AND payload_deleted_by IS NOT NULL`, target.RecordID) != 2 || d.count(t, `SELECT count(*) FROM cairn.authority_event e JOIN cairn.record_authority a USING(event_id) WHERE a.record_id=$1 AND a.version=2 AND e.event_type='forget'`, target.RecordID) != 1 {
			t.Fatal("Forget tombstone/logical payload exclusion/audit mismatch")
		}
		var deletionID string
		if err := d.pool.QueryRow(ctx, `SELECT deletion_id::text FROM cairn.deletion_request WHERE record_id=$1`, target.RecordID).Scan(&deletionID); err != nil {
			t.Fatal(err)
		}
		if d.count(t, `SELECT count(*) FROM cairn.mutation_request WHERE request_id=$1 AND payload_deleted_by=$2`, createRequestID, deletionID) != 1 {
			t.Fatal("Forget did not logically exclude its cached create payload")
		}
		op, err := core.Open(ctx, d.dsn, core.Channel{Principal: "s05-disposable-operator", Operator: true})
		if err != nil {
			t.Fatal(err)
		}
		deletion, err := op.DeletionStatus(ctx, deletionID)
		op.Close()
		if err != nil || deletion.RecordID != target.RecordID || deletion.State != "partial" {
			t.Fatalf("actual DeletionStatus: %+v: %v", deletion, err)
		}
		pending, residual := 0, 0
		for _, effect := range deletion.Effects {
			if effect.Attempts != 0 || effect.CompletedAt != nil {
				t.Fatal("unexpected purge effect execution")
			}
			switch effect.Status {
			case "pending":
				pending++
			case "not_possible":
				if effect.Residual == "" {
					t.Fatal("unlabelled erasure residual")
				}
				residual++
			default:
				t.Fatal("unexpected deletion effect status")
			}
		}
		if pending != 2 || residual != 3 {
			t.Fatal("unexpected synthetic Forget inventory")
		}
		report["actual_DeletionStatus"] = deletion
		report["operation_semantics"] = "operator Bootstrap/PreviewDeletion/Forget committed v2 tombstone with identity/audit retained and all target payloads logically excluded; two database effects pending plus three explicit residuals; no purge invoked or physical/provider erasure established"
	}
	report["public_Get_error_code"] = failure.Code
	if d.count(t, `SELECT count(*) FROM cairn.semantic_document WHERE record_id=$1`, target.RecordID) != 0 || d.count(t, `SELECT count(*) FROM cairn.semantic_passage WHERE record_id=$1`, target.RecordID) != 0 {
		t.Fatal("target semantic payload survived exclusion")
	}
	report["snapshot_after_commit_before_release"] = transitionDeletionSnapshot(t, d, target.RecordID)
	report["exclusion_independently_observed_at"] = time.Now()
	report["post_commit_search_before_release"] = transitionDeletionSearch(t, i, notes, target.RecordID, w.EmbedIdentity, 0)
	close(b.release)
	released = true
	report["old_result_release_at"] = time.Now()
	var stale stepResult
	select {
	case stale = <-done:
		joined = true
	case <-time.After(20 * time.Second):
		t.Fatal("excluded stale Step did not join")
	}
	report["stale_Step"] = map[string]any{"worked": stale.worked, "error": fmt.Sprint(stale.err), "completed_at": time.Now()}
	if !stale.worked || stale.err != nil || !reflect.DeepEqual(jobsAfter, d.restartJobs(t)) || d.count(t, `SELECT count(*) FROM cairn.s05_publication`) != 0 || d.count(t, `SELECT count(*) FROM cairn.semantic_document WHERE record_id=$1`, target.RecordID) != 0 || d.count(t, `SELECT count(*) FROM cairn.semantic_job WHERE record_id=$1`, target.RecordID) != 0 {
		t.Fatal("old result resurrected excluded target or consumed control work")
	}
	report["coverage_after_stale_result"] = transitionRequireCoverage(t, d, w.EmbedIdentity, 2, 0)
	report["snapshot_after_stale_result"] = transitionDeletionSnapshot(t, d, target.RecordID)
	d.exec(t, `UPDATE cairn.s05_phase SET phase=2`)
	steps := []map[string]any{}
	for n := 0; n < 2; n++ {
		worked, err := i.Step(ctx)
		steps = append(steps, map[string]any{"worked": worked, "error": fmt.Sprint(err)})
		report["control_recovery_steps"] = steps
		if !worked || err != nil {
			t.Fatalf("normal control recovery: %v/%v", worked, err)
		}
	}
	coverage := transitionRequireCoverage(t, d, w.EmbedIdentity, 2, 2)
	if len(coverage.Jobs) != 0 {
		t.Fatal("complete controls left pending work")
	}
	report["coverage_after_recovery"] = coverage
	if !reflect.DeepEqual(after, d.realPopulation(t)) || !reflect.DeepEqual(historyAfter, transitionEditHistory(t, d)) {
		t.Fatal("control recovery changed source/lifecycle/history")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_tuple_publication`) != 2 || d.count(t, `SELECT count(*) FROM cairn.s05_tuple_publication WHERE phase<>2 OR record_id=$1`, target.RecordID) != 0 || d.count(t, `SELECT count(*) FROM (SELECT record_id FROM cairn.s05_tuple_publication GROUP BY record_id HAVING count(*)<>1) q`) != 0 {
		t.Fatal("stale/duplicate/missing control publication")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_tuple_publication p LEFT JOIN cairn.memory_record m USING(record_id) LEFT JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version WHERE p.model<>$1 OR v.version IS NULL OR p.version<>v.version OR p.body_sha256<>encode(sha256(convert_to(v.body,'UTF8')),'hex')`, w.EmbedIdentity) != 0 {
		t.Fatal("wrong control model/version/body hash")
	}
	if d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE phase>0 AND operation='INSERT'`) != 0 || d.count(t, `SELECT count(*) FROM cairn.s05_job_audit WHERE phase=2 AND operation='UPDATE' AND old_row->>'attempts' IS DISTINCT FROM new_row->>'attempts'`) != 0 {
		t.Fatal("recovery manufactured work or changed retry attempts")
	}
	report["post_commit_search_after_recovery"] = transitionDeletionSearch(t, i, notes, target.RecordID, w.EmbedIdentity, 2)
	report["source_audit"] = transitionDeletionRows(t, d, `SELECT to_jsonb(a)::text FROM cairn.s05_source_audit a ORDER BY sequence`)
	report["job_audit"] = transitionDeletionRows(t, d, `SELECT to_jsonb(a)::text FROM cairn.s05_job_audit a ORDER BY sequence`)
	report["publication_audit"] = transitionDeletionRows(t, d, `SELECT to_jsonb(a)::text FROM cairn.s05_tuple_publication a ORDER BY record_id`)
	e.document.mu.Lock()
	same := e.document.worker != nil && e.document.worker.command.Process.Pid == held.WorkerPID
	e.document.mu.Unlock()
	if !same || len(b.calls) != 3 {
		t.Fatal("healthy canonical document lane unexpectedly restarted or changed call count")
	}
	report["actual_Document_calls"], report["document_worker_reused"], report["restart_count"] = b.calls, same, 0
	report["scheduler_disposition"] = "no fixture scheduler/job/attempt changes; actual Delete cascade or Forget exclusion removed only target job; normal due control work continued"
	report["stdout_cache_disposition"] = "real old decoded result held then ignored by Index after exclusion; healthy process/reader reused; no embedding-cache protocol; raw stdout evidence is strace write prefixes"
	writers := []*transitionWriter{{name: "direct Go indexer", collection: "three original collections/two eligible controls", model: w.EmbedIdentity, restartable: true}, {name: "inactive alternate definition", collection: "s05-b", model: w.EmbedIdentity, restartable: true, joined: true}}
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
	report["writers_at_final_fence"], report["final_snapshot"] = transitionRealWriters(writers), transitionDeletionSnapshot(t, d, target.RecordID)
	w.verify(t)
	report["same_launcher_bytes_and_generation"], report["status"] = true, "PASS"
	return report
}
