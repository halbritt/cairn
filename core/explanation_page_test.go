package core

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/google/uuid"
	"strings"
	"testing"
	"time"
)

func TestExplainPagePreservesOriginalEvaluatedReferences(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "explain-page-owner"})
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body = "needle original"
	a, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	d.Body = "unrelated source"
	b, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{Repo: repo, TaskID: "inspection", RunID: "original"}, Query: "needle", Purpose: "context", Mode: "index", AvailableTokens: 32000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	first, err := s.ExplainPageForDestination(ctx, ExplainPageRequest{ReceiptID: p.ReceiptID, Limit: 1}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Candidates) != 1 || !first.More {
		t.Fatalf("first page %+v", first)
	}
	if _, err = s.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: a.RecordID, ExpectedVersion: 1, Repo: repo, Body: "later needle replacement"}); err != nil {
		t.Fatal(err)
	}
	d.Body = "new needle source"
	if _, err = s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	next, err := s.ExplainPageForDestination(ctx, ExplainPageRequest{ReceiptID: p.ReceiptID, AfterRecordID: first.NextAfterRecordID, AfterVersion: first.NextAfterVersion, Limit: 1}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(next.Candidates) != 1 || next.More {
		t.Fatalf("next page %+v", next)
	}
	rows := append(first.Candidates, next.Candidates...)
	seen := map[string]CandidateReference{}
	for _, r := range rows {
		seen[r.RecordID] = r
		if r.Version != 1 {
			t.Fatal("current version replaced history")
		}
	}
	if seen[a.RecordID].Reason != "INDEXED" || seen[b.RecordID].Reason != "NO_LEXICAL_MATCH" || seen[b.RecordID].Rank != 0 {
		t.Fatalf("wrong retained reasons %+v", rows)
	}
	encoded, err := json.Marshal(first)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "needle original") || strings.Contains(string(encoded), "evidence") {
		t.Fatalf("raw content escaped: %s", encoded)
	}
}

func TestExplainPageUncomputedRankedCostIsUnknown(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "explain-page-cost"})
	repo := uuid.NewString()
	for _, body := range []string{"needle first source", "needle second source"} {
		d := projectNote(repo)
		d.Body = body
		if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
			t.Fatal(err)
		}
	}
	offset := 1
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{Repo: repo, TaskID: "inspection", RunID: "original"}, Query: "needle", Purpose: "context", Mode: "index", AvailableTokens: 32000, PageOffset: &offset}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	page, err := s.ExplainPageForDestination(ctx, ExplainPageRequest{ReceiptID: p.ReceiptID}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, r := range page.Candidates {
		if r.Reason == "PAGE_OFFSET" {
			found = true
			if r.Rank == 0 || r.Cost != nil {
				t.Fatalf("uncomputed ranked cost was fabricated: %+v", r)
			}
		}
	}
	if !found {
		t.Fatal("fixture did not retain page-offset omission")
	}
}

func TestExplainPageRejectsMalformedRetainedMetadata(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "explain-page-malformed"})
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body = "needle"
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "needle", Purpose: "context", Mode: "index", AvailableTokens: 32000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	var original []byte
	if err = s.pool.QueryRow(ctx, `SELECT detail FROM cairn.retrieval_candidate WHERE receipt_id=$1 AND record_id=$2`, p.ReceiptID, r.RecordID).Scan(&original); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name   string
		change func(map[string]any)
	}{
		{"missing rank", func(v map[string]any) { delete(v, "rank") }},
		{"null boolean", func(v map[string]any) { v["entity_match"] = nil }},
		{"false numeric type", func(v map[string]any) { v["lexical_matches"] = "1" }},
		{"oversized projection", func(v map[string]any) { v["record_id"] = strings.Repeat("secret", 10000) }},
		{"inconsistent identity", func(v map[string]any) { v["version"] = 2 }},
		{"invalid score", func(v map[string]any) { v["semantic_score"] = 1000001 }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var v map[string]any
			if err = json.Unmarshal(original, &v); err != nil {
				t.Fatal(err)
			}
			tc.change(v)
			body, err := json.Marshal(v)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_candidate SET detail=$3 WHERE receipt_id=$1 AND record_id=$2`, p.ReceiptID, r.RecordID, body); err != nil {
				t.Fatal(err)
			}
			out, err := s.ExplainPageForDestination(ctx, ExplainPageRequest{ReceiptID: p.ReceiptID}, Destination{"local", true})
			requireCode(t, err, "INTEGRITY_FAILURE")
			if len(out.Candidates) != 0 || out.ReceiptID != "" {
				t.Fatal("partial metadata on error")
			}
		})
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_candidate SET detail=$3 WHERE receipt_id=$1 AND record_id=$2`, p.ReceiptID, r.RecordID, original); err != nil {
		t.Fatal(err)
	}
	// Negative and zero semantic values are valid retained cosine scores. Missing
	// IDF remains null; inspection does not pretend to reconstruct the ranking.
	for _, score := range []int{-1000000, 0, 1000000} {
		if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_candidate SET detail=jsonb_set(detail,'{semantic_score}',to_jsonb($3::int))-'idf_score' WHERE receipt_id=$1 AND record_id=$2`, p.ReceiptID, r.RecordID, score); err != nil {
			t.Fatal(err)
		}
		out, err := s.ExplainPageForDestination(ctx, ExplainPageRequest{ReceiptID: p.ReceiptID}, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		if len(out.Candidates) != 1 || out.Candidates[0].SemanticScore == nil || *out.Candidates[0].SemanticScore != score || out.Candidates[0].IDFScore != nil {
			t.Fatalf("score absence/zero changed: %+v", out)
		}
	}
}

func TestExplainPageWholeRowsRespectEncodedBound(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "explain-page-bounds"})
	repo := uuid.NewString()
	for i := 0; i < 105; i++ {
		d := projectNote(repo)
		d.Body = fmt.Sprintf("needle source %d", i)
		if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
			t.Fatal(err)
		}
	}
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "needle", Purpose: "context", Mode: "index", AvailableTokens: 32000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	req := ExplainPageRequest{ReceiptID: p.ReceiptID, Limit: 100}
	seen := map[string]bool{}
	pages := 0
	for {
		out, err := s.ExplainPageForDestination(ctx, req, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		pages++
		data, err := json.Marshal(out)
		if err != nil {
			t.Fatal(err)
		}
		if len(data) > 30*1024 || len(out.Candidates) == 0 || len(out.Candidates) > 100 {
			t.Fatalf("invalid bound %d/%d", len(data), len(out.Candidates))
		}
		for _, r := range out.Candidates {
			if seen[r.RecordID] {
				t.Fatal("duplicate reference")
			}
			seen[r.RecordID] = true
		}
		if !out.More {
			if out.NextAfterRecordID != "" || out.NextAfterVersion != 0 {
				t.Fatal("end cursor")
			}
			break
		}
		if len(out.Candidates) >= 100 {
			t.Fatal("fixture failed to exercise byte-driven continuation")
		}
		last := out.Candidates[len(out.Candidates)-1]
		if out.NextAfterRecordID != last.RecordID || out.NextAfterVersion != last.Version {
			t.Fatal("cursor does not name last whole row")
		}
		req.AfterRecordID, req.AfterVersion = out.NextAfterRecordID, out.NextAfterVersion
	}
	if len(seen) != 105 || pages < 2 {
		t.Fatalf("lost candidates %d in %d pages", len(seen), pages)
	}
}

func TestExplainPageRestorePauseAndHistoricalFence(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	verify, _, _ := restoreVerificationFixture(t, s)
	receipt := verify.Fixtures[0].ReceiptID
	req := ExplainPageRequest{ReceiptID: receipt}
	dest := Destination{"local", true}
	_, err := s.ExplainPageForDestination(ctx, req, dest)
	requireCode(t, err, "RESTORE_PAUSED")
	if _, err = s.ResumeRestore(ctx, ResumeRestoreRequest{RequestID: uuid.NewString(), Verification: verify, Policy: "local-restore/1", Reason: "Resume isolated candidate inspection fixture"}); err != nil {
		t.Fatal(err)
	}
	page, err := s.ExplainPageForDestination(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	if !page.Historical || len(page.Candidates) != 1 {
		t.Fatalf("lost historical reference %+v", page)
	}
	requireCode(t, s.ClaimRun(ctx, receipt), "STALE_PACKAGE")
}

func TestExplainPageWaitsForIdentityLockAndCancels(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "explain-page-lock"})
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body = "needle"
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "needle", Purpose: "context", Mode: "index", AvailableTokens: 32000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `SELECT record_id FROM cairn.memory_record WHERE record_id=$1 FOR UPDATE`, r.RecordID); err != nil {
		t.Fatal(err)
	}
	deadline, cancel := context.WithTimeout(ctx, 150*time.Millisecond)
	defer cancel()
	out, err := s.ExplainPageForDestination(deadline, ExplainPageRequest{ReceiptID: p.ReceiptID}, Destination{"local", true})
	if err == nil || deadline.Err() == nil || len(out.Candidates) != 0 {
		t.Fatalf("inspection ignored identity lock/cancellation: %+v %v", out, err)
	}
	if err = tx.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err = s.ExplainPageForDestination(ctx, ExplainPageRequest{ReceiptID: p.ReceiptID}, Destination{"local", true}); err != nil {
		t.Fatalf("cancellation leaked resource: %v", err)
	}
}

func TestExplainPageMissingFactsDistinguishesRejectionFromIncomplete(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "explain-page-facts"})
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Body = "needle"
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "needle", Purpose: "context", Mode: "index", AvailableTokens: 32000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_candidate SET detail=detail-'facts' WHERE receipt_id=$1 AND record_id=$2`, p.ReceiptID, r.RecordID); err != nil {
		t.Fatal(err)
	}
	_, err = s.ExplainPageForDestination(ctx, ExplainPageRequest{ReceiptID: p.ReceiptID}, Destination{"local", true})
	requireCode(t, err, "REPLAY_INCOMPLETE")
	if _, err = s.pool.Exec(ctx, `UPDATE cairn.retrieval_candidate SET reason='EVIDENCE_UNAVAILABLE',detail=(detail-'idf_score')||'{"reason":"EVIDENCE_UNAVAILABLE","rank":0,"cost_bytes":0}'::jsonb WHERE receipt_id=$1 AND record_id=$2`, p.ReceiptID, r.RecordID); err != nil {
		t.Fatal(err)
	}
	out, err := s.ExplainPageForDestination(ctx, ExplainPageRequest{ReceiptID: p.ReceiptID}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(out.Candidates) != 1 || out.Candidates[0].FactsPresent || out.Candidates[0].BodySHA256 != nil || out.Candidates[0].Cost != nil {
		t.Fatalf("missing facts on rejection misrepresented: %+v", out)
	}
}
