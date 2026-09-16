package wakeup

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

type providerObservation struct {
	Schema    string                `json:"schema"`
	AttemptID string                `json:"attempt_id"`
	Harness   string                `json:"harness"`
	Failure   *core.ProviderFailure `json:"failure"`
}

func providerObservationPath(c Config, id string) string {
	return filepath.Join(c.StateDirectory, id+".provider.json")
}

// Persist selected metadata before reporting to the API. Reconciliation reads
// this file after stopping the unit, including when the API report was lost.
func writeProviderObservation(c Config, id string, failure *core.ProviderFailure) error {
	if c.Worker == nil {
		return nil
	}
	if failure != nil {
		if err := failure.Validate(); err != nil {
			return err
		}
		if failure.Harness != c.Worker.Harness {
			return errors.New("provider observation harness mismatch")
		}
	}
	f, err := os.CreateTemp(c.StateDirectory, ".provider-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	err = json.NewEncoder(f).Encode(providerObservation{"cairn.provider-observation/1", id, c.Worker.Harness, failure})
	if err == nil {
		err = f.Sync()
	}
	if err = errors.Join(err, f.Close()); err != nil {
		return err
	}
	if err = os.Rename(f.Name(), providerObservationPath(c, id)); err != nil {
		return err
	}
	dir, err := os.Open(c.StateDirectory)
	if err != nil {
		return err
	}
	return errors.Join(dir.Sync(), dir.Close())
}

func readProviderObservation(c Config, id string) (*core.ProviderFailure, error) {
	if c.Worker == nil {
		return nil, nil
	}
	f, err := os.Open(providerObservationPath(c, id))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	} // Never launched, or an older worker.
	if err != nil {
		return nil, err
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, 4097))
	if err != nil {
		return nil, err
	}
	if len(raw) > 4096 {
		return nil, errors.New("provider observation exceeds 4096 bytes")
	}
	var obs providerObservation
	if err = json.Unmarshal(raw, &obs); err != nil {
		return nil, fmt.Errorf("read provider observation: %w", err)
	}
	if obs.Schema != "cairn.provider-observation/1" || obs.AttemptID != id || obs.Harness != c.Worker.Harness {
		return nil, errors.New("provider observation does not match wake attempt and configured harness")
	}
	if obs.Failure != nil {
		if err = obs.Failure.Validate(); err != nil {
			return nil, err
		}
		if obs.Failure.Harness != obs.Harness {
			return nil, errors.New("provider failure harness mismatch")
		}
	}
	return obs.Failure, nil
}

// An unreadable local observation must be visible, but must not strand a hold
// after the supervisor has confirmed process termination.
func changeWithProviderObservation(ctx context.Context, client *localapi.Client, c Config, id, op, state, reason string) (core.WakeAttempt, error) {
	failure, observationErr := readProviderObservation(c, id)
	if observationErr != nil {
		reason = "provider_observation_unreadable"
	}
	var out core.WakeAttempt
	err := client.Call(ctx, "wake-change", core.WakeChangeRequest{RequestID: uuid.NewString(), AttemptID: id,
		Operation: op, ProcessState: state, Reason: reason, ProviderFailure: failure}, &out)
	return out, errors.Join(observationErr, err)
}
