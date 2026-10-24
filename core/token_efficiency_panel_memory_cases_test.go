package core

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"testing"

	"github.com/google/uuid"
)

type panelMemorySource struct {
	Project        string   `json:"project"`
	Body           string   `json:"body"`
	Kind           string   `json:"kind"`
	Sensitivity    string   `json:"sensitivity"`
	Scope          Scope    `json:"scope"`
	Supersedes     []string `json:"supersedes"`
	RecordClass    string   `json:"record_class"`
	Operation      string   `json:"operation"`
	RecordFamily   string   `json:"record_family"`
	LogicalVersion int      `json:"logical_version"`
	ClaimType      string   `json:"claim_type"`
}

type panelMemoryCase struct {
	ID    string `json:"id"`
	Setup struct {
		Sources     []string          `json:"sources"`
		Events      []json.RawMessage `json:"events"`
		Transitions []struct {
			Action string `json:"action"`
			Target string `json:"target"`
		} `json:"transitions"`
	} `json:"setup"`
}

type panelMemoryFixture struct {
	Sources map[string]panelMemorySource `json:"sources"`
	Cases   []panelMemoryCase            `json:"cases"`
}

func readPanelMemoryFixture(t *testing.T) panelMemoryFixture {
	t.Helper()
	raw, err := os.ReadFile("../trials/token-efficiency/FIXTURE.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture panelMemoryFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func (fixture panelMemoryFixture) memoryCase(t *testing.T, name string, sourceIDs ...string) panelMemoryCase {
	t.Helper()
	var found *panelMemoryCase
	for i := range fixture.Cases {
		if fixture.Cases[i].ID == name {
			if found != nil {
				t.Fatalf("duplicate panel case %s", name)
			}
			found = &fixture.Cases[i]
		}
	}
	if found == nil || len(found.Setup.Sources) != len(sourceIDs) || len(found.Setup.Events) != 0 {
		t.Fatalf("%s: missing or changed memory setup", name)
	}
	for i, id := range sourceIDs {
		if found.Setup.Sources[i] != id {
			t.Fatalf("%s: source %d changed: %s", name, i, found.Setup.Sources[i])
		}
	}
	return *found
}

func (fixture panelMemoryFixture) sourceDraft(t *testing.T, name string) Draft {
	t.Helper()
	source, ok := fixture.Sources[name]
	if !ok || source.RecordClass != "A" || source.Sensitivity != "shareable" ||
		source.ClaimType != "self" || source.Scope != (Scope{Repo: "fixture:cairn-token-efficiency", TaskID: "*", RunID: "*"}) {
		t.Fatalf("%s: unsupported native source contract: %+v", name, source)
	}
	return Draft{Kind: source.Kind, Body: source.Body, Scope: source.Scope,
		Sensitivity: source.Sensitivity, ClaimType: source.ClaimType}
}

func panelMemoryEntry(t *testing.T, index IndexResult, recordID string) (IndexEntry, string, int) {
	t.Helper()
	for position, entry := range index.Package.Semantic.Index {
		if entry.RecordID != recordID {
			continue
		}
		for _, handle := range index.Handles {
			if handle.RecordID == recordID && handle.Version == entry.Version {
				return entry, handle.Handle, position
			}
		}
		t.Fatalf("indexed source %s has no matching pull handle", recordID)
	}
	t.Fatalf("source %s absent from index", recordID)
	return IndexEntry{}, "", -1
}

func panelMemoryPullWhole(t *testing.T, store *Store, index IndexResult, record Record, dest Destination) {
	t.Helper()
	entry, handle, _ := panelMemoryEntry(t, index, record.RecordID)
	if entry.Version != record.Version || entry.Class != "A" {
		t.Fatalf("index changed source identity: %+v", entry)
	}
	pulled, err := store.Expand(context.Background(), ExpandRequest{
		RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: handle}, dest)
	if err != nil || pulled.Span != nil || pulled.Selection.Record.RecordID != record.RecordID ||
		pulled.Selection.Record.Version != record.Version || pulled.Selection.Record.Body != record.Body {
		t.Fatalf("whole source pull changed: %+v %v", pulled, err)
	}
}

// Each case uses its own disposable database to preserve the fixture's exact
// shared repository scope without another case's mandatory C conflict. These
// tests cover native source behavior; they do not grade end-to-end task outcomes.
func TestTokenEfficiencyPanelMemorySources(t *testing.T) {
	fixture := readPanelMemoryFixture(t)
	dest := Destination{Name: "hosted", AllowLocal: false}

	t.Run("semantic_unavailable", func(t *testing.T) {
		row := fixture.memoryCase(t, "semantic_unavailable", "lexical_anchor")
		if len(row.Setup.Transitions) != 1 || row.Setup.Transitions[0].Action != "disable_semantic" ||
			row.Setup.Transitions[0].Target != "lexical_anchor" {
			t.Fatal("semantic-unavailable transition changed")
		}
		source := fixture.Sources["lexical_anchor"]
		if source.Operation != "create" || source.LogicalVersion != 1 || source.Kind != "procedure" || source.Project != "Cairn" {
			t.Fatal("lexical anchor metadata changed")
		}
		_, openStore := reviewDatabase(t)
		store := openStore(Channel{Principal: "agent:panel-lexical-" + uuid.NewString(), Repo: source.Scope.Repo})
		stored, err := store.Create(context.Background(), CreateRequest{
			RequestID: uuid.NewString(), Draft: fixture.sourceDraft(t, "lexical_anchor")})
		if err != nil || stored.Body != source.Body || stored.ObservedWriter != store.channel.Principal {
			t.Fatalf("lexical anchor creation: %+v %v", stored, err)
		}
		store.semanticRanker = func(context.Context, SemanticRankRequest) (SemanticRankResult, error) {
			return SemanticRankResult{}, errors.New("synthetic semantic worker unavailable")
		}
		index, err := store.Index(context.Background(), CompileRequest{RequestID: uuid.NewString(),
			Scope: Scope{Repo: source.Scope.Repo, TaskID: "task", RunID: "run"}, Query: "RAVEN-42",
			Purpose: "context", AvailableTokens: 64000, Mode: "index", Semantic: true}, dest)
		if err != nil || index.Package.Semantic.Discovery == nil ||
			index.Package.Semantic.Discovery.State != "unavailable" ||
			index.Package.Semantic.Status != "DEGRADED_NO_EMBEDDINGS" {
			t.Fatalf("semantic failure did not retain lexical discovery: %+v %v", index, err)
		}
		panelMemoryPullWhole(t, store, index, stored, dest)
	})

	t.Run("wrong_project_memory", func(t *testing.T) {
		// Project is fixture metadata and source text, not an authenticated scope.
		row := fixture.memoryCase(t, "wrong_project_memory", "trail_atlas", "cairn_atlas")
		if len(row.Setup.Transitions) != 0 || fixture.Sources["trail_atlas"].Project != "Trail" ||
			fixture.Sources["cairn_atlas"].Project != "Cairn" {
			t.Fatal("project labels or setup changed")
		}
		_, openStore := reviewDatabase(t)
		store := openStore(Channel{Principal: "agent:panel-project-" + uuid.NewString(), Repo: fixture.Sources["trail_atlas"].Scope.Repo})
		var records [2]Record
		for i, sourceID := range row.Setup.Sources {
			source := fixture.Sources[sourceID]
			if source.Operation != "create" || source.LogicalVersion != 1 || source.Kind != "note" {
				t.Fatalf("%s: project source metadata changed", sourceID)
			}
			created, err := store.Create(context.Background(), CreateRequest{
				RequestID: uuid.NewString(), Draft: fixture.sourceDraft(t, sourceID)})
			if err != nil || created.Body != source.Body || created.ObservedWriter != store.channel.Principal {
				t.Fatalf("%s: native create: %+v %v", sourceID, created, err)
			}
			records[i] = created
		}
		index, err := store.Index(context.Background(), CompileRequest{RequestID: uuid.NewString(),
			Scope: Scope{Repo: fixture.Sources["trail_atlas"].Scope.Repo, TaskID: "task", RunID: "run"}, Query: "Cairn Atlas",
			Purpose: "context", AvailableTokens: 64000, Mode: "index"}, dest)
		if err != nil {
			t.Fatal(err)
		}
		_, _, trailPosition := panelMemoryEntry(t, index, records[0].RecordID)
		_, _, cairnPosition := panelMemoryEntry(t, index, records[1].RecordID)
		if cairnPosition >= trailPosition {
			t.Fatalf("Cairn source did not rank before Trail: Cairn=%d Trail=%d", cairnPosition, trailPosition)
		}
		for _, record := range records {
			panelMemoryPullWhole(t, store, index, record, dest)
		}
	})

	t.Run("meaningful_owner_correction", func(t *testing.T) {
		// This exercises A currentness, not an authenticated owner correction.
		row := fixture.memoryCase(t, "meaningful_owner_correction", "owner_old", "owner_new")
		old, current := fixture.Sources[row.Setup.Sources[0]], fixture.Sources[row.Setup.Sources[1]]
		if len(row.Setup.Transitions) != 1 || row.Setup.Transitions[0].Action != "apply_correction" ||
			row.Setup.Transitions[0].Target != "owner_new" || old.Project != "Cairn" ||
			old.Kind != "preference" || old.Operation != "create" || old.LogicalVersion != 1 ||
			current.Kind != "preference" || current.Operation != "edit" || current.LogicalVersion != 2 ||
			current.RecordFamily != old.RecordFamily || len(current.Supersedes) != 1 ||
			current.Supersedes[0] != "owner_old" {
			t.Fatal("correction transition or lineage changed")
		}
		_, openStore := reviewDatabase(t)
		store := openStore(Channel{Principal: "agent:panel-preference-" + uuid.NewString(), Repo: old.Scope.Repo})
		first, err := store.Create(context.Background(), CreateRequest{
			RequestID: uuid.NewString(), Draft: fixture.sourceDraft(t, "owner_old")})
		if err != nil {
			t.Fatal(err)
		}
		second, err := store.Edit(context.Background(), EditRequest{RequestID: uuid.NewString(),
			RecordID: first.RecordID, ExpectedVersion: first.Version,
			Draft: fixture.sourceDraft(t, "owner_new")})
		if err != nil || first.Version != 1 || second.RecordID != first.RecordID || second.Version != 2 ||
			second.Body != current.Body || second.ObservedWriter != store.channel.Principal {
			t.Fatalf("native correction: %+v %v", second, err)
		}
		history, err := store.History(context.Background(), RecordHistoryRequest{
			RecordID: first.RecordID, Version: 1}, dest)
		if err != nil || len(history.Versions) != 1 || history.Versions[0].Body == nil ||
			*history.Versions[0].Body != old.Body || history.CurrentVersion != 2 {
			t.Fatalf("old preference not retained as history: %+v %v", history, err)
		}
		index, err := store.Index(context.Background(), CompileRequest{RequestID: uuid.NewString(),
			Scope: Scope{Repo: old.Scope.Repo, TaskID: "task", RunID: "run"}, Query: "routine notices urgent requests",
			Purpose: "context", AvailableTokens: 64000, Mode: "index"}, dest)
		if err != nil {
			t.Fatal(err)
		}
		panelMemoryPullWhole(t, store, index, second, dest)
	})
}
