package wakeup

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReadConfigRejectsExtraValuesAndEnforcesSizeBound(t *testing.T) {
	dir := t.TempDir()
	launcher := filepath.Join(dir, "launcher")
	if err := os.WriteFile(launcher, []byte("#!/bin/sh\nexit 0\n"), 0755); err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(Config{
		Name: "worker1", Principal: "agent/test", Repo: "/repo",
		Socket: "/tmp/cairn.sock", AgentToken: "/tmp/agent.token", ObserverToken: "/tmp/observer.token",
		Directory: dir, StateDirectory: dir, Command: []string{launcher}, TimeoutSeconds: 60,
	})
	if err != nil {
		t.Fatal(err)
	}
	valid := string(encoded)
	for _, test := range []struct {
		name string
		body string
		want string
	}{
		{"valid", valid, ""},
		{"exact limit", valid + strings.Repeat(" ", 32768-len(valid)), ""},
		{"one byte over", valid + strings.Repeat(" ", 32769-len(valid)), "maximum size"},
		{"extra value", valid + ` {"extra":true}`, "expected one wake config"},
		{"extra beyond reader", valid + strings.Repeat(" ", 32769-len(valid)) + `{}`, "maximum size"},
		{"unknown field", strings.Replace(valid, `"name":`, `"unknown":true,"name":`, 1), "unknown field"},
	} {
		t.Run(test.name, func(t *testing.T) {
			path := filepath.Join(dir, "config.json")
			if err := os.WriteFile(path, []byte(test.body), 0600); err != nil {
				t.Fatal(err)
			}
			cfg, err := ReadConfig(path)
			if test.want != "" {
				if err == nil || !strings.Contains(err.Error(), test.want) {
					t.Fatalf("expected %q error, got %v", test.want, err)
				}
			} else if err != nil || cfg.Name != "worker1" {
				t.Fatalf("valid config: name=%q error=%v", cfg.Name, err)
			}
		})
	}
}
