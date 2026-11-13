package coordination

import (
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"slices"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// These are literal-frame unit checks, not native refresh or authenticated D2
// evidence. The full consumed setup must retain its own observed executions.
func TestWatchCensusRetainedPythonRow(t *testing.T) {
	for _, role := range []string{"memory", "coordination"} {
		for _, mutation := range []string{"none", "row-argv", "command", "checked", "retained", "config", "script", "flags", "implementation", "missing-execution", "pid", "exit", "observed-argv", "program", "program-hash", "launcher"} {
			t.Run(role+"/"+mutation, func(t *testing.T) {
				r, selected, row, launches, observed := censusRetainedRow(t, role)
				argv, err := hookStringVector(row.Get("argv"))
				if err != nil {
					t.Fatal(err)
				}
				switch mutation {
				case "row-argv":
					row.Set("argv", censusRetainedStrings(append(argv, "--unexpected")))
				case "command":
					row.Get("hook").Set("command", value.Text("another command"))
				case "checked", "retained":
					row.Set(mutation+"_launcher", censusRetainedStrings(argv[:1]))
				case "config", "script", "flags":
					index := len(argv) - 1
					if mutation == "script" {
						index = 3
					} else if mutation == "flags" {
						index = 1
					}
					argv[index] = "wrong-selection"
					for _, field := range []string{"argv", "checked_launcher", "retained_launcher"} {
						row.Set(field, censusRetainedStrings(argv))
					}
					launches.Set(role, censusRetainedStrings(argv))
					command, err := shlexJoinStrings(argv)
					if err != nil {
						t.Fatal(err)
					}
					row.Get("hook").Set("command", value.Text(command))
					observed[0].Argv = slices.Clone(argv)
				case "implementation":
					row.Set("implementation", value.Text("go"))
				case "missing-execution":
					observed = nil
				case "pid":
					observed[0].PID = 1
				case "exit":
					observed[0].Exit = 1
				case "observed-argv":
					observed[0].Argv = append(observed[0].Argv, "--")
				case "program":
					observed[0].Program.Path = "/bin/sh"
				case "program-hash":
					observed[0].Program.SHA256 = fmt.Sprintf("%064d", 0)
					observed[0].Launcher = observed[0].Program
				case "launcher":
					observed[0].Launcher.SHA256 = fmt.Sprintf("%064d", 0)
				}
				before := censusTestSnapshot(t, r.catalog.Root)
				err = r.claudeHookRow(selected, row, observed, launches)
				if (err == nil) != (mutation == "none") {
					t.Fatalf("literal-frame mutation %s: %v", mutation, err)
				}
				if !reflect.DeepEqual(before, censusTestSnapshot(t, r.catalog.Root)) {
					t.Fatal("retained reader changed selected files")
				}
			})
		}
	}
}

func censusRetainedRow(t *testing.T, role string) (*watchCensusRead, WatchCensusClaudeHooks, *value.Value, *value.Value, []WatchCensusHookExecution) {
	t.Helper()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	_, source, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate retained source")
	}
	files := map[string]string{}
	for _, name := range []string{"memory", "coordination"} {
		raw, err := os.ReadFile(filepath.Join(filepath.Dir(source), "../../../integrations/lifecycle", name+".py"))
		if err != nil {
			t.Fatal(err)
		}
		path := filepath.Join(root, name+".py")
		files[path] = writePrivateFile(t, path, raw, 0400)
	}
	profile := func(name string) map[string]string {
		return map[string]string{"path": filepath.Join(root, name+".json"), "sha256": fmt.Sprintf("%064d", 1)}
	}
	binding := censusTestJSON(t, filepath.Join(root, "binding.json"), map[string]any{
		"schema": "cairn.inbox-recall-binding/2", "memory_config": profile("memory"), "coordination_configs": []any{profile("coordination")},
	})
	config := WatchCensusPin{Path: filepath.Join(root, "selected.json"), SHA256: profile("coordination")["sha256"]}
	node := &WatchCensusMember{Config: config, Binding: binding, Mode: "cairn.inbox-recall-binding/2"}
	facts := map[string]string{}
	prof := stage2ServicesProfile()
	cli := &CLI{Profile: prof, Host: NewFileHost(FileHostOptions{Profile: prof})}
	r := &watchCensusRead{cli: cli, catalog: WatchCensusCatalog{Root: root}, nodes: map[string]*WatchCensusMember{config.Path: node},
		files: files, directories: map[string][]string{}, inodes: map[string]string{}, images: map[string]bool{}, report: WatchUnitCensus{Facts: facts},
		local: &watchDrainRead{cli: cli, facts: facts, tokenPaths: map[string]bool{}}}
	python, err := filepath.EvalSymlinks("/usr/bin/python3")
	if err != nil {
		t.Fatal(err)
	}
	program, err := os.ReadFile(python)
	if err != nil {
		t.Fatal(err)
	}
	pin := WatchCensusPin{python, fmt.Sprintf("%x", sha256.Sum256(program))}
	argv := []string{python, "-I", "-B", filepath.Join(root, role+".py")}
	if role == "coordination" {
		argv = append(argv, "hook")
	}
	argv = append(argv, "--config", profile(role)["path"])
	command, err := shlexJoinStrings(argv)
	if err != nil {
		t.Fatal(err)
	}
	row := nativeMap("role", role, "event", "UserPromptSubmit", "implementation", "python",
		"argv", censusRetainedStrings(argv), "checked_launcher", censusRetainedStrings(argv), "retained_launcher", censusRetainedStrings(argv),
		"hook", nativeMap("command", command))
	return r, WatchCensusClaudeHooks{Config: config}, row, nativeMap(role, censusRetainedStrings(argv)),
		[]WatchCensusHookExecution{{Role: role, Event: "UserPromptSubmit", Program: pin, Launcher: pin, Argv: slices.Clone(argv), PID: 42}}
}

func censusRetainedStrings(argv []string) *value.Value {
	vector := nativeList()
	for _, part := range argv {
		vector.Items = append(vector.Items, value.Text(part))
	}
	return vector
}
