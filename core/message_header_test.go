package core

import (
	"context"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
)

func TestTruncateCleanUTF8And9579ByteBoundary(t *testing.T) {
	line := "IC1 requested concrete finite API/owner edge — 日本語 verification line.\n"
	var b strings.Builder
	for b.Len() < 9579 {
		b.WriteString(line)
	}
	full := b.String()[:9579]
	for !utf8.ValidString(full) {
		full = full[:len(full)-1] + "x"
	}
	for len(full) < 9579 {
		full += "y"
	}
	if len(full) != 9579 || !utf8.ValidString(full) {
		t.Fatalf("expected valid 9579-byte UTF-8 fixture, got len=%d", len(full))
	}

	prefix, inc, omitted := TruncateCleanUTF8(full, 1500)
	if !utf8.ValidString(prefix) || inc != len(prefix) || inc+omitted != 9579 {
		t.Fatalf("invalid truncation accounting: inc=%d omitted=%d len=%d", inc, omitted, len(prefix))
	}
	if strings.HasSuffix(prefix, "\n") {
		t.Fatalf("expected clean newline cut without trailing LF: %q", prefix[len(prefix)-20:])
	}
	marker := FormatTruncationMarker(omitted, 9579, "cairn-inbox read abcd1234abcd1234")
	if !strings.Contains(marker, "of 9579 UTF-8 bytes") || !strings.Contains(marker, "cairn-inbox read abcd1234abcd1234") {
		t.Fatalf("unexpected marker: %s", marker)
	}

	// Multi-byte codepoint boundary without spaces/newlines never splits a UTF-8 rune.
	dense := strings.Repeat("あ", 400) // 1200 UTF-8 bytes (3 bytes per rune)
	dp, dInc, dOmitted := TruncateCleanUTF8(dense, 500)
	if !utf8.ValidString(dp) || dInc%3 != 0 || dInc+dOmitted != 1200 {
		t.Fatalf("split multi-byte rune: inc=%d omitted=%d valid=%v", dInc, dOmitted, utf8.ValidString(dp))
	}

	// Malformed trailing multi-byte starter immediately before an ASCII byte backs up cleanly.
	malformed := "abc\xe3\x81xyz"
	mp, mInc, mOmitted := TruncateCleanUTF8(malformed, 5)
	if mp != "abc" || mInc != 3 || mOmitted != len(malformed)-3 || !utf8.ValidString(mp) {
		t.Fatalf("malformed UTF-8 boundary not cleaned: prefix=%q inc=%d omitted=%d", mp, mInc, mOmitted)
	}
}

func TestEventSummaryHeaderPersistenceAndValidation(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	coord := f.register(nativeAgent{Binding: "coord-hdr"})
	lead := f.register(nativeAgent{Binding: "lead-hdr"})
	leadView := f.view(sessionRef(lead))
	coordView := f.view(sessionRef(coord))

	for _, bad := range []string{"   ", "line1\nline2", "tab\there", "esc\x1b[31m", "bidi\u202eoverride", "sep\u2028line", strings.Repeat("a", 281)} {
		_, err := leadView.PublishEvent(ctx, PublishEventRequest{
			RequestID: uuid.NewString(), Repo: f.repo, Kind: "notice", Summary: bad,
			Ref:         RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
			Destination: EventDestination{Type: "agent", Name: coord.Inbox},
		}, f.dest)
		requireCode(t, err, "INVALID_REQUEST")
	}

	due := time.Now().UTC().Add(2 * time.Hour).Truncate(time.Second)
	ev, err := leadView.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, Kind: "request",
		Summary:  "IC1 requested concrete finite API/owner edge",
		ExpectBy: &due,
		Ref:      RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination: EventDestination{
			Type: "agent", Name: coord.Inbox,
		},
	}, f.dest)
	if err != nil || ev.Summary != "IC1 requested concrete finite API/owner edge" {
		t.Fatalf("PublishEvent summary mismatch: %+v %v", ev, err)
	}
	hdr := ev.Header()
	if hdr == nil || hdr.Summary != ev.Summary || hdr.ActionNeeded != "complete" || hdr.Due == nil || !hdr.Due.Equal(due) {
		t.Fatalf("unexpected Header(): %+v", hdr)
	}
	claimed, err := coordView.NextEvent(ctx, NextEventRequest{Repo: f.repo, LeaseSeconds: 60}, f.dest)
	if err != nil || claimed.Delivery == nil || claimed.Delivery.Event.Summary != ev.Summary {
		t.Fatalf("claimed delivery missing summary: %+v %v", claimed, err)
	}

	// Failing and reissuing a headered request preserves Summary and future ExpectBy.
	if _, err := coordView.CompleteEvent(ctx, CompleteEventRequest{
		RequestID: uuid.NewString(), DeliveryID: claimed.Delivery.DeliveryID, LeaseID: claimed.Delivery.LeaseID,
		Disposition: "failed", Code: "processing_failed",
	}, f.dest); err != nil {
		t.Fatalf("CompleteEvent failed: %v", err)
	}
	opStore := testStore(t, Channel{Principal: "operator:admin", Operator: true})
	reissued, err := opStore.ReissueEvent(ctx, ReissueEventRequest{
		RequestID: uuid.NewString(), Repo: f.repo, DeliveryID: claimed.Delivery.DeliveryID,
		Reason: "retry headered request", AcceptUncertainEffects: true,
	})
	if err != nil {
		t.Fatalf("ReissueEvent failed: %v", err)
	}
	if reissued.Summary != ev.Summary || reissued.ExpectBy == nil || !reissued.ExpectBy.Equal(due) {
		t.Fatalf("ReissueEvent lost header fields: %+v", reissued)
	}
}
