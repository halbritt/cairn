package semantic

import (
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

//go:embed testdata/indexed-comparison-inventory.json
var indexedInventoryJSON []byte

type indexedCase struct {
	ID     string `json:"id"`
	Family string `json:"family"`
	Query  string `json:"query"`
}

type indexedInput struct {
	WorkloadSHA256 string              `json:"workload_sha256"`
	Notes          []core.SemanticNote `json:"notes"`
	Cases          []indexedCase       `json:"cases"`
}

type indexedView struct {
	ID                string                        `json:"id"`
	Indexed           int                           `json:"indexed"`
	Eligible          int                           `json:"eligible"`
	Hits              []core.SemanticPassageHit     `json:"sql_hit_order"`
	QueryProjection   *core.SemanticQueryProjection `json:"query_projection"`
	CompilerEntries   []core.IndexEntry             `json:"compiler_returned_order"`
	CompilerDiscovery *core.DiscoveryRanking        `json:"compiler_discovery"`
	Ranking           string                        `json:"ranking"`
	Schema            string                        `json:"schema"`
	ReplayEqual       bool                          `json:"replay_equal"`
}

type indexedObservation struct {
	View               indexedView `json:"discrete"`
	QueryVector        []float32   `json:"query_vector"`
	Distances          []float64   `json:"sql_distances"`
	MinimumPositiveGap *float64    `json:"minimum_positive_distance_gap,omitempty"`
}

type indexedStoredVector struct {
	RecordID string               `json:"record_id"`
	Ordinal  int                  `json:"ordinal"`
	Span     core.ByteSpanRequest `json:"span"`
	Vector   []float32            `json:"vector"`
}

type indexedLane struct {
	Identity     EmbeddingIdentity     `json:"identity"`
	RebuildSteps int                   `json:"rebuild_steps"`
	Cases        []indexedObservation  `json:"cases"`
	Stored       []indexedStoredVector `json:"stored_vectors"`
}

type indexedReport struct {
	Input        indexedInput                   `json:"input"`
	Sources      map[string][]core.SemanticNote `json:"sources"`
	Python       indexedLane                    `json:"python"`
	Rust         indexedLane                    `json:"rust"`
	Complete     bool                           `json:"complete"`
	Differences  []string                       `json:"discrete_differences"`
	VectorPairs  int                            `json:"vector_pairs"`
	MaxCosineGap float64                        `json:"max_abs_one_minus_cosine"`
}

// Observe actual Go-supervised query vectors without replacing either worker.
type indexedObservedModel struct {
	*CommandEmbedder
	last []float32
}

func (m *indexedObservedModel) QueryWithProjection(ctx context.Context, query string) ([]float32, *core.SemanticQueryProjection, error) {
	v, p, err := m.CommandEmbedder.QueryWithProjection(ctx, query)
	m.last = append([]float32(nil), v...)
	return v, p, err
}

func TestIndexedPythonRustComparison(t *testing.T) {
	inputPath, output := os.Getenv("CAIRN_INDEXED_COMPARISON_INPUT"), os.Getenv("CAIRN_INDEXED_COMPARISON_REPORT")
	if inputPath == "" && output == "" {
		t.Skip("requires selected indexed comparison inputs and disposable store")
	}
	if inputPath == "" || output == "" || os.Getenv("CAIRN_TEST_DATABASE_URL") == "" || os.Getenv("CAIRN_EMBEDDING_WORKER") == "" || os.Getenv("CAIRN_RUST_EMBED_TEST_CONFIG") == "" {
		t.Fatal("selected indexed comparison requires both actual backends, input, report and disposable DSN")
	}
	data, err := os.ReadFile(inputPath)
	if err != nil {
		t.Fatal(err)
	}
	report := indexedReport{Differences: []string{}}
	if err = json.Unmarshal(data, &report.Input); err != nil {
		t.Fatal(err)
	}
	defer func() {
		data, err := json.MarshalIndent(report, "", "  ")
		if err != nil {
			t.Error(err)
			return
		}
		if err = os.WriteFile(output, data, 0600); err != nil {
			t.Error(err)
		}
	}()
	if report.Input.WorkloadSHA256 != "30b9b9852c91bac38836c8d121244ee3e762445ac505d5845dc454bae90eaec0" || len(report.Input.Notes) != 12 || len(report.Input.Cases) != 26 {
		t.Fatal("frozen 12-source / 17-workload / 9-boundary inventory missing")
	}
	seen := map[string]bool{}
	families := map[string]int{}
	var inventory struct {
		Workload    []string          `json:"workload_queries"`
		Boundary    []string          `json:"boundary_queries"`
		QueryHashes map[string]string `json:"query_input_sha256"`
	}
	if err = json.Unmarshal(indexedInventoryJSON, &inventory); err != nil {
		t.Fatal(err)
	}
	if len(inventory.Workload) != 17 || len(inventory.Boundary) != 9 || len(inventory.QueryHashes) != 26 {
		t.Fatal("committed query inventory is incomplete")
	}
	for i, id := range append(inventory.Workload, inventory.Boundary...) {
		if i >= len(report.Input.Cases) || report.Input.Cases[i].ID != id {
			t.Fatal("frozen query identity/order differs from committed inventory")
		}
		sum := sha256.Sum256([]byte(report.Input.Cases[i].Query))
		if hex.EncodeToString(sum[:]) != inventory.QueryHashes[id] {
			t.Fatalf("frozen query input mismatch for %s", id)
		}
	}
	config, err := ReadRustWorkerConfig(os.Getenv("CAIRN_RUST_EMBED_TEST_CONFIG"))
	if err != nil {
		t.Fatal(err)
	}
	if config.Mode != RustEmbed {
		t.Fatal("selected indexed comparison requires the Rust embedding backend")
	}
	workload, err := os.ReadFile(filepath.Join(config.SourceRoot, "core/testdata/retrieval-quality.json"))
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(workload)
	if hex.EncodeToString(sum[:]) != report.Input.WorkloadSHA256 {
		t.Fatal("frozen workload bytes differ")
	}
	var frozen struct {
		Notes []struct{ ID, Body string }
	}
	if err = json.Unmarshal(workload, &frozen); err != nil || len(frozen.Notes) != len(report.Input.Notes) {
		t.Fatal("frozen source inventory differs")
	}
	for i, expected := range frozen.Notes {
		note := report.Input.Notes[i]
		sum := sha256.Sum256([]byte(expected.Body))
		if note.RecordID != uuid.NewSHA1(uuid.NameSpaceURL, []byte(expected.ID)).String() || note.Version != 1 || note.Body != expected.Body || note.BodySHA256 != hex.EncodeToString(sum[:]) {
			t.Fatalf("frozen source input mismatch for %s", expected.ID)
		}
	}
	for _, c := range report.Input.Cases {
		if c.ID == "" || seen[c.ID] {
			t.Fatal("duplicate or empty matrix case ID")
		}
		seen[c.ID] = true
		families[c.Family]++
	}
	if families["workload"] != 17 || families["query-boundary"] != 9 {
		t.Fatal("wrong named case families")
	}
	ctx := context.Background()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	store, err := core.Open(ctx, dsn, core.Channel{Principal: "s03-indexed-comparison"})
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if err = store.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	report.Sources = map[string][]core.SemanticNote{}
	for _, n := range report.Input.Notes {
		report.Sources["workload"] = append(report.Sources["workload"], indexedCreateSource(t, store, "workload", n.Body))
	}
	for _, body := range []string{"Keep the database in the private application directory.", "Keep the database in the private application directory.", "Keep the database in a private application directory."} {
		report.Sources["ties"] = append(report.Sources["ties"], indexedCreateSource(t, store, "ties", body))
	}
	indexedRunLane(t, dsn, "python", report.Input.Cases, report.Sources, &report.Python)
	indexedRunLane(t, dsn, "rust", report.Input.Cases, report.Sources, &report.Rust)
	if report.Python.Identity.Algorithm != "bge-original-passages/1" || report.Rust.Identity.Algorithm != report.Python.Identity.Algorithm {
		t.Fatal("configured backends disagree on the accepted embedding algorithm")
	}
	if len(report.Python.Cases) != 29 || len(report.Rust.Cases) != 29 {
		t.Fatal("26 queries plus tie/partial/unavailable cases were not all observed")
	}
	for i, p := range report.Python.Cases {
		r := report.Rust.Cases[i]
		if !reflect.DeepEqual(p.View, r.View) {
			report.Differences = append(report.Differences, p.View.ID)
		}
		report.MaxCosineGap = math.Max(report.MaxCosineGap, indexedVectorGap(t, p.QueryVector, r.QueryVector))
		report.VectorPairs++
	}
	if len(report.Python.Stored) != len(report.Rust.Stored) || len(report.Python.Stored) == 0 {
		t.Fatal("stored passage vector inventory differs or is empty")
	}
	for i, p := range report.Python.Stored {
		r := report.Rust.Stored[i]
		if p.RecordID != r.RecordID || p.Ordinal != r.Ordinal || p.Span != r.Span {
			t.Fatal("stored original passage identity/span/order differs")
		}
		report.MaxCosineGap = math.Max(report.MaxCosineGap, indexedVectorGap(t, p.Vector, r.Vector))
		report.VectorPairs++
	}
	if len(report.Differences) != 0 {
		t.Fatalf("exact indexed discrete differences: %v", report.Differences)
	}
	report.Complete = true
	t.Logf("S03 complete: 26 frozen + 3 state/tie cases per backend; %d vector pairs; max cosine gap %.17g", report.VectorPairs, report.MaxCosineGap)
}

func indexedCreateSource(t *testing.T, s *core.Store, corpus, body string) core.SemanticNote {
	t.Helper()
	r, err := s.Create(context.Background(), core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "lesson", Body: body, Sensitivity: "shareable", Scope: core.Scope{Repo: "s03-" + corpus, TaskID: "*", RunID: "*"}, ClaimType: "self"}})
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256([]byte(body))
	return core.SemanticNote{RecordID: r.RecordID, Version: r.Version, Body: body, BodySHA256: hex.EncodeToString(hash[:])}
}

func indexedRunLane(t *testing.T, dsn, backend string, cases []indexedCase, sources map[string][]core.SemanticNote, lane *indexedLane) {
	t.Helper()
	ctx := context.Background()
	var command *CommandEmbedder
	var err error
	if backend == "python" {
		command, err = EmbeddingCommand(ctx, os.Getenv("CAIRN_EMBEDDING_WORKER"))
	} else {
		config, readErr := ReadRustWorkerConfig(os.Getenv("CAIRN_RUST_EMBED_TEST_CONFIG"))
		if readErr != nil {
			t.Fatal(readErr)
		}
		command, err = RustEmbeddingCommand(ctx, config)
	}
	if err != nil {
		t.Fatal(err)
	}
	defer command.Close()
	model := &indexedObservedModel{CommandEmbedder: command}
	idx, err := OpenIndex(ctx, dsn, model)
	if err != nil {
		t.Fatal(err)
	}
	defer idx.Close()
	for step := 0; ; step++ {
		if step >= 100 {
			t.Fatal("index rebuild did not finish within source bound")
		}
		worked, err := idx.Step(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if !worked {
			break
		}
		lane.RebuildSteps++
	}
	if lane.RebuildSteps != len(sources["workload"])+len(sources["ties"]) {
		t.Fatal("actual rebuild did not process the fixed source inventory")
	}
	reader, err := core.OpenWithSemanticRetriever(ctx, dsn, core.Channel{Principal: "s03-indexed-comparison"}, idx.Search)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	lane.Identity = idx.identity
	rows, err := idx.pool.Query(ctx, `SELECT d.record_id::text,p.ordinal,p.byte_offset,p.byte_length,p.embedding::text FROM cairn.semantic_document d JOIN cairn.semantic_passage p USING(record_id) WHERE d.model_sha256=$1 ORDER BY d.record_id,p.ordinal`, idx.identity.ModelSHA256)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	for rows.Next() {
		var v indexedStoredVector
		var raw string
		if err = rows.Scan(&v.RecordID, &v.Ordinal, &v.Span.Offset, &v.Span.Length, &raw); err != nil {
			t.Fatal(err)
		}
		if err = json.Unmarshal([]byte(raw), &v.Vector); err != nil {
			t.Fatal(err)
		}
		lane.Stored = append(lane.Stored, v)
	}
	if err = rows.Err(); err != nil {
		t.Fatal(err)
	}
	rows.Close()
	for _, c := range cases {
		lane.Cases = append(lane.Cases, indexedObserve(t, idx, model, reader, c, "workload", sources["workload"], len(sources["workload"])))
		t.Logf("S03 %s observed %s", backend, c.ID)
	}
	tie := indexedObserve(t, idx, model, reader, indexedCase{ID: "equal-distance-record-id-ties", Query: "private database directory"}, "ties", sources["ties"], 3)
	ids := []string{sources["ties"][0].RecordID, sources["ties"][1].RecordID}
	positions := map[string]int{}
	for n, hit := range tie.View.Hits {
		positions[hit.RecordID] = n
	}
	if tie.Distances[positions[ids[0]]] != tie.Distances[positions[ids[1]]] || (positions[ids[0]] < positions[ids[1]]) != (ids[0] < ids[1]) {
		t.Fatal("actual equal-distance duplicate text did not retain record-ID tie order")
	}
	lane.Cases = append(lane.Cases, tie)
	if _, err = idx.pool.Exec(ctx, `DELETE FROM cairn.semantic_document WHERE record_id=$1`, sources["workload"][0].RecordID); err != nil {
		t.Fatal(err)
	}
	lane.Cases = append(lane.Cases, indexedObserve(t, idx, model, reader, indexedCase{ID: "partial-coverage", Query: cases[0].Query}, "workload", sources["workload"], len(sources["workload"])-1))
	ids = ids[:0]
	for _, n := range sources["workload"] {
		ids = append(ids, n.RecordID)
	}
	if _, err = idx.pool.Exec(ctx, `DELETE FROM cairn.semantic_document WHERE record_id::text=ANY($1::text[])`, ids); err != nil {
		t.Fatal(err)
	}
	lane.Cases = append(lane.Cases, indexedObserve(t, idx, model, reader, indexedCase{ID: "unavailable-coverage", Query: cases[0].Query}, "workload", sources["workload"], 0))
}

func indexedObserve(t *testing.T, idx *Index, model *indexedObservedModel, reader *core.Store, c indexedCase, corpus string, notes []core.SemanticNote, indexed int) indexedObservation {
	t.Helper()
	ctx := context.Background()
	r, err := idx.Search(ctx, core.SemanticRankRequest{Query: c.Query, Notes: notes})
	if err != nil {
		t.Fatalf("%s Index.Search: %v", c.ID, err)
	}
	if r.Indexed != indexed {
		t.Fatalf("%s coverage=%d want%d", c.ID, r.Indexed, indexed)
	}
	sources := map[string]core.SemanticNote{}
	for _, note := range notes {
		sources[note.RecordID] = note
	}
	for _, hit := range r.Hits {
		note, ok := sources[hit.RecordID]
		span := hit.Span
		if !ok || hit.Version != note.Version || hit.BodySHA256 != note.BodySHA256 || span.Offset < 0 || span.Length <= 0 || span.Offset > len(note.Body) || span.Length > len(note.Body)-span.Offset || !utf8.ValidString(note.Body[span.Offset:span.Offset+span.Length]) {
			t.Fatalf("%s hit does not bind original source/version/hash/span: %+v", c.ID, hit)
		}
	}
	observation := indexedObservation{View: indexedView{ID: c.ID, Indexed: r.Indexed, Eligible: len(notes), Hits: r.Hits, QueryProjection: r.QueryProjection}, QueryVector: append([]float32(nil), model.last...)}
	vector, err := vectorText(model.last)
	if err != nil {
		t.Fatal(err)
	}
	ids := []string{}
	for _, n := range notes {
		ids = append(ids, n.RecordID)
	}
	rows, err := idx.pool.Query(ctx, `SELECT d.record_id::text,d.version,d.body_sha256,p.byte_offset,p.byte_length,(p.embedding <=> $2::public.vector) AS distance FROM cairn.semantic_document d JOIN cairn.semantic_passage p USING(record_id) WHERE d.record_id::text=ANY($1::text[]) AND d.model_sha256=$3 ORDER BY distance,d.record_id,p.ordinal`, ids, vector, idx.identity.ModelSHA256)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	seen := map[string]bool{}
	expected := []core.SemanticPassageHit{}
	for rows.Next() {
		var hit core.SemanticPassageHit
		var distance float64
		if err = rows.Scan(&hit.RecordID, &hit.Version, &hit.BodySHA256, &hit.Span.Offset, &hit.Span.Length, &distance); err != nil {
			t.Fatal(err)
		}
		if seen[hit.RecordID] {
			continue
		}
		seen[hit.RecordID] = true
		hit.Score = int(math.Round(math.Max(-1, math.Min(1, 1-distance)) * 1000000))
		expected = append(expected, hit)
		if n := len(observation.Distances); n > 0 {
			gap := distance - observation.Distances[n-1]
			if gap > 0 && (observation.MinimumPositiveGap == nil || gap < *observation.MinimumPositiveGap) {
				observation.MinimumPositiveGap = &gap
			}
		}
		observation.Distances = append(observation.Distances, distance)
	}
	if err = rows.Err(); err != nil {
		t.Fatal(err)
	}
	rows.Close()
	if !reflect.DeepEqual(expected, r.Hits) {
		t.Fatalf("%s raw SQL order/identity/span/score differs: got%+v want%+v", c.ID, r.Hits, expected)
	}
	budget := 32000
	request := core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: "s03-" + corpus, TaskID: "matrix", RunID: "run"}, Query: c.Query, Purpose: "context", Semantic: true, AvailableTokens: 32000, MemoryBudgetBytes: &budget}
	compiled, err := reader.Index(ctx, request, core.Destination{Name: "hosted"})
	if err != nil {
		t.Fatalf("%s compiler: %v", c.ID, err)
	}
	pkg := compiled.Package
	if pkg.Semantic.Discovery == nil {
		t.Fatalf("%s missing compiler discovery state", c.ID)
	}
	discovery := *pkg.Semantic.Discovery
	if indexed > 0 {
		if discovery.State != "ready" || discovery.Coverage == nil || discovery.Coverage.Indexed != indexed || discovery.Coverage.Eligible != len(notes) || discovery.ModelSHA256 != idx.identity.ModelSHA256 || discovery.Algorithm != idx.identity.Algorithm || !reflect.DeepEqual(discovery.QueryProjection, r.QueryProjection) {
			t.Fatalf("%s compiler discovery/projection/coverage invalid: %+v", c.ID, discovery)
		}
		canonical := append([]core.SemanticPassageHit{}, r.Hits...)
		// Canonical digest order is separate from both observed result orders.
		slices.SortFunc(canonical, func(a, b core.SemanticPassageHit) int { return strings.Compare(a.RecordID, b.RecordID) })
		encoded, err := json.Marshal(canonical)
		if err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(encoded)
		if discovery.ScoresSHA256 != hex.EncodeToString(sum[:]) || len(pkg.Semantic.Index) == 0 {
			t.Fatalf("%s compiler lost the actual passage scores or returned no entries", c.ID)
		}
	} else if discovery.State != "unavailable" {
		t.Fatalf("%s zero coverage did not retain unavailable discovery: %+v", c.ID, discovery)
	}
	// Producer identities are retained in each lane, not compared across producers.
	discovery.ModelSHA256 = ""
	observation.View.CompilerEntries = pkg.Semantic.Index
	observation.View.CompilerDiscovery = &discovery
	observation.View.Ranking, observation.View.Schema = pkg.Semantic.Ranking, pkg.Semantic.Schema
	replayed, err := reader.Recompile(ctx, core.RecompileRequest{ReceiptID: pkg.ReceiptID, Query: c.Query})
	if err != nil || replayed.Seal != pkg.Seal {
		t.Fatalf("%s retained compiler replay: %v", c.ID, err)
	}
	observation.View.ReplayEqual = true
	return observation
}

func indexedVectorGap(t *testing.T, a, b []float32) float64 {
	t.Helper()
	if len(a) != 384 || len(b) != 384 {
		t.Fatal("missing actual 384-component vector pair")
	}
	var dot, aa, bb float64
	for i := range a {
		dot += float64(a[i]) * float64(b[i])
		aa += float64(a[i]) * float64(a[i])
		bb += float64(b[i]) * float64(b[i])
	}
	gap := math.Abs(1 - dot/math.Sqrt(aa*bb))
	if math.IsNaN(gap) || gap > 1e-6 {
		t.Fatalf("indexed vector cosine gap %g exceeds1e-6", gap)
	}
	return gap
}
