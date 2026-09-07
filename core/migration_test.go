package core

import (
	"context"
	"crypto/sha256"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func TestUpgradePreservesOriginalRecords(t *testing.T) {
	ctx := context.Background()
	host := testStore(t, Channel{Principal: "migration:test"})
	database := "cairn_upgrade_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, err := host.pool.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{database}.Sanitize()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := host.pool.Exec(ctx, "DROP DATABASE "+pgx.Identifier{database}.Sanitize()+" WITH (FORCE)"); err != nil {
			t.Error(err)
		}
	})
	config, err := pgx.ParseConfig(os.Getenv("CAIRN_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	config.Database = database
	old, err := pgx.ConnectConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	initial, err := schemas.ReadFile("schema/001_initial.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = old.Exec(ctx, string(initial)); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(initial)
	if _, err = old.Exec(ctx, `CREATE TABLE public.cairn_migration(version integer PRIMARY KEY,digest bytea NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())`); err != nil {
		t.Fatal(err)
	}
	if _, err = old.Exec(ctx, `INSERT INTO public.cairn_migration(version,digest) VALUES(1,$1)`, digest[:]); err != nil {
		t.Fatal(err)
	}
	id := uuid.NewString()
	tx, err := old.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = tx.Exec(ctx, `SELECT set_config('cairn.caller','original:writer',true),set_config('cairn.witness','testimony',true)`); err != nil {
		t.Fatal(err)
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version) VALUES($1,1)`, id); err != nil {
		t.Fatal(err)
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.record_version(record_id,version,kind,body,repo,task_id,run_id,claim_type) VALUES($1,1,'note','original retained text','old:repo','t','r','self')`, id); err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if err = old.Close(ctx); err != nil {
		t.Fatal(err)
	}
	poolConfig, err := pgxpool.ParseConfig(os.Getenv("CAIRN_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	poolConfig.ConnConfig.Database = database
	pool, err := pgxpool.NewWithConfig(ctx, poolConfig)
	if err != nil {
		t.Fatal(err)
	}
	upgraded := &Store{pool: pool, channel: Channel{Principal: "migration:test"}}
	defer upgraded.Close()
	if err = upgraded.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	record, err := upgraded.Get(ctx, id)
	if err != nil || record.Body != "original retained text" || record.ObservedWriter != "original:writer" || record.Version != 1 {
		t.Fatalf("upgrade changed original record %+v %v", record, err)
	}
}
