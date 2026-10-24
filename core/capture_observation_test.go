package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestCaptureObservationRetainsHistoryAndUnknowns(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "capture-observer", Repo: "capture-observation-test"})
	zero := int64(0)
	input := int64(1500)
	base := CaptureObservationRequest{
		RequestID: uuid.NewString(), Repo: "capture-observation-test", Harness: "claude",
		HookEvent: "PreCompact", Method: "cairn-lifecycle/capture-meter/1",
		SessionSHA256: strings.Repeat("a", 64), SnapshotSHA256: strings.Repeat("b", 64),
		Outcome: "nothing_selected", SelectorOutcome: "completed", DurationMS: &zero,
		InputTokens: &input,
	}
	first, err := s.RecordCaptureObservation(ctx, base)
	if err != nil {
		t.Fatal(err)
	}
	replay, err := s.RecordCaptureObservation(ctx, base)
	if err != nil || replay != first {
		t.Fatalf("retry = %+v, %v", replay, err)
	}
	changed := base
	changed.Outcome = "saved"
	if _, err := s.RecordCaptureObservation(ctx, changed); err == nil {
		t.Fatal("changed intent accepted")
	}
	skip := base
	skip.RequestID = uuid.NewString()
	skip.SnapshotSHA256 = ""
	skip.Outcome = "unchanged"
	skip.SelectorOutcome = ""
	skip.DurationMS = nil
	skip.InputTokens = nil
	if _, err := s.RecordCaptureObservation(ctx, skip); err != nil {
		t.Fatal(err)
	}
	report, err := s.CaptureObservations(ctx, CaptureObservationsRequest{Repo: base.Repo, SessionSHA256: base.SessionSHA256, Limit: 1})
	if err != nil {
		t.Fatal(err)
	}
	if len(report.Rows) != 1 || !report.More || report.NextOffset != 1 || report.Rows[0].InputTokens == nil || *report.Rows[0].InputTokens != input || report.Rows[0].OutputTokens != nil {
		t.Fatalf("first page: %+v", report)
	}
	last, err := s.CaptureObservations(ctx, CaptureObservationsRequest{Repo: base.Repo, SessionSHA256: base.SessionSHA256, Limit: 1, Offset: report.NextOffset})
	if err != nil {
		t.Fatal(err)
	}
	if len(last.Rows) != 1 || last.More || last.Rows[0].Outcome != "unchanged" || last.Rows[0].InputTokens != nil || last.Rows[0].DurationMS != nil {
		t.Fatalf("second page: %+v", last)
	}
	encoded, err := json.Marshal(last)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "checkpoint") {
		t.Fatal("source leaked into report")
	}
}

func TestCaptureObservationRejectsInvalidAndCrossRepoClaims(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "capture-observer", Repo: "capture-observation-test"})
	req := CaptureObservationRequest{RequestID: uuid.NewString(), Repo: "capture-observation-test", Harness: "codex",
		HookEvent: "Stop", Method: "capture-meter/1", SessionSHA256: strings.Repeat("a", 64), Outcome: "saved"}
	for name, modify := range map[string]func(*CaptureObservationRequest){
		"method": func(r *CaptureObservationRequest) { r.Method = "raw private prompt" },
		"hash":   func(r *CaptureObservationRequest) { r.SessionSHA256 = "session-secret" },
		"model": func(r *CaptureObservationRequest) {
			r.SelectorOutcome = "completed"
			r.ReportedModel = "model with space"
		},
		"tokens": func(r *CaptureObservationRequest) {
			r.SelectorOutcome = "completed"
			n := int64(-1)
			r.InputTokens = &n
		},
		"unknown outcome": func(r *CaptureObservationRequest) { r.Outcome = "success" },
		"ungated usage":   func(r *CaptureObservationRequest) { n := int64(1); r.OutputTokens = &n },
	} {
		t.Run(name, func(t *testing.T) {
			bad := req
			bad.RequestID = uuid.NewString()
			modify(&bad)
			if _, err := s.RecordCaptureObservation(ctx, bad); err == nil {
				t.Fatal("invalid metric accepted")
			}
		})
	}
	req.Repo = "another-repo"
	if _, err := s.RecordCaptureObservation(ctx, req); err == nil {
		t.Fatal("cross-repo write accepted")
	}
	if _, err := s.CaptureObservations(ctx, CaptureObservationsRequest{Repo: req.Repo, Limit: 1}); err == nil {
		t.Fatal("cross-repo read accepted")
	}
}
