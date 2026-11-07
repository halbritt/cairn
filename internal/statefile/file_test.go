package statefile

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestCheckpointRetainsPublishedStateOnCallbackError(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	f := File[int]{Path: path, Default: func() int { return 1 }}
	callbackErr := errors.New("callback failed after checkpoint")
	err := f.Update(func(state int, save func(int) error) (int, bool, error) {
		if state != 1 {
			t.Fatalf("default = %d", state)
		}
		if err := save(2); err != nil {
			t.Fatal(err)
		}
		return 3, false, callbackErr
	})
	if !errors.Is(err, callbackErr) {
		t.Fatalf("callback identity lost: %v", err)
	}
	raw, err := os.ReadFile(path)
	if err != nil || string(raw) != "2" {
		t.Fatalf("checkpoint = %q, %v", raw, err)
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("published mode: %v, %v", info, err)
	}
}

func TestIOSelectionIsPerFileAndPreservesJoinedErrors(t *testing.T) {
	dir := t.TempDir()
	writeErr := errors.New("selected write failure")
	callbackErr := errors.New("selected callback failure")
	broken := File[int]{Path: filepath.Join(dir, "broken.json")}.WithIO(IO{
		Write: func(*os.File, []byte) (int, error) { return 0, writeErr },
	})
	err := broken.Update(func(int, func(int) error) (int, bool, error) {
		return 7, true, callbackErr
	})
	if !errors.Is(err, writeErr) || !errors.Is(err, callbackErr) {
		t.Fatalf("joined errors lost: %v", err)
	}
	if _, err := os.Stat(broken.Path); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("failed write published: %v", err)
	}
	ordinary := File[int]{Path: filepath.Join(dir, "ordinary.json")}
	if err := ordinary.Update(func(int, func(int) error) (int, bool, error) {
		return 9, true, nil
	}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(ordinary.Path)
	var got int
	if err != nil || json.Unmarshal(raw, &got) != nil || got != 9 {
		t.Fatalf("another instance inherited fault: %q, %v", raw, err)
	}
}
