package core

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

func admissionUpgradeFixture(t *testing.T, version int) *Store {
	t.Helper()
	ctx := context.Background()
	host := testStore(t, Channel{Principal: "migration:admission-host"})
	database := "cairn_admission_upgrade_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, err := host.pool.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{database}.Sanitize()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := host.pool.Exec(context.Background(), "DROP DATABASE "+pgx.Identifier{database}.Sanitize()+" WITH (FORCE)"); err != nil {
			t.Error(err)
		}
	})
	store, err := openUpgradeFixtureStore(ctx, database, Channel{Principal: "migration:old-admitted", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(store.Close)
	store.compatibility = DefaultCompatibilityPolicy()
	store.compatibility.MaxMigrationVersion = version
	if _, err = store.pool.Exec(ctx, `CREATE TABLE public.cairn_migration(version integer PRIMARY KEY,digest bytea NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())`); err != nil {
		t.Fatal(err)
	}
	files, err := schemas.ReadDir("schema")
	if err != nil {
		t.Fatal(err)
	}
	for i, file := range files {
		if i+1 > version {
			break
		}
		body, err := schemas.ReadFile("schema/" + file.Name())
		if err != nil {
			t.Fatal(err)
		}
		if _, err = store.pool.Exec(ctx, string(body)); err != nil {
			t.Fatalf("apply %s: %v", file.Name(), err)
		}
		digest := sha256.Sum256(body)
		if _, err = store.pool.Exec(ctx, `INSERT INTO public.cairn_migration(version,digest) VALUES($1,$2)`, i+1, digest[:]); err != nil {
			t.Fatal(err)
		}
	}
	return store
}

func waitAdmissionMigrationBlock(t *testing.T, ctx context.Context, store *Store, app string, blocker uint32) int {
	t.Helper()
	ticker := time.NewTicker(5 * time.Millisecond)
	defer ticker.Stop()
	for {
		var pid int
		err := store.pool.QueryRow(ctx, `SELECT COALESCE((SELECT pid FROM pg_stat_activity WHERE datname=current_database() AND application_name=$1 AND $2::integer=ANY(pg_blocking_pids(pid)) LIMIT 1),0)`, app, int(blocker)).Scan(&pid)
		if err != nil {
			t.Fatal(err)
		}
		if pid != 0 {
			return pid
		}
		select {
		case <-ctx.Done():
			t.Fatalf("%s did not block on expected backend: %v", app, ctx.Err())
		case <-ticker.C:
		}
	}
}

func TestAdmissionMigrationDrainsAdmittedAndInvalidatesOldSnapshots(t *testing.T) {
	for _, oldVersion := range []int{79, 83} {
		t.Run(fmt.Sprint(oldVersion), func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			old := admissionUpgradeFixture(t, oldVersion)
			prefix := "admission-upgrade-" + uuid.NewString()[:12]
			migrator := adversarialNamedStore(t, ctx, old, prefix+"-migrator")
			legacyWriter := adversarialNamedStore(t, ctx, old, prefix+"-serializable")
			legacyWriter.compatibility = old.compatibility
			legacyReader := adversarialNamedStore(t, ctx, old, prefix+"-repeatable")
			legacyReader.compatibility = old.compatibility
			gate, err := old.pool.Acquire(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer gate.Release()
			gateName := prefix + "-ledger"
			if _, err = gate.Exec(ctx, `SELECT pg_advisory_lock(hashtextextended($1,0))`, gateName); err != nil {
				t.Fatal(err)
			}
			defer gate.Exec(context.Background(), `SELECT pg_advisory_unlock(hashtextextended($1,0))`, gateName)
			// Hold the installer after its barrier, without sleeps or production hooks.
			_, err = old.pool.Exec(ctx, fmt.Sprintf(`CREATE FUNCTION public.admission_upgrade_gate() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.version=%d THEN PERFORM pg_advisory_xact_lock(hashtextextended('%s',0)); END IF; RETURN NEW; END $$;
 CREATE TRIGGER admission_upgrade_gate BEFORE INSERT ON public.cairn_migration FOR EACH ROW EXECUTE FUNCTION public.admission_upgrade_gate()`, oldVersion+1, gateName))
			if err != nil {
				t.Fatal(err)
			}
			var pausedBefore bool
			var generationBefore int64
			var sessionBefore string
			if err = old.pool.QueryRow(ctx, `SELECT a.paused,COALESCE(a.session_id::text,''),g.generation FROM cairn.restore_admission a CROSS JOIN cairn.retrieval_generation g WHERE a.singleton AND g.singleton`).Scan(&pausedBefore, &sessionBefore, &generationBefore); err != nil {
				t.Fatal(err)
			}
			admitted, err := old.beginLevel(ctx, pgx.Serializable)
			if err != nil {
				t.Fatal(err)
			}
			defer admitted.Rollback(context.Background())
			var admittedPID uint32
			if err = admitted.QueryRow(ctx, `SELECT pg_backend_pid()`).Scan(&admittedPID); err != nil {
				t.Fatal(err)
			}
			migrated := make(chan error, 1)
			go func() { migrated <- migrator.Migrate(ctx) }()
			migrationPID := waitAdmissionMigrationBlock(t, ctx, old, prefix+"-migrator", admittedPID)
			// This old operation passed compatibility before the upgrade started. Its
			// write must drain before schema installation, and remain readable afterward.
			recordID := uuid.NewString()
			if _, err = admitted.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version) VALUES($1,1)`, recordID); err != nil {
				t.Fatal(err)
			}
			draft := projectNote("fixture:admitted-before-upgrade")
			draft.Body = "accepted before event admission upgrade"
			if _, err = insertVersion(ctx, admitted, recordID, 1, draft); err != nil {
				t.Fatal(err)
			}
			if err = admitted.Commit(ctx); err != nil {
				t.Fatal(err)
			}
			waitAdmissionMigrationBlock(t, ctx, old, prefix+"-migrator", gate.Conn().PgConn().PID())
			select {
			case err = <-migrated:
				t.Fatalf("upgrade passed held ledger gate: %v", err)
			default:
			}
			writerDone := make(chan error, 1)
			bootstrapID := uuid.NewString()
			go func() {
				_, err := legacyWriter.Bootstrap(ctx, BootstrapRequest{RequestID: bootstrapID, Reason: "Reject old snapshot after the admission migration commits"})
				writerDone <- err
			}()
			readerDone := make(chan error, 1)
			go func() {
				tx, err := legacyReader.beginLevel(ctx, pgx.RepeatableRead)
				if tx != nil {
					tx.Rollback(context.Background())
				}
				readerDone <- err
			}()
			waitAdmissionMigrationBlock(t, ctx, old, prefix+"-serializable", uint32(migrationPID))
			waitAdmissionMigrationBlock(t, ctx, old, prefix+"-repeatable", uint32(migrationPID))
			if _, err = gate.Exec(ctx, `SELECT pg_advisory_unlock(hashtextextended($1,0))`, gateName); err != nil {
				t.Fatal(err)
			}
			if err = <-migrated; err != nil {
				t.Fatalf("migration: %v", err)
			}
			requireCode(t, <-writerDone, "SCHEMA_MISMATCH")
			var pgErr *pgconn.PgError
			if err = <-readerDone; !errors.As(err, &pgErr) || pgErr.Code != "40001" {
				t.Fatalf("old RepeatableRead snapshot survived tuple barrier: %v", err)
			}
			var grants, receipts int64
			if err = old.pool.QueryRow(ctx, `SELECT (SELECT count(*) FROM cairn.authority_grant),(SELECT count(*) FROM cairn.mutation_request WHERE caller=$1 AND operation='bootstrap' AND request_id=$2)`, legacyWriter.channel.Principal, bootstrapID).Scan(&grants, &receipts); err != nil || grants != 0 || receipts != 0 {
				t.Fatalf("old writer committed after upgrade: grants=%d receipts=%d err=%v", grants, receipts, err)
			}
			var pausedAfter bool
			var generationAfter int64
			var sessionAfter string
			var version int
			if err = old.pool.QueryRow(ctx, `SELECT a.paused,COALESCE(a.session_id::text,''),g.generation,(SELECT max(version) FROM public.cairn_migration) FROM cairn.restore_admission a CROSS JOIN cairn.retrieval_generation g WHERE a.singleton AND g.singleton`).Scan(&pausedAfter, &sessionAfter, &generationAfter, &version); err != nil || pausedAfter != pausedBefore || sessionAfter != sessionBefore || generationAfter != generationBefore || version != 84 {
				t.Fatalf("barrier changed restore semantics or schema wrong: paused=%v session=%q generation=%d version=%d err=%v", pausedAfter, sessionAfter, generationAfter, version, err)
			}
			preserved, err := migrator.Get(ctx, recordID)
			if err != nil || preserved.Body != draft.Body {
				t.Fatalf("pre-admitted write was lost: %+v %v", preserved, err)
			}
			_, err = legacyWriter.Bootstrap(ctx, BootstrapRequest{RequestID: uuid.NewString(), Reason: "Fresh old binary must reject the upgraded schema too"})
			requireCode(t, err, "SCHEMA_MISMATCH")
		})
	}
}
