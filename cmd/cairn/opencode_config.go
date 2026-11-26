package main

import (
	"encoding/json"
	"io"
	"os"
	"strings"
)

type openCodeMemoryConfig struct {
	MCP        map[string]openCodeServer   `json:"mcp"`
	Provider   map[string]openCodeProvider `json:"provider,omitempty"`
	Model      string                      `json:"model,omitempty"`
	Permission map[string]string           `json:"permission,omitempty"`
}

type openCodeServer struct {
	Type    string   `json:"type"`
	Enabled bool     `json:"enabled"`
	Command []string `json:"command"`
}

type openCodeProvider struct {
	NPM     string                         `json:"npm"`
	Name    string                         `json:"name"`
	Options map[string]any                 `json:"options"`
	Models  map[string]openCodeModelConfig `json:"models"`
}

type openCodeModelConfig struct {
	Name    string         `json:"name"`
	ID      string         `json:"id,omitempty"`
	Options map[string]any `json:"options,omitempty"`
}

func isTruthyEnv(name string) bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(name))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func writeOpenCodeConfig(out io.Writer, args []string, executable string) error {
	f := flags("opencode-config")
	o := mcpFlags(f)
	memoryOnly := f.Bool("memory-only", false, "restrict the task to memory search and body pulls")
	llamacppURL := f.String("llamacpp-url", "", "optional local llama.cpp OpenAI-compatible base URL (e.g. http://localhost:8081/v1)")
	llamacppModel := f.String("llamacpp-model", "qwen3.8-27b", "local llama.cpp model ID")
	llamacppStructured := f.Bool("llamacpp-structured", false, "configure local llama.cpp structured/runtime defaults with thinking disabled unless --allow-thinking is set")
	allowThinking := f.Bool("allow-thinking", false, "explicit off-switch: re-enable unrestricted thinking on non-structured local turns (also via CAIRN_OPENCODE_ALLOW_THINKING=1)")
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
	if *llamacppURL != "" || *llamacppStructured {
		baseURL := *llamacppURL
		if baseURL == "" {
			baseURL = "http://localhost:8081/v1"
		}
		modelID := strings.TrimSpace(*llamacppModel)
		if modelID == "" {
			return invalid("opencode-config requires a non-empty --llamacpp-model")
		}
		unrestricted := *allowThinking || isTruthyEnv("CAIRN_OPENCODE_ALLOW_THINKING")
		baseOpts := map[string]any{"temperature": 0.6, "top_p": 0.95}
		if *llamacppStructured && !unrestricted {
			baseOpts["chat_template_kwargs"] = map[string]any{"enable_thinking": false}
		}
		structuredID := modelID + "-structured"
		config.Provider = map[string]openCodeProvider{
			"llamacpp": {
				NPM:  "@ai-sdk/openai-compatible",
				Name: "llama.cpp (local)",
				Options: map[string]any{
					"baseURL": baseURL,
					"apiKey":  "no-key-required",
				},
				Models: map[string]openCodeModelConfig{
					modelID: {
						Name:    modelID + " (local llama.cpp)",
						Options: baseOpts,
					},
					structuredID: {
						Name: modelID + " (structured, thinking disabled)",
						ID:   modelID,
						Options: map[string]any{
							"temperature":          0,
							"chat_template_kwargs": map[string]any{"enable_thinking": false},
						},
					},
				},
			},
		}
		if *llamacppStructured {
			config.Model = "llamacpp/" + modelID
		}
	}
	return json.NewEncoder(out).Encode(config)
}
