package semantic

import (
	"bufio"
	"context"
	"encoding/json"
	"math"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/core"
)

func realRustConfig(t *testing.T, variable string) RustWorkerConfig {
	t.Helper()
	path := os.Getenv(variable)
	if path == "" {
		t.Skip("requires explicit real Rust assets/config inside the shared isolation wrapper")
	}
	config, err := ReadRustWorkerConfig(path)
	if err != nil {
		t.Fatal(err)
	}
	return config
}

func TestRustRealRankAndStreamIdleRestore(t *testing.T) {
	config := realRustConfig(t, "CAIRN_RUST_RANK_TEST_CONFIG")
	config.Mode = RustRank
	oneShot, err := RustCommand(config)
	if err != nil {
		t.Fatal(err)
	}
	// Use the frozen public fixture so the same note hashes/metadata exercise
	// all three Go launch/restore paths without an alternate inference path.
	request := frozenRustRankRequest(t)
	first, err := oneShot(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	requireAcceptanceScores(t, first, request)
	config.Mode = RustStream
	stream, closeWorker, err := RustStreamCommand(context.Background(), config, 30*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer closeWorker()
	for range 2 {
		result, err := stream(context.Background(), request)
		if err != nil || !reflect.DeepEqual(result, first) {
			t.Fatalf("real stream/one-shot mismatch: %v", err)
		}
	}
	t.Log("waiting for the unchanged 30-second production idle release")
	time.Sleep(31 * time.Second)
	result, err := stream(context.Background(), request)
	if err != nil || !reflect.DeepEqual(result, first) {
		t.Fatalf("real cache restore mismatch: %v", err)
	}
}

func frozenRustRankRequest(t *testing.T) core.SemanticRankRequest {
	t.Helper()
	file, err := os.Open(os.Getenv("CAIRN_RUST_RANK_TEST_REQUESTS"))
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	line, err := bufio.NewReader(file).ReadBytes('\n')
	if err != nil {
		t.Fatal(err)
	}
	var input struct {
		ID      string                   `json:"id"`
		Request core.SemanticRankRequest `json:"request"`
	}
	if err := json.Unmarshal(line, &input); err != nil || input.ID == "" || len(input.Request.Notes) == 0 {
		t.Fatalf("missing frozen rank request: %v", err)
	}
	return input.Request
}

func requireRustVector(t *testing.T, vector []float32) {
	t.Helper()
	if _, err := vectorText(vector); err != nil {
		t.Fatal(err)
	}
	norm := float64(0)
	for _, x := range vector {
		norm += float64(x) * float64(x)
	}
	if math.Abs(norm-1) > 1e-5 {
		t.Fatalf("real first-pass vector is not normalized: %g", norm)
	}
}

func TestRustRealEmbeddingHandshakeUTF8SpansAndLanes(t *testing.T) {
	config := realRustConfig(t, "CAIRN_RUST_EMBED_TEST_CONFIG")
	model, err := RustEmbeddingCommand(context.Background(), config)
	if err != nil {
		t.Fatal(err)
	}
	defer model.Close()
	identity, err := model.Identity(context.Background())
	if err != nil || identity.Dimensions != 384 || identity.Algorithm != "bge-original-passages/1" {
		t.Fatalf("real identity handshake failed: %+v %v", identity, err)
	}
	query := "Where is the café in 北京?"
	vector, projection, err := model.QueryWithProjection(context.Background(), query)
	if err != nil || projection == nil {
		t.Fatalf("real query projection: %v", err)
	}
	if err := core.ValidateSemanticQueryProjection(projection, query); err != nil {
		t.Fatal(err)
	}
	requireRustVector(t, vector)
	body := strings.Repeat("héllo 北京 ", 140) + "fin"
	passages, err := model.Document(context.Background(), body)
	if err != nil || len(passages) < 2 {
		t.Fatalf("real multiple-passage document: count=%d %v", len(passages), err)
	}
	end := 0
	for _, passage := range passages {
		span := passage.Span
		if span.Offset < 0 || span.Length <= 0 || span.Offset > end || span.Offset+span.Length > len(body) || !utf8.ValidString(body[span.Offset:span.Offset+span.Length]) {
			t.Fatalf("invalid or missing UTF-8 passage coverage: %+v", span)
		}
		end = span.Offset + span.Length
		requireRustVector(t, passage.Vector)
	}
	if end != len(body) {
		t.Fatalf("last real passage stops at %d/%d bytes", end, len(body))
	}
	if model.query.worker == nil || model.document.worker == nil || model.query.worker.command.Process.Pid == model.document.worker.command.Process.Pid {
		t.Fatal("query and document lanes did not own distinct real worker processes")
	}
}
