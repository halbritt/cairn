package localapi_test

import (
	"context"
	"encoding/json"
	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"testing"
)

func TestAuthenticatedRecordHistoryReadsEarlierGuidance(t *testing.T) {
	client, admin, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	draft := core.Draft{Kind: "procedure", Body: "earlier selected guidance", ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}
	record, err := admin.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	_, err = admin.Revise(ctx, core.ReviseRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: 1, Repo: repo, Body: "current selected guidance"})
	if err != nil {
		t.Fatal(err)
	}
	var history struct {
		Historical     bool `json:"historical"`
		CurrentVersion int  `json:"current_version"`
		Versions       []struct {
			Version int     `json:"version"`
			Body    *string `json:"body"`
		} `json:"versions"`
	}
	if err = client.Call(ctx, "history", map[string]any{"record_id": record.RecordID}, &history); err != nil {
		t.Fatalf("reader cannot inspect retained note history: %v", err)
	}
	if !history.Historical || history.CurrentVersion != 2 || len(history.Versions) != 2 || history.Versions[0].Version != 2 || history.Versions[1].Version != 1 || history.Versions[0].Body != nil {
		t.Fatalf("history page: %+v", history)
	}
	var raw json.RawMessage
	if err = client.Call(ctx, "history", map[string]any{"record_id": record.RecordID, "version": 1}, &raw); err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, &history); err != nil {
		t.Fatal(err)
	}
	if len(history.Versions) != 1 || history.Versions[0].Body == nil || *history.Versions[0].Body != draft.Body {
		t.Fatalf("earlier body changed: %s", raw)
	}
}

func TestAuthenticatedRecordHistoryPreservesDestinationAndRepository(t *testing.T) {
	client, admin, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	for _, tc := range []struct{ name, repo, sensitivity, code string }{
		{"private", repo, "local", "NOT_FOUND"},
		{"foreign", uuid.NewString(), "shareable", "AUTHORITY_DENIED"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rec, err := admin.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "protected earlier content", ClaimType: "self", Sensitivity: tc.sensitivity, Scope: core.Scope{Repo: tc.repo, TaskID: "*", RunID: "*"}}})
			if err != nil {
				t.Fatal(err)
			}
			for _, request := range []core.RecordHistoryRequest{{RecordID: rec.RecordID}, {RecordID: rec.RecordID, Version: 1}, {RecordID: rec.RecordID, Version: 1, Span: &core.ByteSpanRequest{Offset: 0, Length: 5}}} {
				var result json.RawMessage
				err = client.Call(ctx, "history", request, &result)
				if core.Code(err) != tc.code || len(result) != 0 {
					t.Fatalf("history disclosed refused content: %s %v", result, err)
				}
			}
		})
	}
	var result json.RawMessage
	err := client.Call(ctx, "history", map[string]any{"record_id": uuid.NewString(), "destination": map[string]any{"name": "local", "allow_local": true}}, &result)
	if core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("caller supplied destination accepted: %v", err)
	}
}
