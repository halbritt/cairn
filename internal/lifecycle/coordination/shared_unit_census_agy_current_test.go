package coordination

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Every trace/process/descriptor below is a synthetic UNIT record. A successful
// private reader assertion is never a native qualification or FixtureReady claim.
type agyCurrentUnit struct {
	observed             WatchCensusAgyCurrentReadback
	generation           agyGenerationRecord
	before, after        []byte
	aSettings, bSettings []byte
	aCommands, bCommands []agyCensusCommand
	account, installed   string
}

func agyCurrentUnitCommands(name string) (WatchCensusAgyCurrentSelection, []agyCensusCommand) {
	s := WatchCensusAgyCurrentSelection{Config: WatchCensusPin{Path: "/unit-current/" + name + ".json"}, Launcher: WatchCensusPin{Path: "/unit-current/" + name + "/inbox"}}
	var commands []agyCensusCommand
	for _, event := range []string{"PreInvocation", "Stop"} {
		program := WatchCensusPin{Path: "/unit-current/" + name + "-control"}
		commands = append(commands, agyCensusCommand{event: event, launcher: s.Launcher, program: program, argv: []string{program.Path, "coordination", "--unit-record", name, "--", "hook", "--config", s.Config.Path, "--event", event}})
	}
	return s, commands
}

func agyUnitExec(buffer *bytes.Buffer, pid int, path string, argv []string) int {
	start := buffer.Len()
	var quoted []string
	for _, arg := range argv {
		quoted = append(quoted, strconv.Quote(arg))
	}
	fmt.Fprintf(buffer, "%d execve(%q, [%s], 0x0 /* UNIT record */) = 0\n", pid, path, strings.Join(quoted, ", "))
	return start
}

func agyUnitPhase(pid, child int, client string, selection WatchCensusAgyCurrentSelection, commands []agyCensusCommand, settings []byte, idle json.RawMessage) ([]byte, WatchCensusAgyCurrentPhase) {
	var raw bytes.Buffer
	agyUnitExec(&raw, pid, client, []string{client})
	fmt.Fprintf(&raw, "%d openat(AT_FDCWD, %q, O_RDONLY|O_CLOEXEC) = 8\n%d read(8, %q, 4096) = %d\n%d read(8, \"\", 4096) = 0\n%d close(8) = 0\n", pid, "/unit-current/account/hooks.json", pid, settings, len(settings), pid, pid)
	var refs []WatchCensusAgyCurrentCallback
	for index, command := range commands {
		callback := child + index
		fmt.Fprintf(&raw, "%d clone(child_stack=NULL, flags=SIGCHLD) = %d\n", pid, callback)
		outer := []string{selection.Launcher.Path, "hook", "--config", selection.Config.Path, "--event", command.event}
		exec := agyUnitExec(&raw, callback, selection.Launcher.Path, outer)
		agyUnitExec(&raw, callback, command.program.Path, command.argv)
		payload := map[string]any{"conversationId": "synthetic-current-unit-session", "workspacePaths": []string{"/unit-current/project"}, "hookEventName": command.event}
		if command.event == "Stop" && idle != nil {
			payload["fullyIdle"] = idle
		}
		body, _ := json.Marshal(payload)
		frame := raw.Len()
		fmt.Fprintf(&raw, "%d write(9, %q, %d) = %d\n%d read(0, %q, 4096) = %d\n%d read(0, \"\", 4096) = 0\n", pid, body, len(body), len(body), callback, body, len(body), callback)
		join := raw.Len()
		fmt.Fprintf(&raw, "%d +++ exited with 0 +++\n", callback)
		refs = append(refs, WatchCensusAgyCurrentCallback{Event: command.event, Exec: exec, Join: join, Frame: frame})
	}
	return raw.Bytes(), WatchCensusAgyCurrentPhase{Start: 0, End: raw.Len(), Callbacks: refs}
}

func newAgyCurrentUnit() agyCurrentUnit {
	a, ac := agyCurrentUnitCommands("A")
	b, bc := agyCurrentUnitCommands("B")
	client := WatchCensusPin{Path: "/unit-current/client"}
	capture := agyGenerationCapture{Process: WatchCensusProcess{PID: 200, Parent: 100, PGID: 100, Start: "111", Boot: "unit-boot"}, Client: client, ClientArgv: []string{client.Path}}
	af, bf := []byte("unit A settings"), []byte("unit B settings")
	before, beforePhase := agyUnitPhase(200, 240, client.Path, a, ac, af, json.RawMessage("true"))
	after, afterPhase := agyUnitPhase(200, 340, client.Path, b, bc, bf, json.RawMessage("true"))
	return agyCurrentUnit{observed: WatchCensusAgyCurrentReadback{Schema: "cairn.watch-agy-readback/2", Harness: "agy", EvidenceKind: "current-observation", NativeSession: "synthetic-current-unit-session", Workspace: "/unit-current/project", Client: client, ClientArgv: capture.ClientArgv, A: a, B: b, Before: beforePhase, After: afterPhase}, generation: agyGenerationRecord{Schema: "cairn.agy-collector-generation/1", Before: capture, After: capture}, before: before, after: after, aSettings: af, bSettings: bf, aCommands: ac, bCommands: bc, account: "/unit-current/account", installed: "/unit-current/account/hooks.json"}
}

func (f agyCurrentUnit) check() error {
	return agyCurrentPair(f.before, f.after, f.observed, f.generation, f.aSettings, f.bSettings, f.aCommands, f.bCommands, f.account, f.installed)
}

func TestAgyCurrentUnitRelations(t *testing.T) {
	for _, mutation := range []string{"none", "missing-generation", "pid-reuse", "boot-change", "foreign-session", "missing-B", "A-after-B", "extra-B", "truncated", "missing-stdin-eof", "wrong-config", "wrong-argv", "wrong-locator", "wrong-layer", "fullyIdle-false", "fullyIdle-string", "fullyIdle-missing"} {
		t.Run(mutation, func(t *testing.T) {
			f := newAgyCurrentUnit()
			switch mutation {
			case "missing-generation":
				f.generation.Before.Process = WatchCensusProcess{}
			case "pid-reuse":
				f.generation.Before.Process.Start = "222"
			case "boot-change":
				f.generation.Before.Process.Boot = "old-unit-boot"
			case "foreign-session":
				f.observed.NativeSession = "foreign-unit-session"
			case "missing-B":
				f.observed.B.Launcher.Path = "/unit-current/unobserved/inbox"
			case "A-after-B", "extra-B":
				var tail bytes.Buffer
				fmt.Fprintf(&tail, "200 clone(child_stack=NULL, flags=SIGCHLD) = 450\n")
				selection := f.observed.A
				if mutation == "extra-B" {
					selection = f.observed.B
				}
				agyUnitExec(&tail, 450, selection.Launcher.Path, []string{selection.Launcher.Path, "hook", "--config", selection.Config.Path, "--event", "Stop"})
				f.after = append(f.after, tail.Bytes()...)
				f.observed.After.End = len(f.after)
			case "truncated":
				f.after = f.after[:len(f.after)-1]
				f.observed.After.End = len(f.after)
			case "missing-stdin-eof":
				f.after = bytes.Replace(f.after, []byte("340 read(0, \"\", 4096) = 0\n"), []byte("340 ignored(0) = 0\n"), 1)
				f.observed.After.End = len(f.after)
			case "wrong-config":
				f.observed.B.Config.Path = "/unit-current/foreign.json"
			case "wrong-argv":
				f.generation.After.ClientArgv = []string{f.observed.Client.Path, "foreign-unit-argument"}
			case "wrong-locator":
				f.observed.After.Callbacks[0].Frame++
			case "wrong-layer":
				f.installed = "/unit-current/project/.agents/hooks.json"
			case "fullyIdle-false", "fullyIdle-string", "fullyIdle-missing":
				var idle json.RawMessage
				if mutation == "fullyIdle-false" {
					idle = json.RawMessage("false")
				}
				if mutation == "fullyIdle-string" {
					idle = json.RawMessage(`"true"`)
				}
				f.after, f.observed.After = agyUnitPhase(200, 340, f.observed.Client.Path, f.observed.B, f.bCommands, f.bSettings, idle)
			}
			err := f.check()
			if mutation == "none" && err != nil {
				t.Fatal(err)
			}
			if mutation != "none" && err == nil {
				t.Fatal("invalid synthetic UNIT observation accepted")
			}
		})
	}
}

func TestAgyCurrentUnitOrderlyNewPID(t *testing.T) {
	f := newAgyCurrentUnit()
	f.generation.After.Process.PID = 300
	f.generation.After.Process.Start = "222"
	f.after, f.observed.After = agyUnitPhase(300, 340, f.observed.Client.Path, f.observed.B, f.bCommands, f.bSettings, json.RawMessage("true"))
	if err := f.check(); err == nil {
		t.Fatal("new PID without exact old owner join accepted")
	}
	f.before = append(f.before, []byte("200 +++ exited with 0 +++\n100 wait4(200, [{WIFEXITED(s) && WEXITSTATUS(s) == 0}], 0, NULL) = 200\n")...)
	f.observed.Before.End = len(f.before)
	if err := f.check(); err != nil {
		t.Fatal(err)
	}
	f.generation.Before.Process.Parent = 101
	if err := f.check(); err == nil {
		t.Fatal("foreign old generation reaper accepted")
	}
}

func TestAgyCurrentUnitAllowsExactABeforeBoundary(t *testing.T) {
	f := newAgyCurrentUnit()
	line := bytes.IndexByte(f.after, '\n') + 1
	delta := len(f.before) - line
	f.after = append(append([]byte{}, f.before...), f.after[line:]...)
	for i := range f.observed.After.Callbacks {
		ref := &f.observed.After.Callbacks[i]
		ref.Exec += delta
		ref.Join += delta
		ref.Frame += delta
	}
	f.observed.After.End = len(f.after)
	if err := f.check(); err != nil {
		t.Fatal(err)
	}
	f.observed.After.Start = len(f.before)
	if err := f.check(); err != nil {
		t.Fatal("retained BEFORE prefix interfered with selected AFTER interval", err)
	}
	f.observed.After.Start = f.observed.After.Callbacks[1].Exec
	if err := f.check(); err == nil {
		t.Fatal("offset hid first actual B execution")
	}
}

func TestAgyCurrentUnitSingleLayerAndTraceReread(t *testing.T) {
	f := newAgyCurrentUnit()
	selection := WatchCensusAgyHooks{Config: f.observed.B.Config, Layers: []WatchCensusAgyLayer{{Generation: f.observed.B.Generation, Settings: f.observed.B.Settings, Installed: WatchCensusPin{Path: f.installed, SHA256: strings.Repeat("1", 64)}}}}
	if _, err := agyCurrentLayer(selection, f.observed, f.bCommands); err != nil {
		t.Fatal(err)
	}
	selection.Layers = append(selection.Layers, selection.Layers[0])
	if _, err := agyCurrentLayer(selection, f.observed, f.bCommands); err == nil {
		t.Fatal("duplicate active layers accepted")
	}
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "unit.trace")
	if err := os.WriteFile(path, f.after, 0600); err != nil {
		t.Fatal(err)
	}
	r := &watchCensusRead{catalog: WatchCensusCatalog{Root: root}, inodes: map[string]string{}, report: WatchUnitCensus{Facts: map[string]string{}}, local: &watchDrainRead{tokenPaths: map[string]bool{}}}
	pin := WatchCensusPin{Path: path, SHA256: agyTraceSHA(f.after)}
	if _, err := r.agyTrace(pin); err != nil {
		t.Fatal(err)
	}
	changed := append(append([]byte{}, f.after...), []byte("200 close(10) = 0\n")...)
	if err := os.WriteFile(path, changed, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := r.agyTrace(pin); err == nil {
		t.Fatal("changed raw trace accepted at final reread")
	}
	pin.SHA256 = agyTraceSHA(changed)
	if _, err := r.agyTrace(pin); err == nil {
		t.Fatal("caller repin bypassed captured trace fact")
	}
	r.local.tokenPaths[path] = true
	if _, err := r.agyTrace(pin); err == nil {
		t.Fatal("credential selector used as trace")
	}
}

func TestAgyCurrentDescriptorBounds(t *testing.T) {
	for _, raw := range []string{`{"Schema":"cairn.watch-agy-readback/1","Harness":"agy","EvidenceKind":"current-observation"}`, `{"Schema":"cairn.watch-agy-readback/2","Harness":"agy","EvidenceKind":"retained-native-strace"}`, `{"Schema":"cairn.watch-agy-readback/2","Harness":"agy","EvidenceKind":"code-preparation"}`, `{"Schema":"cairn.watch-agy-readback/2","Harness":"agy","EvidenceKind":"current-observation","Authorized":true}`, `{"Schema":"x","Schema":"cairn.watch-agy-readback/2"}`, `{"Schema":"x","schema":"cairn.watch-agy-readback/2","Harness":"agy","EvidenceKind":"current-observation"}`, `{"Schema":"cairn.watch-agy-readback/2","Harness":"agy","EvidenceKind":"current-observation","NativeSession":"\ud800"}`} {
		if _, err := agyCurrentDescriptor([]byte(raw)); err == nil {
			t.Fatalf("invalid UNIT descriptor accepted: %s", raw)
		}
	}
	if err := agyOneManagedSettings([]byte(`{"cairn-coordination":{},"unknown-active-hook":{}}`)); err == nil {
		t.Fatal("unknown active group accepted")
	}
}

func TestAgyCurrentGenerationCaptureAndFinalReread(t *testing.T) {
	for _, mutation := range []string{"none", "capture-start", "capture-boot", "owner-start", "owner-missing", "dead", "argv", "executable", "final-drift"} {
		t.Run(mutation, func(t *testing.T) {
			root := t.TempDir()
			if err := os.Chmod(root, 0700); err != nil {
				t.Fatal(err)
			}
			proc := filepath.Join(root, "proc")
			pidRoot := filepath.Join(proc, "200")
			if err := os.MkdirAll(filepath.Join(proc, "sys/kernel/random"), 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.MkdirAll(pidRoot, 0700); err != nil {
				t.Fatal(err)
			}
			image := filepath.Join(root, "owned-unit-image")
			if err := os.WriteFile(image, []byte("synthetic UNIT image"), 0500); err != nil {
				t.Fatal(err)
			}
			writeStat := func(state, start string) {
				fields := make([]string, 20)
				for i := range fields {
					fields[i] = "1"
				}
				fields[0] = state
				fields[1] = "100"
				fields[19] = start
				if err := os.WriteFile(filepath.Join(pidRoot, "stat"), []byte("200 (unit) "+strings.Join(fields, " ")), 0600); err != nil {
					t.Fatal(err)
				}
			}
			writeStat("S", "111")
			if err := os.WriteFile(filepath.Join(proc, "sys/kernel/random/boot_id"), []byte("unit-boot\n"), 0600); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink(image, filepath.Join(pidRoot, "exe")); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(pidRoot, "cmdline"), append([]byte(image), 0), 0600); err != nil {
				t.Fatal(err)
			}
			capture := agyGenerationCapture{Process: WatchCensusProcess{PID: 200, Start: "111", Boot: "unit-boot"}, Client: WatchCensusPin{Path: image, SHA256: agyTraceSHA([]byte("synthetic UNIT image"))}, ClientArgv: []string{image}}
			ownerProcess := censusNativeProcess(capture.Process)
			switch mutation {
			case "capture-start":
				capture.Process.Start = "222"
			case "capture-boot":
				capture.Process.Boot = "old"
			case "owner-start":
				ownerProcess = censusNativeProcess(WatchCensusProcess{PID: 200, Start: "222", Boot: "unit-boot"})
			case "owner-missing":
				ownerProcess = &value.Value{Kind: value.Null}
			case "dead":
				writeStat("Z", "111")
			case "argv":
				os.WriteFile(filepath.Join(pidRoot, "cmdline"), []byte("foreign\x00"), 0600)
			case "executable":
				os.Remove(filepath.Join(pidRoot, "exe"))
				os.Symlink("/foreign/unit-image", filepath.Join(pidRoot, "exe"))
			}
			r := &watchCensusRead{cli: &CLI{ProcRoot: proc}, files: map[string]string{}, images: map[string]bool{}, inodes: map[string]string{}, report: WatchUnitCensus{Facts: map[string]string{}}, local: &watchDrainRead{tokenPaths: map[string]bool{}}}
			state := nativeMap("process", ownerProcess)
			err := r.agyCurrentGeneration(capture, state)
			if mutation == "final-drift" {
				if err != nil {
					t.Fatal(err)
				}
				writeStat("S", "222")
				err = r.agyCurrentGeneration(capture, state)
			}
			if mutation == "none" && err != nil {
				t.Fatal(err)
			}
			if mutation != "none" && err == nil {
				t.Fatal("invalid UNIT generation accepted")
			}
		})
	}
}

func TestAgyCurrentPriorGenerationMustRemainGone(t *testing.T) {
	root := t.TempDir()
	r := &watchCensusRead{cli: &CLI{ProcRoot: root}}
	before, after := WatchCensusProcess{PID: 200}, WatchCensusProcess{PID: 300}
	if err := r.agyCurrentPriorGeneration(before, after); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "200")
	if err := os.Mkdir(path, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(path, "stat"), []byte("changed unit process"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := r.agyCurrentPriorGeneration(before, after); err == nil {
		t.Fatal("old/reused/unknown PID accepted after claimed join")
	}
}
