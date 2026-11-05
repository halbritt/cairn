package plane

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/BurntSushi/toml"
	"github.com/google/uuid"
)

const (
	MaxCLIOutputBytes    = 16 * 1024 * 1024 // 16 MiB
	MaxCLIErrOutputBytes = 1 * 1024 * 1024  // 1 MiB
	DefaultTimeout       = 30 * time.Second
	MaxItemLimit         = 10000
	routingSchemaVersion = "plane-routing/1"
)

type Error struct {
	Code    string
	Message string
	Cause   error
}

func (e *Error) Error() string { return e.Message }
func (e *Error) Unwrap() error { return e.Cause }

var (
	ErrAuthRejected   = &Error{Code: "AUTHORITY_DENIED", Message: "Plane authentication/authorization rejected"}
	ErrTooLarge       = &Error{Code: "PROJECT_TOO_LARGE", Message: "Plane project exceeds maximum of 10,000 items"}
	errStderrTooLarge = errors.New("plane stderr exceeded maximum size")
)

type NormalizedItem struct {
	PlaneItemUUID   uuid.UUID  `json:"plane_item_uuid"`
	Identifier      string     `json:"identifier"`
	Title           string     `json:"title"`
	StateID         *uuid.UUID `json:"state_id,omitempty"`
	StateName       string     `json:"state_name,omitempty"`
	StateGroup      string     `json:"state_group,omitempty"`
	AssigneeIDs     []string   `json:"assignee_ids"`
	AssigneeNames   []string   `json:"assignee_names"`
	UpdatedAtSource *time.Time `json:"updated_at_source,omitempty"`
	SourceURL       string     `json:"source_url,omitempty"`
}

type ConfigSelector struct {
	ConfigPath string
	Instance   string
	BaseURL    string
	Workspace  string
	Project    string
}

type EffectiveConfig struct {
	ConfigPath    string `json:"config_path,omitempty"`
	Instance      string `json:"instance,omitempty"`
	PlaneOrigin   string `json:"plane_origin"`
	WorkspaceSlug string `json:"workspace_slug"`
	Project       string `json:"project,omitempty"`
}

type ProjectBindingConfig struct {
	PlaneOrigin       string    `json:"plane_origin"`
	WorkspaceUUID     uuid.UUID `json:"workspace_uuid"`
	WorkspaceSlug     string    `json:"workspace_slug"`
	ProjectUUID       uuid.UUID `json:"project_uuid"`
	ProjectIdentifier string    `json:"project_identifier"`
	PlaneInstance     string    `json:"plane_instance"`
	ConnectorIdentity string    `json:"connector_identity"`
	ConfigRevision    string    `json:"config_revision"`
}

type Connector struct {
	BinaryPath string
	ConfigPath string
	Timeout    time.Duration
	Getenv     func(string) string
	wrapStdout func(io.Reader) io.Reader
	wrapStderr func(io.Reader) io.Reader
}

func NewConnector() *Connector {
	bin := os.Getenv("CAIRN_PLANE_BIN")
	if bin == "" {
		bin = "plane"
	}
	return &Connector{BinaryPath: bin, Timeout: DefaultTimeout}
}

func (c *Connector) getenv(key string) string {
	if c != nil && c.Getenv != nil {
		return c.Getenv(key)
	}
	return os.Getenv(key)
}

type tomlConfigFile struct {
	BaseURL          string                        `toml:"base_url"`
	Workspace        string                        `toml:"workspace"`
	Project          string                        `toml:"project"`
	DefaultInstance  string                        `toml:"default_instance"`
	Instances        map[string]tomlConfigInstance `toml:"instance"`
	ProjectOverrides map[string]tomlConfigOverride `toml:"project_overrides"`
}

type tomlConfigInstance struct {
	BaseURL   string `toml:"base_url"`
	Workspace string `toml:"workspace"`
}

type tomlConfigOverride struct {
	Instance  string `toml:"instance"`
	BaseURL   string `toml:"base_url"`
	Workspace string `toml:"workspace"`
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

func (c *Connector) resolveConfigPath(flagPath string) string {
	if flagPath != "" {
		return flagPath
	}
	if c != nil && c.ConfigPath != "" {
		return c.ConfigPath
	}
	if p := c.getenv("PLANE_CONFIG"); p != "" {
		return p
	}
	base := c.getenv("XDG_CONFIG_HOME")
	if base == "" {
		home, err := os.UserHomeDir()
		if err != nil || home == "" {
			return ""
		}
		base = filepath.Join(home, ".config")
	}
	return filepath.Join(base, "plane-cli", "config.toml")
}

func (c *Connector) ResolveEffectiveConfig(sel ConfigSelector) (EffectiveConfig, error) {
	var f tomlConfigFile
	path := c.resolveConfigPath(sel.ConfigPath)
	if path != "" {
		if _, err := toml.DecodeFile(path, &f); err != nil && !os.IsNotExist(err) {
			return EffectiveConfig{}, &Error{
				Code:    "INVALID_REQUEST",
				Message: fmt.Sprintf("plane config file %s: %v", path, err),
			}
		}
	}

	project := firstNonEmpty(sel.Project, c.getenv("PLANE_PROJECT"), f.Project)

	var ov tomlConfigOverride
	if project != "" && len(f.ProjectOverrides) > 0 {
		keys := make([]string, 0, len(f.ProjectOverrides))
		for k := range f.ProjectOverrides {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, name := range keys {
			if strings.EqualFold(name, project) {
				ov = f.ProjectOverrides[name]
				break
			}
		}
	}

	instName := firstNonEmpty(sel.Instance, c.getenv("PLANE_INSTANCE"))
	explicit := instName != ""
	if !explicit {
		instName = firstNonEmpty(ov.Instance, f.DefaultInstance)
	}
	if explicit && ov.Instance != "" && !strings.EqualFold(ov.Instance, instName) {
		ov = tomlConfigOverride{}
	}

	var inst tomlConfigInstance
	canonicalInst := ""
	if instName != "" {
		keys := make([]string, 0, len(f.Instances))
		for k := range f.Instances {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		found := false
		for _, name := range keys {
			if strings.EqualFold(name, instName) {
				inst = f.Instances[name]
				canonicalInst = name
				found = true
				break
			}
		}
		if !found {
			return EffectiveConfig{}, &Error{
				Code:    "CONFIG_MISMATCH",
				Message: fmt.Sprintf("Plane instance %q is not defined in %s", instName, path),
			}
		}
	}

	fileBase := firstNonEmpty(ov.BaseURL, inst.BaseURL, f.BaseURL)
	fileWS := firstNonEmpty(ov.Workspace, inst.Workspace, f.Workspace)
	rawBase := firstNonEmpty(sel.BaseURL, c.getenv("PLANE_BASE_URL"), fileBase)
	if strings.TrimSpace(rawBase) == "" {
		return EffectiveConfig{}, &Error{
			Code:    "CONFIG_MISMATCH",
			Message: "Plane base URL is not configured (set PLANE_BASE_URL or base_url in config.toml)",
		}
	}
	normOrigin, err := NormalizeOrigin(rawBase)
	if err != nil {
		return EffectiveConfig{}, err
	}
	wsSlug := strings.TrimSpace(firstNonEmpty(sel.Workspace, c.getenv("PLANE_WORKSPACE"), fileWS))
	if wsSlug == "" {
		return EffectiveConfig{}, &Error{
			Code:    "CONFIG_MISMATCH",
			Message: "Plane workspace slug is not configured (set PLANE_WORKSPACE or workspace in config.toml)",
		}
	}
	return EffectiveConfig{
		ConfigPath: path, Instance: canonicalInst, PlaneOrigin: normOrigin, WorkspaceSlug: wsSlug, Project: project,
	}, nil
}

func NormalizeOrigin(rawURL string) (string, error) {
	trimmed := strings.TrimSpace(rawURL)
	if trimmed == "" {
		return "", &Error{Code: "INVALID_REQUEST", Message: "empty Plane base URL"}
	}
	u, err := url.Parse(trimmed)
	if err != nil {
		return "", &Error{Code: "INVALID_REQUEST", Message: "invalid Plane base URL: " + err.Error()}
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return "", &Error{Code: "INVALID_REQUEST", Message: "plane origin must use http or https scheme"}
	}
	if u.Host == "" {
		return "", &Error{Code: "INVALID_REQUEST", Message: "plane origin must include a host"}
	}
	if u.User != nil {
		return "", &Error{Code: "INVALID_REQUEST", Message: "plane origin must not embed credentials"}
	}
	u.Scheme = strings.ToLower(u.Scheme)
	u.Host = strings.ToLower(u.Host)
	u.Path = ""
	u.RawPath = ""
	u.RawQuery = ""
	u.Fragment = ""
	return strings.TrimRight(u.String(), "/"), nil
}

func parseCanonicalUUID(raw string) (uuid.UUID, error) {
	if len(raw) != 36 {
		return uuid.Nil, fmt.Errorf("expected canonical 36-character UUID, got %q", raw)
	}
	for _, idx := range []int{8, 13, 18, 23} {
		if raw[idx] != '-' {
			return uuid.Nil, fmt.Errorf("expected hyphenated RFC 4122 UUID, got %q", raw)
		}
	}
	return uuid.Parse(raw)
}

func (c *Connector) ConnectorIdentity() string {
	bin := "plane"
	if c != nil && c.BinaryPath != "" {
		bin = c.BinaryPath
	}
	if resolved, err := exec.LookPath(bin); err == nil {
		if eval, err2 := filepath.EvalSymlinks(resolved); err2 == nil {
			return filepath.Clean(eval)
		}
		return filepath.Clean(resolved)
	}
	return filepath.Clean(bin)
}

func ComputeConfigRevision(connectorIdentity, normOrigin, workspaceSlug string, workspaceUUID, projectUUID uuid.UUID) string {
	payload, _ := json.Marshal(struct {
		Schema            string `json:"schema"`
		ConnectorIdentity string `json:"connector_identity"`
		PlaneOrigin       string `json:"plane_origin"`
		WorkspaceSlug     string `json:"workspace_slug"`
		WorkspaceUUID     string `json:"workspace_uuid"`
		ProjectUUID       string `json:"project_uuid"`
	}{
		Schema: routingSchemaVersion, ConnectorIdentity: connectorIdentity, PlaneOrigin: normOrigin,
		WorkspaceSlug: workspaceSlug, WorkspaceUUID: workspaceUUID.String(), ProjectUUID: projectUUID.String(),
	})
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:16])
}

var allowedSubcommands = map[string]map[string]bool{
	"project":   {"get": true, "list": true, "summary": true},
	"state":     {"list": true, "get": true},
	"issue":     {"list": true, "get": true, "search": true},
	"workspace": {"get": true, "list": true, "me": true},
	"me":        {"": true},
	"version":   {"": true},
}

var allowedValueFlags = map[string]bool{
	"-p": true, "--project": true,
	"-i": true, "--instance": true,
	"-w": true, "--workspace": true,
	"--base-url": true, "--config": true,
	"--fields": true, "--limit": true, "--cursor": true,
	"--order-by": true, "--timeout": true, "--max-retries": true,
	"--expand": true, "--priority": true, "--state": true,
	"--label": true, "--assignee": true, "--count-fields": true,
	"--external-id": true, "--external-source": true,
}

var allowedBoolFlags = map[string]bool{
	"--no-resolve": true, "--debug": true, "--workspace-wide": true,
}

func (c *Connector) validateReadCommand(args []string) error {
	var cmd, subcmd string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if arg == "-" || arg == "--" {
			return &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("disallowed Plane argument: %s", arg)}
		}
		if strings.HasPrefix(arg, "-") {
			flagName := arg
			hasInlineValue := false
			if eq := strings.IndexByte(arg, '='); eq > 0 {
				flagName = arg[:eq]
				hasInlineValue = true
			}
			if allowedBoolFlags[flagName] {
				continue
			}
			if allowedValueFlags[flagName] {
				if !hasInlineValue {
					if i+1 >= len(args) {
						return &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("missing value for Plane flag %s", flagName)}
					}
					i++
				}
				continue
			}
			return &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("disallowed Plane flag: %s", flagName)}
		}
		if cmd == "" {
			cmd = arg
		} else if subcmd == "" {
			subcmd = arg
		}
	}
	allowedSubs, ok := allowedSubcommands[cmd]
	if !ok {
		return &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("disallowed Plane command: %s", cmd)}
	}
	if allowedSubs[""] {
		if subcmd != "" {
			return &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("unexpected argument %q for Plane command %s", subcmd, cmd)}
		}
		return nil
	}
	if !allowedSubs[subcmd] {
		return &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("disallowed Plane subcommand: %s %s", cmd, subcmd)}
	}
	return nil
}

func pinnedArgs(eff EffectiveConfig, projectUUID uuid.UUID, tail ...string) []string {
	args := make([]string, 0, 12+len(tail))
	if eff.ConfigPath != "" {
		args = append(args, "--config", eff.ConfigPath)
	}
	if eff.Instance != "" {
		args = append(args, "--instance", eff.Instance)
	}
	args = append(args,
		"--base-url", eff.PlaneOrigin,
		"--workspace", eff.WorkspaceSlug,
		"--project", projectUUID.String(),
		"--no-resolve",
	)
	args = append(args, tail...)
	return args
}

type boundedReader struct {
	r        io.Reader
	limit    int64
	read     int64
	limitErr error
	kill     func()
}

func (b *boundedReader) Read(p []byte) (int, error) {
	n, err := b.r.Read(p)
	if n > 0 {
		b.read += int64(n)
		if b.read > b.limit {
			if b.kill != nil {
				b.kill()
			}
			return n, b.limitErr
		}
	}
	return n, err
}

func (c *Connector) run(parentCtx context.Context, args []string) ([]byte, error) {
	if err := c.validateReadCommand(args); err != nil {
		return nil, err
	}
	timeout := c.Timeout
	if timeout <= 0 {
		timeout = DefaultTimeout
	}
	runCtx, cancel := context.WithTimeout(parentCtx, timeout)
	defer cancel()

	cmd := exec.Command(c.BinaryPath, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	baseEnv := os.Environ()
	env := make([]string, 0, len(baseEnv)+1)
	for _, e := range baseEnv {
		if strings.HasPrefix(e, "PLANE_BASE_URL=") ||
			strings.HasPrefix(e, "PLANE_WORKSPACE=") ||
			strings.HasPrefix(e, "PLANE_PROJECT=") ||
			strings.HasPrefix(e, "PLANE_INSTANCE=") ||
			strings.HasPrefix(e, "PLANE_CONFIG=") {
			continue
		}
		env = append(env, e)
	}
	if cfgPath := c.resolveConfigPath(""); cfgPath != "" {
		env = append(env, "PLANE_CONFIG="+cfgPath)
	}
	cmd.Env = env

	stdoutR, stdoutW, err := os.Pipe()
	if err != nil {
		return nil, &Error{Code: "STORE_ERROR", Message: "failed to open stdout pipe: " + err.Error()}
	}
	stderrR, stderrW, err := os.Pipe()
	if err != nil {
		_ = stdoutR.Close()
		_ = stdoutW.Close()
		return nil, &Error{Code: "STORE_ERROR", Message: "failed to open stderr pipe: " + err.Error()}
	}
	cmd.Stdout = stdoutW
	cmd.Stderr = stderrW
	if err := cmd.Start(); err != nil {
		_ = stdoutR.Close()
		_ = stdoutW.Close()
		_ = stderrR.Close()
		_ = stderrW.Close()
		return nil, &Error{Code: "STORE_ERROR", Message: "failed to start plane CLI: " + err.Error()}
	}
	_ = stdoutW.Close()
	_ = stderrW.Close()

	pgid := cmd.Process.Pid
	killGroup := func() {
		if pgid > 0 {
			_ = syscall.Kill(-pgid, syscall.SIGKILL)
		}
		if cmd.Process != nil {
			_ = cmd.Process.Kill()
		}
	}
	doneCh := make(chan struct{})
	var killedByCtx atomic.Bool
	go func() {
		select {
		case <-runCtx.Done():
			killedByCtx.Store(true)
			killGroup()
			_ = stdoutR.Close()
			_ = stderrR.Close()
		case <-doneCh:
		}
	}()
	var outReader io.Reader = stdoutR
	if c.wrapStdout != nil {
		outReader = c.wrapStdout(stdoutR)
	}
	var errReader io.Reader = stderrR
	if c.wrapStderr != nil {
		errReader = c.wrapStderr(stderrR)
	}
	var stdoutBuf, stderrBuf bytes.Buffer
	boundedStdout := &boundedReader{r: outReader, limit: MaxCLIOutputBytes, limitErr: ErrTooLarge, kill: killGroup}
	boundedStderr := &boundedReader{r: errReader, limit: MaxCLIErrOutputBytes, limitErr: errStderrTooLarge, kill: killGroup}

	var wg sync.WaitGroup
	var stdoutErr, stderrErr error

	wg.Add(2)
	go func() {
		defer wg.Done()
		_, stdoutErr = io.Copy(&stdoutBuf, boundedStdout)
		if stdoutErr != nil {
			_ = stdoutR.Close()
			killGroup()
		}
	}()
	go func() {
		defer wg.Done()
		_, stderrErr = io.Copy(&stderrBuf, boundedStderr)
		if stderrErr != nil {
			_ = stderrR.Close()
			killGroup()
		}
	}()

	waitErr := cmd.Wait()
	killGroup() // Terminate any background descendant in -pgid retaining inherited pipe write FDs
	wg.Wait()   // Drain buffered output while runCtx deadline remains armed on stdoutR/stderrR
	close(doneCh)
	_ = stdoutR.Close()
	_ = stderrR.Close()

	if errors.Is(stdoutErr, ErrTooLarge) || stdoutBuf.Len() > MaxCLIOutputBytes {
		return nil, ErrTooLarge
	}
	if errors.Is(stderrErr, errStderrTooLarge) || stderrBuf.Len() > MaxCLIErrOutputBytes {
		return nil, &Error{Code: "INVALID_REQUEST", Message: "Plane CLI diagnostic output exceeded maximum size"}
	}
	if killedByCtx.Load() || runCtx.Err() != nil {
		if errors.Is(parentCtx.Err(), context.Canceled) {
			return nil, &Error{Code: "INVALID_REQUEST", Message: "Plane CLI execution cancelled", Cause: context.Canceled}
		}
		return nil, &Error{Code: "MODEL_TIMEOUT", Message: "Plane CLI execution timed out", Cause: context.DeadlineExceeded}
	}
	if stdoutErr != nil {
		return nil, &Error{Code: "STORE_ERROR", Message: "failed reading Plane CLI stdout: " + stdoutErr.Error(), Cause: stdoutErr}
	}
	if stderrErr != nil {
		return nil, &Error{Code: "STORE_ERROR", Message: "failed reading Plane CLI stderr: " + stderrErr.Error(), Cause: stderrErr}
	}

	var exitCode int
	if waitErr != nil {
		var exitErr *exec.ExitError
		if errors.As(waitErr, &exitErr) {
			exitCode = exitErr.ExitCode()
		} else {
			return nil, &Error{Code: "STORE_ERROR", Message: fmt.Sprintf("plane execution error: %v", waitErr)}
		}
	}

	raw := stdoutBuf.Bytes()
	if exitCode == 3 {
		return nil, ErrAuthRejected
	}

	var envelope struct {
		OK    bool            `json:"ok"`
		Data  json.RawMessage `json:"data"`
		Error *struct {
			Code       string `json:"code"`
			Message    string `json:"message"`
			HTTPStatus int    `json:"http_status"`
		} `json:"error"`
	}
	if len(raw) > 0 {
		if jsonErr := json.Unmarshal(raw, &envelope); jsonErr == nil && !envelope.OK {
			if envelope.Error != nil {
				if envelope.Error.HTTPStatus == 401 || envelope.Error.HTTPStatus == 403 ||
					strings.EqualFold(envelope.Error.Code, "auth") || strings.EqualFold(envelope.Error.Code, "AUTH_ERROR") {
					return nil, ErrAuthRejected
				}
				return nil, &Error{Code: "INVALID_REQUEST", Message: "Plane API error: " + envelope.Error.Message}
			}
			return nil, &Error{Code: "INVALID_REQUEST", Message: "Plane CLI returned non-OK envelope"}
		}
	}

	if exitCode != 0 {
		return nil, &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("Plane CLI exited with code %d", exitCode)}
	}
	return raw, nil
}

func (c *Connector) RunReadCommand(ctx context.Context, args []string) ([]byte, error) {
	return c.run(ctx, args)
}

func extractCanonicalUUID(raw json.RawMessage) (uuid.UUID, error) {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 || bytes.Equal(trimmed, []byte("null")) {
		return uuid.Nil, errors.New("missing UUID value")
	}
	switch trimmed[0] {
	case '"':
		var s string
		if err := json.Unmarshal(trimmed, &s); err != nil {
			return uuid.Nil, err
		}
		return parseCanonicalUUID(s)
	case '{':
		var obj struct {
			ID string `json:"id"`
		}
		if err := json.Unmarshal(trimmed, &obj); err != nil {
			return uuid.Nil, err
		}
		return parseCanonicalUUID(obj.ID)
	default:
		return uuid.Nil, fmt.Errorf("unexpected JSON token for UUID: %s", string(trimmed))
	}
}

type fetchedProject struct {
	ProjectUUID   uuid.UUID
	WorkspaceUUID uuid.UUID
	Identifier    string
}

func (c *Connector) fetchProject(ctx context.Context, eff EffectiveConfig, projectUUID uuid.UUID) (fetchedProject, error) {
	args := pinnedArgs(eff, projectUUID, "project", "get", projectUUID.String(), "--fields", "id,workspace,identifier")
	raw, err := c.run(ctx, args)
	if err != nil {
		return fetchedProject{}, err
	}

	var env struct {
		OK   bool            `json:"ok"`
		Data json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(raw, &env); err != nil {
		return fetchedProject{}, &Error{Code: "INVALID_REQUEST", Message: "invalid Plane project envelope: " + err.Error()}
	}
	if !env.OK {
		return fetchedProject{}, &Error{Code: "INVALID_REQUEST", Message: "Plane project get returned ok=false"}
	}
	trimmedData := bytes.TrimSpace(env.Data)
	if len(trimmedData) == 0 || trimmedData[0] != '{' {
		return fetchedProject{}, &Error{Code: "INVALID_REQUEST", Message: "Plane project get returned missing or non-object data"}
	}

	var projData struct {
		ID         string          `json:"id"`
		Workspace  json.RawMessage `json:"workspace"`
		Identifier string          `json:"identifier"`
	}
	if err := json.Unmarshal(trimmedData, &projData); err != nil {
		return fetchedProject{}, &Error{Code: "INVALID_REQUEST", Message: "malformed Plane project data: " + err.Error()}
	}
	pUUID, err := parseCanonicalUUID(projData.ID)
	if err != nil {
		return fetchedProject{}, &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("Plane project has invalid UUID %q: %v", projData.ID, err)}
	}
	wUUID, err := extractCanonicalUUID(projData.Workspace)
	if err != nil {
		return fetchedProject{}, &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("Plane project workspace is not a valid UUID: %v", err)}
	}
	ident := strings.TrimSpace(projData.Identifier)
	if ident == "" {
		return fetchedProject{}, &Error{Code: "INVALID_REQUEST", Message: "Plane project get omitted required project identifier"}
	}
	return fetchedProject{ProjectUUID: pUUID, WorkspaceUUID: wUUID, Identifier: ident}, nil
}

func (c *Connector) ResolveBinding(ctx context.Context, sel ConfigSelector, expectedWorkspaceUUID, expectedProjectUUID uuid.UUID) (ProjectBindingConfig, error) {
	if expectedProjectUUID == uuid.Nil || expectedWorkspaceUUID == uuid.Nil {
		return ProjectBindingConfig{}, &Error{Code: "INVALID_REQUEST", Message: "valid workspace and project UUIDs are required"}
	}

	var expectedOrigin string
	if strings.TrimSpace(sel.BaseURL) != "" {
		norm, err := NormalizeOrigin(sel.BaseURL)
		if err != nil {
			return ProjectBindingConfig{}, err
		}
		expectedOrigin = norm
	}

	eff, err := c.ResolveEffectiveConfig(ConfigSelector{
		ConfigPath: sel.ConfigPath,
		Instance:   sel.Instance,
		Workspace:  sel.Workspace,
		Project:    expectedProjectUUID.String(),
	})
	if err != nil {
		return ProjectBindingConfig{}, err
	}
	if expectedOrigin != "" && eff.PlaneOrigin != expectedOrigin {
		return ProjectBindingConfig{}, &Error{
			Code:    "CONFIG_MISMATCH",
			Message: fmt.Sprintf("requested origin %q differs from Plane profile origin %q", expectedOrigin, eff.PlaneOrigin),
		}
	}

	fp, err := c.fetchProject(ctx, eff, expectedProjectUUID)
	if err != nil {
		return ProjectBindingConfig{}, err
	}
	if fp.ProjectUUID != expectedProjectUUID {
		return ProjectBindingConfig{}, &Error{
			Code:    "CONFIG_MISMATCH",
			Message: fmt.Sprintf("Plane project UUID mismatch: expected %s, got %s", expectedProjectUUID, fp.ProjectUUID),
		}
	}
	if fp.WorkspaceUUID != expectedWorkspaceUUID {
		return ProjectBindingConfig{}, &Error{
			Code:    "CONFIG_MISMATCH",
			Message: fmt.Sprintf("Plane workspace UUID mismatch: expected %s, got %s", expectedWorkspaceUUID, fp.WorkspaceUUID),
		}
	}

	connIdent := c.ConnectorIdentity()
	configRev := ComputeConfigRevision(connIdent, eff.PlaneOrigin, eff.WorkspaceSlug, fp.WorkspaceUUID, fp.ProjectUUID)
	return ProjectBindingConfig{
		PlaneOrigin:       eff.PlaneOrigin,
		WorkspaceUUID:     fp.WorkspaceUUID,
		WorkspaceSlug:     eff.WorkspaceSlug,
		ProjectUUID:       fp.ProjectUUID,
		ProjectIdentifier: fp.Identifier,
		PlaneInstance:     sel.Instance,
		ConnectorIdentity: connIdent,
		ConfigRevision:    configRev,
	}, nil
}

func (c *Connector) VerifyRefreshBinding(ctx context.Context, planeInstance, pinnedOrigin string, pinnedWorkspaceUUID, pinnedProjectUUID uuid.UUID, pinnedConfigRev string) (ProjectBindingConfig, error) {
	eff, err := c.ResolveEffectiveConfig(ConfigSelector{
		Instance: planeInstance,
		Project:  pinnedProjectUUID.String(),
	})
	if err != nil {
		return ProjectBindingConfig{}, err
	}
	if eff.PlaneOrigin != pinnedOrigin {
		return ProjectBindingConfig{}, &Error{
			Code:    "CONFIG_MISMATCH",
			Message: fmt.Sprintf("Plane origin changed from pinned %q to %q", pinnedOrigin, eff.PlaneOrigin),
		}
	}
	fp, err := c.fetchProject(ctx, eff, pinnedProjectUUID)
	if err != nil {
		return ProjectBindingConfig{}, err
	}
	if fp.ProjectUUID != pinnedProjectUUID || fp.WorkspaceUUID != pinnedWorkspaceUUID {
		return ProjectBindingConfig{}, &Error{
			Code:    "CONFIG_MISMATCH",
			Message: "Plane project or workspace UUID differs from pinned binding",
		}
	}
	connIdent := c.ConnectorIdentity()
	configRev := ComputeConfigRevision(connIdent, eff.PlaneOrigin, eff.WorkspaceSlug, fp.WorkspaceUUID, fp.ProjectUUID)
	if configRev != pinnedConfigRev {
		return ProjectBindingConfig{}, &Error{
			Code:    "CONFIG_MISMATCH",
			Message: "Plane effective routing configuration revision differs from pinned binding",
		}
	}
	return ProjectBindingConfig{
		PlaneOrigin:       eff.PlaneOrigin,
		WorkspaceUUID:     fp.WorkspaceUUID,
		WorkspaceSlug:     eff.WorkspaceSlug,
		ProjectUUID:       fp.ProjectUUID,
		ProjectIdentifier: fp.Identifier,
		PlaneInstance:     planeInstance,
		ConnectorIdentity: connIdent,
		ConfigRevision:    configRev,
	}, nil
}

type rawPlaneIssue struct {
	ID          string          `json:"id"`
	Name        string          `json:"name"`
	Priority    string          `json:"priority"`
	StateID     string          `json:"state_id"`
	State       json.RawMessage `json:"state"`
	StateDetail *struct {
		ID    string `json:"id"`
		Name  string `json:"name"`
		Group string `json:"group"`
	} `json:"state_detail"`
	UpdatedAt       string          `json:"updated_at"`
	SequenceID      *int            `json:"sequence_id"`
	Assignees       json.RawMessage `json:"assignees"`
	AssigneeDetails []struct {
		ID          string `json:"id"`
		DisplayName string `json:"display_name"`
	} `json:"assignee_details"`
	ProjectDetail *struct {
		ID         string `json:"id"`
		Identifier string `json:"identifier"`
	} `json:"project_detail"`
}

func (c *Connector) RefreshIssues(ctx context.Context, instanceName string, projectUUID uuid.UUID, baseOrigin string, workspaceSlug string, projectIdent string) ([]NormalizedItem, error) {
	normOrigin, err := NormalizeOrigin(baseOrigin)
	if err != nil {
		return nil, err
	}
	wsSlug := strings.TrimSpace(workspaceSlug)
	if wsSlug == "" {
		return nil, &Error{Code: "INVALID_REQUEST", Message: "workspace slug is required for Plane issue refresh"}
	}
	pIdent := strings.TrimSpace(projectIdent)
	if pIdent == "" {
		return nil, &Error{Code: "INVALID_REQUEST", Message: "project identifier is required for Plane issue refresh"}
	}

	eff := EffectiveConfig{
		ConfigPath:    c.resolveConfigPath(""),
		Instance:      strings.TrimSpace(instanceName),
		PlaneOrigin:   normOrigin,
		WorkspaceSlug: wsSlug,
		Project:       projectUUID.String(),
	}
	args := pinnedArgs(eff, projectUUID,
		"issue", "list",
		"--limit", "10001",
		"--expand", "state,assignees",
	)

	raw, err := c.run(ctx, args)
	if err != nil {
		return nil, err
	}

	var envelope struct {
		OK   bool            `json:"ok"`
		Data json.RawMessage `json:"data"`
		Meta *struct {
			Pagination *struct {
				Drained    bool `json:"drained"`
				Fetched    int  `json:"fetched"`
				TotalCount int  `json:"total_count"`
			} `json:"pagination"`
		} `json:"meta"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		return nil, &Error{Code: "INVALID_REQUEST", Message: "failed to parse Plane CLI output: " + err.Error()}
	}
	if !envelope.OK {
		return nil, &Error{Code: "INVALID_REQUEST", Message: "Plane CLI returned non-OK envelope"}
	}
	if envelope.Meta == nil || envelope.Meta.Pagination == nil {
		return nil, &Error{Code: "INVALID_REQUEST", Message: "Plane issue list omitted required pagination metadata"}
	}
	pg := envelope.Meta.Pagination
	if pg.TotalCount > MaxItemLimit || pg.Fetched > MaxItemLimit {
		return nil, ErrTooLarge
	}

	trimmedData := bytes.TrimSpace(envelope.Data)
	if len(trimmedData) == 0 {
		return nil, &Error{Code: "INVALID_REQUEST", Message: "Plane issue list omitted data field"}
	}

	var rawIssues []rawPlaneIssue
	if bytes.Equal(trimmedData, []byte("null")) {
		if !pg.Drained || pg.Fetched != 0 || pg.TotalCount != 0 {
			return nil, &Error{Code: "INVALID_REQUEST", Message: "Plane issue list returned null data with non-empty pagination metadata"}
		}
		rawIssues = []rawPlaneIssue{}
	} else {
		if trimmedData[0] != '[' {
			return nil, &Error{Code: "INVALID_REQUEST", Message: "Plane issue list data must be a JSON array"}
		}
		if err := json.Unmarshal(trimmedData, &rawIssues); err != nil {
			return nil, &Error{Code: "INVALID_REQUEST", Message: "malformed Plane issue list data: " + err.Error()}
		}
	}

	if len(rawIssues) > MaxItemLimit {
		return nil, ErrTooLarge
	}
	if !pg.Drained {
		return nil, &Error{Code: "INVALID_REQUEST", Message: "incomplete Plane issue list acquisition: pagination not drained"}
	}
	if pg.Fetched != len(rawIssues) {
		return nil, &Error{
			Code:    "INVALID_REQUEST",
			Message: fmt.Sprintf("Plane pagination fetched count (%d) does not match returned items (%d)", pg.Fetched, len(rawIssues)),
		}
	}
	if pg.TotalCount > len(rawIssues) {
		return nil, &Error{
			Code:    "INVALID_REQUEST",
			Message: fmt.Sprintf("Plane pagination total_count (%d) exceeds returned items (%d)", pg.TotalCount, len(rawIssues)),
		}
	}

	items := make([]NormalizedItem, 0, len(rawIssues))
	seenIDs := make(map[uuid.UUID]bool, len(rawIssues))

	for i, rawIssue := range rawIssues {
		itemUUID, err := parseCanonicalUUID(rawIssue.ID)
		if err != nil {
			return nil, &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("item %d has invalid UUID %q: %v", i, rawIssue.ID, err)}
		}
		if seenIDs[itemUUID] {
			return nil, &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("duplicate item UUID %s in Plane response", itemUUID)}
		}
		seenIDs[itemUUID] = true

		if rawIssue.SequenceID == nil || *rawIssue.SequenceID < 0 {
			return nil, &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("item %s omitted valid sequence_id", itemUUID)}
		}
		itemIdentPrefix := pIdent
		if rawIssue.ProjectDetail != nil && strings.TrimSpace(rawIssue.ProjectDetail.Identifier) != "" {
			itemIdentPrefix = strings.TrimSpace(rawIssue.ProjectDetail.Identifier)
		}
		ident := fmt.Sprintf("%s-%d", itemIdentPrefix, *rawIssue.SequenceID)

		stateUUID, stateName, stateGroup, err := normalizeIssueState(rawIssue)
		if err != nil {
			return nil, err
		}

		assigneeIDs, assigneeNames, err := normalizeIssueAssignees(rawIssue)
		if err != nil {
			return nil, err
		}

		var updatedAt *time.Time
		if strings.TrimSpace(rawIssue.UpdatedAt) != "" {
			t, err := time.Parse(time.RFC3339Nano, rawIssue.UpdatedAt)
			if err != nil {
				t, err = time.Parse(time.RFC3339, rawIssue.UpdatedAt)
				if err != nil {
					return nil, &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("item %s has invalid updated_at %q", itemUUID, rawIssue.UpdatedAt)}
				}
			}
			updatedAt = &t
		}

		sourceURL := fmt.Sprintf("%s/%s/projects/%s/issues/%s",
			strings.TrimRight(normOrigin, "/"),
			url.PathEscape(wsSlug),
			projectUUID.String(),
			itemUUID.String(),
		)

		items = append(items, NormalizedItem{
			PlaneItemUUID:   itemUUID,
			Identifier:      ident,
			Title:           rawIssue.Name,
			StateID:         stateUUID,
			StateName:       stateName,
			StateGroup:      stateGroup,
			AssigneeIDs:     assigneeIDs,
			AssigneeNames:   assigneeNames,
			UpdatedAtSource: updatedAt,
			SourceURL:       sourceURL,
		})
	}

	return items, nil
}

func normalizeIssueState(rawIssue rawPlaneIssue) (*uuid.UUID, string, string, error) {
	if rawIssue.StateDetail != nil && rawIssue.StateDetail.ID != "" {
		su, err := parseCanonicalUUID(rawIssue.StateDetail.ID)
		if err != nil {
			return nil, "", "", &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("invalid state_detail UUID %q", rawIssue.StateDetail.ID)}
		}
		return &su, rawIssue.StateDetail.Name, rawIssue.StateDetail.Group, nil
	}
	trimmed := bytes.TrimSpace(rawIssue.State)
	if len(trimmed) > 0 && !bytes.Equal(trimmed, []byte("null")) {
		switch trimmed[0] {
		case '{':
			var st struct {
				ID    string `json:"id"`
				Name  string `json:"name"`
				Group string `json:"group"`
			}
			if err := json.Unmarshal(trimmed, &st); err != nil {
				return nil, "", "", &Error{Code: "INVALID_REQUEST", Message: "malformed expanded state object"}
			}
			var suPtr *uuid.UUID
			if st.ID != "" {
				su, err := parseCanonicalUUID(st.ID)
				if err != nil {
					return nil, "", "", &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("invalid state UUID %q", st.ID)}
				}
				suPtr = &su
			}
			return suPtr, st.Name, st.Group, nil
		case '"':
			var s string
			if err := json.Unmarshal(trimmed, &s); err != nil {
				return nil, "", "", &Error{Code: "INVALID_REQUEST", Message: "malformed state UUID string"}
			}
			if s != "" {
				su, err := parseCanonicalUUID(s)
				if err != nil {
					return nil, "", "", &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("invalid state UUID %q", s)}
				}
				return &su, "", "", nil
			}
		default:
			return nil, "", "", &Error{Code: "INVALID_REQUEST", Message: "unexpected JSON value for state"}
		}
	}
	if strings.TrimSpace(rawIssue.StateID) != "" {
		su, err := parseCanonicalUUID(strings.TrimSpace(rawIssue.StateID))
		if err != nil {
			return nil, "", "", &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("invalid state_id UUID %q", rawIssue.StateID)}
		}
		return &su, "", "", nil
	}
	return nil, "", "", nil
}

func normalizeIssueAssignees(rawIssue rawPlaneIssue) ([]string, []string, error) {
	ids := make([]string, 0)
	names := make([]string, 0)
	trimmed := bytes.TrimSpace(rawIssue.Assignees)
	if len(trimmed) > 0 && !bytes.Equal(trimmed, []byte("null")) {
		var elems []json.RawMessage
		if err := json.Unmarshal(trimmed, &elems); err != nil {
			return nil, nil, &Error{Code: "INVALID_REQUEST", Message: "malformed assignees array"}
		}
		for _, elem := range elems {
			et := bytes.TrimSpace(elem)
			if len(et) == 0 || bytes.Equal(et, []byte("null")) {
				continue
			}
			switch et[0] {
			case '{':
				var obj struct {
					ID          string `json:"id"`
					DisplayName string `json:"display_name"`
					FirstName   string `json:"first_name"`
					LastName    string `json:"last_name"`
				}
				if err := json.Unmarshal(et, &obj); err != nil {
					return nil, nil, &Error{Code: "INVALID_REQUEST", Message: "malformed expanded assignee object"}
				}
				u, err := parseCanonicalUUID(obj.ID)
				if err != nil {
					return nil, nil, &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("invalid assignee UUID %q", obj.ID)}
				}
				ids = append(ids, u.String())
				dn := strings.TrimSpace(obj.DisplayName)
				if dn == "" {
					dn = strings.TrimSpace(obj.FirstName + " " + obj.LastName)
				}
				if dn != "" {
					names = append(names, dn)
				}
			case '"':
				var s string
				if err := json.Unmarshal(et, &s); err != nil {
					return nil, nil, &Error{Code: "INVALID_REQUEST", Message: "malformed assignee UUID string"}
				}
				u, err := parseCanonicalUUID(s)
				if err != nil {
					return nil, nil, &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("invalid assignee UUID %q", s)}
				}
				ids = append(ids, u.String())
			default:
				return nil, nil, &Error{Code: "INVALID_REQUEST", Message: "unexpected assignee JSON element"}
			}
		}
	}
	if len(names) == 0 && len(rawIssue.AssigneeDetails) > 0 {
		for _, ad := range rawIssue.AssigneeDetails {
			u, err := parseCanonicalUUID(ad.ID)
			if err != nil {
				return nil, nil, &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("invalid assignee_details UUID %q", ad.ID)}
			}
			if len(ids) == 0 {
				ids = append(ids, u.String())
			}
			if strings.TrimSpace(ad.DisplayName) != "" {
				names = append(names, strings.TrimSpace(ad.DisplayName))
			}
		}
	}
	return ids, names, nil
}
