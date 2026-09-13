package localapi_test

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestAuthenticatedRecompilePreservesOriginalReadSet(t *testing.T) {
	client, admin, repo, _ := authenticatedHost(t, "observer", "hosted", nil)
	ctx := context.Background()
	draft := core.Draft{Kind: "lesson", Body: "original guidance", ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}
	note, err := admin.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	req := core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "repair", RunID: "original"}, Query: "guidance", Purpose: "context", AvailableTokens: 32000}
	original, err := client.Compile(ctx, req, core.Destination{Name: "hosted"})
	if err != nil {
		t.Fatal(err)
	}
	if len(original.Semantic.Selected) != 1 {
		t.Fatal("fixture did not select original guidance")
	}
	if _, err = admin.Revise(ctx, core.ReviseRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: note.Version, Repo: repo, Body: "replacement guidance"}); err != nil {
		t.Fatal(err)
	}
	draft.Body = "later guidance"
	if _, err = admin.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft}); err != nil {
		t.Fatal(err)
	}
	var result struct {
		Historical bool         `json:"historical"`
		Package    core.Package `json:"package"`
	}
	if err = client.Call(ctx, "recompile", core.RecompileRequest{ReceiptID: original.ReceiptID, Query: req.Query}, &result); err != nil {
		t.Fatalf("owner cannot reconstruct original receipt: %v", err)
	}
	if !result.Historical || !reflect.DeepEqual(result.Package, original) {
		t.Fatalf("historical package changed: %+v", result)
	}
	// A historical read must not make obsolete guidance eligible for execution.
	_, err = client.RunPackage(ctx, core.RunPackageRequest{ReceiptID: original.ReceiptID, Seal: original.Seal}, core.Destination{Name: "hosted"})
	if core.Code(err) != "STALE_PACKAGE" {
		t.Fatalf("historical inspection authorized stale execution: %v", err)
	}
}

func TestAuthenticatedRecompileKeepsReceiptOwnershipAndIntent(t *testing.T) {
	client, admin, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	req := core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "inspection", RunID: "original"}, Query: "original query", Purpose: "context", AvailableTokens: 32000}
	original, err := client.Compile(ctx, req, core.Destination{Name: "hosted"})
	if err != nil {
		t.Fatal(err)
	}
	var result json.RawMessage
	if err = client.Call(ctx, "recompile", core.RecompileRequest{ReceiptID: original.ReceiptID, Query: req.Query}, &result); err != nil {
		t.Fatal(err)
	}
	foreign, err := admin.Compile(ctx, req, core.Destination{Name: "hosted"})
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name    string
		request any
		code    string
	}{
		{"foreign owner", core.RecompileRequest{ReceiptID: foreign.ReceiptID, Query: req.Query}, "AUTHORITY_DENIED"},
		{"different query", core.RecompileRequest{ReceiptID: original.ReceiptID, Query: "new query"}, "INVALID_REQUEST"},
		{"caller supplied destination", map[string]any{"receipt_id": original.ReceiptID, "query": req.Query, "destination": core.Destination{Name: "local", AllowLocal: true}}, "INVALID_REQUEST"},
		{"caller supplied identity", map[string]any{"receipt_id": original.ReceiptID, "query": req.Query, "principal": "operator"}, "INVALID_REQUEST"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			result = nil
			err := client.Call(ctx, "recompile", tc.request, &result)
			if core.Code(err) != tc.code || len(result) != 0 {
				t.Fatalf("refusal disclosed historical data: %s %v", result, err)
			}
		})
	}
}
