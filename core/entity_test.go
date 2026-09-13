package core

import (
	"context"
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestEntityAssociationsSurviveOrdinaryRevision(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	d := projectNote(uuid.NewString())
	const associations = `[{"kind":"file","name":"core/currentness.go"},{"kind":"symbol","name":"core.applicabilityReason"}]`
	if err := json.Unmarshal([]byte(`{"entities":`+associations+`}`), &d); err != nil {
		t.Fatal(err)
	}
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	assertAssociations := func(r Record) {
		t.Helper()
		b, err := json.Marshal(r)
		if err != nil {
			t.Fatal(err)
		}
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(b, &fields); err != nil {
			t.Fatal(err)
		}
		if string(fields["entities"]) != associations {
			t.Fatalf("lost explicit associations: %s", b)
		}
	}
	assertAssociations(r)
	_, err = s.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: 1, Repo: d.Scope.Repo, Body: "Corrected guidance"})
	if err != nil {
		t.Fatal(err)
	}
	r, err = s.Get(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	assertAssociations(r)
	history, err := s.History(ctx, RecordHistoryRequest{RecordID: r.RecordID, Version: 1}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(history.Versions[0])
	if !strings.Contains(string(b), `"entities":`+associations) {
		t.Fatalf("history lost version associations: %s", b)
	}
}

func TestEntityValidationAndCanonicalRetries(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	d := projectNote(uuid.NewString())
	req := CreateRequest{uuid.NewString(), d}
	for _, ref := range []EntityRef{{"other", "x"}, {"file", "../x"}, {"file", "/x"}, {"file", "a/../x"}, {"file", "./x"}, {"file", "x/"}, {"file", "a\\b"}, {"file", "C:/x"}, {"symbol", ""}, {"symbol", " x"}, {"symbol", "x\n"}, {"symbol", strings.Repeat("x", 513)}} {
		req.Draft.Entities = []EntityRef{ref}
		_, err := s.Create(ctx, req)
		requireCode(t, err, "INVALID_REQUEST")
	}
	req.Draft.Entities = []EntityRef{{"symbol", "core.Check"}, {"file", "docs/日本語 guide.md"}, {"symbol", "core.Check"}}
	first, err := s.Create(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	req.Draft.Entities = []EntityRef{{"file", "docs/日本語 guide.md"}, {"symbol", "core.Check"}}
	again, err := s.Create(ctx, req)
	if err != nil || again.RecordID != first.RecordID {
		t.Fatalf("equivalent retry changed capture: %+v %v", again, err)
	}
	req.Draft.Entities[1].Name = "core.check"
	_, err = s.Create(ctx, req)
	if err == nil {
		t.Fatal("changed case reused old capture")
	}
}

func TestEntityRetrievalPreservesGatesAcrossModes(t *testing.T) {
	for _, mode := range []string{"body", "index", "semantic", "fallback", "browse"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			s, grant := testOperator(t)
			repo := uuid.NewString()
			refs := []EntityRef{{Kind: "file", Name: "core/currentness.go"}}
			d := projectNote(repo)
			d.Body, d.Entities, d.Sensitivity = "Inspect every known mismatch first.", refs, "shareable"
			wanted, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
			if err != nil {
				t.Fatal(err)
			}
			excluded := []string{}
			for _, gate := range []string{"private", "revision", "task", "repo"} {
				x := d
				x.Body = "repair fixture excluded " + gate
				switch gate {
				case "private":
					x.Sensitivity = "local"
				case "revision":
					x.Pins = &Applicability{Revision: strings.Repeat("a", 40)}
				case "task":
					x.Scope.TaskID = "different"
				case "repo":
					x.Scope.Repo = uuid.NewString()
				}
				r, err := s.Create(ctx, CreateRequest{uuid.NewString(), x})
				if err != nil {
					t.Fatal(err)
				}
				excluded = append(excluded, r.RecordID)
			}
			d.Entities, d.Body = nil, "repair fixture: general guidance"
			general, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
			if err != nil {
				t.Fatal(err)
			}
			d.Kind, d.Body = "instruction", "Keep source references."
			required, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: grant.ID, Mandatory: true, PolicyKey: "sources", Reason: "Required fixture"})
			if err != nil {
				t.Fatal(err)
			}
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "repair fixture", Entities: refs, Context: &ContextPins{Revision: strings.Repeat("b", 40)}, Purpose: "context", AvailableTokens: 64000}
			if mode == "semantic" || mode == "fallback" {
				req.Semantic = true
			}
			s.semanticRanker = func(_ context.Context, r SemanticRankRequest) (SemanticRankResult, error) {
				result := SemanticRankResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1"}
				for _, n := range r.Notes {
					if slices.Contains(excluded, n.RecordID) {
						t.Fatal("ineligible association reached semantic worker")
					}
					score := 900000
					if n.RecordID == wanted.RecordID {
						score = -100000
					}
					result.Scores = append(result.Scores, SemanticScore{n.RecordID, n.Version, n.BodySHA256, score})
				}
				return result, nil
			}
			if mode == "fallback" {
				s.semanticRanker = nil
			}
			if mode == "browse" {
				req.Query, req.Entities, req.BrowseOffset = "", nil, new(int)
			}
			var pkg Package
			if mode == "body" {
				pkg, err = s.Compile(ctx, req, Destination{"hosted", false})
			} else {
				index, e := s.Index(ctx, req, Destination{"hosted", false})
				pkg, err = index.Package, e
			}
			if err != nil {
				t.Fatal(err)
			}
			if len(pkg.Semantic.Selected) == 0 || pkg.Semantic.Selected[0].Record.RecordID != required.RecordID {
				t.Fatal("required context lost")
			}
			ids := []string{}
			for _, e := range pkg.Semantic.Selected {
				if !e.Mandatory {
					ids = append(ids, e.Record.RecordID)
				}
			}
			for _, e := range pkg.Semantic.Index {
				ids = append(ids, e.RecordID)
			}
			if len(ids) != 2 || !slices.Contains(ids, general.RecordID) || !slices.Contains(ids, wanted.RecordID) || (mode != "browse" && ids[0] != wanted.RecordID) {
				t.Fatalf("eligible association ordering in %s: %v", mode, ids)
			}
			b, _ := json.Marshal(pkg)
			for _, id := range excluded {
				if strings.Contains(string(b), id) {
					t.Fatal("excluded identity leaked")
				}
			}
			replayed, err := s.Recompile(ctx, RecompileRequest{ReceiptID: pkg.ReceiptID, Query: req.Query, Entities: req.Entities})
			if err != nil || replayed.Seal != pkg.Seal {
				t.Fatalf("%s replay: %v", mode, err)
			}
		})
	}
}

func TestEntitySearchPrefersAssociationOverBodyMention(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	d := projectNote(uuid.NewString())
	d.Sensitivity = "shareable"
	d.Entities = []EntityRef{{Kind: "file", Name: "core/currentness.go"}}
	d.Body = "Check all known applicability mismatches before missing fields."
	wanted, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Entities = nil
	d.Body = "Incidental reference to core/currentness.go; unrelated maintenance."
	incidental, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{d.Scope.Repo, "task", "run"}, Query: `"core/currentness.go"`, Purpose: "context", AvailableTokens: 32000}
	if err = json.Unmarshal([]byte(`{"entities":[{"kind":"file","name":"core/currentness.go"}]}`), &req); err != nil {
		t.Fatal(err)
	}
	index, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	entries := index.Package.Semantic.Index
	if len(entries) != 2 || entries[0].RecordID != wanted.RecordID || entries[1].RecordID != incidental.RecordID {
		t.Fatalf("explicit association must precede incidental body text: %+v", entries)
	}
	b, _ := json.Marshal(entries[0])
	if !strings.Contains(string(b), `"entities":[{"kind":"file","name":"core/currentness.go"}]`) {
		t.Fatalf("index lost selected association: %s", b)
	}
	planning := req
	planning.RequestID, planning.Query, planning.Purpose = uuid.NewString(), "unrelated words", "planning"
	blocked, err := s.Compile(ctx, planning, Destination{"hosted", false})
	if err != nil || len(blocked.Semantic.Selected) != 0 {
		t.Fatalf("entity hints granted consequential use: %v", err)
	}
	explanation, err := s.Explain(ctx, blocked.ReceiptID)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range explanation.Candidates {
		if e.RecordID == wanted.RecordID && !e.EscalationBlocked {
			t.Fatal("unqualified entity demand was lost")
		}
	}
	// Changing the association changes future retrieval, not the old receipt.
	d = wanted.Draft
	d.Entities = []EntityRef{{Kind: "symbol", Name: "core/currentness.go"}}
	if _, err = s.Edit(ctx, EditRequest{uuid.NewString(), wanted.RecordID, wanted.Version, d}); err != nil {
		t.Fatal(err)
	}
	var replayRequest RecompileRequest
	if err = json.Unmarshal([]byte(`{"receipt_id":"`+index.Package.ReceiptID+`","query":"\"core/currentness.go\"","entities":[{"kind":"file","name":"core/currentness.go"}]}`), &replayRequest); err != nil {
		t.Fatal(err)
	}
	replay, err := s.Recompile(ctx, replayRequest)
	if err != nil || replay.Seal != index.Package.Seal {
		t.Fatalf("exact earlier association did not recompile: %v", err)
	}
	req.RequestID, req.Query = uuid.NewString(), ""
	after, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil || len(after.Package.Semantic.Index) != 0 {
		t.Fatalf("file query matched a symbol or incidental mention: %+v %v", after.Package.Semantic.Index, err)
	}
	replayRequest.Entities = nil
	_, err = s.Recompile(ctx, replayRequest)
	requireCode(t, err, "INVALID_REQUEST")
	replayRequest.Entities = req.Entities
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.record_entities SET entities='[{"kind":"file","name":"changed.go"}]' WHERE record_id=$1 AND version=1`, wanted.RecordID); err != nil {
		t.Fatal(err)
	}
	_, err = s.Recompile(ctx, replayRequest)
	requireCode(t, err, "INTEGRITY_FAILURE")
}

func TestEntityAndFailureMatchesShareFirstRankingTier(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "operator:entity-signature", Operator: true, Instrumented: true})
	d := projectNote(uuid.NewString())
	d.Sensitivity, d.Body = "shareable", "Initialize the isolated database."
	p, failureNote := signatureLesson(t, s, d)
	_, err := s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: p.Version, Disposition: "converted", ResultRecord: failureNote.RecordID, ResultVersion: 1, SignatureShareable: true, Reason: "Share fixture association"})
	if err != nil {
		t.Fatal(err)
	}
	d.Entities = []EntityRef{{Kind: "file", Name: "core/currentness.go"}}
	d.Body = "repair guidance"
	entityNote, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{d.Scope.Repo, "task", "run"}, Query: "repair guidance", Entities: d.Entities, ErrorSignature: strings.Repeat("b", 64), Purpose: "context", AvailableTokens: 64000}
	index, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	if len(index.Package.Semantic.Index) != 2 || index.Package.Semantic.Index[0].RecordID != entityNote.RecordID {
		t.Fatalf("lexical match should decide between exact entity and failure hints: %+v", index.Package.Semantic.Index)
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: index.Package.ReceiptID, Query: req.Query, Entities: req.Entities})
	if err != nil || replay.Seal != index.Package.Seal {
		t.Fatalf("combined intent replay: %v", err)
	}
}
