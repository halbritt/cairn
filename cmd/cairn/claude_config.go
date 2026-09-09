package main

import (
	"encoding/json"
	"io"
	"strings"
	"unicode/utf8"
)

type claudeMemoryConfig struct {
	Servers map[string]claudeServer `json:"mcpServers"`
}

type claudeServer struct {
	Type    string   `json:"type"`
	Command string   `json:"command"`
	Args    []string `json:"args"`
}

func writeClaudeConfig(out io.Writer, args []string, executable string) error {
	f := flags("claude-config")
	o := mcpFlags(f)
	if err := f.Parse(args); err != nil {
		return err
	}
	if o.config.CodexThread {
		return invalid("claude-config requires explicit --task and --run; --codex-thread is for Codex clients")
	}
	if err := o.validate(f.NArg()); err != nil {
		return err
	}
	command, err := o.command(executable)
	if err != nil {
		return err
	}
	for _, arg := range command {
		if !utf8.ValidString(arg) || strings.Contains(arg, "${") {
			return invalid("Claude configuration requires UTF-8 arguments without environment expansion (${)")
		}
	}
	return json.NewEncoder(out).Encode(claudeMemoryConfig{Servers: map[string]claudeServer{
		"cairn": {Type: "stdio", Command: command[0], Args: command[1:]},
	}})
}
