package localapi_test

import (
	"context"
	"crypto/sha256"
	"fmt"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestAuthenticatedHistoryReadsBoundedEarlierPassage(t *testing.T) {
	client, admin, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	body := strings.Repeat("x", 65000) + "Earlier selected command."
	note, err := admin.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "procedure", Body: body, ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = admin.Revise(ctx, core.ReviseRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 1, Repo: repo, Body: "Current guidance"}); err != nil {
		t.Fatal(err)
	}
	var result struct {
		Historical     bool `json:"historical"`
		CurrentVersion int  `json:"current_version"`
		Versions       []struct {
			Body       *string        `json:"body"`
			BodySHA256 string         `json:"body_sha256"`
			Span       *core.ByteSpan `json:"span"`
		} `json:"versions"`
	}
	request := map[string]any{"record_id": note.RecordID, "version": 1, "span": map[string]int{"offset": 65000, "length": 1024}}
	if err = client.Call(ctx, "history", request, &result); err != nil {
		t.Fatalf("cannot read a bounded historical passage: %v", err)
	}
	if !result.Historical || result.CurrentVersion != 2 || len(result.Versions) != 1 {
		t.Fatalf("wrong historical identity: %+v", result)
	}
	v := result.Versions[0]
	if v.Body != nil || v.Span == nil || v.Span.Body != "Earlier selected command." || v.Span.TotalBytes != len(body) || v.Span.End != len(body) || v.BodySHA256 != fmt.Sprintf("%x", sha256.Sum256([]byte(body))) {
		t.Fatalf("wrong excerpt or full body escaped: %+v", v)
	}
}
