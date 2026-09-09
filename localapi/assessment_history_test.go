package localapi_test

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestAuthenticatedAssessmentHistoryPreservesNarrativeAndBoundaries(t *testing.T) {
	for _, role := range []string{"agent", "observer"} {
		t.Run(role, func(t *testing.T) {
			client, _, repo, _ := authenticatedHost(t, role, "hosted", nil)
			ctx := context.Background()
			p, err := client.Compile(ctx, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "review", RunID: "one"}, Purpose: "context", AvailableTokens: 32000}, core.Destination{Name: "hosted"})
			if err != nil {
				t.Fatal(err)
			}
			request := map[string]string{"receipt_id": p.ReceiptID}
			var history []core.Assessment
			if err = client.Call(ctx, "assessments", request, &history); err != nil || history == nil || len(history) != 0 {
				t.Fatalf("empty owned history: %+v %v", history, err)
			}
			var evidence core.Evidence
			if err = client.Call(ctx, "evidence", core.EvidenceRequest{RequestID: uuid.NewString(), Repo: repo, Body: "PRIVATE SUPPORT BODY", Source: "selected fixture", Sensitivity: "local"}, &evidence); err != nil {
				t.Fatal(err)
			}
			expected := []core.Assessment{}
			for i, reason := range []string{"Guidance suggested a useful check; task acceptance and net benefit remain unknown.", "Later review found prior context could explain the same check; preserve both judgments."} {
				var assessment core.Assessment
				req := core.AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, ExpectedVersion: i, TaskOutcome: "unknown", FailureDomain: "unknown", Method: "qualitative-review/1", Reason: reason, EvidenceIDs: []string{evidence.ID}}
				if err = client.Call(ctx, "assess-run", req, &assessment); err != nil {
					t.Fatal(err)
				}
				expected = append(expected, assessment)
			}
			var first json.RawMessage
			if err = client.Call(ctx, "assessments", request, &first); err != nil {
				t.Fatal(err)
			}
			want, err := json.Marshal(expected)
			if err != nil {
				t.Fatal(err)
			}
			var actualValue, expectedValue any
			if err = json.Unmarshal(first, &actualValue); err != nil {
				t.Fatal(err)
			}
			if err = json.Unmarshal(want, &expectedValue); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(actualValue, expectedValue) {
				t.Fatalf("history differs from exact write responses: %s", first)
			}
			var repeated json.RawMessage
			if err = client.Call(ctx, "assessments", request, &repeated); err != nil || string(repeated) != string(first) {
				t.Fatalf("read changed history: %s %v", repeated, err)
			}
			if expected[0].Observer != "host:test" || expected[0].Version != 1 || expected[1].Version != 2 {
				t.Fatal("assessment identity lost")
			}
			witness := "testimony"
			if role == "observer" {
				witness = "instrumented"
			}
			if expected[0].Witness != witness {
				t.Fatal("history widened witness")
			}
			// Receipts are created through the store to model a prior local binding or
			// another caller/repository; the authenticated reader must not disclose them.
			for _, other := range []struct{ principal, repo, destination string }{
				{"other-reader", repo, "hosted"},
				{"host:test", uuid.NewString(), "hosted"},
				{"host:test", repo, "local"},
			} {
				s, err := core.Open(ctx, os.Getenv("CAIRN_TEST_DATABASE_URL"), core.Channel{Principal: other.principal, Repo: other.repo})
				if err != nil {
					t.Fatal(err)
				}
				q, err := s.Compile(ctx, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: other.repo, TaskID: "review", RunID: "other"}, Purpose: "context", AvailableTokens: 32000}, core.Destination{Name: other.destination, AllowLocal: other.destination == "local"})
				if err == nil {
					_, err = s.AssessRun(ctx, core.AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: q.ReceiptID, TaskOutcome: "unknown", FailureDomain: "unknown", Method: "private-review/1", Reason: "PRIVATE REVIEW MUST NOT LEAVE ITS BINDING"})
				}
				s.Close()
				if err != nil {
					t.Fatal(err)
				}
				err = client.Call(ctx, "assessments", map[string]string{"receipt_id": q.ReceiptID}, &history)
				if core.Code(err) != "AUTHORITY_DENIED" {
					t.Fatalf("disclosed other binding %+v: %v", other, err)
				}
			}
			for id, code := range map[string]string{uuid.NewString(): "NOT_FOUND", "invalid": "INVALID_REQUEST"} {
				err = client.Call(ctx, "assessments", map[string]string{"receipt_id": id}, &history)
				if core.Code(err) != code {
					t.Fatalf("bad receipt %q: %v", id, err)
				}
			}
			err = client.Call(ctx, "assessments", map[string]string{"receipt_id": p.ReceiptID, "destination": "local"}, &history)
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("caller destination override accepted: %v", err)
			}
			var report core.RunReport
			err = client.Call(ctx, "run-report", core.RunReportRequest{Repo: repo, Limit: 10}, &report)
			if core.Code(err) != "AUTHORITY_DENIED" {
				t.Fatalf("protected report widened: %v", err)
			}
		})
	}
}
