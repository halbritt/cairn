package wakeup

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestGroupedWakeSuppliesSlotOwnedReplyContract(t *testing.T) {
	deadline := time.Now().Add(time.Hour)
	w := core.WakeAttempt{ID: uuid.NewString(), Delivery: core.AgentDelivery{DeliveryID: uuid.NewString(), LeaseID: uuid.NewString(), Event: core.AgentEvent{EventID: uuid.NewString(), From: "agent/requester", CorrelationID: uuid.NewString(), ResponseGroup: &core.ResponseGroupSpec{Deadline: deadline, PartialPolicy: "all"}}}}
	c := Config{Name: "worker-01", Principal: "agent/worker-01", Repo: "collection", Directory: t.TempDir(), StateDirectory: t.TempDir(), Socket: "/fixture/api.sock", AgentToken: "/fixture/slot.token"}
	context, path, err := writeContext(c, w, "/fixture/cairn", deadline)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var saved WakeContext
	if err = json.Unmarshal(raw, &saved); err != nil {
		t.Fatal(err)
	}
	if len(saved.Response) == 0 || saved.ResponseGroup == nil || !saved.ResponseGroup.Deadline.Equal(deadline) {
		t.Fatalf("missing reply contract: %s", raw)
	}
	args := strings.Join(saved.Response, " ")
	for _, want := range []string{"--token-file /fixture/slot.token", "--to agent/requester", "--causation-id " + w.Delivery.Event.EventID, "--correlation-id " + w.Delivery.Event.CorrelationID, "--kind response"} {
		if !strings.Contains(args, want) {
			t.Fatalf("reply missing %s: %s", want, args)
		}
	}
	if strings.Contains(args, "--agent-id") || strings.Contains(args, "--execution-id") {
		t.Fatalf("reply changed slot ownership: %s", args)
	}
	if strings.Join(context.Response, " ") != args {
		t.Fatal("saved retry identity differs")
	}
	stat, err := os.Stat(path)
	if err != nil || stat.Mode().Perm() != 0600 {
		t.Fatalf("context permissions: %v %v", stat, err)
	}
}
