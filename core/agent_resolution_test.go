package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestAgentResolutionPinsContextAndRetryRecipient(t *testing.T) {
	ctx := context.Background()
	sender, receiver, source, dest := eventFixture(t)
	query := AgentDirectoryQuery{Harness: "codex", Project: "rhumb"}
	empty, err := sender.ResolveAgent(ctx, query, dest)
	if err != nil || empty.State != "no-match" || empty.Resolution != nil {
		t.Fatalf("empty: %+v %v", empty, err)
	}
	req := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "codex-first", NativeSessionID: "one", Metadata: AgentMetadata{Harness: "codex", Project: "Rhumb", Workspace: "/work/rhumb", State: "busy", DeliveryMode: "existing-session"}}
	one, err := receiver.RegisterAgent(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	selected, err := sender.ResolveAgent(ctx, query, dest)
	if err != nil || selected.State != "unique" || selected.Resolution == nil || selected.Resolution.AgentID != one.AgentID {
		t.Fatalf("unique: %+v %v", selected, err)
	}
	publish := PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{Type: "agent", Name: one.Inbox}, Resolution: selected.Resolution}
	event, err := sender.PublishEvent(ctx, publish, dest)
	if err != nil {
		t.Fatal(err)
	}
	metadata := one.Metadata
	metadata.Project = "cairn"
	_, err = receiver.UpdateAgent(ctx, UpdateAgentRequest{RequestID: uuid.NewString(), Session: AgentSessionRef{one.AgentID, one.ExecutionID}, ExpectedRevision: one.ContextRevision, Metadata: metadata}, dest)
	if err != nil {
		t.Fatal(err)
	}
	retry, err := sender.PublishEvent(ctx, publish, dest)
	if err != nil || retry.EventID != event.EventID || retry.Destination.Name != one.Inbox {
		t.Fatalf("retry rerouted: %+v %v", retry, err)
	}
	publish.RequestID = uuid.NewString()
	_, err = sender.PublishEvent(ctx, publish, dest)
	requireCode(t, err, "STALE_RESOLUTION")
	stats, err := sender.AgentEventStats(ctx, EventQuery{}, dest)
	if err != nil || stats.Published != 1 {
		t.Fatalf("stale send wrote an event: %+v %v", stats, err)
	}
	// Direct addresses still deliberately support offline/context-independent mail.
	publish.Resolution = nil
	_, err = sender.PublishEvent(ctx, publish, dest)
	if err != nil {
		t.Fatal(err)
	}
}

func TestAgentResolutionAmbiguityAndOfflineCandidates(t *testing.T) {
	ctx := context.Background()
	sender, receiver, _, dest := eventFixture(t)
	req := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "codex", NativeSessionID: "one", Metadata: AgentMetadata{Harness: "codex", Project: "Rhumb", ProjectAliases: []string{"charts"}, Workspace: "/work/rhumb-one", TaskSummary: "Chart editor", State: "busy", DeliveryMode: "existing-session"}}
	one, err := receiver.RegisterAgent(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	req.RequestID, req.NativeSessionID, req.Metadata.Workspace = uuid.NewString(), "two", "/work/rhumb-two"
	two, err := receiver.RegisterAgent(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	result, err := sender.ResolveAgent(ctx, AgentDirectoryQuery{Harness: "CODEX", Project: "CHARTS"}, dest)
	if err != nil || result.State != "ambiguous" || result.Resolution != nil || len(result.Candidates) != 2 || result.Candidates[0].DisplayName == result.Candidates[1].DisplayName {
		t.Fatalf("ambiguity: %+v %v", result, err)
	}
	for _, a := range []AgentInstance{one, two} {
		if _, err = receiver.LeaveAgent(ctx, AgentSessionRef{a.AgentID, a.ExecutionID}, dest); err != nil {
			t.Fatal(err)
		}
	}
	result, err = sender.ResolveAgent(ctx, AgentDirectoryQuery{Harness: "codex", Project: "rhumb"}, dest)
	if err != nil || result.State != "stale" || result.Resolution != nil || len(result.Candidates) != 2 {
		t.Fatalf("offline: %+v %v", result, err)
	}
}

func TestAgentResolutionRejectsChangedPresenceAndPrivateMetadata(t *testing.T) {
	for _, change := range []string{"expiry", "resume", "restore", "visibility", "collection"} {
		t.Run(change, func(t *testing.T) {
			ctx := context.Background()
			sender, receiver, source, dest := eventFixture(t)
			req := RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "codex", NativeSessionID: "one", Metadata: AgentMetadata{Harness: "codex", Project: "rhumb", Workspace: "/work/rhumb", State: "idle", DeliveryMode: "existing-session"}}
			one, err := receiver.RegisterAgent(ctx, req, dest)
			if err != nil {
				t.Fatal(err)
			}
			selected, err := sender.ResolveAgent(ctx, AgentDirectoryQuery{AgentID: one.AgentID}, dest)
			if err != nil || selected.Resolution == nil {
				t.Fatalf("resolve: %+v %v", selected, err)
			}
			switch change {
			case "expiry":
				_, err = receiver.pool.Exec(ctx, `UPDATE cairn.agent_session SET expires_at=clock_timestamp()-interval '1 second' WHERE agent_id=$1`, one.AgentID)
			case "resume":
				req.RequestID = uuid.NewString()
				_, err = receiver.RegisterAgent(ctx, req, dest)
			case "restore":
				operator := testStore(t, Channel{Principal: "restore-resolution", Operator: true})
				_, err = operator.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "isolated resolution restore test"})
			case "visibility":
				// A private directory entry cannot be disclosed in a shareable event even
				// by a locally privileged sender. Use a current (not merely old) stamp.
				req.RequestID = uuid.NewString()
				one, err = receiver.RegisterAgent(ctx, req, Destination{Name: "local", AllowLocal: true})
				if err == nil {
					selected, err = sender.ResolveAgent(ctx, AgentDirectoryQuery{AgentID: one.AgentID}, Destination{Name: "local", AllowLocal: true})
				}
				if err == nil && selected.Resolution == nil {
					t.Fatal("local resolution missing")
				}
				dest = Destination{Name: "local", AllowLocal: true}
			case "collection":
				// The UUID belongs to a different collection, regardless of caller input.
				_, err = receiver.pool.Exec(ctx, `UPDATE cairn.agent_session SET repo=$2 WHERE agent_id=$1`, one.AgentID, uuid.NewString())
			}
			if err != nil {
				t.Fatal(err)
			}
			_, err = sender.PublishEvent(ctx, PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: RecordVersionRef{source.RecordID, source.Version}, Destination: EventDestination{"agent", one.Inbox}, Resolution: selected.Resolution}, dest)
			requireCode(t, err, "STALE_RESOLUTION")
			stats, err := sender.AgentEventStats(ctx, EventQuery{}, dest)
			if err != nil || stats.Published != 0 {
				t.Fatalf("refused send wrote an event: %+v %v", stats, err)
			}
		})
	}
}
