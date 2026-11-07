package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	"github.com/halbritt/cairn/core"
)

type foundationPolicyResult struct {
	called bool
	loaded []byte
	err    error
}

type foundationPolicyProfile struct {
	name, seed, absoluteError, sizeError, validationError, parsePrefix string
	maxBytes                                                           int
	invoke                                                             func(*testing.T, string, string) foundationPolicyResult
}

var foundationCallbackError = errors.New("synthetic callback error")

func foundationPolicyJSON(t *testing.T, value any) []byte {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func foundationPolicyProfiles() []foundationPolicyProfile {
	return []foundationPolicyProfile{
		{
			name: "send-journal", maxBytes: 8192,
			seed:            "{\"schema\":\"cairn.send-journal/1\",\"intent_sha256\":\"" + strings.Repeat("0", 64) + "\",\"source_request_id\":\"11111111-1111-4111-8111-111111111111\",\"publication_request_id\":\"22222222-2222-4222-8222-222222222222\"}",
			absoluteError:   "--journal requires an absolute path",
			sizeError:       "send journal must be an owner-only regular file under 8 KiB",
			validationError: "invalid send journal identity", parsePrefix: "invalid send journal",
			invoke: func(t *testing.T, path, mode string) (result foundationPolicyResult) {
				result.err = sendJournalStore(path).Update(func(state sendJournal, _ func(sendJournal) error) (sendJournal, bool, error) {
					result.called, result.loaded = true, foundationPolicyJSON(t, state)
					switch mode {
					case "invalid-next":
						state.Schema = "invalid-next"
					case "large-next":
						state.IntentSHA256 = strings.Repeat("x", 8193)
					case "callback-error":
						state.IntentSHA256 = strings.Repeat("1", 64)
						return state, true, foundationCallbackError
					}
					return state, true, nil
				})
				return result
			},
		},
		{
			name: "attention", maxBytes: 512 * 1024,
			seed:            "{\"schema\":\"cairn.delivery-attention/1\",\"scope\":\"fixture\",\"deliveries\":{}}",
			absoluteError:   "attention state_file must be absolute",
			sizeError:       "attention state exceeds size limit",
			validationError: "attention state scope or schema mismatch; use a separate state file", parsePrefix: "invalid attention state",
			invoke: func(t *testing.T, path, mode string) (result foundationPolicyResult) {
				_, result.err = withAttentionCache(core.AttentionOptions{StateFile: path}, "fixture",
					func(state core.AttentionCache) (core.AttentionResult, core.AttentionCache, error) {
						result.called, result.loaded = true, foundationPolicyJSON(t, state)
						switch mode {
						case "invalid-next":
							state.Schema = "invalid-next"
						case "large-next":
							state.Scope = strings.Repeat("x", 512*1024+1)
						case "callback-error":
							state.Deliveries["fixture"] = core.AttentionEntry{Fingerprint: "changed"}
							return core.AttentionResult{}, state, foundationCallbackError
						}
						return core.AttentionResult{}, state, nil
					})
				return result
			},
		},
		{
			name: "delivery-alerts", maxBytes: 1024 * 1024,
			seed:            "{\"schema\":\"cairn.delivery-alerts/1\",\"scope\":\"fixture\",\"alerts\":{}}",
			absoluteError:   "delivery-alerts state_file must be absolute",
			sizeError:       "delivery alert state exceeds size limit",
			validationError: "delivery alert state scope or schema mismatch; use a separate state file", parsePrefix: "invalid delivery alert state",
			invoke: func(t *testing.T, path, mode string) (result foundationPolicyResult) {
				result.err = withDeliveryAlertState(path, "fixture", true, func(state deliveryAlertState) (deliveryAlertState, error) {
					result.called, result.loaded = true, foundationPolicyJSON(t, state)
					switch mode {
					case "invalid-next":
						state.Schema = "invalid-next"
					case "large-next":
						state.Scope = strings.Repeat("x", 1024*1024+1)
					case "callback-error":
						state.Alerts["fixture"] = deliveryAlertEntry{Ref: "changed"}
						return state, foundationCallbackError
					}
					return state, nil
				})
				return result
			},
		},
	}
}

func TestFoundationCharacterizationCallerStatePolicies(t *testing.T) {
	for _, profile := range foundationPolicyProfiles() {
		for _, name := range []string{
			"empty", "relative", "missing", "at-max", "max-plus-one", "mode-0640", "directory",
			"state-symlink", "lock-symlink", "lock-mode-0640", "directory-0770", "directory-0755",
			"trailing", "unknown-field", "malformed", "invalid-loaded", "invalid-next", "large-next",
			"callback-error", "create-temp-failure", "rename-failure",
		} {
			t.Run(profile.name+"/"+name, func(t *testing.T) {
				directory := t.TempDir()
				if err := os.Chmod(directory, 0700); err != nil {
					t.Fatal(err)
				}
				path := filepath.Join(directory, "state.json")
				raw := []byte(profile.seed)
				mode := "same"
				switch name {
				case "at-max":
					raw = append(raw, bytes.Repeat([]byte(" "), profile.maxBytes-len(raw))...)
				case "max-plus-one":
					raw = bytes.Repeat([]byte(" "), profile.maxBytes+1)
				case "trailing":
					raw = append(raw, []byte(" {}")...)
				case "unknown-field":
					raw = append(raw[:len(raw)-1], []byte(",\"unknown\":true}")...)
				case "malformed":
					raw = []byte("{")
				case "invalid-loaded":
					raw = []byte("{\"schema\":\"invalid\"}")
				case "invalid-next", "large-next", "callback-error":
					mode = name
				}
				if name != "empty" && name != "relative" && name != "missing" {
					if err := os.WriteFile(path, raw, 0600); err != nil {
						t.Fatal(err)
					}
				}
				switch name {
				case "empty":
					path = ""
				case "relative":
					current, err := os.Getwd()
					if err != nil {
						t.Fatal(err)
					}
					path, err = filepath.Rel(current, path)
					if err != nil {
						t.Fatal(err)
					}
				case "mode-0640":
					if err := os.Chmod(path, 0640); err != nil {
						t.Fatal(err)
					}
				case "directory":
					if err := os.Remove(path); err != nil {
						t.Fatal(err)
					}
					if err := os.Mkdir(path, 0700); err != nil {
						t.Fatal(err)
					}
				case "state-symlink":
					if err := os.Rename(path, path+".target"); err != nil {
						t.Fatal(err)
					}
					if err := os.Symlink(path+".target", path); err != nil {
						t.Fatal(err)
					}
				case "lock-symlink":
					if err := os.WriteFile(path+".target", nil, 0600); err != nil {
						t.Fatal(err)
					}
					if err := os.Symlink(path+".target", path+".lock"); err != nil {
						t.Fatal(err)
					}
				case "lock-mode-0640":
					if err := os.WriteFile(path+".lock", nil, 0640); err != nil {
						t.Fatal(err)
					}
					if err := os.Chmod(path+".lock", 0640); err != nil {
						t.Fatal(err)
					}
				case "directory-0770", "directory-0755", "create-temp-failure":
					permissions := os.FileMode(0770)
					if name == "directory-0755" {
						permissions = 0755
					}
					if name == "create-temp-failure" {
						if err := os.WriteFile(path+".lock", nil, 0600); err != nil {
							t.Fatal(err)
						}
						permissions = 0500
					}
					if err := os.Chmod(directory, permissions); err != nil {
						t.Fatal(err)
					}
					t.Cleanup(func() { os.Chmod(directory, 0700) })
				case "rename-failure":
					original := lockedStateRename
					lockedStateRename = func(from, to string) error {
						return &os.LinkError{Op: "rename", Old: from, New: to, Err: syscall.EIO}
					}
					t.Cleanup(func() { lockedStateRename = original })
				}
				var before os.FileInfo
				if path != "" && filepath.IsAbs(path) {
					before, _ = os.Lstat(path)
				}
				result := profile.invoke(t, path, mode)
				var refusal *core.Error
				switch {
				case name == "relative":
					if !errors.As(result.err, &refusal) || refusal.Code != "INVALID_REQUEST" || refusal.Message != profile.absoluteError {
						t.Fatalf("relative path: %v", result.err)
					}
				case name == "max-plus-one" || profile.name == "send-journal" && (name == "mode-0640" || name == "directory"):
					if !errors.As(result.err, &refusal) || refusal.Code != "INVALID_REQUEST" || refusal.Message != profile.sizeError {
						t.Fatalf("size/mode/type: %v", result.err)
					}
				case profile.name == "send-journal" && name == "lock-mode-0640":
					if !errors.As(result.err, &refusal) || refusal.Message != "send journal lock must be owner-only regular file" {
						t.Fatalf("lock mode: %v", result.err)
					}
				case profile.name == "send-journal" && name == "directory-0770":
					if !errors.As(result.err, &refusal) || refusal.Message != "send journal directory must not be writable by others" {
						t.Fatalf("directory mode: %v", result.err)
					}
				case name == "state-symlink" || name == "lock-symlink":
					if !errors.Is(result.err, syscall.ELOOP) {
						t.Fatalf("symlink outcome: %v", result.err)
					}
				case name == "directory":
					var pathError *os.PathError
					if !errors.Is(result.err, syscall.EISDIR) || !errors.As(result.err, &pathError) || pathError.Op != "read" {
						t.Fatalf("non-private directory outcome: %v", result.err)
					}
				case name == "trailing" && profile.name == "send-journal":
					if !errors.As(result.err, &refusal) || refusal.Message != "send journal contains trailing data" {
						t.Fatalf("strict trailing outcome: %v", result.err)
					}
				case name == "trailing" || name == "malformed":
					if result.err == nil {
						t.Fatalf("parse outcome: %v", result.err)
					}
					text := result.err.Error()
					if errors.As(result.err, &refusal) {
						text = refusal.Message
					}
					if !strings.HasPrefix(text, profile.parsePrefix+": ") {
						t.Fatalf("parse prefix: %v", result.err)
					}
					message := "unexpected end of JSON input"
					if name == "trailing" {
						message = "invalid character '{' after top-level value"
					} else if profile.name == "send-journal" {
						message = "unexpected EOF"
					}
					if text != profile.parsePrefix+": "+message {
						t.Fatalf("parse text: %v", result.err)
					}
					if profile.name == "send-journal" {
						if !errors.As(result.err, &refusal) || refusal.Code != "INVALID_REQUEST" {
							t.Fatalf("strict parse class: %T", result.err)
						}
					} else {
						var syntax *json.SyntaxError
						if !errors.As(result.err, &syntax) {
							t.Fatalf("non-strict parse cause: %T", result.err)
						}
					}
				case name == "invalid-loaded":
					if !errors.As(result.err, &refusal) || refusal.Message != profile.validationError {
						t.Fatalf("validation outcome: %v", result.err)
					}
				case name == "create-temp-failure":
					if !result.called || !errors.Is(result.err, os.ErrPermission) {
						t.Fatalf("CreateTemp outcome: called=%v error=%v", result.called, result.err)
					}
				case name == "rename-failure":
					if !result.called || !errors.Is(result.err, syscall.EIO) {
						t.Fatalf("injected rename outcome: called=%v error=%v", result.called, result.err)
					}
				case name == "callback-error":
					if !result.called || !errors.Is(result.err, foundationCallbackError) {
						t.Fatalf("callback outcome: %v", result.err)
					}
				default:
					if !result.called || result.err != nil {
						t.Fatalf("accepted case: called=%v error=%v", result.called, result.err)
					}
				}
				if result.err != nil && name != "callback-error" && name != "rename-failure" && name != "create-temp-failure" && result.called {
					t.Fatal("load/path refusal reached callback")
				}
				observation := map[string]any{
					"profile": profile.name, "case": name, "path": path,
					"callback_reached": result.called, "loaded": json.RawMessage(result.loaded),
					"error_class": fmt.Sprintf("%T", result.err), "error": fmt.Sprint(result.err),
					"seed_bytes": len(raw), "seed_sha256": fmt.Sprintf("%x", sha256.Sum256(raw)),
				}
				observation["input_exists"] = before != nil
				if before != nil {
					observation["input_mode"] = before.Mode().String()
					if before.Mode().IsRegular() {
						observation["input_bytes"], observation["input_sha256"] = len(raw), fmt.Sprintf("%x", sha256.Sum256(raw))
					}
				}
				if path != "" && filepath.IsAbs(path) {
					info, statErr := os.Lstat(path)
					if statErr == nil && info.Mode().IsRegular() {
						after, err := os.ReadFile(path)
						if err != nil {
							t.Fatal(err)
						}
						observation["output_bytes"], observation["output_sha256"] = len(after), fmt.Sprintf("%x", sha256.Sum256(after))
						observation["state_mode"] = fmt.Sprintf("%04o", info.Mode().Perm())
						if (name == "create-temp-failure" || name == "rename-failure" ||
							name == "callback-error" && profile.name == "attention") &&
							(!bytes.Equal(after, raw) || !os.SameFile(before, info)) {
							t.Fatal("failed/no-write persistence changed prior bytes or inode")
						}
						if name == "callback-error" && profile.name == "send-journal" {
							var persisted sendJournal
							if err := json.Unmarshal(after, &persisted); err != nil || persisted.IntentSHA256 != strings.Repeat("1", 64) {
								t.Fatalf("callback-error journal next state: %s, parse error: %v", after, err)
							}
						}
						if name == "callback-error" && profile.name == "delivery-alerts" {
							var persisted deliveryAlertState
							if err := json.Unmarshal(after, &persisted); err != nil || persisted.Alerts["fixture"].Ref != "changed" {
								t.Fatalf("callback-error alerts next state: %s, parse error: %v", after, err)
							}
						}
						if name == "large-next" {
							if len(after) <= profile.maxBytes {
								t.Fatal("write was unexpectedly size-capped")
							}
							next := profile.invoke(t, path, "same")
							if next.called || !errors.As(next.err, &refusal) || refusal.Message != profile.sizeError {
								t.Fatalf("oversized next read: %v", next.err)
							}
						}
						if name == "invalid-next" {
							next := profile.invoke(t, path, "same")
							if next.called || !errors.As(next.err, &refusal) || refusal.Message != profile.validationError {
								t.Fatalf("unvalidated next read: %v", next.err)
							}
						}
						if result.err == nil && info.Mode().Perm() != 0600 {
							t.Fatalf("created state mode: %v", info.Mode())
						}
					}
					if lock, err := os.Lstat(path + ".lock"); err == nil && lock.Mode().IsRegular() {
						observation["lock_mode"] = fmt.Sprintf("%04o", lock.Mode().Perm())
						expected := os.FileMode(0600)
						if name == "lock-mode-0640" {
							expected = 0640
						}
						if lock.Mode().Perm() != expected {
							t.Fatalf("lock mode: %v", lock.Mode())
						}
						file, err := os.OpenFile(path+".lock", os.O_RDWR, 0)
						if err != nil {
							t.Fatal(err)
						}
						if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
							file.Close()
							t.Fatalf("lock remained held: %v", err)
						}
						syscall.Flock(int(file.Fd()), syscall.LOCK_UN)
						file.Close()
						observation["lock_reacquired"] = true
					}
				}
				temporary, err := filepath.Glob(filepath.Join(directory, ".cairn-*"))
				if err != nil || len(temporary) != 0 {
					t.Fatalf("temporary cleanup: %v %v", temporary, err)
				}
				t.Logf("foundation-characterization %s", foundationPolicyJSON(t, observation))
			})
		}
	}
}

func TestFoundationCharacterizationMissingValidationAndNonblockingFIFO(t *testing.T) {
	validated := 0
	path := filepath.Join(t.TempDir(), "missing.json")
	store := LockedStateFile[map[string]string]{
		Path: path, Default: func() map[string]string { return map[string]string{"default": "retained"} },
		Validate: func(map[string]string) error { validated++; return errors.New("validation reached") },
	}
	err := store.Update(func(state map[string]string, _ func(map[string]string) error) (map[string]string, bool, error) {
		if state["default"] != "retained" {
			t.Fatal("missing state lost Default")
		}
		return state, false, nil
	})
	if err != nil || validated != 0 {
		t.Fatalf("missing state validation: %v, calls=%d", err, validated)
	}
	if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("read-only missing state was published: %v", err)
	}
	directory := t.TempDir()
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	fifo := filepath.Join(directory, "fifo")
	if err := syscall.Mkfifo(fifo, 0600); err != nil {
		t.Fatal(err)
	}
	called := false
	err = sendJournalStore(fifo).Update(func(state sendJournal, _ func(sendJournal) error) (sendJournal, bool, error) {
		called = true
		return state, false, nil
	})
	var refusal *core.Error
	if called || !errors.As(err, &refusal) || refusal.Message != "send journal must be an owner-only regular file under 8 KiB" {
		t.Fatalf("nonblocking FIFO: called=%v error=%v", called, err)
	}
	t.Logf("foundation-characterization %s", foundationPolicyJSON(t, map[string]any{
		"case": "missing-default-and-send-journal-fifo", "validation_calls": validated,
		"fifo_callback_reached": called, "error_class": fmt.Sprintf("%T", err), "error": err.Error(),
	}))
}
