package main

import (
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
