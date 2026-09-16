package wakeup

import (
	"encoding/json"
	"os"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestProviderObservationSurvivesRestartAndClearsAfterRecovery(t *testing.T) {
	c := Config{StateDirectory: t.TempDir(), Worker: &core.WorkerSpec{Harness: "codex"}}
	id := uuid.NewString()
	failure := &core.ProviderFailure{Harness: "codex", Source: "native-diagnostic", Kind: "quota", Code: "codex_quota_exceeded"}
	if err := writeProviderObservation(c, id, failure); err != nil {
		t.Fatal(err)
	}
	got, err := readProviderObservation(c, id)
	if err != nil || got == nil || *got != *failure {
		t.Fatalf("retained: %+v %v", got, err)
	}
	info, err := os.Stat(providerObservationPath(c, id))
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("permissions: %v %v", info, err)
	}
	if err = writeProviderObservation(c, id, nil); err != nil {
		t.Fatal(err)
	}
	got, err = readProviderObservation(c, id)
	if err != nil || got != nil {
		t.Fatalf("recovered: %+v %v", got, err)
	}
}

func TestProviderObservationRejectsWrongAttemptHarnessAndCorruptFile(t *testing.T) {
	c := Config{StateDirectory: t.TempDir(), Worker: &core.WorkerSpec{Harness: "codex"}}
	id := uuid.NewString()
	if got, err := readProviderObservation(c, id); got != nil || err != nil {
		t.Fatalf("absent file: %v %v", got, err)
	}
	for _, obs := range []providerObservation{
		{Schema: "cairn.provider-observation/1", AttemptID: uuid.NewString(), Harness: "codex"},
		{Schema: "cairn.provider-observation/1", AttemptID: id, Harness: "claude"},
		{Schema: "cairn.provider-observation/1", AttemptID: id, Harness: "codex", Failure: &core.ProviderFailure{Harness: "codex", Source: "tool-output", Kind: "quota", Code: "quota"}},
	} {
		raw, err := json.Marshal(obs)
		if err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(providerObservationPath(c, id), raw, 0600); err != nil {
			t.Fatal(err)
		}
		if _, err = readProviderObservation(c, id); err == nil {
			t.Fatalf("accepted %+v", obs)
		}
	}
	for _, raw := range []string{"{", string(make([]byte, 4097))} {
		if err := os.WriteFile(providerObservationPath(c, id), []byte(raw), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := readProviderObservation(c, id); err == nil {
			t.Fatal("accepted invalid file")
		}
	}
}
