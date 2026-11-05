package core

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/google/uuid"
)

// The trigger pauses a real admission after its feature check, before the new
// project is visible to a retirement count. This exercises both Store entry
// points; a sleep cannot establish the required transaction ordering.
func TestWorkFeatureRetirementSerializesProjectAdmission(t *testing.T) {
	s := restoreTestStore(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "active",
	}); err != nil {
		t.Fatal(err)
	}
	gateName := "work-admission-review-" + uuid.NewString()
	gate, err := s.pool.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer gate.Release()
	if _, err := gate.Exec(ctx, `SELECT pg_advisory_lock(hashtextextended($1,0))`, gateName); err != nil {
		t.Fatal(err)
	}
	defer gate.Exec(context.Background(), `SELECT pg_advisory_unlock(hashtextextended($1,0))`, gateName)
	triggerSQL := fmt.Sprintf(`
CREATE FUNCTION cairn.review_pause_work_admission() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN PERFORM pg_advisory_xact_lock(hashtextextended('%s',0)); RETURN NEW; END $$;
CREATE TRIGGER review_pause_work_admission BEFORE INSERT ON cairn.work_project
FOR EACH ROW EXECUTE FUNCTION cairn.review_pause_work_admission()`, gateName)
	if _, err := s.pool.Exec(ctx, triggerSQL); err != nil {
		t.Fatal(err)
	}
	type addResult struct {
		project WorkProject
		err     error
	}
	added := make(chan addResult, 1)
	go func() {
		p, err := s.AddWorkProject(ctx, AddWorkProjectRequest{
			RequestID: uuid.NewString(), LocalSlug: "retirement-race", Collection: "fixture:work-race",
			PlaneInstance: "work", PlaneOrigin: "https://plane.example.com",
			WorkspaceUUID: uuid.New(), ProjectUUID: uuid.New(), ConfigRevision: "fixture-config-v1",
		})
		added <- addResult{p, err}
	}()
	var admissionPID int
	for admissionPID == 0 {
		if err := s.pool.QueryRow(ctx, `SELECT COALESCE((SELECT pid FROM pg_locks
WHERE locktype='advisory' AND NOT granted
AND classid=((hashtextextended($1,0)>>32)&4294967295)::oid
AND objid=(hashtextextended($1,0)&4294967295)::oid LIMIT 1),0)`, gateName).Scan(&admissionPID); err != nil {
			t.Fatal(err)
		}
		if admissionPID != 0 {
			break
		}
		select {
		case result := <-added:
			t.Fatalf("admission ended before reaching the insertion gate: %v", result.err)
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		case <-time.After(5 * time.Millisecond):
		}
	}
	retired := make(chan error, 1)
	go func() {
		retired <- s.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
			RequestID: uuid.NewString(), FeatureName: "work-view/1", Status: "retired",
		})
	}()
	var retirementErr error
	retirementFinished := false
	for {
		select {
		case retirementErr = <-retired:
			retirementFinished = true
		default:
		}
		if retirementFinished {
			break
		}
		var blocked bool
		if err := s.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity
WHERE datname=current_database() AND $1::integer=ANY(pg_blocking_pids(pid)))`, admissionPID).Scan(&blocked); err != nil {
			t.Fatal(err)
		}
		if blocked {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("retirement neither finished nor reached the admission fence: ", ctx.Err())
		case <-time.After(5 * time.Millisecond):
		}
	}
	if _, err := gate.Exec(ctx, `SELECT pg_advisory_unlock(hashtextextended($1,0))`, gateName); err != nil {
		t.Fatal(err)
	}
	var result addResult
	select {
	case result = <-added:
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	if result.err != nil {
		t.Fatalf("first admission must complete: %v", result.err)
	}
	if !retirementFinished {
		select {
		case retirementErr = <-retired:
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		}
	}
	if retirementErr == nil {
		t.Fatal("retirement committed while a previously admitted active project was still inserting")
	}
	requireCode(t, retirementErr, "INVALID_REQUEST")
	status, err := s.GetWorkFeatureStatus(ctx, "work-view/1")
	if err != nil || status != "active" {
		t.Fatalf("failed retirement changed feature status: %q, %v", status, err)
	}
	project, err := s.GetWorkProject(ctx, "fixture:work-race", "retirement-race")
	if err != nil || project.BindingID != result.project.BindingID || project.Status != "active" {
		t.Fatalf("admitted project was lost: %+v, %v", project, err)
	}
}
