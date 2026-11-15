package coordination

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func legacyExecutableFixture(t *testing.T) (string, string) {
	t.Helper()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	parent := filepath.Join(root, "legacy-bin")
	if err := os.Mkdir(parent, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(parent, 0755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(parent, "cairn")
	legacyTestFile(t, path, []byte("#!/bin/sh\nexit 0\n"), 0755)
	return parent, path
}

func legacyTestFile(t *testing.T, path string, body []byte, mode os.FileMode) {
	t.Helper()
	if err := os.Chmod(path, 0600); err != nil && !os.IsNotExist(err) {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, body, mode); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, mode); err != nil {
		t.Fatal(err)
	}
}

func TestBootstrapLegacyExecutableParentContract(t *testing.T) {
	parent, path := legacyExecutableFixture(t)
	if _, err := bootstrapLegacyExecutable(path); err != nil {
		t.Fatalf("valid original before fault injection: %v", err)
	}
	observed, err := bootstrapLegacyExecutable(path)
	if err != nil || observed.SHA256 == "" || observed.Fingerprint == "" {
		t.Fatalf("owner-controlled0755 original: %+v %v", observed, err)
	}
	if _, err := compatibilityOpenedBinary(path, uint32(os.Getuid())); err == nil {
		t.Fatal("generic /3 reader accepted the legacy0755 parent")
	}
	if err := os.Chmod(parent, 0775); err != nil {
		t.Fatal(err)
	}
	if _, err := bootstrapLegacyExecutable(path); err == nil {
		t.Fatal("group-writable legacy parent accepted")
	}
	if err := os.Chmod(parent, 0755); err != nil {
		t.Fatal(err)
	}
	if _, err := bootstrapLegacyExecutable(path); err != nil {
		t.Fatalf("fixture-owned admitted0755 ancestry: %v", err)
	}
}

func TestBootstrapLegacyExecutableRefusals(t *testing.T) {
	for _, kind := range []string{"group-write", "setuid", "not-executable", "hardlink", "leaf-symlink", "parent-symlink", "noncanonical", "directory"} {
		t.Run(kind, func(t *testing.T) {
			parent, path := legacyExecutableFixture(t)
			var err error
			switch kind {
			case "group-write":
				err = os.Chmod(path, 0775)
			case "setuid":
				err = os.Chmod(path, os.ModeSetuid|0755)
			case "not-executable":
				err = os.Chmod(path, 0644)
			case "hardlink":
				err = os.Link(path, path+".alias")
			case "leaf-symlink":
				link := path + ".link"
				err = os.Symlink(path, link)
				path = link
			case "parent-symlink":
				link := parent + "-alias"
				err = os.Symlink(parent, link)
				path = filepath.Join(link, "cairn")
			case "noncanonical":
				path = parent + "/../legacy-bin/cairn"
			case "directory":
				path = parent
			}
			if err != nil {
				t.Fatal(err)
			}
			if _, err := bootstrapLegacyExecutable(path); err == nil {
				t.Fatal("unsafe original executable accepted")
			}
		})
	}
	if os.Getuid() != 0 {
		if _, err := bootstrapLegacyExecutable("/usr/bin/true"); err == nil {
			t.Fatal("root-owned executable accepted as the user's original Cairn")
		}
	}
}

func TestBootstrapLegacyObservationRetainsReplacementIdentity(t *testing.T) {
	_, path := legacyExecutableFixture(t)
	before, err := bootstrapLegacyExecutable(path)
	if err != nil {
		t.Fatal(err)
	}
	legacyTestFile(t, path+".new", []byte("#!/bin/sh\nexit 0\n"), 0755)
	if err := os.Rename(path+".new", path); err != nil {
		t.Fatal(err)
	}
	after, err := bootstrapLegacyExecutable(path)
	if err != nil {
		t.Fatal(err)
	}
	if before.SHA256 != after.SHA256 || before.Fingerprint == after.Fingerprint {
		t.Fatal("equal bytes hid replacement between fresh observations")
	}
}

func TestBootstrapLegacyExternalProfileModes(t *testing.T) {
	parent, _ := legacyExecutableFixture(t)
	path := filepath.Join(parent, "profile.json")
	for _, mode := range []os.FileMode{0600, 0400, 0644, 0700} {
		legacyTestFile(t, path, []byte("{\"inbox_recall_binding\":null}\n"), mode)
		observed, err := bootstrapLegacyProfile(path)
		if mode == 0600 || mode == 0400 {
			if err != nil || !strings.Contains(string(observed.Raw), "inbox_recall_binding") {
				t.Fatalf("private original profile mode%o: %+v %v", mode, observed, err)
			}
		} else if err == nil {
			t.Fatalf("external profile mode%o accepted", mode)
		}
	}
}

func TestBootstrapLegacyConfigurationHomes(t *testing.T) {
	for _, tc := range []struct {
		name  string
		homes []any
		valid bool
	}{
		{"single-absent", []any{nil}, true},
		{"single-null", []any{nativeMap("config_home", nil).Get("config_home")}, true},
		{"single-explicit", []any{"/account"}, true},
		{"single-relative", []any{"account"}, false},
		{"single-empty", []any{""}, false},
		{"single-noncanonical", []any{"/account/../account"}, false},
		{"single-wrong-type", []any{true}, false},
		{"shared-distinct", []any{"/account-one", "/account-two"}, true},
		{"shared-absent", []any{nil, "/account-two"}, false},
		{"shared-conflicting", []any{"/account", "/account"}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var profiles []*value.Value
			for _, home := range tc.homes {
				profile := nativeMap("binding", "original")
				if home != nil {
					profile.Set("config_home", nativeMap("config_home", home).Get("config_home"))
				}
				profiles = append(profiles, profile)
			}
			if err := bootstrapLegacyProfileHomes(profiles); (err == nil) != tc.valid {
				t.Fatalf("homes%v: %v", tc.homes, err)
			}
		})
	}
}

func rewriteLegacyAccount(t *testing.T, f *grantCurrentnessFixture, p *UnitBootstrapPlan, home any) {
	t.Helper()
	manifestPath := p.Associations["one.json"].Binding.Path
	manifestRaw, err := os.ReadFile(manifestPath)
	if err != nil {
		t.Fatal(err)
	}
	var manifest map[string]any
	if err := json.Unmarshal(manifestRaw, &manifest); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{filepath.Join(f.root, "memory.json"), filepath.Join(f.root, "coord.json"), filepath.Join(p.Current.Directory, "one.json")} {
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		var config map[string]any
		if err := json.Unmarshal(raw, &config); err != nil {
			t.Fatal(err)
		}
		delete(config, "config_home")
		if home != nil && filepath.Base(path) != "memory.json" {
			config["config_home"] = home
		}
		raw, err = json.Marshal(config)
		if err != nil {
			t.Fatal(err)
		}
		sha := writePrivateFile(t, path, raw, 0600)
		switch filepath.Base(path) {
		case "memory.json":
			manifest["memory_config"].(map[string]any)["sha256"] = sha
		case "coord.json":
			manifest["coordination_configs"].([]any)[0].(map[string]any)["sha256"] = sha
		default:
			p.Current.Pins["one.json"] = sha
		}
	}
	manifestRaw, err = json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	selected := p.Associations["one.json"]
	selected.Binding.SHA256 = writePrivateFile(t, manifestPath, manifestRaw, 0600)
	p.Associations["one.json"] = selected
}

func TestBootstrapLegacyOriginalMissingHomes(t *testing.T) {
	f, p, _, _, _ := bootstrapEffectFixture(t)
	rewriteLegacyAccount(t, f, p, nil)
	if _, _, err := f.cli.readBootstrapSelection(context.Background(), p, nil, nil); err != nil {
		t.Fatalf("Source single coordinator and memory without config_home: %v", err)
	}
	raw, err := os.ReadFile(filepath.Join(p.Current.Directory, "one.json"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "config_home") {
		t.Fatal("reader injected a default account home")
	}
}

func TestBootstrapLegacyOriginalExternalAccount(t *testing.T) {
	f, p, _, _, _ := bootstrapEffectFixture(t)
	account := t.TempDir()
	if err := os.Chmod(account, 0755); err != nil {
		t.Fatal(err)
	}
	rewriteLegacyAccount(t, f, p, account)
	if _, _, err := f.cli.readBootstrapSelection(context.Background(), p, nil, nil); err != nil {
		t.Fatalf("Source-owned0755 account outside private payload root: %v", err)
	}
	if err := os.Chmod(account, 0775); err != nil {
		t.Fatal(err)
	}
	if _, _, err := f.cli.readBootstrapSelection(context.Background(), p, nil, nil); err == nil {
		t.Fatal("group-writable original account accepted")
	}
}

func bootstrapTargetPlan(t *testing.T, original *UnitBootstrapPlan) *UnitBootstrapPlan {
	t.Helper()
	target, _, err := readUnitBootstrapPlan(original.Transition.Target.Path, original.Transition.Target.SHA256)
	if err != nil {
		t.Fatal(err)
	}
	return target
}

func rewriteBootstrapTargetProfiles(t *testing.T, target *UnitBootstrapPlan, change func(bool, map[string]any)) {
	t.Helper()
	selected := target.Associations["one.json"]
	read := func(path string) map[string]any {
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		var data map[string]any
		if err := json.Unmarshal(raw, &data); err != nil {
			t.Fatal(err)
		}
		return data
	}
	write := func(path string, data map[string]any) string {
		raw, err := json.Marshal(data)
		if err != nil {
			t.Fatal(err)
		}
		return writePrivateFile(t, path, raw, 0400)
	}
	manifest, receipt := read(selected.Binding.Path), read(selected.Receipt.Path)
	descriptors := []map[string]any{manifest["memory_config"].(map[string]any)}
	for _, entry := range manifest["coordination_configs"].([]any) {
		descriptors = append(descriptors, entry.(map[string]any))
	}
	for i, descriptor := range descriptors {
		path := descriptor["path"].(string)
		profile := read(path)
		change(i == 0, profile)
		descriptor["sha256"] = write(path, profile)
		if i == 1 {
			target.Current.Pins["one.json"] = write(filepath.Join(target.Current.Directory, "one.json"), profile)
		}
	}
	receipt["memory_config"], receipt["coordination_configs"] = manifest["memory_config"], manifest["coordination_configs"]
	selected.Binding.SHA256 = write(selected.Binding.Path, manifest)
	receipt["binding"].(map[string]any)["sha256"] = selected.Binding.SHA256
	selected.Receipt.SHA256 = write(selected.Receipt.Path, receipt)
	target.Associations["one.json"] = selected
}

func TestBootstrapAccountIdentityBothPhases(t *testing.T) {
	f, p, _, _, fence := bootstrapEffectFixture(t)
	target := bootstrapTargetPlan(t, p)
	account := t.TempDir()
	if err := os.Chmod(account, 0755); err != nil {
		t.Fatal(err)
	}
	rewriteLegacyAccount(t, f, p, account)
	rewriteBootstrapTargetProfiles(t, target, func(memory bool, profile map[string]any) {
		delete(profile, "config_home")
		if !memory {
			profile["config_home"] = account
		}
	})
	targetRaw, err := json.Marshal(target)
	if err != nil {
		t.Fatal(err)
	}
	p.Transition.Target.SHA256 = writePrivateFile(t, p.Transition.Target.Path, targetRaw, 0600)
	path, pin := writeBootstrapPlan(t, f.root, p)
	for _, operation := range []string{"switch", "reverse"} {
		var result UnitBootstrapResult
		if err := f.cli.bootstrapMutation(context.Background(), path, pin, operation, &result, fence.capture(), nil); err != nil {
			t.Fatalf("%s with unchanged external0755 account: %v", operation, err)
		}
	}
	info, err := os.Stat(account)
	if err != nil || info.Mode().Perm() != 0755 {
		t.Fatal("bootstrap changed account permissions")
	}
}

func TestBootstrapAccountReplacementRefuses(t *testing.T) {
	parent, _ := legacyExecutableFixture(t)
	account := filepath.Join(parent, "account")
	if err := os.Mkdir(account, 0755); err != nil {
		t.Fatal(err)
	}
	b := &bootstrapRead{drain: &watchDrainRead{facts: map[string]string{}}}
	if err := b.accountIdentity(account); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(account, account+".retained"); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(account, 0755); err != nil {
		t.Fatal(err)
	}
	if err := b.accountIdentity(account); err == nil {
		t.Fatal("replaced account identity accepted at fresh readback")
	}
}

func TestBootstrapCurrent3UnboundKinds(t *testing.T) {
	for _, harness := range []string{"agy", "hermes", "opencode", "codex"} {
		t.Run(harness, func(t *testing.T) {
			f, p, _, _, _ := bootstrapEffectFixture(t)
			target := bootstrapTargetPlan(t, p)
			rewriteBootstrapTargetProfiles(t, target, func(memory bool, profile map[string]any) {
				delete(profile, "inbox_recall_binding")
				delete(profile, "config_home")
				profile["harness"] = harness
			})
			// A new unbound harness fixture has no retained activation ledger.
			// A separate refusal below preserves the unknown-harness grant fence.
			if err := os.Remove(f.ledgerPath); err != nil {
				t.Fatal(err)
			}
			if _, associations, err := f.cli.readBootstrapSelection(context.Background(), target, nil, nil); err != nil {
				t.Fatalf("receipt-selected %s unbound/3: %v", harness, err)
			} else if associations["one.json"] != "cairn.inbox-recall-binding/3:disabled-unbound" {
				t.Fatalf("unbound mode was normalized: %v", associations)
			}
		})
	}
}

func TestBootstrapCurrent3UnboundReceiptRefusals(t *testing.T) {
	for _, fault := range []string{"bound-field-removed", "unbound-field-present", "receipt-hash", "receipt-schema", "receipt-binding"} {
		t.Run(fault, func(t *testing.T) {
			f, p, _, _, _ := bootstrapEffectFixture(t)
			target := bootstrapTargetPlan(t, p)
			if fault != "bound-field-removed" {
				rewriteBootstrapTargetProfiles(t, target, func(_ bool, profile map[string]any) { delete(profile, "inbox_recall_binding") })
			}
			selected := target.Associations["one.json"]
			if fault == "bound-field-removed" || fault == "unbound-field-present" {
				path := filepath.Join(target.Current.Directory, "one.json")
				raw, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				var profile map[string]any
				if err := json.Unmarshal(raw, &profile); err != nil {
					t.Fatal(err)
				}
				delete(profile, "inbox_recall_binding")
				if fault == "unbound-field-present" {
					profile["inbox_recall_binding"] = selected.Binding.Path
				}
				raw, err = json.Marshal(profile)
				if err != nil {
					t.Fatal(err)
				}
				target.Current.Pins["one.json"] = writePrivateFile(t, path, raw, 0400)
			} else if fault == "receipt-hash" {
				selected.Receipt.SHA256 = strings.Repeat("0", 64)
			} else {
				raw, err := os.ReadFile(selected.Receipt.Path)
				if err != nil {
					t.Fatal(err)
				}
				var receipt map[string]any
				if err := json.Unmarshal(raw, &receipt); err != nil {
					t.Fatal(err)
				}
				if fault == "receipt-schema" {
					receipt["schema"] = "cairn.lifecycle-compatibility/0"
				} else {
					receipt["binding"].(map[string]any)["path"] = filepath.Join(f.root, "foreign-binding.json")
				}
				raw, err = json.Marshal(receipt)
				if err != nil {
					t.Fatal(err)
				}
				selected.Receipt.SHA256 = writePrivateFile(t, selected.Receipt.Path, raw, 0400)
			}
			target.Associations["one.json"] = selected
			if _, _, err := f.cli.readBootstrapSelection(context.Background(), target, nil, nil); err == nil {
				t.Fatalf("%s accepted", fault)
			}
		})
	}
}

func TestBootstrapCurrent3UnknownHarnessGrantStillRefuses(t *testing.T) {
	for _, harness := range []string{"agy", "hermes", "opencode"} {
		t.Run(harness, func(t *testing.T) {
			f, p, _, _, _ := bootstrapEffectFixture(t)
			target := bootstrapTargetPlan(t, p)
			rewriteBootstrapTargetProfiles(t, target, func(_ bool, profile map[string]any) {
				delete(profile, "inbox_recall_binding")
				delete(profile, "config_home")
				profile["harness"] = harness
			})
			if _, _, err := f.cli.readBootstrapSelection(context.Background(), target, nil, nil); err == nil || !strings.Contains(err.Error(), "owning harness turn visibility is unknown") {
				t.Fatalf("unknown retained grant accepted: %v", err)
			}
		})
	}
}
