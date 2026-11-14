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

func TestAgyCurrentUnitNativeInputLiveOnly(t *testing.T) {
	f := newAgyCurrentUnit()
	if err := f.check(); err != nil {
		t.Fatal("complete synthetic control", err)
	}
	// Readback/2 may observe one native stdin wait after complete callbacks.
	// Closed/readback1 decoding still requires every syscall to complete.
	f.before = append(f.before, []byte("200 read(0 <unfinished ...>\n")...)
	f.observed.Before.End = len(f.before)
	if _, err := agyTraceCalls(f.before); err == nil || err.Error() != "incomplete Agy trace syscall coverage" {
		t.Fatal("unfinished input was accepted or refused for another reason", err)
	}
	if err := f.check(); err != nil {
		t.Fatal("bounded native stdin wait refused by current phase", err)
	}
}

func TestAgyCurrentUnitLivePendingBounds(t *testing.T) {
	for _, name := range []string{"native-thread", "no-pending", "fd00", "fd1", "fd-argument", "fd-hex", "nonread", "second-pending", "overlap", "complete-overlap", "fork", "callback", "fork-thread", "fake-thread-flag", "failed-thread", "missing-eof", "not-idle"} {
		t.Run(name, func(t *testing.T) {
			f := newAgyCurrentUnit()
			tail := "200 read(0 <unfinished ...>\n"
			switch name {
			case "native-thread":
				tail = "200 clone(child_stack=NULL, flags=CLONE_VM|CLONE_SIGHAND|CLONE_THREAD) = 290\n290 read(0 <unfinished ...>\n"
			case "no-pending":
				tail = ""
			case "fd00":
				tail = "200 read(00 <unfinished ...>\n"
			case "fd1":
				tail = "200 read(1 <unfinished ...>\n"
			case "fd-argument":
				tail = "200 read(0, <unfinished ...>\n"
			case "fd-hex":
				tail = "200 read(0x0 <unfinished ...>\n"
			case "nonread":
				tail = "200 futex(0x1, FUTEX_WAIT, 0, NULL <unfinished ...>\n"
			case "second-pending":
				tail = "200 clone(child_stack=NULL, flags=CLONE_THREAD) = 290\n200 read(0 <unfinished ...>\n290 read(0 <unfinished ...>\n"
			case "overlap":
				tail += "200 read(0 <unfinished ...>\n"
			case "complete-overlap":
				tail += "200 close(9) = 0\n"
			case "fork":
				tail = "200 fork() = 290\n290 read(0 <unfinished ...>\n"
			case "callback":
				tail = "340 read(0 <unfinished ...>\n"
			case "fork-thread":
				tail = "200 fork() = 290\n290 clone(child_stack=NULL, flags=CLONE_THREAD) = 291\n291 read(0 <unfinished ...>\n"
			case "fake-thread-flag":
				tail = "200 clone(child_stack=NULL, flags=NOT_CLONE_THREAD) = 290\n290 read(0 <unfinished ...>\n"
			case "failed-thread":
				tail = "200 clone3({flags=CLONE_THREAD}, 88) = -1 ENOSYS (Function not implemented)\n290 read(0 <unfinished ...>\n"
			case "missing-eof":
				f.after = bytes.Replace(f.after, []byte("340 read(0, \"\", 4096) = 0\n"), []byte("340 close(0) = 0\n"), 1)
			case "not-idle":
				f.after, f.observed.After = agyUnitPhase(200, 340, f.observed.Client.Path, f.observed.B, f.bCommands, f.bSettings, json.RawMessage("false"))
			}
			f.after = append(f.after, []byte(tail)...)
			f.observed.After.End = len(f.after)
			err := f.check()
			positive := name == "native-thread" || name == "no-pending"
			if positive && err != nil {
				t.Fatal(err)
			}
			if !positive && err == nil {
				t.Fatal("invalid pending input accepted")
			}
		})
	}
}

func TestAgyCurrentUnitStopMarkerRemainsRefused(t *testing.T) {
	f := newAgyCurrentUnit()
	if err := f.check(); err != nil {
		t.Fatal("complete synthetic control", err)
	}
	// This is the actual kernel notification form observed by the owned
	// nonnative probe, used here only in a synthetic codec control.
	f.after = append(f.after, []byte("200 --- stopped by SIGSTOP ---\n")...)
	f.observed.After.End = len(f.after)
	if _, err := agyTraceCalls(f.after); err != nil {
		t.Fatal("the syscall codec should accept the kernel marker", err)
	}
	if err := f.check(); err == nil || err.Error() != "unknown current Agy trace record" {
		t.Fatal("full current phase stop-marker guard changed", err)
	}
}

// This opt-in test reads historical raw bytes only. It neither starts the
// held image nor supplies a current process/session or authenticated drain.
func TestAgyCurrentRetainedLivePrefix(t *testing.T) {
	path := os.Getenv("CAIRN_R06_AGY_RETAINED_LIVE_PREFIX")
	if path == "" {
		t.Skip("explicit retained live-prefix codec source not selected")
	}
	var input struct {
		Schema, NativeSession, Workspace string
		Source, Prefix                   WatchCensusPin
		NativePID, PendingPID            int
		PendingStart                     int
		PendingRaw                       string
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := hookDecode(raw, &input); err != nil {
		t.Fatal(err)
	}
	if input.Schema != "cairn.agy-retained-live-prefix/1" || input.NativePID <= 1 || input.PendingPID <= 1 ||
		!filepath.IsAbs(input.Source.Path) || !filepath.IsAbs(input.Prefix.Path) || !filepath.IsAbs(input.Workspace) {
		t.Fatal("missing retained source provenance")
	}
	source, err := os.ReadFile(input.Source.Path)
	if err != nil || agyTraceSHA(source) != input.Source.SHA256 {
		t.Fatal("retained source changed", err)
	}
	prefix, err := os.ReadFile(input.Prefix.Path)
	if err != nil || agyTraceSHA(prefix) != input.Prefix.SHA256 || len(prefix) == 0 || len(prefix) >= len(source) ||
		prefix[len(prefix)-1] != '\n' || !bytes.Equal(prefix, source[:len(prefix)]) {
		t.Fatal("prefix is not an exact complete raw source prefix", err)
	}
	expected := fmt.Sprintf("%d  read(0 <unfinished ...>\n", input.PendingPID)
	if input.PendingRaw != expected || input.PendingStart < 0 || input.PendingStart+len(expected) >= len(prefix) ||
		string(prefix[input.PendingStart:input.PendingStart+len(expected)]) != expected {
		t.Fatal("pending input locator differs from retained bytes")
	}
	calls, err := agyTraceCalls(source)
	if err != nil {
		t.Fatal("complete historical trace codec refused", err)
	}
	threads := map[int]bool{input.NativePID: true}
	for changed := true; changed; {
		changed = false
		for _, call := range calls {
			if call.start >= input.PendingStart || !threads[call.pid] ||
				(call.name != "clone" && call.name != "clone3") || !strings.Contains(call.body, "CLONE_THREAD") {
				continue
			}
			index := strings.LastIndex(call.body, " = ")
			if index >= 0 {
				child, err := strconv.Atoi(call.body[index+3:])
				if err == nil && child > 1 && !threads[child] {
					threads[child], changed = true, true
				}
			}
		}
	}
	if !threads[input.PendingPID] {
		t.Fatal("pending input is not a traced native thread")
	}
	idleAfterPending, laterCompleted := false, false
	for _, call := range calls {
		// The exact original read(0 prefix is pinned above. strace's resumed
		// form can retain whitespace before its comma; the start locator ties
		// the completed call to those same original bytes.
		if call.pid == input.PendingPID && call.start == input.PendingStart && call.name == "read" {
			laterCompleted = true
		}
		if call.start <= input.PendingStart || call.start >= len(prefix) || call.name != "write" || !threads[call.pid] {
			continue
		}
		body, _, err := agyTraceIO(call)
		if err == nil && agyCurrentFrame(body, "Stop", input.NativeSession, input.Workspace) == nil {
			idleAfterPending = true
		}
	}
	if !idleAfterPending || !laterCompleted {
		t.Fatalf("missing actual idle frame or later raw completion: idle=%t completed=%t", idleAfterPending, laterCompleted)
	}
	if _, err := agyTraceCalls(prefix); err == nil || err.Error() != "incomplete Agy trace syscall coverage" {
		t.Fatal("live prefix did not reproduce the exact remaining guard", err)
	}
	if _, err := agyTraceCallsLive(prefix, input.NativePID); err != nil {
		t.Fatal("retained prefix codec refused the approved native-thread wait", err)
	}
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
	if _, err := agyCurrentLayer(selection, f.observed, f.bCommands, nil); err != nil {
		t.Fatal(err)
	}
	selection.Layers = append(selection.Layers, selection.Layers[0])
	if _, err := agyCurrentLayer(selection, f.observed, f.bCommands, nil); err == nil {
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

func TestAgyCurrentUnitTargetDoesNotSelectB(t *testing.T) {
	f := newAgyCurrentUnit()
	f.observed.A.Config.SHA256 = strings.Repeat("a", 64)
	f.observed.B.Config.SHA256 = strings.Repeat("b", 64)
	current := WatchCensusPin{Path: "/unit-current/selected/coordination.json", SHA256: f.observed.A.Config.SHA256}
	selection := WatchCensusAgyHooks{Config: current, Layers: []WatchCensusAgyLayer{{Generation: f.observed.B.Generation, Settings: f.observed.B.Settings, Installed: WatchCensusPin{Path: f.installed, SHA256: strings.Repeat("1", 64)}}}}
	if _, err := agyCurrentLayer(selection, f.observed, f.bCommands, nil); err == nil {
		t.Fatal("different B accepted without a private target observation")
	}
	view := &unitCensusTargetObservation{current: current, config: f.observed.B.Config}
	if _, err := agyCurrentLayer(selection, f.observed, f.bCommands, view); err != nil {
		t.Fatal(err)
	}
	if selection.Config != current {
		t.Fatal("target observation promoted B into current")
	}
	for _, mutation := range []string{"current-path", "current-pin", "target-path", "target-pin"} {
		t.Run(mutation, func(t *testing.T) {
			bad := *view
			switch mutation {
			case "current-path":
				bad.current.Path += ".copy"
			case "current-pin":
				bad.current.SHA256 = bad.config.SHA256
			case "target-path":
				bad.config.Path += ".copy"
			case "target-pin":
				bad.config.SHA256 = current.SHA256
			}
			if _, err := agyCurrentLayer(selection, f.observed, f.bCommands, &bad); err == nil {
				t.Fatal("mixed target/current tuple accepted")
			}
		})
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
