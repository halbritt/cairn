package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestConflictInspectionPreservesOriginalPositionsAndResolution(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	left, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Body = "Use a different compiler command for this fixture"
	right, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	dispute, err := s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{left.RecordID, right.RecordID}, "These fixture notes give conflicting compiler advice"})
	if err != nil {
		t.Fatal(err)
	}
	third, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	other, err := s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{right.RecordID, third.RecordID}, "A second independently reviewable fixture conflict"})
	if err != nil {
		t.Fatal(err)
	}
	draft = left.Draft
	draft.Body = "Revised advice after the conflict was opened"
	if _, err = s.Edit(ctx, EditRequest{uuid.NewString(), left.RecordID, left.Version, draft}); err != nil {
		t.Fatal(err)
	}
	page, err := s.Conflicts(ctx, ConflictsRequest{Repo: repo, RecordID: left.RecordID, Limit: 1})
	if err != nil || len(page.Conflicts) != 1 || page.More || page.Conflicts[0].ID != dispute.ID {
		t.Fatalf("find blocking conflict: %+v %v", page, err)
	}
	detail, err := s.Conflict(ctx, dispute.ID)
	if err != nil || detail.Resolved || detail.Resolution != nil || len(detail.Members) != 2 || detail.MemberCount != 2 {
		t.Fatalf("inspect: %+v %v", detail, err)
	}
	foundLeft := false
	for _, member := range detail.Members {
		if member.RecordID == left.RecordID {
			foundLeft = true
		}
		if member.RecordID == left.RecordID && (member.Version != 1 || member.CurrentVersion != 2 || member.Body == nil || *member.Body != left.Body || !member.PayloadAvailable || member.ObservedWriter != s.channel.Principal) {
			t.Fatalf("original position rewritten: %+v", member)
		}
	}
	if !foundLeft {
		t.Fatal("original member omitted")
	}
	outsider := testStore(t, Channel{Principal: "conflict-outsider", Repo: uuid.NewString()})
	_, err = outsider.Conflict(ctx, dispute.ID)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = outsider.Conflicts(ctx, ConflictsRequest{Repo: repo, Limit: 100})
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = s.Conflict(ctx, uuid.NewString())
	requireCode(t, err, "NOT_FOUND")
	_, err = s.Conflicts(ctx, ConflictsRequest{Repo: repo, Limit: 201})
	requireCode(t, err, "INVALID_REQUEST")
	if _, err = s.Resolve(ctx, ResolveRequest{uuid.NewString(), dispute.ID, 1, root.ID, "Resolve fixture disagreement while retaining both original positions"}); err != nil {
		t.Fatal(err)
	}
	page, err = s.Conflicts(ctx, ConflictsRequest{Repo: repo, Limit: 100})
	if err != nil || len(page.Conflicts) != 1 || page.Conflicts[0].ID != other.ID {
		t.Fatalf("resolved conflict still open: %+v %v", page, err)
	}
	page, err = s.Conflicts(ctx, ConflictsRequest{Repo: repo, Limit: 1, IncludeResolved: true})
	if err != nil || len(page.Conflicts) != 1 || !page.More || page.NextOffset != 1 || page.Conflicts[0].ID != dispute.ID {
		t.Fatalf("resolved history missing: %+v %v", page, err)
	}
	page, err = s.Conflicts(ctx, ConflictsRequest{Repo: repo, Limit: 1, Offset: page.NextOffset, IncludeResolved: true})
	if err != nil || len(page.Conflicts) != 1 || page.More || page.NextOffset != 2 || page.Conflicts[0].ID != other.ID {
		t.Fatalf("next conflict page: %+v %v", page, err)
	}
	detail, err = s.Conflict(ctx, dispute.ID)
	if err != nil || !detail.Resolved || detail.Version != 2 || detail.Resolution == nil || detail.Resolution.Actor != detail.OpenedBy || detail.Resolution.EventID == "" || len(detail.Members) != 2 {
		t.Fatalf("resolution erased involvement: %+v %v", detail, err)
	}
	preview, err := s.PreviewDeletion(ctx, left.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Forget(ctx, ForgetRequest{uuid.NewString(), left.RecordID, 2, root.ID, preview.PreviewID}); err != nil {
		t.Fatal(err)
	}
	detail, err = s.Conflict(ctx, dispute.ID)
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	for _, member := range detail.Members {
		seen[member.RecordID] = true
		if member.RecordID == left.RecordID && (member.Body != nil || member.PayloadAvailable || member.CurrentLifecycle != "tombstoned") {
			t.Fatalf("forgotten conflict payload disclosed: %+v", member)
		}
		if member.RecordID == right.RecordID && (member.Body == nil || *member.Body != right.Body || !member.PayloadAvailable) {
			t.Fatalf("surviving position lost: %+v", member)
		}
	}
	if len(seen) != 2 || !seen[left.RecordID] || !seen[right.RecordID] {
		t.Fatal("forgetting changed conflict membership")
	}
}
