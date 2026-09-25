package main

import (
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"testing"
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
