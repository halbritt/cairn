package coordination

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
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
