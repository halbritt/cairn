package semantic

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

type fixtureEmbedder struct {
	documents int
	modelHash string
}

type delayedEmbedder struct {
	fixtureEmbedder
	target  string
	started chan struct{}
	release chan struct{}
}

func (e *delayedEmbedder) Document(ctx context.Context, body string) ([]EmbeddedPassage, error) {
	if body == e.target {
		close(e.started)
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-e.release:
		}
	}
	return e.fixtureEmbedder.Document(ctx, body)
}

func (e *fixtureEmbedder) Identity(context.Context) (EmbeddingIdentity, error) {
	hash := e.modelHash
	if hash == "" {
		hash = strings.Repeat("a", 64)
	}
	return EmbeddingIdentity{ModelSHA256: hash, Algorithm: "fixture/1", Dimensions: 384}, nil
}

func TestRealPersistentPassageDiscovery(t *testing.T) {
	path := os.Getenv("CAIRN_EMBEDDING_WORKER")
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if path == "" || dsn == "" {
		t.Skip("requires local prepared embedding worker and disposable PostgreSQL")
	}
	ctx := context.Background()
	s, err := core.Open(ctx, dsn, core.Channel{Principal: "real-index-fixture"})
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	repo := uuid.NewString()
	body := strings.Repeat("The orchard grows pears, apples and oranges. ", 150) + "Keep the database in the private application directory."
	draft := core.Draft{Kind: "lesson", Body: body, Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}
	want, err := s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	for n := range 64 {
		draft.Body = fmt.Sprintf("Orchard %d harvests ripe pears, apples and oranges.", n)
		if _, err = s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft}); err != nil {
			t.Fatal(err)
		}
	}
	model, err := EmbeddingCommand(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer model.Close()
	idx, err := OpenIndex(ctx, dsn, model)
	if err != nil {
		t.Fatal(err)
	}
	defer idx.Close()
	for n := 0; n < 100; n++ {
		worked, err := idx.Step(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if !worked {
			break
		}
	}
	reader, err := core.OpenWithSemanticRetriever(ctx, dsn, core.Channel{Principal: "real-index-fixture"}, idx.Search)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	req := core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Query: "persistent storage location", Purpose: "context", Semantic: true, AvailableTokens: 32000}
	started := time.Now()
	result, err := reader.Index(ctx, req, core.Destination{Name: "hosted"})
	t.Logf("real model plus store search: %s", time.Since(started))
	if err != nil {
		t.Fatal(err)
	}
	entries := result.Package.Semantic.Index
	if result.Package.Semantic.Discovery.State != "ready" || len(entries) == 0 || entries[0].RecordID != want.RecordID {
		t.Fatalf("real semantic match missing: %+v", result.Package.Semantic)
	}
	span := entries[0].MatchSpan
	if span == nil || !strings.Contains(body[span.Offset:span.Offset+span.Length], "Keep the database") {
		t.Fatalf("winning passage failed to locate relevant guidance: %+v", span)
	}
	replayed, err := reader.Recompile(ctx, core.RecompileRequest{ReceiptID: result.Package.ReceiptID, Query: req.Query})
	if err != nil || replayed.Seal != result.Package.Seal {
		t.Fatalf("real frozen replay: %v", err)
	}
}
func (e *fixtureEmbedder) Query(context.Context, string) ([]float32, error) {
	v := make([]float32, 384)
	v[0] = 1
	return v, nil
}
func (e *fixtureEmbedder) Document(_ context.Context, body string) ([]EmbeddedPassage, error) {
	e.documents++
	v := make([]float32, 384)
	v[0] = 1
	return []EmbeddedPassage{{Span: core.ByteSpanRequest{Length: len(body)}, Vector: v}}, nil
}

func TestPersistentIndexSurvivesRestartAndInvalidatesEditedSource(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires disposable PostgreSQL")
	}
	ctx := context.Background()
	s, err := core.Open(ctx, dsn, core.Channel{Principal: "index-fixture"})
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	body := "Keep the database in the private application directory."
	draft := core.Draft{Kind: "lesson", Body: body, Sensitivity: "shareable", Scope: core.Scope{Repo: uuid.NewString(), TaskID: "*", RunID: "*"}, ClaimType: "self"}
	record, err := s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	model := &fixtureEmbedder{}
	idx, err := OpenIndex(ctx, dsn, model)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 100; i++ {
		worked, err := idx.Step(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if !worked {
			break
		}
	}
	idx.Close()
	if model.documents == 0 {
		t.Fatal("no source was embedded")
	}
	model = &fixtureEmbedder{}
	idx, err = OpenIndex(ctx, dsn, model)
	if err != nil {
		t.Fatal(err)
	}
	defer idx.Close()
	hash := sha256.Sum256([]byte(body))
	req := core.SemanticRankRequest{Query: "storage", Notes: []core.SemanticNote{{RecordID: record.RecordID, Version: 1, BodySHA256: hex.EncodeToString(hash[:]), Body: body}}}
	result, err := idx.Search(ctx, req)
	if err != nil || result.Indexed != 1 || len(result.Hits) != 1 || result.Hits[0].RecordID != record.RecordID || model.documents != 0 {
		t.Fatalf("persistent lookup: %+v %v embedded=%d", result, err, model.documents)
	}
	changedModel := &fixtureEmbedder{modelHash: strings.Repeat("b", 64)}
	changed, err := OpenIndex(ctx, dsn, changedModel)
	if err != nil {
		t.Fatal(err)
	}
	defer changed.Close()
	result, err = changed.Search(ctx, req)
	if err != nil || result.Indexed != 0 || len(result.Hits) != 0 {
		t.Fatalf("old vectors mixed with new model: %+v %v", result, err)
	}
	for n := 0; n < 100; n++ {
		worked, err := changed.Step(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if !worked {
			break
		}
	}
	result, err = changed.Search(ctx, req)
	if err != nil || result.Indexed != 1 || len(result.Hits) != 1 || changedModel.documents == 0 {
		t.Fatalf("new model failed to rebuild: %+v %v", result, err)
	}
	idx = changed
	if _, err = s.Revise(ctx, core.ReviseRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: 1, Repo: draft.Scope.Repo, Body: "The directory moved."}); err != nil {
		t.Fatal(err)
	}
	result, err = idx.Search(ctx, req)
	if err != nil || result.Indexed != 0 || len(result.Hits) != 0 {
		t.Fatalf("edited source survived: %+v %v", result, err)
	}
}

func TestIndexEditDuringEmbeddingCannotPublishStalePassages(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires disposable PostgreSQL")
	}
	ctx := context.Background()
	s, err := core.Open(ctx, dsn, core.Channel{Principal: "index-concurrency"})
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	body := "old source " + uuid.NewString()
	d := core.Draft{Kind: "lesson", Body: body, Sensitivity: "shareable", Scope: core.Scope{Repo: uuid.NewString(), TaskID: "*", RunID: "*"}, ClaimType: "self"}
	r, err := s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: d})
	if err != nil {
		t.Fatal(err)
	}
	model := &delayedEmbedder{target: body, started: make(chan struct{}), release: make(chan struct{})}
	idx, err := OpenIndex(ctx, dsn, model)
	if err != nil {
		t.Fatal(err)
	}
	defer idx.Close()
	done := make(chan error, 1)
	go func() {
		for n := 0; n < 100; n++ {
			worked, err := idx.Step(ctx)
			if err != nil {
				done <- err
				return
			}
			if !worked {
				done <- nil
				return
			}
		}
		done <- nil
	}()
	select {
	case <-model.started:
	case <-time.After(3 * time.Second):
		t.Fatal("embedding did not begin")
	}
	editCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	_, err = s.Revise(editCtx, core.ReviseRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: 1, Repo: d.Scope.Repo, Body: "corrected source"})
	close(model.release)
	if err != nil {
		t.Fatal(err)
	}
	if err = <-done; err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256([]byte(body))
	old := core.SemanticRankRequest{Query: "source", Notes: []core.SemanticNote{{RecordID: r.RecordID, Version: 1, BodySHA256: hex.EncodeToString(hash[:]), Body: body}}}
	result, err := idx.Search(ctx, old)
	if err != nil || result.Indexed != 0 || len(result.Hits) != 0 {
		t.Fatalf("stale embedding published: %+v %v", result, err)
	}
	hash = sha256.Sum256([]byte("corrected source"))
	fresh := core.SemanticRankRequest{Query: "source", Notes: []core.SemanticNote{{RecordID: r.RecordID, Version: 2, BodySHA256: hex.EncodeToString(hash[:]), Body: "corrected source"}}}
	result, err = idx.Search(ctx, fresh)
	if err != nil || result.Indexed != 1 || len(result.Hits) != 1 {
		t.Fatalf("new version lost after old attempt: %+v %v", result, err)
	}
	if _, err = s.Delete(ctx, core.DeleteRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: 2}); err != nil {
		t.Fatal(err)
	}
	result, err = idx.Search(ctx, fresh)
	if err != nil || result.Indexed != 0 || len(result.Hits) != 0 {
		t.Fatalf("deleted passages remain: %+v %v", result, err)
	}
}

type projectedFixtureEmbedder struct {
	fixtureEmbedder
	original string
}

func (e *projectedFixtureEmbedder) QueryWithProjection(ctx context.Context, text string) ([]float32, *core.SemanticQueryProjection, error) {
	e.original = text
	v, err := e.Query(ctx, text)
	prefix := strings.Split(text, " ")[0]
	sum := sha256.Sum256([]byte(prefix))
	return v, &core.SemanticQueryProjection{Method: "original-prefix/1", Truncated: true, OriginalTokens: 600, EmbeddedTokens: 20, PrefixBytes: len(prefix), PrefixSHA256: hex.EncodeToString(sum[:])}, err
}
func TestPersistentIndexCarriesProjectionWithoutReplacingQuery(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires disposable PostgreSQL")
	}
	ctx := context.Background()
	s, err := core.Open(ctx, dsn, core.Channel{Principal: "projection-fixture"})
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	body := "Keep full lexical source selection."
	record, err := s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "lesson", Body: body, Sensitivity: "shareable", Scope: core.Scope{Repo: uuid.NewString(), TaskID: "*", RunID: "*"}, ClaimType: "self"}})
	if err != nil {
		t.Fatal(err)
	}
	model := &projectedFixtureEmbedder{}
	idx, err := OpenIndex(ctx, dsn, model)
	if err != nil {
		t.Fatal(err)
	}
	defer idx.Close()
	for range 100 {
		worked, err := idx.Step(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if !worked {
			break
		}
	}
	sum := sha256.Sum256([]byte(body))
	query := "é漢🙂 " + strings.Repeat("full lexical constraints ", 35)
	result, err := idx.Search(ctx, core.SemanticRankRequest{Query: query, Notes: []core.SemanticNote{{RecordID: record.RecordID, Version: 1, BodySHA256: hex.EncodeToString(sum[:]), Body: body}}})
	if err != nil || result.Indexed != 1 || len(result.Hits) != 1 || model.original != query || result.QueryProjection == nil {
		t.Fatalf("projected index result: %+v %v", result, err)
	}
	if err = core.ValidateSemanticQueryProjection(result.QueryProjection, query); err != nil {
		t.Fatal(err)
	}
}
