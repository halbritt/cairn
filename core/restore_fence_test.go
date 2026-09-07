package core

import (
	"context"
	"errors"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

// A restore fence is whole-store state, so these tests need their own database
// even when other package suites are using the disposable integration cluster.
func restoreTestStore(t *testing.T) *Store {
	t.Helper()
	ctx := context.Background()
	host := testStore(t, Channel{Principal: "restore:test"})
	name := "cairn_restore_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, err := host.pool.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{name}.Sanitize()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := host.pool.Exec(ctx, "DROP DATABASE "+pgx.Identifier{name}.Sanitize()+" WITH (FORCE)"); err != nil {
			t.Error(err)
		}
	})
	config, err := pgxpool.ParseConfig(os.Getenv("CAIRN_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	config.ConnConfig.Database = name
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	s := &Store{pool: pool, channel: Channel{Principal: "restore:test", Operator: true, Instrumented: true}}
	t.Cleanup(s.Close)
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	return s
}

func TestRestoreFenceRequiresFreshCompileAndPreservesHistory(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{Repo: "fixture:restore", TaskID: "t", RunID: "r"}, Purpose: "context", AvailableTokens: 64000}
	old, err := s.Compile(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	fence := RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Fence restored launch receipts before service resumes"}
	result, err := s.FenceRestore(ctx, fence)
	if err != nil {
		t.Fatal(err)
	}
	if result.Generation != 1 {
		t.Fatalf("first restore generation: %+v", result)
	}
	requireCode(t, s.ClaimRun(ctx, old.ReceiptID), "STALE_PACKAGE")
	_, err = s.Compile(ctx, req, Destination{"local", true})
	requireCode(t, err, "STALE_PACKAGE")
	historical, err := s.Replay(ctx, old.ReceiptID)
	if err != nil || historical.Seal != old.Seal {
		t.Fatalf("history changed: %+v %v", historical, err)
	}
	req.RequestID = uuid.NewString()
	fresh, err := s.Compile(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	repeated, err := s.FenceRestore(ctx, fence)
	if err != nil || repeated != result {
		t.Fatalf("fence retry changed generation: %+v %v", repeated, err)
	}
	if err = s.ClaimRun(ctx, fresh.ReceiptID); err != nil {
		t.Fatal(err)
	}
	requireCode(t, s.ClaimRun(ctx, fresh.ReceiptID), "RUN_ALREADY_STARTED")
}

func TestRestoreFenceBlocksCachedBindingsPullsAndLateContextWrites(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	_, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote("fixture:restore")})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{Repo: "fixture:restore", TaskID: "t", RunID: "r"}, Purpose: "context", AvailableTokens: 64000}
	pkg, err := s.Compile(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	binding := RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: pkg.ReceiptID, TaskClass: "build", BindingID: "fixture", CapabilityID: "fixture", CommandSHA256: strings.Repeat("a", 64)}
	if _, err = s.BindRun(ctx, binding); err != nil {
		t.Fatal(err)
	}
	if err = s.ClaimRun(ctx, pkg.ReceiptID); err != nil {
		t.Fatal(err)
	}
	req.RequestID = uuid.NewString()
	index, err := s.Index(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(index.Handles) != 1 {
		t.Fatalf("missing fixture handle: %+v", index)
	}
	pull := ExpandRequest{uuid.NewString(), index.Package.ReceiptID, index.Handles[0].Handle}
	if _, err = s.Expand(ctx, pull, Destination{"local", true}); err != nil {
		t.Fatal(err)
	}
	if _, err = s.FenceRestore(ctx, RestoreFenceRequest{uuid.NewString(), "Invalidate restored delivery capabilities"}); err != nil {
		t.Fatal(err)
	}
	_, err = s.BindRun(ctx, binding)
	requireCode(t, err, "STALE_PACKAGE")
	_, err = s.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "STALE_PACKAGE")
	_, err = s.Index(ctx, req, Destination{"local", true})
	requireCode(t, err, "STALE_PACKAGE")
	_, err = s.RegisterManagedContext(ctx, ManagedContextRequest{OwnershipID: uuid.NewString(), RequestID: uuid.NewString(), ReceiptID: pkg.ReceiptID, Directory: "/fixture/" + pkg.ReceiptID, DirectoryDevice: "1", DirectoryInode: "2"})
	requireCode(t, err, "STALE_PACKAGE")
	// Delayed facts about already-started work remain reportable after the fence.
	exit := 0
	if _, err = s.RecordOutcome(ctx, OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: pkg.ReceiptID, ExitCode: &exit, ProcessState: "exited", StdoutSHA256: strings.Repeat("a", 64), StderrSHA256: strings.Repeat("b", 64)}); err != nil {
		t.Fatal(err)
	}
	for _, channel := range []Channel{{Principal: "agent:test"}, {Principal: "scoped:test", Operator: true, Repo: "fixture:restore"}} {
		denied := &Store{pool: s.pool, channel: channel}
		_, err = denied.FenceRestore(ctx, RestoreFenceRequest{uuid.NewString(), "Unauthorized restore fence fixture"})
		requireCode(t, err, "AUTHORITY_DENIED")
	}
}

func TestCompileSnapshotCannotCrossCommittedRestoreFence(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{Repo: "fixture:restore", TaskID: "t", RunID: "r"}, Purpose: "context", AvailableTokens: 64000}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	evaluations := map[string]*CandidateEvaluation{}
	semantic, err := s.compileSnapshot(ctx, tx, req, Destination{"local", true}, evaluations)
	if err != nil {
		t.Fatal(err)
	}
	canonical, seal, err := sealPackage(semantic)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.FenceRestore(ctx, RestoreFenceRequest{uuid.NewString(), "Fence after candidate snapshot before receipt commit"}); err != nil {
		t.Fatal(err)
	}
	_, err = s.commitRetrieval(ctx, tx, req, semantic, canonical, seal, evaluations)
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "40001" {
		t.Fatalf("old snapshot stamped as fresh: %v", err)
	}
	if err = tx.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	fresh, err := s.Compile(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if err = s.ClaimRun(ctx, fresh.ReceiptID); err != nil {
		t.Fatal(err)
	}
}

func TestRestoreFenceWaitsForReceiptCommit(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{Repo: "fixture:restore", TaskID: "t", RunID: "r"}, Purpose: "context", AvailableTokens: 64000}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	evaluations := map[string]*CandidateEvaluation{}
	semantic, err := s.compileSnapshot(ctx, tx, req, Destination{"local", true}, evaluations)
	if err != nil {
		t.Fatal(err)
	}
	canonical, seal, err := sealPackage(semantic)
	if err != nil {
		t.Fatal(err)
	}
	pkg, err := s.commitRetrieval(ctx, tx, req, semantic, canonical, seal, evaluations)
	if err != nil {
		t.Fatal(err)
	}
	fence := RestoreFenceRequest{uuid.NewString(), "Wait for the in-flight receipt transaction"}
	blocked, cancel := context.WithTimeout(ctx, 150*time.Millisecond)
	defer cancel()
	_, err = s.FenceRestore(blocked, fence)
	if err == nil || blocked.Err() != context.DeadlineExceeded {
		t.Fatalf("fence crossed uncommitted receipt: %v", err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	result, err := s.FenceRestore(ctx, fence)
	if err != nil || result.Generation != 1 || result.Receipts != 1 {
		t.Fatalf("retry lost preceding receipt: %+v %v", result, err)
	}
	requireCode(t, s.ClaimRun(ctx, pkg.ReceiptID), "STALE_PACKAGE")
}
