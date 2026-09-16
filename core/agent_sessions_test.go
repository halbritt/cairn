package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestAgentSessionIdentityFollowsConversationNotAccount(t *testing.T) {
	ctx := context.Background()
	profile := testStore(t, Channel{Principal: "session-owner:" + uuid.NewString(), Repo: "sessions:" + uuid.NewString()})
	dest := Destination{Name: "hosted"}
	req := RegisterAgentRequest{
		RequestID: uuid.NewString(), Binding: "codex-default", NativeSessionID: "thread-one",
		Metadata: AgentMetadata{Harness: "codex", Model: "model-one", Project: "rhumb", Workspace: "/fixture/rhumb", State: "busy", DeliveryMode: "existing-session"},
	}
	one, err := profile.RegisterAgent(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	again, err := profile.RegisterAgent(ctx, req, dest)
	if err != nil || again.AgentID != one.AgentID || again.ExecutionID != one.ExecutionID || again.Ordinal != one.Ordinal {
		t.Fatalf("registration retry changed identity: %+v %v", again, err)
	}
	other := req
	other.RequestID, other.NativeSessionID = uuid.NewString(), "thread-two"
	two, err := profile.RegisterAgent(ctx, other, dest)
	if err != nil || two.AgentID == one.AgentID || two.Inbox == one.Inbox || two.Ordinal == one.Ordinal {
		t.Fatalf("same-account conversations collapsed: %+v %v", two, err)
	}
	resume := req
	resume.RequestID, resume.Metadata.Model = uuid.NewString(), "model-two"
	resumed, err := profile.RegisterAgent(ctx, resume, dest)
	if err != nil || resumed.AgentID != one.AgentID || resumed.Ordinal != one.Ordinal || resumed.ExecutionID == one.ExecutionID || resumed.Metadata.Model != "model-two" {
		t.Fatalf("resume changed conversation identity or kept old execution: %+v %v", resumed, err)
	}
	otherAccount := req
	otherAccount.RequestID, otherAccount.Binding = uuid.NewString(), "codex-other"
	separate, err := profile.RegisterAgent(ctx, otherAccount, dest)
	if err != nil || separate.AgentID == one.AgentID {
		t.Fatalf("native session IDs were not namespaced by binding: %+v %v", separate, err)
	}
}

func TestAgentSessionPresenceFencesOldExecutionAndContext(t *testing.T) {
	ctx := context.Background()
	profile := testStore(t, Channel{Principal: "presence-owner:" + uuid.NewString(), Repo: "presence:" + uuid.NewString()})
	dest := Destination{Name: "hosted"}
	req := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "codex", NativeSessionID: "conversation",
		Metadata: AgentMetadata{Harness: "codex", Project: "rhumb", Workspace: "/fixture/rhumb", State: "busy", DeliveryMode: "existing-session"}}
	one, err := profile.RegisterAgent(ctx, req, Destination{Name: "hosted"})
	if err != nil {
		t.Fatal(err)
	}
	ref := AgentSessionRef{AgentID: one.AgentID, ExecutionID: one.ExecutionID}
	metadata := one.Metadata
	metadata.Project, metadata.Workspace = "cairn", "/fixture/cairn"
	update := UpdateAgentRequest{RequestID: uuid.NewString(), Session: ref, ExpectedRevision: one.ContextRevision, Metadata: metadata}
	changed, err := profile.UpdateAgent(ctx, update, dest)
	if err != nil || changed.ContextRevision != one.ContextRevision+1 || changed.Metadata.Project != "cairn" {
		t.Fatalf("context update: %+v %v", changed, err)
	}
	heartbeat, err := profile.HeartbeatAgent(ctx, ref, dest)
	if err != nil || heartbeat.ContextRevision != changed.ContextRevision || heartbeat.Metadata.Project != "cairn" {
		t.Fatalf("heartbeat overwrote context: %+v %v", heartbeat, err)
	}
	update.RequestID = uuid.NewString()
	_, err = profile.UpdateAgent(ctx, update, dest)
	requireCode(t, err, "VERSION_CONFLICT")
	req.RequestID = uuid.NewString()
	resumed, err := profile.RegisterAgent(ctx, req, Destination{Name: "hosted"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = profile.HeartbeatAgent(ctx, ref, dest)
	requireCode(t, err, "STALE_SESSION")
	_, err = profile.LeaveAgent(ctx, ref, dest)
	requireCode(t, err, "STALE_SESSION")
	ref.ExecutionID = resumed.ExecutionID
	left, err := profile.LeaveAgent(ctx, ref, dest)
	if err != nil || !left.Stopped {
		t.Fatalf("leave: %+v %v", left, err)
	}
	_, err = profile.HeartbeatAgent(ctx, ref, dest)
	requireCode(t, err, "STALE_SESSION")
}

func TestAgentDirectoryFiltersExpiryVisibilityAndRestore(t *testing.T) {
	ctx := context.Background()
	profile := testStore(t, Channel{Principal: "directory-owner:" + uuid.NewString(), Repo: "directory:" + uuid.NewString()})
	dest := Destination{Name: "hosted"}
	req := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "codex", NativeSessionID: "one",
		Metadata: AgentMetadata{Harness: "codex", Project: "Rhumb", Workspace: "/fixture/rhumb", State: "busy", DeliveryMode: "existing-session"}}
	one, err := profile.RegisterAgent(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	other := req
	other.RequestID, other.NativeSessionID = uuid.NewString(), "two"
	two, err := profile.RegisterAgent(ctx, other, dest)
	if err != nil {
		t.Fatal(err)
	}
	other.RequestID, other.NativeSessionID = uuid.NewString(), "private"
	if _, err = profile.RegisterAgent(ctx, other, Destination{Name: "local", AllowLocal: true}); err != nil {
		t.Fatal(err)
	}
	query := AgentDirectoryQuery{Harness: "codex", Project: "rhumb", Limit: 1}
	first, err := profile.AgentDirectory(ctx, query, dest)
	if err != nil || len(first.Agents) != 1 || !first.More || first.Agents[0].AgentID != one.AgentID {
		t.Fatalf("first page: %+v %v", first, err)
	}
	query.After = first.NextAfter
	second, err := profile.AgentDirectory(ctx, query, dest)
	if err != nil || len(second.Agents) != 1 || second.More || second.Agents[0].AgentID != two.AgentID {
		t.Fatalf("second page or private metadata leaked: %+v %v", second, err)
	}
	// Simulate a missed heartbeat without a 90-second sleep.
	if _, err = profile.pool.Exec(ctx, `UPDATE cairn.agent_session SET expires_at=clock_timestamp()-interval '1 second' WHERE agent_id=$1`, one.AgentID); err != nil {
		t.Fatal(err)
	}
	query.After, query.Limit = 0, 100
	fresh, err := profile.AgentDirectory(ctx, query, dest)
	if err != nil || len(fresh.Agents) != 1 || fresh.Agents[0].AgentID != two.AgentID {
		t.Fatalf("expired presence matched: %+v %v", fresh, err)
	}
	operator := testStore(t, Channel{Principal: "restore-operator", Operator: true})
	if _, err = operator.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "fence isolated agent presence test"}); err != nil {
		t.Fatal(err)
	}
	fenced, err := profile.AgentDirectory(ctx, query, dest)
	if err != nil || len(fenced.Agents) != 0 {
		t.Fatalf("restored old presence remained online: %+v %v", fenced, err)
	}
	_, err = profile.HeartbeatAgent(ctx, AgentSessionRef{AgentID: two.AgentID, ExecutionID: two.ExecutionID}, dest)
	requireCode(t, err, "STALE_SESSION")
	_, err = profile.RegisterAgent(ctx, req, dest)
	requireCode(t, err, "STALE_SESSION")
	req.RequestID = uuid.NewString()
	resumed, err := profile.RegisterAgent(ctx, req, dest)
	if err != nil || resumed.AgentID != one.AgentID || resumed.ExecutionID == one.ExecutionID {
		t.Fatalf("post-restore resume: %+v %v", resumed, err)
	}
}

func TestSessionInboxSelectionAndResumeFencing(t *testing.T) {
	ctx := context.Background()
	publisher, profile, source, dest := eventFixture(t)
	req := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "codex", NativeSessionID: "first",
		Metadata: AgentMetadata{Harness: "codex", Project: "rhumb", Workspace: "/fixture/rhumb", State: "busy", DeliveryMode: "existing-session"}}
	one, err := profile.RegisterAgent(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	other := req
	other.RequestID, other.NativeSessionID = uuid.NewString(), "second"
	two, err := profile.RegisterAgent(ctx, other, dest)
	if err != nil {
		t.Fatal(err)
	}
	event, err := publisher.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Ref: RecordVersionRef{source.RecordID, 1}, Kind: "request", Destination: EventDestination{Type: "agent", Name: one.Inbox}}, dest)
	if err != nil {
		t.Fatal(err)
	}
	first, err := profile.ForAgentSession(AgentSessionRef{AgentID: one.AgentID, ExecutionID: one.ExecutionID}, dest)
	if err != nil {
		t.Fatal(err)
	}
	second, err := profile.ForAgentSession(AgentSessionRef{AgentID: two.AgentID, ExecutionID: two.ExecutionID}, dest)
	if err != nil {
		t.Fatal(err)
	}
	wrong, err := second.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || wrong.Delivery != nil {
		t.Fatalf("another session consumed the message: %+v %v", wrong, err)
	}
	claim, err := first.NextEvent(ctx, NextEventRequest{}, dest)
	if err != nil || claim.Delivery == nil || claim.Delivery.Event.EventID != event.EventID {
		t.Fatalf("addressed session did not receive message: %+v %v", claim, err)
	}
	unassociated, err := publisher.ForAgentSession(AgentSessionRef{AgentID: one.AgentID, ExecutionID: one.ExecutionID}, dest)
	if err != nil {
		t.Fatal(err)
	}
	_, err = unassociated.NextEvent(ctx, NextEventRequest{}, dest)
	requireCode(t, err, "NOT_FOUND")
	req.RequestID = uuid.NewString()
	if _, err = profile.RegisterAgent(ctx, req, dest); err != nil {
		t.Fatal(err)
	}
	_, err = first.CompleteEvent(ctx, CompleteEventRequest{RequestID: uuid.NewString(), DeliveryID: claim.Delivery.DeliveryID, LeaseID: claim.Delivery.LeaseID, Disposition: "handled"}, dest)
	requireCode(t, err, "STALE_SESSION")
	// Closing a borrowed session view must not close the configured profile pool.
	first.Close()
	if _, err = profile.AgentEventStats(ctx, EventQuery{}, dest); err != nil {
		t.Fatal(err)
	}
}
