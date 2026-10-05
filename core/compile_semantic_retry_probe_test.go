package core

import (
	"context"
	"crypto/sha256"
	"fmt"
	"testing"
	"time"

	"github.com/google/uuid"
)

// Both exposure writers use public Index; the existing record_use trigger, not
// an injected SQL error or direct mutation, causes the snapshot conflicts.
func TestCompileSemanticReuseAfterCompetingExposure(t *testing.T) {
	for _, competing := range []int{0, 1} {
		t.Run(fmt.Sprintf("competing_exposures_%d", competing), func(t *testing.T) {
			s, _ := testOperator(t)
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			repo := uuid.NewString()
			draft := projectNote(repo)
			draft.Body = "synthetic retryprobe exposure note"
			draft.Sensitivity = "shareable"
			record, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
			if err != nil {
				t.Fatal(err)
			}
			bodyHash := fmt.Sprintf("%x", sha256.Sum256([]byte(draft.Body)))
			request := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"},
				Query: "retryprobe", Purpose: "context", AvailableTokens: 32000, Semantic: true}
			destination := Destination{"hosted", false}
			generation := func() int64 {
				t.Helper()
				var value int64
				var version int
				if err := s.pool.QueryRow(ctx, `SELECT use_generation,current_version FROM cairn.memory_record WHERE record_id=$1`, record.RecordID).Scan(&value, &version); err != nil {
					t.Fatal(err)
				}
				if version != record.Version {
					t.Fatalf("content version changed: %d", version)
				}
				return value
			}
			assertIndex := func(index IndexResult) {
				t.Helper()
				entries := index.Package.Semantic.Index
				if len(entries) != 1 || entries[0].RecordID != record.RecordID || entries[0].Version != record.Version || entries[0].BodySHA256 != bodyHash {
					t.Fatalf("expected exact synthetic exposure, got %+v", entries)
				}
				if index.Package.ReceiptID == "" || len(index.Package.Semantic.Selected) != 0 {
					t.Fatal("expected committed optional index exposure only")
				}
			}
			if got := generation(); got != 0 {
				t.Fatalf("initial exposure generation %d", got)
			}
			calls := 0
			receipts := make(map[string]string)
			s.semanticRetriever = func(call context.Context, input SemanticRankRequest) (SemanticRetrievalResult, error) {
				calls++
				if input.Query != request.Query || len(input.Notes) != 1 {
					t.Fatalf("unexpected candidate set on call %d: %+v", calls, input)
				}
				note := input.Notes[0]
				if note.RecordID != record.RecordID || note.Version != record.Version || note.BodySHA256 != bodyHash || note.Body != draft.Body {
					t.Fatalf("candidate identity changed on call %d", calls)
				}
				if calls > competing+1 {
					t.Fatalf("unexpected extra semantic invocation %d", calls)
				}
				// This callback is a synchronous barrier: collection established
				// the outer snapshot, and it cannot reach its exposure writes until
				// this nested, independent public transaction has committed.
				if calls <= competing {
					lexical := request
					lexical.RequestID, lexical.Semantic = uuid.NewString(), false
					index, err := s.Index(call, lexical, destination)
					if err != nil {
						t.Fatalf("competing lexical commit: %v", err)
					}
					assertIndex(index)
					receipts[lexical.RequestID] = index.Package.ReceiptID
					if got := generation(); got != int64(calls) {
						t.Fatalf("competing exposure not committed: generation %d, want %d", got, calls)
					}
				}
				// Match StartIndex's per-call budget without invoking a worker,
				// model, semantic index or provider. Outer context stays live so
				// rankIndexed takes its ordinary lexical fallback.
				bounded, stop := context.WithTimeout(call, 2*time.Second)
				defer stop()
				<-bounded.Done()
				return SemanticRetrievalResult{}, bounded.Err()
			}
			started := time.Now()
			index, err := s.Index(ctx, request, destination)
			elapsed := time.Since(started)
			if err != nil {
				t.Fatal(err)
			}
			assertIndex(index)
			if calls != 1 || index.Package.Semantic.Discovery == nil || index.Package.Semantic.Discovery.State != "unavailable" {
				t.Fatalf("semantic calls=%d, discovery=%+v", calls, index.Package.Semantic.Discovery)
			}
			receipts[request.RequestID] = index.Package.ReceiptID
			if len(receipts) != competing+1 || generation() != int64(competing+1) {
				t.Fatal("failed outer attempts changed committed receipt/exposure counts")
			}
			for requestID, receiptID := range receipts {
				var count int
				if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.retrieval_receipt r JOIN cairn.record_use u USING(receipt_id)
 WHERE r.caller=$1 AND r.request_id=$2 AND r.receipt_id=$3 AND u.record_id=$4 AND u.version=$5 AND u.exposure_kind='index'`,
					s.channel.Principal, requestID, receiptID, record.RecordID, record.Version).Scan(&count); err != nil || count != 1 {
					t.Fatalf("request did not retain exactly its matching exposure: count=%d err=%v", count, err)
				}
			}
			var uses int
			if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.record_use WHERE record_id=$1`, record.RecordID).Scan(&uses); err != nil || uses != competing+1 {
				t.Fatalf("unexpected retained uses=%d err=%v", uses, err)
			}
			t.Logf("competing_commits=%d semantic_calls=%d retained_receipts=%d use_generation=%d elapsed=%s", competing, calls, len(receipts), generation(), elapsed)
		})
	}
}
