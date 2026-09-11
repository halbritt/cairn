package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/integrations/opencode"
)

func TestOpenCodeInstallDeclaredTaskScope(t *testing.T) {
	project := t.TempDir()
	args := append(installArgs(project), "--task", "repair 日本語", "--run", "attempt 'literal'")
	if _, err := installOpenCode(args, "/tmp/cairn"); err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile(filepath.Join(project, ".opencode/cairn.json"))
	if err != nil {
		t.Fatal(err)
	}
	var settings map[string]any
	if err := json.Unmarshal(body, &settings); err != nil {
		t.Fatal(err)
	}
	if settings["task_id"] != "repair 日本語" || settings["run_id"] != "attempt 'literal'" {
		t.Fatalf("declared task scope changed: %s", body)
	}
}

func TestOpenCodeInstallTaskScopeDefaultsAndRefusals(t *testing.T) {
	for _, args := range [][]string{{}, {"--task", "long-lived-task"}} {
		project := t.TempDir()
		if _, err := installOpenCode(append(installArgs(project), args...), "/tmp/cairn"); err != nil {
			t.Fatal(err)
		}
		body, err := os.ReadFile(filepath.Join(project, ".opencode/cairn.json"))
		if err != nil {
			t.Fatal(err)
		}
		var settings map[string]any
		if err := json.Unmarshal(body, &settings); err != nil {
			t.Fatal(err)
		}
		if _, found := settings["run_id"]; found {
			t.Fatalf("installer fixed an undeclared run: %s", body)
		}
		if len(args) == 0 && settings["task_id"] != nil {
			t.Fatalf("installer fixed an undeclared task: %s", body)
		}
	}
	for _, args := range [][]string{{"--task", ""}, {"--task", " "}, {"--task", "*"}, {"--task", strings.Repeat("界", 86)}, {"--task", "bad\x00scope"}, {"--task", "bad\xffscope"}, {"--run", "run-only"}, {"--task", "valid", "--run", ""}, {"--task", "valid", "--run", "*"}} {
		project := t.TempDir()
		if _, err := installOpenCode(append(installArgs(project), args...), "/tmp/cairn"); err == nil {
			t.Fatalf("accepted invalid scope %q", args)
		}
		if _, err := os.Stat(filepath.Join(project, ".opencode")); !os.IsNotExist(err) {
			t.Fatalf("invalid scope wrote installation files: %v", err)
		}
	}
}

func installArgs(project string) []string {
	return []string{"--project", project, "--socket", "missing socket '$(literal)'", "--token-file", "missing token 日本語", "--repo", "repo:installation"}
}

func TestOpenCodeInstallWorksOfflineAndPreservesHostPolicy(t *testing.T) {
	t.Setenv("HOME", "")
	t.Setenv("CAIRN_HOME", "")
	t.Setenv("CAIRN_DATABASE_URL", "host=/absent-install-db dbname=denied")
	project := t.TempDir()
	policy := []byte(`{"permission":{"bash":"allow"},"model":"host/model"}`)
	if err := os.WriteFile(filepath.Join(project, "opencode.json"), policy, 0600); err != nil {
		t.Fatal(err)
	}
	args := append(installArgs(project), "--tokens", "64000", "--revision", strings.Repeat("a", 40), "--workspace-sha256", strings.Repeat("b", 64), "--task-class", "build", "--task-phase", "validation", "--binding", "host; literal", "--capability", "native")
	value, err := run(context.Background(), append([]string{"opencode-install"}, args...), strings.NewReader(""))
	if err != nil {
		t.Fatal(err)
	}
	result := value.(openCodeInstallation)
	if len(result.Files) != 2 {
		t.Fatal(result)
	}
	for _, file := range result.Files {
		info, err := os.Stat(file.Path)
		if err != nil || info.Mode().Perm() != 0600 || file.State != "written" {
			t.Fatalf("bad installed file: %+v, %v", file, err)
		}
		if err := os.Chtimes(file.Path, time.Unix(123, 0), time.Unix(123, 0)); err != nil {
			t.Fatal(err)
		}
	}
	var settings openCodeSettings
	config, err := os.ReadFile(filepath.Join(project, ".opencode/cairn.json"))
	if err != nil || json.Unmarshal(config, &settings) != nil {
		t.Fatalf("configuration unreadable: %v", err)
	}
	wantSocket, _ := filepath.Abs("missing socket '$(literal)'")
	wantToken, _ := filepath.Abs("missing token 日本語")
	executable, _ := os.Executable()
	if settings.Executable != executable || settings.Socket != wantSocket || settings.TokenFile != wantToken || settings.Repo != "repo:installation" || settings.Tokens != 64000 {
		t.Fatalf("configuration changed literal paths or identity: %s", config)
	}
	if !reflect.DeepEqual(settings.Context, map[string]string{"revision": strings.Repeat("a", 40), "workspace_sha256": strings.Repeat("b", 64), "task_class": "build", "task_phase": "validation", "binding": "host; literal", "capability": "native"}) {
		t.Fatal(settings.Context)
	}
	adapter, err := os.ReadFile(filepath.Join(project, ".opencode/tools/cairn.ts"))
	if err != nil || string(adapter) != opencode.Adapter() {
		t.Fatal("installed adapter differs from the binary's source", err)
	}
	retained, err := os.ReadFile(filepath.Join(project, "opencode.json"))
	if err != nil || string(retained) != string(policy) {
		t.Fatal("host policy changed", err)
	}
	value, err = run(context.Background(), append([]string{"opencode-install"}, args...), strings.NewReader(""))
	if err != nil {
		t.Fatal(err)
	}
	for _, file := range value.(openCodeInstallation).Files {
		info, err := os.Stat(file.Path)
		if err != nil || file.State != "unchanged" || info.ModTime().Unix() != 123 {
			t.Fatal("identical rerun rewrote an installed file", file, err)
		}
	}
}

func TestOpenCodeInstallChecksBothFilesBeforeReplacement(t *testing.T) {
	project := t.TempDir()
	dir := filepath.Join(project, ".opencode/tools")
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	adapter := filepath.Join(dir, "cairn.ts")
	if err := os.WriteFile(adapter, []byte("custom adapter"), 0600); err != nil {
		t.Fatal(err)
	}
	args := installArgs(project)
	_, err := installOpenCode(args, "/cairn")
	if core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "--replace") {
		t.Fatalf("differing file was not refused: %v", err)
	}
	if _, err := os.Stat(filepath.Join(project, ".opencode/cairn.json")); !os.IsNotExist(err) {
		t.Fatal("configuration written before detecting adapter conflict", err)
	}
	old, err := os.ReadFile(adapter)
	if err != nil || string(old) != "custom adapter" {
		t.Fatal("custom adapter changed on refused install", err)
	}
	if _, err := installOpenCode(append(args, "--replace"), "/cairn"); err != nil {
		t.Fatal(err)
	}
	current, err := os.ReadFile(adapter)
	if err != nil || string(current) != opencode.Adapter() {
		t.Fatal("explicit replacement did not install bundled adapter", err)
	}
}

func TestOpenCodeInstallRefusesSymlinkDestinations(t *testing.T) {
	for _, target := range []string{".opencode", ".opencode/tools", ".opencode/cairn.json", ".opencode/tools/cairn.ts"} {
		t.Run(target, func(t *testing.T) {
			project, outside := t.TempDir(), t.TempDir()
			path := filepath.Join(project, target)
			if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink(outside, path); err != nil {
				t.Fatal(err)
			}
			_, err := installOpenCode(append(installArgs(project), "--replace"), "/cairn")
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("symlink destination accepted: %v", err)
			}
			files, err := os.ReadDir(outside)
			if err != nil || len(files) != 0 {
				t.Fatal("installation escaped through symlink", err)
			}
		})
	}
}

func TestOpenCodeInstallInvalidArgumentsHaveNoEffects(t *testing.T) {
	for _, extra := range [][]string{{"--repo", "*"}, {"--token-file="}, {"--tokens", "255"}, {"--unknown-option", "invented"}, {"unexpected"}} {
		project := t.TempDir()
		_, err := installOpenCode(append(installArgs(project), extra...), "/cairn")
		if core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("invalid arguments accepted: %v: %v", extra, err)
		}
		files, err := os.ReadDir(project)
		if err != nil || len(files) != 0 {
			t.Fatal("invalid arguments created files", extra, err)
		}
	}
}

func TestOpenCodeInstallRecentFilesIsOptIn(t *testing.T) {
	project := t.TempDir()
	args := installArgs(project)
	plugin := filepath.Join(project, ".opencode/plugins/cairn-recent-files.ts")
	if _, err := installOpenCode(args, "/cairn"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Dir(plugin)); !os.IsNotExist(err) {
		t.Fatal("default installation created plugin directory", err)
	}
	result, err := installOpenCode(append(args, "--recent-files"), "/cairn")
	if err != nil || len(result.Files) != 3 {
		t.Fatal("opt-in plugin installation failed", result, err)
	}
	content, err := os.ReadFile(plugin)
	if err != nil || len(content) == 0 {
		t.Fatal("plugin missing", err)
	}
	if err := os.WriteFile(plugin, []byte("custom plugin"), 0600); err != nil {
		t.Fatal(err)
	}
	// Omission is not an uninstall or an implicit upgrade of a custom plugin.
	if _, err := installOpenCode(args, "/cairn"); err != nil {
		t.Fatal(err)
	}
	if _, err := installOpenCode(append(args, "--recent-files"), "/cairn"); core.Code(err) != "INVALID_REQUEST" {
		t.Fatal("differing plugin accepted without replacement", err)
	}
	if _, err := installOpenCode(append(args, "--recent-files", "--replace"), "/cairn"); err != nil {
		t.Fatal(err)
	}
	current, err := os.ReadFile(plugin)
	if err != nil || string(current) != string(content) {
		t.Fatal("plugin replacement differs", err)
	}
}

func TestOpenCodeInstallRecentFilesRefusesPluginSymlinks(t *testing.T) {
	for _, target := range []string{".opencode/plugins", ".opencode/plugins/cairn-recent-files.ts"} {
		t.Run(target, func(t *testing.T) {
			project, outside := t.TempDir(), t.TempDir()
			path := filepath.Join(project, target)
			if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink(outside, path); err != nil {
				t.Fatal(err)
			}
			if _, err := installOpenCode(append(installArgs(project), "--recent-files", "--replace"), "/cairn"); core.Code(err) != "INVALID_REQUEST" {
				t.Fatal("plugin symlink accepted", err)
			}
			if _, err := os.Stat(filepath.Join(project, ".opencode/cairn.json")); !os.IsNotExist(err) {
				t.Fatal("configuration written before plugin destination check", err)
			}
		})
	}
}
