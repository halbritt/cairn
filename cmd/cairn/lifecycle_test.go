package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
)

type lifecyclePanicReader struct{}

func (lifecyclePanicReader) Read([]byte) (int, error) { panic("disabled lifecycle read stdin") }
func TestLifecyclePublicRegistrationClosed(t *testing.T) {
	for _, args := range [][]string{{"lifecycle"}, {"lifecycle", "check"}, {"lifecycle", "--enable"}, {"lifecycle", "binding", "activation"}} {
		t.Run(strings.Join(args, "_"), func(t *testing.T) {
			t.Setenv("CAIRN_LIFECYCLE_ENABLED", "1")
			_, err := run(context.Background(), args, lifecyclePanicReader{})
			if core.Code(err) != "LIFECYCLE_DISABLED" {
				t.Fatalf("disabled registration: %v", err)
			}
			if _, code := commandEnvelope(nil, err); code == 0 {
				t.Fatal("disabled registration reported success")
			}
		})
	}
	if !strings.Contains(help, "lifecycle (DISABLED") {
		t.Fatal("lifecycle registration missing from help")
	}
}

type lifecycleChildInput struct{ Args []string }

func TestLifecycleConsumerChild(t *testing.T) {
	path := os.Getenv("CAIRN_F01_CONSUMER_INPUT")
	if path == "" {
		return
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		panic(err)
	}
	var in lifecycleChildInput
	if err = json.Unmarshal(raw, &in); err != nil {
		panic(err)
	}
	result, err := executeLifecycleCheck(context.Background(), in.Args, os.Stdin)
	if err != nil {
		json.NewEncoder(os.Stdout).Encode(map[string]string{"error": err.Error()})
		os.Exit(1)
	}
	json.NewEncoder(os.Stdout).Encode(result)
	os.Exit(0)
}
func lifecycleSHA(raw []byte) string { s := sha256.Sum256(raw); return hex.EncodeToString(s[:]) }
func lifecycleWrite(t *testing.T, path string, raw []byte) {
	t.Helper()
	if err := os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
}
func lifecycleJSON(t *testing.T, v any) []byte {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

type lifecycleFixture struct {
	Root, Work, Binary, Config, Coord, Manifest, Receipt, Input, ReceiptSHA string
	Memory, Coordination                                                    map[string]any
	Enabled                                                                 bool
}

func newLifecycleFixture(t *testing.T, enabled bool) *lifecycleFixture {
	t.Helper()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	f := &lifecycleFixture{Root: root, Work: filepath.Join(root, "work"), Binary: filepath.Join(root, "stage", "consumer"), Config: filepath.Join(root, "memory.json"), Coord: filepath.Join(root, "coordination.json"), Manifest: filepath.Join(root, "binding.json"), Receipt: filepath.Join(root, "receipt.json"), Input: filepath.Join(root, "input.json"), Enabled: enabled}
	for _, p := range []string{f.Work, filepath.Dir(f.Binary)} {
		if err := os.Mkdir(p, 0700); err != nil {
			t.Fatal(err)
		}
	}
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(self)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(f.Binary, raw, 0500); err != nil {
		t.Fatal(err)
	}
	f.Memory = map[string]any{"harness": "codex", "cairn": "/not-executed", "socket": "/never-open.sock", "token_file": "/never-read-token", "repo": "fixture", "recall_mode": "agent_tools", "context_bytes": 9500, "state_dir": filepath.Join(root, "memory-state"), "inbox_recall_binding": f.Manifest}
	f.Coordination = map[string]any{"harness": "codex", "cairn": "/not-executed", "socket": "/never-open.sock", "token_file": "/never-read-token", "repo": "fixture", "native_delivery": true, "state_dir": filepath.Join(root, "coord-state"), "inbox_recall_binding": f.Manifest}
	f.write(t)
	return f
}
func (f *lifecycleFixture) write(t *testing.T) {
	t.Helper()
	mem := lifecycleJSON(t, f.Memory)
	coord := lifecycleJSON(t, f.Coordination)
	lifecycleWrite(t, f.Config, mem)
	lifecycleWrite(t, f.Coord, coord)
	binary, err := os.ReadFile(f.Binary)
	if err != nil {
		t.Fatal(err)
	}
	bd := map[string]any{"path": f.Binary, "sha256": lifecycleSHA(binary)}
	md := map[string]any{"path": f.Config, "sha256": lifecycleSHA(mem)}
	cd := map[string]any{"path": f.Coord, "sha256": lifecycleSHA(coord)}
	manifest := map[string]any{"schema": "cairn.inbox-recall-binding/3", "enabled": f.Enabled, "binary": bd, "memory_config": md, "coordination_configs": []any{cd}}
	mr := lifecycleJSON(t, manifest)
	lifecycleWrite(t, f.Manifest, mr)
	names := []string{"lifecycle_bundle.py", "memory.py", "coordination.py", "inbox_recall.py", "inbox_binding.py", "grant_ledger.py", "transcript_recovery.py", "unix_peer_transport.py", "wake_transport.py", "process_scan.py", "codex_queue.py", "claude_channel.py", "opencode_queue.py", "hermes_queue.py", "recall_dispatch.py", "hook_admission.py", "native_session.py", "lifecycle_compatibility.py"}
	code := map[string]string{}
	members := map[string]any{}
	for _, name := range names {
		raw := []byte("# checked fixture bytes, never executed: " + name + "\n")
		code[name] = lifecycleSHA(raw)
		members[name] = map[string]any{"sha256": code[name], "bytes": len(raw)}
	}
	snapshot := filepath.Join(f.Root, lifecycleSHA(lifecycleJSON(t, code)))
	if err = os.MkdirAll(snapshot, 0700); err != nil {
		t.Fatal(err)
	}
	for _, name := range names {
		lifecycleWrite(t, filepath.Join(snapshot, name), []byte("# checked fixture bytes, never executed: "+name+"\n"))
	}
	receipt := map[string]any{"schema": "cairn.lifecycle-compatibility/1", "source_revision": "fixture-writer/v1", "snapshot_root": snapshot, "members": members, "bootstrap": "lifecycle_compatibility.py", "loader": "lifecycle_bundle.py", "roles": map[string]string{"memory": "memory.py", "coordination": "coordination.py", "inbox_recall": "inbox_recall.py", "inbox_binding": "inbox_binding.py"}, "aliases": map[string]string{}, "binary": bd, "binding": map[string]any{"path": f.Manifest, "sha256": lifecycleSHA(mr), "schema": "cairn.inbox-recall-binding/3"}, "memory_config": md, "coordination_configs": []any{cd}}
	raw := lifecycleJSON(t, receipt)
	lifecycleWrite(t, f.Receipt, raw)
	f.ReceiptSHA = lifecycleSHA(raw)
	lifecycleWrite(t, f.Input, lifecycleJSON(t, lifecycleChildInput{[]string{"check", "--entrypoint", "memory", "--config", f.Config, "--receipt", f.Receipt, "--receipt-sha256", f.ReceiptSHA}}))
}
func lifecycleEnv(extra []string) []string {
	var env []string
	for _, e := range os.Environ() {
		key, _, _ := strings.Cut(e, "=")
		if strings.HasPrefix(key, "CAIRN_") || key == "CODEX_HOME" || key == "CLAUDE_CONFIG_DIR" {
			continue
		}
		env = append(env, e)
	}
	return append(env, extra...)
}
func (f *lifecycleFixture) run(t *testing.T, event []byte, env []string) (map[string]any, int, string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, f.Binary, "-test.run=^TestLifecycleConsumerChild$")
	cmd.Dir = f.Work
	cmd.Env = lifecycleEnv(append(env, "CAIRN_F01_CONSUMER_INPUT="+f.Input))
	cmd.Stdin = bytes.NewReader(event)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	code := 0
	if err != nil {
		if e, ok := err.(*exec.ExitError); ok {
			code = e.ExitCode()
		} else {
			t.Fatal(err)
		}
	}
	var result map[string]any
	if err := json.Unmarshal(stdout.Bytes(), &result); err != nil {
		t.Fatalf("invalid child output %q,stderr%q: %v", stdout.String(), stderr.String(), err)
	}
	result["_stdout_for_evidence"] = stdout.String()
	return result, code, stderr.String()
}
func fixtureState(t *testing.T, f *lifecycleFixture) map[string]string {
	t.Helper()
	result := map[string]string{}
	err := filepath.Walk(f.Root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if path == f.Binary {
			result[path] = "binary:" + info.Mode().String()
			return nil
		}
		if !info.Mode().IsRegular() {
			result[path] = info.Mode().String()
			return nil
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		result[path] = lifecycleSHA(raw)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func TestLifecycleConsumerSameAdmissionCorpus(t *testing.T) {
	source, err := filepath.Abs("../../integrations/lifecycle/hook_admission.py")
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name, event, env, disposition string
		marker, otherHome             bool
	}{
		{name: "writer-user-prompt", event: "UserPromptSubmit", disposition: "checked"},
		{name: "writer-session-start", event: "SessionStart", disposition: "checked"},
		{name: "writer-disabled", event: "UserPromptSubmit", disposition: "disabled"},
		{name: "guard-disabled-env", event: "UserPromptSubmit", env: "CAIRN_LIFECYCLE_DISABLED=1", disposition: "excluded"},
		{name: "guard-child-env", event: "UserPromptSubmit", env: "CAIRN_LIFECYCLE_CHILD=1", disposition: "excluded"},
		{name: "guard-wake", event: "UserPromptSubmit", env: "CAIRN_WAKE_CONTEXT=/not-opened", disposition: "excluded"},
		{name: "writer-unsupported-event", event: "Stop", disposition: "excluded"},
		{name: "writer-privacy", event: "UserPromptSubmit", marker: true, disposition: "excluded"},
		{name: "writer-other-home", event: "UserPromptSubmit", otherHome: true, disposition: "excluded"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newLifecycleFixture(t, tc.name != "writer-disabled")
			var extra []string
			if tc.env != "" {
				extra = append(extra, tc.env)
			}
			if tc.marker {
				lifecycleWrite(t, filepath.Join(f.Work, ".cairn-no-memory"), []byte("fixture"))
			}
			if tc.otherHome {
				f.Memory["config_home"] = filepath.Join(f.Root, "other-home")
				f.write(t)
				extra = append(extra, "CODEX_HOME="+filepath.Join(f.Root, "active-home"))
			}
			event := lifecycleJSON(t, map[string]any{"hook_event_name": tc.event, "cwd": f.Work, "session_id": "11111111-1111-4111-8111-111111111111", "turn_id": "22222222-2222-4222-8222-222222222222", "opaque": map[string]any{"int": json.Number("9007199254740993"), "unicode": "é<>&"}})
			before := fixtureState(t, f)
			actual, code, stderr := f.run(t, event, extra)
			goStdout := actual["_stdout_for_evidence"].(string)
			delete(actual, "_stdout_for_evidence")
			goState := fixtureState(t, f)
			if !reflect.DeepEqual(before, goState) {
				t.Fatal("Go consumer changed fixture state")
			}
			if code != 0 || stderr != "" {
				t.Fatalf("Go code%d stderr%q result%v", code, stderr, actual)
			}
			if actual["disposition"] != tc.disposition {
				t.Fatalf("disposition %v", actual)
			}
			py := exec.Command("python3", "-I", "-B", "-c", `import hashlib,json,runpy,sys
from pathlib import Path
a=runpy.run_path(sys.argv[1]); b=runpy.run_path(str(Path(sys.argv[1]).with_name('inbox_binding.py')))
raw=Path(sys.argv[2]).read_bytes(); cfg=json.loads(raw); e=json.loads(sys.stdin.buffer.read()); d=a['decide']('inbox_recall',cfg,e)
out={'disposition':'excluded','verdict':d.verdict,'config_sha256':hashlib.sha256(raw).hexdigest()}
if d.reason: out['reason']=d.reason
if d.run:
    path,m=b['_owner_manifest'](cfg)
    out['binding_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    out['disposition']='disabled'
    if m['enabled']:
        b['_checked_file'](m['memory_config'])
        for descriptor in m['coordination_configs']: b['_checked_file'](descriptor)
        out['disposition']='checked'
ordered={'disposition':out['disposition'],'verdict':out['verdict']}
if 'reason' in out: ordered['reason']=out['reason']
ordered['config_sha256']=out['config_sha256']
if 'binding_sha256' in out: ordered['binding_sha256']=out['binding_sha256']
print(json.dumps(ordered,separators=(',',':')))`, source, f.Config)
			py.Dir = f.Work
			py.Env = lifecycleEnv(append(extra, "CAIRN_F01_CONSUMER_INPUT="+f.Input))
			py.Stdin = bytes.NewReader(event)
			var pstdout, pstderr bytes.Buffer
			py.Stdout = &pstdout
			py.Stderr = &pstderr
			if err := py.Run(); err != nil {
				t.Fatalf("Python oracle: %v stderr%q", err, pstderr.String())
			}
			var expected map[string]any
			if err := json.Unmarshal(pstdout.Bytes(), &expected); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(actual, expected) || goStdout != pstdout.String() || pstderr.String() != stderr {
				t.Fatalf("same corpus mismatch: Go%v Python%v streams%q/%q", actual, expected, stderr, pstderr.String())
			}
			t.Logf("ClassA writer-producible input=%q Go_stdout=%q Go_stderr=%q Go_exit=%d Python_stdout=%q Python_stderr=%q Python_exit=0 state_before=%v state_after=%v", event, goStdout, stderr, code, pstdout.String(), pstderr.String(), before, fixtureState(t, f))
			if after := fixtureState(t, f); !reflect.DeepEqual(before, after) {
				t.Fatal("read-only consumer/oracle changed state files")
			}
		})
	}
}
func TestLifecycleConsumerRefusals(t *testing.T) {
	for _, which := range []string{"malformed-event", "receipt-tamper", "binding-tamper", "config-hash", "profile", "legacy", "closure-tamper", "excluded-binding-unread", "fifo-data"} {
		t.Run(which, func(t *testing.T) {
			f := newLifecycleFixture(t, true)
			event := []byte(fmt.Sprintf(`{"hook_event_name":"UserPromptSubmit","cwd":%q}`, f.Work))
			extra := []string{}
			wantError := true
			switch which {
			case "fifo-data":
				if err := os.Remove(f.Manifest); err != nil {
					t.Fatal(err)
				}
				if err := syscall.Mkfifo(f.Manifest, 0600); err != nil {
					t.Fatal(err)
				}
			case "malformed-event":
				event = []byte("[")
			case "receipt-tamper":
				lifecycleWrite(t, f.Receipt, []byte("{}"))
			case "binding-tamper":
				lifecycleWrite(t, f.Manifest, []byte("{}"))
			case "config-hash":
				f.Memory["opaque"] = true
				lifecycleWrite(t, f.Config, lifecycleJSON(t, f.Memory))
			case "profile":
				f.Coordination["native_delivery"] = false
				f.write(t)
			case "legacy":
				var m map[string]any
				raw, _ := os.ReadFile(f.Manifest)
				json.Unmarshal(raw, &m)
				m["schema"] = "cairn.inbox-recall-binding/2"
				lifecycleWrite(t, f.Manifest, lifecycleJSON(t, m))
			case "closure-tamper":
				var r map[string]any
				raw, _ := os.ReadFile(f.Receipt)
				json.Unmarshal(raw, &r)
				lifecycleWrite(t, filepath.Join(r["snapshot_root"].(string), "memory.py"), []byte("changed"))
			case "excluded-binding-unread":
				extra = append(extra, "CAIRN_LIFECYCLE_DISABLED=1")
				lifecycleWrite(t, f.Manifest, []byte("not json"))
				wantError = false
			}
			before := fixtureState(t, f)
			result, code, stderr := f.run(t, event, extra)
			goStdout := result["_stdout_for_evidence"].(string)
			delete(result, "_stdout_for_evidence")
			if stderr != "" {
				t.Fatalf("unexpected stderr %q", stderr)
			}
			if wantError {
				if code != 1 || result["error"] == nil {
					t.Fatalf("failure accepted: %v,%d", result, code)
				}
			} else if code != 0 || result["disposition"] != "excluded" {
				t.Fatalf("binding was consumed before guard: %v,%d", result, code)
			}
			expectedErrors := map[string]string{"malformed-event": "JSONDecodeError:", "receipt-tamper": "compatibility receipt changed", "binding-tamper": "binding field must be a string", "config-hash": "inbox recall binding changed", "profile": "incompatible inbox recall profile binding", "legacy": "retained pinned Python", "closure-tamper": "compatibility closure member changed", "fifo-data": "binding data is not owner-controlled"}
			if wantError && !strings.Contains(result["error"].(string), expectedErrors[which]) {
				t.Fatalf("unstable refusal site for %s: %v", which, result)
			}
			if which == "legacy" && !strings.Contains(result["error"].(string), "retained pinned Python") {
				t.Fatalf("/2 trusted Go: %v", result)
			}
			t.Logf("Go refusal/input=%q stdout=%q stderr=%q exit=%d state_before=%v state_after=%v", event, goStdout, stderr, code, before, fixtureState(t, f))
			if !reflect.DeepEqual(before, fixtureState(t, f)) {
				t.Fatal("refusal changed state files")
			}
		})
	}
}

var _ io.Reader = lifecyclePanicReader{}

func TestLifecycleActualPublicBinaryDisabled(t *testing.T) {
	dir := t.TempDir()
	binary := filepath.Join(dir, "cairn")
	build := exec.Command("go", "build", "-buildvcs=false", "-o", binary, ".")
	if raw, err := build.CombinedOutput(); err != nil {
		t.Fatalf("fixture CLI build: %v %s", err, raw)
	}
	cmd := exec.Command(binary, "lifecycle", "check", "--enable")
	cmd.Env = append(os.Environ(), "CAIRN_LIFECYCLE_ENABLED=1", "CAIRN_DATABASE_URL=postgresql://forbidden.invalid/never")
	cmd.Stdin = strings.NewReader(`{"enabled":true,"operator":true}`)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	if err == nil {
		t.Fatal("public lifecycle enabled")
	}
	var result response
	if err := json.Unmarshal(stdout.Bytes(), &result); err != nil {
		t.Fatalf("CLI output %q: %v", stdout.String(), err)
	}
	if result.Status != "LIFECYCLE_DISABLED" || result.OK || stderr.Len() != 0 {
		t.Fatalf("public refusal: %+v stderr%q", result, stderr.String())
	}
}
