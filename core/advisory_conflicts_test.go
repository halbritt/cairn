package core

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestAdvisoryConflictDeliveryRequiresExplicitIntentAndWholePositions(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	first := projectNote(repo)
	first.Sensitivity = "shareable"
	first.Body = "connection_strategy: reuse the connection between calls. " + strings.Repeat("First position rationale. ", 10)
	a, err := s.Create(ctx, CreateRequest{uuid.NewString(), first})
	if err != nil {
		t.Fatal(err)
	}
	second := first
	second.Body = "Create a fresh connection each time to avoid retaining stale state. " + strings.Repeat("Second position rationale. ", 10)
	b, err := s.Create(ctx, CreateRequest{uuid.NewString(), second})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{a.RecordID, b.RecordID}, "PRIVATE-OPENING-REASON"})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "connection_strategy", Purpose: "context", AvailableTokens: 64000}
	for _, mode := range []string{"", "index"} {
		req.Mode = mode
		req.RequestID = uuid.NewString()
		req.AdvisoryConflicts = false
		ordinary, err := s.Compile(ctx, req, Destination{"hosted", false})
		if err != nil || len(ordinary.Semantic.Selected) != 0 || len(ordinary.Semantic.Index) != 0 {
			t.Fatalf("default dispute omission changed: %+v, %v", ordinary, err)
		}
		req.RequestID = uuid.NewString()
		req.AdvisoryConflicts = true
		advisory, err := s.Compile(ctx, req, Destination{"hosted", false})
		if err != nil {
			t.Fatal(err)
		}
		replayed, err := s.Recompile(ctx, RecompileRequest{ReceiptID: advisory.ReceiptID, Query: req.Query})
		if err != nil || replayed.Seal != advisory.Seal {
			t.Fatalf("historical competing positions did not reproduce: %v", err)
		}
		var positions []Selection
		if mode == "index" {
			if len(advisory.Semantic.Selected) != 0 || len(advisory.Semantic.Index) != 2 {
				t.Fatalf("index must allocate both previews: %+v", advisory.Semantic)
			}
			for _, entry := range advisory.Semantic.Index {
				if len(entry.Conflicts) != 1 || len(entry.Conflicts[0].Members) != 2 || entry.SummarySpan != nil {
					t.Fatalf("unqualified preview or partial pull offered: %+v", entry)
				}
			}
			req.RequestID = uuid.NewString()
			index, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			for _, handle := range index.Handles {
				pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: handle.Handle}
				expanded, err := s.Expand(ctx, pull, Destination{"hosted", false})
				if err != nil {
					t.Fatal(err)
				}
				positions = append([]Selection{expanded.Selection}, expanded.Competing...)
				if expanded.Selection.Record.RecordID != handle.RecordID || len(positions) != 2 {
					t.Fatalf("pull lost requested or competing position: %+v", expanded)
				}
				retry, err := s.Expand(ctx, pull, Destination{"hosted", false})
				firstWire, firstErr := json.Marshal(expanded)
				retryWire, retryErr := json.Marshal(retry)
				if err != nil || firstErr != nil || retryErr != nil || !bytes.Equal(firstWire, retryWire) {
					t.Fatalf("group retry changed result: %+v %v", retry, err)
				}
				pull.RequestID = uuid.NewString()
				pull.Span = &ByteSpanRequest{Offset: 0, Length: 10}
				_, err = s.Expand(ctx, pull, Destination{"hosted", false})
				requireCode(t, err, "INVALID_REQUEST")
			}
			report, err := s.UseReport(ctx, UseReportRequest{Repo: repo, Limit: 100})
			if err != nil {
				t.Fatal(err)
			}
			expandedIDs := map[string]bool{}
			for _, row := range report.Rows {
				if row.ReceiptID == index.Package.ReceiptID && row.Usage == "expanded" {
					expandedIDs[row.RecordID] = true
				}
			}
			if len(expandedIDs) != 2 {
				t.Fatalf("companion usage missing: %+v", report)
			}
		} else {
			positions = advisory.Semantic.Selected
			if len(positions) != 2 || len(advisory.Semantic.Index) != 0 {
				t.Fatalf("body retrieval must deliver both complete positions together: %+v", advisory.Semantic)
			}
		}
		bodies := map[string]string{}
		for _, position := range positions {
			if len(position.Conflicts) != 1 || len(position.Conflicts[0].Members) != 2 {
				t.Fatalf("unqualified position: %+v", position)
			}
			bodies[position.Record.RecordID] = position.Record.Body
		}
		if bodies[a.RecordID] != first.Body || bodies[b.RecordID] != second.Body {
			t.Fatalf("position changed: %+v", bodies)
		}
		rendered, err := json.Marshal(struct {
			Package   Package
			Positions []Selection
		}{advisory, positions})
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(rendered), "PRIVATE-OPENING-REASON") {
			t.Fatal("private conflict context disclosed")
		}
	}
}

func TestAdvisoryConflictCounterpartGates(t *testing.T) {
	for _, gate := range []string{"private", "scope", "pins", "edited", "overlap-private"} {
		t.Run(gate, func(t *testing.T) {
			ctx := context.Background()
			s, _ := testOperator(t)
			repo := uuid.NewString()
			d := projectNote(repo)
			d.Sensitivity = "shareable"
			a, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
			if err != nil {
				t.Fatal(err)
			}
			d.Body = "COUNTERPART-PRIVATE-TEXT"
			switch gate {
			case "private":
				d.Sensitivity = "local"
			case "scope":
				d.Scope.TaskID = "other-task"
			case "pins":
				d.Pins = &Applicability{Revision: strings.Repeat("a", 40)}
			}
			b, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
			if err != nil {
				t.Fatal(err)
			}
			group, err := s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{a.RecordID, b.RecordID}, "PRIVATE-REASON"})
			if err != nil {
				t.Fatal(err)
			}
			if gate == "edited" {
				d.Body = "An edited position is not the originally disputed version"
				if _, err = s.Edit(ctx, EditRequest{uuid.NewString(), b.RecordID, b.Version, d}); err != nil {
					t.Fatal(err)
				}
			}
			if gate == "overlap-private" {
				d.Sensitivity = "local"
				c, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
				if err != nil {
					t.Fatal(err)
				}
				if _, err = s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{b.RecordID, c.RecordID}, "PRIVATE-REASON"}); err != nil {
					t.Fatal(err)
				}
			}
			for _, mode := range []string{"", "index"} {
				req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000, Mode: mode, AdvisoryConflicts: true}
				pkg, err := s.Compile(ctx, req, Destination{"hosted", false})
				if err != nil {
					t.Fatal(err)
				}
				if len(pkg.Semantic.Selected)+len(pkg.Semantic.Index) != 0 {
					t.Fatalf("incomplete group delivered: %+v", pkg)
				}
				wire, err := pkg.Render()
				if err != nil {
					t.Fatal(err)
				}
				for _, secret := range []string{b.RecordID, group.ID, "COUNTERPART-PRIVATE-TEXT", "PRIVATE-REASON"} {
					if strings.Contains(wire, secret) {
						t.Fatalf("undeliverable conflict detail leaked: %s", secret)
					}
				}
				if gate == "private" && pkg.Semantic.Omitted["OPEN_CONFLICT"] != 1 {
					t.Fatalf("private counterpart affected visible count: %+v", pkg.Semantic.Omitted)
				}
				replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: pkg.ReceiptID, Query: req.Query})
				if err != nil || replay.Seal != pkg.Seal {
					t.Fatalf("omitted group replay: %v", err)
				}
			}
		})
	}
}

func TestAdvisoryConflictFreshnessBeforeCachedPull(t *testing.T) {
	for _, change := range []string{"edit", "resolve", "new-conflict", "private"} {
		t.Run(change, func(t *testing.T) {
			ctx := context.Background()
			s, root := testOperator(t)
			repo := uuid.NewString()
			d := projectNote(repo)
			d.Sensitivity = "shareable"
			a, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
			if err != nil {
				t.Fatal(err)
			}
			d.Body = "A competing connection strategy"
			b, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
			if err != nil {
				t.Fatal(err)
			}
			group, err := s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{a.RecordID, b.RecordID}, "Synthetic disagreement"})
			if err != nil {
				t.Fatal(err)
			}
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000, AdvisoryConflicts: true, Mode: "index"}
			idx, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			var handle string
			for _, h := range idx.Handles {
				if h.RecordID == a.RecordID {
					handle = h.Handle
				}
			}
			pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: handle}
			if _, err = s.Expand(ctx, pull, Destination{"hosted", false}); err != nil {
				t.Fatal(err)
			}
			observerChannel := s.channel
			observerChannel.Instrumented = true
			observer := testStore(t, observerChannel)
			run := RunPackageRequest{ReceiptID: idx.Package.ReceiptID, Seal: idx.Package.Seal}
			if _, err = observer.RunIndex(ctx, run, Destination{"hosted", false}); err != nil {
				t.Fatal(err)
			}
			switch change {
			case "edit":
				d.Body = "Revised position"
				_, err = s.Edit(ctx, EditRequest{uuid.NewString(), b.RecordID, b.Version, d})
			case "private":
				// Exercise destination revalidation of restored/restricted metadata;
				// ordinary edits cannot change sensitivity.
				_, err = s.pool.Exec(ctx, `UPDATE cairn.memory_record SET sensitivity='local' WHERE record_id=$1`, b.RecordID)
			case "resolve":
				_, err = s.Resolve(ctx, ResolveRequest{uuid.NewString(), group.ID, group.Version, root.ID, "Synthetic resolution"})
			case "new-conflict":
				c, createErr := s.Create(ctx, CreateRequest{uuid.NewString(), d})
				if createErr != nil {
					t.Fatal(createErr)
				}
				_, err = s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{b.RecordID, c.RecordID}, "Additional synthetic disagreement"})
			}
			if err != nil {
				t.Fatal(err)
			}
			_, err = s.Expand(ctx, pull, Destination{"hosted", false})
			requireCode(t, err, "STALE_HANDLE")
			_, err = observer.RunIndex(ctx, run, Destination{"hosted", false})
			requireCode(t, err, "STALE_PACKAGE")
			_, err = s.Index(ctx, req, Destination{"hosted", false})
			requireCode(t, err, "STALE_PACKAGE")
			replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: idx.Package.ReceiptID, Query: req.Query})
			if err != nil || replay.Seal != idx.Package.Seal {
				t.Fatalf("historical positions changed with live state: %v", err)
			}
		})
	}
}

func TestAdvisoryConflictOverlapBudgetsAndPages(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	ids := []string{}
	for i := 0; i < 3; i++ {
		r, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}) // Equal bodies remain distinct positions.
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, r.RecordID)
	}
	for i := 0; i < 2; i++ {
		if _, err := s.Dispute(ctx, DisputeRequest{uuid.NewString(), ids[i : i+2], "Overlapping synthetic disagreement"}); err != nil {
			t.Fatal(err)
		}
	}
	for _, mode := range []string{"", "index"} {
		omitted, delivered := false, false
		for _, budget := range []int{2000, 4000, 8000, 16000, 32000, 64000} {
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: budget, AdvisoryConflicts: true, Mode: mode}
			p, err := s.Compile(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			count := len(p.Semantic.Selected) + len(p.Semantic.Index)
			if count == 0 {
				omitted = true
			} else if count == 3 {
				delivered = true
			} else {
				t.Fatalf("partial group with %d bytes: %+v", budget, p.Semantic)
			}
			replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: p.ReceiptID, Query: req.Query})
			if err != nil || replay.Seal != p.Seal {
				t.Fatalf("budget replay: %v", err)
			}
		}
		if !omitted || !delivered {
			t.Fatalf("did not exercise both budget boundaries: %v %v", omitted, delivered)
		}
	}
	d.Body = "An unrelated optional note"
	plain, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	for _, offset := range []int{0, 1} {
		req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000, AdvisoryConflicts: true, BrowseOffset: &offset}
		idx, err := s.Index(ctx, req, Destination{"hosted", false})
		if err != nil {
			t.Fatal(err)
		}
		want := 4
		if offset == 1 {
			want = 1
		}
		if len(idx.Package.Semantic.Index) != want {
			t.Fatalf("offset split competing positions: %+v", idx)
		}
		if offset == 1 && idx.Package.Semantic.Index[0].RecordID != plain.RecordID {
			t.Fatal("offset did not skip whole conflict component")
		}
		replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: idx.Package.ReceiptID})
		if err != nil || replay.Seal != idx.Package.Seal {
			t.Fatalf("browse replay: %v", err)
		}
	}
}

func TestAdvisoryConflictRelevanceAndSemanticFallback(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity, d.Kind = "shareable", "decision"
	d.Entities = []EntityRef{{Kind: "file", Name: "core/compiler.go"}}
	a, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body, d.Kind, d.Entities = "A different suggestion with other vocabulary", "note", nil
	b, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{a.RecordID, b.RecordID}, "Synthetic alternative"}); err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"kind", "entity", "semantic", "fallback", "unmatched"} {
		t.Run(mode, func(t *testing.T) {
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000, AdvisoryConflicts: true}
			s.semanticRanker = nil
			want := 2
			switch mode {
			case "kind":
				req.Kinds = []string{"decision"}
			case "entity":
				req.Query, req.Entities = "", a.Entities
			case "fallback":
				req.Semantic, req.Kinds = true, []string{"decision"}
			case "unmatched":
				req.Semantic, req.Query = true, "unmatchedopaqueterm"
				want = 0
			case "semantic":
				req.Semantic, req.Query, req.Kinds = true, "unseen_query", []string{"decision"}
				s.semanticRanker = func(_ context.Context, input SemanticRankRequest) (SemanticRankResult, error) {
					if len(input.Notes) != 2 {
						t.Fatalf("model did not receive all eligible positions: %+v", input)
					}
					result := SemanticRankResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1"}
					for _, n := range input.Notes {
						result.Scores = append(result.Scores, SemanticScore{n.RecordID, n.Version, n.BodySHA256, 500000})
					}
					return result, nil
				}
			}
			idx, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			if len(idx.Package.Semantic.Index) != want {
				t.Fatalf("relevance split or added an unrelated group: %+v", idx.Package.Semantic)
			}
			s.semanticRanker = func(context.Context, SemanticRankRequest) (SemanticRankResult, error) {
				t.Fatal("replay invoked model")
				return SemanticRankResult{}, nil
			}
			replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: idx.Package.ReceiptID, Query: req.Query, Entities: req.Entities})
			if err != nil || replay.Seal != idx.Package.Seal {
				t.Fatalf("relevance replay: %v", err)
			}
		})
	}
}

func TestAdvisoryConflictPreservesEvidenceAndBindingRefusal(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "advisory-writer:" + repo, Repo: repo})
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	a, err := writer.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	evidence, err := s.CaptureEvidence(ctx, EvidenceRequest{uuid.NewString(), repo, "Evidence for one position", "synthetic observation", "shareable", ""})
	if err != nil {
		t.Fatal(err)
	}
	b, err := s.Promote(ctx, PromoteRequest{uuid.NewString(), a.RecordID, a.Version, root.ID, []string{evidence.ID}, "Independent synthetic support", nil})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = "A competing advisory position"
	other, err := writer.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	group, err := s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{b.RecordID, other.RecordID}, "Synthetic evidence disagreement"})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000, AdvisoryConflicts: true}
	idx, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	var handle string
	for _, h := range idx.Handles {
		if h.RecordID == b.RecordID {
			handle = h.Handle
		}
	}
	pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: handle}
	expanded, err := s.Expand(ctx, pull, Destination{"hosted", false})
	if err != nil || len(expanded.Selection.Authority) == 0 || len(expanded.Selection.Evidence) != 1 || len(expanded.Competing) != 1 {
		t.Fatalf("lost evidence or authority qualifier: %+v %v", expanded, err)
	}
	evidencePull := ExpandEvidenceRequest{RequestID: uuid.NewString(), ReceiptID: pull.ReceiptID, Handle: pull.Handle, EvidenceID: evidence.ID, ExpectedSHA256: evidence.Digest}
	if _, err = s.ExpandEvidence(ctx, evidencePull, Destination{"hosted", false}); err != nil {
		t.Fatal(err)
	}
	req.RequestID, req.Purpose = uuid.NewString(), "planning"
	_, err = s.Compile(ctx, req, Destination{"hosted", false})
	requireCode(t, err, "INVALID_REQUEST")
	req.AdvisoryConflicts = false
	p, err := s.Compile(ctx, req, Destination{"hosted", false})
	if err != nil || len(p.Semantic.Selected) != 0 {
		t.Fatalf("consequential disagreement bypass: %+v %v", p, err)
	}
	if _, err = s.Resolve(ctx, ResolveRequest{uuid.NewString(), group.ID, group.Version, root.ID, "Synthetic resolution"}); err != nil {
		t.Fatal(err)
	}
	_, err = s.ExpandEvidence(ctx, evidencePull, Destination{"hosted", false})
	requireCode(t, err, "STALE_HANDLE")
	d.Kind, d.Body = "instruction", "Required synthetic instruction"
	instruction, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, PolicyKey: "conflict-fixture", Reason: "Synthetic binding instruction"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{instruction.RecordID, other.RecordID}, "Binding conflict"}); err != nil {
		t.Fatal(err)
	}
	req.RequestID, req.Purpose, req.AdvisoryConflicts = uuid.NewString(), "context", true
	_, err = s.Compile(ctx, req, Destination{"hosted", false})
	requireCode(t, err, "OPEN_CONFLICT")
}

func TestAdvisoryConflictConnectedComponentLimit(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	ids := []string{}
	for i := 0; i < 17; i++ {
		r, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, r.RecordID)
	}
	for i := 0; i < 16; i++ {
		if _, err := s.Dispute(ctx, DisputeRequest{uuid.NewString(), ids[i : i+2], "Connected synthetic disagreement"}); err != nil {
			t.Fatal(err)
		}
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 1000000, AdvisoryConflicts: true}
	idx, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	if len(idx.Handles) != 0 || len(idx.Package.Semantic.Index) != 0 || idx.Package.Semantic.Omitted["OPEN_CONFLICT"] != 17 {
		t.Fatalf("oversized component was partly admitted: %+v", idx)
	}
	replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: idx.Package.ReceiptID, Query: req.Query})
	if err != nil || replay.Seal != idx.Package.Seal {
		t.Fatalf("bounded component replay: %v", err)
	}
}

func TestAdvisoryConflictPullBudgetAndCompanionDeletion(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	a, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = strings.Repeat("Competing text ", 100)
	b, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	group, err := s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{a.RecordID, b.RecordID}, "Synthetic budget disagreement"})
	if err != nil {
		t.Fatal(err)
	}
	idx, err := s.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000, AdvisoryConflicts: true}, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	var handle string
	for _, h := range idx.Handles {
		if h.RecordID == a.RecordID {
			handle = h.Handle
		}
	}
	pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: handle}
	// Enough for the requested short body but not both positions. The refusal
	// must leave the shared session untouched and record no body exposure.
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.index_session SET remaining_bytes=1500 WHERE receipt_id=$1`, pull.ReceiptID); err != nil {
		t.Fatal(err)
	}
	_, err = s.Expand(ctx, pull, Destination{"hosted", false})
	requireCode(t, err, "BUDGET_REFUSED")
	var credits, remaining, uses int
	if err = s.pool.QueryRow(ctx, `SELECT credits,remaining_bytes FROM cairn.index_session WHERE receipt_id=$1`, pull.ReceiptID).Scan(&credits, &remaining); err != nil {
		t.Fatal(err)
	}
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.usage_observation WHERE receipt_id=$1`, pull.ReceiptID).Scan(&uses); err != nil {
		t.Fatal(err)
	}
	if credits != 4 || remaining != 1500 || uses != 0 {
		t.Fatalf("partial pull changed budget or exposure: %d %d %d", credits, remaining, uses)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.index_session SET remaining_bytes=24000 WHERE receipt_id=$1`, pull.ReceiptID); err != nil {
		t.Fatal(err)
	}
	expanded, err := s.Expand(ctx, pull, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	if expanded.CreditsRemaining != 3 || expanded.BytesRemaining >= 24000-len(b.Body) {
		t.Fatalf("companion did not spend shared budget: %+v", expanded)
	}
	if _, err = s.Resolve(ctx, ResolveRequest{uuid.NewString(), group.ID, group.Version, root.ID, "Resolve before synthetic deletion"}); err != nil {
		t.Fatal(err)
	}
	preview, err := s.PreviewDeletion(ctx, b.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	forgotten, err := s.Forget(ctx, ForgetRequest{uuid.NewString(), b.RecordID, b.Version, root.ID, preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.PurgeDeletion(ctx, forgotten.DeletionID); err != nil {
		t.Fatal(err)
	}
	var purged bool
	if err = s.pool.QueryRow(ctx, `SELECT response IS NULL FROM cairn.mutation_request WHERE operation='expand' AND request_id=$1`, pull.RequestID).Scan(&purged); err != nil || !purged {
		t.Fatalf("cached companion survived deletion: %v %v", purged, err)
	}
	_, err = s.Expand(ctx, pull, Destination{"hosted", false})
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
}

func TestAdvisoryConflictSharedResponsePurgeOrder(t *testing.T) {
	for _, laterFirst := range []bool{true, false} {
		name := "earlier-first"
		if laterFirst {
			name = "later-first"
		}
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			s, root := testOperator(t)
			repo := uuid.NewString()
			d := projectNote(repo)
			d.Sensitivity = "shareable"
			records := []Record{}
			requests := []string{}
			for i := 0; i < 2; i++ {
				request := uuid.NewString()
				r, err := s.Create(ctx, CreateRequest{request, d})
				if err != nil {
					t.Fatal(err)
				}
				records = append(records, r)
				requests = append(requests, request)
			}
			group, err := s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{records[0].RecordID, records[1].RecordID}, "Synthetic shared response"})
			if err != nil {
				t.Fatal(err)
			}
			idx, err := s.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000, AdvisoryConflicts: true}, Destination{"hosted", false})
			if err != nil || len(idx.Handles) != 2 {
				t.Fatalf("conflict index: %+v %v", idx, err)
			}
			pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: idx.Handles[0].Handle}
			expanded, err := s.Expand(ctx, pull, Destination{"hosted", false})
			if err != nil || len(expanded.Competing) != 1 {
				t.Fatalf("shared expansion: %+v %v", expanded, err)
			}
			if _, err = s.Resolve(ctx, ResolveRequest{uuid.NewString(), group.ID, group.Version, root.ID, "Resolve before deletion"}); err != nil {
				t.Fatal(err)
			}
			deletions := []Deletion{}
			for _, r := range records {
				preview, err := s.PreviewDeletion(ctx, r.RecordID)
				if err != nil {
					t.Fatal(err)
				}
				deleted, err := s.Forget(ctx, ForgetRequest{uuid.NewString(), r.RecordID, r.Version, root.ID, preview.PreviewID})
				if err != nil {
					t.Fatal(err)
				}
				deletions = append(deletions, deleted)
			}
			checkResponse := func(operation, request string, wantPurged bool, wantTag string) {
				t.Helper()
				var purged bool
				var tag string
				err := s.pool.QueryRow(ctx, `SELECT response IS NULL,payload_deleted_by::text FROM cairn.mutation_request WHERE operation=$1 AND request_id=$2`, operation, request).Scan(&purged, &tag)
				if err != nil || purged != wantPurged || tag != wantTag {
					t.Fatalf("%s response purged=%v tag=%s, want %v %s: %v", operation, purged, tag, wantPurged, wantTag, err)
				}
			}
			checkResponse("expand", pull.RequestID, false, deletions[0].DeletionID)
			order := []int{0, 1}
			if laterFirst {
				order = []int{1, 0}
			}
			for step, index := range order {
				for retry := 0; retry < 2; retry++ {
					status, err := s.PurgeDeletion(ctx, deletions[index].DeletionID)
					if err != nil || status.State != "limited" {
						t.Fatalf("purge status: %+v %v", status, err)
					}
					checkResponse("expand", pull.RequestID, true, deletions[0].DeletionID)
					checkResponse("create", requests[index], true, deletions[index].DeletionID)
					checkResponse("create", requests[1-index], step == 1, deletions[1-index].DeletionID)
				}
			}
			_, err = s.Expand(ctx, pull, Destination{"hosted", false})
			requireCode(t, err, "PAYLOAD_UNAVAILABLE")
		})
	}
}

func TestAdvisoryConflictSignatureCompanionAndOmittedReplay(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "operator:advisory-signature", Operator: true, Instrumented: true})
	for _, private := range []bool{false, true} {
		repo := uuid.NewString()
		d := projectNote(repo)
		d.Sensitivity, d.Body = "shareable", "Initialize the isolated database"
		proposal, a := signatureLesson(t, s, d)
		_, err := s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: proposal.ID, ExpectedVersion: proposal.Version, Disposition: "converted", ResultRecord: a.RecordID, ResultVersion: a.Version, SignatureShareable: true, Reason: "Synthetic shared failure lesson"})
		if err != nil {
			t.Fatal(err)
		}
		d.Body = "A competing lesson"
		if private {
			d.Sensitivity = "local"
		}
		b, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
		if err != nil {
			t.Fatal(err)
		}
		if _, err = s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{a.RecordID, b.RecordID}, "Synthetic disagreement"}); err != nil {
			t.Fatal(err)
		}
		req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, ErrorSignature: strings.Repeat("b", 64), Purpose: "context", AvailableTokens: 64000, AdvisoryConflicts: true}
		idx, err := s.Index(ctx, req, Destination{"hosted", false})
		if err != nil {
			t.Fatal(err)
		}
		want := 2
		if private {
			want = 0
		}
		if len(idx.Package.Semantic.Index) != want {
			t.Fatalf("signature did not preserve whole-group qualification: %+v", idx)
		}
		replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: idx.Package.ReceiptID})
		if err != nil || replay.Seal != idx.Package.Seal {
			t.Fatalf("signature replay private=%v: %v", private, err)
		}
	}
}

func TestAdvisoryConflictEnvelopeDropsWholeGroupAfterMandatoryContext(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Sensitivity = "shareable"
	a, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = "Competing position"
	b, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{a.RecordID, b.RecordID}, "Synthetic envelope disagreement"}); err != nil {
		t.Fatal(err)
	}
	d.Kind, d.Body = "instruction", strings.Repeat("m", 30000)
	if _, err = s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: root.ID, Mandatory: true, PolicyKey: "envelope", Reason: "Synthetic mandatory envelope"}); err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"", "index"} {
		req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000, Mode: mode, AdvisoryConflicts: true}
		full, err := s.Compile(ctx, req, Destination{"hosted", false})
		if err != nil {
			t.Fatal(err)
		}
		if len(full.Semantic.Selected)+len(full.Semantic.Index) != 3 {
			t.Fatalf("fixture did not admit complete context: %+v", full.Semantic.Omitted)
		}
		rendered, err := full.Render()
		if err != nil {
			t.Fatal(err)
		}
		req.RequestID = uuid.NewString()
		req.AvailableTokens = len(rendered) - 1
		if mode == "index" {
			req.AvailableTokens += 160*len(full.Semantic.Index) + 512
		}
		trimmed, err := s.Compile(ctx, req, Destination{"hosted", false})
		if err != nil {
			t.Fatal(err)
		}
		if len(trimmed.Semantic.Selected) != 1 || !trimmed.Semantic.Selected[0].Mandatory || len(trimmed.Semantic.Index) != 0 || trimmed.Semantic.Omitted["TOTAL_BUDGET"] != 2 {
			t.Fatalf("envelope split a group or evicted required context: %+v", trimmed.Semantic.Omitted)
		}
		replay, err := s.Recompile(ctx, RecompileRequest{ReceiptID: trimmed.ReceiptID, Query: req.Query})
		if err != nil || replay.Seal != trimmed.Seal {
			t.Fatalf("trimmed group replay: %v", err)
		}
	}
}
