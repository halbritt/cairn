package core

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestConvertedProposalPinsLessonVersion(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "operator:proposal-version", Operator: true, Instrumented: true})
	repo := uuid.NewString()
	groupFailure(t, s, repo, "repair", "repair", "local", "model", strings.Repeat("b", 64))
	batch, err := s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo})
	if err != nil || len(batch.Proposals) != 1 {
		t.Fatalf("proposal: %+v %v", batch, err)
	}
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	p := batch.Proposals[0]
	request := ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: p.Version, Disposition: "converted", ResultRecord: note.RecordID, Reason: "Retain the selected lesson version"}
	converted, err := s.ReviewProposal(ctx, request)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(converted)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]any
	if err = json.Unmarshal(encoded, &fields); err != nil {
		t.Fatal(err)
	}
	if fields["result_version"] != float64(note.Version) {
		t.Fatalf("conversion omitted exact lesson version: %s", encoded)
	}
	_, err = s.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 1, Repo: repo, Body: "A later, different lesson."})
	if err != nil {
		t.Fatal(err)
	}
	current, err := s.Proposal(ctx, p.ID)
	if err != nil {
		t.Fatal(err)
	}
	after, _ := json.Marshal(current)
	if string(after) != string(encoded) {
		t.Fatalf("editing lesson changed proposal: %s", after)
	}
	retried, err := s.ReviewProposal(ctx, request)
	if err != nil {
		t.Fatal(err)
	}
	retry, _ := json.Marshal(retried)
	if string(retry) != string(encoded) {
		t.Fatal("review retry changed selected version")
	}
}

func TestProposalHistoryPreservesConversionAcrossReopening(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "operator:proposal-history", Operator: true, Instrumented: true})
	repo := uuid.NewString()
	groupFailure(t, s, repo, "repair", "repair", "local", "model", strings.Repeat("c", 64))
	batch, err := s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	p := batch.Proposals[0]
	converted, err := s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: 1, Disposition: "converted", ResultRecord: note.RecordID, ResultVersion: 1, Reason: "Selected original lesson"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: converted.Version, Disposition: "open", Reason: "Reconsider the failure lesson"})
	if err != nil {
		t.Fatal(err)
	}
	history, err := s.ProposalHistory(ctx, ProposalHistoryRequest{ProposalID: p.ID, Limit: 1})
	if err != nil {
		t.Fatal(err)
	}
	if history.Proposal.Disposition != "open" || history.Proposal.ResultRecord != "" || history.Proposal.ResultVersion != 0 || !history.More || len(history.Reviews) != 1 || history.Reviews[0].Disposition != "open" {
		t.Fatalf("current history: %+v", history)
	}
	earlier, err := s.ProposalHistory(ctx, ProposalHistoryRequest{ProposalID: p.ID, BeforeVersion: *history.NextBeforeVersion})
	if err != nil {
		t.Fatal(err)
	}
	if earlier.More || len(earlier.Reviews) != 1 {
		t.Fatalf("earlier history: %+v", earlier)
	}
	review := earlier.Reviews[0]
	if review.ResultRecord != note.RecordID || review.ResultVersion != 1 || review.Reason != "Selected original lesson" || review.Observer != s.channel.Principal || review.ObservedAt.IsZero() {
		t.Fatalf("lost selected conversion: %+v", review)
	}
	_, err = s.Delete(ctx, DeleteRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 1})
	requireCode(t, err, "FORGET_REQUIRED")
}

func TestProposalConversionChecksSelectedVersionAndAllowsForgetting(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	s := testStore(t, Channel{Principal: op.channel.Principal, Operator: true, Instrumented: true})
	repo := uuid.NewString()
	groupFailure(t, s, repo, "repair", "repair", "local", "model", strings.Repeat("d", 64))
	batch, err := s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	p := batch.Proposals[0]
	req := ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: p.ID, ExpectedVersion: 1, Disposition: "converted", ResultRecord: note.RecordID, ResultVersion: 1, Reason: "Pin the inspected lesson"}
	canary := "Selected-source-canary-" + uuid.NewString()
	_, err = s.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 1, Repo: repo, Body: canary})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.ReviewProposal(ctx, req)
	requireCode(t, err, "VERSION_CONFLICT")
	history, err := s.ProposalHistory(ctx, ProposalHistoryRequest{ProposalID: p.ID})
	if err != nil || len(history.Reviews) != 0 || history.Proposal.Version != 1 {
		t.Fatalf("failed review changed state: %+v %v", history, err)
	}
	req.ResultVersion = 2
	converted, err := s.ReviewProposal(ctx, req)
	if err != nil || converted.ResultVersion != 2 {
		t.Fatalf("corrected conversion: %+v %v", converted, err)
	}
	_, err = s.ProposalHistory(ctx, ProposalHistoryRequest{ProposalID: p.ID, Limit: 101})
	requireCode(t, err, "INVALID_REQUEST")
	restricted := testStore(t, Channel{Principal: s.channel.Principal, Repo: "other", Operator: true})
	_, err = restricted.ProposalHistory(ctx, ProposalHistoryRequest{ProposalID: p.ID})
	requireCode(t, err, "AUTHORITY_DENIED")
	preview, err := s.PreviewDeletion(ctx, note.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	forgotten, err := s.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 2, GrantID: root.ID, PreviewID: preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.PurgeDeletion(ctx, forgotten.DeletionID); err != nil {
		t.Fatal(err)
	}
	history, err = s.ProposalHistory(ctx, ProposalHistoryRequest{ProposalID: p.ID})
	if err != nil || len(history.Reviews) != 1 || history.Reviews[0].ResultVersion != 2 {
		t.Fatalf("forgetting removed review identity: %+v %v", history, err)
	}
	body, err := json.Marshal(history)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(body), canary) {
		t.Fatal("proposal history copied forgotten lesson bytes")
	}
	_, err = s.History(ctx, RecordHistoryRequest{RecordID: note.RecordID, Version: 2}, Destination{"local", true})
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
}
