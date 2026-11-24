package semantic

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5/pgxpool"
)

type orderEmbedder struct {
	fixtureEmbedder
	bodies []string
}

func (e *orderEmbedder) Document(ctx context.Context, body string) ([]EmbeddedPassage, error) {
	e.bodies = append(e.bodies, body)
	return e.fixtureEmbedder.Document(ctx, body)
}

func drainIndex(t *testing.T, ctx context.Context, idx *Index) {
	t.Helper()
	for n := 0; n < 100000; n++ {
		worked, err := idx.Step(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if !worked {
			return
		}
	}
	t.Fatal("index did not drain")
}

func searchable(t *testing.T, ctx context.Context, idx *Index, id string, version int, body string) bool {
	t.Helper()
	hash := sha256.Sum256([]byte(body))
	result, err := idx.Search(ctx, core.SemanticRankRequest{Query: "source", Notes: []core.SemanticNote{{RecordID: id, Version: version, BodySHA256: hex.EncodeToString(hash[:]), Body: body}}})
	if err != nil {
		t.Fatal(err)
	}
	return result.Indexed == 1 && len(result.Hits) == 1 && result.Hits[0].RecordID == id
}

// The operator requeue re-derives every current source through the indexer's
// own job path: documents stay searchable until replaced, a repeat adds only
// absent jobs, edits are claimed before refreshes, and an interrupted pass
// resumes after restart to a complete index.
func TestSemanticRequeueRederivesInPlaceAndResumesAfterInterruption(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires disposable PostgreSQL")
	}
	ctx := context.Background()
	writer, err := core.Open(ctx, dsn, core.Channel{Principal: "requeue-fixture"})
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if err = writer.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	operator, err := core.Open(ctx, dsn, core.Channel{Principal: "requeue-operator", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	defer operator.Close()
	scoped, err := core.Open(ctx, dsn, core.Channel{Principal: "requeue-scoped", Operator: true, Repo: "requeue-repo"})
	if err != nil {
		t.Fatal(err)
	}
	defer scoped.Close()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()

	repo := uuid.NewString()
	type source struct{ id, body string }
	var sources []source
	var ids []string
	for n := 0; n < 3; n++ {
		body := "requeue source " + uuid.NewString()
		r, err := writer.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "lesson", Body: body, Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}})
		if err != nil {
			t.Fatal(err)
		}
		sources = append(sources, source{r.RecordID, body})
		ids = append(ids, r.RecordID)
	}
	idx, err := OpenIndex(ctx, dsn, &fixtureEmbedder{})
	if err != nil {
		t.Fatal(err)
	}
	drainIndex(t, ctx, idx)
	var before time.Time
	if err = pool.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&before); err != nil {
		t.Fatal(err)
	}

	// Only an unscoped operator may queue; the reason is required.
	reason := "full semantic rebuild under the selected identity"
	for _, refused := range []struct {
		store *core.Store
		req   core.RequeueSemanticRequest
		code  string
	}{
		{writer, core.RequeueSemanticRequest{RequestID: uuid.NewString(), Reason: reason}, "AUTHORITY_DENIED"},
		{scoped, core.RequeueSemanticRequest{RequestID: uuid.NewString(), Reason: reason}, "AUTHORITY_DENIED"},
		{operator, core.RequeueSemanticRequest{RequestID: uuid.NewString(), Reason: "short"}, "INVALID_REQUEST"},
	} {
		_, err := refused.store.RequeueSemantic(ctx, refused.req)
		var coreErr *core.Error
		if !errors.As(err, &coreErr) || coreErr.Code != refused.code {
			t.Fatalf("requeue refusal: want %s, got %v", refused.code, err)
		}
	}
	var jobs int
	if err = pool.QueryRow(ctx, `SELECT count(*) FROM cairn.semantic_job WHERE record_id=ANY($1::uuid[])`, ids).Scan(&jobs); err != nil || jobs != 0 {
		t.Fatalf("refused requeue queued jobs: %d %v", jobs, err)
	}

	first := core.RequeueSemanticRequest{RequestID: uuid.NewString(), Reason: reason}
	queued, err := operator.RequeueSemantic(ctx, first)
	if err != nil || queued.Queued < 3 || queued.Queued+queued.AlreadyQueued != queued.Eligible {
		t.Fatalf("first requeue: %+v %v", queued, err)
	}
	// The same request replays its committed result; a new request adds nothing
	// while every job is still pending.
	replay, err := operator.RequeueSemantic(ctx, first)
	if err != nil || replay != queued {
		t.Fatalf("requeue replay: %+v %v", replay, err)
	}
	again, err := operator.RequeueSemantic(ctx, core.RequeueSemanticRequest{RequestID: uuid.NewString(), Reason: reason})
	if err != nil || again.Eligible != queued.Eligible || again.Queued != 0 || again.AlreadyQueued != again.Eligible {
		t.Fatalf("repeated requeue: %+v %v", again, err)
	}
	for _, s := range sources {
		if !searchable(t, ctx, idx, s.id, 1, s.body) {
			t.Fatalf("queued source lost its searchable document: %s", s.id)
		}
	}

	// An edit after the requeue has no document, so it is claimed before every
	// refresh even though its job is newest.
	edited := "requeue edited " + uuid.NewString()
	if _, err = writer.Revise(ctx, core.ReviseRequest{RequestID: uuid.NewString(), RecordID: sources[0].id, ExpectedVersion: 1, Repo: repo, Body: edited}); err != nil {
		t.Fatal(err)
	}
	ordered := &orderEmbedder{}
	idx.Close()
	idx, err = OpenIndex(ctx, dsn, ordered)
	if err != nil {
		t.Fatal(err)
	}
	if worked, err := idx.Step(ctx); err != nil || !worked || len(ordered.bodies) != 1 || ordered.bodies[0] != edited {
		t.Fatalf("edit was not claimed first: worked=%v err=%v bodies=%q", worked, err, ordered.bodies)
	}
	if !searchable(t, ctx, idx, sources[0].id, 2, edited) {
		t.Fatal("edited source not searchable after its claim")
	}

	// Interrupt a refresh while it embeds: the worker's context ends, the lease
	// is left behind as after a crash, and the old document stays searchable.
	victim := sources[1]
	blocking := &delayedEmbedder{target: victim.body, started: make(chan struct{}), release: make(chan struct{})}
	idx.Close()
	idx, err = OpenIndex(ctx, dsn, blocking)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = pool.Exec(ctx, `UPDATE cairn.semantic_job SET retry_at=clock_timestamp()+interval '1 hour' WHERE record_id<>$1`, victim.id); err != nil {
		t.Fatal(err) // fixture-only: make the victim the next due job
	}
	stepCtx, cancel := context.WithCancel(ctx)
	done := make(chan error, 1)
	go func() {
		_, err := idx.Step(stepCtx)
		done <- err
	}()
	select {
	case <-blocking.started:
	case <-time.After(3 * time.Second):
		t.Fatal("refresh did not begin")
	}
	if !searchable(t, ctx, idx, victim.id, 1, victim.body) {
		t.Fatal("document unsearchable while its replacement is computed")
	}
	cancel()
	if err = <-done; !errors.Is(err, context.Canceled) {
		t.Fatalf("interrupted refresh: %v", err)
	}
	idx.Close()
	var leased bool
	if err = pool.QueryRow(ctx, `SELECT lease_id IS NOT NULL FROM cairn.semantic_job WHERE record_id=$1`, victim.id).Scan(&leased); err != nil || !leased {
		t.Fatalf("interrupted job lost: leased=%v %v", leased, err)
	}
	// A rerun during the interruption keeps the stranded job as it is.
	during, err := operator.RequeueSemantic(ctx, core.RequeueSemanticRequest{RequestID: uuid.NewString(), Reason: reason})
	if err != nil || during.Queued != 1 || during.AlreadyQueued != during.Eligible-1 {
		t.Fatalf("requeue during interruption: %+v %v", during, err) // only the edited source, already derived, is re-added
	}
	if err = pool.QueryRow(ctx, `SELECT lease_id IS NOT NULL FROM cairn.semantic_job WHERE record_id=$1`, victim.id).Scan(&leased); err != nil || !leased {
		t.Fatalf("requeue reset the stranded lease: leased=%v %v", leased, err)
	}

	// Restart: the lease expires (fixture-only clock), all jobs are due, and the
	// reopened indexer finishes with every current source re-derived once.
	if _, err = pool.Exec(ctx, `UPDATE cairn.semantic_job SET retry_at=clock_timestamp(),lease_until=CASE WHEN lease_id IS NULL THEN NULL ELSE clock_timestamp()-interval '1 second' END`); err != nil {
		t.Fatal(err)
	}
	idx, err = OpenIndex(ctx, dsn, &fixtureEmbedder{})
	if err != nil {
		t.Fatal(err)
	}
	defer idx.Close()
	drainIndex(t, ctx, idx)
	if err = pool.QueryRow(ctx, `SELECT count(*) FROM cairn.semantic_job WHERE record_id=ANY($1::uuid[])`, ids).Scan(&jobs); err != nil || jobs != 0 {
		t.Fatalf("jobs remain after resume: %d %v", jobs, err)
	}
	var stale, incomplete int
	if err = pool.QueryRow(ctx, `SELECT count(*) FILTER (WHERE d.indexed_at<=$1),count(*) FILTER (WHERE d.record_id IS NULL OR d.version<>m.current_version OR d.model_sha256<>$2)
 FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
 LEFT JOIN cairn.semantic_document d ON d.record_id=m.record_id
 WHERE m.lifecycle='active' AND v.payload_deleted_by IS NULL AND m.record_id=ANY($3::uuid[])`, before, strings.Repeat("a", 64), ids).Scan(&stale, &incomplete); err != nil || stale != 0 || incomplete != 0 {
		t.Fatalf("index not fully re-derived: stale=%d incomplete=%d %v", stale, incomplete, err)
	}
	if !searchable(t, ctx, idx, victim.id, 1, victim.body) || !searchable(t, ctx, idx, sources[2].id, 1, sources[2].body) || !searchable(t, ctx, idx, sources[0].id, 2, edited) {
		t.Fatal("re-derived sources not searchable")
	}
}
