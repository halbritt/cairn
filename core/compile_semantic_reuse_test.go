package core

import (
	"context"
	"crypto/sha256"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
)

func reuseFixture(t *testing.T) (context.Context, *Store, Grant, Record, CompileRequest) {
	t.Helper()
	s, root := testOperator(t)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	t.Cleanup(cancel)
	repo := uuid.NewString()
	draft := projectNote(repo)
	draft.Body, draft.Sensitivity = "synthetic retryprobe advice", "shareable"
	record, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	return ctx, s, root, record, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "retryprobe", Purpose: "context", AvailableTokens: 32000, Semantic: true}
}

func reuseResult(input SemanticRankRequest) SemanticRetrievalResult {
	n := input.Notes[0]
	return SemanticRetrievalResult{ModelSHA256: strings.Repeat("a", 64), Algorithm: "fixture/1", Indexed: len(input.Notes), Hits: []SemanticPassageHit{{SemanticScore: SemanticScore{n.RecordID, n.Version, n.BodySHA256, 900000}, Span: ByteSpanRequest{Length: len(n.Body)}}}}
}

func competingReuseIndex(ctx context.Context, s *Store, req CompileRequest, id string) error {
	req.RequestID, req.Semantic = uuid.NewString(), false
	r, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		return err
	}
	if len(r.Package.Semantic.Index) != 1 || r.Package.Semantic.Index[0].RecordID != id {
		return fmt.Errorf("competing index did not expose expected record")
	}
	return nil
}

func TestSemanticReuseProviderDispositionsAfterRealConflict(t *testing.T) {
	for _, mode := range []string{"indexed-ready", "ranker-ready", "ranker-failed", "zero-indexed", "oversized-invalid"} {
		t.Run(mode, func(t *testing.T) {
			ctx, s, _, record, req := reuseFixture(t)
			calls := 0
			before := func(call context.Context) {
				calls++
				if calls == 1 {
					if err := competingReuseIndex(call, s, req, record.RecordID); err != nil {
						t.Fatal(err)
					}
				}
			}
			if strings.HasPrefix(mode, "ranker") {
				s.semanticRanker = func(call context.Context, input SemanticRankRequest) (SemanticRankResult, error) {
					before(call)
					if mode == "ranker-failed" {
						return SemanticRankResult{}, fmt.Errorf("fixture unavailable")
					}
					r := reuseResult(input)
					return SemanticRankResult{ModelSHA256: r.ModelSHA256, Algorithm: r.Algorithm, Scores: []SemanticScore{r.Hits[0].SemanticScore}}, nil
				}
			} else {
				s.semanticRetriever = func(call context.Context, input SemanticRankRequest) (SemanticRetrievalResult, error) {
					before(call)
					if mode == "zero-indexed" {
						return SemanticRetrievalResult{}, nil
					}
					r := reuseResult(input)
					if mode == "oversized-invalid" {
						for len(r.Hits) < 101 {
							r.Hits = append(r.Hits, r.Hits[0])
						}
					}
					return r, nil
				}
			}
			index, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			wantCalls, wantState := 1, "ready"
			if mode == "zero-indexed" || mode == "ranker-failed" {
				wantState = "unavailable"
			}
			if mode == "oversized-invalid" {
				wantCalls, wantState = 2, "invalid_result"
			}
			entries := index.Package.Semantic.Index
			if calls != wantCalls || index.Package.Semantic.Discovery.State != wantState || len(entries) != 1 || entries[0].RecordID != record.RecordID || entries[0].Version != record.Version || entries[0].BodySHA256 != fmt.Sprintf("%x", sha256.Sum256([]byte(record.Body))) {
				t.Fatalf("calls=%d state=%s entries=%+v", calls, index.Package.Semantic.Discovery.State, entries)
			}
			var generation, receipts int
			if err := s.pool.QueryRow(ctx, `SELECT use_generation,(SELECT count(*) FROM cairn.record_use WHERE record_id=$1) FROM cairn.memory_record WHERE record_id=$1`, record.RecordID).Scan(&generation, &receipts); err != nil || generation != 2 || receipts != 2 {
				t.Fatalf("generation=%d uses=%d err=%v", generation, receipts, err)
			}
		})
	}
}

func TestSemanticReuseRecollectsChangedSources(t *testing.T) {
	for _, change := range []string{"revision", "retraction", "synthetic-hosted-restriction"} {
		t.Run(change, func(t *testing.T) {
			ctx, s, root, record, req := reuseFixture(t)
			calls := 0
			s.semanticRetriever = func(call context.Context, input SemanticRankRequest) (SemanticRetrievalResult, error) {
				calls++
				if len(input.Notes) != 1 || input.Notes[0].RecordID != record.RecordID {
					t.Fatal("unexpected candidate identity")
				}
				if calls == 1 {
					switch change {
					case "revision":
						_, err := s.Revise(call, ReviseRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, Repo: req.Scope.Repo, Body: "synthetic retryprobe revised advice"})
						if err != nil {
							t.Fatal(err)
						}
					case "retraction":
						preview, err := s.PreviewRetraction(call, record.RecordID)
						if err != nil {
							t.Fatal(err)
						}
						_, err = s.Retract(call, RetractRequest{uuid.NewString(), record.RecordID, record.Version, root.ID, "Retract synthetic retry candidate", preview.PreviewID})
						if err != nil {
							t.Fatal(err)
						}
					case "synthetic-hosted-restriction":
						// No public sensitivity transition exists. As in
						// recompile_destination_test.go, model a store-owned
						// restriction only in this disposable fixture.
						_, err := s.pool.Exec(call, `UPDATE cairn.memory_record SET sensitivity='local' WHERE record_id=$1`, record.RecordID)
						if err != nil {
							t.Fatal(err)
						}
					}
				} else if change != "revision" || input.Notes[0].Version != 2 || input.Notes[0].BodySHA256 != fmt.Sprintf("%x", sha256.Sum256([]byte("synthetic retryprobe revised advice"))) {
					t.Fatal("retry did not use fresh source")
				}
				return reuseResult(input), nil
			}
			index, err := s.Index(ctx, req, Destination{"hosted", false})
			if err != nil {
				t.Fatal(err)
			}
			if change == "revision" {
				if calls != 2 || len(index.Package.Semantic.Index) != 1 || index.Package.Semantic.Index[0].Version != 2 {
					t.Fatal("stale revision delivered or provider input not refreshed")
				}
			} else if calls != 1 || len(index.Package.Semantic.Index) != 0 {
				t.Fatal("ineligible source delivered after retry")
			}
		})
	}
}

func TestSemanticReuseRechecksCurrentAuthority(t *testing.T) {
	ctx, s, root, record, req := reuseFixture(t)
	grant, err := s.Grant(ctx, GrantRequest{RequestID: uuid.NewString(), ParentID: root.ID, Principal: s.channel.Principal, Repo: req.Scope.Repo, Capabilities: []string{"issue"}, Reason: "Synthetic revocable instruction authority"})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(req.Scope.Repo)
	draft.Kind = "instruction"
	draft.Body = "Mandatory synthetic retry instruction"
	draft.Sensitivity = "shareable"
	instruction, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: grant.ID, Mandatory: true, PolicyKey: "retry-fixture", Reason: "Synthetic required context before retry"})
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	s.semanticRetriever = func(call context.Context, input SemanticRankRequest) (SemanticRetrievalResult, error) {
		calls++
		if calls == 1 {
			_, err := s.RevokeGrant(call, RevokeGrantRequest{uuid.NewString(), grant.ID, root.ID, grant.Version, "Revoke synthetic instruction before retry"})
			if err != nil {
				t.Fatal(err)
			}
			if err := competingReuseIndex(call, s, req, record.RecordID); err != nil {
				t.Fatal(err)
			}
		}
		return reuseResult(input), nil
	}
	index, err := s.Index(ctx, req, Destination{"hosted", false})
	if err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Fatalf("unchanged optional input redispatched %d times", calls)
	}
	for _, entry := range index.Package.Semantic.Selected {
		if entry.Record.RecordID == instruction.RecordID {
			t.Fatal("cached provider outcome bypassed revoked authority")
		}
	}
	var uses int
	if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.record_use WHERE receipt_id=$1 AND record_id=$2`, index.Package.ReceiptID, instruction.RecordID).Scan(&uses); err != nil || uses != 0 {
		t.Fatalf("revoked instruction use retained: %d %v", uses, err)
	}
}

func TestSemanticReuseIsolatedConcurrentPublicCalls(t *testing.T) {
	ctx, s, _, record, req := reuseFixture(t)
	var calls atomic.Int32
	var commits sync.Mutex
	arrived := make(chan struct{}, 2)
	release := make(chan struct{})
	s.semanticRetriever = func(call context.Context, input SemanticRankRequest) (SemanticRetrievalResult, error) {
		calls.Add(1)
		select {
		case arrived <- struct{}{}:
		case <-call.Done():
			return SemanticRetrievalResult{}, call.Err()
		}
		select {
		case <-release:
		case <-call.Done():
			return SemanticRetrievalResult{}, call.Err()
		}
		commits.Lock()
		err := competingReuseIndex(call, s, req, record.RecordID)
		commits.Unlock()
		if err != nil {
			return SemanticRetrievalResult{}, err
		}
		return reuseResult(input), nil
	}
	type outcome struct {
		result IndexResult
		err    error
	}
	done := make(chan outcome, 2)
	for range 2 {
		go func() {
			r := req
			r.RequestID = uuid.NewString()
			index, err := s.Index(ctx, r, Destination{"hosted", false})
			done <- outcome{index, err}
		}()
	}
	for range 2 {
		select {
		case <-arrived:
		case <-ctx.Done():
			t.Fatal("two independent callbacks did not reach barrier")
		}
	}
	close(release)
	seen := map[string]bool{}
	for range 2 {
		select {
		case out := <-done:
			if out.err != nil {
				t.Fatal(out.err)
			}
			seen[out.result.Package.ReceiptID] = true
		case <-ctx.Done():
			t.Fatal("concurrent compiles did not complete")
		}
	}
	if calls.Load() != 2 || len(seen) != 2 {
		t.Fatalf("public call outcomes shared: calls=%d receipts=%d", calls.Load(), len(seen))
	}
	var generation, uses int
	if err := s.pool.QueryRow(ctx, `SELECT use_generation,(SELECT count(*) FROM cairn.record_use WHERE record_id=$1) FROM cairn.memory_record WHERE record_id=$1`, record.RecordID).Scan(&generation, &uses); err != nil || generation != 4 || uses != 4 {
		t.Fatalf("both retries not committed: generation=%d uses=%d err=%v", generation, uses, err)
	}
}
