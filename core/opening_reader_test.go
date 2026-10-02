package core

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestOpeningReaderRefusalPreservesBudgetAndRetry(t *testing.T) {
	ctx := context.Background()
	s, _ := testOperator(t)
	d := projectNote(uuid.NewString())
	d.Body = "Project test. " + strings.Repeat("context ", 40) + "tail"
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), d}); err != nil {
		t.Fatal(err)
	}
	dest := Destination{"local", true}
	for _, legacy := range []bool{false, true} {
		idx, err := s.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{d.Scope.Repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, dest)
		if err != nil {
			t.Fatal(err)
		}
		if legacy {
			setTestSpanContext(t, s, &idx, "")
		}
		pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: idx.Package.ReceiptID, Handle: idx.Handles[0].Handle, Span: &ByteSpanRequest{Offset: len(d.Body) - 4, Length: 4}}
		if legacy {
			pull.OpeningBytes = 96
		}
		var before, after struct{ credits, remaining, observations, mutations int }
		read := func(out *struct{ credits, remaining, observations, mutations int }) {
			t.Helper()
			err := s.pool.QueryRow(ctx, `SELECT credits,remaining_bytes,(SELECT count(*) FROM cairn.usage_observation WHERE receipt_id=$1),(SELECT count(*) FROM cairn.mutation_request WHERE request_id=$2) FROM cairn.index_session WHERE receipt_id=$1`, pull.ReceiptID, pull.RequestID).Scan(&out.credits, &out.remaining, &out.observations, &out.mutations)
			if err != nil {
				t.Fatal(err)
			}
		}
		read(&before)
		_, err = s.ExpandForReader(ctx, pull, dest, false)
		requireCode(t, err, "PROTOCOL_UNSUPPORTED")
		read(&after)
		if before != after {
			t.Fatalf("unsupported reader changed receipt: %v -> %v", before, after)
		}
		got, err := s.ExpandForReader(ctx, pull, dest, true)
		if err != nil || got.SourceOpening == nil || got.SourceOpening.Body != d.Body[:96] || got.Span.Body != "tail" || got.CreditsRemaining != before.credits-1 {
			t.Fatalf("upgraded retry: %+v %v", got, err)
		}
		read(&before)
		_, err = s.ExpandForReader(ctx, pull, dest, false)
		requireCode(t, err, "PROTOCOL_UNSUPPORTED")
		read(&after)
		if before != after {
			t.Fatalf("cached refusal changed receipt: %v -> %v", before, after)
		}
		again, err := s.ExpandForReader(ctx, pull, dest, true)
		if err != nil || !sameOpeningWire(t, got, again) {
			t.Fatalf("capable retry changed: %v", err)
		}
		for _, span := range []*ByteSpanRequest{nil, {Offset: 0, Length: 10}} {
			pull.RequestID, pull.Span, pull.OpeningBytes = uuid.NewString(), span, 0
			plain, err := s.ExpandForReader(ctx, pull, dest, false)
			if err != nil || plain.SourceOpening != nil {
				t.Fatalf("unpaired read unavailable: %+v %v", plain, err)
			}
		}
		if legacy {
			pull.RequestID, pull.Span = uuid.NewString(), &ByteSpanRequest{Offset: len(d.Body) - 4, Length: 4}
			plain, err := s.ExpandForReader(ctx, pull, dest, false)
			if err != nil || plain.SourceOpening != nil || plain.Span.Body != "tail" {
				t.Fatalf("legacy read changed: %+v %v", plain, err)
			}
		}
	}
}
