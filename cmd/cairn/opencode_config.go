package main

import (
	"encoding/json"
	"io"
	"path/filepath"
	"strconv"
)

type openCodeMemoryConfig struct {
	MCP        map[string]openCodeServer `json:"mcp"`
	Permission map[string]string         `json:"permission,omitempty"`
}

type openCodeServer struct {
	Type    string   `json:"type"`
	Enabled bool     `json:"enabled"`
	Command []string `json:"command"`
}

func writeOpenCodeConfig(out io.Writer, args []string, executable string) error {
	f := flags("opencode-config")
	o := mcpFlags(f)
	memoryOnly := f.Bool("memory-only", false, "restrict the task to memory search and body pulls")
	if err := f.Parse(args); err != nil {
		return err
	}
	if o.config.CodexThread {
		return invalid("opencode-config requires explicit --task and --run; --codex-thread is for Codex clients")
	}
	if err := o.validate(f.NArg()); err != nil {
		return err
	}
	// The harness may launch from another directory. Resolve paths without
	// opening the token or requiring a running API just to render configuration.
	paths := []*string{&executable, &o.socket, &o.token}
	for _, path := range paths {
		absolute, err := filepath.Abs(*path)
		if err != nil {
			return err
		}
		*path = absolute
	}
	command := []string{executable, "mcp", "--socket", o.socket, "--token-file", o.token,
		"--repo", o.config.Scope.Repo, "--task", o.config.Scope.TaskID, "--run", o.config.Scope.RunID,
		"--tokens", strconv.Itoa(o.config.AvailableTokens)}
	for _, pin := range [][2]string{{"--revision", o.pins.Revision}, {"--workspace-sha256", o.pins.WorkspaceSHA256},
		{"--task-class", o.pins.TaskClass}, {"--binding", o.pins.BindingID}, {"--capability", o.pins.CapabilityID}} {
		if pin[1] != "" {
			command = append(command, pin[0], pin[1])
		}
	}
	config := openCodeMemoryConfig{MCP: map[string]openCodeServer{"cairn": {Type: "local", Enabled: true, Command: command}}}
	if *memoryOnly {
		config.Permission = map[string]string{"*": "deny", "cairn_cairn_search": "allow", "cairn_cairn_pull": "allow"}
	}
	return json.NewEncoder(out).Encode(config)
}
