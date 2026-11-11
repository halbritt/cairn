package semantic

// These are deterministic protocol/process fixtures, NOT Python/Rust model
// acceptance. The production Index and CommandEmbedder are never replaced.
import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5/pgxpool"
)

type transitionDB struct {
	dsn   string
	pool  *pgxpool.Pool
	store *core.Store
}

// Only the wrapper's private cluster is admissible. A normal integration DSN,
// including one accidentally inherited from the operator, is never consumed.
func transitionDatabase(t *testing.T) *transitionDB {
	t.Helper()
	root := os.Getenv("CAIRN_TRANSITION_DISPOSABLE_ROOT")
	if root == "" {
		t.Skip("requires scripts/check_semantic_transition.py's isolated disposable cluster")
	}
	if !strings.HasPrefix(root, "/tmp/semantic-transition-") || filepath.Clean(root) != root {
		t.Fatal("invalid disposable transition root")
	}
	marker, err := os.ReadFile(filepath.Join(root, "owned-cluster"))
	if err != nil || string(marker) != "semantic-transition-fixture/1\n" {
		t.Fatal("missing disposable cluster ownership marker")
	}
	cfg, err := pgxpool.ParseConfig(os.Getenv("CAIRN_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ConnConfig.Host != filepath.Join(root, "socket") || cfg.ConnConfig.Database != "postgres" {
		t.Fatal("transition DSN must select the owned cluster's private Unix socket")
	}
	ctx := context.Background()
	admin, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	name := "cairn_s05_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, err = admin.Exec(ctx, `CREATE DATABASE `+name); err != nil {
		admin.Close()
		t.Fatal(err)
	}
	t.Cleanup(func() {
		defer admin.Close()
		if _, err := admin.Exec(context.Background(), `DROP DATABASE `+name+` WITH (FORCE)`); err != nil {
			t.Error(err)
		}
	})
	selected := cfg.Copy()
	selected.ConnConfig.Database = name
	pool, err := pgxpool.NewWithConfig(ctx, selected)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	u := &url.URL{Scheme: "postgresql", User: url.User(cfg.ConnConfig.User), Path: "/" + name}
	q := u.Query()
	q.Set("host", cfg.ConnConfig.Host)
	q.Set("sslmode", "disable")
	u.RawQuery = q.Encode()
	dsn := u.String()
	s, err := core.Open(ctx, dsn, core.Channel{Principal: "s05-deterministic-fixture"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Close)
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	db := &transitionDB{dsn, pool, s}
	db.exec(t, `CREATE TABLE cairn.s05_phase (phase integer NOT NULL);
 INSERT INTO cairn.s05_phase VALUES(0);
 CREATE TABLE cairn.s05_publication (sequence bigserial PRIMARY KEY, phase integer NOT NULL, record_id uuid NOT NULL, model text NOT NULL);
 CREATE FUNCTION cairn.s05_observe_publication() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN INSERT INTO cairn.s05_publication(phase,record_id,model) SELECT phase,NEW.record_id,NEW.model_sha256 FROM cairn.s05_phase; RETURN NEW; END $$;
 CREATE TRIGGER s05_observe_publication AFTER INSERT ON cairn.semantic_document FOR EACH ROW EXECUTE FUNCTION cairn.s05_observe_publication();`)
	return db
}

func (d *transitionDB) exec(t *testing.T, sql string, args ...any) {
	t.Helper()
	if _, err := d.pool.Exec(context.Background(), sql, args...); err != nil {
		t.Fatal(err)
	}
}

func (d *transitionDB) count(t *testing.T, sql string, args ...any) int {
	t.Helper()
	var n int
	if err := d.pool.QueryRow(context.Background(), sql, args...).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func (d *transitionDB) source(t *testing.T, collection, body string) core.SemanticNote {
	t.Helper()
	r, err := d.store.Create(context.Background(), core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{
		Kind: "lesson", Body: body, Sensitivity: "shareable", ClaimType: "self",
		Scope: core.Scope{Repo: collection, TaskID: "*", RunID: "*"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	return core.SemanticNote{RecordID: r.RecordID, Version: r.Version, Body: body, BodySHA256: transitionHash(body)}
}

func transitionHash(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

type transitionProblem struct {
	RecordID string `json:"record_id"`
	Reason   string `json:"reason"`
}
type transitionJob struct {
	RecordID string    `json:"record_id"`
	Attempts int       `json:"attempts"`
	RetryAt  time.Time `json:"retry_at"`
	Leased   bool      `json:"leased"`
}
type transitionCoverage struct {
	Eligible int                 `json:"eligible"`
	Complete int                 `json:"complete"`
	Problems []transitionProblem `json:"problems"`
	Jobs     []transitionJob     `json:"jobs"`
}

// Independently inspect the source population, not a queue, query, or document
// count. Keep every active current non-payload-deleted source across ALL repos.
func (d *transitionDB) coverage(t *testing.T, model string) transitionCoverage {
	t.Helper()
	ctx := context.Background()
	tx, err := d.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(context.Background())
	// One consistent observation; callers also fence mutation for a final gate.
	if _, err = tx.Exec(ctx, `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY`); err != nil {
		t.Fatal(err)
	}
	rows, err := tx.Query(ctx, `SELECT m.record_id::text,v.version,v.body,d.version,d.body_sha256,d.model_sha256
 FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
 LEFT JOIN cairn.semantic_document d ON d.record_id=m.record_id
 WHERE m.lifecycle='active' AND v.payload_deleted_by IS NULL ORDER BY m.record_id`)
	if err != nil {
		t.Fatal(err)
	}
	type source struct {
		id          string
		version     int
		body        string
		dv          *int
		hash, model *string
	}
	var population []source
	for rows.Next() {
		var s source
		if err = rows.Scan(&s.id, &s.version, &s.body, &s.dv, &s.hash, &s.model); err != nil {
			t.Fatal(err)
		}
		population = append(population, s)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		t.Fatal(err)
	}
	r := transitionCoverage{Eligible: len(population), Problems: []transitionProblem{}, Jobs: []transitionJob{}}
	for _, s := range population {
		reason := ""
		switch {
		case s.dv == nil:
			reason = "missing_document"
		case *s.dv != s.version:
			reason = "source_version"
		case *s.hash != transitionHash(s.body):
			reason = "source_body_hash"
		case *s.model != model:
			reason = "model_identity"
		}
		passages, e := tx.Query(ctx, `SELECT ordinal,byte_offset,byte_length,embedding::text FROM cairn.semantic_passage WHERE record_id=$1 ORDER BY ordinal`, s.id)
		if e != nil {
			t.Fatal(e)
		}
		var ps []transitionPassage
		for passages.Next() {
			var p transitionPassage
			if e = passages.Scan(&p.ordinal, &p.offset, &p.length, &p.vector); e != nil {
				t.Fatal(e)
			}
			ps = append(ps, p)
		}
		passages.Close()
		if e = passages.Err(); e != nil {
			t.Fatal(e)
		}
		if reason == "" {
			reason = transitionPassageProblem(s.body, ps)
		}
		if reason == "" {
			r.Complete++
		} else {
			r.Problems = append(r.Problems, transitionProblem{s.id, reason})
		}
	}
	jobs, err := tx.Query(ctx, `SELECT record_id::text,attempts,retry_at,lease_id IS NOT NULL FROM cairn.semantic_job ORDER BY record_id`)
	if err != nil {
		t.Fatal(err)
	}
	for jobs.Next() {
		var j transitionJob
		if err = jobs.Scan(&j.RecordID, &j.Attempts, &j.RetryAt, &j.Leased); err != nil {
			t.Fatal(err)
		}
		r.Jobs = append(r.Jobs, j)
	}
	jobs.Close()
	if err = jobs.Err(); err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	return r
}

type transitionPassage struct {
	ordinal, offset, length int
	vector                  string
}

func transitionPassageProblem(body string, ps []transitionPassage) string {
	if len(ps) < 1 || len(ps) > 256 {
		return "passage_count"
	}
	for n, p := range ps {
		if p.ordinal != n {
			return "passage_ordinals"
		}
		if p.offset < 0 || p.length <= 0 || p.offset > len(body) || p.length > len(body)-p.offset || !utf8.ValidString(body[p.offset:p.offset+p.length]) {
			return "passage_utf8_span"
		}
		var vector []float64
		if json.Unmarshal([]byte(p.vector), &vector) != nil || len(vector) != 384 {
			return "vector_dimensions_or_encoding"
		}
		norm := float64(0)
		for _, v := range vector {
			if math.IsNaN(v) || math.IsInf(v, 0) {
				return "vector_finite"
			}
			norm += v * v
		}
		if norm == 0 || math.IsInf(norm, 0) {
			return "vector_nonzero_finite"
		}
	}
	return ""
}

func transitionRequireCoverage(t *testing.T, d *transitionDB, hash string, eligible, complete int) transitionCoverage {
	t.Helper()
	r := d.coverage(t, hash)
	if r.Eligible != eligible || r.Complete != complete || len(r.Problems) != eligible-complete {
		t.Fatalf("exact coverage: %+v want %d/%d", r, complete, eligible)
	}
	t.Logf("S05 coverage %s", transitionJSON(r))
	return r
}
func transitionJSON(v any) string {
	b, e := json.Marshal(v)
	if e != nil {
		panic(e)
	}
	return string(b)
}

type transitionWorker struct{ dir, path, hash string }

func transitionFixture(t *testing.T, generation string) transitionWorker {
	t.Helper()
	dir := t.TempDir()
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	w := transitionWorker{dir: dir, path: filepath.Join(dir, "fixed-launcher"), hash: transitionHash("deterministic-fixture/" + generation)}
	config := filepath.Join(dir, "worker.json")
	if err = os.WriteFile(config, []byte(transitionJSON(map[string]string{"directory": dir, "model": w.hash})), 0600); err != nil {
		t.Fatal(err)
	}
	quote := func(s string) string { return "'" + strings.ReplaceAll(s, "'", "'\"'\"'") + "'" }
	launcher := "#!/bin/sh\nexec " + quote(exe) + " -test.run=^TestSemanticTransitionWorkerProcess$ -- " + quote(config) + " \"$@\"\n"
	if err = os.WriteFile(w.path, []byte(launcher), 0700); err != nil {
		t.Fatal(err)
	}
	w.action(t, "ok")
	return w
}
func (w transitionWorker) action(t *testing.T, action string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(w.dir, "action"), []byte(action), 0600); err != nil {
		t.Fatal(err)
	}
}

// Runs only as a child of a fixed test launcher. No real model or Python is
// loaded. Explicit fixture identities cannot be mistaken for package identities.
func TestSemanticTransitionWorkerProcess(t *testing.T) {
	var config string
	for n, arg := range os.Args {
		if arg == "--" && n+1 < len(os.Args) {
			config = os.Args[n+1]
			break
		}
	}
	if config == "" {
		return
	}
	var cfg struct {
		Directory string `json:"directory"`
		Model     string `json:"model"`
	}
	b, err := os.ReadFile(config)
	if err != nil || json.Unmarshal(b, &cfg) != nil {
		os.Exit(91)
	}
	if os.Getenv("CAIRN_DATABASE_URL") != "" || os.Getenv("CAIRN_TEST_DATABASE_URL") != "" {
		os.Exit(92)
	}
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 65536), embeddingFrameLimit)
	for scanner.Scan() {
		var r struct{ ID, Operation, Text string }
		if json.Unmarshal(scanner.Bytes(), &r) != nil {
			os.Exit(93)
		}
		log, err := os.OpenFile(filepath.Join(cfg.Directory, "calls.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0600)
		if err != nil {
			os.Exit(94)
		}
		_, err = fmt.Fprintln(log, transitionJSON(map[string]any{"pid": os.Getpid(), "operation": r.Operation, "model": cfg.Model}))
		if err != nil || log.Close() != nil {
			os.Exit(95)
		}
		for {
			action, e := os.ReadFile(filepath.Join(cfg.Directory, "action"))
			if e != nil {
				os.Exit(96)
			}
			if (string(action) == "block" && r.Operation == "document") || (string(action) == "cold-query" && r.Operation == "query") || (string(action) == "cold-identity" && r.Operation == "identity") {
				time.Sleep(5 * time.Millisecond)
				continue
			}
			if string(action) == "die" && r.Operation == "document" {
				os.Exit(23)
			}
			break
		}
		out := embeddingReply{ID: r.ID, Identity: EmbeddingIdentity{ModelSHA256: cfg.Model, Algorithm: "deterministic-transition-fixture/1", Dimensions: 384}}
		vector := make([]float32, 384)
		vector[0] = 1
		if r.Operation == "query" {
			out.Vector = vector
		}
		if r.Operation == "document" && strings.TrimSpace(r.Text) != "" && r.Text != "\u200b" {
			out.Passages = []EmbeddedPassage{{Span: core.ByteSpanRequest{Offset: 0, Length: len(r.Text)}, Vector: vector}}
		}
		if _, err = fmt.Fprintln(os.Stdout, transitionJSON(out)); err != nil {
			os.Exit(97)
		}
	}
	if scanner.Err() != nil {
		os.Exit(98)
	}
	os.Exit(0)
}

func (w transitionWorker) pids(t *testing.T, operation string) []int {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(w.dir, "calls.jsonl"))
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	seen := map[int]bool{}
	var out []int
	for _, line := range strings.Split(strings.TrimSpace(string(b)), "\n") {
		var call struct {
			PID       int
			Operation string
		}
		if json.Unmarshal([]byte(line), &call) != nil {
			t.Fatal("invalid fixture process log")
		}
		if (operation == "" || call.Operation == operation) && !seen[call.PID] {
			out = append(out, call.PID)
			seen[call.PID] = true
		}
	}
	return out
}
func transitionWait(t *testing.T, what string, ready func() bool) {
	t.Helper()
	until := time.Now().Add(10 * time.Second)
	for !ready() {
		if time.Now().After(until) {
			t.Fatal("timed out: " + what)
		}
		time.Sleep(10 * time.Millisecond)
	}
}
func (w transitionWorker) reaped(t *testing.T) {
	w.reapedOperation(t, "")
}
func (w transitionWorker) reapedOperation(t *testing.T, operation string) {
	t.Helper()
	pids := w.pids(t, operation)
	if len(pids) == 0 {
		t.Fatal("fixture did not execute")
	}
	for _, pid := range pids {
		if err := syscall.Kill(pid, 0); err != syscall.ESRCH {
			t.Fatalf("worker %d not reaped: %v", pid, err)
		}
	}
}

type transitionWriter struct {
	name, collection, model string
	restartable, joined     bool
	stop                    func()
}

func transitionFence(writers []*transitionWriter) error {
	for _, w := range writers {
		if w.restartable || !w.joined {
			return fmt.Errorf("writer %s (%s) remains active or restartable", w.name, w.collection)
		}
	}
	return nil
}
func transitionStart(t *testing.T, d *transitionDB, w transitionWorker) (core.SemanticRetriever, func()) {
	t.Helper()
	r, closeIndex, err := StartIndex(context.Background(), d.dsn, w.path, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(closeIndex)
	return r, closeIndex
}
func transitionIndex(t *testing.T, d *transitionDB, w transitionWorker) *Index {
	t.Helper()
	e, err := EmbeddingCommand(context.Background(), w.path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(e.Close)
	i, err := OpenIndex(context.Background(), d.dsn, e)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(i.Close)
	return i
}
func transitionDrain(t *testing.T, i *Index) {
	t.Helper()
	for n := 0; n < 1000; n++ {
		worked, err := i.Step(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		if !worked {
			return
		}
	}
	t.Fatal("fixture exceeded explicit source bound")
}

// A small helper used by gate tests, without trusting vectorText under test.
func transitionVector(first float64) string {
	parts := make([]string, 384)
	for n := range parts {
		parts[n] = "0"
	}
	parts[0] = strconv.FormatFloat(first, 'g', -1, 64)
	return "[" + strings.Join(parts, ",") + "]"
}
