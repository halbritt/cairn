package localapi_test

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestAuthenticatedAppendPreservesExistingText(t *testing.T) {
	client, admin, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	draft := core.Draft{Kind: "procedure", Body: "Existing instruction.\r\n日本語 and trailing spaces  ", ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}
	note, err := admin.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	suffix := "\n\nAdditional source-checked guidance.\n"
	req := map[string]any{"request_id": uuid.NewString(), "record_id": note.RecordID, "expected_version": note.Version, "repo": repo, "body": suffix}
	var result core.Revision
	if err = client.Call(ctx, "append", req, &result); err != nil {
		t.Fatalf("agent cannot append without rewriting the old body: %v", err)
	}
	updated, err := admin.Get(ctx, note.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if updated.Body != draft.Body+suffix || updated.Version != note.Version+1 || result.RecordID != note.RecordID || result.Version != updated.Version {
		t.Fatalf("append changed original bytes or identity: %+v", updated)
	}
	var retry core.Revision
	if err = client.Call(ctx, "append", req, &retry); err != nil || retry != result {
		t.Fatalf("append retry: %+v %v", retry, err)
	}
	updated, err = admin.Get(ctx, note.RecordID)
	if err != nil || updated.Body != draft.Body+suffix || updated.Version != result.Version {
		t.Fatalf("retry duplicated suffix: %+v %v", updated, err)
	}
}
