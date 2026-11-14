package install

import (
	"bufio"
	"bytes"
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
	"time"
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

func TestAgyRecoveryKeepsCommittedSelectionAndRetainedCorrespondence(t *testing.T) {
	for _, layer := range []string{"account", "project"} {
		for _, present := range []bool{false, true} {
			t.Run(fmt.Sprint(layer, "/", present), func(t *testing.T) {
				plan, selected, _ := agyRecoveryTestPublish(t, layer, present)
				before := agyRecoveryTestTree(t, filepath.Dir(plan.AccountHome))
				committed := agyTestBytes(t, selected)
				out, err := RecoverAgyLayer(context.Background(), plan)
				if err != nil || out.Changed || out.SHA256 != agyDigest(committed) {
					t.Fatal("committed recovery must finish forward without another activation", out, err)
				}
				if !reflect.DeepEqual(before, agyRecoveryTestTree(t, filepath.Dir(plan.AccountHome))) {
					t.Fatal("forward recovery changed committed settings or retained correspondence")
				}
			})
		}
	}
}

func TestAgyLayerOtherInProgressRecordRefusesBeforeCommit(t *testing.T) {
	for _, layer := range []string{"account", "project"} {
		t.Run(layer, func(t *testing.T) {
			plan, selected, other := agyRecoveryTestPlan(t, layer, true)
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			ready, release := make(chan struct{}), make(chan struct{})
			done := make(chan error, 1)
			go func() {
				_, err := publishAgyLayer(ctx, plan, func(point string) error {
					if point == "after:recovery-sync" {
						close(ready)
						select {
						case <-release:
						case <-ctx.Done():
							return ctx.Err()
						}
					}
					return nil
				})
				done <- err
			}()
			refused := false
			defer func() {
				close(release)
				if err := <-done; refused && err != nil {
					t.Error("first publisher must finish after peer withdrawal", err)
				}
			}()
			select {
			case <-ready:
			case <-ctx.Done():
				t.Fatal("publisher did not expose its durable precommit record")
			}
			peer := plan
			peer.Layer = map[string]string{"account": "project", "project": "account"}[layer]
			peer.RecoveryPath = filepath.Join(filepath.Dir(other), ".cairn-recovery-agy-fedcba9876543210fedcba9876543210.json")
			before := agyTestBytes(t, other)
			out, err := PublishAgyLayer(ctx, peer)
			if err == nil || out != (AgyLayerSelection{}) || !strings.Contains(err.Error(), "in-progress") {
				t.Fatal("opposite publisher crossed another durable precommit intent", out, err)
			}
			refused = true
			if !bytes.Equal(before, agyTestBytes(t, other)) || string(agyTestBytes(t, selected)) != agyOldTest {
				t.Fatal("record conflict changed either selected settings file")
			}
			if _, err := os.Lstat(peer.RecoveryPath); !errors.Is(err, os.ErrNotExist) {
				t.Fatal("refusing publisher did not withdraw its own record", err)
			}
		})
	}
}

func TestAgyLayerCommittedRecordStillRefusesOtherDispatch(t *testing.T) {
	for _, layer := range []string{"account", "project"} {
		t.Run(layer, func(t *testing.T) {
			plan, selected, _ := agyRecoveryTestPlan(t, layer, true)
			first, err := PublishAgyLayer(context.Background(), plan)
			if err != nil || !first.Changed || first.Recovery == nil {
				t.Fatal(first, err)
			}
			peer := plan
			peer.Layer = map[string]string{"account": "project", "project": "account"}[layer]
			peer.RecoveryPath = ""
			before := agyConcurrencyObserve(peer)
			out, err := PublishAgyLayer(context.Background(), peer)
			if err == nil || out != (AgyLayerSelection{}) || !strings.Contains(err.Error(), "nonselected layer conflict") || !reflect.DeepEqual(before, agyConcurrencyObserve(peer)) {
				t.Fatal("committed other dispatch was not preserved/refused", out, err)
			}
			recovered, err := RecoverAgyLayer(context.Background(), AgyLayerRecovery{plan.AccountHome, plan.Workspace, layer, *first.Recovery})
			if err != nil || recovered.Changed || recovered.SHA256 != agyDigest(agyTestBytes(t, selected)) {
				t.Fatal("committed record was not classified forward", recovered, err)
			}
		})
	}
}

func TestAgyLayerUnknownOtherRecordRefusesWithoutChangingFiles(t *testing.T) {
	for _, layer := range []string{"account", "project"} {
		for _, problem := range []string{"record-json", "record-mode", "record-inode", "missing-backup", "backup-inode", "ambiguous-new", "record-name"} {
			t.Run(layer+"/"+problem, func(t *testing.T) {
				plan, selected, other := agyRecoveryTestPlan(t, layer, true)
				original := plan.Settings
				published, err := PublishAgyLayer(context.Background(), plan)
				if err != nil {
					t.Fatal(err)
				}
				record, _, err := agyLoadRecovery(AgyLayerRecovery{plan.AccountHome, plan.Workspace, plan.Layer, *published.Recovery})
				if err != nil {
					t.Fatal(err)
				}
				backup := filepath.Join(filepath.Dir(selected), record.Temporary)
				// Put the actual generated inode pair in its precommit layout.
				// Genuine killed publishers exercise this layout in the process fixture.
				if err := unix.Renameat2(unix.AT_FDCWD, backup, unix.AT_FDCWD, selected, unix.RENAME_EXCHANGE); err != nil {
					t.Fatal(err)
				}
				stagedBytes := agyTestBytes(t, backup)
				switch problem {
				case "record-json":
					if err := os.Chmod(plan.RecoveryPath, 0600); err != nil {
						t.Fatal(err)
					}
					agyTestWrite(t, plan.RecoveryPath, `{"unknown":true}`, 0400)
				case "record-mode":
					if err := os.Chmod(plan.RecoveryPath, 0600); err != nil {
						t.Fatal(err)
					}
				case "record-inode":
					record.NewFile.Ino++
					if err := os.Chmod(plan.RecoveryPath, 0600); err != nil {
						t.Fatal(err)
					}
					raw, err := json.Marshal(record)
					if err != nil {
						t.Fatal(err)
					}
					agyTestWrite(t, plan.RecoveryPath, string(raw), 0400)
				case "missing-backup":
					if err := os.Rename(backup, backup+".retained"); err != nil {
						t.Fatal(err)
					}
				case "backup-inode":
					if err := os.Rename(backup, backup+".retained"); err != nil {
						t.Fatal(err)
					}
					agyTestWrite(t, backup, string(stagedBytes), 0640)
				case "ambiguous-new":
					record.NewSHA256 = record.Selected.SHA256
					if err := os.Chmod(plan.RecoveryPath, 0600); err != nil {
						t.Fatal(err)
					}
					raw, err := json.Marshal(record)
					if err != nil {
						t.Fatal(err)
					}
					agyTestWrite(t, plan.RecoveryPath, string(raw), 0400)
				case "record-name":
					if err := os.Rename(plan.RecoveryPath, filepath.Join(filepath.Dir(plan.RecoveryPath), ".cairn-recovery-agy-unknown.json")); err != nil {
						t.Fatal(err)
					}
				}
				peer := plan
				peer.Layer, peer.Settings, peer.RecoveryPath = map[string]string{"account": "project", "project": "account"}[layer], original, ""
				before := agyConcurrencyObserve(peer)
				peerRoot, peerRelative := peer.AccountHome, ".gemini/config/hooks.json"
				if peer.Layer == "project" {
					peerRoot, peerRelative = peer.Workspace, ".agents/hooks.json"
				}
				selectedBefore, err := inspectAgyLayer(peerRoot, peerRelative)
				if err != nil {
					t.Fatal(err)
				}
				out, err := PublishAgyLayer(context.Background(), peer)
				if err == nil || out != (AgyLayerSelection{}) || !reflect.DeepEqual(before, agyConcurrencyObserve(peer)) {
					t.Fatal("unknown record accepted or protected bytes/inodes changed", out, err, other)
				}
				if err := selectedBefore.recheck(); err != nil {
					t.Fatal(err)
				}
			})
		}
	}
}

func TestAgyRecoveryCompletesForwardAndRepeats(t *testing.T) {
	for _, layer := range []string{"account", "project"} {
		for _, present := range []bool{false, true} {
			t.Run(fmt.Sprint(layer, "/", present), func(t *testing.T) {
				p, s, o := agyRecoveryTestPublish(t, layer, present)
				other := agyTestBytes(t, o)
				journal := agyTestBytes(t, p.Record.Path)
				committed := agyTestBytes(t, s)
				out, err := RecoverAgyLayer(context.Background(), p)
				if err != nil || out.Changed || out.SHA256 != agyDigest(committed) {
					t.Fatal(out, err)
				}
				info, err := os.Stat(s)
				wantMode := os.FileMode(0600)
				if present {
					wantMode = 0640
				}
				if err != nil || info.Mode().Perm() != wantMode || !bytes.Equal(agyTestBytes(t, s), committed) {
					t.Fatal("committed bytes/mode lost", info, err)
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
				ancestor := filepath.Dir(s)
				original, err := os.Stat(ancestor)
				if err != nil || original.Mode().Perm() != 0750 {
					t.Fatal("fixture ancestor did not start at intended mode 0750", original, err)
				}
				if err := os.Chmod(ancestor, 0700); err != nil {
					t.Fatal(err)
				}
				changed, err := os.Stat(ancestor)
				if err != nil || changed.Mode().Perm() != 0700 || changed.Mode() == original.Mode() || !os.SameFile(original, changed) {
					t.Fatal("ancestor-mode mutation did not change the same fixture directory from 0750 to 0700", original, changed, err)
				}
				t.Log("fixture ancestor mode changed from 0750 to 0700 on the same inode")
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
	next := strings.Replace(agyPreparedTest, `"timeout":15`, `"timeout":16`, 1)
	path := filepath.Join(filepath.Dir(p.AccountHome), "next-prepared.json")
	agyTestWrite(t, path, next, 0400)
	plan := AgyLayerPlan{AccountHome: p.AccountHome, Workspace: p.Workspace, Layer: p.Layer, Settings: AgyLayerSettings{path, agyDigest([]byte(next))}, ReplaceCommands: []string{"/pinned/cairn hook --event PreInvocation"}}
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

func TestAgyRecoveryKeepsForeignInodeOrLaterWriter(t *testing.T) {
	for _, present := range []bool{true, false} {
		for _, later := range []bool{false, true} {
			t.Run(fmt.Sprint(present, "/", later), func(t *testing.T) {
				p, selected, _ := agyRecoveryTestPublish(t, "project", present)
				foreign := selected + ".foreign"
				agyTestWrite(t, foreign, `{"foreign":"preserve exact inode"}`, 0640)
				if err := os.Rename(foreign, selected); err != nil {
					t.Fatal(err)
				}
				if later {
					agyTestWrite(t, foreign, `{"later":"leave untouched"}`, 0600)
					if err := os.Rename(foreign, selected); err != nil {
						t.Fatal(err)
					}
				}
				before := agyRecoveryTestTree(t, filepath.Dir(p.AccountHome))
				out, err := RecoverAgyLayer(context.Background(), p)
				if err == nil || out != (AgyLayerSelection{}) || !reflect.DeepEqual(before, agyRecoveryTestTree(t, filepath.Dir(p.AccountHome))) {
					t.Fatal("recovery changed foreign inode or retained evidence", out, err)
				}
			})
		}
	}
}
