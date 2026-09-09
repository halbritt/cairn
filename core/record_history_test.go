package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestRecordHistoryPagesThroughAppendAndReadsExactVersion(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "history-writer"})
	draft := projectNote(uuid.NewString())
	draft.Body = "first\n雪"
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	for version := 1; version < 4; version++ {
		if _, err = s.Revise(ctx, ReviseRequest{uuid.NewString(), r.RecordID, version, r.Scope.Repo, strings.Repeat("current ", version)}); err != nil {
			t.Fatal(err)
		}
	}
	dest := Destination{"local", true}
	first, err := s.History(ctx, RecordHistoryRequest{RecordID: r.RecordID, Limit: 2}, dest)
	if err != nil || len(first.Versions) != 2 || first.CurrentVersion != 4 || first.Versions[0].Version != 4 || first.Versions[1].Version != 3 || !first.More || first.NextBeforeVersion == nil || *first.NextBeforeVersion != 3 {
		t.Fatalf("first page: %+v %v", first, err)
	}
	if _, err = s.Revise(ctx, ReviseRequest{uuid.NewString(), r.RecordID, 4, r.Scope.Repo, "newly appended guidance"}); err != nil {
		t.Fatal(err)
	}
	next, err := s.History(ctx, RecordHistoryRequest{RecordID: r.RecordID, Limit: 2, BeforeVersion: *first.NextBeforeVersion}, dest)
	if err != nil || len(next.Versions) != 2 || next.CurrentVersion != 5 || next.Versions[0].Version != 2 || next.Versions[1].Version != 1 || next.More || next.NextBeforeVersion != nil {
		t.Fatalf("append shifted older versions: %+v %v", next, err)
	}
	old, err := s.History(ctx, RecordHistoryRequest{RecordID: r.RecordID, Version: 1}, dest)
	if err != nil || len(old.Versions) != 1 {
		t.Fatalf("exact version: %+v %v", old, err)
	}
	v := old.Versions[0]
	sum := sha256.Sum256([]byte(draft.Body))
	if !old.Historical || old.CurrentVersion != 5 || v.Body == nil || *v.Body != draft.Body || v.BodySHA256 != hex.EncodeToString(sum[:]) || v.BodyBytes != len(draft.Body) || v.Scope != draft.Scope || v.Class != "A" || v.ObservedWriter != r.ObservedWriter || v.Witness != r.Witness || !v.WrittenAt.Equal(r.WrittenAt) {
		t.Fatalf("historical source differs: %+v", old)
	}
	for _, page := range []RecordHistory{first, next} {
		for _, v := range page.Versions {
			if v.Body != nil || !v.PayloadAvailable {
				t.Fatalf("metadata mode returned body: %+v", v)
			}
		}
	}
	for _, bad := range []RecordHistoryRequest{{RecordID: r.RecordID, Version: -1}, {RecordID: r.RecordID, Version: 2147483648}, {RecordID: r.RecordID, BeforeVersion: -1}, {RecordID: r.RecordID, BeforeVersion: 2147483648}, {RecordID: r.RecordID, Limit: -1}, {RecordID: r.RecordID, Limit: 101}, {RecordID: r.RecordID, Version: 1, Limit: 1}, {RecordID: r.RecordID, Version: 1, BeforeVersion: 3}} {
		_, err = s.History(ctx, bad, dest)
		requireCode(t, err, "INVALID_REQUEST")
	}
	_, err = s.History(ctx, RecordHistoryRequest{RecordID: r.RecordID, Version: 6}, dest)
	requireCode(t, err, "NOT_FOUND")
	empty, err := s.History(ctx, RecordHistoryRequest{RecordID: r.RecordID, BeforeVersion: 1}, dest)
	if err != nil || len(empty.Versions) != 0 || empty.More {
		t.Fatalf("end page: %+v %v", empty, err)
	}
	_, err = s.History(ctx, RecordHistoryRequest{RecordID: r.RecordID}, Destination{"hosted", true})
	requireCode(t, err, "INVALID_REQUEST")
	// Read-only history does not acquire retained-use or mutation references.
	if _, err = s.Delete(ctx, DeleteRequest{uuid.NewString(), r.RecordID, 5}); err != nil {
		t.Fatalf("inspection prevented ordinary deletion: %v", err)
	}
	_, err = s.History(ctx, RecordHistoryRequest{RecordID: r.RecordID}, dest)
	requireCode(t, err, "NOT_FOUND")
}

func TestRecordHistoryKeepsVersionClassAndForgettingExclusion(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	writer := testStore(t, Channel{Principal: "history-independent-writer"})
	repo := uuid.NewString()
	a, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	evidence := testEvidence(t, op, repo)
	b, err := op.Promote(ctx, PromoteRequest{RequestID: uuid.NewString(), RecordID: a.RecordID, ExpectedVersion: 1, GrantID: root.ID, EvidenceIDs: []string{evidence.ID}, Reason: "Qualify the historical inspection fixture"})
	if err != nil {
		t.Fatal(err)
	}
	history, err := op.History(ctx, RecordHistoryRequest{RecordID: a.RecordID}, Destination{"local", true})
	if err != nil || history.CurrentClass != "B" || len(history.Versions) != 2 || history.Versions[0].Class != "B" || history.Versions[1].Class != "A" {
		t.Fatalf("current class relabelled history: %+v %v", history, err)
	}
	preview, err := op.PreviewDeletion(ctx, b.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	deletion, err := op.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: b.RecordID, ExpectedVersion: b.Version, GrantID: root.ID, PreviewID: preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	for _, version := range []int{0, 1, 2} {
		_, err = op.History(ctx, RecordHistoryRequest{RecordID: b.RecordID, Version: version}, Destination{"local", true})
		requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	}
	// Model an excluded earlier payload whose identity is still retained while
	// a later version remains available. Inspection must respect the row marker.
	current, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = writer.Revise(ctx, ReviseRequest{uuid.NewString(), current.RecordID, 1, repo, "later available text"}); err != nil {
		t.Fatal(err)
	}
	if _, err = op.pool.Exec(ctx, `UPDATE cairn.record_version SET payload_deleted_by=$2 WHERE record_id=$1 AND version=1`, current.RecordID, deletion.DeletionID); err != nil {
		t.Fatal(err)
	}
	page, err := op.History(ctx, RecordHistoryRequest{RecordID: current.RecordID}, Destination{"local", true})
	if err != nil || len(page.Versions) != 2 || page.Versions[1].PayloadAvailable || page.Versions[1].BodySHA256 != "" || page.Versions[1].BodyBytes != 0 || page.Versions[1].Body != nil {
		t.Fatalf("excluded source metadata leaked: %+v %v", page, err)
	}
	_, err = op.History(ctx, RecordHistoryRequest{RecordID: current.RecordID, Version: 1}, Destination{"local", true})
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	latest, err := op.History(ctx, RecordHistoryRequest{RecordID: current.RecordID, Version: 2}, Destination{"local", true})
	if err != nil || len(latest.Versions) != 1 || latest.Versions[0].Body == nil || *latest.Versions[0].Body != "later available text" {
		t.Fatalf("available version lost: %+v %v", latest, err)
	}

}
