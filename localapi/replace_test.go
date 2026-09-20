package localapi_test

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestHostedReplaceCannotProbeLocalNote(t *testing.T) {
	client, admin, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	draft := core.Draft{Kind: "note", Body: "private passage", ClaimType: "self", Sensitivity: "local", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}
	note, err := admin.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	for _, probe := range []struct {
		text    string
		version int
	}{{"private", 1}, {"absent", 1}, {"private", 2}} {
		req := map[string]any{"request_id": uuid.NewString(), "record_id": note.RecordID, "expected_version": probe.version, "repo": repo, "old_text": probe.text, "new_text": "changed"}
		var result core.Revision
		if err := client.Call(ctx, "replace", req, &result); core.Code(err) != "NOT_FOUND" {
			t.Errorf("local note probe must be hidden regardless of match/version: %v", err)
		}
	}
	updated, err := admin.Get(ctx, note.RecordID)
	if err != nil || updated.Body != draft.Body || updated.Version != 1 {
		t.Fatalf("hosted replacement modified local note: %+v %v", updated, err)
	}
}

func TestLocalReplaceCanEditLocalNote(t *testing.T) {
	client, admin, repo, _ := authenticatedHost(t, "agent", "local", nil)
	ctx := context.Background()
	note, err := admin.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "local passage", ClaimType: "self", Sensitivity: "local", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}})
	if err != nil {
		t.Fatal(err)
	}
	req := map[string]any{"request_id": uuid.NewString(), "record_id": note.RecordID, "expected_version": 1, "repo": repo, "old_text": "local", "new_text": "corrected"}
	var result core.Revision
	if err := client.Call(ctx, "replace", req, &result); err != nil {
		t.Fatal(err)
	}
	updated, err := admin.Get(ctx, note.RecordID)
	if err != nil || updated.Body != "corrected passage" || result.Version != 2 {
		t.Fatalf("local replacement: %+v %+v %v", updated, result, err)
	}
}

func TestAuthenticatedReplacePreservesOtherPassages(t *testing.T) {
	client, admin, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	draft := core.Draft{Kind: "procedure", Body: "Keep this instruction.\r\nOld command.\n日本語 and trailing spaces  ", ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}
	note, err := admin.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	req := map[string]any{"request_id": uuid.NewString(), "record_id": note.RecordID, "expected_version": 1, "repo": repo, "old_text": "Old command.", "new_text": "Corrected command."}
	var result core.Revision
	if err = client.Call(ctx, "replace", req, &result); err != nil {
		t.Fatalf("agent cannot correct a passage without rewriting unrelated instructions: %v", err)
	}
	updated, err := admin.Get(ctx, note.RecordID)
	if err != nil || updated.Body != "Keep this instruction.\r\nCorrected command.\n日本語 and trailing spaces  " || result != (core.Revision{RecordID: note.RecordID, Version: 2}) {
		t.Fatalf("replacement changed other text: %+v %+v %v", updated, result, err)
	}
	var retry core.Revision
	if err = client.Call(ctx, "replace", req, &retry); err != nil || retry != result {
		t.Fatalf("exact retry: %+v %v", retry, err)
	}
}
