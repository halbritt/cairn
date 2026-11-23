package coordination

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// censusNativeRow builds one /3-binding native Claude UserPromptSubmit row as
// stage_lifecycle_hooks.py emits it, over the real compatibility fixture.
// goOnly selects a /2 receipt (checked Go anchor, no retained launcher);
// otherwise the /1 receipt with its retained Python launcher and anchor.
func censusNativeRow(t *testing.T, role string, goOnly bool) (*watchCensusRead, WatchCensusClaudeHooks, *value.Value, *value.Value, []WatchCensusHookExecution) {
	t.Helper()
	f := newCompatFixture(t)
	raw, err := os.ReadFile(f.receiptPath)
	if err != nil {
		t.Fatal(err)
	}
	var old map[string]any
	if err := json.Unmarshal(raw, &old); err != nil {
		t.Fatal(err)
	}
	desc := func(v any) WatchCensusPin {
		m := v.(map[string]any)
		return WatchCensusPin{m["path"].(string), m["sha256"].(string)}
	}
	image := desc(old["binary"])
	memory := desc(old["memory_config"])
	binding := desc(old["binding"])
	receiptPath, receiptSHA := f.receiptPath, f.compat.ReceiptSHA256
	if goOnly {
		receiptPath, receiptSHA, _, _ = goOnlyFixture(t, f, nil)
	}
	config, timeout := f.configPath, "15"
	if role == "memory" {
		config, timeout = memory.Path, "13"
	}
	flags := []string{"--receipt", receiptPath, "--receipt-sha256", receiptSHA, "--role", role,
		"--entrypoint", role + ".py", "--config", config, "--hook-timeout", timeout,
		"--launch-root", f.launchRoot, "--shell", "/bin/sh"}
	vector := append([]string{image.Path, role}, flags...)
	body := []byte("#!/bin/sh\nexec " + strings.Join(vector, " ") + " -- \"$@\"\n")
	sum := sha256.Sum256(body)
	digest := hex.EncodeToString(sum[:])
	launcher := filepath.Join(f.launchRoot, digest, "inbox")
	writePrivateFile(t, launcher, body, 0o500)

	anchor := vector
	retained := nativeList()
	if !goOnly {
		anchor = append([]string{"/usr/bin/python3", "-I", "-S", "-c", "pass"}, flags...)
		retainedRaw, err := LauncherBytes(anchor, "/bin/sh")
		if err != nil {
			t.Fatal(err)
		}
		retainedPath := LauncherPath(f.launchRoot, retainedRaw)
		writePrivateFile(t, retainedPath, retainedRaw, 0o500)
		retained = censusRetainedStrings([]string{retainedPath})
	}
	argv := []string{launcher}
	if role == "coordination" {
		argv = append(argv, "hook", "--config", config)
	}
	command, err := shlexJoinStrings(argv)
	if err != nil {
		t.Fatal(err)
	}
	hookTimeout := 15
	if role == "memory" {
		hookTimeout = 13
	}
	row := nativeMap("role", role, "event", "UserPromptSubmit", "implementation", "go",
		"argv", censusRetainedStrings(argv), "checked_launcher", censusRetainedStrings(argv[:1]),
		"retained_launcher", retained, "hook", nativeMap("type", "command", "command", command, "timeout", hookTimeout))
	anchors := nativeMap(role+":1:"+timeout, nativeMap("vector", censusRetainedStrings(anchor)))

	coordSHA := hex.EncodeToString(func() []byte { s := sha256.Sum256(mustRead(t, f.configPath)); return s[:] }())
	selected := WatchCensusClaudeHooks{Config: WatchCensusPin{f.configPath, coordSHA}}
	node := &WatchCensusMember{Config: selected.Config, Binding: binding, Memory: memory, Mode: "cairn.inbox-recall-binding/3"}
	facts := map[string]string{}
	r := &watchCensusRead{cli: &CLI{Profile: f.profile, Host: NewFileHost(FileHostOptions{Profile: f.profile})},
		catalog: WatchCensusCatalog{Root: filepath.Dir(f.root)}, nodes: map[string]*WatchCensusMember{f.configPath: node},
		files: map[string]string{}, directories: map[string][]string{}, inodes: map[string]string{}, images: map[string]bool{},
		report: WatchUnitCensus{Facts: facts}, local: &watchDrainRead{cli: &CLI{Profile: f.profile}, facts: facts, tokenPaths: map[string]bool{}}}
	observed := []WatchCensusHookExecution{{Role: role, Event: "UserPromptSubmit", Program: image,
		Launcher: WatchCensusPin{launcher, digest}, Argv: append(append(slices.Clone(vector), "--"), argv[1:]...), PID: 42}}
	return r, selected, row, anchors, observed
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

// TestWatchCensusGoOnlyNativeRow pins the /2 census row contract: the checked
// Go anchor is the literal program vector, no retained launcher is named or
// read, and no Python member is pinned.
func TestWatchCensusGoOnlyNativeRow(t *testing.T) {
	for _, role := range []string{"memory", "coordination"} {
		for _, mutation := range []string{"none", "retained-named", "python-anchor", "no-anchor", "anchor-image", "anchor-config", "implementation", "observed-argv", "missing-execution", "closure-absent"} {
			t.Run(role+"/"+mutation, func(t *testing.T) {
				r, selected, row, anchors, observed := censusNativeRow(t, role, true)
				label := anchors.Members[0].Key
				vector, _ := hookStringVector(anchors.Get(string(label)).Get("vector"))
				switch mutation {
				case "retained-named":
					row.Set("retained_launcher", censusRetainedStrings([]string{nativeText(row.Get("argv").Items[0])}))
				case "python-anchor":
					anchors.Get(string(label)).Set("vector", censusRetainedStrings(append([]string{"/usr/bin/python3", "-I", "-S", "-c", "pass"}, vector[2:]...)))
				case "no-anchor":
					anchors.Members = nil
				case "anchor-image":
					changed := slices.Clone(vector)
					changed[0] = "/other/cairn"
					anchors.Get(string(label)).Set("vector", censusRetainedStrings(changed))
				case "anchor-config":
					changed := slices.Clone(vector)
					changed[11] = filepath.Join(r.catalog.Root, "other.json")
					anchors.Get(string(label)).Set("vector", censusRetainedStrings(changed))
				case "implementation":
					row.Set("implementation", value.Text("python"))
				case "observed-argv":
					observed[0].Argv = append(observed[0].Argv, "--extra")
				case "missing-execution":
					observed = nil
				case "closure-absent":
					// No snapshot exists for /2; removing any stray one changes nothing.
					_ = os.RemoveAll(filepath.Join(r.catalog.Root, "snapshots"))
				}
				before := censusTestSnapshot(t, r.catalog.Root)
				err := r.claudeHookRow(selected, row, observed, anchors)
				if ok := mutation == "none" || mutation == "closure-absent"; (err == nil) != ok {
					t.Fatalf("%s: %v", mutation, err)
				}
				if !reflect.DeepEqual(before, censusTestSnapshot(t, r.catalog.Root)) {
					t.Fatal("census changed selected files")
				}
				for path := range r.files {
					if strings.HasSuffix(path, ".py") {
						t.Fatalf("a /2 row pinned Python member %s", path)
					}
				}
			})
		}
	}
}

// TestWatchCensusPythonClosureNativeRowUnchanged keeps the /1 row contract:
// the retained launcher must be named, exist with its anchor bytes, and the
// closure members are pinned. A Go-shaped anchor does not satisfy /1.
func TestWatchCensusPythonClosureNativeRowUnchanged(t *testing.T) {
	for _, role := range []string{"memory", "coordination"} {
		for _, mutation := range []string{"none", "retained-empty", "retained-changed", "go-anchor"} {
			t.Run(role+"/"+mutation, func(t *testing.T) {
				r, selected, row, anchors, observed := censusNativeRow(t, role, false)
				label := string(anchors.Members[0].Key)
				switch mutation {
				case "retained-empty":
					row.Set("retained_launcher", nativeList())
				case "retained-changed":
					path := nativeText(row.Get("retained_launcher").Items[0])
					if err := os.Chmod(path, 0o700); err != nil {
						t.Fatal(err)
					}
					writePrivateFile(t, path, []byte("#!/bin/sh\nexec /tampered -- \"$@\"\n"), 0o500)
				case "go-anchor":
					anchors.Get(label).Set("vector", censusRetainedStrings(observed[0].Argv[:len(observed[0].Argv)-len(row.Get("argv").Items)]))
				}
				err := r.claudeHookRow(selected, row, observed, anchors)
				if (err == nil) != (mutation == "none") {
					t.Fatalf("%s: %v", mutation, err)
				}
				if mutation == "none" {
					pinned := 0
					for path := range r.files {
						if strings.HasSuffix(path, ".py") {
							pinned++
						}
					}
					if pinned != len(CompatibilityClosureMembers) {
						t.Fatalf("/1 row pinned %d closure members", pinned)
					}
				}
			})
		}
	}
}
