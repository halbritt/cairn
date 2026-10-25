package siwc

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"

	"github.com/google/uuid"
)

const AuthProbeModel = "gpt-6-luna"

var accountAliasPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,31}$`)

// AuthBindingFile contains only opaque registration identifiers and a local
// operator label. The alias-to-subject association is unverified as a mapping
// to a particular Codex account and proves no model entitlement; owner mapping
// and completed inference remain separate evidence.
type AuthBindingFile struct {
	Version  int              `json:"version"`
	HostID   string           `json:"ext_agent_host_id"`
	Accounts []AccountBinding `json:"accounts"`
}

type AccountBinding struct {
	Alias    string `json:"alias"`
	Subject  string `json:"subject"`
	ClientID string `json:"client_id"`
}

type AuthProbeOptions struct {
	Alias              string
	BindingPath        string
	Authorize          bool
	ProbeEntitlement   bool
	OAuthHTTPClient    *http.Client
	OpenBrowser        func(string) error
	EntitlementTimeout time.Duration
}

type AuthProbeResult struct {
	Alias            string
	Subject          string
	ClientID         string
	ModelListed      bool
	EntitlementProbe bool
	Usage            Usage
}

// RunAuthorizationProbe is inert unless Authorize is explicitly true. OAuth
// secrets remain inside the call and are never written by AuthBindingFile.
func RunAuthorizationProbe(ctx context.Context, opts AuthProbeOptions) (AuthProbeResult, error) {
	if !opts.Authorize {
		return AuthProbeResult{}, errors.New("authorization probe is offline by default; pass --authorize to start consent")
	}
	if !accountAliasPattern.MatchString(opts.Alias) {
		return AuthProbeResult{}, errors.New("alias must use lowercase letters, digits, underscore, or hyphen")
	}
	path, err := resolveBindingPath(opts.BindingPath)
	if err != nil {
		return AuthProbeResult{}, err
	}
	if err := preparePrivatePath(filepath.Dir(path)); err != nil {
		return AuthProbeResult{}, err
	}
	bindings, exists, err := readBindings(path)
	if err != nil {
		return AuthProbeResult{}, err
	}
	if !exists {
		hostID, err := newHostID()
		if err != nil {
			return AuthProbeResult{}, err
		}
		bindings = AuthBindingFile{Version: 1, HostID: hostID, Accounts: []AccountBinding{}}
		if err := writeBindings(path, bindings, true); err != nil {
			return AuthProbeResult{}, err
		}
	}
	if bindings.Version != 1 || !validHostID(bindings.HostID) {
		return AuthProbeResult{}, errors.New("binding file has unsupported or invalid host identity")
	}
	var selected *AccountBinding
	for i := range bindings.Accounts {
		if bindings.Accounts[i].Alias == opts.Alias {
			selected = &bindings.Accounts[i]
			break
		}
	}
	var clientID, expectedSubject string
	if selected != nil {
		clientID, expectedSubject = selected.ClientID, selected.Subject
	}
	listener, err := ListenCallback()
	if err != nil {
		return AuthProbeResult{}, err
	}
	defer listener.Close()
	attempt, err := NewAttempt(listener.RedirectURI(), bindings.HostID, "Cairn Trial", clientID, expectedSubject)
	if err != nil {
		return AuthProbeResult{}, err
	}
	authURL, err := attempt.AuthorizationURL()
	if err != nil {
		return AuthProbeResult{}, err
	}
	open := opts.OpenBrowser
	if open == nil {
		open = openSystemBrowser
	}
	if err := open(authURL); err != nil {
		return AuthProbeResult{}, fmt.Errorf("open system browser: %w", err)
	}
	callback, err := listener.Wait(ctx)
	if err != nil {
		return AuthProbeResult{}, fmt.Errorf("wait for authorization callback: %w", err)
	}
	creds, err := attempt.Exchange(ctx, opts.OAuthHTTPClient, callback)
	if err != nil {
		return AuthProbeResult{}, err
	}
	// The account's catalog and optional inference use the same in-memory
	// credential object. Neither credentials nor account profile data persist.
	responses := &ResponsesClient{Credentials: creds, httpClient: opts.OAuthHTTPClient}
	models, err := responses.ModelCatalog(ctx)
	if err != nil {
		return AuthProbeResult{}, fmt.Errorf("model catalog check failed: %w", err)
	}
	if !contains(models, AuthProbeModel) {
		return AuthProbeResult{}, fmt.Errorf("requested model %q is not listed for this account", AuthProbeModel)
	}
	result := AuthProbeResult{Alias: opts.Alias, Subject: creds.Subject(), ClientID: creds.ClientID(), ModelListed: true}
	if opts.ProbeEntitlement {
		timeout := opts.EntitlementTimeout
		if timeout <= 0 || timeout > 90*time.Second {
			timeout = 45 * time.Second
		}
		probeCtx, cancel := context.WithTimeout(ctx, timeout)
		defer cancel()
		probe, err := responses.Run(probeCtx, AuthProbeModel, "Reply with exactly: OK", RunOptions{ReasoningEffort: "low", UseCairnTools: false})
		if err != nil {
			return AuthProbeResult{}, fmt.Errorf("bounded entitlement probe failed: %w", err)
		}
		if err := validateEntitlementResult(probe); err != nil {
			return AuthProbeResult{}, fmt.Errorf("bounded entitlement probe failed validation: %w", err)
		}
		result.EntitlementProbe = true
		result.Usage = probe.Usage
	}
	// Do not replace an existing binding unless both its opaque identity values
	// match exactly. A different identity requires an explicit fresh alias.
	if selected != nil {
		if selected.Subject != result.Subject || selected.ClientID != result.ClientID {
			return AuthProbeResult{}, errors.New("verified identity does not match the selected alias binding")
		}
	} else {
		bindings.Accounts = append(bindings.Accounts, AccountBinding{Alias: opts.Alias, Subject: result.Subject, ClientID: result.ClientID})
		if err := writeBindings(path, bindings, false); err != nil {
			return AuthProbeResult{}, err
		}
	}
	return result, nil
}

// RemoveAccountBinding explicitly removes one alias so the operator can
// re-enroll it. The stable host ID and all other aliases remain unchanged.
func RemoveAccountBinding(path, alias string) error {
	if !accountAliasPattern.MatchString(alias) {
		return errors.New("alias must use lowercase letters, digits, underscore, or hyphen")
	}
	resolved, err := resolveBindingPath(path)
	if err != nil {
		return err
	}
	if err := preparePrivatePath(filepath.Dir(resolved)); err != nil {
		return err
	}
	bindings, exists, err := readBindings(resolved)
	if err != nil {
		return err
	}
	if !exists {
		return errors.New("binding file does not exist")
	}
	if bindings.Version != 1 || !validHostID(bindings.HostID) {
		return errors.New("binding file has unsupported or invalid host identity")
	}
	kept := make([]AccountBinding, 0, len(bindings.Accounts))
	removed := false
	for _, account := range bindings.Accounts {
		if account.Alias == alias {
			removed = true
			continue
		}
		kept = append(kept, account)
	}
	if !removed {
		return errors.New("alias is not present in the binding file")
	}
	bindings.Accounts = kept
	return writeBindings(resolved, bindings, false)
}

func DefaultBindingPath() (string, error) {
	base := os.Getenv("XDG_CONFIG_HOME")
	if base == "" {
		base = os.Getenv("HOME")
		if base == "" {
			return "", errors.New("HOME or XDG_CONFIG_HOME is required")
		}
		base = filepath.Join(base, ".config")
	}
	return filepath.Join(base, "cairn", "siwc", "bindings.json"), nil
}

func resolveBindingPath(path string) (string, error) {
	if path == "" {
		var err error
		path, err = DefaultBindingPath()
		if err != nil {
			return "", err
		}
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	abs = filepath.Clean(abs)
	if err := rejectCheckoutPath(filepath.Dir(abs)); err != nil {
		return "", err
	}
	return abs, nil
}

func rejectCheckoutPath(dir string) error {
	for current := dir; ; current = filepath.Dir(current) {
		if _, err := os.Lstat(filepath.Join(current, ".git")); err == nil {
			return errors.New("binding path must be outside every Git checkout")
		} else if !errors.Is(err, os.ErrNotExist) {
			return fmt.Errorf("inspect binding ancestor: %w", err)
		}
		parent := filepath.Dir(current)
		if parent == current {
			return nil
		}
	}
}

func preparePrivatePath(path string) error {
	abs, err := filepath.Abs(path)
	if err != nil {
		return err
	}
	parts := strings.Split(filepath.Clean(abs), string(filepath.Separator))
	current := string(filepath.Separator)
	for _, part := range parts {
		if part == "" {
			continue
		}
		current = filepath.Join(current, part)
		info, err := os.Lstat(current)
		if errors.Is(err, os.ErrNotExist) {
			if err := os.Mkdir(current, 0700); err != nil && !errors.Is(err, os.ErrExist) {
				return fmt.Errorf("create owner-only binding directory: %w", err)
			}
			info, err = os.Lstat(current)
		}
		if err != nil {
			return fmt.Errorf("inspect binding path component: %w", err)
		}
		if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
			return errors.New("binding path contains a symlink or non-directory ancestor")
		}
		if current == abs {
			if stat, ok := info.Sys().(*syscall.Stat_t); ok && stat.Uid != uint32(os.Getuid()) {
				return errors.New("binding directory is not owned by the current user")
			}
			if err := os.Chmod(current, 0700); err != nil {
				return fmt.Errorf("set owner-only binding directory permissions: %w", err)
			}
		}
	}
	return nil
}

func readBindings(path string) (AuthBindingFile, bool, error) {
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return AuthBindingFile{}, false, nil
	}
	if err != nil {
		return AuthBindingFile{}, false, fmt.Errorf("inspect binding file: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return AuthBindingFile{}, false, errors.New("binding path is not a regular file")
	}
	if stat, ok := info.Sys().(*syscall.Stat_t); ok && stat.Uid != uint32(os.Getuid()) {
		return AuthBindingFile{}, false, errors.New("binding file is not owned by the current user")
	}
	if info.Mode().Perm()&0077 != 0 {
		return AuthBindingFile{}, false, errors.New("binding file permissions must be owner-only")
	}
	if err := os.Chmod(path, 0600); err != nil {
		return AuthBindingFile{}, false, fmt.Errorf("set binding file permissions: %w", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return AuthBindingFile{}, false, err
	}
	var file AuthBindingFile
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&file); err != nil {
		return AuthBindingFile{}, false, errors.New("binding file is malformed")
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return AuthBindingFile{}, false, errors.New("binding file contains trailing JSON data")
	}
	seen := map[string]bool{}
	subjects := map[string]bool{}
	clientIDs := map[string]bool{}
	for _, a := range file.Accounts {
		if !accountAliasPattern.MatchString(a.Alias) || a.Subject == "" || !strings.HasPrefix(a.ClientID, registeredPrefix) || seen[a.Alias] || subjects[a.Subject] || clientIDs[a.ClientID] {
			return AuthBindingFile{}, false, errors.New("binding file contains invalid account metadata")
		}
		seen[a.Alias] = true
		subjects[a.Subject] = true
		clientIDs[a.ClientID] = true
	}
	return file, true, nil
}

func validateEntitlementResult(result Result) error {
	if result.ServedModel != AuthProbeModel {
		return fmt.Errorf("served model %q did not match requested model %q", result.ServedModel, AuthProbeModel)
	}
	if result.Usage.InputTokens <= 0 || result.Usage.OutputTokens <= 0 || result.Usage.TotalTokens != result.Usage.InputTokens+result.Usage.OutputTokens {
		return errors.New("inference usage is missing or inconsistent")
	}
	return nil
}

func writeBindings(path string, file AuthBindingFile, createOnly bool) error {
	data, err := json.MarshalIndent(file, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	tmp, err := os.CreateTemp(filepath.Dir(path), ".bindings-*.tmp")
	if err != nil {
		return fmt.Errorf("create binding temp file: %w", err)
	}
	tmpPath := tmp.Name()
	defer os.Remove(tmpPath)
	if err := tmp.Chmod(0600); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if createOnly {
		if err := os.Link(tmpPath, path); err != nil {
			return fmt.Errorf("create stable host binding without overwrite: %w", err)
		}
		return os.Remove(tmpPath)
	}
	current, exists, err := readBindings(path)
	if err != nil {
		return err
	}
	if !exists || current.HostID != file.HostID {
		return errors.New("binding file changed or host ID drifted; refusing overwrite")
	}
	if err := os.Rename(tmpPath, path); err != nil {
		return fmt.Errorf("atomically update binding file: %w", err)
	}
	return nil
}

func newHostID() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	id, err := uuid.FromBytes(b)
	if err != nil {
		return "", err
	}
	return "urn:uuid:" + id.String(), nil
}

func validHostID(v string) bool {
	if !strings.HasPrefix(v, "urn:uuid:") {
		return false
	}
	_, err := uuid.Parse(strings.TrimPrefix(v, "urn:uuid:"))
	return err == nil
}

func openSystemBrowser(raw string) error {
	if _, err := url.ParseRequestURI(raw); err != nil {
		return errors.New("invalid authorization URL")
	}
	return exec.Command("xdg-open", raw).Start()
}

func Fingerprint(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:6])
}
