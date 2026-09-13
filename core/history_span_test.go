package core

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"testing"

	"github.com/google/uuid"
)

func TestHistorySpanPreservesEarlierBytesAndIdentity(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "history-excerpts"})
	draft := projectNote(uuid.NewString())
	draft.Body = "old 雪 guidance"
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Revise(ctx, ReviseRequest{uuid.NewString(), r.RecordID, 1, r.Scope.Repo, "new"}); err != nil {
		t.Fatal(err)
	}
	dest := Destination{"local", true}
	for _, tc := range []struct {
		name                string
		offset, length, end int
		base64              bool
	}{
		{"text", 0, 3, 3, false},
		{"unicode", 4, 3, 7, false},
		{"split-unicode", 5, 1, 6, true},
		{"clip-at-eof", 8, 65536, len(draft.Body), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			history, err := s.History(ctx, RecordHistoryRequest{RecordID: r.RecordID, Version: 1, Span: &ByteSpanRequest{Offset: tc.offset, Length: tc.length}}, dest)
			if err != nil || len(history.Versions) != 1 {
				t.Fatalf("read excerpt: %+v %v", history, err)
			}
			v := history.Versions[0]
			if !history.Historical || history.CurrentVersion != 2 || v.Version != 1 || v.Class != "A" || v.Body != nil || v.Span == nil || v.BodySHA256 != fmt.Sprintf("%x", sha256.Sum256([]byte(draft.Body))) || v.BodyBytes != len(draft.Body) || v.Scope != r.Scope || v.ObservedWriter != r.ObservedWriter || !v.WrittenAt.Equal(r.WrittenAt) {
				t.Fatalf("excerpt lost historical identity: %+v", history)
			}
			span := v.Span
			selected := draft.Body[tc.offset:tc.end]
			if span.Offset != tc.offset || span.End != tc.end || span.TotalBytes != len(draft.Body) || span.SHA256 != fmt.Sprintf("%x", sha256.Sum256([]byte(selected))) {
				t.Fatalf("incorrect byte range: %+v", span)
			}
			if tc.base64 {
				if span.Body != "" || span.BodyBase64 != base64.StdEncoding.EncodeToString([]byte(selected)) {
					t.Fatalf("split UTF-8 bytes changed: %+v", span)
				}
			} else if span.Body != selected || span.BodyBase64 != "" {
				t.Fatalf("selected text changed: %+v", span)
			}
		})
	}
	for _, span := range []ByteSpanRequest{{-1, 1}, {65536, 1}, {0, 0}, {0, -1}, {0, 65537}, {len(draft.Body), 1}, {int(^uint(0) >> 1), 1}, {1, int(^uint(0) >> 1)}} {
		_, err = s.History(ctx, RecordHistoryRequest{RecordID: r.RecordID, Version: 1, Span: &span}, dest)
		requireCode(t, err, "INVALID_REQUEST")
	}
	_, err = s.History(ctx, RecordHistoryRequest{RecordID: r.RecordID, Span: &ByteSpanRequest{0, 1}}, dest)
	requireCode(t, err, "INVALID_REQUEST")
	_, err = s.History(ctx, RecordHistoryRequest{RecordID: r.RecordID, Version: 3, Span: &ByteSpanRequest{0, 1}}, dest)
	requireCode(t, err, "NOT_FOUND")
	// Inspection must not add durable references that prevent ordinary deletion.
	if _, err = s.Delete(ctx, DeleteRequest{uuid.NewString(), r.RecordID, 2}); err != nil {
		t.Fatal(err)
	}
}
