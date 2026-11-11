package journal

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestStage2DNaNScalarAndSessionJournals(t *testing.T) {
	if got := scalarString(json.Number("NaN")); got != "nan" {
		t.Fatalf("scalarString(NaN) = %q, want %q", got, "nan")
	}
	if got := scalarString(json.Number("Infinity")); got != "inf" {
		t.Fatalf("scalarString(Infinity) = %q, want %q", got, "inf")
	}
	if got := scalarString(json.Number("-Infinity")); got != "-inf" {
		t.Fatalf("scalarString(-Infinity) = %q, want %q", got, "-inf")
	}

	raw := map[string]any{
		"inbox_journals": []any{json.Number("NaN"), "att-1"},
		"inbox_attempt":  map[string]any{"attempt_id": json.Number("NaN")},
	}
	withConsts := DefaultJSONConstants(raw).(map[string]any)
	ids, err := SessionJournals(t.TempDir(), withConsts)
	if err != nil {
		t.Fatalf("SessionJournals error: %v", err)
	}
	if len(ids) != 2 {
		t.Fatalf("SessionJournals duplicated NaN attempt_id: got len=%d (%#v), want 2", len(ids), ids)
	}
	plain := PlainValue(withConsts).(map[string]any)
	wantPlain := map[string]any{
		"inbox_journals": []any{json.Number("NaN"), "att-1"},
		"inbox_attempt":  map[string]any{"attempt_id": json.Number("NaN")},
	}
	if !reflect.DeepEqual(plain, wantPlain) {
		t.Fatalf("PlainValue = %#v, want %#v", plain, wantPlain)
	}
}

func TestStage2DClassAPythonGoJournalCorpus(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	t.Setenv("CAIRN_INBOX_ROOT", "")
	coordPy, err := filepath.Abs("../../../integrations/lifecycle/coordination.py")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(coordPy); err != nil {
		t.Fatalf("missing reference script %s: %v", coordPy, err)
	}

	root := t.TempDir()
	fakeCairn := filepath.Join(root, "fake-cairn.py")
	script := "#!/usr/bin/env python3\nimport json, sys\nargs = sys.argv[1:]\nif 'complete' in args:\n    _ = sys.stdin.read()\n    print(json.dumps({'ok': True, 'data': {'result': {'record_id': '301e246e-a3b7-498b-b6a7-3d84e592097f', 'version': 1}}}))\nelif 'publish' in args:\n    print(json.dumps({'ok': True, 'data': {'event_id': '291f7647-91e2-4e8b-9817-1870a469258b', 'ref': {'record_id': '301e246e-a3b7-498b-b6a7-3d84e592097f', 'version': 1}}}))\nelse:\n    print(json.dumps({'ok': True}))\n"
	if err := os.WriteFile(fakeCairn, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}

	makeEnv := func(sub string) (string, string, map[string]any) {
		dir := filepath.Join(root, sub)
		inboxDir := filepath.Join(dir, "inbox")
		if err := os.MkdirAll(inboxDir, 0o700); err != nil {
			t.Fatal(err)
		}
		ctxPath := filepath.Join(inboxDir, "att-corpus-1.json")
		ctxObj := map[string]any{
			"schema":       "cairn.session-inbox/1",
			"agent_id":     "301e246e-a3b7-498b-b6a7-3d84e592097f",
			"execution_id": "da3e0447-4f10-40f7-b64d-6b66de691ef2",
			"attempt_id":   "att-corpus-1",
			"event_id":     "80c5da6e-9772-47a9-9cdf-2517d38811b7",
			"kind":         "request",
			"sender":       "agent/291f7647-91e2-4e8b-9817-1870a469258b",
			"source":       map[string]any{"record_id": "80c5da6e-9772-47a9-9cdf-2517d38811b7", "version": 1},
			"delivery_id":  "5e02aa76-2451-47c2-bd89-2eab763ffa46",
			"lease_id":     "0fc910a9-7f79-4329-914d-59ce2ca98db8",
			"state_dir":    dir,
			"request_ids":  map[string]any{"completion": "req-comp-1", "response": "req-resp-1"},
			"commands": map[string]any{
				"complete": []any{fakeCairn, "complete", "--request-id", "req-comp-1", "--lease", "0fc910a9-7f79-4329-914d-59ce2ca98db8", "--shareable", "--stdin", "5e02aa76-2451-47c2-bd89-2eab763ffa46"},
				"ack":      []any{fakeCairn, "ack", "--request-id", "req-comp-1", "--lease", "0fc910a9-7f79-4329-914d-59ce2ca98db8", "5e02aa76-2451-47c2-bd89-2eab763ffa46"},
				"respond":  []any{fakeCairn, "publish", "--request-id", "req-resp-1", "--to", "agent/291f7647-91e2-4e8b-9817-1870a469258b", "--kind", "response", "--causation-id", "80c5da6e-9772-47a9-9cdf-2517d38811b7"},
			},
		}
		raw, err := json.Marshal(ctxObj)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(ctxPath, raw, 0o600); err != nil {
			t.Fatal(err)
		}
		return dir, ctxPath, ctxObj
	}

	pyDir, pyCtx, _ := makeEnv("py")
	goDir, _, goObj := makeEnv("go")

	pyCmd := exec.Command("python3", "-I", "-B", coordPy, "inbox", "--context", pyCtx, "finish-reply")
	pyCmd.Stdin = strings.NewReader("selected result body\n")
	pyOut, err := pyCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("python finish-reply failed: %v (%s)", err, string(pyOut))
	}

	rawCtx := RawContext{
		StateDir:  goDir,
		AttemptID: "att-corpus-1",
		Values:    goObj,
		Command: func(name string) ([]any, error) {
			return rawList(goObj["commands"].(map[string]any)[name])
		},
	}
	goRes, err := ExecuteRaw(context.Background(), rawCtx, Request{Kind: "finish-reply", Input: "selected result body\n"}, Options{})
	if err != nil || goRes.ExitCode != 0 {
		t.Fatalf("go finish-reply failed: res=%+v err=%v", goRes, err)
	}
	var pyStdoutMap, goStdoutMap map[string]any
	if err := json.Unmarshal(pyOut, &pyStdoutMap); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(goRes.Stdout), &goStdoutMap); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(goStdoutMap, pyStdoutMap) {
		t.Fatalf("stdout JSON mismatch:\ngo: %#v\npy: %#v", goStdoutMap, pyStdoutMap)
	}

	pyJournalRaw, err := os.ReadFile(path(pyDir, "att-corpus-1"))
	if err != nil {
		t.Fatal(err)
	}
	goJournalRaw, err := os.ReadFile(path(goDir, "att-corpus-1"))
	if err != nil {
		t.Fatal(err)
	}
	var pyJournalMap, goJournalMap map[string]any
	if err := json.Unmarshal(pyJournalRaw, &pyJournalMap); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(goJournalRaw, &goJournalMap); err != nil {
		t.Fatal(err)
	}
	for _, m := range []map[string]any{pyJournalMap, goJournalMap} {
		for _, slot := range []string{"completion", "response"} {
			if s, ok := m[slot].(map[string]any); ok {
				if _, ok := s["recorded_at"].(float64); !ok {
					t.Fatalf("missing numeric recorded_at in %s: %#v", slot, s)
				}
				delete(s, "recorded_at")
			}
		}
	}
	if !reflect.DeepEqual(goJournalMap, pyJournalMap) {
		t.Fatalf("journal JSON mismatch:\ngo: %#v\npy: %#v", goJournalMap, pyJournalMap)
	}
}
