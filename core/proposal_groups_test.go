package core

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func groupFailure(t *testing.T, s *Store, repo, task, taskClass, binding, capability, signature string) string {
	t.Helper()
	ctx := context.Background()
	p, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, task, uuid.NewString()}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.BindRun(ctx, RunBindingRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, TaskClass: taskClass, BindingID: binding, CapabilityID: capability, CommandSHA256: strings.Repeat("a", 64)})
	if err != nil {
		t.Fatal(err)
	}
	evidence := testEvidence(t, s, repo)
	_, err = s.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: p.ReceiptID, TaskOutcome: "rejected", FailureDomain: "task", FailureKind: "regression", ErrorSignature: signature, Method: "group-fixture/1", EvidenceIDs: []string{evidence.ID}, Reason: "Observe a synthetic repair failure"})
	if err != nil {
		t.Fatal(err)
	}
	return p.ReceiptID
}

func TestDocketGroupsMatchingFailuresAcrossTasks(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "operator:groups", Operator: true, Instrumented: true})
	repo := uuid.NewString()
	for _, task := range []string{"task-a", "task-b"} {
		groupFailure(t, s, repo, task, "repair", "host:local", "model:v1", strings.Repeat("b", 64))
	}
	batch, err := s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo})
	if err != nil || len(batch.Proposals) != 2 {
		t.Fatalf("source proposals: %+v %v", batch, err)
	}
	docket, err := s.Docket(ctx, repo)
	if err != nil || docket.Truncated || len(docket.Items) != 1 || docket.Items[0].Reason != "FAILURE_CLUSTER" {
		t.Fatalf("matching task failures should occupy one review row: %+v %v", docket, err)
	}
}

func TestProposalGroupMembersPreserveSources(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "observer:group-sources", Operator: true, Instrumented: true})
	repo := uuid.NewString()
	first := groupFailure(t, s, repo, "task-a", "repair", "host:local", "model:v1", strings.Repeat("b", 64))
	second := groupFailure(t, s, repo, "task-b", "repair", "host:local", "model:v1", strings.Repeat("b", 64))
	operator := testStore(t, Channel{Principal: s.channel.Principal, Operator: true})
	evidence := testEvidence(t, operator, repo)
	_, err := operator.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: second, ExpectedVersion: 1, TaskOutcome: "rejected", FailureDomain: "task", FailureKind: "regression", ErrorSignature: strings.Repeat("b", 64), Method: "manual-review/1", EvidenceIDs: []string{evidence.ID}, Reason: "Explicitly review the second failure as testimony"})
	if err != nil {
		t.Fatal(err)
	}
	batch, err := operator.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo})
	if err != nil || len(batch.Proposals) != 2 {
		t.Fatalf("generation: %+v %v", batch, err)
	}
	docket, err := operator.Docket(ctx, repo)
	if err != nil || len(docket.Items) != 1 {
		t.Fatalf("docket: %+v %v", docket, err)
	}
	key := docket.Items[0].ProposalGroupKey
	page, err := operator.ProposalGroup(ctx, ProposalGroupRequest{Repo: repo, Key: key, Limit: 1})
	if err != nil {
		t.Fatal(err)
	}
	if page.Summary.ProposalCount != 2 || page.Summary.TaskCount != 2 || page.Summary.InstrumentedCount != 1 || page.Summary.TestimonyCount != 1 || len(page.Members) != 1 || !page.More || page.NextOffset != 1 {
		t.Fatalf("group summary/page: %+v", page)
	}
	next, err := operator.ProposalGroup(ctx, ProposalGroupRequest{Repo: repo, Key: key, Limit: 1, Offset: page.NextOffset})
	if err != nil || len(next.Members) != 1 || next.More || next.NextOffset != 2 {
		t.Fatalf("second page: %+v %v", next, err)
	}
	members := map[string]ProposalGroupMember{page.Members[0].Proposal.FailureReceipt: page.Members[0], next.Members[0].Proposal.FailureReceipt: next.Members[0]}
	if len(members) != 2 {
		t.Fatalf("pagination repeated a source: %+v", members)
	}
	member := members[first]
	if member.Proposal.FailureReceipt != first || member.FailureScope.TaskID != "task-a" || member.FailureWitness != "instrumented" || len(member.Proposal.EvidenceIDs) != 1 {
		t.Fatalf("first source: %+v", member)
	}
	member = members[second]
	if member.Proposal.FailureReceipt != second || member.Proposal.FailureVersion != 2 || len(member.Proposal.EvidenceIDs) != 1 || member.Proposal.EvidenceIDs[0] != evidence.ID || member.FailureWitness != "testimony" || member.FailureObserver != s.channel.Principal {
		t.Fatalf("second source: %+v", member)
	}
	if member.Proposal.Disposition != "open" || member.Proposal.Version != 1 || !member.Proposal.SourceCurrent {
		t.Fatalf("inspection changed review state: %+v", member.Proposal)
	}
}

func TestProposalGroupTracksDueAndCorrectedSources(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "observer:group-current", Operator: true, Instrumented: true})
	repo := uuid.NewString()
	for _, task := range []string{"task-a", "task-b", "task-c"} {
		groupFailure(t, s, repo, task, "repair", "host:local", "model:v1", strings.Repeat("b", 64))
	}
	batch, err := s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo})
	if err != nil || len(batch.Proposals) != 3 {
		t.Fatalf("generation: %+v %v", batch, err)
	}
	docket, err := s.Docket(ctx, repo)
	if err != nil || len(docket.Items) != 1 || docket.Items[0].ProposalCount != 3 {
		t.Fatalf("initial docket: %+v %v", docket, err)
	}
	key := docket.Items[0].ProposalGroupKey
	until := time.Now().Add(time.Hour)
	_, err = s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: batch.Proposals[0].ID, ExpectedVersion: 1, Disposition: "deferred", Until: &until, Reason: "Defer this specific source"})
	if err != nil {
		t.Fatal(err)
	}
	group, err := s.ProposalGroup(ctx, ProposalGroupRequest{Repo: repo, Key: key, Limit: 10})
	if err != nil || group.Summary.ProposalCount != 2 || len(group.Members) != 2 {
		t.Fatalf("deferred member remains: %+v %v", group, err)
	}
	_, err = s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: batch.Proposals[1].ID, ExpectedVersion: 1, Disposition: "dismissed", Reason: "Dismiss a different source"})
	if err != nil {
		t.Fatal(err)
	}
	docket, err = s.Docket(ctx, repo)
	if err != nil || len(docket.Items) != 1 || docket.Items[0].ProposalID != batch.Proposals[2].ID || docket.Items[0].Reason != "TASK_FAILURE" {
		t.Fatalf("remaining singleton: %+v %v", docket, err)
	}
	_, err = s.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: batch.Proposals[2].FailureReceipt, ExpectedVersion: 1, TaskOutcome: "unknown", FailureDomain: "unknown", Method: "correction/1", Reason: "Withdraw the unverified rejection"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.ProposalGroup(ctx, ProposalGroupRequest{Repo: repo, Key: key, Limit: 10})
	requireCode(t, err, "NOT_FOUND")
	retained, err := s.Proposal(ctx, batch.Proposals[2].ID)
	if err != nil || retained.SourceCurrent || retained.Disposition != "open" {
		t.Fatalf("correction rewrote retained proposal: %+v %v", retained, err)
	}
	newReceipt := groupFailure(t, s, repo, "task-d", "repair", "host:local", "model:v1", strings.Repeat("b", 64))
	_, err = s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	group, err = s.ProposalGroup(ctx, ProposalGroupRequest{Repo: repo, Key: key, Limit: 10})
	if err != nil || group.Summary.ProposalCount != 1 || group.Members[0].Proposal.FailureReceipt != newReceipt || group.Members[0].Proposal.Disposition != "open" {
		t.Fatalf("new source inherited an earlier group member's disposition: %+v %v", group, err)
	}
}

func TestProposalGroupSeparatesComparisonTuples(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "observer:group-keys", Operator: true, Instrumented: true})
	repo := uuid.NewString()
	for _, key := range []struct{ taskClass, binding, capability, signature string }{
		{"repair", "host:a", "model:a", strings.Repeat("a", 64)},
		{"repair", "host:b", "model:a", strings.Repeat("a", 64)},
		{"repair", "host:a", "model:b", strings.Repeat("a", 64)},
		{"repair", "host:a", "model:a", strings.Repeat("b", 64)},
		{"analysis", "host:a", "model:a", strings.Repeat("a", 64)},
	} {
		for _, task := range []string{"task-a", "task-b"} {
			groupFailure(t, s, repo, task, key.taskClass, key.binding, key.capability, key.signature)
		}
	}
	_, err := s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	docket, err := s.Docket(ctx, repo)
	if err != nil || docket.Truncated || len(docket.Items) != 5 {
		t.Fatalf("different comparison tuples merged: %+v %v", docket, err)
	}
	keys := map[string]bool{}
	for _, item := range docket.Items {
		if item.Reason != "FAILURE_CLUSTER" || item.ProposalCount != 2 || item.FailureCount != 2 || item.TaskCount != 2 || keys[item.ProposalGroupKey] {
			t.Fatalf("invalid group: %+v", item)
		}
		keys[item.ProposalGroupKey] = true
	}
	scoped := testStore(t, Channel{Principal: "scoped:reader", Repo: "another-repo"})
	_, err = scoped.ProposalGroup(ctx, ProposalGroupRequest{Repo: repo, Key: docket.Items[0].ProposalGroupKey, Limit: 10})
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = s.ProposalGroup(ctx, ProposalGroupRequest{Repo: "another-repo", Key: docket.Items[0].ProposalGroupKey, Limit: 10})
	requireCode(t, err, "NOT_FOUND")
}

func TestProposalGroupKeepsRecoverySuppression(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "observer:group-recovery", Operator: true, Instrumented: true})
	repo := uuid.NewString()
	first := groupFailure(t, s, repo, "task-a", "repair", "host:local", "model:v1", strings.Repeat("b", 64))
	groupFailure(t, s, repo, "task-b", "repair", "host:local", "model:v1", strings.Repeat("b", 64))
	_, err := s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	docket, err := s.Docket(ctx, repo)
	if err != nil || len(docket.Items) != 1 {
		t.Fatalf("initial docket: %+v %v", docket, err)
	}
	key := docket.Items[0].ProposalGroupKey
	recovery := groupFailure(t, s, repo, "task-a", "repair", "host:local", "model:v1", strings.Repeat("b", 64))
	evidence := testEvidence(t, s, repo)
	_, err = s.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: recovery, ExpectedVersion: 1, TaskOutcome: "accepted", FailureDomain: "none", Method: "review/1", EvidenceIDs: []string{evidence.ID}, Reason: "Correct the later run to accepted"})
	if err != nil {
		t.Fatal(err)
	}
	batch, err := s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo})
	if err != nil || len(batch.Proposals) != 2 {
		t.Fatalf("recovery generation: %+v %v", batch, err)
	}
	group, err := s.ProposalGroup(ctx, ProposalGroupRequest{Repo: repo, Key: key, Limit: 10})
	if err != nil || group.Summary.ProposalCount != 2 || group.Summary.StandaloneCount != 1 || group.Summary.FailureCount != 2 {
		t.Fatalf("standalone counted with its pair: %+v %v", group, err)
	}
	var pair Proposal
	for _, member := range group.Members {
		if member.Proposal.FailureReceipt == first {
			pair = member.Proposal
			if member.RecoveryWitness != "instrumented" || member.RecoveryObserver != s.channel.Principal {
				t.Fatalf("recovery attribution missing: %+v", member)
			}
		}
	}
	if pair.Kind != "failure_recovery" || pair.RecoveryReceipt != recovery {
		t.Fatalf("wrong paired source: %+v", pair)
	}
	_, err = s.ReviewProposal(ctx, ReviewProposalRequest{RequestID: uuid.NewString(), ProposalID: pair.ID, ExpectedVersion: pair.Version, Disposition: "dismissed", Reason: "Dismiss the richer pair without reviving its predecessor"})
	if err != nil {
		t.Fatal(err)
	}
	group, err = s.ProposalGroup(ctx, ProposalGroupRequest{Repo: repo, Key: key, Limit: 10})
	if err != nil || group.Summary.ProposalCount != 1 || group.Members[0].Proposal.FailureReceipt == first {
		t.Fatalf("dismissal revived standalone: %+v %v", group, err)
	}
	_, err = s.AssessRun(ctx, AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: recovery, ExpectedVersion: 2, TaskOutcome: "unknown", FailureDomain: "unknown", Method: "correction/1", Reason: "Withdraw the premature accepted recovery"})
	if err != nil {
		t.Fatal(err)
	}
	group, err = s.ProposalGroup(ctx, ProposalGroupRequest{Repo: repo, Key: key, Limit: 10})
	if err != nil || group.Summary.ProposalCount != 2 || group.Summary.StandaloneCount != 2 {
		t.Fatalf("correction did not restore current standalone demand: %+v %v", group, err)
	}
}

func TestProposalGroupCountsBeforeDocketLimit(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "observer:group-volume", Operator: true, Instrumented: true})
	repo := uuid.NewString()
	for range 105 {
		groupFailure(t, s, repo, uuid.NewString(), "repair", "host:local", "model:v1", strings.Repeat("b", 64))
	}
	groupFailure(t, s, repo, "different-error", "repair", "host:local", "model:v1", strings.Repeat("c", 64))
	for offset := 0; ; {
		batch, err := s.GenerateProposals(ctx, GenerateProposalsRequest{RequestID: uuid.NewString(), Repo: repo, Offset: offset})
		if err != nil {
			t.Fatal(err)
		}
		if !batch.More {
			break
		}
		offset = batch.NextOffset
	}
	docket, err := s.Docket(ctx, repo)
	if err != nil || docket.Truncated || len(docket.Items) != 2 || docket.Items[0].ProposalCount != 105 || docket.Items[0].FailureCount != 105 || docket.Items[0].TaskCount != 105 {
		t.Fatalf("proposal row limit hid demand before grouping: %+v %v", docket, err)
	}
	key := docket.Items[0].ProposalGroupKey
	page, err := s.ProposalGroup(ctx, ProposalGroupRequest{Repo: repo, Key: key, Limit: 100})
	if err != nil || len(page.Members) != 100 || !page.More || page.Summary.ProposalCount != 105 {
		t.Fatalf("first source page: %+v %v", page, err)
	}
	next, err := s.ProposalGroup(ctx, ProposalGroupRequest{Repo: repo, Key: key, Limit: 100, Offset: page.NextOffset})
	if err != nil || len(next.Members) != 5 || next.More || next.NextOffset != 105 {
		t.Fatalf("remaining source page: %+v %v", next, err)
	}
	ids := map[string]bool{}
	for _, member := range append(page.Members, next.Members...) {
		if ids[member.Proposal.ID] {
			t.Fatalf("repeated source across pages: %s", member.Proposal.ID)
		}
		ids[member.Proposal.ID] = true
	}
	for _, req := range []ProposalGroupRequest{
		{Repo: repo, Key: key, Limit: 0},
		{Repo: repo, Key: key, Limit: 201},
		{Repo: repo, Key: key, Limit: 1, Offset: -1},
		{Repo: repo, Key: "invalid", Limit: 1},
	} {
		_, err := s.ProposalGroup(ctx, req)
		requireCode(t, err, "INVALID_REQUEST")
	}
}
