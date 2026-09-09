package localapi_test

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestAuthenticatedNoteWritesReachDecodedBodyLimit(t *testing.T) {
	client, _, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	create := core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: strings.Repeat("<", 65536), ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}}
	var note core.Record
	if err := client.Call(ctx, "create", create, &note); err != nil {
		t.Fatalf("valid ordinary body cannot cross capture transport: %v", err)
	}
	if note.Body != create.Draft.Body {
		t.Fatal("capture changed source bytes")
	}
	var retry core.Record
	if err := client.Call(ctx, "create", create, &retry); err != nil || retry.RecordID != note.RecordID {
		t.Fatalf("capture retry changed identity: %+v %v", retry, err)
	}
	draft := create.Draft
	draft.Body = strings.Repeat("\x01", 65536)
	edit := core.EditRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 1, Draft: draft}
	if err := client.Call(ctx, "edit", edit, &note); err != nil || note.Body != draft.Body || note.Version != 2 {
		t.Fatalf("full draft edit lost escaped source: %+v %v", note, err)
	}
	revise := core.ReviseRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 2, Repo: repo, Body: strings.Repeat("&", 65536)}
	var revision core.Revision
	if err := client.Call(ctx, "revise", revise, &revision); err != nil || revision.Version != 3 {
		t.Fatalf("body-only edit cannot cross transport: %+v %v", revision, err)
	}
	if err := client.Call(ctx, "get", map[string]string{"record_id": note.RecordID}, &note); err != nil || note.Body != revise.Body || note.Sensitivity != "shareable" {
		t.Fatalf("body-only edit changed source or metadata: %v", err)
	}
	if err := client.Call(ctx, "edit", edit, &retry); err != nil || retry.Version != 2 || retry.Body != draft.Body {
		t.Fatalf("full edit retry changed after later revision: %v", err)
	}
	if err := client.Call(ctx, "revise", revise, &revision); err != nil || revision.Version != 3 {
		t.Fatalf("body-only retry changed: %v", err)
	}
	// Enough encoding room must not widen the decoded source contract.
	create.RequestID, create.Draft.Body = uuid.NewString(), strings.Repeat("x", 65537)
	if err := client.Call(ctx, "create", create, &note); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("oversized decoded note accepted: %v", err)
	}
	create.Draft.Body = "valid intent after rejected decoded size"
	if err := client.Call(ctx, "create", create, &note); err != nil {
		t.Fatalf("decoded refusal reserved capture identity: %v", err)
	}
	edit.RequestID, edit.RecordID, edit.ExpectedVersion, edit.Draft.Body = uuid.NewString(), note.RecordID, 1, strings.Repeat("x", 65537)
	if err := client.Call(ctx, "edit", edit, &note); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("oversized decoded edit accepted: %v", err)
	}
	edit.Draft.Body = "valid edit after rejected decoded size"
	if err := client.Call(ctx, "edit", edit, &note); err != nil || note.Version != 2 {
		t.Fatalf("decoded refusal reserved edit identity: %v", err)
	}
	revise.RequestID, revise.RecordID, revise.ExpectedVersion, revise.Body = uuid.NewString(), note.RecordID, 2, strings.Repeat("x", 65537)
	if err := client.Call(ctx, "revise", revise, &revision); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("oversized decoded revision accepted: %v", err)
	}
	revise.Body = "valid revision after rejected decoded size"
	if err := client.Call(ctx, "revise", revise, &revision); err != nil || revision.Version != 3 {
		t.Fatalf("decoded refusal reserved revision identity: %v", err)
	}
}
