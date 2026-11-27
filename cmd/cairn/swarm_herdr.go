package main

import (
	"context"
	"time"
)

// Herdr pane state for the swarm view (CAIRN-829). The read is host-local and
// has no side effects: nothing is sent to a pane and nothing read from Herdr
// is written to the store. The loop, cache and staleness are in
// swarm_stream.go; this file owns only the reader.

// swarmPane is the Herdr pane that hosts one local Cairn session.
type swarmPane struct {
	PaneID        string `json:"pane_id"`
	TabID         string `json:"tab_id,omitempty"`
	WorkspaceID   string `json:"workspace_id,omitempty"`
	AgentStatus   string `json:"agent_status"`
	TerminalTitle string `json:"terminal_title,omitempty"`
	Focused       bool   `json:"focused,omitempty"`
	// FocusCommand is shown with a copy control. The server never runs it.
	FocusCommand string `json:"focus_command"`
}

// swarmPaneObservation is one Herdr read. A session with no entry in Panes
// has no pane on this host.
type swarmPaneObservation struct {
	ObservedAt time.Time            `json:"observed_at"`
	Status     string               `json:"status"` // "ok" or "unavailable"; the loop adds "pending" before the first read
	Error      string               `json:"error,omitempty"`
	Sessions   int                  `json:"sessions"` // local sessions considered
	Panes      map[string]swarmPane `json:"panes"`    // by agent_id
}

// swarmPaneReader makes one observation. It never blocks beyond ctx.
type swarmPaneReader func(ctx context.Context) swarmPaneObservation

// newHerdrPaneReader returns the reader over the coordination bindings in
// bindingsDir. The Herdr part of CAIRN-829 replaces this stub.
func newHerdrPaneReader(bindingsDir, procRoot string) swarmPaneReader {
	return func(ctx context.Context) swarmPaneObservation {
		return swarmPaneObservation{ObservedAt: time.Now(), Status: "unavailable", Error: "the Herdr pane reader is not built yet", Panes: map[string]swarmPane{}}
	}
}
