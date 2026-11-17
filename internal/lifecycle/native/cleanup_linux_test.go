package native

import (
	"context"
	"errors"
	"os/exec"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/procpeer"
)

// A hung child is a cleanup fault, never a substitute for native qualification.
func TestNativeCleanupDeadlineReapsOwnedChildAndKeepsOwnerUnknown(t *testing.T) {
	o, _ := ownerFixture(t)
	childCtx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	command := exec.CommandContext(childCtx, "sleep", "30")
	input, err := command.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	output, err := command.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	ref, err := procpeer.ProcessRefOf(command.Process.Pid)
	if err != nil {
		t.Fatal(err)
	}
	child := &claudeClient{command: command, input: input, events: make(chan clientMessage, 16), ref: ref, ctx: childCtx, cancel: cancel}
	go child.read(output)
	o.controller, o.client, o.phase = true, child, "initialized"
	ctx, cancelWait := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancelWait()
	started := time.Now()
	err = o.CloseNative(ctx)
	if !errors.Is(err, context.DeadlineExceeded) || time.Since(started) > 2*time.Second || procpeer.Live(ref) || o.client != nil {
		t.Fatal("hung child was not bounded/reaped", err, o.phase)
	}
	if got := o.Inspect(context.Background()); got.Verdict != Unknown {
		t.Fatal("failed join became authority", got)
	}
	if _, err := o.Initialize(context.Background(), o.generation); err == nil {
		t.Fatal("uncertain owner admitted a new client")
	}
}
