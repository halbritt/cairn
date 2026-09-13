package main

import (
	"encoding/json"
	"io"
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
	if err := parseHarnessFlags(f, args, out); err != nil {
		return err
	}
	if o.config.CodexThread {
		return invalid("opencode-config requires explicit --task and --run; --codex-thread is for Codex clients")
	}
	if err := o.validate(f.NArg()); err != nil {
		return err
	}
	command, err := o.command(executable)
	if err != nil {
		return err
	}
	config := openCodeMemoryConfig{MCP: map[string]openCodeServer{"cairn": {Type: "local", Enabled: true, Command: command}}}
	if *memoryOnly {
		config.Permission = map[string]string{"*": "deny", "cairn_cairn_search": "allow", "cairn_cairn_pull": "allow"}
	}
	return json.NewEncoder(out).Encode(config)
}
