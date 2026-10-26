package main

import (
	"errors"
	"os"
	"path/filepath"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
)

type testCounterState struct {
	Schema string `json:"schema"`
	Count  int    `json:"count"`
}

func TestLockedStateFileAtomicUpdateAndLockContention(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "nested", "state")
	path := filepath.Join(dir, "counter.json")
	store := LockedStateFile[testCounterState]{
		Path:        path,
		TempPattern: ".cairn-test-*",
		MaxBytes:    4096,
		CreateDir:   true,
		Default: func() testCounterState {
			return testCounterState{Schema: "cairn.test/1"}
		},
		Validate: func(s testCounterState) error {
			if s.Schema != "cairn.test/1" {
				return invalid("schema mismatch")
			}
			return nil
		},
	}

	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			err := store.Update(func(s testCounterState, _ func(testCounterState) error) (testCounterState, bool, error) {
				s.Count++
				return s, true, nil
			})
			if err != nil {
				t.Errorf("concurrent update failed: %v", err)
			}
		}()
	}
	wg.Wait()

	var final testCounterState
	err := store.Update(func(s testCounterState, _ func(testCounterState) error) (testCounterState, bool, error) {
		final = s
		return s, false, nil
	})
	if err != nil || final.Count != 8 {
		t.Fatalf("expected count 8 after concurrent updates, got %+v (err=%v)", final, err)
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("expected 0600 state file permissions, got %v (err=%v)", info, err)
	}
}

func TestLockedStateFileNonBlockingContentionAndCheckpointSave(t *testing.T) {
	dir := t.TempDir()
	if err := os.Chmod(dir, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "journal.json")
	store := LockedStateFile[testCounterState]{
		Path:               path,
		TempPattern:        ".cairn-nb-*",
		MaxBytes:           1024,
		RequirePrivateDir:  true,
		RequirePrivateFile: true,
		StrictJSON:         true,
		NonBlocking:        true,
		Default: func() testCounterState {
			return testCounterState{Schema: "cairn.test/1"}
		},
	}

	// Checkpoint save persists intermediate state even if the callback later returns an error.
	boom := errors.New("network interrupted")
	err := store.Update(func(s testCounterState, save func(testCounterState) error) (testCounterState, bool, error) {
		s.Count = 42
		if saveErr := save(s); saveErr != nil {
			return s, false, saveErr
		}
		return s, false, boom
	})
	if !errors.Is(err, boom) {
		t.Fatalf("expected callback error, got %v", err)
	}
	var loaded testCounterState
	if err := store.Update(func(s testCounterState, _ func(testCounterState) error) (testCounterState, bool, error) {
		loaded = s
		return s, false, nil
	}); err != nil || loaded.Count != 42 {
		t.Fatalf("checkpoint state lost: %+v (err=%v)", loaded, err)
	}

	// Hold an external flock on .lock and verify NonBlocking refuses immediately.
	lockFile, err := os.OpenFile(path+".lock", os.O_RDWR, 0600)
	if err != nil {
		t.Fatal(err)
	}
	defer lockFile.Close()
	if err := syscall.Flock(int(lockFile.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	defer syscall.Flock(int(lockFile.Fd()), syscall.LOCK_UN)

	err = store.Update(func(s testCounterState, _ func(testCounterState) error) (testCounterState, bool, error) {
		return s, false, nil
	})
	if core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("expected INVALID_REQUEST on contended non-blocking lock, got %v", err)
	}
}

func TestCoreDirectReviewDeliveryAttentionAndStarvation(t *testing.T) {
	now := time.Date(2026, 10, 26, 12, 0, 0, 0, time.UTC)
	waited900 := int64(900)
	waited3600 := int64(3600)
	zeroTools := 0

	rows := []core.ReviewDelivery{
		{
			DeliveryID:  "del-waiting-idle",
			Consumer:    "agent/idle-1",
			State:       "pending",
			AvailableAt: now.Add(-15 * time.Minute),
			Event:       core.AgentEvent{EventID: "evt-1", From: "agent/pub", Kind: "request"},
			Diagnosis: &core.DeliveryDiagnosis{
				Stage:          "waiting",
				WaitingSeconds: &waited900,
				Recipient:      core.DiagnosisRecipient{Kind: "session", AgentID: "idle-1", ExecutionID: "exec-1", State: "idle", Presence: "online"},
				Host:           &core.DiagnosisHost{Condition: "wake_retained", Applies: "exact", Freshness: "current"},
			},
		},
		{
			DeliveryID:  "del-stopped-notice",
			Consumer:    "agent/stopped-2",
			State:       "pending",
			Execution:   "never_launched",
			AvailableAt: now.Add(-2 * time.Minute),
			Event:       core.AgentEvent{EventID: "evt-2", From: "agent/pub", Kind: "notice", Ref: core.RecordVersionRef{RecordID: "rec-2", Version: 1}},
			Diagnosis: &core.DeliveryDiagnosis{
				Stage:     "waiting",
				Recipient: core.DiagnosisRecipient{Kind: "session", AgentID: "stopped-2", ExecutionID: "exec-2", State: "idle", Presence: "stopped"},
				Host: &core.DiagnosisHost{
					Condition: "unknown",
					Reported:  "wake_retained",
					Applies:   "exact",
					Freshness: "offline",
					Wake:      &core.ObservedWake{Transport: "codex-queue", Status: "refused"},
				},
			},
		},
		{
			DeliveryID:   "del-held-stalled",
			Consumer:     "agent/busy-3",
			State:        "leased",
			Held:         true,
			AvailableAt:  now.Add(-2 * time.Hour),
			Event:        core.AgentEvent{EventID: "evt-3", From: "agent/pub", Kind: "request"},
			LatestNative: &core.ReviewAttempt{State: "running", CreatedAt: now.Add(-2 * time.Hour), OpenTools: &zeroTools},
			Diagnosis: &core.DeliveryDiagnosis{
				Stage:          "held",
				WaitingSeconds: &waited3600,
				Recipient:      core.DiagnosisRecipient{Kind: "session", AgentID: "busy-3", ExecutionID: "exec-3", State: "busy", Presence: "online"},
			},
		},
	}

	emptyCache := core.AttentionCache{Schema: "cairn.delivery-attention/1", Scope: "direct", Deliveries: map[string]core.AttentionEntry{}}
	att, nextCache, err := core.SummarizeAttention(rows, true, core.AttentionOptions{OlderThanSeconds: 300}, emptyCache, now)
	if err != nil {
		t.Fatalf("SummarizeAttention failed: %v", err)
	}
	if len(att.Groups) != 2 || len(att.Changed) != 2 || len(att.StoppedSessions) != 1 || len(nextCache.Deliveries) != 2 {
		t.Fatalf("unexpected direct attention summary: %+v", att)
	}
	if att.StoppedSessions[0].Recipient != "agent/stopped-2" || att.StoppedSessions[0].AdmissionUncertainty != "unknown" {
		t.Fatalf("unexpected stopped session backlog: %+v", att.StoppedSessions[0])
	}

	starved, err := core.DetectStarvation(rows, core.StarvationOptions{}, now, nil)
	if err != nil {
		t.Fatalf("DetectStarvation failed: %v", err)
	}
	if len(starved) != 2 {
		t.Fatalf("expected 2 starved sessions (hold_stalled and pending_starved), got %+v", starved)
	}
	if starved[0].Recipient != "agent/busy-3" || starved[0].Condition != "hold_stalled" || starved[1].Recipient != "agent/idle-1" || starved[1].Condition != "pending_starved" {
		t.Fatalf("unexpected starved sessions order or conditions: %+v", starved)
	}
}
