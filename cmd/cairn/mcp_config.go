package main

import (
	"path/filepath"
	"strconv"
)

func (o *mcpOptions) command(executable string) ([]string, error) {
	// Harnesses may launch from another directory. Resolve paths without
	// opening credentials or connecting to the API just to render configuration.
	paths := []string{executable, o.socket, o.token}
	for i, path := range paths {
		absolute, err := filepath.Abs(path)
		if err != nil {
			return nil, err
		}
		paths[i] = absolute
	}
	command := []string{paths[0], "mcp", "--socket", paths[1], "--token-file", paths[2], "--repo", o.config.Scope.Repo}
	if o.config.CodexThread {
		command = append(command, "--codex-thread")
	} else {
		command = append(command, "--task", o.config.Scope.TaskID, "--run", o.config.Scope.RunID)
	}
	command = append(command, "--tokens", strconv.Itoa(o.config.AvailableTokens))
	for _, pin := range [][2]string{{"--revision", o.pins.Revision}, {"--workspace-sha256", o.pins.WorkspaceSHA256},
		{"--task-class", o.pins.TaskClass}, {"--task-phase", o.pins.TaskPhase}, {"--binding", o.pins.BindingID}, {"--capability", o.pins.CapabilityID}} {
		if pin[1] != "" {
			command = append(command, pin[0], pin[1])
		}
	}
	return command, nil
}
