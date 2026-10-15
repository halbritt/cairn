//go:build ownerpg

package experimentaldebit

import (
	"context"
	"errors"
	"flag"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// SOURCE ONLY in this authoring lane. No TestMain, init, env DSN fallback,
// cluster creation, process command or default connection is supplied.
var pgRoot = flag.String("owner-pg-root", "", "separately authorized owned disposable cluster root")

func futurePool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	if *pgRoot == "" {
		t.Fatal("actual_database_execution_not_selected")
	}
	root := filepath.Clean(*pgRoot)
	if filepath.Dir(root) != "/tmp" || !strings.HasPrefix(filepath.Base(root), "cairn-debit-pg-") {
		t.Fatal("disposable_root_required")
	}
	st, e := os.Lstat(root)
	if e != nil || !st.IsDir() || st.Mode()&0077 != 0 {
		t.Fatal("private_disposable_root_required")
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !ok || sys.Uid != uint32(os.Getuid()) {
		t.Fatal("owned_root_required")
	}
	for _, entry := range os.Environ() {
		key := strings.SplitN(entry, "=", 2)[0]
		if strings.HasPrefix(key, "PG") || strings.Contains(key, "DATABASE_URL") {
			t.Fatal("inherited_database_route_refused")
		}
	}
	// This is a test-selection interlock, not authenticated funding/hold evidence.
	receipt, e := os.ReadFile(filepath.Join(root, "SOURCE-TEST-SELECTION"))
	if e != nil || string(receipt) != "disposable-debit-owner-tests/1\n" {
		t.Fatal("explicit_test_selection_required")
	}
	cfg, e := pgxpool.ParseConfig("host=" + root + "/socket dbname=cairn_debit_disposable user=cairn_debit_test sslmode=disable passfile=/dev/null servicefile=/dev/null connect_timeout=2")
	if e != nil {
		t.Fatal("test_config")
	}
	cfg.MaxConns = 4
	pool, e := pgxpool.NewWithConfig(context.Background(), cfg)
	if e != nil {
		t.Fatal("test_pool")
	}
	t.Cleanup(pool.Close)
	var fsync, commit, pages, db, data string
	e = pool.QueryRow(context.Background(), `SELECT current_setting('fsync'),current_setting('synchronous_commit'),current_setting('full_page_writes'),current_database(),current_setting('data_directory')`).Scan(&fsync, &commit, &pages, &db, &data)
	if e != nil || fsync != "on" || commit != "on" || pages != "on" || db != "cairn_debit_disposable" || filepath.Clean(data) != root+"/data" {
		t.Fatal("durable_disposable_settings_required")
	}
	return pool
}
func sqlExec(t *testing.T, p *pgxpool.Pool, sql string, args ...any) {
	t.Helper()
	if _, e := p.Exec(context.Background(), sql, args...); e != nil {
		t.Fatal("selected_test_sql_failed")
	}
}
func pgBook(t *testing.T, p *pgxpool.Pool) Book {
	t.Helper()
	var b Book
	e := transaction(context.Background(), p, func(tx pgx.Tx) error {
		var e error
		b, _, e = loadMeta(context.Background(), tx, "SHARE")
		if e != nil {
			return e
		}
		rows, e := tx.Query(context.Background(), `SELECT key FROM cairn_debit_experiment.slot ORDER BY key`)
		if e != nil {
			return e
		}
		keys := []string{}
		for rows.Next() {
			var key string
			if e = rows.Scan(&key); e != nil {
				rows.Close()
				return e
			}
			keys = append(keys, key)
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return e
		}
		for _, key := range keys {
			if e = loadSlot(context.Background(), tx, &b, key); e != nil {
				return e
			}
		}
		return nil
	})
	if e != nil {
		t.Fatal("read synthetic book")
	}
	return b
}
func seed(t *testing.T, p *pgxpool.Pool, b Book) {
	t.Helper()
	if e := validateBook(b); e != nil {
		t.Fatal(e)
	}
	// Only synthetic fixture rows reset between distinct disposable test scenarios.
	sqlExec(t, p, `TRUNCATE cairn_debit_experiment.audit,cairn_debit_experiment.balance,cairn_debit_experiment.slot,cairn_debit_experiment.ledger`)
	sqlExec(t, p, `INSERT INTO cairn_debit_experiment.ledger VALUES(1,$1,$2,$3,$4,$5,$6,$7,$8)`, b.Revision, b.Generation, b.Recovery, b.Principal, b.Incarnation, b.Active, b.Currency, b.UnitsPerCurrency)
	for key, v := range b.Balances {
		sqlExec(t, p, `INSERT INTO cairn_debit_experiment.balance VALUES($1,$2,$3)`, key, v.Remaining, v.Committed)
	}
	for key, slot := range b.Slots {
		raw, e := checkedBytes(slot, SourceBound)
		if e != nil {
			t.Fatal(e)
		}
		sqlExec(t, p, `INSERT INTO cairn_debit_experiment.slot VALUES($1,$2,$3::jsonb,$4)`, key, slot.Attempt, string(raw), slot.Quarantined)
	}
}
func dbTime(t *testing.T, p *pgxpool.Pool) int64 {
	t.Helper()
	var n int64
	if e := p.QueryRow(context.Background(), `SELECT floor(extract(epoch FROM clock_timestamp()))::bigint`).Scan(&n); e != nil {
		t.Fatal(e)
	}
	return n
}
func freshPGBook(t *testing.T, p *pgxpool.Pool) Book {
	b := syntheticBook()
	s := b.Slots["slot-1"]
	n := dbTime(t, p)
	s.NotBefore = n - 1
	s.ValidUntil = n + 300
	b.Slots["slot-1"] = s
	return b
}

type lostCommitDB struct {
	*pgxpool.Pool
	mu            sync.Mutex
	loseAt, calls int
}
type lostCommitTx struct {
	pgx.Tx
	owner *lostCommitDB
}

func (d *lostCommitDB) BeginTx(ctx context.Context, o pgx.TxOptions) (pgx.Tx, error) {
	tx, e := d.Pool.BeginTx(ctx, o)
	if e != nil {
		return nil, e
	}
	return &lostCommitTx{tx, d}, nil
}
func (t *lostCommitTx) Commit(ctx context.Context) error {
	if e := t.Tx.Commit(ctx); e != nil {
		return e
	}
	t.owner.mu.Lock()
	defer t.owner.mu.Unlock()
	t.owner.calls++
	if t.owner.calls == t.owner.loseAt {
		return errors.New("injected lost commit acknowledgment")
	}
	return nil
}
func TestPostgreSQLOwnerContract(t *testing.T) {
	p := futurePool(t)
	ctx := context.Background()
	// Deliberately fail if schema exists: root must select a fresh disposable suite.
	sqlExec(t, p, experimentalSchema)
	store := &postgresStore{p}
	t.Run("all_or_none_audit_failure", func(t *testing.T) {
		b := freshPGBook(t, p)
		s := b.Slots["slot-1"]
		s.Phase = Armed
		b.Slots["slot-1"] = s
		seed(t, p, b)
		sqlExec(t, p, `ALTER TABLE cairn_debit_experiment.audit ADD CONSTRAINT injected_failure CHECK(false) NOT VALID`)
		ack, e := store.apply(ctx, cmd(b, "debit"))
		if e == nil || ack != (acknowledgment{}) || digest(pgBook(t, p)) != digest(b) {
			t.Fatal("partial durable debit")
		}
		sqlExec(t, p, `ALTER TABLE cairn_debit_experiment.audit DROP CONSTRAINT injected_failure`)
	})
	t.Run("shared_balance_concurrent_clients", func(t *testing.T) {
		b := freshPGBook(t, p)
		s := b.Slots["slot-1"]
		s.Phase = Armed
		b.Slots["slot-1"] = s
		second := s
		second.Attempt = "attempt-2"
		second.Binding.Hold = "hold-2"
		second.Binding.Instance = "actor-2"
		b.Slots["slot-2"] = second
		b.Balances["shared-pool"] = Balance{Remaining: 9}
		seed(t, p, b)
		c1 := cmd(b, "debit")
		c2 := c1
		c2.Slot = "slot-2"
		c2.Attempt = second.Attempt
		c2.BindingSHA = digest(second.Binding)
		start := make(chan struct{})
		errs := make(chan error, 2)
		for _, c := range []command{c1, c2} {
			go func(c command) { <-start; _, e := (&postgresStore{p}).apply(ctx, c); errs <- e }(c)
		}
		close(start)
		wins := 0
		for range 2 {
			if <-errs == nil {
				wins++
			}
		}
		after := pgBook(t, p)
		if wins != 1 || after.Balances["shared-pool"] != (Balance{0, 9}) || after.Balances["currency-global"].Committed != 7 {
			t.Fatal("cross-client overdraw")
		}
	})
	t.Run("audit_and_all_roles_exceed_one_emitted_page_without_pruning", func(t *testing.T) {
		b := completeSyntheticWork(freshPGBook(t, p))
		now := dbTime(t, p)
		for key, slot := range b.Slots {
			slot.NotBefore = now - 1
			slot.ValidUntil = now + 300
			b.Slots[key] = slot
		}
		seed(t, p, b)
		for key, slot := range b.Slots {
			snap, e := store.read(ctx, key)
			if e != nil {
				t.Fatal(e)
			}
			c := command{Slot: key, Attempt: slot.Attempt, ExpectedRevision: snap.Revision, Identity: snap.Identity, BindingSHA: digest(slot.Binding), DebitSHA: digest(slot.Debit)}
			for _, op := range []string{"arm", "debit", "consume"} {
				c.Op = op
				ack, e := store.apply(ctx, c)
				if e != nil {
					t.Fatal(e)
				}
				c.ExpectedRevision = ack.Revision
			}
			if e = store.observe(ctx, c, Observation{Phase: Consumed, Outcome: "complete"}); e != nil {
				t.Fatal(e)
			}
		}
		var auditBytes, count int64
		if e := p.QueryRow(ctx, `SELECT sum(octet_length(event::text)),count(*) FROM cairn_debit_experiment.audit`).Scan(&auditBytes, &count); e != nil {
			t.Fatal(e)
		}
		if auditBytes <= 65536 || count != 4*257 {
			t.Fatal("campaign audit incorrectly capped")
		}
		seen := 0
		after := ""
		for i := 0; i < 10; i++ {
			page, e := store.page(ctx, after)
			if e != nil {
				t.Fatal(e)
			}
			if _, e = checkedBytes(page, MetadataBound); e != nil {
				t.Fatal(e)
			}
			seen += len(page.Records)
			if page.Done {
				break
			}
			if page.Next <= after {
				t.Fatal("cursor")
			}
			after = page.Next
		}
		if seen != 257 {
			t.Fatal("lost persistent slots")
		}
	})
	t.Run("lost_debit_commit_and_reconstructed_owner", func(t *testing.T) {
		b := freshPGBook(t, p)
		seed(t, p, b)
		db := &lostCommitDB{Pool: p}
		ps := &postgresStore{db}
		factories := 0
		a := adapters{ps, fakeAuthority{}, fakeQualifier{}, func() (transport, error) { factories++; return &fakeTransport{answer: "complete"}, nil }}
		o, e := assemble(ctx, a, Identity{Principal: b.Principal, Incarnation: "client-one", Recovery: 1})
		if e != nil {
			t.Fatal(e)
		}
		selected := pgBook(t, p)
		selected.Slots = b.Slots
		seed(t, p, selected)
		db.mu.Lock()
		db.calls = 0
		db.loseAt = 3
		db.mu.Unlock() // snapshot, arm, then real committed debit loses ack
		_, e = o.Prepare(ctx, "slot-1")
		if e == nil || factories != 0 {
			t.Fatal("lost ack forwarded")
		}
		retained := pgBook(t, p)
		if retained.Balances["currency-global"].Committed != 7 || !retained.Slots["slot-1"].Quarantined {
			t.Fatal("lost exposure/quarantine")
		}
		db.mu.Lock()
		db.loseAt = 0
		db.mu.Unlock()
		restarted, e := assemble(ctx, a, Identity{Principal: b.Principal, Incarnation: "client-two", Recovery: 1})
		if e != nil {
			t.Fatal(e)
		}
		_, e = restarted.Prepare(ctx, "slot-1")
		if e == nil || factories != 0 {
			t.Fatal("reconstructed continuation")
		}
	})
	t.Run("fence_ordered_against_dispatch", func(t *testing.T) {
		b := freshPGBook(t, p)
		s := b.Slots["slot-1"]
		s.Phase = Consumed
		b.Slots["slot-1"] = s
		seed(t, p, b)
		c := command{Op: "recover", ExpectedRevision: b.Revision, Identity: Identity{Principal: b.Principal, Incarnation: "fenced", Recovery: 1}}
		result := runFenceCheck(ctx, 2*time.Second, 2*time.Second,
			func(work context.Context, call func() error) error {
				return store.dispatch(work, cmd(b, "consume"), call)
			},
			func(work context.Context) (fenceTestLease, error) {
				conn, e := p.Acquire(work)
				if conn == nil {
					return nil, e
				}
				// Ownership transfers immediately, including a compound error.
				return &pgFenceTestLease{conn: conn, command: c}, e
			})
		if result.Primary != nil || !result.Joined || result.Release != nil {
			t.Fatalf("fence check: primary=%v dispatch=%v release=%v joined=%v", result.Primary, result.Dispatch, result.Release, result.Joined)
		}
		var e error
		if _, e = store.apply(ctx, c); e != nil {
			t.Fatal(e)
		}
		called := false
		e = store.dispatch(ctx, cmd(b, "consume"), func() error { called = true; return nil })
		if e == nil || called {
			t.Fatal("stale sender forwarded")
		}
	})
}

// Two externally controlled phases for a FUTURE owned cluster crash/restart.
// Do not select these with the ordinary suite. No process is started/stopped here.
func TestPGCrashStage(t *testing.T) {
	p := futurePool(t)
	sqlExec(t, p, experimentalSchema)
	b := freshPGBook(t, p)
	seed(t, p, b)
	s := &postgresStore{p}
	ctx := context.Background()
	for _, op := range []string{"arm", "debit", "consume"} {
		b = pgBook(t, p)
		if _, e := s.apply(ctx, cmd(b, op)); e != nil {
			t.Fatal(e)
		}
	}
	if pgBook(t, p).Slots["slot-1"].Phase != Consumed {
		t.Fatal("not persisted")
	}
	// Owner/root must now stop/crash/restart ONLY this owned cluster externally.
}
func TestPGCrashAfterRestart(t *testing.T) {
	p := futurePool(t)
	b := pgBook(t, p)
	if b.Slots["slot-1"].Phase != Consumed || b.Balances["currency-global"].Committed != 7 {
		t.Fatal("durability lost")
	}
	calls := 0
	a := adapters{&postgresStore{p}, fakeAuthority{}, fakeQualifier{}, func() (transport, error) { calls++; return nil, nil }}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	o, e := assemble(ctx, a, Identity{Principal: b.Principal, Incarnation: "after-crash", Recovery: 1})
	if e != nil {
		t.Fatal(e)
	}
	_, e = o.Prepare(ctx, "slot-1")
	if e == nil || calls != 0 || !pgBook(t, p).Slots["slot-1"].Quarantined {
		t.Fatal("restart reissued")
	}
}

// Only future selected PostgreSQL tests construct this adapter.
type pgFenceTestLease struct {
	conn    *pgxpool.Conn
	command command
}

func (f *pgFenceTestLease) exercise(ctx context.Context) error {
	if _, e := f.conn.Exec(ctx, `SET statement_timeout='50ms'`); e != nil {
		return e
	}
	// No scheduling sleep oracle: the blocked fence must return an error.
	if _, e := (&postgresStore{f.conn.Conn()}).apply(ctx, f.command); e == nil {
		return errors.New("fence overtook dispatch")
	}
	return nil
}
func (f *pgFenceTestLease) release(ctx context.Context) error {
	defer f.conn.Release()
	_, e := f.conn.Exec(ctx, `RESET statement_timeout`)
	return e
}
