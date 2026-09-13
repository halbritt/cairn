package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestKindFilterFindsDirectionAndPreservesRequiredContext(t *testing.T) {
	for _, mode := range []string{"body", "index", "browse", "semantic", "fallback", "not-needed"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			s, grant := testOperator(t)
			repo := uuid.NewString()
			d := projectNote(repo)
			d.Sensitivity = "shareable"
			wanted := map[string]bool{}
			for _, kind := range []string{"decision", "preference", "procedure"} {
				d.Kind, d.Body = kind, "Project guidance: "+kind
				r, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
				if err != nil {
					t.Fatal(err)
				}
				if kind != "procedure" {
					wanted[r.RecordID] = true
				}
			}
			d.Kind, d.Body, d.Sensitivity = "decision", "Project private content", "local"
			private, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
			if err != nil {
				t.Fatal(err)
			}
			d.Kind, d.Body, d.Sensitivity = "instruction", "Keep source references.", "shareable"
			required, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: grant.ID, Mandatory: true, PolicyKey: "sources", Reason: "Required context"})
			if err != nil {
				t.Fatal(err)
			}
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "project", Purpose: "context", AvailableTokens: 32000}
			req.Kinds = []string{"preference", "decision", "preference"}
			if mode == "browse" {
				offset := 0
				req.Query = ""
				req.BrowseOffset = &offset
			}
			if mode == "semantic" || mode == "fallback" || mode == "not-needed" {
				req.Semantic = true
			}
			if mode == "not-needed" {
				if err := json.Unmarshal([]byte(`{"kinds":["observation"]}`), &req); err != nil {
					t.Fatal(err)
				}
			}
			s.semanticRanker = func(_ context.Context, r SemanticRankRequest) (SemanticRankResult, error) {
				result := SemanticRankResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1"}
				for _, n := range r.Notes {
					if !wanted[n.RecordID] {
						t.Fatalf("filtered or private note reached semantic worker: %s", n.RecordID)
					}
					result.Scores = append(result.Scores, SemanticScore{n.RecordID, n.Version, n.BodySHA256, 500000})
				}
				return result, nil
			}
			if mode == "fallback" {
				s.semanticRanker = nil
			}
			dest := Destination{"hosted", false}
			compile := func(r CompileRequest) (Package, error) {
				if mode == "body" {
					return s.Compile(ctx, r, dest)
				}
				index, err := s.Index(ctx, r, dest)
				return index.Package, err
			}
			pkg, err := compile(req)
			if err != nil {
				t.Fatal(err)
			}
			got := map[string]bool{}
			requiredSeen := false
			for _, e := range pkg.Semantic.Selected {
				if e.Mandatory {
					requiredSeen = e.Record.RecordID == required.RecordID
				} else {
					got[e.Record.RecordID] = true
				}
			}
			for _, e := range pkg.Semantic.Index {
				got[e.RecordID] = true
				if e.SummarySpan == nil {
					t.Fatal("filtered preview lost source location")
				}
			}
			expected := wanted
			if mode == "not-needed" {
				expected = map[string]bool{}
			}
			if !requiredSeen || len(got) != len(expected) {
				t.Fatalf("direction retrieval: got=%v want=%v required=%v", got, expected, requiredSeen)
			}
			for id := range got {
				if !expected[id] {
					t.Fatalf("unexpected optional record: %s", id)
				}
			}
			encoded, _ := json.Marshal(pkg)
			if strings.Contains(string(encoded), private.RecordID) || strings.Contains(string(encoded), "Project private content") {
				t.Fatal("private source leaked")
			}
			wantOmitted := 1
			if mode == "not-needed" {
				wantOmitted = 3
			}
			if pkg.Semantic.Omitted["KIND_FILTERED"] != wantOmitted {
				t.Fatalf("filtered census counts private source or misses public source: %+v", pkg.Semantic.Omitted)
			}
			if mode == "semantic" && pkg.Semantic.Discovery.State != "ready" {
				t.Fatal(pkg.Semantic.Discovery)
			}
			if mode == "fallback" && pkg.Semantic.Discovery.State != "unavailable" {
				t.Fatal(pkg.Semantic.Discovery)
			}
			if mode == "not-needed" && pkg.Semantic.Discovery.State != "not_needed" {
				t.Fatal(pkg.Semantic.Discovery)
			}
			retry := req
			if mode != "not-needed" {
				_ = json.Unmarshal([]byte(`{"kinds":["decision","preference"]}`), &retry)
			}
			repeated, err := compile(retry)
			if err != nil || repeated.ReceiptID != pkg.ReceiptID || repeated.Seal != pkg.Seal {
				t.Fatalf("equivalent filter retry: %v", err)
			}
			_ = json.Unmarshal([]byte(`{"kinds":["lesson"]}`), &retry)
			_, err = compile(retry)
			requireCode(t, err, "IDEMPOTENCY_CONFLICT")
			if mode == "index" {
				for id := range wanted {
					d.Kind, d.Body = "procedure", "Changed kind after retrieval"
					_, err = s.Edit(ctx, EditRequest{uuid.NewString(), id, 1, d})
					if err != nil {
						t.Fatal(err)
					}
					break
				}
			}
			historical, err := s.Recompile(ctx, RecompileRequest{ReceiptID: pkg.ReceiptID, Query: req.Query})
			if err != nil || historical.Seal != pkg.Seal {
				t.Fatalf("historical filtered selection: %v", err)
			}
		})
	}
}

func TestKindFilterCannotHidePrivateRequiredInstruction(t *testing.T) {
	ctx := context.Background()
	s, g := testOperator(t)
	d := projectNote(uuid.NewString())
	d.Kind = "instruction"
	d.Body = "Private required instruction"
	d.Sensitivity = "local"
	_, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: g.ID, Mandatory: true, PolicyKey: "private", Reason: "Required"})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{d.Scope.Repo, "task", "run"}, Purpose: "context", AvailableTokens: 32000}
	_ = json.Unmarshal([]byte(`{"kinds":["preference"]}`), &req)
	_, err = s.Index(ctx, req, Destination{"hosted", false})
	requireCode(t, err, "POLICY_UNENFORCEABLE")
}

func TestKindFilterRejectsInvalidLabelsBeforeStoreAccess(t *testing.T) {
	for _, kinds := range [][]string{{""}, {"Decision"}, {"unknown"}, {"note", "note", "note", "note", "note", "note", "note", "note", "note"}} {
		_, err := (*Store)(nil).Compile(context.Background(), CompileRequest{Kinds: kinds}, Destination{})
		requireCode(t, err, "INVALID_REQUEST")
	}
}
