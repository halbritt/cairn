package localapi_test

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestSessionQuiescenceTypedTransportRetainsStoppedIdentity(t *testing.T) {
	ctx := context.Background()
	client, _, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	registration := core.RegisterAgentRequest{RequestID: uuid.NewString(), Binding: "quiescence",
		NativeSessionID: uuid.NewString(), Metadata: core.AgentMetadata{Harness: "claude", Project: "fixture",
			Workspace: "/fixture", State: "idle", DeliveryMode: "existing-session"}}
	var a core.AgentInstance
	if err := client.Call(ctx, "agent-register", registration, &a); err != nil {
		t.Fatal(err)
	}
	ref := core.AgentSessionRef{AgentID: a.AgentID, ExecutionID: a.ExecutionID}
	before, err := client.SessionQuiescence(ctx, core.SessionQuiescenceQuery{Session: ref})
	if err != nil || !before.Complete || before.Stopped || before.Repo != repo || before.Binding != registration.Binding || before.NativeSessionID != registration.NativeSessionID {
		t.Fatalf("live observation lost identity: %+v %v", before, err)
	}
	var left any
	if err := client.Call(ctx, "agent-leave", ref, &left); err != nil {
		t.Fatal(err)
	}
	after, err := client.SessionQuiescence(ctx, core.SessionQuiescenceQuery{Session: ref})
	if err != nil || !after.Complete || !after.Stopped || after.Session != ref || after.Generation != a.DatabaseGeneration || after.FactsSHA256 == before.FactsSHA256 {
		t.Fatalf("stopped observation lost correspondence: %+v %v", after, err)
	}
	if _, err := client.SessionQuiescence(ctx, core.SessionQuiescenceQuery{Session: core.AgentSessionRef{AgentID: a.AgentID, ExecutionID: uuid.NewString()}}); err == nil {
		t.Fatal("transport accepted stale execution")
	}
}
