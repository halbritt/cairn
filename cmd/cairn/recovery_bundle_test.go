package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func bundleFixture(t *testing.T) ([]core.RecoveryRecord, core.RecoveryExport) {
	t.Helper()
	summary := core.RecoveryExport{RootGrantID: uuid.NewString(), SetID: uuid.NewString(), Count: 2}
	records := make([]core.RecoveryRecord, 2)
	for i := range records {
		r := core.RecoveryRecord{Schema: "cairn.recovery-record/1", RootGrantID: summary.RootGrantID, CapturedAt: time.Now().UTC(), Audit: []core.AuditMember{}, Withdrawals: []core.RecoveryWithdrawal{}, Contexts: []core.RecoveryContext{}, Segment: &core.RecoverySegment{SetID: summary.SetID, Index: i + 1, Count: 2}}
		body, err := json.Marshal(r)
		if err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(body)
		r.SHA256 = hex.EncodeToString(sum[:])
		records[i] = r
	}
	return records, summary
}
func bundleProducer(records []core.RecoveryRecord, summary core.RecoveryExport) func(func(core.RecoveryRecord) error) (core.RecoveryExport, error) {
	return func(emit func(core.RecoveryRecord) error) (core.RecoveryExport, error) {
		for _, r := range records {
			if err := emit(r); err != nil {
				return summary, err
			}
		}
		return summary, nil
	}
}
func TestRecoveryDirectoryInterruptedExportRetryAndCollision(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "complete")
	records, summary := bundleFixture(t)
	interrupted := errors.New("fixture interrupted stream")
	_, err := writeRecoveryDirectory(path, func(emit func(core.RecoveryRecord) error) (core.RecoveryExport, error) {
		if err := emit(records[0]); err != nil {
			return summary, err
		}
		return summary, interrupted
	})
	if !errors.Is(err, interrupted) {
		t.Fatalf("lost callback failure: %v", err)
	}
	if _, err = os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("partial directory published: %v", err)
	}
	entries, err := os.ReadDir(root)
	if err != nil || len(entries) != 0 {
		t.Fatalf("ordinary failure left staging: %v %v", entries, err)
	}
	result, err := writeRecoveryDirectory(path, bundleProducer(records, summary))
	if err != nil {
		t.Fatal(err)
	}
	if result.Count != 2 || result.ManifestSHA256 == "" {
		t.Fatalf("wrong summary: %+v", result)
	}
	before, err := os.ReadFile(filepath.Join(path, recoveryManifestName))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = writeRecoveryDirectory(path, bundleProducer(records, summary)); err == nil {
		t.Fatal("existing complete export overwritten")
	}
	after, err := os.ReadFile(filepath.Join(path, recoveryManifestName))
	if err != nil || !bytes.Equal(before, after) {
		t.Fatal("collision changed prior export")
	}
	reader, err := openRecoveryBundle(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	for i := 0; i < 2; i++ {
		got, ok, err := reader.Next()
		if err != nil || !ok || got.SHA256 != records[i].SHA256 {
			t.Fatalf("part %d: %+v %t %v", i, got, ok, err)
		}
	}
	if _, ok, err := reader.Next(); err != nil || ok || reader.manifestSHA256 != result.ManifestSHA256 {
		t.Fatalf("manifest completion: %t %v", ok, err)
	}
	empty := filepath.Join(root, "existing-empty")
	if err = os.Mkdir(empty, 0700); err != nil {
		t.Fatal(err)
	}
	oldInfo, _ := os.Stat(empty)
	if _, err = writeRecoveryDirectory(empty, bundleProducer(records, summary)); err == nil {
		t.Fatal("existing empty directory replaced")
	}
	newInfo, _ := os.Stat(empty)
	if !os.SameFile(oldInfo, newInfo) {
		t.Fatal("empty collision changed destination identity")
	}
}

func TestRecoveryDirectoryRefusesDamagedPathsPartsAndManifest(t *testing.T) {
	for _, damage := range []string{"missing", "bytes", "symlink", "path", "trailer", "oversized-line", "omitted-part", "crlf", "unterminated"} {
		t.Run(damage, func(t *testing.T) {
			root := t.TempDir()
			path := filepath.Join(root, "bundle")
			records, summary := bundleFixture(t)
			if _, err := writeRecoveryDirectory(path, bundleProducer(records, summary)); err != nil {
				t.Fatal(err)
			}
			part := filepath.Join(path, recoveryPartName(2))
			manifest := filepath.Join(path, recoveryManifestName)
			switch damage {
			case "missing":
				if err := os.Remove(part); err != nil {
					t.Fatal(err)
				}
			case "bytes":
				f, err := os.OpenFile(part, os.O_APPEND|os.O_WRONLY, 0)
				if err != nil {
					t.Fatal(err)
				}
				_, err = f.WriteString(" ")
				if err != nil {
					t.Fatal(err)
				}
				f.Close()
			case "symlink":
				if err := os.Remove(part); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(recoveryPartName(1), part); err != nil {
					t.Fatal(err)
				}
			default:
				body, err := os.ReadFile(manifest)
				if err != nil {
					t.Fatal(err)
				}
				switch damage {
				case "path":
					body = bytes.Replace(body, []byte(recoveryPartName(2)), []byte("../outside.json"), 1)
				case "trailer":
					body = body[:bytes.LastIndex(bytes.TrimSuffix(body, []byte{'\n'}), []byte{'\n'})+1]
				case "crlf":
					body = bytes.ReplaceAll(body, []byte{'\n'}, []byte{'\r', '\n'})
				case "unterminated":
					body = bytes.TrimSuffix(body, []byte{'\n'})
				case "oversized-line":
					body = []byte(strings.Repeat("x", 5000) + "\n")
				case "omitted-part":
					lines := bytes.Split(body, []byte{'\n'})
					body = bytes.Join(append(lines[:2], lines[3:]...), []byte{'\n'})
				}
				if err = os.WriteFile(manifest, body, 0600); err != nil {
					t.Fatal(err)
				}
			}
			reader, err := openRecoveryBundle(path)
			if err != nil {
				return
			}
			defer reader.Close()
			for {
				_, ok, err := reader.Next()
				if err != nil {
					return
				}
				if !ok {
					t.Fatal("damaged bundle accepted")
					return
				}
			}
		})
	}
}

func TestRecoveryDirectoryCrashLeavesOnlyUnpublishedStaging(t *testing.T) {
	if path := os.Getenv("CAIRN_BUNDLE_CRASH_FIXTURE"); path != "" {
		records, summary := bundleFixture(t)
		_, _ = writeRecoveryDirectory(path, func(emit func(core.RecoveryRecord) error) (core.RecoveryExport, error) {
			if err := emit(records[0]); err != nil {
				t.Fatal(err)
			}
			os.Exit(81)
			return summary, nil
		})
		t.Fatal("crash fixture returned")
	}
	root := t.TempDir()
	path := filepath.Join(root, "published")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestRecoveryDirectoryCrashLeavesOnlyUnpublishedStaging$")
	cmd.Env = append(os.Environ(), "CAIRN_BUNDLE_CRASH_FIXTURE="+path)
	out, err := cmd.CombinedOutput()
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != 81 {
		t.Fatalf("crash fixture: %s %v", out, err)
	}
	if _, err = os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("crash published partial target: %v", err)
	}
	entries, err := os.ReadDir(root)
	if err != nil || len(entries) != 1 || !strings.HasPrefix(entries[0].Name(), ".cairn-recovery-staging-") {
		t.Fatalf("unrecognized crash residue: %v %v", entries, err)
	}
	records, summary := bundleFixture(t)
	if _, err = writeRecoveryDirectory(path, bundleProducer(records, summary)); err != nil {
		t.Fatalf("new export after crash failed: %v", err)
	}
	// Recovery of a new export must not delete an earlier abandoned directory.
	if _, err = os.Stat(filepath.Join(root, entries[0].Name())); err != nil {
		t.Fatalf("retry removed prior staging: %v", err)
	}
}
