package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"github.com/fxamacker/cbor/v2"
	"reflect"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func projectionFixture(query string, prefix int, original, embedded int) *SemanticQueryProjection {
	sum := sha256.Sum256([]byte(query[:prefix]))
	return &SemanticQueryProjection{Method: "original-prefix/1", Truncated: prefix < len(query), OriginalTokens: original, EmbeddedTokens: embedded, PrefixBytes: prefix, PrefixSHA256: hex.EncodeToString(sum[:])}
}

func TestQueryProjectionBindsOriginalUnicodePrefixAndRequiresCompleteDeclaration(t *testing.T) {
	query := "é漢🙂 storage suffix"
	p := projectionFixture(query, len("é漢🙂"), 600, 512)
	if err := ValidateSemanticQueryProjection(p, query); err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(*SemanticQueryProjection){
		func(p *SemanticQueryProjection) { p.PrefixBytes-- },
		func(p *SemanticQueryProjection) { p.PrefixSHA256 = strings.Repeat("a", 64) },
		func(p *SemanticQueryProjection) { p.Method = "unknown/1" },
		func(p *SemanticQueryProjection) { p.Truncated = false },
		func(p *SemanticQueryProjection) { p.EmbeddedTokens = 513 },
		func(p *SemanticQueryProjection) { p.OriginalTokens = 512 },
	} {
		copy := *p
		change(&copy)
		if ValidateSemanticQueryProjection(&copy, query) == nil {
			t.Fatal("invalid projection accepted")
		}
	}
	raw, _ := json.Marshal(p)
	var fields map[string]any
	_ = json.Unmarshal(raw, &fields)
	for key := range fields {
		var incomplete map[string]any
		_ = json.Unmarshal(raw, &incomplete)
		delete(incomplete, key)
		data, _ := json.Marshal(incomplete)
		var got SemanticQueryProjection
		if json.Unmarshal(data, &got) == nil {
			t.Fatalf("missing %s accepted", key)
		}
	}
	for _, key := range []string{"truncated", "original_tokens"} {
		var invalid map[string]any
		_ = json.Unmarshal(raw, &invalid)
		invalid[key] = nil
		data, _ := json.Marshal(invalid)
		var got SemanticQueryProjection
		if json.Unmarshal(data, &got) == nil {
			t.Fatalf("null %s accepted", key)
		}
	}
	old := DiscoveryRanking{State: "unavailable"}
	before, _ := json.Marshal(old)
	if string(before) != `{"state":"unavailable"}` {
		t.Fatalf("legacy JSON changed: %s", before)
	}
}

func TestIndexedQueryProjectionKeepsIntentAndReplays(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "Keep the original source reference."
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	query := "é漢🙂 " + strings.Repeat("task constraints ", 40) + " suffix exact"
	projection := projectionFixture(query, len("é漢🙂 "), 600, 20)
	s.semanticRetriever = func(_ context.Context, r SemanticRankRequest) (SemanticRetrievalResult, error) {
		if r.Query != query {
			t.Fatal("lexical intent replaced")
		}
		n := r.Notes[0]
		return SemanticRetrievalResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1", Indexed: 1, QueryProjection: projection, Hits: []SemanticPassageHit{{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, 800000}, Span: ByteSpanRequest{Length: len(n.Body)}}}}, nil
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: query, Purpose: "context", AvailableTokens: 32000, Semantic: true}
	result, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	p := result.Package
	sum := sha256.Sum256([]byte(query))
	if p.Semantic.Schema != "cairn.semantic/19" || p.Semantic.Query != "sha256:"+hex.EncodeToString(sum[:]) || p.Semantic.Discovery.QueryProjection == nil || *p.Semantic.Discovery.QueryProjection != *projection {
		t.Fatalf("intent or projection lost: %+v", p.Semantic)
	}
	wire, err := json.Marshal(result)
	if err != nil || !strings.Contains(string(wire), `"query_projection":{"method":"original-prefix/1","truncated":true`) {
		t.Fatalf("public envelope lost projection: %v", err)
	}
	s.semanticRetriever = func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
		t.Fatal("replay called model")
		return SemanticRetrievalResult{}, nil
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: query})
	if err != nil || replay.Seal != p.Seal {
		t.Fatalf("replay: %v", err)
	}
	for _, schema := range []string{"cairn.semantic/15", "cairn.semantic/16", "cairn.semantic/17"} {
		bad := p.Semantic
		bad.Schema = schema
		requireCode(t, validateFrozenDiscovery(bad, nil, query), "INTEGRITY_FAILURE")
	}
	bad := p.Semantic
	copy := *projection
	copy.PrefixSHA256 = strings.Repeat("b", 64)
	discovery := *bad.Discovery
	discovery.QueryProjection = &copy
	bad.Discovery = &discovery
	requireCode(t, validateFrozenDiscovery(bad, nil, query), "INTEGRITY_FAILURE")
	bad = p.Semantic
	discovery = *bad.Discovery
	discovery.QueryProjection = nil
	bad.Discovery = &discovery
	requireCode(t, validateFrozenDiscovery(bad, nil, query), "INTEGRITY_FAILURE")
}

func TestQueryProjectionLegacyCanonicalBytesAndReaderRefusal(t *testing.T) {
	discoveryType := reflect.TypeOf(DiscoveryRanking{})
	fields := []reflect.StructField{}
	for n := 0; n < discoveryType.NumField(); n++ {
		f := discoveryType.Field(n)
		if f.Name != "QueryProjection" {
			fields = append(fields, f)
		}
	}
	legacyDiscovery := reflect.StructOf(fields)
	fields = nil
	packageType := reflect.TypeOf(SemanticPackage{})
	for n := 0; n < packageType.NumField(); n++ {
		f := packageType.Field(n)
		if f.Name == "Discovery" {
			f.Type = reflect.PointerTo(legacyDiscovery)
		}
		fields = append(fields, f)
	}
	legacyPackage := reflect.StructOf(fields)
	options := cbor.CanonicalEncOptions()
	options.Time = cbor.TimeRFC3339Nano
	encoder, err := options.EncMode()
	if err != nil {
		t.Fatal(err)
	}
	for _, projected := range []bool{false, true} {
		p := SemanticPackage{Schema: "cairn.semantic/16", Discovery: &DiscoveryRanking{State: "ready"}}
		if projected {
			p.Schema = "cairn.semantic/18"
			p.Discovery.QueryProjection = projectionFixture("original", len("original"), 15, 15)
		}
		encoded, _, err := sealPackage(p)
		if err != nil {
			t.Fatal(err)
		}
		legacy := reflect.New(legacyPackage)
		if err = cbor.Unmarshal(encoded, legacy.Interface()); err != nil {
			t.Fatal(err)
		}
		roundtrip, err := encoder.Marshal(legacy.Elem().Interface())
		if err != nil {
			t.Fatal(err)
		}
		if bytes.Equal(encoded, roundtrip) == projected {
			t.Fatalf("legacy canonical integrity, projected=%v", projected)
		}
	}
}
