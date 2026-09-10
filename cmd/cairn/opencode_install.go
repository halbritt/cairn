package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/integrations/opencode"
)

type openCodeSettings struct {
	Executable string            `json:"executable"`
	Socket     string            `json:"socket"`
	TokenFile  string            `json:"token_file"`
	Repo       string            `json:"repo"`
	Tokens     int               `json:"tokens"`
	Context    map[string]string `json:"context,omitempty"`
}

type installedFile struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
	State  string `json:"state"`
}

type openCodeInstallation struct {
	Project string          `json:"project"`
	Files   []installedFile `json:"files"`
}

func installationError(path string, err error) error {
	return &core.Error{Code: "INSTALL_FAILED", Message: fmt.Sprintf("install %s: %v", path, err), Cause: err}
}

// installOpenCode writes Cairn's adapter, connection file and optional plugin. It does not
// read credentials, contact the API, or change OpenCode's tool permissions.
func installOpenCode(args []string, executable string) (openCodeInstallation, error) {
	result := openCodeInstallation{Files: []installedFile{}}
	f := flags("opencode-install")
	project := f.String("project", ".", "existing target project directory")
	replace := f.Bool("replace", false, "replace differing Cairn adapter and connection files")
	recentFiles := f.Bool("recent-files", false, "install optional session-local recent-file search hints")
	settings := openCodeSettings{Executable: executable}
	f.StringVar(&settings.Socket, "socket", "", "Cairn Unix socket (required)")
	f.StringVar(&settings.TokenFile, "token-file", "", "provisioned ordinary agent token path (required)")
	f.StringVar(&settings.Repo, "repo", "", "canonical repository identity (required)")
	f.IntVar(&settings.Tokens, "tokens", 32000, "memory input room per tool result")
	pins := map[string]*string{}
	for _, name := range []string{"revision", "workspace-sha256", "task-class", "task-phase", "binding", "capability"} {
		pins[name] = f.String(name, "", "declared context pin; validated by the API on retrieval")
	}
	if err := f.Parse(args); err != nil {
		return result, invalid(err.Error())
	}
	if f.NArg() != 0 || *project == "" || settings.Socket == "" || settings.TokenFile == "" || strings.TrimSpace(settings.Repo) == "" || settings.Repo == "*" || len(settings.Repo) > 256 {
		return result, invalid("opencode-install requires --socket, --token-file and --repo, a project directory, and no positional arguments")
	}
	if settings.Tokens < 256 || settings.Tokens > 1000000 {
		return result, invalid("memory input room must be between 256 and 1000000")
	}
	for name, value := range pins {
		if *value != "" {
			if settings.Context == nil {
				settings.Context = map[string]string{}
			}
			settings.Context[strings.ReplaceAll(name, "-", "_")] = *value
		}
	}
	for _, path := range []*string{&settings.Executable, &settings.Socket, &settings.TokenFile, project} {
		absolute, err := filepath.Abs(*path)
		if err != nil {
			return result, installationError(*path, err)
		}
		*path = absolute
	}
	resolved, err := filepath.EvalSymlinks(*project)
	if err != nil {
		return result, installationError(*project, err)
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return result, installationError(resolved, err)
	}
	if !info.IsDir() {
		return result, invalid("project must be an existing directory")
	}
	result.Project = resolved
	directory := filepath.Join(resolved, ".opencode")
	tools := filepath.Join(directory, "tools")
	plugins := filepath.Join(directory, "plugins")
	directories := []string{directory, tools}
	if *recentFiles {
		directories = append(directories, plugins)
	}
	for _, path := range directories {
		info, err := os.Lstat(path)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return result, installationError(path, err)
		}
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return result, invalid(path + " must be a directory, not a symlink")
		}
	}
	config, err := json.MarshalIndent(settings, "", "  ")
	if err != nil {
		return result, installationError(directory, err)
	}
	files := []struct {
		path string
		body []byte
		same bool
	}{
		{path: filepath.Join(directory, "cairn.json"), body: append(config, '\n')},
		{path: filepath.Join(tools, "cairn.ts"), body: []byte(opencode.Adapter())},
	}
	if *recentFiles {
		files = append(files, struct {
			path string
			body []byte
			same bool
		}{path: filepath.Join(plugins, "cairn-recent-files.ts"), body: []byte(opencode.RecentFiles())})
	}
	// Check every destination before changing any file. Existing customizations
	// require an explicit replacement; identical reruns are safe.
	for i := range files {
		file := &files[i]
		info, err := os.Lstat(file.path)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return result, installationError(file.path, err)
		}
		if !info.Mode().IsRegular() {
			return result, invalid(file.path + " must be a regular file, not a symlink")
		}
		old, err := os.ReadFile(file.path)
		if err != nil {
			return result, installationError(file.path, err)
		}
		if !bytes.Equal(old, file.body) && !*replace {
			return result, invalid(file.path + " differs; inspect it and use --replace to replace selected Cairn files")
		}
		file.same = bytes.Equal(old, file.body) && info.Mode().Perm() == 0600
	}
	for _, path := range directories {
		if err = os.MkdirAll(path, 0700); err != nil {
			return result, installationError(path, err)
		}
	}
	for _, file := range files {
		state := "unchanged"
		if !file.same {
			if err = installFile(file.path, file.body); err != nil {
				return result, installationError(file.path, err)
			}
			state = "written"
		}
		digest := sha256.Sum256(file.body)
		result.Files = append(result.Files, installedFile{Path: file.path, SHA256: hex.EncodeToString(digest[:]), State: state})
	}
	return result, nil
}

// Each file is replaced atomically; the group is not a filesystem transaction.
// After a partial I/O failure, rerun the same installation to finish it.
func installFile(path string, body []byte) error {
	file, err := os.CreateTemp(filepath.Dir(path), ".cairn-install-*")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name()) // best-effort pending-file cleanup; rename removes it on success
	if err = file.Chmod(0600); err == nil {
		_, err = file.Write(body)
	}
	closeErr := file.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(file.Name(), path)
}
