package install

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const agyPreparedTest = `{"ignored":"not-installed","cairn-coordination":{"PreInvocation":[{"type":"command","command":"/pinned/cairn hook --event PreInvocation","timeout":15}]}}`
const agyOldTest = "{\n  \"user\" : {\"big\":99999999999999999999999999999999999,\"unicode\":\"\\ud83d\\ude00\"},\n  \"token_file\" : \"/never-read.token\"\n}\n"

func agyTestWrite(t *testing.T, path, raw string, mode os.FileMode) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0750); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, 0600); err != nil && !errors.Is(err, os.ErrNotExist) {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(raw), mode); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, mode); err != nil {
		t.Fatal(err)
	}
}

func agyTestPlan(t *testing.T, layer string, existing bool) (AgyLayerPlan, string, string) {
	t.Helper()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	plan := AgyLayerPlan{AccountHome: filepath.Join(root, "account"), Workspace: filepath.Join(root, "workspace"), Layer: layer,
		Settings: AgyLayerSettings{Path: filepath.Join(root, "prepared.json"), SHA256: agyDigest([]byte(agyPreparedTest))}}
	for _, path := range []string{plan.AccountHome, plan.Workspace} {
		if err := os.Mkdir(path, 0750); err != nil {
			t.Fatal(err)
		}
	}
	agyTestWrite(t, plan.Settings.Path, agyPreparedTest, 0400)
	selected, other := filepath.Join(plan.AccountHome, ".gemini", "config", "hooks.json"), filepath.Join(plan.Workspace, ".agents", "hooks.json")
	if layer == "project" {
		selected, other = other, selected
	}
	if existing {
		agyTestWrite(t, selected, agyOldTest, 0640)
	}
	agyTestWrite(t, other, `{"other-hooks":{"Stop":[{"type":"command","command":"echo audit"}]},"token_file":"/never-open"}`, 0600)
	return plan, selected, other
}

func agyTestBytes(t *testing.T, path string) []byte {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestAgyLayerPublishesBothSelectionsPreservingBytesModesAndIdempotence(t *testing.T) {
	for _, layer := range []string{"account", "project"} {
		for _, existing := range []bool{false, true} {
			t.Run(layer+"/"+map[bool]string{true: "existing", false: "absent"}[existing], func(t *testing.T) {
				plan, selected, other := agyTestPlan(t, layer, existing)
				otherBytes := agyTestBytes(t, other)
				otherInfo, err := os.Stat(other)
				if err != nil {
					t.Fatal(err)
				}
				result, err := PublishAgyLayer(context.Background(), plan)
				if err != nil || !result.Changed || result.Path != selected || result.Layer != layer {
					t.Fatalf("actual publication: %+v %v", result, err)
				}
				actual := agyTestBytes(t, selected)
				if result.SHA256 != agyDigest(actual) || bytes.Contains(actual, []byte("not-installed")) {
					t.Fatal("wrong actual publication pin or unrelated prepared field copied")
				}
				if existing {
					for _, retained := range []string{`"big":99999999999999999999999999999999999`, `"unicode":"\ud83d\ude00"`, `"token_file" : "/never-read.token"`} {
						if !bytes.Contains(actual, []byte(retained)) {
							t.Fatalf("lost exact unrelated bytes %s", retained)
						}
					}
				}
				info, err := os.Stat(selected)
				if err != nil || info.Mode().Perm() != map[bool]os.FileMode{true: 0640, false: 0600}[existing] {
					t.Fatal("selected mode changed", info, err)
				}
				again, err := PublishAgyLayer(context.Background(), plan)
				after, statErr := os.Stat(selected)
				if err != nil || statErr != nil || again.Changed || !os.SameFile(info, after) || !bytes.Equal(actual, agyTestBytes(t, selected)) {
					t.Fatal("idempotence replaced selected bytes/inode", again, err, statErr)
				}
				otherAfter, err := os.Stat(other)
				if err != nil || !os.SameFile(otherInfo, otherAfter) || otherInfo.Mode() != otherAfter.Mode() || !bytes.Equal(otherBytes, agyTestBytes(t, other)) {
					t.Fatal("nonselected layer changed", err)
				}
			})
		}
	}
}

func TestAgyLayerRefusesBothActiveMalformedUnknownAndAliasesBeforeWrite(t *testing.T) {
	for _, problem := range []string{"identical", "other-group", "malformed", "duplicate", "nested-duplicate", "null-command", "unicode", "unreadable", "symlink-file", "symlink-directory", "hardlink", "prepared-pin", "root-symlink"} {
		t.Run(problem, func(t *testing.T) {
			plan, selected, other := agyTestPlan(t, "project", true)
			before := agyTestBytes(t, selected)
			info, err := os.Stat(selected)
			if err != nil {
				t.Fatal(err)
			}
			switch problem {
			case "identical":
				agyTestWrite(t, other, agyPreparedTest, 0600)
			case "other-group":
				agyTestWrite(t, other, `{"renamed":{"Stop":[{"type":"command","command":"python /old/coordination.py hook"}]}}`, 0600)
			case "malformed":
				agyTestWrite(t, other, `{`, 0600)
			case "duplicate":
				agyTestWrite(t, other, `{"x":1,"x":2}`, 0600)
			case "nested-duplicate":
				agyTestWrite(t, other, `{"x":[{"command":"echo one","command":"/cairn"}]}`, 0600)
			case "null-command":
				agyTestWrite(t, other, `{"other":{"Stop":[{"type":"command","command":null}]}}`, 0600)
			case "unicode":
				agyTestWrite(t, other, "{\"x\":\"\xff\"}", 0600)
			case "unreadable":
				if err := os.Chmod(other, 0000); err != nil {
					t.Fatal(err)
				}
			case "symlink-file":
				if err := os.Remove(other); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(selected, other); err != nil {
					t.Fatal(err)
				}
			case "hardlink":
				if err := os.Remove(other); err != nil {
					t.Fatal(err)
				}
				if err := os.Link(selected, other); err != nil {
					t.Fatal(err)
				}
			case "symlink-directory":
				dir := filepath.Dir(other)
				if err := os.Rename(dir, dir+"-original"); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(dir+"-original", dir); err != nil {
					t.Fatal(err)
				}
			case "prepared-pin":
				plan.Settings.SHA256 = strings.Repeat("0", 64)
			case "root-symlink":
				alias := plan.AccountHome + "-alias"
				if err := os.Symlink(plan.AccountHome, alias); err != nil {
					t.Fatal(err)
				}
				plan.AccountHome = alias
			}
			result, err := PublishAgyLayer(context.Background(), plan)
			if err == nil || result != (AgyLayerSelection{}) {
				t.Fatalf("invalid paired layer published: %+v %v", result, err)
			}
			after, err := os.Stat(selected)
			if err != nil || !os.SameFile(info, after) || after.Mode() != info.Mode() || !bytes.Equal(before, agyTestBytes(t, selected)) {
				t.Fatal("refusal rewrote selected layer", err)
			}
		})
	}
}

func TestAgyLayerForeignSelectedOwnerRetainsOldRecoveryCopy(t *testing.T) {
	plan, selected, other := agyTestPlan(t, "project", true)
	otherBefore := agyTestBytes(t, other)
	const foreign = `{"foreign":"leave this other writer untouched"}`
	var foreignInfo os.FileInfo
	result, publishErr := publishAgyLayer(context.Background(), plan, func(point string) error {
		if point == "after:publication" {
			path := filepath.Join(filepath.Dir(selected), ".foreign-write")
			agyTestWrite(t, path, foreign, 0600)
			var err error
			foreignInfo, err = os.Stat(path)
			if err != nil {
				return err
			}
			return os.Rename(path, selected)
		}
		return nil
	})
	if publishErr == nil || result != (AgyLayerSelection{}) || !strings.Contains(publishErr.Error(), "rollback ownership changed") {
		t.Fatal("foreign ownership did not retain explicit recovery failure", result, publishErr)
	}
	current, statErr := os.Stat(selected)
	if statErr != nil || !os.SameFile(current, foreignInfo) || !bytes.Equal([]byte(foreign), agyTestBytes(t, selected)) {
		t.Fatal("rollback overwrote the foreign selected inode", statErr)
	}
	entries, readErr := os.ReadDir(filepath.Dir(selected))
	if readErr != nil {
		t.Fatal(readErr)
	}
	retained := 0
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), ".cairn-agy-") {
			path := filepath.Join(filepath.Dir(selected), entry.Name())
			info, statErr := os.Stat(path)
			if statErr != nil || info.Mode().Perm() != 0640 || !bytes.Equal([]byte(agyOldTest), agyTestBytes(t, path)) || !strings.Contains(publishErr.Error(), path) {
				t.Fatal("old complete recovery settings or exact reconciliation path lost", statErr, publishErr)
			}
			retained++
		}
	}
	if retained != 1 || !bytes.Equal(otherBefore, agyTestBytes(t, other)) {
		t.Fatal("recovery copy count or other layer changed", retained)
	}
}

func TestAgyLayerFinalOtherReadInvalidatesBeforePublication(t *testing.T) {
	for _, boundary := range []string{"before:publication", "after:publication"} {
		t.Run(boundary, func(t *testing.T) {
			plan, selected, other := agyTestPlan(t, "account", true)
			before := agyTestBytes(t, selected)
			_, err := publishAgyLayer(context.Background(), plan, func(point string) error {
				if point == boundary {
					agyTestWrite(t, other, agyPreparedTest, 0600)
				}
				return nil
			})
			if err == nil || !strings.Contains(err.Error(), "observation changed") || !bytes.Equal(before, agyTestBytes(t, selected)) || !bytes.Equal([]byte(agyPreparedTest), agyTestBytes(t, other)) {
				t.Fatal("changed other-layer observation did not refuse before write", err)
			}
		})
	}
}

func TestAgyLayerPreservesCairnLookingUnselectedHookAndAllowsExplicitReplacement(t *testing.T) {
	const unrelated = `{ "command" : "/usr/bin/printf \"cairn docs\"", "type" : "command", "timeout":7 }`
	const preserved = `{"cairn-coordination":{"PreInvocation":[{"type":"command","timeout":7,"command":"/usr/bin/printf \"cairn docs\""},{"type":"command","command":"/new/cairn hook"}]}}`
	for _, problem := range []string{"preserved-plus-new", "dropped-unselected", "explicit-old-command", "missing-duplicate"} {
		t.Run(problem, func(t *testing.T) {
			plan, selected, _ := agyTestPlan(t, "project", true)
			old := `{"opaque":900719925474099312345,"cairn-coordination":{"PreInvocation":[` + unrelated + `]}}`
			prepared := preserved
			success := problem == "preserved-plus-new" || problem == "explicit-old-command"
			switch problem {
			case "dropped-unselected":
				prepared = agyPreparedTest
			case "explicit-old-command":
				old = `{"opaque":900719925474099312345,"cairn-coordination":{"PreInvocation":[{"type":"command","command":"/old/coordination.py hook"}]}}`
				prepared = agyPreparedTest
				plan.ReplaceCommands = []string{"/old/coordination.py hook"}
			case "missing-duplicate":
				old = `{"opaque":900719925474099312345,"cairn-coordination":{"PreInvocation":[` + unrelated + `,` + unrelated + `]}}`
			}
			agyTestWrite(t, selected, old, 0640)
			agyTestWrite(t, plan.Settings.Path, prepared, 0400)
			plan.Settings.SHA256 = agyDigest([]byte(prepared))
			result, err := PublishAgyLayer(context.Background(), plan)
			if success {
				if err != nil || !result.Changed {
					t.Fatal("preserved hook plus new/explicit hook refused", result, err)
				}
				actual := agyTestBytes(t, selected)
				if !bytes.Contains(actual, []byte(`"opaque":900719925474099312345`)) || problem == "preserved-plus-new" && !bytes.Contains(actual, []byte(unrelated)) {
					t.Fatal("unrelated hook literal or big integer changed")
				}
				again, err := PublishAgyLayer(context.Background(), plan)
				if err != nil || again.Changed || !bytes.Equal(actual, agyTestBytes(t, selected)) {
					t.Fatal("prepared group idempotence failed", again, err)
				}
			} else if err == nil || result != (AgyLayerSelection{}) || !bytes.Equal([]byte(old), agyTestBytes(t, selected)) {
				t.Fatal("unselected hook was removed or rewritten", result, err)
			}
		})
	}
}

func TestAgyLayerEveryBoundaryRestoresCompleteOldOrAbsence(t *testing.T) {
	for _, existing := range []bool{true, false} {
		for _, point := range []string{"settings-sync", "publication", "readback", "directory-sync"} {
			for _, side := range []string{"before:", "after:"} {
				t.Run(map[bool]string{true: "old/", false: "absent/"}[existing]+side+point, func(t *testing.T) {
					plan, selected, other := agyTestPlan(t, "project", existing)
					otherBefore := agyTestBytes(t, other)
					sentinel := errors.New("injected Agy publication interruption")
					result, err := publishAgyLayer(context.Background(), plan, func(at string) error {
						if at == side+point {
							return sentinel
						}
						return nil
					})
					if !errors.Is(err, sentinel) || result != (AgyLayerSelection{}) {
						t.Fatal("interruption did not refuse", result, err)
					}
					if existing {
						info, err := os.Stat(selected)
						if err != nil || info.Mode().Perm() != 0640 || !bytes.Equal([]byte(agyOldTest), agyTestBytes(t, selected)) {
							t.Fatal("interruption lost complete old settings/mode", err)
						}
					} else if _, err := os.Lstat(selected); !errors.Is(err, os.ErrNotExist) {
						t.Fatal("interruption published previously absent hooks", err)
					}
					if !bytes.Equal(otherBefore, agyTestBytes(t, other)) {
						t.Fatal("interruption touched other layer")
					}
					entries, err := os.ReadDir(filepath.Dir(selected))
					if err != nil {
						t.Fatal(err)
					}
					for _, entry := range entries {
						if strings.HasPrefix(entry.Name(), ".cairn-agy-") {
							t.Fatal("owned temporary/recovery member left after successful rollback")
						}
					}
				})
			}
		}
	}
}
