package coordination

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/google/uuid"
)

type censusFixture struct {
	root      string
	catalog   WatchCensusCatalog
	proposal  WatchCensusProposal
	configs   map[string]map[string]any
	memories  map[string]string
	manifests map[string]string
}

func censusTestJSON(t *testing.T, path string, body any) WatchCensusPin {
	t.Helper()
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	return WatchCensusPin{path, writePrivateFile(t, path, append(raw, '\n'), 0600)}
}

func newCensusFixture(t *testing.T) *censusFixture {
	t.Helper()
	return censusFixtureAt(t, t.TempDir())
}

func censusFixtureAt(t *testing.T, root string) *censusFixture {
	t.Helper()
	if err := os.MkdirAll(root, 0700); err != nil {
		t.Fatal(err)
	}
	f := &censusFixture{root: root, configs: map[string]map[string]any{}, memories: map[string]string{}, manifests: map[string]string{}}
	f.catalog = WatchCensusCatalog{Schema: "cairn.watch-unit-catalog/1", Scope: "disposable-fixture", Root: f.root,
		ConfigRoot: filepath.Join(f.root, "selections"), CallbackRoot: filepath.Join(f.root, "callbacks")}
	if err := os.MkdirAll(f.catalog.CallbackRoot, 0700); err != nil {
		t.Fatal(err)
	}
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	image, err := os.ReadFile(exe)
	if err != nil {
		t.Fatal(err)
	}
	client := filepath.Join(f.root, "client", "image")
	clientPin := WatchCensusPin{client, writePrivateFile(t, client, image, 0500)}
	f.catalog.Clients = []WatchCensusPin{clientPin}
	for _, unit := range []string{"a", "b"} {
		names := []string{"one.json", "two.json"}
		if unit == "b" {
			names = []string{"three.json"}
		}
		binding := filepath.Join(f.root, "descriptors", unit, "binding.json")
		token := filepath.Join(f.root, "credentials", unit, "fixture.token")
		writePrivateFile(t, token, []byte("synthetic unreadable fixture token"), 0600)
		if err := os.Chmod(token, 0000); err != nil {
			t.Fatal(err)
		}
		f.catalog.CredentialPaths = append(f.catalog.CredentialPaths, token)
		stateDir, memoryDir := filepath.Join(f.root, "state-"+unit), filepath.Join(f.root, "memory-"+unit)
		for _, dir := range []string{stateDir, memoryDir} {
			if err := os.MkdirAll(dir, 0700); err != nil {
				t.Fatal(err)
			}
		}
		censusTestJSON(t, filepath.Join(memoryDir, "retained.memory-budget.json"), map[string]any{"inbox_activation": map[string]bool{"old-turn": false}, "opaque": "preserved"})
		common := map[string]any{"harness": "codex", "cairn": client, "socket": filepath.Join(f.root, "unused.sock"), "token_file": token, "repo": "fixture:census:" + unit, "inbox_recall_binding": binding}
		memory := map[string]any{}
		for k, v := range common {
			memory[k] = v
		}
		memory["state_dir"] = memoryDir
		memory["recall_mode"] = "agent_tools"
		memory["context_bytes"] = 9500
		memoryPin := censusTestJSON(t, filepath.Join(f.root, "descriptors", unit, "memory.json"), memory)
		f.memories[unit] = memoryPin.Path
		selection := WatchBindings{Directory: filepath.Join(f.catalog.ConfigRoot, unit), Pins: map[string]string{}}
		var descriptors []WatchCensusPin
		for _, name := range names {
			config := map[string]any{}
			for k, v := range common {
				config[k] = v
			}
			config["state_dir"] = stateDir
			config["native_delivery"] = true
			config["binding"] = unit + "-" + name
			config["config_home"] = filepath.Join(f.root, "accounts", unit+"-"+name)
			if err := os.MkdirAll(config["config_home"].(string), 0700); err != nil {
				t.Fatal(err)
			}
			immutable := censusTestJSON(t, filepath.Join(f.root, "descriptors", unit, name), config)
			descriptors = append(descriptors, immutable)
			active := censusTestJSON(t, filepath.Join(selection.Directory, name), config)
			selection.Pins[name] = active.SHA256
			f.configs[active.Path] = config
			native := "fixture-native-" + unit + name
			state := map[string]any{"schema": NativeSessionSchema, "native_session_id": native, "agent": map[string]any{"agent_id": uuid.NewString(), "execution_id": uuid.NewString()}, "inbox_journals": []string{}}
			sessionPin := censusTestJSON(t, SessionStatePath(stateDir, config["binding"].(string), native), state)
			f.catalog.Sessions = append(f.catalog.Sessions, sessionPin)
			if unit == "a" {
				f.proposal.Sessions = append(f.proposal.Sessions, sessionPin.Path)
			}
		}
		binary := filepath.Join(f.root, "runtime-"+unit, "image")
		binaryPin := WatchCensusPin{binary, writePrivateFile(t, binary, image, 0500)}
		// Manifest descriptors use the existing lower-case profile wire fields.
		coords := []map[string]string{}
		for _, pin := range descriptors {
			coords = append(coords, map[string]string{"path": pin.Path, "sha256": pin.SHA256})
		}
		manifest := map[string]any{"schema": "cairn.inbox-recall-binding/3", "enabled": false,
			"memory_config": map[string]string{"path": memoryPin.Path, "sha256": memoryPin.SHA256}, "coordination_configs": coords,
			"binary": map[string]string{"path": binaryPin.Path, "sha256": binaryPin.SHA256}}
		f.catalog.Bindings = append(f.catalog.Bindings, censusTestJSON(t, binding, manifest))
		f.manifests[unit] = binding
		f.catalog.Selections = append(f.catalog.Selections, selection)
		if unit == "a" {
			f.proposal.Bindings = append(f.proposal.Bindings, selection)
		}
	}
	return f
}

func (f *censusFixture) inspect(t *testing.T) (WatchUnitCensus, error) {
	t.Helper()
	pin := censusTestJSON(t, filepath.Join(f.root, "catalog.json"), f.catalog)
	profile := stage2ServicesProfile()
	cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile})}
	return cli.InspectWatchUnitCensus(context.Background(), pin, f.proposal)
}

func censusTestSnapshot(t *testing.T, root string) map[string]string {
	t.Helper()
	out := map[string]string{}
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() || filepath.Base(path) == "catalog.json" {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if info.Mode().Perm() == 0 {
			out[path] = "unreadable-credential-metadata"
			return nil
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		sum := sha256.Sum256(raw)
		out[path] = hex.EncodeToString(sum[:])
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func TestWatchCensusClosedTwoBindingUnit(t *testing.T) {
	f := newCensusFixture(t)
	before := censusTestSnapshot(t, f.root)
	got, err := f.inspect(t)
	if err != nil || !got.FixtureReady || len(got.Members) != 2 || len(got.Sessions) != 2 {
		t.Fatalf("census=%+v: %v", got, err)
	}
	if got.ExternalCoverage != "unknown" || got.NativeRefresh != "unobserved" {
		t.Fatal("fixture claimed production closure/refresh")
	}
	for _, member := range got.Members {
		if filepath.Dir(member.Config.Path) != f.catalog.Selections[0].Directory {
			t.Fatal("disjoint unit was included")
		}
	}
	again, err := f.inspect(t)
	if err != nil || again.Digest != got.Digest {
		t.Fatalf("nondeterministic census: %v", err)
	}
	if !reflect.DeepEqual(before, censusTestSnapshot(t, f.root)) {
		t.Fatal("read-only census changed fixture state or bytes")
	}
}

func (f *censusFixture) share(t *testing.T, kind string) {
	t.Helper()
	raw, err := os.ReadFile(f.manifests["b"])
	if err != nil {
		t.Fatal(err)
	}
	manifest := map[string]any{}
	if err := json.Unmarshal(raw, &manifest); err != nil {
		t.Fatal(err)
	}
	if kind == "ledger" {
		raw, err := os.ReadFile(f.memories["b"])
		if err != nil {
			t.Fatal(err)
		}
		memory := map[string]any{}
		if err := json.Unmarshal(raw, &memory); err != nil {
			t.Fatal(err)
		}
		memory["state_dir"] = filepath.Join(f.root, "memory-a")
		pin := censusTestJSON(t, f.memories["b"], memory)
		manifest["memory_config"] = map[string]string{"path": pin.Path, "sha256": pin.SHA256}
	} else {
		path := filepath.Join(f.root, "runtime-a", "image")
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(raw)
		manifest["binary"] = map[string]string{"path": path, "sha256": hex.EncodeToString(sum[:])}
	}
	pin := censusTestJSON(t, f.manifests["b"], manifest)
	for i := range f.catalog.Bindings {
		if f.catalog.Bindings[i].Path == pin.Path {
			f.catalog.Bindings[i] = pin
		}
	}
}

func TestWatchCensusSharedUsersRequireUnion(t *testing.T) {
	for _, kind := range []string{"ledger", "runtime"} {
		t.Run(kind, func(t *testing.T) {
			f := newCensusFixture(t)
			f.share(t, kind)
			got, err := f.inspect(t)
			if err == nil || got.FixtureReady || len(got.RequiredBindings) != 3 {
				t.Fatalf("half switch accepted: %+v %v", got, err)
			}
			f.proposal.Bindings = slices.Clone(f.catalog.Selections)
			f.proposal.Sessions = nil
			for _, pin := range f.catalog.Sessions {
				f.proposal.Sessions = append(f.proposal.Sessions, pin.Path)
			}
			got, err = f.inspect(t)
			if err != nil || !got.FixtureReady || len(got.Members) != 3 {
				t.Fatalf("union refused: %+v %v", got, err)
			}
		})
	}
}

func TestWatchCensusUnknownOmittedAndUnsafeMembersRefuse(t *testing.T) {
	cases := []struct {
		name, want string
		change     func(*testing.T, *censusFixture)
	}{
		{"extra-binding", "membership", func(t *testing.T, f *censusFixture) {
			censusTestJSON(t, filepath.Join(f.catalog.Selections[0].Directory, "extra.json"), map[string]string{"unknown": "selected user"})
		}},
		{"omitted-directory", "omitted selected config directory", func(t *testing.T, f *censusFixture) { f.catalog.Selections = f.catalog.Selections[:1] }},
		{"missing-binding-pin", "binding pin", func(t *testing.T, f *censusFixture) { f.catalog.Bindings = f.catalog.Bindings[:1] }},
		{"changed-binding-pin", "binding pin", func(t *testing.T, f *censusFixture) { f.catalog.Bindings[0].SHA256 = strings.Repeat("0", 64) }},
		{"changed-config-pin", "config pin", func(t *testing.T, f *censusFixture) {
			p := filepath.Join(f.catalog.Selections[0].Directory, "one.json")
			raw, err := os.ReadFile(p)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(p, append(raw, ' '), 0600); err != nil {
				t.Fatal(err)
			}
		}},
		{"changed-memory-pin", "descriptor changed", func(t *testing.T, f *censusFixture) {
			censusTestJSON(t, f.memories["a"], map[string]string{"changed": "immutable memory descriptor"})
		}},
		{"extra-session", "selected session", func(t *testing.T, f *censusFixture) {
			config := f.configs[filepath.Join(f.catalog.Selections[0].Directory, "one.json")]
			native := "unexpected-selected-session"
			censusTestJSON(t, SessionStatePath(config["state_dir"].(string), config["binding"].(string), native), map[string]any{"schema": NativeSessionSchema, "native_session_id": native, "agent": map[string]string{"agent_id": uuid.NewString(), "execution_id": uuid.NewString()}})
		}},
		{"omitted-proposed-session", "session coverage", func(t *testing.T, f *censusFixture) { f.proposal.Sessions = f.proposal.Sessions[:1] }},
		{"extra-callback-reference", "callback reference", func(t *testing.T, f *censusFixture) {
			censusTestJSON(t, filepath.Join(f.catalog.CallbackRoot, "unexpected.json"), map[string]string{"unobserved": "callback"})
		}},
		{"extra-proposed-callback", "callback coverage", func(t *testing.T, f *censusFixture) {
			f.proposal.Callbacks = []string{filepath.Join(f.catalog.CallbackRoot, "not-observed.json")}
		}},
		{"external-unknown", "external observation coverage", func(t *testing.T, f *censusFixture) { f.catalog.Scope = "external-unknown" }},
		{"unsafe-directory-alias", "alias", func(t *testing.T, f *censusFixture) {
			alias := filepath.Join(f.root, "aliased-selections")
			if err := os.Symlink(f.catalog.ConfigRoot, alias); err != nil {
				t.Fatal(err)
			}
			f.catalog.ConfigRoot = alias
			for i := range f.catalog.Selections {
				f.catalog.Selections[i].Directory = filepath.Join(alias, filepath.Base(f.catalog.Selections[i].Directory))
			}
		}},
		{"credential-overlap", "credential selector", func(t *testing.T, f *censusFixture) {
			f.catalog.CredentialPaths = append(f.catalog.CredentialPaths, f.memories["a"])
		}},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			f := newCensusFixture(t)
			test.change(t, f)
			got, err := f.inspect(t)
			if err == nil || got.FixtureReady || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("wanted %q refusal, got ready=%v err=%v", test.want, got.FixtureReady, err)
			}
		})
	}
}

func TestWatchCensusPositiveGrantRemainsVisible(t *testing.T) {
	f := newCensusFixture(t)
	path := filepath.Join(f.root, "memory-a", "retained.memory-budget.json")
	censusTestJSON(t, path, map[string]any{"inbox_activation": map[string]bool{"old-turn": true}, "opaque": "preserved"})
	before := censusTestSnapshot(t, f.root)
	got, err := f.inspect(t)
	if err == nil || got.FixtureReady || !slices.Equal(got.PositiveGrants, []string{path + "#old-turn"}) {
		t.Fatalf("positive grant hidden: %+v %v", got, err)
	}
	if !reflect.DeepEqual(before, censusTestSnapshot(t, f.root)) {
		t.Fatal("inspector changed or deleted a positive grant")
	}
}

func TestWatchCensusNoProfileOrCanceledContextRefuses(t *testing.T) {
	var cli *CLI
	if _, err := cli.InspectWatchUnitCensus(context.Background(), WatchCensusPin{}, WatchCensusProposal{}); err == nil {
		t.Fatal("nil inspector accepted")
	}
	f := newCensusFixture(t)
	pin := censusTestJSON(t, filepath.Join(f.root, "catalog.json"), f.catalog)
	profile := stage2ServicesProfile()
	cli = &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile})}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := cli.InspectWatchUnitCensus(ctx, pin, f.proposal); err == nil || got.FixtureReady {
		t.Fatal("canceled census accepted")
	}
}
