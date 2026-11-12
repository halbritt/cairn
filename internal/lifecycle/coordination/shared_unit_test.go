package coordination

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func sharedWatchFixture(t *testing.T) (WatchBindings, []install.File, string) {
	t.Helper()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	active := WatchBindings{Directory: filepath.Join(root, "bindings"), Pins: map[string]string{}}
	var next []install.File
	for _, name := range []string{"one.json", "two.json"} {
		old := []byte("{\"binding\":\"" + name + "\",\"selection\":\"old\",\"state_dir\":\"/shared/state\"}\n")
		new := bytes.Replace(old, []byte("old"), []byte("new"), 1)
		active.Pins[name] = writePrivateFile(t, filepath.Join(active.Directory, name), old, 0600)
		path := filepath.Join(root, "generation", name)
		next = append(next, install.File{Name: name, Path: path, SHA256: writePrivateFile(t, path, new, 0400)})
	}
	return active, next, filepath.Join(root, "retained")
}

func TestSharedWatchUnitExchangeAndReverse(t *testing.T) {
	old, next, retained := sharedWatchFixture(t)
	lock := WatcherLockPath(old.Directory)
	writePrivateFile(t, lock, nil, 0644)
	beforeLock, err := os.Stat(lock)
	if err != nil {
		t.Fatal(err)
	}
	state := filepath.Join(filepath.Dir(old.Directory), "shared-state", "journal.json")
	retainedState := []byte("{\"request_id\":\"retained-retry\",\"reply_possible\":true}\n")
	writePrivateFile(t, state, retainedState, 0600)
	result, err := exchangeWatchBindings(context.Background(), old, retained, next, nil)
	if err != nil || !result.Published {
		t.Fatalf("exchange: result=%+v err=%v", result, err)
	}
	for _, dir := range []string{result.Active.Directory, result.Retained.Directory} {
		info, err := os.Stat(WatcherLockPath(dir))
		if err != nil || !os.SameFile(beforeLock, info) || info.Mode().Perm() != 0644 {
			t.Fatalf("changed shared lock in %s: info=%v err=%v", dir, info, err)
		}
	}
	if err := readWatchBindings(result.Active); err != nil {
		t.Fatal(err)
	}
	if err := readWatchBindings(result.Retained); err != nil {
		t.Fatal(err)
	}
	lease, err := AcquireWatcherLock(result.Retained.Directory)
	if err != nil {
		t.Fatal(err)
	}
	if other, err := AcquireWatcherLock(result.Active.Directory); !IsSessionBusy(err) {
		if other != nil {
			mustRelease(t, other)
		}
		t.Fatalf("retained watcher did not exclude active watcher: %v", err)
	}
	mustRelease(t, lease)
	var reverse []install.File
	for _, name := range []string{"one.json", "two.json"} {
		reverse = append(reverse, install.File{Name: name, Path: filepath.Join(retained, name), SHA256: old.Pins[name]})
	}
	back, err := exchangeWatchBindings(context.Background(), result.Active, retained+"-new", reverse, nil)
	if err != nil || !back.Published {
		t.Fatalf("reverse: result=%+v err=%v", back, err)
	}
	if err := readWatchBindings(WatchBindings{Directory: old.Directory, Pins: old.Pins}); err != nil {
		t.Fatal(err)
	}
	gotState, err := os.ReadFile(state)
	if err != nil || !bytes.Equal(gotState, retainedState) {
		t.Fatalf("shared state changed: %q %v", gotState, err)
	}
}

func TestSharedWatchUnitRefusesRunningWatcher(t *testing.T) {
	old, next, retained := sharedWatchFixture(t)
	lease, err := AcquireWatcherLock(old.Directory)
	if err != nil {
		t.Fatal(err)
	}
	defer mustRelease(t, lease)
	result, err := exchangeWatchBindings(context.Background(), old, retained, next, nil)
	if !IsSessionBusy(err) || result.Published {
		t.Fatalf("running watcher accepted: %+v %v", result, err)
	}
	if _, err := os.Lstat(retained); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("refusal created retained directory: %v", err)
	}
	if err := readWatchBindings(old); err != nil {
		t.Fatal(err)
	}
}

func TestSharedWatchUnitPinAndMembershipRefusals(t *testing.T) {
	wantError := map[string]string{
		"old-pin": "watch config pin changed", "new-pin": "next watch config pin mismatch",
		"extra": "membership changed", "missing": "membership changed", "symlink": "membership changed",
		"duplicate": "duplicate next watch binding", "omitted": "must retain every binding name",
		"existing-target": "retained watch selection already exists", "name": "must retain every binding name",
	}
	for _, change := range []string{"old-pin", "new-pin", "extra", "missing", "duplicate", "omitted", "symlink", "existing-target", "name"} {
		t.Run(change, func(t *testing.T) {
			old, next, retained := sharedWatchFixture(t)
			switch change {
			case "old-pin":
				old.Pins["one.json"] = strings.Repeat("0", 64)
			case "new-pin":
				next[1].SHA256 = strings.Repeat("0", 64)
			case "extra":
				writePrivateFile(t, filepath.Join(old.Directory, "extra.json"), []byte("{}"), 0600)
			case "missing":
				if err := os.Remove(filepath.Join(old.Directory, "two.json")); err != nil {
					t.Fatal(err)
				}
			case "duplicate":
				next[1] = next[0]
			case "omitted":
				next = next[:1]
			case "symlink":
				path := filepath.Join(old.Directory, "one.json")
				if err := os.Remove(path); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(next[0].Path, path); err != nil {
					t.Fatal(err)
				}
			case "existing-target":
				if err := os.Mkdir(retained, 0700); err != nil {
					t.Fatal(err)
				}
			case "name":
				next[1].Name = "../escape.json"
			}
			result, err := exchangeWatchBindings(context.Background(), old, retained, next, nil)
			if err == nil || result.Published || !strings.Contains(err.Error(), wantError[change]) {
				t.Fatalf("invalid selection accepted: %+v %v", result, err)
			}
		})
	}
}

func TestSharedWatchUnitEveryPublicationBoundary(t *testing.T) {
	boundaries := []string{"watch-lock-sync", "write:one.json", "file-sync:one.json", "write:two.json", "file-sync:two.json", "prepared-sync", "active-sync", "parent-preparation-sync", "exchange", "parent-publication-sync"}
	for _, boundary := range boundaries {
		for _, side := range []string{"before:", "after:"} {
			t.Run(side+boundary, func(t *testing.T) {
				old, next, retained := sharedWatchFixture(t)
				fault := errors.New("selected publication failure")
				result, err := exchangeWatchBindings(context.Background(), old, retained, next, func(step string) error {
					if step == side+boundary {
						return fault
					}
					return nil
				})
				if !errors.Is(err, fault) {
					t.Fatalf("fault not reached: %+v %v", result, err)
				}
				expectNew := boundary == "exchange" && side == "after:" || boundary == "parent-publication-sync"
				if result.Published != expectNew {
					t.Fatalf("wrong published state: %+v, expected %v", result, expectNew)
				}
				selected := old
				if expectNew {
					selected = result.Active
					if err := readWatchBindings(result.Retained); err != nil {
						t.Fatal(err)
					}
				}
				if err := readWatchBindings(selected); err != nil {
					t.Fatalf("partial active selection after %s%s: %v", side, boundary, err)
				}
				lease, lockErr := AcquireWatcherLock(selected.Directory)
				if lockErr != nil {
					t.Fatalf("failure retained lock ownership: %v", lockErr)
				}
				mustRelease(t, lease)
			})
		}
	}
}

func TestSharedWatchUnitDetectsChangeBeforeExchange(t *testing.T) {
	for _, change := range []string{"active-config", "canonical-config", "prepared-config", "watch-lock"} {
		t.Run(change, func(t *testing.T) {
			old, next, retained := sharedWatchFixture(t)
			result, err := exchangeWatchBindings(context.Background(), old, retained, next, func(step string) error {
				if step != "before:exchange" {
					return nil
				}
				var path string
				switch change {
				case "active-config":
					path = filepath.Join(old.Directory, "one.json")
				case "canonical-config":
					path = next[0].Path
				case "prepared-config":
					path = filepath.Join(retained, "one.json")
				case "watch-lock":
					path = WatcherLockPath(old.Directory)
					if err := os.Remove(path); err != nil {
						return err
					}
				}
				if err := os.Chmod(path, 0600); err != nil && change != "watch-lock" {
					return err
				}
				return os.WriteFile(path, []byte("changed"), 0600)
			})
			if err == nil || result.Published {
				t.Fatalf("concurrent %s change accepted: %+v %v", change, result, err)
			}
		})
	}
}

// The installed-process fixture builds this package as a private test binary.
// This helper adds no production CLI or registration surface.
func TestSharedWatchUnitProcess(t *testing.T) {
	path := os.Getenv("CAIRN_R06_WATCH_EXCHANGE_PLAN")
	if path == "" {
		return
	}
	var plan struct {
		Current     WatchBindings
		Retained    string
		Next        []install.File
		Crash       string
		Result      string
		Guard       bool
		Callbacks   []map[string]any
		Inject      []string
		InjectInput string
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &plan); err != nil {
		t.Fatal(err)
	}
	observe := func(step string) error {
		if step == plan.Crash {
			os.Exit(86)
		}
		if step == "before:exchange" && len(plan.Inject) > 0 {
			command := exec.Command(plan.Inject[0], plan.Inject[1:]...)
			command.Stdin = strings.NewReader(plan.InjectInput)
			output, err := command.CombinedOutput()
			if err != nil {
				return errors.Join(err, errors.New(string(output)))
			}
		}
		return nil
	}
	var result WatchBindingsExchange
	var runErr error
	var drainFacts map[string]string
	if plan.Guard {
		profile := stage2ServicesProfile()
		cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile}), ProcRoot: "/proc"}
		var callbacks []*value.Value
		if plan.Callbacks != nil {
			callbacks = []*value.Value{}
		}
		for _, ref := range plan.Callbacks {
			raw, err := json.Marshal(ref)
			if err != nil {
				t.Fatal(err)
			}
			decoded, err := value.DecodeBytes(raw, 0)
			if err != nil {
				t.Fatal(err)
			}
			callbacks = append(callbacks, decoded)
		}
		receipt, err := cli.CaptureWatchDrain(context.Background(), plan.Current, callbacks)
		if err != nil {
			runErr = err
		} else {
			drainFacts = receipt.Facts()
			result, runErr = exchangeDrainedWatchBindings(context.Background(), receipt, plan.Retained, plan.Next, observe)
		}
	} else {
		result, runErr = exchangeWatchBindings(context.Background(), plan.Current, plan.Retained, plan.Next, observe)
	}
	output := struct {
		WatchBindingsExchange
		Error      string
		DrainFacts map[string]string
	}{WatchBindingsExchange: result, DrainFacts: drainFacts}
	if runErr != nil {
		output.Error = runErr.Error()
	}
	raw, err = json.Marshal(output)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(plan.Result, raw, 0600); err != nil {
		t.Fatal(err)
	}
	if runErr != nil {
		t.Fatal(runErr)
	}
}
