package core

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestDistinctQueryFallbackKeepsSealedOptions(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	d.Body = "storage guidance in the private directory"
	d.Entities = []EntityRef{{Kind: "file", Name: "core/compiler.go"}}
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	s.semanticRetriever = func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
		return SemanticRetrievalResult{}, errors.New("offline")
	}
	for _, tc := range []struct {
		name   string
		option func(*CompileRequest)
	}{
		{"explicit memory", func(r *CompileRequest) { room := 10000; r.MemoryBudgetBytes = &room }},
		{"pull reserve", func(r *CompileRequest) {
			room, reserve := 10000, 1000
			r.MemoryBudgetBytes = &room
			r.MinPullBytes = &reserve
		}},
		{"whole inspection", func(r *CompileRequest) {
			room := 10000
			r.MemoryBudgetBytes = &room
			r.InspectionPolicy = firstFittingWhole
		}},
		{"compact preview", func(r *CompileRequest) { r.CompactPreviewEntities = true }},
		{"advisory", func(r *CompileRequest) { r.AdvisoryConflicts = true }},
		{"entities", func(r *CompileRequest) { r.Entities = d.Entities }},
		{"page", func(r *CompileRequest) { zero := 0; r.PageOffset = &zero }},
		{"signature", func(r *CompileRequest) { r.ErrorSignature = strings.Repeat("a", 64) }},
		{"kind", func(r *CompileRequest) { r.Kinds = []string{d.Kind} }},
		{"phase", func(r *CompileRequest) { r.Context = &ContextPins{TaskPhase: "repair"} }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "storage", Semantic: true, Purpose: "context", AvailableTokens: 32000}
			tc.option(&req)
			old, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			req.RequestID = uuid.NewString()
			req.SemanticQuery = "Store it in the private directory."
			current, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			view := current.Package.Semantic
			view.Schema = old.Package.Semantic.Schema
			view.QueryIntent = nil
			a, _, err := sealPackage(old.Package.Semantic)
			if err != nil {
				t.Fatal(err)
			}
			b, _, err := sealPackage(view)
			if err != nil || !bytes.Equal(a, b) {
				t.Fatalf("fallback option changed: %v", err)
			}
			p, err := s.Recompile(ctx, RecompileRequest{ReceiptID: current.Package.ReceiptID, Query: req.Query, SemanticQuery: req.SemanticQuery, Entities: req.Entities})
			if err != nil || p.Seal != current.Package.Seal {
				t.Fatalf("replay option: %v", err)
			}
			p, err = s.Replay(ctx, current.Package.ReceiptID)
			if err != nil || p.Seal != current.Package.Seal {
				t.Fatalf("inspection option: %v", err)
			}
			if len(current.Handles) > 0 {
				_, err = s.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: current.Package.ReceiptID, Handle: current.Handles[0].Handle}, Destination{"hosted", false})
				if err != nil {
					t.Fatalf("expansion option: %v", err)
				}
			}
		})
	}
}

func TestDistinctQueryRefusalRetainsDigestWithoutRawTaskText(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Kind = "instruction"
	d.Sensitivity = "shareable"
	_, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, RequiresRuntime: true, PolicyKey: "distinct-query-runtime", Reason: "Test private refusal data without unsupported mediation"})
	if err != nil {
		t.Fatal(err)
	}
	s.semanticRetriever = func(context.Context, SemanticRankRequest) (SemanticRetrievalResult, error) {
		t.Fatal("policy refusal invoked provider")
		return SemanticRetrievalResult{}, nil
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "private-lexical-refusal-canary", SemanticQuery: "private-semantic-task-canary Do not reveal this task text.", Semantic: true, Purpose: "context", AvailableTokens: 32000, Mode: "index"}
	_, err = s.Compile(ctx, req, Destination{"hosted", false})
	requireCode(t, err, "POLICY_UNENFORCEABLE")
	var failure *Error
	if !errors.As(err, &failure) || failure.RefusalID == "" {
		t.Fatal("no durable refusal")
	}
	refusal, err := s.Refusal(ctx, failure.RefusalID)
	if err != nil {
		t.Fatal(err)
	}
	public, err := json.Marshal(refusal)
	if err != nil {
		t.Fatal(err)
	}
	var durable string
	if err = s.pool.QueryRow(ctx, `SELECT detail::text FROM cairn.refusal WHERE refusal_id=$1`, failure.RefusalID).Scan(&durable); err != nil {
		t.Fatal(err)
	}
	for _, canary := range []string{req.Query, req.SemanticQuery} {
		if bytes.Contains(public, []byte(canary)) || strings.Contains(durable, canary) {
			t.Fatal("raw input persisted in refusal")
		}
	}
	var stored []byte
	if err = s.pool.QueryRow(ctx, `SELECT request_digest FROM cairn.refusal WHERE refusal_id=$1`, failure.RefusalID).Scan(&stored); err != nil {
		t.Fatal(err)
	}
	digest, err := compileIntentDigest(req, Destination{"hosted", false})
	if err != nil || !bytes.Equal(stored, digest[:]) {
		t.Fatal("refusal intent digest differs from compile contract")
	}
	// Public projection hashes can expose low-entropy input to guessing. This
	// test verifies absence of raw text, not secrecy of the input digest.
}
