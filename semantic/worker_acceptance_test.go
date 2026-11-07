package semantic

import (
	"context"
	"encoding/json"
	"os"
	"os/user"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/core"
)

// These opt-in model tests run inside the external acceptance namespace. Normal
// Go tests report their skips; the strict runner requires every selected test.
// These are smoke entry points: rank/stream use case 0; embed uses query-ascii
// and document-ascii. Complete case and cross-role parity belong to the external
// comparison inventory, not these three Go tests.
type acceptanceInputs struct {
	Rank []struct {
		ID string `json:"id"`
		core.SemanticRankRequest
	} `json:"rank"`
	Embedding []struct {
		ID        string `json:"id"`
		Operation string `json:"operation"`
		Text      string `json:"text"`
		Expected  string `json:"expected"`
	} `json:"embedding"`
}

func acceptanceFixture(t *testing.T, selector string) (string, acceptanceInputs) {
	t.Helper()
	if os.Getenv("CAIRN_SEMANTIC_ACCEPTANCE_REQUIRED") != "1" {
		t.Skip("requires the strict, externally isolated real-worker acceptance runner")
	}
	network, err := os.Readlink("/proc/self/ns/net")
	if err != nil || os.Getenv("CAIRN_SEMANTIC_ACCEPTANCE_HOST_NET") == "" || network == os.Getenv("CAIRN_SEMANTIC_ACCEPTANCE_HOST_NET") {
		t.Fatal("actual supervisor is not in a different network namespace")
	}
	owner, err := user.Current()
	if err != nil {
		t.Fatal(err)
	}
	for _, relative := range []string{".local/share/cairn/hosted-agent.token"} {
		if _, err := os.Stat(filepath.Join(owner.HomeDir, relative)); !os.IsNotExist(err) {
			t.Fatal("home lookup exposes pre-existing owner state")
		}
	}
	probe, err := os.Stat(filepath.Join(owner.HomeDir, ".semantic-isolation-probe"))
	expectedInode, parseErr := strconv.ParseUint(os.Getenv("CAIRN_SEMANTIC_ACCEPTANCE_HOME_INODE"), 10, 64)
	if err != nil || parseErr != nil || probe.Sys().(*syscall.Stat_t).Ino != expectedInode {
		t.Fatal("OS home lookup does not match the disposable mapped-home proof")
	}
	path := os.Getenv(selector)
	if err := validateCommand(path); err != nil {
		t.Fatalf("required %s: %v", selector, err)
	}
	raw, err := os.ReadFile(os.Getenv("CAIRN_SEMANTIC_ACCEPTANCE_INPUTS"))
	if err != nil {
		t.Fatal(err)
	}
	var inputs acceptanceInputs
	if err := json.Unmarshal(raw, &inputs); err != nil || len(inputs.Rank) != 19 || len(inputs.Embedding) != 15 {
		t.Fatalf("missing reviewed fixture inventory: %v", err)
	}
	return path, inputs
}

func requireAcceptanceScores(t *testing.T, result core.SemanticRankResult, request core.SemanticRankRequest) {
	t.Helper()
	if len(result.ModelSHA256) != 64 || result.Algorithm != "bge-max-chunk/1" || len(result.Scores) != len(request.Notes) {
		t.Fatal("invalid model identity or incomplete score result")
	}
	for n, score := range result.Scores {
		note := request.Notes[n]
		if score.RecordID != note.RecordID || score.Version != note.Version || score.BodySHA256 != note.BodySHA256 || score.Score < -1000000 || score.Score > 1000000 {
			t.Fatal("score metadata/order/bounds differ from input")
		}
	}
}

func TestWorkerAcceptanceRank(t *testing.T) {
	path, inputs := acceptanceFixture(t, "CAIRN_SEMANTIC_WORKER")
	rank, err := Command(path)
	if err != nil {
		t.Fatal(err)
	}
	request := inputs.Rank[0].SemanticRankRequest
	first, err := rank(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	requireAcceptanceScores(t, first, request)
	second, err := rank(context.Background(), request)
	if err != nil || !reflect.DeepEqual(first, second) {
		t.Fatalf("cold/repeated real rank result differs: %v", err)
	}
}

func TestWorkerAcceptanceStream(t *testing.T) {
	path, inputs := acceptanceFixture(t, "CAIRN_SEMANTIC_STREAM_WORKER")
	rank, closeWorker, err := StreamCommand(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer closeWorker()
	request := inputs.Rank[0].SemanticRankRequest
	first, err := rank(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	requireAcceptanceScores(t, first, request)
	second, err := rank(context.Background(), request)
	if err != nil || !reflect.DeepEqual(first, second) {
		t.Fatalf("warm real stream result differs: %v", err)
	}
	// Keep the actual 30-second production idle timer, unlike protocol doubles.
	t.Log("waiting for the unchanged stream idle release")
	time.Sleep(31 * time.Second)
	restored, err := rank(context.Background(), request)
	if err != nil || !reflect.DeepEqual(first, restored) {
		t.Fatalf("real idle restart/restore differs: %v", err)
	}
}

func TestWorkerAcceptanceEmbedding(t *testing.T) {
	path, inputs := acceptanceFixture(t, "CAIRN_EMBEDDING_WORKER")
	model, err := EmbeddingCommand(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer model.Close()
	identity, err := model.Identity(context.Background())
	if err != nil || identity.Dimensions != 384 || identity.Algorithm != "bge-original-passages/1" {
		t.Fatalf("real model did not initialize: %v", err)
	}
	query, body := "", ""
	for _, input := range inputs.Embedding {
		if input.Expected != "accepted" || input.ID == "document-maximum-punctuation" {
			t.Fatal("unreviewed or forbidden automatic acceptance input")
		}
		if input.ID == "query-ascii" {
			query = input.Text
		}
		if input.ID == "document-ascii" {
			body = input.Text
		}
	}
	if strings.TrimSpace(query) == "" || strings.TrimSpace(body) == "" {
		t.Fatal("missing named query/document smoke inputs")
	}
	vector, projection, err := model.QueryWithProjection(context.Background(), query)
	if err != nil || projection == nil {
		t.Fatalf("real query/projection failed: %v", err)
	}
	if _, err = vectorText(vector); err != nil {
		t.Fatal(err)
	}
	passages, err := model.Document(context.Background(), body)
	if err != nil || len(passages) == 0 || len(passages) > 256 {
		t.Fatalf("real document failed: %v", err)
	}
	for _, passage := range passages {
		if passage.Span.Offset < 0 || passage.Span.Length <= 0 || passage.Span.Offset+passage.Span.Length > len(body) || !utf8.ValidString(body[passage.Span.Offset:passage.Span.Offset+passage.Span.Length]) {
			t.Fatal("invalid original UTF-8 span")
		}
		if _, err = vectorText(passage.Vector); err != nil {
			t.Fatal(err)
		}
	}
}
