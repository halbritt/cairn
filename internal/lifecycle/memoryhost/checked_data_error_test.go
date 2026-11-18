package memoryhost

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/memoryhook"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestCheckedConfigurationFailureRecordsOnlySourceEntryAndExit(t *testing.T) {
	for _, class := range []string{"OSError", "ValueError"} {
		t.Run(class, func(t *testing.T) {
			root := t.TempDir()
			config := filepath.Join(root, "missing-private-config")
			directory := filepath.Join(root, "invocation-trace")
			if err := os.Mkdir(directory, 0700); err != nil {
				t.Fatal(err)
			}
			var sourceError error = &value.Error{Class: "ValueError", Message: "private-data-must-not-appear"}
			if class == "OSError" {
				sourceError = &os.PathError{Op: "open", Path: filepath.Join(root, "private-data-must-not-appear"), Err: os.ErrNotExist}
			}
			data := &binding.OrdinaryDataError{Phase: "configuration", Err: sourceError}
			want := memoryhook.HostFailure(data.Phase, data.Err)
			var out, diagnostic bytes.Buffer
			if code := checkedDataFailure(config, data, &out, &diagnostic); code != want.ExitCode || out.String() != want.Stdout || diagnostic.String() != want.Stderr {
				t.Fatalf("configuration failure changed: code=%d stdout=%q stderr=%q", code, &out, &diagnostic)
			}
			raw, err := os.ReadFile(filepath.Join(directory, "trace.jsonl"))
			if err != nil {
				t.Fatal(err)
			}
			lines := bytes.Split(bytes.TrimSpace(raw), []byte{'\n'})
			if len(lines) != 2 || strings.Contains(string(raw), "private-data") {
				t.Fatalf("configuration trace count/content: %q", raw)
			}
			rows := make([]map[string]any, 2)
			for i, line := range lines {
				if len(line)+1 > 512 || json.Unmarshal(line, &rows[i]) != nil {
					t.Fatalf("invalid bounded trace record: %q", line)
				}
			}
			keys := func(row map[string]any) []string {
				var result []string
				for key := range row {
					result = append(result, key)
				}
				sort.Strings(result)
				return result
			}
			if !reflect.DeepEqual(keys(rows[0]), []string{"at", "inv", "kind", "pid", "ppid", "schema"}) ||
				!reflect.DeepEqual(keys(rows[1]), []string{"at", "duration_ms", "entry_recorded", "error_class", "inv", "kind", "main_return_code", "outcome", "phase", "schema"}) {
				t.Fatalf("configuration trace keys changed: %+v", rows)
			}
			if rows[0]["kind"] != "entry" || rows[1]["kind"] != "exit" || rows[0]["inv"] != rows[1]["inv"] ||
				rows[1]["phase"] != "configuration" || rows[1]["error_class"] != class || rows[1]["outcome"] != "error" ||
				rows[1]["main_return_code"] != float64(1) || rows[1]["entry_recorded"] != true {
				t.Fatalf("configuration failure trace fields: %+v", rows)
			}
			entries, err := os.ReadDir(root)
			if err != nil || len(entries) != 1 || entries[0].Name() != "invocation-trace" {
				t.Fatalf("configuration failure created body state: %+v %v", entries, err)
			}
		})
	}
}

func TestCheckedDataFailureKeepsOptionalObserverFailuresHarmless(t *testing.T) {
	for _, kind := range []string{"disabled", "unsafe-directory", "unwritable-record", "other-phase"} {
		t.Run(kind, func(t *testing.T) {
			root := t.TempDir()
			directory := filepath.Join(root, "invocation-trace")
			if kind != "disabled" {
				if err := os.Mkdir(directory, 0700); err != nil {
					t.Fatal(err)
				}
			}
			if kind == "unsafe-directory" {
				if err := os.Chmod(directory, 0777); err != nil {
					t.Fatal(err)
				}
			}
			if kind == "unwritable-record" {
				if err := os.Mkdir(filepath.Join(directory, "trace.jsonl"), 0700); err != nil {
					t.Fatal(err)
				}
			}
			phase := "configuration"
			if kind == "other-phase" {
				phase = "activation"
			}
			data := &binding.OrdinaryDataError{Phase: phase, Err: os.ErrNotExist}
			want := memoryhook.HostFailure(data.Phase, data.Err)
			var out, diagnostic bytes.Buffer
			if code := checkedDataFailure(filepath.Join(root, "missing"), data, &out, &diagnostic); code != want.ExitCode || out.String() != want.Stdout || diagnostic.String() != want.Stderr {
				t.Fatalf("observer changed error result: code=%d stdout=%q stderr=%q", code, &out, &diagnostic)
			}
			if kind == "disabled" {
				if _, err := os.Stat(directory); !os.IsNotExist(err) {
					t.Fatalf("disabled observer created directory: %v", err)
				}
			} else if kind != "unwritable-record" {
				if _, err := os.Stat(filepath.Join(directory, "trace.jsonl")); !os.IsNotExist(err) {
					t.Fatalf("unavailable observer wrote trace: %v", err)
				}
			}
		})
	}
}

func TestStateTraceDirAllowsImmutableGenerationTracing(t *testing.T) {
	root := t.TempDir()
	genDir := filepath.Join(root, "selection-11111111-2222-3333-4444-555555555555")
	if err := os.Mkdir(genDir, 0700); err != nil {
		t.Fatal(err)
	}
	stateDir := filepath.Join(root, "state")
	traceDir := stateDir + ".invocation-trace"
	if err := os.Mkdir(traceDir, 0700); err != nil {
		t.Fatal(err)
	}
	cfg := value.Mapping(value.Field("state_dir", value.Text(stateDir)))
	tr := newTrace(filepath.Join(genDir, "memory.json"), stateTraceDir(cfg))
	tr.entry()
	tr.finish(0, "handle")
	if entries, err := os.ReadDir(genDir); err != nil || len(entries) != 0 {
		t.Fatalf("generation directory mutated: %+v %v", entries, err)
	}
	raw, err := os.ReadFile(filepath.Join(traceDir, "trace.jsonl"))
	if err != nil || len(bytes.Split(bytes.TrimSpace(raw), []byte{'\n'})) != 2 {
		t.Fatalf("expected entry and exit records in state trace dir: %q %v", raw, err)
	}
	if err := os.Mkdir(filepath.Join(genDir, "invocation-trace"), 0777); err != nil {
		t.Fatal(err)
	}
	_ = os.Chmod(filepath.Join(genDir, "invocation-trace"), 0777)
	_ = os.Remove(filepath.Join(traceDir, "trace.jsonl"))
	blocked := newTrace(filepath.Join(genDir, "memory.json"), stateTraceDir(cfg))
	blocked.entry()
	blocked.finish(0, "handle")
	if _, err := os.Stat(filepath.Join(traceDir, "trace.jsonl")); !os.IsNotExist(err) {
		t.Fatalf("unsafe primary trace dir must not fall through to fallback: %v", err)
	}
}
