package coordination

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"slices"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func bootstrapFixture(t *testing.T) (*grantCurrentnessFixture, *UnitBootstrapPlan, string, string) {
	t.Helper()
	f := newGrantCurrentnessFixture(t, "codex")
	token := filepath.Join(f.root, "fixture.token")
	writePrivateFile(t, token, []byte("fixture credential canary; must not be read by metadata inspector"), 0600)
	p := &UnitBootstrapPlan{Schema: "cairn.unit-bootstrap/1", Root: f.root, CredentialSelectors: []string{token}, Callbacks: json.RawMessage("[]"), Archives: []BootstrapMember{}}
	p.Current.Directory, p.Current.Pins = f.selected.Directory, f.selected.Pins
	for i := range 17 {
		path := filepath.Join(f.selected.Directory, fmt.Sprintf("member-%02d.before-native-inbox", i))
		writePrivateFile(t, path, []byte("opaque archival canary; must never become an active profile"), 0600)
		member, _, err := bootstrapStat(path)
		if err != nil {
			t.Fatal(err)
		}
		p.Archives = append(p.Archives, member)
	}
	var err error
	p.WatchLock, _, err = bootstrapStat(WatcherLockPath(f.selected.Directory))
	if err != nil {
		t.Fatal(err)
	}
	path, pin := writeBootstrapPlan(t, f.root, p)
	return f, p, path, pin
}

func bootstrapEffectFixture(t *testing.T) (*grantCurrentnessFixture, *UnitBootstrapPlan, string, string, *selectedFenceFixture) {
	t.Helper()
	f, p, _, _ := bootstrapFixture(t)
	p.Schema = "cairn.unit-bootstrap/2"
	sourceDir := filepath.Join(f.root, "retained-python")
	if err := os.Mkdir(sourceDir, 0700); err != nil {
		t.Fatal(err)
	}
	_, source, _, _ := runtime.Caller(0)
	files := value.Mapping()
	for _, name := range CompatibilityClosureMembers {
		if name == "lifecycle_compatibility.py" {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(filepath.Dir(source), "../../../integrations/lifecycle", name))
		if err != nil {
			t.Fatal(err)
		}
		files.Set(name, value.Text(writePrivateFile(t, filepath.Join(sourceDir, name), raw, 0400)))
	}
	digest, err := canonicalSortedJSONSHA256(files)
	if err != nil {
		t.Fatal(err)
	}
	bundle := nativeMap("schema", "cairn.lifecycle-bundle/1", "sha256", digest, "files", files)
	manifestPath := filepath.Join(f.root, "binding.json")
	raw, err := os.ReadFile(manifestPath)
	if err != nil {
		t.Fatal(err)
	}
	manifest, err := value.DecodeBytes(raw, 0)
	if err != nil {
		t.Fatal(err)
	}
	account := filepath.Join(f.root, "account")
	if err := os.Mkdir(account, 0700); err != nil {
		t.Fatal(err)
	}
	for _, profilePath := range []string{filepath.Join(f.root, "coord.json"), filepath.Join(f.root, "memory.json"), filepath.Join(p.Current.Directory, "one.json")} {
		raw, err := os.ReadFile(profilePath)
		if err != nil {
			t.Fatal(err)
		}
		profile, err := value.DecodeBytes(raw, 0)
		if err != nil {
			t.Fatal(err)
		}
		profile.Set("config_home", value.Text(account))
		sha := writePrivateFile(t, profilePath, mustCurrentnessJSON(t, profile), 0600)
		switch profilePath {
		case filepath.Join(f.root, "coord.json"):
			manifest.Get("coordination_configs").Items[0].Set("sha256", value.Text(sha))
		case filepath.Join(f.root, "memory.json"):
			manifest.Get("memory_config").Set("sha256", value.Text(sha))
		default:
			p.Current.Pins["one.json"] = sha
		}
	}
	manifest.Set("engine", nativeMap("path", filepath.Join(sourceDir, "memory.py"), "sha256", nativeText(files.Get("memory.py")), "bundle", bundle))
	manifest.Set("bridge", nativeMap("path", filepath.Join(sourceDir, "inbox_recall.py"), "sha256", nativeText(files.Get("inbox_recall.py")), "bundle", bundle))
	manifestSHA := writePrivateFile(t, manifestPath, mustCurrentnessJSON(t, manifest), 0600)
	argvPath := filepath.Join(f.root, "retained-argv.json")
	argv := map[string][]string{"memory": {"/usr/bin/python3.11", "-I", "-B", filepath.Join(sourceDir, "memory.py"), "--config", filepath.Join(f.root, "memory.json")}, "coordination": {"/usr/bin/python3.11", "-I", "-B", filepath.Join(sourceDir, "coordination.py"), "hook", "--config", filepath.Join(f.root, "coord.json")}}
	encoded, err := json.Marshal(argv)
	if err != nil {
		t.Fatal(err)
	}
	argvSHA := writePrivateFile(t, argvPath, encoded, 0400)
	p.Associations = map[string]BootstrapRetainedAssociation{"one.json": {Binding: WatchCensusPin{Path: manifestPath, SHA256: manifestSHA}, Argv: WatchCensusPin{Path: argvPath, SHA256: argvSHA}}}
	target := *p
	target.Archives = []BootstrapMember{}
	target.Transition = nil
	target.Current.Directory = filepath.Join(f.root, "prepared-bindings")
	target.Current.Pins = map[string]string{}
	if err := os.Mkdir(target.Current.Directory, 0700); err != nil {
		t.Fatal(err)
	}
	for name := range p.Current.Pins {
		raw, err := os.ReadFile(filepath.Join(p.Current.Directory, name))
		if err != nil {
			t.Fatal(err)
		}
		target.Current.Pins[name] = writePrivateFile(t, filepath.Join(target.Current.Directory, name), append([]byte("\n"), raw...), 0400)
	}
	if err := os.Link(WatcherLockPath(p.Current.Directory), WatcherLockPath(target.Current.Directory)); err != nil {
		t.Fatal(err)
	}
	bootstrapCurrent3Fixture(t, f, &target)
	targetPath := filepath.Join(f.root, "target-plan.json")
	encoded, err = json.Marshal(target)
	if err != nil {
		t.Fatal(err)
	}
	targetSHA := writePrivateFile(t, targetPath, encoded, 0600)
	originalIdentity, err := bootstrapDirectory(p.Current.Directory)
	if err != nil {
		t.Fatal(err)
	}
	targetIdentity, err := bootstrapDirectory(target.Current.Directory)
	if err != nil {
		t.Fatal(err)
	}
	fence := newSelectedFenceFixture(t)
	fence.scope.Database = ""
	fence.scope.DatabaseSystemID = ""
	fence.host.dbRead = nil
	p.Transition = &BootstrapTransition{Target: WatchCensusPin{Path: targetPath, SHA256: targetSHA}, OriginalIdentity: originalIdentity, TargetIdentity: targetIdentity, Fence: fence.scope}
	path, pin := writeBootstrapPlan(t, f.root, p)
	return f, p, path, pin, fence
}

func bootstrapCurrent3Fixture(t *testing.T, f *grantCurrentnessFixture, target *UnitBootstrapPlan) {
	t.Helper()
	root := filepath.Join(f.root, "current3")
	if err := os.Mkdir(root, 0700); err != nil {
		t.Fatal(err)
	}
	bindingPath := filepath.Join(root, "binding.json")
	profiles := map[string]WatchCensusPin{}
	for _, role := range []string{"memory", "coord"} {
		raw, err := os.ReadFile(filepath.Join(f.root, role+".json"))
		if err != nil {
			t.Fatal(err)
		}
		config, err := value.DecodeBytes(raw, 0)
		if err != nil {
			t.Fatal(err)
		}
		config.Set("inbox_recall_binding", value.Text(bindingPath))
		path := filepath.Join(root, role+".json")
		profiles[role] = WatchCensusPin{Path: path, SHA256: writePrivateFile(t, path, mustCurrentnessJSON(t, config), 0400)}
		if role == "coord" {
			target.Current.Pins["one.json"] = writePrivateFile(t, filepath.Join(target.Current.Directory, "one.json"), mustCurrentnessJSON(t, config), 0400)
		}
	}
	binaryPath := filepath.Join(f.root, "client")
	binarySHA, err := compatibilityOpenedBinary(binaryPath, uint32(os.Getuid()))
	if err != nil {
		t.Fatal(err)
	}
	toValue := func(pin WatchCensusPin) *value.Value { return nativeMap("path", pin.Path, "sha256", pin.SHA256) }
	bin := nativeMap("path", binaryPath, "sha256", binarySHA)
	coords := nativeList(toValue(profiles["coord"]))
	manifest := nativeMap("schema", "cairn.inbox-recall-binding/3", "enabled", false, "binary", bin, "memory_config", toValue(profiles["memory"]), "coordination_configs", coords)
	bindingSHA := writePrivateFile(t, bindingPath, mustCurrentnessJSON(t, manifest), 0400)
	_, source, _, _ := runtime.Caller(0)
	hashes := map[string]string{}
	bodies := map[string][]byte{}
	for _, name := range CompatibilityClosureMembers {
		raw, err := os.ReadFile(filepath.Join(filepath.Dir(source), "../../../integrations/lifecycle", name))
		if err != nil {
			t.Fatal(err)
		}
		bodies[name] = raw
		path := filepath.Join(root, "source-inputs", name)
		hashes[name] = writePrivateFile(t, path, raw, 0400)
	}
	snapshot := filepath.Join(root, CompatibilitySnapshotIdentity(hashes))
	members := value.Mapping()
	for name, raw := range bodies {
		writePrivateFile(t, filepath.Join(snapshot, name), raw, 0400)
		members.Set(name, nativeMap("sha256", hashes[name], "bytes", len(raw)))
	}
	receipt := nativeMap("schema", "cairn.lifecycle-compatibility/1", "source_revision", "owned unit fixture; no runtime qualification", "snapshot_root", snapshot, "members", members,
		"bootstrap", "lifecycle_compatibility.py", "loader", "lifecycle_bundle.py", "roles", nativeMap("memory", "memory.py", "coordination", "coordination.py", "inbox_recall", "inbox_recall.py", "inbox_binding", "inbox_binding.py"), "aliases", value.Mapping(),
		"binary", bin, "binding", nativeMap("path", bindingPath, "sha256", bindingSHA, "schema", "cairn.inbox-recall-binding/3"), "memory_config", toValue(profiles["memory"]), "coordination_configs", coords)
	receiptPath := filepath.Join(root, "receipt.json")
	receiptPin := WatchCensusPin{Path: receiptPath, SHA256: writePrivateFile(t, receiptPath, mustCurrentnessJSON(t, receipt), 0400)}
	target.Associations = map[string]BootstrapRetainedAssociation{"one.json": {Binding: WatchCensusPin{Path: bindingPath, SHA256: bindingSHA}, Receipt: &receiptPin}}
}

func TestUnitBootstrapEffectfulRoundTrip(t *testing.T) {
	f, p, path, pin, fence := bootstrapEffectFixture(t)
	original := bootstrapPreservedMetadata(t, p.Current.Directory)
	var forward UnitBootstrapResult
	if err := f.cli.bootstrapMutation(context.Background(), path, pin, "switch", &forward, fence.capture(), nil); err != nil {
		t.Fatal(err)
	}
	if !forward.Published || !forward.Rechecked || forward.DatabaseCoverage != "not-selected" || forward.OwnerFence != "selected-units-only" {
		t.Fatalf("missing real exchange: %+v", forward)
	}
	var reverse UnitBootstrapResult
	if err := f.cli.bootstrapMutation(context.Background(), path, pin, "reverse", &reverse, fence.capture(), nil); err != nil {
		t.Fatal(err)
	}
	if !reverse.Published || reverse.Reverse != "original restored; forward selection retained" || !reflect.DeepEqual(original, bootstrapPreservedMetadata(t, p.Current.Directory)) {
		t.Fatal("reverse did not restore exact original directory members")
	}
	if _, err := os.Stat(filepath.Join(p.Current.Directory, p.Archives[0].Name)); err != nil {
		t.Fatal("original opaque archive lost")
	}
}

func TestUnitBootstrapInterruptedExchangeHasExplicitRollback(t *testing.T) {
	for _, phase := range []string{"before:exchange", "after:exchange", "before:parent-sync", "after:parent-sync"} {
		t.Run(phase, func(t *testing.T) {
			f, p, path, pin, fence := bootstrapEffectFixture(t)
			before := bootstrapPreservedMetadata(t, p.Current.Directory)
			var result UnitBootstrapResult
			err := f.cli.bootstrapMutation(context.Background(), path, pin, "switch", &result, fence.capture(), func(at string) error {
				if at == phase {
					return fmt.Errorf("injected owned fixture failure")
				}
				return nil
			})
			if err == nil {
				t.Fatal("injected failure was ignored")
			}
			var rollback UnitBootstrapResult
			if err := f.cli.bootstrapMutation(context.Background(), path, pin, "rollback", &rollback, fence.capture(), nil); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(before, bootstrapPreservedMetadata(t, p.Current.Directory)) {
				t.Fatal("rollback lost original bytes/metadata")
			}
			if rollback.Published != result.Published {
				t.Fatal("rollback confused pre/post publication")
			}
		})
	}
}

func bootstrapPreservedMetadata(t *testing.T, root string) map[string]string {
	t.Helper()
	rows := bootstrapTreeMetadata(t, root)
	// Directory rename changes ctime; it does not change inode, file mode,
	// owner, size or mtime. The existing read-only observer keeps testing ctime.
	for path, row := range rows {
		rows[path] = row[:strings.LastIndex(row, ":")]
	}
	return rows
}

func TestUnitBootstrapStateAncestorRefusesBeforeWalk(t *testing.T) {
	f, _, path, pin := bootstrapFixture(t)
	raw, err := os.ReadFile(filepath.Join(f.root, "coord.json"))
	if err != nil {
		t.Fatal(err)
	}
	config, err := value.DecodeBytes(raw, 0)
	if err != nil {
		t.Fatal(err)
	}
	directory := nativeText(config.Get("state_dir"))
	parked := directory + "-parked"
	if err := os.Rename(directory, parked); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(parked, directory); err != nil {
		t.Fatal(err)
	}
	if _, err := f.cli.captureBootstrapDrain(context.Background(), path, pin); err == nil || !strings.Contains(err.Error(), "unsafe path/alias") {
		t.Fatal("state symlink was traversed", err)
	}
	if f.reads != 0 {
		t.Fatal("unsafe state ancestry reached authenticated API")
	}
}

func TestUnitBootstrapEffectfulRefusals(t *testing.T) {
	for _, change := range []string{"copied-fence", "zero-fence", "fence-scope", "database-selection", "fence-drift", "target-writable", "archive-drift", "bundle-drift", "held-lock", "lock-as-archive", "D6-api-drift", "D6-fence-drift"} {
		t.Run(change, func(t *testing.T) {
			f, p, path, pin, ff := bootstrapEffectFixture(t)
			receipt := ff.capture()
			var observe func(string) error
			switch change {
			case "copied-fence":
				copy := *receipt
				receipt = &copy
			case "zero-fence":
				receipt = &SelectedUnitFenceReceipt{}
			case "fence-scope":
				p.Transition.Fence.Units = []string{"another.service"}
				path, pin = writeBootstrapPlan(t, f.root, p)
			case "database-selection":
				p.Transition.Fence.Database = "foreign"
				path, pin = writeBootstrapPlan(t, f.root, p)
			case "fence-drift":
				ff.units["selected.service"]["ActiveState"] = "active"
			case "target-writable":
				if err := os.Chmod(filepath.Join(f.root, "prepared-bindings/one.json"), 0600); err != nil {
					t.Fatal(err)
				}
			case "archive-drift":
				if err := os.Chmod(filepath.Join(p.Current.Directory, p.Archives[0].Name), 0400); err != nil {
					t.Fatal(err)
				}
			case "bundle-drift":
				writePrivateFile(t, filepath.Join(f.root, "retained-python/memory.py"), []byte("changed generation"), 0400)
			case "held-lock":
				lock, err := AcquireWatcherLock(p.Current.Directory)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { mustRelease(t, lock) })
			case "lock-as-archive":
				p.Archives = append(p.Archives, p.WatchLock)
				p.WatchLock = BootstrapMember{Name: ".watch.lock", Type: "absent"}
				path, pin = writeBootstrapPlan(t, f.root, p)
			case "D6-api-drift":
				observe = func(phase string) error {
					if phase == "before:exchange" {
						f.remote.Set("fixture_drift", value.Text("changed"))
					}
					return nil
				}
			case "D6-fence-drift":
				observe = func(phase string) error {
					if phase == "before:exchange" {
						ff.units["selected.service"]["StateChangeTimestampMonotonic"] = "101"
					}
					return nil
				}
			}
			before := bootstrapTreeMetadata(t, p.Current.Directory)
			var result UnitBootstrapResult
			if err := f.cli.bootstrapMutation(context.Background(), path, pin, "switch", &result, receipt, observe); err == nil {
				t.Fatal("unknown/drift/copy acquired publication")
			}
			if result.Published || !reflect.DeepEqual(before, bootstrapTreeMetadata(t, p.Current.Directory)) {
				t.Fatal("refusal changed original selection")
			}
		})
	}
}

func TestUnitBootstrapStrictTransitionFields(t *testing.T) {
	f, p, _, _, _ := bootstrapEffectFixture(t)
	raw, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	for _, change := range []string{"Fence", "uid", "stopped", "SHA256-alias"} {
		t.Run(change, func(t *testing.T) {
			bad := raw
			switch change {
			case "Fence":
				bad = bytes.Replace(bad, []byte(`"fence":`), []byte(`"Fence":`), 1)
			case "uid":
				bad = bytes.Replace(bad, []byte(`"UID":`), []byte(`"uid":`), 1)
			case "stopped":
				bad = bytes.Replace(bad, []byte(`"fence":{`), []byte(`"fence":{"stopped":true,`), 1)
			case "SHA256-alias":
				bad = bytes.Replace(bad, []byte(`"SHA256":`), []byte(`"sha256":`), 1)
			}
			path := filepath.Join(f.root, "bad-"+change+".json")
			pin := writePrivateFile(t, path, bad, 0600)
			if _, _, err := readUnitBootstrapPlan(path, pin); err == nil {
				t.Fatal("unknown/case-aliased transition field accepted")
			}
		})
	}
}

func TestUnitBootstrapParentDescriptorRaceRefusesBeforeRename(t *testing.T) {
	f, p, path, pin, ff := bootstrapEffectFixture(t)
	parked := f.root + "-parked"
	t.Cleanup(func() { _ = os.RemoveAll(parked) })
	var result UnitBootstrapResult
	err := f.cli.bootstrapMutation(context.Background(), path, pin, "switch", &result, ff.capture(), func(phase string) error {
		if phase != "before:rename" {
			return nil
		}
		if err := os.Rename(f.root, parked); err != nil {
			return err
		}
		if err := os.Mkdir(f.root, 0700); err != nil {
			return err
		}
		entries, err := os.ReadDir(parked)
		if err != nil {
			return err
		}
		for _, entry := range entries {
			if err := os.Rename(filepath.Join(parked, entry.Name()), filepath.Join(f.root, entry.Name())); err != nil {
				return err
			}
		}
		for _, name := range []string{"bindings", "prepared-bindings"} {
			directory := filepath.Join(parked, name)
			if err := os.Mkdir(directory, 0700); err != nil {
				return err
			}
			writePrivateFile(t, filepath.Join(directory, "canary"), []byte(name), 0600)
		}
		return nil
	})
	if err == nil || result.Published || !strings.Contains(err.Error(), "opened parent identity changed") {
		t.Fatal("stale parent descriptor published unrelated entries", err)
	}
	if err := bootstrapIdentityAt(p.Current.Directory, p.Transition.OriginalIdentity); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"bindings", "prepared-bindings"} {
		raw, err := os.ReadFile(filepath.Join(parked, name, "canary"))
		if err != nil || string(raw) != name {
			t.Fatal("unrelated parked canary was exchanged")
		}
	}
}

func TestUnitBootstrapFinalFenceRefusesAfterOperandPreparation(t *testing.T) {
	for _, operation := range []string{"switch", "reverse"} {
		for _, drift := range []string{"reactivation", "generation"} {
			t.Run(operation+"/"+drift, func(t *testing.T) {
				f, p, path, pin, ff := bootstrapEffectFixture(t)
				if operation == "reverse" {
					var forward UnitBootstrapResult
					if err := f.cli.bootstrapMutation(context.Background(), path, pin, "switch", &forward, ff.capture(), nil); err != nil {
						t.Fatal(err)
					}
				}
				before := bootstrapTreeMetadata(t, p.Current.Directory)
				injected := false
				var result UnitBootstrapResult
				err := f.cli.bootstrapMutation(context.Background(), path, pin, operation, &result, ff.capture(), func(phase string) error {
					if phase == "before:commit" {
						injected = true
						if drift == "reactivation" {
							ff.units["selected.service"]["ActiveState"] = "active"
							ff.units["selected.service"]["SubState"] = "running"
						} else {
							ff.units["selected.service"]["StateChangeTimestampMonotonic"] = "101"
						}
					}
					return nil
				})
				if !injected || err == nil || result.Published || !reflect.DeepEqual(before, bootstrapTreeMetadata(t, p.Current.Directory)) {
					t.Fatalf("late %s was not refused before %s namespace exchange: injected=%v published=%v err=%v", drift, operation, injected, result.Published, err)
				}
			})
		}
	}
}

func bootstrapExternalMemoryFixture(t *testing.T) (*grantCurrentnessFixture, *UnitBootstrapPlan, string, string, *selectedFenceFixture) {
	t.Helper()
	f, p, _, _, ff := bootstrapEffectFixture(t)
	parent := filepath.Join(privateCurrentnessDirectory(t), ".hermes", "cairn")
	if err := os.MkdirAll(parent, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(parent, 0755); err != nil {
		t.Fatal(err)
	}
	directory := filepath.Join(parent, "state")
	if err := os.Rename(f.memory, directory); err != nil {
		t.Fatal(err)
	}
	f.memory = directory
	memoryPath := filepath.Join(f.root, "memory.json")
	raw, err := os.ReadFile(memoryPath)
	if err != nil {
		t.Fatal(err)
	}
	memory, err := value.DecodeBytes(raw, 0)
	if err != nil {
		t.Fatal(err)
	}
	memory.Set("state_dir", value.Text(directory))
	sha := writePrivateFile(t, memoryPath, mustCurrentnessJSON(t, memory), 0600)
	association := p.Associations["one.json"]
	raw, err = os.ReadFile(association.Binding.Path)
	if err != nil {
		t.Fatal(err)
	}
	manifest, err := value.DecodeBytes(raw, 0)
	if err != nil {
		t.Fatal(err)
	}
	manifest.Get("memory_config").Set("sha256", value.Text(sha))
	association.Binding.SHA256 = writePrivateFile(t, association.Binding.Path, mustCurrentnessJSON(t, manifest), 0600)
	p.Associations["one.json"] = association
	identity, err := bootstrapDirectory(directory)
	if err != nil {
		t.Fatal(err)
	}
	p.ExternalStates = []BootstrapExternalState{{Member: "one.json", Role: "memory", Directory: directory, Identity: identity}}
	target := bootstrapTargetPlan(t, p)
	rewriteBootstrapTargetProfiles(t, target, func(isMemory bool, profile map[string]any) {
		if isMemory {
			profile["state_dir"] = directory
		}
	})
	target.ExternalStates = slices.Clone(p.ExternalStates)
	raw, err = json.Marshal(target)
	if err != nil {
		t.Fatal(err)
	}
	p.Transition.Target.SHA256 = writePrivateFile(t, p.Transition.Target.Path, raw, 0600)
	path, pin := writeBootstrapPlan(t, f.root, p)
	return f, p, path, pin, ff
}

func TestUnitBootstrapDeclaredExternalStateRoundTrip(t *testing.T) {
	f, p, path, pin, ff := bootstrapExternalMemoryFixture(t)
	before := bootstrapTreeMetadata(t, f.memory)
	identity := p.ExternalStates[0].Identity
	for _, operation := range []string{"switch", "reverse"} {
		var result UnitBootstrapResult
		if err := f.cli.bootstrapMutation(context.Background(), path, pin, operation, &result, ff.capture(), nil); err != nil {
			t.Fatal(operation, err)
		}
		if !result.Published || !reflect.DeepEqual(before, bootstrapTreeMetadata(t, f.memory)) {
			t.Fatal("external state was changed or transition not published", operation)
		}
		if err := bootstrapIdentityAt(f.memory, identity); err != nil {
			t.Fatal(err)
		}
	}
}

func TestUnitBootstrapExternalStateRefusals(t *testing.T) {
	for _, change := range []string{"undeclared", "duplicate", "profile", "role", "identity", "foreign-identity", "0755", "0775", "special-mode", "symlink", "unsafe-parent", "root-overlap", "account-overlap", "binary-overlap", "other-state-overlap", "unsettled-grant", "target-identity"} {
		t.Run(change, func(t *testing.T) {
			f, p, _, _, ff := bootstrapExternalMemoryFixture(t)
			directory := p.ExternalStates[0].Directory
			switch change {
			case "undeclared":
				p.ExternalStates = nil
			case "duplicate":
				p.ExternalStates = append(p.ExternalStates, p.ExternalStates[0])
			case "profile":
				p.ExternalStates[0].Member = "unselected.json"
			case "role":
				p.ExternalStates[0].Role = "unknown"
			case "identity", "target-identity":
				p.ExternalStates[0].Identity.Inode++
			case "foreign-identity":
				p.ExternalStates[0].Identity.UID++
			case "root-overlap":
				p.ExternalStates[0].Directory = f.root
				p.ExternalStates[0].Identity, _ = bootstrapDirectory(f.root)
			case "0755", "0775", "special-mode":
				mode := os.FileMode(0755)
				if change == "0775" {
					mode = 0775
				} else if change == "special-mode" {
					mode = 0700 | os.ModeSticky
				}
				if err := os.Chmod(directory, mode); err != nil {
					t.Fatal(err)
				}
			case "symlink":
				if err := os.Rename(directory, directory+"-retained"); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(directory+"-retained", directory); err != nil {
					t.Fatal(err)
				}
			case "unsafe-parent":
				if err := os.Chmod(filepath.Dir(directory), 0775); err != nil {
					t.Fatal(err)
				}
			case "account-overlap", "binary-overlap", "other-state-overlap":
				read := &bootstrapRead{plan: p, drain: &watchDrainRead{facts: map[string]string{}}, accounts: map[string]bool{}, binaryDirs: map[string]bool{}}
				affected := []bootstrapAffectedState{{"one.json", "memory", directory}}
				if change == "account-overlap" {
					read.accounts[filepath.Dir(directory)] = true
				} else if change == "binary-overlap" {
					read.binaryDirs[filepath.Join(directory, "bin")] = true
				} else {
					affected = append(affected, bootstrapAffectedState{"two.json", "memory", directory})
				}
				if err := read.selectStateRoots(affected); err == nil {
					t.Fatal("overlapping selected resource accepted")
				}
				return
			case "unsettled-grant":
				writePrivateFile(t, filepath.Join(directory, "unknown.json"), []byte(`{"inbox_activation":{"unmatched":true}}`), 0600)
			}
			if change != "target-identity" {
				target := bootstrapTargetPlan(t, p)
				target.ExternalStates = slices.Clone(p.ExternalStates)
				raw, err := json.Marshal(target)
				if err != nil {
					t.Fatal(err)
				}
				p.Transition.Target.SHA256 = writePrivateFile(t, p.Transition.Target.Path, raw, 0600)
			}
			path, pin := writeBootstrapPlan(t, f.root, p)
			before := bootstrapTreeMetadata(t, p.Current.Directory)
			var result UnitBootstrapResult
			if err := f.cli.bootstrapMutation(context.Background(), path, pin, "switch", &result, ff.capture(), nil); err == nil || result.Published {
				t.Fatal("invalid external state acquired publication", change, err)
			}
			if !reflect.DeepEqual(before, bootstrapTreeMetadata(t, p.Current.Directory)) {
				t.Fatal("refusal changed the active namespace")
			}
		})
	}
}

func TestUnitBootstrapExternalStateLiteralFields(t *testing.T) {
	f, p, _, _, _ := bootstrapExternalMemoryFixture(t)
	raw, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	for _, change := range []string{"member-case", "authority-flag", "identity-alias", "schema1"} {
		t.Run(change, func(t *testing.T) {
			bad := raw
			switch change {
			case "member-case":
				bad = bytes.Replace(bad, []byte(`"member":`), []byte(`"Member":`), 1)
			case "authority-flag":
				bad = bytes.Replace(bad, []byte(`"external_states":[{`), []byte(`"external_states":[{"trusted":true,`), 1)
			case "identity-alias":
				bad = bytes.Replace(bad, []byte(`"identity":{"name":`), []byte(`"identity":{"Name":`), 1)
			case "schema1":
				bad = bytes.Replace(bad, []byte(`"schema":"cairn.unit-bootstrap/2"`), []byte(`"schema":"cairn.unit-bootstrap/1"`), 1)
			}
			selected := filepath.Join(f.root, "invalid-"+change+".json")
			pin := writePrivateFile(t, selected, bad, 0600)
			if _, _, err := readUnitBootstrapPlan(selected, pin); err == nil {
				t.Fatal("nonliteral external state declaration accepted", change)
			}
		})
	}
}

func TestUnitBootstrapExternalStateReplacementAtCommit(t *testing.T) {
	for _, operation := range []string{"switch", "reverse"} {
		t.Run(operation, func(t *testing.T) {
			f, p, path, pin, ff := bootstrapExternalMemoryFixture(t)
			if operation == "reverse" {
				var forward UnitBootstrapResult
				if err := f.cli.bootstrapMutation(context.Background(), path, pin, "switch", &forward, ff.capture(), nil); err != nil {
					t.Fatal(err)
				}
			}
			before := bootstrapTreeMetadata(t, p.Current.Directory)
			var result UnitBootstrapResult
			err := f.cli.bootstrapMutation(context.Background(), path, pin, operation, &result, ff.capture(), func(phase string) error {
				if phase == "before:commit" {
					if err := os.Rename(f.memory, f.memory+"-original"); err != nil {
						return err
					}
					return os.Mkdir(f.memory, 0700)
				}
				return nil
			})
			if err == nil || result.Published || !strings.Contains(err.Error(), "external state directory identity") || !reflect.DeepEqual(before, bootstrapTreeMetadata(t, p.Current.Directory)) {
				t.Fatal("replacement after preparation acquired namespace exchange", operation, err)
			}
		})
	}
}

func TestUnitBootstrapFinalRefusalRetainsEarlierLegacyDisable(t *testing.T) {
	for _, operation := range []string{"switch", "reverse"} {
		t.Run(operation, func(t *testing.T) {
			f, p, path, pin, ff, original, identity := bootstrapEnabledOriginal(t)
			if operation == "reverse" {
				var forward UnitBootstrapResult
				if err := f.cli.bootstrapMutation(context.Background(), path, pin, "switch", &forward, ff.capture(), nil); err != nil {
					t.Fatal(err)
				}
			}
			before := bootstrapTreeMetadata(t, p.Current.Directory)
			manifest := p.Associations["one.json"].Binding.Path
			var atCommit []byte
			var result UnitBootstrapResult
			err := f.cli.bootstrapMutation(context.Background(), path, pin, operation, &result, ff.capture(), func(phase string) error {
				if phase == "before:commit" {
					var err error
					atCommit, err = os.ReadFile(manifest)
					ff.units["selected.service"]["InvocationID"] = strings.Repeat("b", 32)
					return err
				}
				return nil
			})
			if err == nil || result.Published || len(atCommit) == 0 || !reflect.DeepEqual(before, bootstrapTreeMetadata(t, p.Current.Directory)) {
				t.Fatal("late refusal exchanged the original namespace", operation, err)
			}
			after, err := os.ReadFile(manifest)
			if err != nil || !bytes.Equal(after, atCommit) || bytes.Equal(after, original) {
				t.Fatal("late refusal automatically undid prior legacy disable", err)
			}
			if result.Reconciliation == "" {
				t.Fatal("prior recovery effects were not reported")
			}
			ff.units["selected.service"]["InvocationID"] = ""
			var rollback UnitBootstrapResult
			if err := f.cli.bootstrapMutation(context.Background(), path, pin, "rollback", &rollback, ff.capture(), nil); err != nil {
				t.Fatal("fresh stopped-boundary rollback", err)
			}
			assertBootstrapRawOriginal(t, p, original, identity)
		})
	}
}

// The maintained Python fixture supplies actual authenticated API/PG routing.
// Only systemd/cgroup observations are fixture-owned data, captured through the
// landed private producer, never JSON assertions passed as a receipt.
func TestUnitBootstrapOwnedTransition(t *testing.T) {
	path := os.Getenv("CAIRN_BOOTSTRAP_EFFECT_PLAN")
	if path == "" {
		t.Skip("explicit owned API/PG bootstrap integration fixture")
	}
	var spec struct {
		Plan, Pin, Result        string
		Operation                string
		ActorArgv                []string
		ActorBody                json.RawMessage
		Refuse                   bool
		RefuseContains           string
		HoldPhase, Marker        string
		Recover, ExpectPublished bool
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &spec); err != nil {
		t.Fatal(err)
	}
	p, _, err := readUnitBootstrapPlan(spec.Plan, spec.Pin)
	if err != nil {
		t.Fatal(err)
	}
	fence := newSelectedFenceFixture(t)
	fence.scope.Database = ""
	fence.scope.DatabaseSystemID = ""
	fence.host.dbRead = nil
	p.Transition.Fence = fence.scope
	raw, err = json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	spec.Pin = writePrivateFile(t, spec.Plan, raw, 0600)
	profile := stage2ServicesProfile()
	host := NewFileHost(FileHostOptions{Profile: profile})
	concrete := host.Call
	var reads []string
	host.Call = func(ctx context.Context, config *value.Value, operation string, body *value.Value, options NativeCallOptions) (*value.Value, error) {
		switch operation {
		case "wake-attempts", "event-watch", "session-inbox-control", "session-inbox-attempt-status", "session-inbox-poll-status":
		default:
			t.Errorf("bootstrap attempted non-read API operation %s", operation)
			return nil, fmt.Errorf("unexpected non-read operation %s", operation)
		}
		result, err := concrete(ctx, config, operation, body, options)
		reads = append(reads, operation)
		return result, err
	}
	cli := &CLI{Profile: profile, Host: host, ProcRoot: "/proc"}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	timings := map[string]float64{}
	invoke := func(operation string, result *UnitBootstrapResult, observer func(string) error) error {
		bounded, stop := context.WithTimeout(ctx, 30*time.Second)
		defer stop()
		start := time.Now()
		var err error
		if observer != nil {
			err = cli.bootstrapMutation(bounded, spec.Plan, spec.Pin, operation, result, fence.capture(), observer)
		} else {
			var output bytes.Buffer
			code := cli.runUnitBootstrap(bounded, []string{operation, "--plan", spec.Plan, "--sha256", spec.Pin}, &output, fence.capture())
			if decodeErr := json.Unmarshal(output.Bytes(), result); decodeErr != nil {
				t.Fatal(decodeErr)
			}
			if code != 0 {
				if result.Error == "" {
					t.Fatal("shared CLI failure omitted structured error")
				}
				err = errors.New(result.Error)
			}
			if code == 0 && result.Error != "" {
				t.Fatal("shared CLI exit status contradicted structured error")
			}
		}
		timings[operation] = time.Since(start).Seconds()
		if timings[operation] >= 30 || bounded.Err() != nil {
			t.Fatalf("unchanged30s bootstrap deadline exceeded: %s %v", operation, err)
		}
		return err
	}
	before := bootstrapPreservedMetadata(t, p.Current.Directory)
	var forward, reverse UnitBootstrapResult
	var observe func(string) error
	if spec.Operation != "" {
		if spec.Operation != "switch" && spec.Operation != "reverse" && spec.Operation != "rollback" {
			t.Fatal("invalid owned operation")
		}
		var transition UnitBootstrapResult
		if err := invoke(spec.Operation, &transition, nil); err != nil {
			t.Fatal(err)
		}
		if spec.Operation != "rollback" && (!transition.Published || !transition.Rechecked || transition.DatabaseCoverage != "not-selected") {
			t.Fatal("shared CLI operation did not publish")
		}
		raw, err := json.Marshal(map[string]any{"result": "PASS", "operation": spec.Operation, "transition": transition, "seconds": timings, "deadline_seconds": 30, "api_read_operations": reads, "workflow_proof": "SHARED_CLI_WORKFLOW_WITH_FIXTURE_FENCE"})
		if err != nil {
			t.Fatal(err)
		}
		writePrivateFile(t, spec.Result, raw, 0600)
		return
	}
	if spec.Recover {
		if err := invoke("rollback", &reverse, nil); err != nil {
			t.Fatal(err)
		}
		if reverse.Published != spec.ExpectPublished {
			t.Fatal("recovery confused publication state")
		}
		if err := bootstrapIdentityAt(p.Current.Directory, p.Transition.OriginalIdentity); err != nil {
			t.Fatal(err)
		}
		raw, _ := json.Marshal(map[string]any{"result": "PASS", "recovery": reverse, "actual_previous_process": "SIGKILL joined by owned fixture", "seconds": timings, "deadline_seconds": 30, "api_read_operations": reads, "workflow_proof": "SHARED_CLI_WORKFLOW_WITH_FIXTURE_FENCE"})
		writePrivateFile(t, spec.Result, raw, 0600)
		return
	}
	if spec.HoldPhase != "" {
		observe = func(phase string) error {
			if phase == spec.HoldPhase {
				writePrivateFile(t, spec.Marker, []byte(phase), 0600)
				<-ctx.Done()
				return ctx.Err()
			}
			return nil
		}
	}
	if spec.Refuse {
		observe = func(phase string) error {
			if phase != "before:exchange" {
				return nil
			}
			command := exec.CommandContext(ctx, spec.ActorArgv[0], spec.ActorArgv[1:]...)
			command.Stdin = bytes.NewReader(spec.ActorBody)
			output, err := command.CombinedOutput()
			writePrivateFile(t, filepath.Join(filepath.Dir(spec.Result), "actor-result.json"), output, 0600)
			return err
		}
	}
	err = invoke("switch", &forward, observe)
	if spec.RefuseContains != "" {
		if err == nil || !strings.Contains(err.Error(), spec.RefuseContains) || forward.Published || !reflect.DeepEqual(before, bootstrapPreservedMetadata(t, p.Current.Directory)) {
			t.Fatalf("production shape refusal lost: %v %+v", err, forward)
		}
		raw, _ := json.Marshal(map[string]any{"result": "PASS", "published": false, "error": err.Error(), "seconds": timings, "deadline_seconds": 30, "api_read_operations": reads, "workflow_proof": "SHARED_CLI_WORKFLOW_WITH_FIXTURE_FENCE"})
		writePrivateFile(t, spec.Result, raw, 0600)
		return
	}
	if spec.Refuse {
		if err == nil || !forward.Inspected || forward.Published || !strings.Contains(err.Error(), "D6 bootstrap drain invalidated") || !reflect.DeepEqual(before, bootstrapPreservedMetadata(t, p.Current.Directory)) {
			t.Fatal("actual owning claim did not invalidate D6", err)
		}
		raw, _ := json.Marshal(map[string]any{"result": "PASS", "published": false, "error": err.Error(), "actor": "separate actual API claim at before:exchange"})
		writePrivateFile(t, spec.Result, raw, 0600)
		return
	}
	if err != nil {
		t.Fatal(err)
	}
	if !forward.Published || !forward.Rechecked || forward.DatabaseCoverage != "not-selected" {
		t.Fatal("owned transition did not publish")
	}
	if err := invoke("reverse", &reverse, nil); err != nil {
		t.Fatal(err)
	}
	if !reverse.Published || !reflect.DeepEqual(before, bootstrapPreservedMetadata(t, p.Current.Directory)) {
		t.Fatal("owned transition did not restore exact original")
	}
	raw, err = json.Marshal(map[string]any{"forward": forward, "reverse": reverse, "result": "PASS", "api": "actual authenticated FileHost transport", "systemd": "owned observation fixture only", "runtime_qualification": "not claimed", "seconds": timings, "deadline_seconds": 30, "api_read_operations": reads, "workflow_proof": "SHARED_CLI_WORKFLOW_WITH_FIXTURE_FENCE"})
	if err != nil {
		t.Fatal(err)
	}
	writePrivateFile(t, spec.Result, raw, 0600)
}

func writeBootstrapPlan(t *testing.T, root string, p *UnitBootstrapPlan) (string, string) {
	t.Helper()
	raw, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "bootstrap-plan.json")
	return path, writePrivateFile(t, path, raw, 0600)
}

// No content hashing: neither opaque archives nor credential inode aliases are
// read by the assertion observer. The real API driver retains safe store facts.
func bootstrapTreeMetadata(t *testing.T, root string) map[string]string {
	t.Helper()
	rows := map[string]string{}
	err := filepath.WalkDir(root, func(path string, _ os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		st := info.Sys().(*syscall.Stat_t)
		rows[path] = fmt.Sprintf("%d:%d:%d:%d:%d:%d:%d:%d:%d", st.Dev, st.Ino, st.Uid, st.Gid, st.Mode, st.Nlink, st.Size, st.Mtim.Nano(), st.Ctim.Nano())
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return rows
}

func TestUnitBootstrapMixedLayoutPrivateCaptureAndReread(t *testing.T) {
	f, _, path, pin := bootstrapFixture(t)
	// Unit API doubles are labelled here; the Python driver uses real auth/PG.
	before := bootstrapTreeMetadata(t, f.root)
	receipt, err := f.cli.captureBootstrapDrain(context.Background(), path, pin)
	if err != nil {
		t.Fatal(err)
	}
	if f.reads == 0 || receipt.associations["one.json"] != "cairn.inbox-recall-binding/2:disabled-bound" {
		t.Fatal("lost actual bound2 association/read operations", receipt.associations, f.reads)
	}
	if err := receipt.recheck(context.Background()); err != nil {
		t.Fatal(err)
	}
	for key := range receipt.facts {
		if strings.Contains(key, ".before-") && !strings.HasPrefix(key, "bootstrap-metadata:") {
			t.Fatal("opaque archive was read as content", key)
		}
	}
	if !reflect.DeepEqual(before, bootstrapTreeMetadata(t, f.root)) {
		t.Fatal("read-only bootstrap mutated original metadata")
	}
	if err := readWatchBindings(f.selected); err == nil {
		t.Fatal("new bootstrap loosened checked Go3 membership guard")
	}
}

func TestUnitBootstrapRefusesChangedFactsAndUnsupportedAssociations(t *testing.T) {
	for _, change := range []string{"archive-mode", "unknown-member", "missing-archive", "lock", "active-mode", "enabled", "unbound", "live-lock", "opaque-alias"} {
		t.Run(change, func(t *testing.T) {
			f, p, path, pin := bootstrapFixture(t)
			receipt, err := f.cli.captureBootstrapDrain(context.Background(), path, pin)
			if err != nil {
				t.Fatal(err)
			}
			switch change {
			case "archive-mode":
				if err := os.Chmod(filepath.Join(f.selected.Directory, p.Archives[0].Name), 0400); err != nil {
					t.Fatal(err)
				}
			case "unknown-member":
				writePrivateFile(t, filepath.Join(f.selected.Directory, "unknown"), []byte("unread"), 0600)
			case "missing-archive":
				if err := os.Remove(filepath.Join(f.selected.Directory, p.Archives[0].Name)); err != nil {
					t.Fatal(err)
				}
			case "lock":
				lock := WatcherLockPath(f.selected.Directory)
				if err := os.Remove(lock); err != nil {
					t.Fatal(err)
				}
				writePrivateFile(t, lock, nil, 0600)
			case "active-mode":
				if err := os.Chmod(filepath.Join(f.selected.Directory, "one.json"), 0400); err != nil {
					t.Fatal(err)
				}
			case "enabled":
				manifest := filepath.Join(f.root, "binding.json")
				raw, err := os.ReadFile(manifest)
				if err != nil {
					t.Fatal(err)
				}
				writePrivateFile(t, manifest, bytes.Replace(raw, []byte(`"enabled":false`), []byte(`"enabled":true`), 1), 0600)
			case "unbound":
				config := filepath.Join(f.selected.Directory, "one.json")
				raw, err := os.ReadFile(config)
				if err != nil {
					t.Fatal(err)
				}
				var body map[string]any
				if err := json.Unmarshal(raw, &body); err != nil {
					t.Fatal(err)
				}
				delete(body, "inbox_recall_binding")
				raw, err = json.Marshal(body)
				if err != nil {
					t.Fatal(err)
				}
				p.Current.Pins["one.json"] = writePrivateFile(t, config, raw, 0600)
				path, pin = writeBootstrapPlan(t, f.root, p)
				if _, err := f.cli.captureBootstrapDrain(context.Background(), path, pin); err == nil || !strings.Contains(err.Error(), "unbound retained /2 association unsupported") {
					t.Fatal("unbound inventory became a verified association", err)
				}
			case "live-lock":
				lease, err := AcquireWatcherLock(f.selected.Directory)
				if err != nil {
					t.Fatal(err)
				}
				defer mustRelease(t, lease)
			case "opaque-alias":
				name := filepath.Join(f.selected.Directory, p.Archives[0].Name)
				if err := os.Remove(name); err != nil {
					t.Fatal(err)
				}
				if err := os.Link(p.CredentialSelectors[0], name); err != nil {
					t.Fatal(err)
				}
			}
			before := bootstrapTreeMetadata(t, f.root)
			if err := receipt.recheck(context.Background()); err == nil || !strings.Contains(err.Error(), "D6 bootstrap drain invalidated") {
				t.Fatal("changed current facts survived private reread", err)
			}
			if !reflect.DeepEqual(before, bootstrapTreeMetadata(t, f.root)) {
				t.Fatal("refusal modified original files")
			}
		})
	}
}

func TestUnitBootstrapCannotMintReceiptFromJSONOrRunConsequentialOperation(t *testing.T) {
	f, _, path, pin := bootstrapFixture(t)
	for _, operation := range []string{"switch", "reverse"} {
		var out bytes.Buffer
		reads := f.reads
		if code := f.cli.RunUnitBootstrap(context.Background(), []string{operation, "--plan", path, "--sha256", pin}, &out); code != 1 {
			t.Fatal("consequential bootstrap became available")
		}
		var result UnitBootstrapResult
		if err := json.Unmarshal(out.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		if result.Inspected || result.Rechecked || result.Published || result.OwnerFence != "unknown" || f.reads != reads {
			t.Fatal("unimplemented fence manufactured success", result)
		}
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, injection := range []string{`"facts":{},`, `"stopped":true,`, `"scope":"disposable-fixture",`, `"Schema":"cairn.unit-bootstrap/1",`, `"receipt":{},`} {
		changed := append([]byte("{"+injection), raw[1:]...)
		badPin := writePrivateFile(t, path, changed, 0600)
		if _, _, err := readUnitBootstrapPlan(path, badPin); err == nil {
			t.Fatal("serialized receipt/trust flag accepted", injection)
		}
	}
	var zero *bootstrapDrainReceipt
	if err := zero.recheck(context.Background()); err == nil {
		t.Fatal("nil receipt rechecked")
	}
}

func TestUnitBootstrapPrivateReadOnlyCLIResult(t *testing.T) {
	f, _, path, pin := bootstrapFixture(t)
	var out bytes.Buffer
	if code := f.cli.RunUnitBootstrap(context.Background(), []string{"inspect", "--plan", path, "--sha256", pin}, &out); code != 0 {
		t.Fatalf("inspect failed: %s", out.String())
	}
	var result UnitBootstrapResult
	if err := json.Unmarshal(out.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if !result.Inspected || !result.Rechecked || result.Published || result.OwnerFence != "unknown" || result.Reverse != "unsupported" {
		t.Fatal("read result became cutover authority", result)
	}
}

func TestUnitBootstrapRejectsDuplicateIdentityAndSpecialArchiveMetadata(t *testing.T) {
	for _, change := range []string{"duplicate", "setuid-archive"} {
		t.Run(change, func(t *testing.T) {
			f, p, _, _ := bootstrapFixture(t)
			if change == "duplicate" {
				raw, err := os.ReadFile(filepath.Join(f.selected.Directory, "one.json"))
				if err != nil {
					t.Fatal(err)
				}
				p.Current.Pins["two.json"] = writePrivateFile(t, filepath.Join(f.selected.Directory, "two.json"), raw, 0600)
			} else if err := os.Chmod(filepath.Join(f.selected.Directory, p.Archives[0].Name), 0600|os.ModeSetuid); err != nil {
				t.Fatal(err)
			}
			path, pin := writeBootstrapPlan(t, f.root, p)
			if _, err := f.cli.captureBootstrapDrain(context.Background(), path, pin); err == nil {
				t.Fatal("ambiguous identity/special metadata produced a drain")
			}
			if f.reads != 0 {
				t.Fatal("invalid local association reached API")
			}
		})
	}
}

func TestUnitBootstrapPlanCredentialAliasRefusesBeforeContentRead(t *testing.T) {
	f, p, path, pin := bootstrapFixture(t)
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(p.CredentialSelectors[0], path); err != nil {
		t.Fatal(err)
	}
	before := bootstrapTreeMetadata(t, f.root)
	if _, _, err := readUnitBootstrapPlan(path, pin); err == nil || !strings.Contains(err.Error(), "before content read") {
		t.Fatal("plan credential alias reached content reader", err)
	}
	if !reflect.DeepEqual(before, bootstrapTreeMetadata(t, f.root)) || f.reads != 0 {
		t.Fatal("plan alias refusal mutated files or reached API")
	}
}
