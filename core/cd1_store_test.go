package core

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestCD1PublicationEnvelopeCrossCheckAndAutoSettleSafety(t *testing.T) {
	ctx := context.Background()
	f := newNativeFixture(t)
	sender := f.sender
	receiver := f.receiver
	optIn := f.register(nativeAgent{Binding: "cd1-auto-settle"})
	to := EventDestination{Type: "agent", Name: optIn.Inbox}

	saveNote := func(body string) RecordVersionRef {
		draft := projectNote(f.repo)
		draft.Body = body
		draft.Sensitivity = "shareable"
		rec, err := sender.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
		if err != nil {
			t.Fatal(err)
		}
		return RecordVersionRef{RecordID: rec.RecordID, Version: rec.Version}
	}

	// 1. Valid cd1 notice with act=info and envelope action=none publishes & auto-settles.
	validInfo := saveNote("cd1 notice " + f.repo + " T1 done act=info\nobs cache warmed")
	evInfo, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "none", Ref: validInfo, Destination: to,
	}, f.dest)
	if err != nil {
		t.Fatalf("valid cd1 act=info failed: %v", err)
	}
	settleRes, err := receiver.SettleSessionActionNone(ctx, SessionInboxSettleNone{
		RequestID: uuid.NewString(), Session: sessionRef(optIn),
	}, f.dest)
	if err != nil || len(settleRes.Settled) != 1 {
		t.Fatalf("expected valid cd1 act=info to auto-settle: %+v %v", settleRes, err)
	}
	st, err := sender.AgentEventStatus(ctx, EventStatusRequest{EventID: evInfo.EventID}, f.dest)
	if err != nil || st.Deliveries[0].State != "handled" || st.Deliveries[0].Code != "auto_settled_none" {
		t.Fatalf("unexpected delivery status: %+v %v", st, err)
	}

	// 2. Mismatched act vs envelope action rejected at publication.
	mismatchRef := saveNote("cd1 notice " + f.repo + " T2 active act=work\nobs working")
	_, err = sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "none", Ref: mismatchRef, Destination: to,
	}, f.dest)
	requireCode(t, err, "INVALID_REQUEST")

	// 3. Envelope action=none with substantive blk/req/dec/q rejected even when act= omitted.
	hiddenBlk := saveNote("cd1 notice " + f.repo + " T3 active\nblk waiting on fixture")
	_, err = sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "none", Ref: hiddenBlk, Destination: to,
	}, f.dest)
	requireCode(t, err, "INVALID_REQUEST")

	// 4. Unknown protocol version (cd2) rejected at publication.
	cd2Ref := saveNote("cd2 notice " + f.repo + " T4 active act=info\nobs unknown")
	_, err = sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "none", Ref: cd2Ref, Destination: to,
	}, f.dest)
	requireCode(t, err, "INVALID_REQUEST")

	// 5. Legacy/unvalidated cd2 stored under bypass must NEVER auto-settle.
	t.Setenv("CAIRN_ALLOW_UNVALIDATED_CD1", "1")
	legacyBad, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "none", Ref: cd2Ref, Destination: to,
	}, f.dest)
	if err != nil {
		t.Fatalf("expected bypass publish for legacy test: %v", err)
	}
	t.Setenv("CAIRN_ALLOW_UNVALIDATED_CD1", "")
	settleBad, err := receiver.SettleSessionActionNone(ctx, SessionInboxSettleNone{
		RequestID: uuid.NewString(), Session: sessionRef(optIn),
	}, f.dest)
	if err != nil || len(settleBad.Settled) != 0 || settleBad.Ready.EventID != legacyBad.EventID {
		t.Fatalf("expected invalid/cd2 notice NOT to auto-settle and remain ready: %+v %v", settleBad, err)
	}

	// 6. L1 delta referencing parent handle resolves via CausationID / CorrelationID thread context.
	l0Ref := saveNote("cd1 req " + f.repo + " T6 pending act=work\nref e1=7c3a1b9e-4d2f-4a8c-9e1b-0f2a3c4d5e6f\nauth root@2026-11-25\nreq run checks")
	l0Ev, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "request", Ref: l0Ref, Destination: to,
	}, f.dest)
	if err != nil {
		t.Fatalf("L0 publish failed: %v", err)
	}
	l1Ref := saveNote("cd1 notice " + f.repo + " T6 verified re=e1 act=review\nchk unit pass")
	// Without causation/correlation, L1 fails with UNBOUND_HANDLE.
	_, err = sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "review", Ref: l1Ref, Destination: to,
	}, f.dest)
	if err == nil || !strings.Contains(err.Error(), "UNBOUND_HANDLE") {
		t.Fatalf("expected UNBOUND_HANDLE without causation/correlation, got %v", err)
	}
	// With causation_id linking to l0Ev, L1 resolves e1 and publishes cleanly.
	if _, err := sender.PublishEvent(ctx, PublishEventRequest{
		RequestID: uuid.NewString(), Kind: "notice", Action: "review", Ref: l1Ref, Destination: to,
		CausationID: l0Ev.EventID,
	}, f.dest); err != nil {
		t.Fatalf("L1 publish with CausationID failed: %v", err)
	}
}
