// Fixed S01b store-function and idle-process observations. Never a production worker.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/semantic"
	"github.com/jackc/pgx/v5"
)

const pgRoot = "/tmp/cairn-s01b-pg"
const body = "é source span control."

type call struct {
	Result any    `json:"result"`
	Error  string `json:"error"`
}

func observed(result any, err error) call {
	message := ""
	if err != nil {
		message = err.Error()
	}
	return call{Result: result, Error: message}
}

type fixtureModel struct {
	passages                       []semantic.EmbeddedPassage
	documents, queries, identities int
}

func (m *fixtureModel) Identity(context.Context) (semantic.EmbeddingIdentity, error) {
	m.identities++
	return semantic.EmbeddingIdentity{ModelSHA256: strings.Repeat("a", 64), Algorithm: "s01b-store-double/1", Dimensions: 384}, nil
}
func (m *fixtureModel) Query(context.Context, string) ([]float32, error) {
	m.queries++
	vector := make([]float32, 384)
	vector[0] = 1
	return vector, nil
}
func (m *fixtureModel) Document(_ context.Context, text string) ([]semantic.EmbeddedPassage, error) {
	m.documents++
	if text != body {
		return nil, fmt.Errorf("fixture received a different source body")
	}
	return m.passages, nil
}

var storeCases = []string{
	"F-EMBED/invalid-utf8-span/negative-offset", "F-EMBED/invalid-utf8-span/zero-length",
	"F-EMBED/invalid-utf8-span/outside-source", "F-EMBED/invalid-utf8-span/mid-codepoint",
	"F-EMBED/too-many-passages/zero", "F-EMBED/too-many-passages/256", "F-EMBED/too-many-passages/257",
	"F-EMBED/bad-dimensions/383", "F-EMBED/bad-dimensions/384", "F-EMBED/bad-dimensions/385",
	"F-EMBED/nonfinite-vectors/NaN", "F-EMBED/nonfinite-vectors/positive-infinity",
	"F-EMBED/nonfinite-vectors/negative-infinity", "F-EMBED/zero-vectors/boundary",
}

func inputs(name string) ([]semantic.EmbeddedPassage, error) {
	count, dimensions := 1, 384
	switch name {
	case "F-EMBED/too-many-passages/zero":
		count = 0
	case "F-EMBED/too-many-passages/256":
		count = 256
	case "F-EMBED/too-many-passages/257":
		count = 257
	case "F-EMBED/bad-dimensions/383":
		dimensions = 383
	case "F-EMBED/bad-dimensions/385":
		dimensions = 385
	}
	known := false
	for _, candidate := range storeCases {
		if candidate == name {
			known = true
		}
	}
	if !known {
		return nil, fmt.Errorf("unknown fixed store case")
	}
	passages := make([]semantic.EmbeddedPassage, count)
	for n := range passages {
		vector := make([]float32, dimensions)
		vector[0] = 1
		span := core.ByteSpanRequest{Offset: 0, Length: 2}
		switch name {
		case "F-EMBED/invalid-utf8-span/negative-offset":
			span.Offset = -1
		case "F-EMBED/invalid-utf8-span/zero-length":
			span.Length = 0
		case "F-EMBED/invalid-utf8-span/outside-source":
			span.Offset = len(body) + 1
		case "F-EMBED/invalid-utf8-span/mid-codepoint":
			span.Offset = 1
			span.Length = 1
		case "F-EMBED/nonfinite-vectors/NaN":
			vector[0] = math.Float32frombits(0x7fc00001)
		case "F-EMBED/nonfinite-vectors/positive-infinity":
			vector[0] = float32(math.Inf(1))
		case "F-EMBED/nonfinite-vectors/negative-infinity":
			vector[0] = float32(math.Inf(-1))
		case "F-EMBED/zero-vectors/boundary":
			vector[0] = 0
		}
		passages[n] = semantic.EmbeddedPassage{Span: span, Vector: vector}
	}
	return passages, nil
}

type passageInput struct {
	Span        core.ByteSpanRequest `json:"span"`
	Float32Bits []uint32             `json:"float32_bits"`
}

func inputBits(passages []semantic.EmbeddedPassage) []passageInput {
	result := make([]passageInput, len(passages))
	for n, passage := range passages {
		bits := make([]uint32, len(passage.Vector))
		for index, value := range passage.Vector {
			bits[index] = math.Float32bits(value)
		}
		result[n] = passageInput{Span: passage.Span, Float32Bits: bits}
	}
	return result
}

func storeCase(ctx context.Context, admin *pgx.Conn, number int, name string) (map[string]any, error) {
	database := fmt.Sprintf("s01b_residual_%02d", number)
	if _, err := admin.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{database}.Sanitize()); err != nil {
		return nil, err
	}
	dsn := "host=" + pgRoot + "/socket user=s01bfixturepg dbname=" + database + " sslmode=disable"
	s, err := core.Open(ctx, dsn, core.Channel{Principal: "s01b-residual-store"})
	if err != nil {
		return nil, err
	}
	defer s.Close()
	if err = s.Migrate(ctx); err != nil {
		return nil, err
	}
	repo := uuid.NewString()
	record, err := s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{
		Kind: "lesson", Body: body, Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}})
	if err != nil {
		return nil, err
	}
	passages, err := inputs(name)
	if err != nil {
		return nil, err
	}
	model := &fixtureModel{passages: passages}
	index, err := semantic.OpenIndex(ctx, dsn, model)
	if err != nil {
		return nil, err
	}
	defer index.Close()
	worked, stepErr := index.Step(ctx)
	connection, err := pgx.Connect(ctx, dsn)
	if err != nil {
		return nil, err
	}
	defer connection.Close(ctx)
	var documents, storedPassages, jobs, attempts int
	var leaseReleased bool
	err = connection.QueryRow(ctx, `SELECT
 (SELECT count(*) FROM cairn.semantic_document),
 (SELECT count(*) FROM cairn.semantic_passage),
 (SELECT count(*) FROM cairn.semantic_job),
 COALESCE((SELECT attempts FROM cairn.semantic_job LIMIT 1),0),
 COALESCE((SELECT lease_id IS NULL AND lease_until IS NULL FROM cairn.semantic_job LIMIT 1),true)`).Scan(
		&documents, &storedPassages, &jobs, &attempts, &leaseReleased)
	if err != nil {
		return nil, err
	}
	hash := sha256.Sum256([]byte(body))
	search, searchErr := index.Search(ctx, core.SemanticRankRequest{Query: "source span control", Notes: []core.SemanticNote{{
		RecordID: record.RecordID, Version: record.Version, BodySHA256: hex.EncodeToString(hash[:]), Body: body}}})
	reader, err := core.OpenWithSemanticRetriever(ctx, dsn, core.Channel{Principal: "s01b-residual-store"}, index.Search)
	if err != nil {
		return nil, err
	}
	defer reader.Close()
	compiled, compileErr := reader.Index(ctx, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{
		Repo: repo, TaskID: "fixture", RunID: "current"}, Query: "source span control", Purpose: "context", Semantic: true, AvailableTokens: 32000}, core.Destination{Name: "hosted"})
	var extension string
	if err = connection.QueryRow(ctx, "SELECT extversion FROM pg_extension WHERE extname='vector'").Scan(&extension); err != nil {
		return nil, err
	}
	return map[string]any{"id": name, "scope": "Go/Index.Step-vectorText/disposable-store/model-double",
		"input": map[string]any{"body": body, "passages": inputBits(passages)},
		"step":  observed(worked, stepErr), "search": observed(search, searchErr),
		"discovery":          observed(compiled.Package.Semantic.Discovery, compileErr),
		"store":              map[string]any{"documents": documents, "passages": storedPassages, "jobs": jobs, "attempts": attempts, "lease_released": leaseReleased},
		"model_double_calls": map[string]int{"identity": model.identities, "document": model.documents, "query": model.queries},
		"database":           database, "pgvector_version": extension, "model_initialized": false, "real_model_inference": false}, nil
}

func store(output string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	admin, err := pgx.Connect(ctx, "host="+pgRoot+"/socket user=s01bfixturepg dbname=postgres sslmode=disable")
	if err != nil {
		return err
	}
	defer admin.Close(ctx)
	var directory, addresses, sockets, version string
	if err = admin.QueryRow(ctx, "SELECT current_setting('data_directory'),current_setting('listen_addresses'),current_setting('unix_socket_directories'),version()").Scan(
		&directory, &addresses, &sockets, &version); err != nil {
		return err
	}
	if directory != pgRoot+"/data" || addresses != "" || sockets != pgRoot+"/socket" {
		return fmt.Errorf("not the fixed fresh private fixture cluster")
	}
	rows := make([]map[string]any, 0, len(storeCases))
	for number, name := range storeCases {
		row, err := storeCase(ctx, admin, number, name)
		if err != nil {
			return fmt.Errorf("%s: %w", name, err)
		}
		rows = append(rows, row)
	}
	data, err := json.Marshal(map[string]any{"schema": "cairn.semantic-store-refusals/1", "cases": rows,
		"postgres_version": version, "data_directory": directory, "unix_socket": sockets, "model_execution": false})
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(output, "store-observations.json"), append(data, '\n'), 0600)
}

func idle(launcher, events string) error {
	owner, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	rank, closeWorker, err := semantic.StreamCommandWithIdleTimeout(owner, launcher, 500*time.Millisecond)
	if err != nil {
		return err
	}
	defer closeWorker()
	var calls []call
	request := core.SemanticRankRequest{Query: "query"}
	for n := 0; n < 2; n++ {
		result, err := rank(owner, request)
		calls = append(calls, observed(result, err))
	}
	raw, err := os.ReadFile(events)
	if err != nil {
		return err
	}
	var first struct {
		PID   int    `json:"pid"`
		Start string `json:"start_ticks"`
	}
	if err = json.Unmarshal([]byte(strings.Split(string(raw), "\n")[0]), &first); err != nil {
		return err
	}
	if first.PID <= 1 || first.Start == "" {
		return fmt.Errorf("invalid owned child identity")
	}
	deadline := time.Now().Add(5 * time.Second)
	absent := false
	for time.Now().Before(deadline) {
		stat, err := os.ReadFile("/proc/" + strconv.Itoa(first.PID) + "/stat")
		if os.IsNotExist(err) {
			absent = true
			break
		}
		if err != nil {
			return err
		}
		fields := strings.Fields(string(stat[strings.LastIndex(string(stat), ") ")+2:]))
		if len(fields) < 20 || fields[19] != first.Start {
			return fmt.Errorf("owned PID identity changed before absence was observed")
		}
		time.Sleep(10 * time.Millisecond)
	}
	if !absent {
		return fmt.Errorf("idle child did not leave /proc before replacement")
	}
	result, err := rank(owner, request)
	calls = append(calls, observed(result, err))
	closeWorker()
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"case": "residual-idle", "calls": calls,
		"old_child_absent_before_replacement": absent, "old_child_pid": first.PID, "old_child_start_ticks": first.Start})
}

func main() {
	var err error
	if len(os.Args) == 3 && os.Args[1] == "store" {
		err = store(os.Args[2])
	} else if len(os.Args) == 4 && os.Args[1] == "idle" {
		err = idle(os.Args[2], os.Args[3])
	} else {
		fmt.Fprintln(os.Stderr, "expected fixed store or idle fixture mode")
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
