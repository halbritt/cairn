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
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/google/uuid"
)

const (
	MaxCLIOutputBytes = 16 * 1024 * 1024 // 16 MiB
	DefaultTimeout    = 30 * time.Second
	MaxItemLimit      = 10000
)

type Error struct {
	Code    string
	Message string
}

func (e *Error) Error() string {
	return e.Message
}

var (
	ErrAuthRejected = &Error{Code: "AUTHORITY_DENIED", Message: "Plane authentication/authorization rejected"}
	ErrTooLarge     = &Error{Code: "PROJECT_TOO_LARGE", Message: "Plane project exceeds maximum of 10,000 items"}
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

type ProjectBindingConfig struct {
	PlaneOrigin       string    `json:"plane_origin"`
	WorkspaceUUID     uuid.UUID `json:"workspace_uuid"`
	ProjectUUID       uuid.UUID `json:"project_uuid"`
	ProjectIdentifier string    `json:"project_identifier"`
	ConfigRevision    string    `json:"config_revision"`
}

type Connector struct {
	BinaryPath string
	ConfigPath string
	Timeout    time.Duration
}

func NewConnector() *Connector {
	bin := os.Getenv("CAIRN_PLANE_BIN")
	if bin == "" {
		bin = "plane"
	}
	cfg := os.Getenv("PLANE_CONFIG")
	if cfg == "" {
		home, _ := os.UserHomeDir()
		if home != "" {
			cfg = filepath.Join(home, ".config", "plane-cli", "config.toml")
		}
	}
	return &Connector{
		BinaryPath: bin,
		ConfigPath: cfg,
		Timeout:    DefaultTimeout,
	}
}

// Disallow mutating commands strictly.
var allowedSubcommands = map[string]map[string]bool{
	"project":   {"get": true, "list": true, "summary": true},
	"state":     {"list": true, "get": true},
	"issue":     {"list": true, "get": true, "search": true},
	"workspace": {"get": true, "list": true, "me": true},
	"me":        {"": true},
	"version":   {"": true},
}

func (c *Connector) validateReadCommand(args []string) error {
	var cmd, subcmd string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if strings.HasPrefix(arg, "-") {
			if arg == "-p" || arg == "--project" || arg == "-i" || arg == "--instance" ||
				arg == "-w" || arg == "--workspace" || arg == "--config" || arg == "--fields" ||
				arg == "--limit" || arg == "--order-by" || arg == "--timeout" || arg == "--expand" {
				i++
			}
			continue
		}
		if cmd == "" {
			cmd = arg
		} else if subcmd == "" {
			subcmd = arg
			break
		}
	}

	allowedSubs, ok := allowedSubcommands[cmd]
	if !ok {
		return &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("disallowed Plane command: %s", cmd)}
	}
	if allowedSubs[""] {
		return nil
	}
	if !allowedSubs[subcmd] {
		return &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("disallowed Plane subcommand: %s %s", cmd, subcmd)}
	}
	return nil
}

// boundedBuffer captures up to limit bytes, erroring and killing process if exceeded.
type boundedReader struct {
	r     io.Reader
	limit int64
	read  int64
	kill  func()
}

func (b *boundedReader) Read(p []byte) (int, error) {
	n, err := b.r.Read(p)
	if n > 0 {
		b.read += int64(n)
		if b.read > b.limit {
			if b.kill != nil {
				b.kill()
			}
			return n, ErrTooLarge
		}
	}
	return n, err
}

func (c *Connector) run(ctx context.Context, args []string) ([]byte, error) {
	if err := c.validateReadCommand(args); err != nil {
		return nil, err
	}

	timeout := c.Timeout
	if timeout <= 0 {
		timeout = DefaultTimeout
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, c.BinaryPath, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	// Scrub conflicting environment variables to ensure exact effective config
	env := make([]string, 0, len(os.Environ())+1)
	for _, e := range os.Environ() {
		if strings.HasPrefix(e, "PLANE_BASE_URL=") ||
			strings.HasPrefix(e, "PLANE_WORKSPACE=") ||
			strings.HasPrefix(e, "PLANE_PROJECT=") ||
			strings.HasPrefix(e, "PLANE_INSTANCE=") ||
			strings.HasPrefix(e, "PLANE_CONFIG=") {
			continue
		}
		env = append(env, e)
	}
	if c.ConfigPath != "" {
		env = append(env, "PLANE_CONFIG="+c.ConfigPath)
	}
	cmd.Env = env

	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		return nil, &Error{Code: "STORE_ERROR", Message: "failed to open stdout pipe: " + err.Error()}
	}
	stderrPipe, err := cmd.StderrPipe()
	if err != nil {
		return nil, &Error{Code: "STORE_ERROR", Message: "failed to open stderr pipe: " + err.Error()}
	}

	if err := cmd.Start(); err != nil {
		return nil, &Error{Code: "STORE_ERROR", Message: "failed to start plane CLI: " + err.Error()}
	}

	killFunc := func() {
		if cmd.Process != nil {
			_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
			_ = cmd.Process.Kill()
		}
	}

	var stdoutBuf, stderrBuf bytes.Buffer
	boundedStdout := &boundedReader{r: stdoutPipe, limit: MaxCLIOutputBytes, kill: killFunc}
	boundedStderr := &boundedReader{r: stderrPipe, limit: 1024 * 1024, kill: killFunc}

	var wg sync.WaitGroup
	var stdoutErr error

	wg.Add(2)
	go func() {
		defer wg.Done()
		_, stdoutErr = io.Copy(&stdoutBuf, boundedStdout)
	}()
	go func() {
		defer wg.Done()
		_, _ = io.Copy(&stderrBuf, boundedStderr)
	}()

	waitErr := cmd.Wait()
	wg.Wait()

	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		killFunc()
		return nil, &Error{Code: "MODEL_TIMEOUT", Message: "Plane CLI execution timed out"}
	}
	if errors.Is(stdoutErr, ErrTooLarge) || stdoutBuf.Len() > MaxCLIOutputBytes {
		return nil, ErrTooLarge
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

	// Parse JSON envelope
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
		if jsonErr := json.Unmarshal(raw, &envelope); jsonErr == nil {
			if !envelope.OK && envelope.Error != nil {
				if envelope.Error.HTTPStatus == 401 || envelope.Error.HTTPStatus == 403 || envelope.Error.Code == "AUTH_ERROR" {
					return nil, ErrAuthRejected
				}
				return nil, &Error{Code: "INVALID_REQUEST", Message: "Plane API error: " + envelope.Error.Message}
			}
		}
	}

	if exitCode != 0 {
		return nil, &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("Plane CLI exited with code %d: %s", exitCode, strings.TrimSpace(stderrBuf.String()))}
	}

	return raw, nil
}

func (c *Connector) RunReadCommand(ctx context.Context, args []string) ([]byte, error) {
	return c.run(ctx, args)
}

func (c *Connector) RefreshIssues(ctx context.Context, instanceName string, projectUUID uuid.UUID, baseOrigin string, workspaceSlug string, projectIdent string) ([]NormalizedItem, error) {
	// Request 10001 items to bound acquisition while detecting >10,000 items (PLANE-4)
	args := []string{
		"issue", "list",
		"--instance", instanceName,
		"-p", projectUUID.String(),
		"--no-resolve",
		"--limit", "10001",
		"--expand", "state,assignees",
	}

	raw, err := c.run(ctx, args)
	if err != nil {
		return nil, err
	}

	var envelope struct {
		OK   bool `json:"ok"`
		Data []struct {
			ID          string `json:"id"`
			Name        string `json:"name"`
			Priority    string `json:"priority"`
			StateID     string `json:"state_id"`
			UpdatedAt   string `json:"updated_at"`
			SequenceID  *int   `json:"sequence_id"`
			StateDetail *struct {
				ID    string `json:"id"`
				Name  string `json:"name"`
				Group string `json:"group"`
			} `json:"state_detail"`
			StateObj *struct {
				ID    string `json:"id"`
				Name  string `json:"name"`
				Group string `json:"group"`
			} `json:"state"`
			Assignees       json.RawMessage `json:"assignees"`
			AssigneeDetails []struct {
				ID          string `json:"id"`
				DisplayName string `json:"display_name"`
			} `json:"assignee_details"`
			ProjectDetail *struct {
				ID         string `json:"id"`
				Identifier string `json:"identifier"`
			} `json:"project_detail"`
		} `json:"data"`
		Meta *struct {
			Pagination *struct {
				Drained    bool `json:"drained"`
				Fetched    int  `json:"fetched"`
				TotalCount int  `json:"total_count"`
			} `json:"pagination"`
		} `json:"meta"`
	}

	if err := json.Unmarshal(raw, &envelope); err != nil {
		return nil, &Error{Code: "STORE_ERROR", Message: "failed to parse Plane CLI output: " + err.Error()}
	}

	if !envelope.OK || envelope.Data == nil {
		return nil, &Error{Code: "INVALID_REQUEST", Message: "Plane CLI returned non-OK or empty envelope"}
	}

	if len(envelope.Data) > MaxItemLimit {
		return nil, ErrTooLarge
	}

	// Verify pagination completeness if pagination metadata is present (PLANE-4)
	if envelope.Meta != nil && envelope.Meta.Pagination != nil {
		if envelope.Meta.Pagination.TotalCount > MaxItemLimit {
			return nil, ErrTooLarge
		}
		if !envelope.Meta.Pagination.Drained && envelope.Meta.Pagination.TotalCount > len(envelope.Data) {
			return nil, &Error{Code: "INVALID_REQUEST", Message: "incomplete Plane issue list acquisition; pagination not drained"}
		}
	}

	items := make([]NormalizedItem, 0, len(envelope.Data))
	seenIDs := make(map[uuid.UUID]bool, len(envelope.Data))

	for i, rawIssue := range envelope.Data {
		// Strict hexadecimal RFC 4122 UUID validation (PLANE-4)
		itemUUID, err := uuid.Parse(rawIssue.ID)
		if err != nil {
			return nil, &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("item %d has invalid UUID %q: %v", i, rawIssue.ID, err)}
		}
		if seenIDs[itemUUID] {
			return nil, &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("duplicate item UUID %s in Plane response", itemUUID)}
		}
		seenIDs[itemUUID] = true

		ident := rawIssue.Name
		pIdent := projectIdent
		if rawIssue.ProjectDetail != nil && rawIssue.ProjectDetail.Identifier != "" {
			pIdent = rawIssue.ProjectDetail.Identifier
		}
		if pIdent != "" && rawIssue.SequenceID != nil {
			ident = fmt.Sprintf("%s-%d", pIdent, *rawIssue.SequenceID)
		} else if rawIssue.SequenceID != nil {
			ident = fmt.Sprintf("ISSUE-%d", *rawIssue.SequenceID)
		}

		var stateUUID *uuid.UUID
		stateName := ""
		stateGroup := ""

		if rawIssue.StateDetail != nil {
			if su, err := uuid.Parse(rawIssue.StateDetail.ID); err == nil {
				stateUUID = &su
			}
			stateName = rawIssue.StateDetail.Name
			stateGroup = rawIssue.StateDetail.Group
		} else if rawIssue.StateObj != nil {
			if su, err := uuid.Parse(rawIssue.StateObj.ID); err == nil {
				stateUUID = &su
			}
			stateName = rawIssue.StateObj.Name
			stateGroup = rawIssue.StateObj.Group
		} else if rawIssue.StateID != "" {
			if su, err := uuid.Parse(rawIssue.StateID); err == nil {
				stateUUID = &su
			}
		}

		assigneeIDs := make([]string, 0)
		assigneeNames := make([]string, 0)
		if len(rawIssue.AssigneeDetails) > 0 {
			for _, ad := range rawIssue.AssigneeDetails {
				assigneeIDs = append(assigneeIDs, ad.ID)
				assigneeNames = append(assigneeNames, ad.DisplayName)
			}
		} else if len(rawIssue.Assignees) > 0 {
			var strList []string
			if err := json.Unmarshal(rawIssue.Assignees, &strList); err == nil {
				assigneeIDs = append(assigneeIDs, strList...)
			}
		}

		var updatedAt *time.Time
		if rawIssue.UpdatedAt != "" {
			if t, err := time.Parse(time.RFC3339Nano, rawIssue.UpdatedAt); err == nil {
				updatedAt = &t
			} else if t, err := time.Parse(time.RFC3339, rawIssue.UpdatedAt); err == nil {
				updatedAt = &t
			}
		}

		sourceURL := ""
		if baseOrigin != "" && workspaceSlug != "" {
			sourceURL = fmt.Sprintf("%s/%s/projects/%s/issues/%s", strings.TrimRight(baseOrigin, "/"), workspaceSlug, projectUUID.String(), itemUUID.String())
		}

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

func NormalizeOrigin(rawURL string) (string, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return "", &Error{Code: "INVALID_REQUEST", Message: "invalid Plane base URL: " + err.Error()}
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return "", &Error{Code: "INVALID_REQUEST", Message: "plane origin must use http or https scheme"}
	}
	u.Path = ""
	u.RawQuery = ""
	u.Fragment = ""
	return strings.TrimRight(u.String(), "/"), nil
}

func (c *Connector) ResolveBinding(ctx context.Context, instanceName string, workspaceStr string, projectStr string) (ProjectBindingConfig, string, error) {
	// First resolve project and workspace UUIDs via real plane CLI calls (PLANE-1)
	args := []string{"project", "get", projectStr, "--instance", instanceName, "--no-resolve"}
	raw, err := c.run(ctx, args)
	if err != nil {
		// If --no-resolve fails, try without it to resolve by name
		args = []string{"project", "get", projectStr, "--instance", instanceName}
		raw, err = c.run(ctx, args)
		if err != nil {
			return ProjectBindingConfig{}, "", &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("could not resolve project %q in instance %q: %v", projectStr, instanceName, err)}
		}
	}

	var projEnv struct {
		OK   bool `json:"ok"`
		Data struct {
			ID         string `json:"id"`
			Workspace  string `json:"workspace"`
			Identifier string `json:"identifier"`
		} `json:"data"`
	}
	if err := json.Unmarshal(raw, &projEnv); err != nil || !projEnv.OK || projEnv.Data.ID == "" {
		return ProjectBindingConfig{}, "", &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("invalid project response for %q: %v", projectStr, err)}
	}

	projUUID, err := uuid.Parse(projEnv.Data.ID)
	if err != nil {
		return ProjectBindingConfig{}, "", &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("project has non-UUID ID %q: %v", projEnv.Data.ID, err)}
	}

	wsUUID, err := uuid.Parse(projEnv.Data.Workspace)
	if err != nil {
		// If workspace returned was not UUID, try parsing workspaceStr
		if w, err2 := uuid.Parse(workspaceStr); err2 == nil {
			wsUUID = w
		} else {
			return ProjectBindingConfig{}, "", &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("workspace is not a valid UUID: %q", projEnv.Data.Workspace)}
		}
	}

	cfgContent, err := os.ReadFile(c.ConfigPath)
	if err != nil {
		return ProjectBindingConfig{}, "", &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("cannot read plane-cli config at %s: %v", c.ConfigPath, err)}
	}

	// Parse instance config
	lines := strings.Split(string(cfgContent), "\n")
	inTargetInstance := false
	baseURL := ""
	workspaceSlug := ""
	apiKeyFile := ""

	targetHeader := fmt.Sprintf("[instance.%s]", instanceName)
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			inTargetInstance = (trimmed == targetHeader)
			continue
		}
		if inTargetInstance && strings.Contains(trimmed, "=") {
			parts := strings.SplitN(trimmed, "=", 2)
			k := strings.TrimSpace(parts[0])
			v := strings.Trim(strings.TrimSpace(parts[1]), `"'`)
			switch k {
			case "base_url":
				baseURL = v
			case "workspace":
				workspaceSlug = v
			case "api_key_file":
				apiKeyFile = v
			}
		}
	}

	if baseURL == "" {
		return ProjectBindingConfig{}, "", &Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("instance %q not found in %s", instanceName, c.ConfigPath)}
	}

	normOrigin, err := NormalizeOrigin(baseURL)
	if err != nil {
		return ProjectBindingConfig{}, "", err
	}

	var apiKeyHash string
	if apiKeyFile != "" {
		if keyBytes, err := os.ReadFile(apiKeyFile); err == nil {
			h := sha256.Sum256(keyBytes)
			apiKeyHash = hex.EncodeToString(h[:])
		}
	}

	revInput := fmt.Sprintf("%s|%s|%s|%s", normOrigin, workspaceSlug, apiKeyFile, apiKeyHash)
	revSum := sha256.Sum256([]byte(revInput))
	configRev := hex.EncodeToString(revSum[:16])

	return ProjectBindingConfig{
		PlaneOrigin:       normOrigin,
		WorkspaceUUID:     wsUUID,
		ProjectUUID:       projUUID,
		ProjectIdentifier: projEnv.Data.Identifier,
		ConfigRevision:    configRev,
	}, workspaceSlug, nil
}
