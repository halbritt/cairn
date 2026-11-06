package core

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/advice"
)

func reviewAdviceJob(t *testing.T, ctx context.Context, s *Store) *WorkAdviceJob {
	t.Helper()
	binding, item := uuid.New(), uuid.New()
	seedWorkAdviceTestEntities(t, ctx, s, binding, item)
	return &WorkAdviceJob{
		BindingID: binding, PlaneItemUUID: item, RequestUUID: uuid.New(),
		ProfileName: "review-subscription", ModelRoute: "review-model-a", TemplateVersion: 1,
		InputPayloadDigest: "review-selected-input", InputManifestDigest: "review-manifest",
	}
}

// This boundary provider counts one invocation and honors the real launch gate.
// It does not claim to start a native process or contact a model.
type reviewAdviceProvider struct{ countedAdviceProvider }

func (p *reviewAdviceProvider) GenerateAdvice(ctx context.Context, snapshot *advice.Snapshot, deadline time.Duration, onLaunch func(advice.ProcessIdentity) error) (*advice.Proposal, *advice.ProcessResult, error) {
	p.callCount.Add(1)
	identity := advice.ProcessIdentity{PID: 9999, PGID: 9999, StartTicks: 50, BootID: "review-synthetic-boot"}
	if onLaunch != nil {
		if err := onLaunch(identity); err != nil {
			return nil, nil, err
		}
	}
	return p.proposal, &advice.ProcessResult{Stdout: []byte(`{"actions":[]}`), ExitCode: 0, Identity: identity}, nil
}

// A real INSERT trigger holds each job after admission has checked capacity.
// Distinct items must share the host fence; per-item locks cannot protect it.
func TestWorkAdviceReviewHostAdmissionAcrossItems(t *testing.T) {
	s := restoreTestStore(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := s.SetAdviceFeature(ctx, "active"); err != nil {
		t.Fatal(err)
	}
	jobs := []*WorkAdviceJob{reviewAdviceJob(t, ctx, s), reviewAdviceJob(t, ctx, s), reviewAdviceJob(t, ctx, s)}
	gateName := "advice-host-admission-" + uuid.NewString()
	gate, err := s.pool.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer gate.Release()
	if _, err := gate.Exec(ctx, `SELECT pg_advisory_lock(hashtextextended($1,0))`, gateName); err != nil {
		t.Fatal(err)
	}
	defer gate.Exec(context.Background(), `SELECT pg_advisory_unlock(hashtextextended($1,0))`, gateName)
	if _, err := s.pool.Exec(ctx, fmt.Sprintf(`
CREATE FUNCTION cairn.review_pause_advice_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN PERFORM pg_advisory_xact_lock(hashtextextended('%s',0)); RETURN NEW; END $$;
CREATE TRIGGER review_pause_advice_insert BEFORE INSERT ON cairn.work_advice_job
FOR EACH ROW EXECUTE FUNCTION cairn.review_pause_advice_insert()`, gateName)); err != nil {
		t.Fatal(err)
	}
	results := make(chan error, len(jobs))
	for _, job := range jobs {
		go func(job *WorkAdviceJob) { results <- s.CreateAdviceJob(ctx, job) }(job)
	}
	for {
		var blocked int
		if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM pg_stat_activity
WHERE datname=current_database() AND state='active' AND cardinality(pg_blocking_pids(pid))>0`).Scan(&blocked); err != nil {
			t.Fatal(err)
		}
		if blocked == len(jobs) {
			break
		}
		select {
		case err := <-results:
			t.Fatalf("admission ended before all contenders reached the fence: %v", err)
		case <-ctx.Done():
			t.Fatal("contenders did not reach the insertion or shared admission fence: ", ctx.Err())
		case <-time.After(5 * time.Millisecond):
		}
	}
	if _, err := gate.Exec(ctx, `SELECT pg_advisory_unlock(hashtextextended($1,0))`, gateName); err != nil {
		t.Fatal(err)
	}
	succeeded, refused := 0, 0
	for range jobs {
		select {
		case err := <-results:
			if err == nil {
				succeeded++
			} else if Code(err) == "INFLIGHT_LIMIT" {
				refused++
			} else {
				t.Fatalf("unexpected admission error: %v", err)
			}
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		}
	}
	if succeeded != 2 || refused != 1 {
		t.Fatalf("host capacity must admit exactly two of three distinct items; admitted=%d refused=%d", succeeded, refused)
	}
}

func TestWorkAdviceReviewChangedModelIntentRefused(t *testing.T) {
	s := restoreTestStore(t)
	ctx := context.Background()
	if err := s.SetAdviceFeature(ctx, "active"); err != nil {
		t.Fatal(err)
	}
	job := reviewAdviceJob(t, ctx, s)
	if err := s.CreateAdviceJob(ctx, job); err != nil {
		t.Fatal(err)
	}
	retry := *job
	retry.ModelRoute = "review-model-b"
	requireCode(t, s.CreateAdviceJob(ctx, &retry), "IDEMPOTENCY_CONFLICT")
}

func TestWorkAdviceReviewTerminalPersistenceFailureIsReported(t *testing.T) {
	s := restoreTestStore(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := s.SetAdviceFeature(ctx, "active"); err != nil {
		t.Fatal(err)
	}
	job := reviewAdviceJob(t, ctx, s)
	if _, err := s.pool.Exec(ctx, `
CREATE SEQUENCE cairn.review_advice_completion_hits;
CREATE FUNCTION cairn.review_fail_advice_completion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
IF NEW.status='completed' THEN
PERFORM nextval('cairn.review_advice_completion_hits');
RAISE EXCEPTION 'review completion write failure';
END IF;
RETURN NEW;
END $$;
CREATE TRIGGER review_fail_advice_completion BEFORE UPDATE OF status ON cairn.work_advice_job
FOR EACH ROW EXECUTE FUNCTION cairn.review_fail_advice_completion()`); err != nil {
		t.Fatal(err)
	}
	provider := &reviewAdviceProvider{countedAdviceProvider{proposal: &advice.Proposal{Actions: []advice.ProposedAction{}}}}
	_, _, err := s.ExecuteAdviceJob(ctx, job, AdviceInputSpec{BindingID: job.BindingID, PlaneItemUUID: job.PlaneItemUUID}, provider)
	if provider.callCount.Load() != 1 {
		t.Fatalf("fixture must reach exactly one provider invocation; calls=%d error=%v", provider.callCount.Load(), err)
	}
	var fired int
	if scanErr := s.pool.QueryRow(ctx, `SELECT CASE WHEN is_called THEN last_value ELSE 0 END
FROM cairn.review_advice_completion_hits`).Scan(&fired); scanErr != nil {
		t.Fatal(scanErr)
	}
	if fired != 1 {
		t.Fatalf("fixture must reach the terminal write exactly once; trigger fires=%d error=%v", fired, err)
	}
	if err == nil {
		t.Fatal("generation reported success after the terminal job write failed")
	}
	if !strings.Contains(err.Error(), "review completion write failure") {
		t.Fatalf("terminal write error was not propagated: %v", err)
	}
	var proposals int
	if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.work_advice_proposal WHERE job_uuid=$1`, job.JobUUID).Scan(&proposals); err != nil {
		t.Fatal(err)
	}
	if proposals != 0 {
		t.Fatalf("failed atomic completion retained %d proposal rows", proposals)
	}
}
