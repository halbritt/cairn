package install

import (
	"bytes"
	"context"
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
)

const agyDuplicateCommand = "/pinned/cairn hook"
const agyDuplicateHook = `{ "type":"command", "command":"/pinned/cairn hook", "timeout":7 }`
const agyCarriedHook = `{ "command" : "/usr/bin/printf \"cairn docs\"", "type" : "command", "timeout":7 }`

type agyDuplicateFile struct {
	Mode       uint32
	Dev, Inode uint64
	Raw        []byte
}

func agyDuplicateInventory(t *testing.T, root string) map[string]agyDuplicateFile {
	t.Helper()
	files := map[string]agyDuplicateFile{}
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		stat := info.Sys().(*syscall.Stat_t)
		file := agyDuplicateFile{Mode: uint32(info.Mode()), Dev: uint64(stat.Dev), Inode: stat.Ino}
		if info.Mode().IsRegular() && info.Mode().Perm()&0400 != 0 {
			file.Raw, err = os.ReadFile(path)
			if err != nil {
				return err
			}
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		files[relative] = file
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return files
}

func agyDuplicateInputs(t *testing.T, layer, outside, managed, prepared string) (AgyLayerPlan, string, string, map[string]agyDuplicateFile) {
	t.Helper()
	plan, selected, other := agyTestPlan(t, layer, true)
	root := filepath.Dir(plan.AccountHome)
	token := filepath.Join(root, "synthetic-unreadable.token")
	agyTestWrite(t, token, "synthetic credential; staging must not read it", 0000)
	selector, err := json.Marshal(token)
	if err != nil {
		t.Fatal(err)
	}
	old := "{\n  \"opaque\" : {\"unicode\":\"日本語 😀\",\"big\":90071992547409931234567890123456789},\n  \"token_file\" : " + string(selector)
	if outside != "" {
		old += ",\n" + outside
	}
	if managed != "" {
		old += ",\n\"cairn-coordination\":" + managed
	}
	old += "\n}\n"
	agyTestWrite(t, selected, old, 0640)
	agyTestWrite(t, plan.Settings.Path, `{"cairn-coordination":`+prepared+`}`, 0400)
	plan.Settings.SHA256 = agyDigest(agyTestBytes(t, plan.Settings.Path))
	plan.RecoveryPath = filepath.Join(filepath.Dir(selected), ".cairn-recovery-agy-1234567890abcdef1234567890abcdef.json")
	agyTestWrite(t, filepath.Join(filepath.Dir(selected), ".cairn-agy-existing.record"), "retained prior correspondence\n", 0400)
	agyTestWrite(t, filepath.Join(filepath.Dir(selected), ".cairn-agy-existing.backup"), "retained prior backup\n", 0640)
	before := agyDuplicateInventory(t, root)
	t.Cleanup(func() {
		if destination := os.Getenv("CAIRN_AGY_DUPLICATE_EVIDENCE"); destination != "" {
			path := filepath.Join(destination, strings.ReplaceAll(t.Name(), "/", "-"))
			if err := os.MkdirAll(path, 0700); err != nil {
				t.Fatal(err)
			}
			raw, err := json.MarshalIndent(struct {
				Root          string
				Plan          AgyLayerPlan
				Before, After map[string]agyDuplicateFile
				Failed        bool
			}{root, plan, before, agyDuplicateInventory(t, root), t.Failed()}, "", "  ")
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(path, "observation.json"), raw, 0600); err != nil {
				t.Fatal(err)
			}
		}
	})
	return plan, selected, other, before
}

func TestAgyNewDispatchDuplicateRefusesBeforeAnyPublication(t *testing.T) {
	out := `"audit-hooks":{"Stop":[` + agyDuplicateHook + `]}`
	for _, layer := range []string{"account", "project"} {
		for _, test := range []struct{ name, outside, managed, prepared string }{
			{"new-managed-group", out, "", `{"Stop":[` + agyDuplicateHook + `]}`},
			{"carried-plus-new", out, `{"Stop":[` + agyCarriedHook + `]}`, `{"Stop":[` + agyCarriedHook + `,` + agyDuplicateHook + `]}`},
			{"new-multiplicity", out, `{"Stop":[` + agyDuplicateHook + `]}`, `{"Stop":[` + agyDuplicateHook + `,` + agyDuplicateHook + `]}`},
			{"replace-list-does-not-delete-outside", out, "", `{"Stop":[` + agyDuplicateHook + `]}`},
			{"escaped-same-command", out, "", `{"Stop":[{"type":"command","command":"\u002fpinned/cairn hook","timeout":15}]}`},
			{"opaque-siblings-do-not-hide-actual-hook", `"audit-hooks":{"Stop":[null,{"type":"prompt","command":"unrelated"},` + agyDuplicateHook + `]}`, "", `{"Stop":[` + agyDuplicateHook + `]}`},
		} {
			t.Run(layer+"/"+test.name, func(t *testing.T) {
				plan, _, _, before := agyDuplicateInputs(t, layer, test.outside, test.managed, test.prepared)
				if test.name == "replace-list-does-not-delete-outside" {
					plan.ReplaceCommands = []string{agyDuplicateCommand}
				}
				selection, err := PublishAgyLayer(context.Background(), plan)
				if err == nil || !strings.Contains(err.Error(), "duplicates selected-layer group audit-hooks") || selection != (AgyLayerSelection{}) {
					t.Fatalf("new duplicate was not refused by the public publisher: %+v %v", selection, err)
				}
				after := agyDuplicateInventory(t, filepath.Dir(plan.AccountHome))
				if !reflect.DeepEqual(before, after) {
					t.Fatal("refusal changed raw bytes, modes, inodes or file/record inventory")
				}
			})
		}
	}
}

func TestAgyNewDispatchPreservesExistingHooksAndExactScope(t *testing.T) {
	out := `"audit-hooks":{"Stop":[` + agyDuplicateHook + `]}`
	for _, layer := range []string{"account", "project"} {
		for _, test := range []struct{ name, outside, managed, prepared string }{
			{"distinct-command", out, "", `{"Stop":[` + agyCarriedHook + `]}`},
			{"different-event", out, "", `{"PreInvocation":[` + agyDuplicateHook + `]}`},
			{"literal-whitespace-distinct", out, "", `{"Stop":[{"type":"command","command":"/pinned/cairn hook ","timeout":15}]}`},
			{"carried-unrelated-duplicate", `"audit-hooks":{"Stop":[` + agyCarriedHook + `]}`, `{"Stop":[` + agyCarriedHook + `]}`, `{"Stop":[` + agyCarriedHook + `,` + agyDuplicateHook + `]}`},
			{"preexisting-multiplicity", out, `{"Stop":[` + agyDuplicateHook + `,` + agyDuplicateHook + `]}`, `{"Stop":[` + agyDuplicateHook + `,` + agyDuplicateHook + `,` + agyCarriedHook + `]}`},
			{"opaque-command-metadata", `"metadata":{"command":"/pinned/cairn hook","Stop":{"type":"command","command":"/pinned/cairn hook"},"nested":{"Stop":[` + agyDuplicateHook + `]}}`, "", `{"Stop":[` + agyDuplicateHook + `]}`},
			{"case-distinct-field-names", `"audit-hooks":{"Stop":[{"TYPE":"command","COMMAND":"/pinned/cairn hook"}]}`, "", `{"Stop":[` + agyDuplicateHook + `]}`},
			{"prompt-command-metadata", `"audit-hooks":{"Stop":[{"type":"prompt","command":"/pinned/cairn hook"}]}`, "", `{"Stop":[` + agyDuplicateHook + `]}`},
		} {
			t.Run(layer+"/"+test.name, func(t *testing.T) {
				plan, selected, other, before := agyDuplicateInputs(t, layer, test.outside, test.managed, test.prepared)
				selection, err := PublishAgyLayer(context.Background(), plan)
				if err != nil || !selection.Changed {
					t.Fatalf("allowed exact-scope publication refused: %+v %v", selection, err)
				}
				actual := agyTestBytes(t, selected)
				for _, raw := range []string{`"unicode":"日本語 😀"`, `"big":90071992547409931234567890123456789`, `"token_file" : `, test.outside} {
					if !bytes.Contains(actual, []byte(raw)) {
						t.Fatalf("unrelated raw setting changed: %s", raw)
					}
				}
				if test.managed != "" {
					for _, hook := range []string{agyCarriedHook, agyDuplicateHook} {
						if count := strings.Count(test.managed, hook); count > 0 && bytes.Count(actual, []byte(hook)) < count {
							t.Fatal("carried hook bytes or multiplicity lost")
						}
					}
				}
				root := filepath.Dir(plan.AccountHome)
				after := agyDuplicateInventory(t, root)
				for _, path := range []string{selected, other, plan.Settings.Path, filepath.Join(root, "synthetic-unreadable.token")} {
					relative, err := filepath.Rel(root, path)
					if err != nil {
						t.Fatal(err)
					}
					if path == selected {
						if after[relative].Mode != before[relative].Mode {
							t.Fatal("publication changed selected mode")
						}
					} else if !reflect.DeepEqual(after[relative], before[relative]) {
						t.Fatal("publication changed another input, selector, mode or inode", path)
					}
				}
				again, err := PublishAgyLayer(context.Background(), plan)
				if err != nil || again.Changed || !reflect.DeepEqual(after, agyDuplicateInventory(t, root)) {
					t.Fatal("repeat publication changed bytes, inode or record inventory", again, err)
				}
			})
		}
	}
}
