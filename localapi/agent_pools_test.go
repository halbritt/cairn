package localapi_test

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestPoolTransportAndHealthRecovery(t *testing.T) {
	c, op, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	if _, err := op.ConfigureWorkerPool(ctx, core.WorkerPoolConfigRequest{RequestID: uuid.NewString(), Repo: repo, Name: "coding", Enabled: true, MaxPending: 4, MaxPendingPerPublisher: 2}); err != nil {
		t.Fatal(err)
	}
	var worker core.WorkerSlot
	if err := c.Call(ctx, "worker-register", core.WorkerRegisterRequest{RequestID: uuid.NewString(), Spec: core.WorkerSpec{Name: "worker", Harness: "codex", Workspace: "/fixture", Pools: []string{"coding"}, LaunchSpacingSeconds: 3}}, &worker); err != nil {
		t.Fatal(err)
	}
	var note core.Record
	if err := c.Call(ctx, "create", core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "pool transport fixture", Sensitivity: "shareable", ClaimType: "self", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}}, &note); err != nil {
		t.Fatal(err)
	}
	var event core.AgentEvent
	if err := c.Call(ctx, "event-publish", core.PublishEventRequest{RequestID: uuid.NewString(), Kind: "request", Ref: core.RecordVersionRef{RecordID: note.RecordID, Version: 1}, Destination: core.EventDestination{Type: "pool", Name: "coding"}, Pool: &core.PoolRequirements{Workspace: "/fixture"}}, &event); err != nil {
		t.Fatal(err)
	}
	var status core.EventStatus
	if err := c.Call(ctx, "event-inspect", core.EventStatusRequest{EventID: event.EventID}, &status); err != nil || status.Pool == nil || status.Pool.State != "queued" {
		t.Fatalf("queued API status: %+v %v", status, err)
	}
	if err := c.Call(ctx, "worker-health", core.WorkerHealthRequest{RequestID: uuid.NewString(), SupervisorID: worker.SupervisorID, ExpectedRevision: worker.Revision, Health: "unavailable", Reason: "explicit fixture quota observation"}, &worker); err != nil {
		t.Fatal(err)
	}
	if err := c.Call(ctx, "worker-heartbeat", core.WorkerHeartbeatRequest{SupervisorID: worker.SupervisorID}, &worker); err != nil || worker.Health != "unavailable" {
		t.Fatalf("heartbeat: %+v %v", worker, err)
	}
	var result core.WakeResult
	if err := c.Call(ctx, "wake-claim", core.WakeClaimRequest{RequestID: uuid.NewString(), WorkerID: worker.SupervisorID}, &result); err != nil || result.Attempt != nil {
		t.Fatalf("unavailable admission: %+v %v", result, err)
	}
	var workers core.WorkerList
	if err := c.Call(ctx, "worker-list", core.WorkerListRequest{}, &workers); err != nil || len(workers.Workers) != 1 {
		t.Fatalf("list: %+v %v", workers, err)
	}
	var pools core.WorkerPoolList
	if err := c.Call(ctx, "pool-list", core.WorkerListRequest{}, &pools); err != nil || len(pools.Pools) != 1 {
		t.Fatalf("pools: %+v %v", pools, err)
	}
	if err := c.Call(ctx, "worker-health", core.WorkerHealthRequest{RequestID: uuid.NewString(), SupervisorID: worker.SupervisorID, ExpectedRevision: worker.Revision, Health: "available", Reason: "explicit fixture recovery"}, &worker); err != nil {
		t.Fatal(err)
	}
	if err := c.Call(ctx, "wake-claim", core.WakeClaimRequest{RequestID: uuid.NewString(), WorkerID: worker.SupervisorID}, &result); err != nil || result.Attempt == nil || result.Attempt.Delivery.Event.EventID != event.EventID {
		t.Fatalf("recovered claim: %+v %v", result, err)
	}
}
