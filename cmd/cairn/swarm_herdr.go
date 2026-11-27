package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/lifecycle/wake"
)

type swarmPane struct {
	PaneID        string `json:"pane_id"`
	TabID         string `json:"tab_id,omitempty"`
	WorkspaceID   string `json:"workspace_id,omitempty"`
	AgentStatus   string `json:"agent_status"`
	TerminalTitle string `json:"terminal_title,omitempty"`
	Focused       bool   `json:"focused,omitempty"`
	FocusCommand  string `json:"focus_command"`
}

type swarmPaneObservation struct {
	ObservedAt        time.Time            `json:"observed_at"`
	Status            string               `json:"status"`
	Error             string               `json:"error,omitempty"`
	Sessions          int                  `json:"sessions"`
	SkippedStateFiles int                  `json:"skipped_state_files,omitempty"`
	Panes             map[string]swarmPane `json:"panes"`
}

type swarmPaneReader func(ctx context.Context) swarmPaneObservation

func newHerdrPaneReader(bindingsDir, procRoot string) swarmPaneReader {
	return func(ctx context.Context) swarmPaneObservation {
		observation := swarmPaneObservation{Status: "unavailable", Panes: make(map[string]swarmPane)}
		scan, err := swarmHerdrSessions(ctx, bindingsDir, procRoot)
		observation.Sessions = len(scan.sessions)
		observation.SkippedStateFiles = scan.skippedStateFiles
		if err != nil {
			observation.Error = err.Error()
			return observation
		}
		matches, err := scan.client.ObservePanes(ctx, scan.sessions, os.Environ())
		if err != nil {
			var hostErr *wake.HerdrError
			switch {
			case ctx.Err() != nil:
				observation.Error = ctx.Err().Error()
			case errors.As(err, &hostErr):
				observation.Error = hostErr.Message
			default:
				observation.Error = "could not inspect Herdr session processes"
			}
			return observation
		}
		for _, match := range matches {
			if err := ctx.Err(); err != nil {
				observation.Error = err.Error()
				clear(observation.Panes)
				return observation
			}
			observation.Panes[match.AgentID] = swarmPane{
				PaneID:        match.PaneID,
				TabID:         match.TabID,
				WorkspaceID:   match.WorkspaceID,
				AgentStatus:   match.AgentStatus,
				TerminalTitle: match.TerminalTitle,
				Focused:       match.Focused,
				FocusCommand:  swarmPaneFocusCommand(match.PaneID),
			}
		}
		observation.Status = "ok"
		return observation
	}
}

type swarmHerdrBinding struct {
	Harness    string `json:"harness"`
	StateDir   string `json:"state_dir"`
	IdleWakeup string `json:"idle_wakeup"`
}

type swarmHerdrState struct {
	Retired swarmStateFlag  `json:"retired"`
	Ending  swarmStateFlag  `json:"ending"`
	Process wake.ProcessRef `json:"process"`
	Agent   struct {
		ID       string `json:"agent_id"`
		NativeID string `json:"native_session_id"`
		Metadata struct {
			Harness string `json:"harness"`
		} `json:"metadata"`
	} `json:"agent"`
}

// Keep the watcher's truth semantics for lifecycle files from older bindings.
type swarmStateFlag bool

func (flag *swarmStateFlag) UnmarshalJSON(data []byte) error {
	state, err := value.DecodeBytes(data, 0)
	if err != nil {
		return err
	}
	*flag = swarmStateFlag(value.Truth(state))
	return nil
}

type swarmHerdrScan struct {
	client            wake.HerdrClient
	sessions          []wake.PaneSession
	skippedStateFiles int
}

func swarmHerdrSessions(ctx context.Context, bindingsDir, procRoot string) (swarmHerdrScan, error) {
	if err := ctx.Err(); err != nil {
		return swarmHerdrScan{}, err
	}
	type result struct {
		scan swarmHerdrScan
		err  error
	}
	done := make(chan result, 1)
	go func() {
		scan, err := scanSwarmHerdrSessions(ctx, bindingsDir, procRoot)
		done <- result{scan, err}
	}()
	select {
	case <-ctx.Done():
		return swarmHerdrScan{}, ctx.Err()
	case scanned := <-done:
		return scanned.scan, scanned.err
	}
}

func scanSwarmHerdrSessions(ctx context.Context, bindingsDir, procRoot string) (swarmHerdrScan, error) {
	scan := swarmHerdrScan{client: wake.HerdrClient{ProcRoot: procRoot}}
	if bindingsDir == "" {
		home, err := dataDirectory()
		if err != nil {
			return scan, errors.New("could not find Cairn's bindings directory")
		}
		bindingsDir = filepath.Join(home, "coordination", "bindings")
	}
	bindings, err := os.ReadDir(bindingsDir)
	if err != nil {
		return scan, errors.New("could not list Cairn bindings")
	}
	stateDirs := make(map[string]bool)
	for _, entry := range bindings {
		if err := ctx.Err(); err != nil {
			return scan, err
		}
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		var binding swarmHerdrBinding
		data, err := readSwarmHerdrFile(filepath.Join(bindingsDir, entry.Name()))
		if err != nil || json.Unmarshal(data, &binding) != nil {
			return scan, errors.New("could not read a Cairn binding")
		}
		if scan.client.Executable == "" && filepath.IsAbs(binding.IdleWakeup) {
			scan.client.Executable = binding.IdleWakeup
		}
		if binding.StateDir == "" {
			return scan, errors.New("a Cairn binding has no session state directory")
		}
		directory := filepath.Clean(binding.StateDir)
		if stateDirs[directory] {
			continue
		}
		stateDirs[directory] = true
		if err := appendSwarmHerdrSessions(ctx, &scan, directory, binding.Harness); err != nil {
			return scan, err
		}
	}
	if err := ctx.Err(); err != nil {
		return scan, err
	}
	if scan.client.Executable == "" {
		return scan, errors.New("no Cairn binding has an absolute Herdr executable")
	}
	return scan, nil
}

func appendSwarmHerdrSessions(ctx context.Context, scan *swarmHerdrScan, directory, bindingHarness string) error {
	entries, err := os.ReadDir(directory)
	if err != nil {
		return errors.New("could not list Cairn session state")
	}
	live := make(map[wake.ProcessRef]bool)
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return err
		}
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		path := filepath.Join(directory, entry.Name())
		data, err := readSwarmHerdrFile(path)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return errors.New("could not read Cairn session state")
		}
		var state swarmHerdrState
		if err := json.Unmarshal(data, &state); err != nil {
			scan.skippedStateFiles++
			continue
		}
		if state.Retired || state.Ending {
			continue
		}
		if state.Agent.ID == "" || state.Agent.NativeID == "" {
			continue
		}
		alive, seen := live[state.Process]
		if !seen {
			alive = scan.client.ProcessAlive(state.Process)
			live[state.Process] = alive
		}
		if !alive {
			continue
		}
		harness := state.Agent.Metadata.Harness
		if harness == "" {
			harness = bindingHarness
		}
		scan.sessions = append(scan.sessions, wake.PaneSession{
			AgentID: state.Agent.ID, NativeSessionID: state.Agent.NativeID, Harness: harness,
			Process: state.Process, StateDir: directory, StatePath: path,
		})
	}
	return nil
}

func readSwarmHerdrFile(path string) ([]byte, error) {
	const maxBytes = 4 << 20
	data, err := readRegularFilePrefix(path, maxBytes+1)
	if err != nil {
		return nil, err
	}
	if len(data) > maxBytes {
		return nil, errors.New("Cairn lifecycle file exceeds size limit")
	}
	return data, nil
}

var plainSwarmPaneID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]*$`)

func swarmPaneFocusCommand(paneID string) string {
	if plainSwarmPaneID.MatchString(paneID) {
		return "herdr agent focus " + paneID
	}
	return "herdr agent focus -- '" + strings.ReplaceAll(paneID, "'", "'\"'\"'") + "'"
}
