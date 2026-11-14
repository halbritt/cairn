package coordination

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/install"
)

// Finite public preflight with actual R02/R04 profiles; status doubles below
// supply no authentication, native session, refresh or retained D2 claim.
func TestR06UnboundControlPreflightProcess(t *testing.T) {
	f := readUnboundDrainFixture(t, "CAIRN_R06_UNBOUND_FIXTURE")
	current := f.Proposal.Bindings[0]
	receipt := mustCurrentnessRead(t, f.Receipt.Path)
	coordinators := receipt.Get("coordination_configs")
	if coordinators == nil || len(coordinators.Items) != 1 {
		t.Fatal("genuine single-coordinator input required")
	}
	next := install.File{Name: filepath.Base(f.Config.Path), Path: nativeText(coordinators.Items[0].Get("path")), SHA256: nativeText(coordinators.Items[0].Get("sha256"))}
	plan := map[string]any{"schema": "cairn.unit-control/2", "current": map[string]any{"directory": current.Directory, "pins": current.Pins}, "next": []install.File{next}, "retained": filepath.Join(filepath.Dir(current.Directory), "preflight-retained"), "census": map[string]any{"Catalog": f.Catalog, "Proposal": f.Proposal, "Retained": []WatchBindings{}}}
	pin := censusTestJSON(t, filepath.Join(f.Root, "public-preflight-plan.json"), plan)
	cli, calls := unboundDrainReadCLI(t)
	before := censusTestSnapshot(t, f.Root)
	credential := nativeText(mustCurrentnessRead(t, f.Config.Path).Get("token_file"))
	protectedBefore := unboundControlSnapshot(t, f.Root, credential)
	saveUnboundControlSnapshot(t, "before", protectedBefore)
	var out bytes.Buffer
	code := cli.RunUnitControl(context.Background(), []string{"inspect", "--plan", pin.Path, "--sha256", pin.SHA256}, &out)
	var result UnitControlResult
	if err := json.Unmarshal(out.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if code != 0 || !result.Inspected || result.Exchange.Published || *calls != 2 {
		t.Fatalf("public preflight did not reach the existing drain readers: code=%d calls=%d error=%q", code, *calls, result.Error)
	}
	if !reflect.DeepEqual(before, censusTestSnapshot(t, f.Root)) {
		t.Fatal("public inspect changed genuine input files")
	}
	protectedAfter := unboundControlSnapshot(t, f.Root, credential)
	saveUnboundControlSnapshot(t, "after", protectedAfter)
	if !reflect.DeepEqual(protectedBefore, protectedAfter) {
		t.Fatal("public preflight changed protected file identities")
	}
}

func unboundControlPlan(t *testing.T, f unboundDrainFixture) map[string]any {
	t.Helper()
	current := f.Proposal.Bindings[0]
	receipt := mustCurrentnessRead(t, f.Receipt.Path)
	coords := receipt.Get("coordination_configs")
	if coords == nil || len(coords.Items) != 1 {
		t.Fatal("single actual coordinator required")
	}
	return map[string]any{"schema": "cairn.unit-control/2", "current": map[string]any{"directory": current.Directory, "pins": current.Pins}, "next": []install.File{{Name: filepath.Base(f.Config.Path), Path: nativeText(coords.Items[0].Get("path")), SHA256: nativeText(coords.Items[0].Get("sha256"))}}, "retained": filepath.Join(filepath.Dir(current.Directory), "preflight-retained"), "census": map[string]any{"Catalog": f.Catalog, "Proposal": f.Proposal, "Retained": []WatchBindings{}}}
}

func unboundControlSnapshot(t *testing.T, root, credential string) map[string]string {
	t.Helper()
	rows := map[string]string{}
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		st := info.Sys().(*syscall.Stat_t)
		row := fmt.Sprintf("%d:%d:%d:%d:%d:%d:%d:%v:%v", st.Dev, st.Ino, st.Mode, st.Uid, st.Gid, st.Nlink, st.Size, st.Mtim, st.Ctim)
		if info.Mode().IsRegular() && path != credential && info.Mode().Perm() != 0 {
			raw, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			h := sha256.Sum256(raw)
			row += ":" + hex.EncodeToString(h[:])
		}
		if info.Mode()&os.ModeSymlink != 0 {
			target, err := os.Readlink(path)
			if err != nil {
				return err
			}
			row += ":" + target
		}
		rows[path] = row
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return rows
}

func saveUnboundControlSnapshot(t *testing.T, label string, rows map[string]string) {
	t.Helper()
	directory := os.Getenv("CAIRN_R06_PREFLIGHT_EVIDENCE")
	if directory == "" {
		return
	}
	if err := os.MkdirAll(directory, 0700); err != nil {
		t.Fatal(err)
	}
	raw, err := json.MarshalIndent(rows, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	name := strings.ReplaceAll(t.Name(), "/", "_") + "-" + label + ".json"
	if err := os.WriteFile(filepath.Join(directory, name), raw, 0600); err != nil {
		t.Fatal(err)
	}
}

func TestR06UnboundControlPreflightRefusalsProcess(t *testing.T) {
	f := readUnboundDrainFixture(t, "CAIRN_R06_UNBOUND_FIXTURE")
	cfg := mustCurrentnessRead(t, f.Config.Path)
	credential := nativeText(cfg.Get("token_file"))
	for _, row := range []struct{ name, want string }{
		{"missing-receipt", "no selected receipt association"},
		{"corrupt-receipt", "census pin changed"},
		{"foreign-association", "outside the census universe"},
		{"enabled-manifest", "private disabled /3 manifest"},
		{"unknown-next", "lacks a tracked receipt descriptor"},
		{"foreign-next", "lacks a tracked receipt descriptor"},
		{"credential-next", "refuses credential-content reads"},
		{"credential-catalog", "refuses credential-content reads"},
		{"credential-alias-next", "refuses credential-content reads through aliases"},
		{"credential-alias-catalog", "refuses credential-content reads through aliases"},
		{"candidate-memory-drift", "census pin changed"},
		{"candidate-profile-drift", "census pin changed"},
		{"candidate-selector-drift", "census pin changed"},
		{"resource-drift", "unknown native session census member"},
		{"wrong-plan-pin", "plan pin or UTF-8 mismatch"},
		{"wrong-current-pin", "watch config pin changed"},
		{"wrong-census-current", "exact pinned current census selection"},
		{"unknown-status", "wake-attempts visibility is unknown"},
	} {
		t.Run(row.name, func(t *testing.T) {
			directory, err := os.MkdirTemp(f.Root, "public-negative-")
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = os.RemoveAll(directory) })
			// Every case receives its own parsed plan and file; no shared pin mutation.
			raw, err := json.Marshal(unboundControlPlan(t, f))
			if err != nil {
				t.Fatal(err)
			}
			var plan UnitControlPlan
			if err := json.Unmarshal(raw, &plan); err != nil {
				t.Fatal(err)
			}
			next := plan.Next[0]
			var catalog WatchCensusCatalog
			raw, err = os.ReadFile(f.Catalog.Path)
			if err != nil {
				t.Fatal(err)
			}
			if err := censusDecode(raw, &catalog); err != nil {
				t.Fatal(err)
			}
			produced := map[string]any{}
			raw, err = os.ReadFile(f.Receipt.Path)
			if err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(raw, &produced); err != nil {
				t.Fatal(err)
			}
			object := func(path string) map[string]any {
				t.Helper()
				raw, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				v := map[string]any{}
				if err := json.Unmarshal(raw, &v); err != nil {
					t.Fatal(err)
				}
				return v
			}
			rewrite := func(path string, body []byte) {
				t.Helper()
				info, err := os.Stat(path)
				if err != nil {
					t.Fatal(err)
				}
				original, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { writePrivateFile(t, path, original, info.Mode().Perm()) })
				writePrivateFile(t, path, body, info.Mode().Perm())
			}
			switch row.name {
			case "missing-receipt":
				catalog.UnboundReceipts = nil
			case "corrupt-receipt":
				produced["members"].(map[string]any)["coordination.py"].(map[string]any)["sha256"] = strings.Repeat("0", 64)
				catalog.UnboundReceipts = []WatchCensusPin{censusTestJSON(t, filepath.Join(directory, "receipt.json"), produced)}
			case "foreign-association":
				produced["snapshot_root"] = t.TempDir()
				catalog.UnboundReceipts = []WatchCensusPin{censusTestJSON(t, filepath.Join(directory, "receipt.json"), produced)}
			case "enabled-manifest":
				manifest := object(produced["binding"].(map[string]any)["path"].(string))
				manifest["enabled"] = true
				pin := censusTestJSON(t, filepath.Join(directory, "binding.json"), manifest)
				produced["binding"] = map[string]any{"path": pin.Path, "sha256": pin.SHA256, "schema": "cairn.inbox-recall-binding/3"}
				catalog.Bindings = []WatchCensusPin{pin}
				catalog.UnboundReceipts = []WatchCensusPin{censusTestJSON(t, filepath.Join(directory, "receipt.json"), produced)}
			case "unknown-next", "foreign-next":
				raw, err := os.ReadFile(next.Path)
				if err != nil {
					t.Fatal(err)
				}
				path := filepath.Join(directory, "unknown.json")
				if row.name == "foreign-next" {
					path = filepath.Join(t.TempDir(), "foreign.json")
				}
				plan.Next[0].Path = path
				writePrivateFile(t, path, raw, 0400)
			case "credential-next":
				plan.Next[0].Path = credential
			case "credential-catalog":
				plan.Census.Catalog.Path = credential
			case "credential-alias-next", "credential-alias-catalog":
				alias := filepath.Join(directory, "credential-alias")
				if err := os.Link(credential, alias); err != nil {
					t.Fatal(err)
				}
				if row.name == "credential-alias-next" {
					plan.Next[0].Path = alias
				} else {
					plan.Census.Catalog.Path = alias
				}
			case "candidate-memory-drift":
				path := produced["memory_config"].(map[string]any)["path"].(string)
				raw, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				old, _ := json.Marshal(nativeText(mustCurrentnessRead(t, path).Get("state_dir")))
				replacement, _ := json.Marshal(filepath.Join(directory, "foreign-memory-state"))
				if !bytes.Contains(raw, old) {
					t.Fatal("memory mutation did not select actual field")
				}
				rewrite(path, bytes.ReplaceAll(raw, old, replacement))
			case "candidate-profile-drift":
				raw, err := os.ReadFile(next.Path)
				if err != nil {
					t.Fatal(err)
				}
				rewrite(next.Path, append(raw, ' '))
			case "candidate-selector-drift":
				raw, err := os.ReadFile(next.Path)
				if err != nil {
					t.Fatal(err)
				}
				old, _ := json.Marshal(credential)
				replacement, _ := json.Marshal(filepath.Join(directory, "foreign-token"))
				if !bytes.Contains(raw, old) {
					t.Fatal("selector mutation did not select actual field")
				}
				rewrite(next.Path, bytes.ReplaceAll(raw, old, replacement))
			case "resource-drift":
				path := filepath.Join(nativeText(cfg.Get("state_dir")), "unknown.json")
				writePrivateFile(t, path, []byte("{}"), 0600)
				t.Cleanup(func() { _ = os.Remove(path) })
			case "wrong-current-pin":
				plan.Current.Pins[next.Name] = strings.Repeat("0", 64)
			case "wrong-census-current":
				plan.Census.Proposal.Bindings[0].Pins[next.Name] = strings.Repeat("0", 64)
			}
			if row.name == "missing-receipt" || row.name == "corrupt-receipt" || row.name == "foreign-association" || row.name == "enabled-manifest" {
				plan.Census.Catalog = censusTestJSON(t, filepath.Join(directory, "catalog.json"), catalog)
			}
			pin := censusTestJSON(t, filepath.Join(directory, "plan.json"), plan)
			if row.name == "wrong-plan-pin" {
				pin.SHA256 = strings.Repeat("0", 64)
			}
			cli, calls := unboundDrainReadCLI(t)
			if row.name == "unknown-status" {
				call := cli.Host.Call
				cli.Host.Call = func(ctx context.Context, cfg *value.Value, op string, body *value.Value, options NativeCallOptions) (*value.Value, error) {
					_, _ = call(ctx, cfg, op, body, options)
					return nil, fmt.Errorf("NOT_FOUND")
				}
			}
			before := unboundControlSnapshot(t, f.Root, credential)
			saveUnboundControlSnapshot(t, "before", before)
			var out bytes.Buffer
			code := cli.RunUnitControl(context.Background(), []string{"inspect", "--plan", pin.Path, "--sha256", pin.SHA256}, &out)
			after := unboundControlSnapshot(t, f.Root, credential)
			saveUnboundControlSnapshot(t, "after", after)
			var result UnitControlResult
			if err := json.Unmarshal(out.Bytes(), &result); err != nil {
				t.Fatal(err)
			}
			if code != 1 || result.Inspected || result.Exchange.Published || !strings.Contains(result.Error, row.want) {
				t.Fatalf("wrong public refusal: code=%d error=%q want=%q", code, result.Error, row.want)
			}
			expectedCalls := 0
			if row.name == "unknown-status" {
				expectedCalls = 1
			}
			if *calls != expectedCalls {
				t.Fatalf("refusal crossed status boundary: calls=%d want=%d", *calls, expectedCalls)
			}
			if !reflect.DeepEqual(before, after) {
				t.Fatal("refusal changed protected input state")
			}
		})
	}
}

func TestR06UnboundControlPreflightIdentityDriftProcess(t *testing.T) {
	f := readUnboundDrainFixture(t, "CAIRN_R06_UNBOUND_FIXTURE")
	credential := nativeText(mustCurrentnessRead(t, f.Config.Path).Get("token_file"))
	for _, kind := range []string{"proposed-inode", "credential-inode"} {
		t.Run(kind, func(t *testing.T) {
			plan := unboundControlPlan(t, f)
			pin := censusTestJSON(t, filepath.Join(f.Root, "public-drift-plan.json"), plan)
			next := plan["next"].([]install.File)[0]
			path := next.Path
			if kind == "credential-inode" {
				path = credential
			}
			info, err := os.Stat(path)
			if err != nil {
				t.Fatal(err)
			}
			var raw []byte
			if path != credential {
				raw, err = os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
			}
			backup := filepath.Join(f.Root, "preflight-actor-original")
			t.Cleanup(func() { _ = os.Remove(pin.Path) })
			cli, calls := unboundDrainReadCLI(t)
			call := cli.Host.Call
			changed := false
			var actorAfter map[string]string
			cli.Host.Call = func(ctx context.Context, cfg *value.Value, op string, body *value.Value, options NativeCallOptions) (*value.Value, error) {
				if op == "event-watch" && !changed {
					if err := os.Rename(path, backup); err != nil {
						t.Fatal(err)
					}
					writePrivateFile(t, path, raw, info.Mode().Perm())
					changed = true
					actorAfter = unboundControlSnapshot(t, f.Root, credential)
					saveUnboundControlSnapshot(t, "actor-after", actorAfter)
				}
				return call(ctx, cfg, op, body, options)
			}
			before := unboundControlSnapshot(t, f.Root, credential)
			saveUnboundControlSnapshot(t, "before", before)
			var out bytes.Buffer
			code := cli.RunUnitControl(context.Background(), []string{"inspect", "--plan", pin.Path, "--sha256", pin.SHA256}, &out)
			if changed {
				t.Cleanup(func() {
					_ = os.Remove(path)
					if err := os.Rename(backup, path); err != nil {
						t.Error(err)
					}
				})
			}
			after := unboundControlSnapshot(t, f.Root, credential)
			saveUnboundControlSnapshot(t, "after", after)
			var result UnitControlResult
			if err := json.Unmarshal(out.Bytes(), &result); err != nil {
				t.Fatal(err)
			}
			if !changed || code != 1 || result.Inspected || result.Exchange.Published || *calls != 2 || !strings.Contains(result.Error, "association changed during drain read") {
				t.Fatalf("identity drift survived public capture: code=%d calls=%d error=%q", code, *calls, result.Error)
			}
			if reflect.DeepEqual(before, after) || !reflect.DeepEqual(actorAfter, after) {
				t.Fatal("operation changed more than the declared owned actor")
			}
		})
	}
}

func TestR06UnboundControlAuthenticatedProcess(t *testing.T) {
	f := readUnboundDrainFixture(t, "CAIRN_R06_UNBOUND_AUTH_FIXTURE")
	profile := stage2ServicesProfile()
	cli := &CLI{Host: NewFileHost(FileHostOptions{Profile: profile}), Profile: profile}
	call := cli.Host.Call
	reads := map[string]int{}
	cli.Host.Call = func(ctx context.Context, cfg *value.Value, op string, body *value.Value, options NativeCallOptions) (*value.Value, error) {
		if op != "wake-attempts" && op != "event-watch" {
			t.Fatalf("public inspect attempted non-read %s", op)
		}
		result, err := call(ctx, cfg, op, body, options)
		if err == nil {
			reads[op]++
		}
		return result, err
	}
	plan := unboundControlPlan(t, f)
	pin := censusTestJSON(t, filepath.Join(f.Root, "public-auth-plan.json"), plan)
	var out bytes.Buffer
	code := cli.RunUnitControl(context.Background(), []string{"inspect", "--plan", pin.Path, "--sha256", pin.SHA256}, &out)
	var result UnitControlResult
	if err := json.Unmarshal(out.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if code != 0 || !result.Inspected || result.Exchange.Published || result.Census == nil || result.Census.NativeRefresh != "unobserved" || result.Census.ExternalCoverage != "unknown" || reads["wake-attempts"] != 1 || reads["event-watch"] != 1 {
		t.Fatalf("actual public API preflight failed: code=%d reads=%v error=%q", code, reads, result.Error)
	}
	t.Logf("actual public inspect authenticated reads: %v; finite preflight only", reads)
}

// Exercise the same preflight entry twice without a drain receipt, so a later
// drain reread cannot mask loss of the preflight's original identity comparison.
func TestR06UnboundControlPreflightBetweenReadsProcess(t *testing.T) {
	f := readUnboundDrainFixture(t, "CAIRN_R06_UNBOUND_FIXTURE")
	credential := nativeText(mustCurrentnessRead(t, f.Config.Path).Get("token_file"))
	raw, err := json.Marshal(unboundControlPlan(t, f))
	if err != nil {
		t.Fatal(err)
	}
	var plan UnitControlPlan
	if err := json.Unmarshal(raw, &plan); err != nil {
		t.Fatal(err)
	}
	cli, calls := unboundDrainReadCLI(t)
	var proof *unitControlUnboundPreflight
	if err := cli.validateUnitControlSelection(context.Background(), &plan, "inspect", &proof); err != nil {
		t.Fatal(err)
	}
	path := plan.Next[0].Path
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	raw, err = os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	before := unboundControlSnapshot(t, f.Root, credential)
	saveUnboundControlSnapshot(t, "before", before)
	backup := filepath.Join(f.Root, "preflight-between-original")
	if err := os.Rename(path, backup); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = os.Remove(path)
		if err := os.Rename(backup, path); err != nil {
			t.Error(err)
		}
	})
	writePrivateFile(t, path, raw, info.Mode().Perm())
	actorAfter := unboundControlSnapshot(t, f.Root, credential)
	saveUnboundControlSnapshot(t, "actor-after", actorAfter)
	err = cli.validateUnitControlSelection(context.Background(), &plan, "inspect", &proof)
	after := unboundControlSnapshot(t, f.Root, credential)
	saveUnboundControlSnapshot(t, "after", after)
	if err == nil || !strings.Contains(err.Error(), "unbound preflight association/identity changed") || *calls != 0 {
		t.Fatalf("preflight lost original proposed-file identity: calls=%d error=%v", *calls, err)
	}
	if reflect.DeepEqual(before, after) || !reflect.DeepEqual(actorAfter, after) {
		t.Fatal("preflight refusal changed more than its owned actor")
	}
}
