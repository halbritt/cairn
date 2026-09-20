package core

import (
	"context"
	"crypto/sha256"
	"os"
	"strconv"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// Upgrading a 048-era store must preserve existing native attempts with
// pinned turns: the new exclusivity column defaults to false and constrains
// only exclusive bindings, never pinned legacy rows.
func TestUpgrade049PreservesPinnedNativeAttempts(t *testing.T) {
	ctx := context.Background()
	host := testStore(t, Channel{Principal: "migration:native-cancel"})
	database := "cairn_upgrade_native_" + strings.ReplaceAll(uuid.NewString(), "-", "")
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
	defer old.Close(ctx)
	if err = old.QueryRow(ctx, `SELECT 1`).Scan(new(int)); err != nil {
		t.Fatal(err)
	}
	// Apply everything through 048 exactly as the deployed store did.
	files, err := schemas.ReadDir("schema")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = old.Exec(ctx, `CREATE TABLE public.cairn_migration(version integer PRIMARY KEY,digest bytea NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())`); err != nil {
		t.Fatal(err)
	}
	applied := 0
	for _, file := range files {
		digits := strings.SplitN(file.Name(), "_", 2)[0]
		version, versionErr := strconv.Atoi(digits)
		if versionErr != nil {
			t.Fatal(versionErr)
		}
		if version >= 49 {
			continue // The upgrade fixture stops at the deployed 048 schema.
		}
		body, err := schemas.ReadFile("schema/" + file.Name())
		if err != nil {
			t.Fatal(err)
		}
		if _, err = old.Exec(ctx, string(body)); err != nil {
			t.Fatalf("apply %s: %v", file.Name(), err)
		}
		digest := sha256.Sum256(body)
		if _, err = old.Exec(ctx, `INSERT INTO public.cairn_migration(version,digest) VALUES($1,$2)`, version, digest[:]); err != nil {
			t.Fatal(err)
		}
		applied++
	}
	if applied < 48 {
		t.Fatalf("expected at least the 048 schema, applied %d", applied)
	}
	// Publish real events and register a session through the 048-era store,
	// then attach two 048-shaped native attempts by direct insert.
	parsed, err := pgx.ParseConfig(os.Getenv("CAIRN_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	parsed.Database = database
	storeDSN := "host=" + parsed.Host + " port=" + strconv.Itoa(int(parsed.Port)) + " dbname=" + parsed.Database
	if user := parsed.User; user != "" {
		storeDSN += " user=" + user
	}
	if mode := parsed.RuntimeParams["sslmode"]; mode != "" {
		storeDSN += " sslmode=" + mode
	}
	store, err := Open(ctx, storeDSN, Channel{Principal: "upgrade-fixture", Repo: "upgrade-native"})
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	dest := Destination{"hosted", false}
	a, err := store.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "fixture-binding", NativeSessionID: "fixture-native", Metadata: AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{a.AgentID, a.ExecutionID}
	draft := projectNote(store.channel.Repo)
	draft.Sensitivity = "shareable"
	source, err := store.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	// The one-unfinished-attempt-per-agent rule predates 049, so the plain
	// legacy attempt lives on a second registered conversation.
	second, err := store.RegisterAgent(ctx, RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "fixture-binding-two", NativeSessionID: "fixture-native-two", Metadata: AgentMetadata{Harness: "codex", Project: "test", Workspace: "/work/test", State: "idle", DeliveryMode: "existing-session"}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	pinnedAttempt, plainAttempt := uuid.NewString(), uuid.NewString()
	for i, spec := range []struct {
		attempt, agent, execution, inbox, turn string
	}{
		{pinnedAttempt, a.AgentID, a.ExecutionID, a.Inbox, "legacy-turn-048"},
		{plainAttempt, second.AgentID, second.ExecutionID, second.Inbox, ""},
	} {
		attempt, agentID, execution, inbox, turn := spec.attempt, spec.agent, spec.execution, spec.inbox, spec.turn
		event, err := store.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", inbox}}, dest)
		if err != nil {
			t.Fatal(err)
		}
		status, err := store.AgentEventStatus(ctx, EventStatusRequest{EventID: event.EventID}, dest)
		if err != nil || len(status.Deliveries) != 1 {
			t.Fatalf("fixture delivery %d: %+v %v", i, status, err)
		}
		delivery := status.Deliveries[0]
		if _, err = store.pool.Exec(ctx, `UPDATE cairn.agent_delivery SET state='leased',lease_id=$2,lease_until=clock_timestamp()+interval '1 hour' WHERE delivery_id=$1`, delivery.DeliveryID, uuid.NewString()); err != nil {
			t.Fatal(err)
		}
		if _, err = store.pool.Exec(ctx, `INSERT INTO cairn.agent_session_attempt(attempt_id,agent_id,execution_id,owner,delivery_id,lease_id,native_turn_id) VALUES($1,$2,$3,'upgrade-fixture',$4,$5,$6)`,
			attempt, agentID, execution, delivery.DeliveryID, uuid.NewString(), turn); err != nil {
			t.Fatal(err)
		}
	}
	_ = ref
	_ = old.Close(ctx)
	upgraded, upgradeErr := Open(ctx, storeDSN, host.channel)
	if upgradeErr != nil {
		t.Fatal(upgradeErr)
	}

	defer upgraded.Close()
	if err = upgraded.Migrate(ctx); err != nil {
		t.Fatalf("049 upgrade rejected legacy pinned attempts: %v", err)
	}
	var exclusive, pinned bool
	if err = upgraded.pool.QueryRow(ctx, `SELECT turn_exclusive,native_turn_id<>'' FROM cairn.agent_session_attempt WHERE attempt_id=$1`, pinnedAttempt).Scan(&exclusive, &pinned); err != nil {
		t.Fatal(err)
	}
	if exclusive || !pinned {
		t.Fatalf("legacy pinned attempt must stay non-exclusive with its turn id: exclusive=%v pinned=%v", exclusive, pinned)
	}
	if err = upgraded.pool.QueryRow(ctx, `SELECT turn_exclusive FROM cairn.agent_session_attempt WHERE attempt_id=$1`, plainAttempt).Scan(&exclusive); err != nil || exclusive {
		t.Fatalf("legacy plain attempt changed: %v %v", exclusive, err)
	}
	// A repo-scoped exclusive binding still refuses a second owner, even on
	// a different conversation.
	if _, err = upgraded.pool.Exec(ctx, `UPDATE cairn.agent_session_attempt SET turn_exclusive=true WHERE attempt_id=$1`, pinnedAttempt); err != nil {
		t.Fatal(err)
	}
	if _, err = upgraded.pool.Exec(ctx, `UPDATE cairn.agent_session_attempt SET turn_exclusive=true,native_turn_id='legacy-turn-048' WHERE attempt_id=$1`, plainAttempt); err == nil || !errContains(err, "agent_session_one_exclusive_turn") {
		t.Fatalf("exclusive turn bound twice after upgrade: %v", err)
	}
}
