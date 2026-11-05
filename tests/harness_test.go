package tests

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

type Harness struct {
	t          *testing.T
	Binary     string
	DSN        string
	Collection string
	Env        []string
}

func repoRoot(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	// If run from ./tests, parent is repo root
	if filepath.Base(wd) == "tests" {
		return filepath.Dir(wd)
	}
	return wd
}

var (
	candidateOnce   sync.Once
	candidateBinary string
	candidateErr    error
	migrateOnce     sync.Once
	migrateErr      error
)

func buildCandidateBinary(t *testing.T) string {
	t.Helper()
	candidateOnce.Do(func() {
		root := repoRoot(t)
		tmpDir, err := os.MkdirTemp("", "cairn-candidate-bin-*")
		if err != nil {
			candidateErr = err
			return
		}
		target := filepath.Join(tmpDir, "cairn")
		cmd := exec.Command("go", "build", "-o", target, "./cmd/cairn")
		cmd.Dir = root
		if out, err := cmd.CombinedOutput(); err != nil {
			candidateErr = fmt.Errorf("failed to build candidate cairn binary: %w\nOutput: %s", err, string(out))
			return
		}
		candidateBinary = target
	})
	if candidateErr != nil {
		t.Fatalf("candidate binary build failed: %v", candidateErr)
	}
	return candidateBinary
}

func newTestHarness(t *testing.T) *Harness {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("PostgreSQL integration test: run make test-integration")
	}
	bin := buildCandidateBinary(t)

	migrateOnce.Do(func() {
		cmd := exec.Command(bin, "migrate")
		cmd.Env = append(os.Environ(),
			"CAIRN_DATABASE_URL="+dsn,
			"CAIRN_TEST_DATABASE_URL="+dsn,
		)
		if out, err := cmd.CombinedOutput(); err != nil {
			migrateErr = fmt.Errorf("failed to migrate test database: %w\nOutput: %s", err, string(out))
		}
	})
	if migrateErr != nil {
		t.Fatalf("test database migration failed: %v", migrateErr)
	}

	collection := "/collections/e2e-" + uuid.NewString()
	h := &Harness{
		t:          t,
		Binary:     bin,
		DSN:        dsn,
		Collection: collection,
		Env: []string{
			"CAIRN_DATABASE_URL=" + dsn,
			"CAIRN_TEST_DATABASE_URL=" + dsn,
		},
	}
	return h
}

func (h *Harness) SupportsCommand(subcommand string) bool {
	h.t.Helper()
	cmd := exec.Command(h.Binary, "--help")
	out, err := cmd.CombinedOutput()
	if err != nil {
		return false
	}
	return strings.Contains(string(out), subcommand)
}

func (h *Harness) RequireMilestone(milestone, subcommand string) {
	h.t.Helper()
	if !h.SupportsCommand(subcommand) {
		// Only skip if explicitly filtered by developer subset selector
		if subset := os.Getenv("CAIRN_E2E_SUBSET"); subset != "" {
			if !strings.Contains(subset, milestone) {
				h.t.Skipf("Skipping %s: CAIRN_E2E_SUBSET=%q does not include this milestone", milestone, subset)
			}
		}
		// Full acceptance run: MUST FAIL if command is missing
		h.t.Fatalf("RELEASE/ACCEPTANCE FAILURE: Required command %q for milestone %s is missing from candidate binary under test (%s)", subcommand, milestone, h.Binary)
	}
}

func (h *Harness) RunCommand(bin, stdin string, args ...string) (string, string, int, error) {
	h.t.Helper()
	cmd := exec.Command(bin, args...)
	cmd.Env = append(os.Environ(), h.Env...)
	if stdin != "" {
		cmd.Stdin = strings.NewReader(stdin)
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else {
			exitCode = 1
		}
	}
	return stdout.String(), stderr.String(), exitCode, err
}

func (h *Harness) Run(stdin string, args ...string) (string, string, int, error) {
	return h.RunCommand(h.Binary, stdin, args...)
}

func (h *Harness) RunJSON(v any, stdin string, args ...string) (int, error) {
	h.t.Helper()
	stdout, _, exitCode, err := h.Run(stdin, args...)
	if err != nil && exitCode == 0 {
		return exitCode, err
	}
	if v != nil && stdout != "" {
		if jsonErr := json.Unmarshal([]byte(stdout), v); jsonErr != nil {
			return exitCode, jsonErr
		}
	}
	return exitCode, err
}

type CairnEnvelope struct {
	Schema  string `json:"schema"`
	OK      bool   `json:"ok"`
	Status  string `json:"status"`
	Message string `json:"message"`
	Data    any    `json:"data"`
}

func (h *Harness) RunCairn(stdin string, args ...string) (CairnEnvelope, int, string, error) {
	h.t.Helper()
	stdout, stderr, code, err := h.Run(stdin, args...)
	var env CairnEnvelope
	if stdout != "" {
		_ = json.Unmarshal([]byte(stdout), &env)
	}
	return env, code, stderr, err
}

func (h *Harness) Store(channel core.Channel) *core.Store {
	h.t.Helper()
	ctx := context.Background()
	s, err := core.Open(ctx, h.DSN, channel)
	if err != nil {
		h.t.Fatalf("failed to open store: %v", err)
	}
	h.t.Cleanup(s.Close)
	return s
}

func (h *Harness) MockPlane(mode string) string {
	h.t.Helper()
	root := repoRoot(h.t)
	script := filepath.Join(root, "tests", "fixtures", "mock_plane.sh")
	h.Env = append(h.Env, "CAIRN_PLANE_BIN="+script, "MOCK_PLANE_MODE="+mode)
	return script
}

func (h *Harness) MockClaude(mode string) string {
	h.t.Helper()
	root := repoRoot(h.t)
	script := filepath.Join(root, "tests", "fixtures", "mock_claude.sh")
	h.Env = append(h.Env, "CAIRN_CLAUDE_BIN="+script, "MOCK_CLAUDE_MODE="+mode)
	return script
}
