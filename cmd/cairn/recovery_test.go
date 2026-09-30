package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"github.com/halbritt/cairn/core"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestRecoveryFileRejectsUnsafeInputsAndNeverOverwrites(t *testing.T) {
	directory := t.TempDir()
	path := filepath.Join(directory, "recovery.json")
	body := []byte(`{"schema":"cairn.recovery-record/1"}`)
	if err := writeRecoveryFile(path, body); err != nil {
		t.Fatal(err)
	}
	if err := writeRecoveryFile(path, []byte("replacement")); err == nil {
		t.Fatal("overwrote recovery record")
	}
	if _, err := readRecoveryFile(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := readRecoveryFile(path); err == nil {
		t.Fatal("accepted public recovery record")
	}
	if err := os.Chmod(path, 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(directory, "link")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	if _, err := readRecoveryFile(link); err == nil {
		t.Fatal("followed symlink")
	}
	fifo := filepath.Join(directory, "fifo")
	if err := syscall.Mkfifo(fifo, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readRecoveryFile(fifo); err == nil {
		t.Fatal("accepted FIFO")
	}
	for _, invalid := range []string{`{} {}`, `{"unknown":true}`, `{"schema":`} {
		if err := os.WriteFile(path, []byte(invalid), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := readRecoveryFile(path); err == nil {
			t.Fatalf("accepted malformed JSON %s", invalid)
		}
	}
	if err := os.WriteFile(path, make([]byte, maxRecoveryBytes+1), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readRecoveryFile(path); err == nil {
		t.Fatal("accepted oversized recovery record")
	}
}

func TestRecoveryExportRemovesPartialFileBeforeExactPathRetry(t *testing.T) {
	path := filepath.Join(t.TempDir(), "recovery.json")
	content := []byte("complete recovery record")
	failure := errors.New("injected partial write")
	err := writeRecoveryFileWith(path, content, func(file *os.File, body []byte) (int, error) {
		written, err := file.Write(body[:5])
		return written, errors.Join(err, failure)
	})
	if !errors.Is(err, failure) {
		t.Fatalf("lost write failure: %v", err)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("partial export still occupies exact path: %v", err)
	}
	if err := writeRecoveryFile(path, content); err != nil {
		t.Fatalf("exact-path retry: %v", err)
	}
	got, err := os.ReadFile(path)
	if err != nil || string(got) != string(content) {
		t.Fatalf("retry bytes: %q %v", got, err)
	}
	if err := writeRecoveryFile(path, []byte("replacement")); !errors.Is(err, os.ErrExist) {
		t.Fatalf("successful export was replaceable: %v", err)
	}
	got, err = os.ReadFile(path)
	if err != nil || string(got) != string(content) {
		t.Fatalf("immutable export changed: %q %v", got, err)
	}
}

func TestRecoverySegmentPathsKeepSinglePathAndNumberSets(t *testing.T) {
	if got := recoverySegmentPaths("/private/after.json", 1); len(got) != 1 || got[0] != "/private/after.json" {
		t.Fatalf("single record renamed: %v", got)
	}
	got := recoverySegmentPaths("/private/after.json", 3)
	want := []string{"/private/after.part-0001-of-0003.json", "/private/after.part-0002-of-0003.json", "/private/after.part-0003-of-0003.json"}
	if len(got) != len(want) {
		t.Fatalf("segment paths: %v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("segment %d: %s want %s", i, got[i], want[i])
		}
	}
	if got := recoverySegmentPaths("/private/after", 2); got[0] != "/private/after.part-0001-of-0002" || got[1] != "/private/after.part-0002-of-0002" {
		t.Fatalf("extensionless paths: %v", got)
	}
}

func TestRecoverySegmentExportCollisionCleansOnlyNewFilesAndRetries(t *testing.T) {
	path := filepath.Join(t.TempDir(), "expectations.json")
	set := make([]core.RecoveryRecord, 2)
	for i := range set {
		r := core.RecoveryRecord{Schema: "cairn.recovery-record/1", RootGrantID: "11111111-1111-4111-8111-111111111111", CapturedAt: time.Now().UTC(), Audit: []core.AuditMember{}, Withdrawals: []core.RecoveryWithdrawal{}, Contexts: []core.RecoveryContext{}, Segment: &core.RecoverySegment{SetID: "22222222-2222-4222-8222-222222222222", Index: i + 1, Count: 2}}
		raw, err := json.Marshal(r)
		if err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256(raw)
		r.SHA256 = hex.EncodeToString(digest[:])
		set[i] = r
	}
	paths := recoverySegmentPaths(path, len(set))
	sentinel := []byte("an existing operator file")
	if err := writeRecoveryFile(paths[1], sentinel); err != nil {
		t.Fatal(err)
	}
	if _, err := writeRecoverySet(path, set); !errors.Is(err, os.ErrExist) {
		t.Fatalf("collision not refused: %v", err)
	}
	if _, err := os.Stat(paths[0]); !os.IsNotExist(err) {
		t.Fatalf("failed export left its first segment: %v", err)
	}
	got, err := os.ReadFile(paths[1])
	if err != nil || string(got) != string(sentinel) {
		t.Fatalf("existing file changed: %q %v", got, err)
	}
	if err := os.Remove(paths[1]); err != nil {
		t.Fatal(err)
	}
	result, err := writeRecoverySet(path, set)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.(exportedRecovery).Segments) != 2 {
		t.Fatalf("missing exported segment metadata: %+v", result)
	}
	records := make([]core.RecoveryRecord, 2)
	before := make([][]byte, 2)
	for i, p := range paths {
		records[i], err = readRecoveryFile(p)
		if err != nil {
			t.Fatal(err)
		}
		before[i], err = os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		if records[i].SHA256 != set[i].SHA256 {
			t.Fatalf("segment%d checksum changed", i)
		}
	}
	if err := core.CheckRecoverySegments(records); err != nil {
		t.Fatal(err)
	}
	if err := core.CheckRecoverySegments(records[:1]); err == nil {
		t.Fatal("partial exported set treated as complete")
	}
	if _, err := writeRecoverySet(path, set); !errors.Is(err, os.ErrExist) {
		t.Fatalf("existing complete set was replaceable: %v", err)
	}
	for i, p := range paths {
		got, err := os.ReadFile(p)
		if err != nil || string(got) != string(before[i]) {
			t.Fatalf("retry changed segment%d", i)
		}
	}
}
