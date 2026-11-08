package procpeer

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestMeasuredIdentityAndSyntheticMismatches(t *testing.T) {
	ref, err := ProcessRefOf(os.Getpid())
	if err != nil || ref.PID != os.Getpid() || ref.Start == 0 || ref.Boot == "" || !Live(ref) {
		t.Fatalf("self measurement: %+v, %v", ref, err)
	}
	for _, changed := range []ProcessRef{
		{PID: ref.PID, Start: ref.Start + 1, Boot: ref.Boot},
		{PID: ref.PID, Start: ref.Start, Boot: ref.Boot + "-synthetic"},
		{PID: -1, Start: ref.Start, Boot: ref.Boot},
	} {
		if Live(changed) {
			t.Fatalf("synthetic identity mismatch accepted: %+v", changed)
		}
	}
	raw, err := json.Marshal(ref)
	if err != nil || !strings.HasPrefix(string(raw), `{"pid":`) || !strings.Contains(string(raw), `,"start":`) || !strings.Contains(string(raw), `,"boot":`) {
		t.Fatalf("wire shape changed: %s, %v", raw, err)
	}
}

func TestGoPolicyMeasuresOwnedZombieUntilReaped(t *testing.T) {
	// Deliberately defer Wait so the kernel exposes a real owned Z process.
	child := exec.Command("/bin/sh", "-c", "exit 0")
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	if child.Process.Pid <= 1 {
		t.Fatal("invalid owned child PID")
	}
	defer child.Wait()
	path := "/proc/" + strconv.Itoa(child.Process.Pid) + "/stat"
	deadline := time.Now().Add(3 * time.Second)
	for {
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		fields := strings.Fields(string(raw)[strings.LastIndexByte(string(raw), ')')+1:])
		if len(fields) > 0 && fields[0] == "Z" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("owned child did not expose zombie state")
		}
		time.Sleep(time.Millisecond)
	}
	ref, err := ProcessRefOf(child.Process.Pid)
	if err != nil || !Live(ref) {
		t.Fatalf("existing Go measurement policy changed for actual Z: %+v, %v", ref, err)
	}
	// The retained Python reader sees this very same real Z process and refuses
	// it. Keep that deliberate policy difference rather than forcing parity.
	source, err := filepath.Abs("../../integrations/lifecycle/unix_peer_transport.py")
	if err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(ref)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	observed, err := exec.CommandContext(ctx, "python3", "-I", "-c",
		`import json,runpy,sys; p=runpy.run_path(sys.argv[1]); print(json.dumps(p["process_alive"](json.loads(sys.argv[2]))))`, source, string(raw)).CombinedOutput()
	if err != nil || strings.TrimSpace(string(observed)) != "false" {
		t.Fatalf("Python policy for same actual Z: %q, %v", observed, err)
	}
	if err := child.Wait(); err != nil {
		t.Fatal(err)
	}
	if Live(ref) {
		t.Fatal("reaped child still measured as matching")
	}
}
