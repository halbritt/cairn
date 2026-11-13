package install

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"golang.org/x/sys/unix"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
)

func agyRecoveryTestPlan(t *testing.T, layer string, present bool) (AgyLayerPlan, string, string) {
	t.Helper()
	p, s, o := agyTestPlan(t, layer, present)
	p.RecoveryPath = filepath.Join(filepath.Dir(s), ".cairn-recovery-agy-0123456789abcdef0123456789abcdef.json")
	return p, s, o
}

func agyRecoveryTestPublish(t *testing.T, layer string, present bool) (AgyLayerRecovery, string, string) {
	t.Helper()
	p, s, o := agyRecoveryTestPlan(t, layer, present)
	selected, err := PublishAgyLayer(context.Background(), p)
	if err != nil || selected.Recovery == nil {
		t.Fatal(selected, err)
	}
	return AgyLayerRecovery{p.AccountHome, p.Workspace, p.Layer, *selected.Recovery}, s, o
}

func TestAgyRecoveryRestoresExactOldOrAbsenceAndRepeats(t *testing.T) {
	for _, layer := range []string{"account", "project"} {
		for _, present := range []bool{false, true} {
			t.Run(fmt.Sprint(layer, "/", present), func(t *testing.T) {
				p, s, o := agyRecoveryTestPublish(t, layer, present)
				other := agyTestBytes(t, o)
				journal := agyTestBytes(t, p.Record.Path)
				out, err := RecoverAgyLayer(context.Background(), p)
				if err != nil || !out.Changed {
					t.Fatal(out, err)
				}
				if present {
					info, err := os.Stat(s)
					if err != nil || info.Mode().Perm() != 0640 || string(agyTestBytes(t, s)) != agyOldTest {
						t.Fatal("old bytes/mode lost", info, err)
					}
				} else if _, err := os.Lstat(s); !errors.Is(err, os.ErrNotExist) {
					t.Fatal("absence not restored", err)
				}
				if string(agyTestBytes(t, o)) != string(other) || string(agyTestBytes(t, p.Record.Path)) != string(journal) {
					t.Fatal("other layer or recovery evidence changed")
				}
				before := agyRecoveryTestTree(t, filepath.Dir(p.AccountHome))
				out, err = RecoverAgyLayer(context.Background(), p)
				if err != nil || out.Changed || !reflect.DeepEqual(before, agyRecoveryTestTree(t, filepath.Dir(p.AccountHome))) {
					t.Fatal("repeat was not exact no-op", out, err)
				}
			})
		}
	}
}

func agyRecoveryTestTree(t *testing.T, root string) map[string]string {
	t.Helper()
	tree := map[string]string{}
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		raw := ""
		if info.Mode().IsRegular() && info.Mode().Perm()&0400 != 0 {
			b, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			raw = string(b)
		}
		if info.Mode()&os.ModeSymlink != 0 {
			raw, err = os.Readlink(path)
			if err != nil {
				return err
			}
		}
		stat := *info.Sys().(*syscall.Stat_t)
		stat.Atim = syscall.Timespec{}
		tree[path] = fmt.Sprintf("%v/%v/%v/%s", info.Mode(), stat, info.Size(), raw)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return tree
}

func TestAgyRecoveryRefusesUnknownMutatedStaleOrAliasedInputs(t *testing.T) {
	for _, problem := range []string{"pin", "missing-record", "record-mode", "record-link", "record-symlink", "forged-inode", "forged-root", "forged-field", "missing-backup", "backup-bytes", "backup-mode", "backup-inode", "backup-link", "selected-bytes", "selected-mode", "selected-inode", "selected-link", "other-bytes", "other-mode", "ancestor-mode", "record-selector", "wrong-layer"} {
		t.Run(problem, func(t *testing.T) {
			p, s, o := agyRecoveryTestPublish(t, "project", true)
			record, _, err := agyLoadRecovery(p)
			if err != nil {
				t.Fatal(err)
			}
			backup := filepath.Join(filepath.Dir(s), record.Temporary)
			write := func(path string, raw []byte, mode os.FileMode) {
				t.Helper()
				if err := os.Chmod(path, 0600); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, raw, mode); err != nil {
					t.Fatal(err)
				}
				if err := os.Chmod(path, mode); err != nil {
					t.Fatal(err)
				}
			}
			replace := func(path string) {
				t.Helper()
				raw := agyTestBytes(t, path)
				if err := os.Rename(path, path+".retained"); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, raw, 0640); err != nil {
					t.Fatal(err)
				}
			}
			switch problem {
			case "pin":
				p.Record.SHA256 = strings.Repeat("0", 64)
			case "missing-record":
				if err := os.Rename(p.Record.Path, p.Record.Path+".retained"); err != nil {
					t.Fatal(err)
				}
			case "record-mode":
				if err := os.Chmod(p.Record.Path, 0600); err != nil {
					t.Fatal(err)
				}
			case "record-link":
				if err := os.Link(p.Record.Path, p.Record.Path+".alias"); err != nil {
					t.Fatal(err)
				}
			case "record-symlink":
				original := p.Record.Path + ".retained"
				if err := os.Rename(p.Record.Path, original); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(original, p.Record.Path); err != nil {
					t.Fatal(err)
				}
			case "forged-inode", "forged-root":
				if problem == "forged-inode" {
					record.NewFile.Ino++
				} else {
					record.Other.Root = p.Workspace
				}
				raw, err := json.Marshal(record)
				if err != nil {
					t.Fatal(err)
				}
				write(p.Record.Path, raw, 0400)
				p.Record.SHA256 = agyDigest(raw)
			case "forged-field":
				raw := append([]byte(`{"unknown":true,`), agyTestBytes(t, p.Record.Path)[1:]...)
				write(p.Record.Path, raw, 0400)
				p.Record.SHA256 = agyDigest(raw)
			case "missing-backup":
				if err := os.Rename(backup, backup+".retained"); err != nil {
					t.Fatal(err)
				}
			case "backup-bytes":
				write(backup, []byte(`{"foreign":true}`), 0640)
			case "backup-mode":
				if err := os.Chmod(backup, 0600); err != nil {
					t.Fatal(err)
				}
			case "backup-inode":
				replace(backup)
			case "backup-link":
				if err := os.Link(backup, backup+".alias"); err != nil {
					t.Fatal(err)
				}
			case "selected-bytes":
				write(s, []byte(`{"foreign":true}`), 0640)
			case "selected-mode":
				if err := os.Chmod(s, 0600); err != nil {
					t.Fatal(err)
				}
			case "selected-inode":
				replace(s)
			case "selected-link":
				if err := os.Link(s, s+".alias"); err != nil {
					t.Fatal(err)
				}
			case "other-bytes":
				write(o, []byte(`{"foreign":true}`), 0600)
			case "other-mode":
				if err := os.Chmod(o, 0640); err != nil {
					t.Fatal(err)
				}
			case "ancestor-mode":
				if err := os.Chmod(filepath.Dir(s), 0700); err != nil {
					t.Fatal(err)
				}
			case "record-selector":
				p.Record.Path = filepath.Join(filepath.Dir(p.Record.Path), "unknown.json")
			case "wrong-layer":
				p.Layer = "account"
			}
			before := agyRecoveryTestTree(t, filepath.Dir(p.AccountHome))
			out, err := RecoverAgyLayer(context.Background(), p)
			if err == nil || out != (AgyLayerSelection{}) {
				t.Fatal("unsafe recovery accepted", out, err)
			}
			if !reflect.DeepEqual(before, agyRecoveryTestTree(t, filepath.Dir(p.AccountHome))) {
				t.Fatal("refusal changed files or recovery evidence")
			}
		})
	}
}

func TestAgyRecoveryOldRecordDoesNotOwnLaterPublication(t *testing.T) {
	p, s, _ := agyRecoveryTestPublish(t, "project", true)
	if _, err := RecoverAgyLayer(context.Background(), p); err != nil {
		t.Fatal(err)
	}
	plan := AgyLayerPlan{AccountHome: p.AccountHome, Workspace: p.Workspace, Layer: p.Layer, Settings: AgyLayerSettings{filepath.Join(filepath.Dir(p.AccountHome), "prepared.json"), agyDigest([]byte(agyPreparedTest))}}
	if _, err := PublishAgyLayer(context.Background(), plan); err != nil {
		t.Fatal(err)
	}
	before := agyTestBytes(t, s)
	if _, err := RecoverAgyLayer(context.Background(), p); err == nil {
		t.Fatal("stale record accepted")
	}
	if string(before) != string(agyTestBytes(t, s)) {
		t.Fatal("stale record changed later publication")
	}
}

// The process fixture builds this test binary and kills only its owned child.
// The production CLI has no observer flag or injected-crash branch.
func TestAgyRecoveryOwnedChild(t *testing.T) {
	path := os.Getenv("CAIRN_AGY_RECOVERY_CHILD")
	if path == "" {
		return
	}
	var request struct {
		Plan     AgyLayerPlan
		Boundary string
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &request); err != nil {
		t.Fatal(err)
	}
	_, err = publishAgyLayer(context.Background(), request.Plan, func(at string) error {
		if at == request.Boundary {
			fmt.Println("AGY-BOUNDARY:" + at)
			_, err := bufio.NewReader(os.Stdin).ReadString('\n')
			return err
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Fatal("owned crash child completed without its required kill")
}

func TestAgyRecoveryPublisherRechecksDurableMembersBeforeRename(t *testing.T) {
	for _, member := range []string{"journal", "staged", "foreign-staged"} {
		t.Run(member, func(t *testing.T) {
			plan, selected, other := agyRecoveryTestPlan(t, "project", true)
			otherBefore := agyTestBytes(t, other)
			var tampered string
			out, err := publishAgyLayer(context.Background(), plan, func(at string) error {
				if at != "before:publication" {
					return nil
				}
				tampered = plan.RecoveryPath
				if member != "journal" {
					tampered = filepath.Join(filepath.Dir(selected), ".cairn-agy-0123456789abcdef0123456789abcdef")
				}
				if member == "foreign-staged" {
					foreign := tampered + ".foreign"
					agyTestWrite(t, foreign, `{"foreign":true}`, 0640)
					return os.Rename(foreign, tampered)
				}
				agyTestWrite(t, tampered, `{"modified":true}`, 0400)
				return nil
			})
			if err == nil || out != (AgyLayerSelection{}) {
				t.Fatal("mutated durable member published", out, err)
			}
			if string(agyTestBytes(t, selected)) != agyOldTest || string(agyTestBytes(t, other)) != string(otherBefore) {
				t.Fatal("member refusal changed a layer")
			}
			if string(agyTestBytes(t, tampered)) != `{"modified":true}` && member != "foreign-staged" {
				t.Fatal("mutated evidence deleted or repaired")
			}
			if _, err := os.Stat(plan.RecoveryPath); err != nil {
				t.Fatal("recovery record deleted", err)
			}
		})
	}
}

func TestAgyRecoveryAbsenceMemberAndCancelledContextRefuseUnchanged(t *testing.T) {
	for _, problem := range []string{"foreign-absence-member", "cancelled", "restored-member-mutated"} {
		t.Run(problem, func(t *testing.T) {
			p, s, _ := agyRecoveryTestPublish(t, "project", false)
			r, _, err := agyLoadRecovery(p)
			if err != nil {
				t.Fatal(err)
			}
			if problem == "restored-member-mutated" {
				if _, err := RecoverAgyLayer(context.Background(), p); err != nil {
					t.Fatal(err)
				}
			}
			if problem != "cancelled" {
				agyTestWrite(t, filepath.Join(filepath.Dir(s), r.Temporary), `{"foreign":true}`, 0600)
			}
			ctx := context.Background()
			if problem == "cancelled" {
				var cancel context.CancelFunc
				ctx, cancel = context.WithCancel(ctx)
				cancel()
			}
			before := agyRecoveryTestTree(t, filepath.Dir(p.AccountHome))
			out, err := RecoverAgyLayer(ctx, p)
			if err == nil || out != (AgyLayerSelection{}) || !reflect.DeepEqual(before, agyRecoveryTestTree(t, filepath.Dir(p.AccountHome))) {
				t.Fatal("unsafe recovery changed settings/evidence", out, err)
			}
		})
	}
}

func TestAgyRecoveryCompensationKeepsForeignInodeOrLaterWriter(t *testing.T) {
	for _, present := range []bool{true, false} {
		for _, later := range []bool{false, true} {
			t.Run(fmt.Sprint(present, "/", later), func(t *testing.T) {
				p, s, _ := agyRecoveryTestPublish(t, "project", present)
				r, _, err := agyLoadRecovery(p)
				if err != nil {
					t.Fatal(err)
				}
				foreign := s + ".foreign"
				agyTestWrite(t, foreign, `{"foreign":"preserve exact inode"}`, 0640)
				if err := os.Rename(foreign, s); err != nil {
					t.Fatal(err)
				}
				parent, err := openDirectory(filepath.Dir(s), false)
				if err != nil {
					t.Fatal(err)
				}
				defer parent.Close()
				foreignRaw, foreignID, err := agyReadFile(parent, "hooks.json")
				if err != nil {
					t.Fatal(err)
				}
				if present {
					err = unix.Renameat2(int(parent.Fd()), r.Temporary, int(parent.Fd()), "hooks.json", unix.RENAME_EXCHANGE)
				} else {
					err = unix.Renameat2(int(parent.Fd()), "hooks.json", int(parent.Fd()), r.Temporary, unix.RENAME_NOREPLACE)
				}
				if err != nil {
					t.Fatal(err)
				}
				raw, id, err := agyReadFile(parent, r.Temporary)
				if err != nil {
					t.Fatal(err)
				}
				if later {
					agyTestWrite(t, foreign, `{"later":"leave untouched"}`, 0600)
					if err := os.Rename(foreign, s); err != nil {
						t.Fatal(err)
					}
				}
				before := agyRecoveryTestTree(t, filepath.Dir(p.AccountHome))
				err = agyUndoRecovery(parent, r, agyRecoveryDisplaced{id, agyDigest(raw)})
				if later {
					if err == nil || !reflect.DeepEqual(before, agyRecoveryTestTree(t, filepath.Dir(p.AccountHome))) {
						t.Fatal("compensation overwrote a later writer", err)
					}
				} else {
					if err != nil {
						t.Fatal(err)
					}
					actual, actualID, err := agyReadFile(parent, "hooks.json")
					if err != nil || !agyRecoveryFileEqual(actualID, foreignID) || string(actual) != string(foreignRaw) {
						t.Fatal("foreign inode/bytes not restored", err)
					}
				}
			})
		}
	}
}
