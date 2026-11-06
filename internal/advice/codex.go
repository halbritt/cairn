package advice

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
)

const (
	CodexAdviceCapabilitiesSchema = "cairn.codex-advice-capabilities/1"
	CodexAdviceRequestSchema      = "cairn.codex-advice-request/1"
	CodexAdviceResultSchema       = "cairn.codex-advice-result/1"

	DefaultCodexTimeoutMs = 90000
	MinCodexTimeoutMs     = 10000
	MaxCodexTimeoutMs     = 180000

	DefaultCodexMaxBytes = 65536
	MinCodexMaxBytes     = 1024
	MaxCodexMaxBytes     = 262144

	MaxCodexStdinBytes = 262144

	RequiredCodexUpstreamCommit = "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856"
)

var (
	hex40Regex = regexp.MustCompile("^[0-9a-f]{40}$")
	hex64Regex = regexp.MustCompile("^[0-9a-f]{64}$")
)

// CodexAdviceRequest is the stdin envelope sent to `codex cairn-advice`.
type CodexAdviceRequest struct {
	Schema    string `json:"schema"`
	RequestID string `json:"request_id"`
	Prompt    string `json:"prompt"`
}

// CodexAdviceError describes an error returned in a CodexAdviceResult.
type CodexAdviceError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// CodexAdviceResult is the terminal stdout envelope from `codex cairn-advice`.
type CodexAdviceResult struct {
	Schema     string            `json:"schema"`
	RequestID  *string           `json:"request_id"`
	OK         bool              `json:"ok"`
	Model      string            `json:"model,omitempty"`
	Provider   string            `json:"provider,omitempty"`
	AuthMethod string            `json:"auth_method,omitempty"`
	Result     string            `json:"result,omitempty"`
	Error      *CodexAdviceError `json:"error,omitempty"`
}

// CodexSubscriptionAdapter executes model advice generation through a narrow
// native `codex cairn-advice` command using the pinned Codex ChatGPT subscription route.
type CodexSubscriptionAdapter struct {
	CLIPath                string
	BinarySHA256           string // 64-char lowercase hex
	Model                  string // fixed full model name
	CodexHome              string // existing directory
	ExpectedUpstreamCommit string // "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856"
	ExpectedPatchSHA256    string // 64-char lowercase hex, NEVER "unverified" or empty
	TimeoutMs              int64
	MaxOutputBytes         int64
}

// NewCodexSubscriptionAdapter creates an adapter with standard defaults.
func NewCodexSubscriptionAdapter(cliPath, binarySHA256, model, codexHome, expectedPatchSHA256 string) *CodexSubscriptionAdapter {
	return &CodexSubscriptionAdapter{
		CLIPath:                cliPath,
		BinarySHA256:           binarySHA256,
		Model:                  model,
		CodexHome:              codexHome,
		ExpectedUpstreamCommit: RequiredCodexUpstreamCommit,
		ExpectedPatchSHA256:    expectedPatchSHA256,
		TimeoutMs:              DefaultCodexTimeoutMs,
		MaxOutputBytes:         DefaultCodexMaxBytes,
	}
}

func (a *CodexSubscriptionAdapter) Name() string {
	return RouteCodexSubscription
}

// VerifyCapabilities executes `codex cairn-advice --capabilities` and validates executable and build provenance.
func (a *CodexSubscriptionAdapter) VerifyCapabilities(ctx context.Context) (*CodexCapabilities, error) {
	if strings.TrimSpace(a.CLIPath) == "" {
		return nil, NewAdviceError(ErrSubscriptionRequired, "codex CLI executable path is empty", nil)
	}
	if strings.TrimSpace(a.Model) == "" {
		return nil, NewAdviceError(ErrSubscriptionRequired, "model parameter cannot be empty for codex advice", nil)
	}
	if strings.TrimSpace(a.CodexHome) == "" {
		return nil, NewAdviceError(ErrSubscriptionRequired, "CODEX_HOME is required", nil)
	}
	stat, err := os.Stat(a.CodexHome)
	if err != nil || !stat.IsDir() {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("CODEX_HOME directory %q does not exist or is not a directory", a.CodexHome), err)
	}

	expectedBinarySHA := strings.ToLower(strings.TrimSpace(a.BinarySHA256))
	if !hex64Regex.MatchString(expectedBinarySHA) {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("invalid binary_sha256 %q: must be 64 lowercase hex characters", a.BinarySHA256), nil)
	}
	f, err := os.Open(a.CLIPath)
	if err != nil {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("failed to open codex binary %s: %v", a.CLIPath, err), err)
	}
	defer f.Close()
	hasher := sha256.New()
	if _, err := io.Copy(hasher, f); err != nil {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("failed to hash codex binary: %v", err), err)
	}
	actualSHA := hex.EncodeToString(hasher.Sum(nil))
	if actualSHA != expectedBinarySHA {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("codex binary SHA-256 mismatch: expected %s, got %s", expectedBinarySHA, actualSHA), nil)
	}

	if a.ExpectedUpstreamCommit != RequiredCodexUpstreamCommit {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("codex upstream_commit mismatch: expected %s, got %s", RequiredCodexUpstreamCommit, a.ExpectedUpstreamCommit), nil)
	}

	expectedPatch := strings.ToLower(strings.TrimSpace(a.ExpectedPatchSHA256))
	if expectedPatch == "" || expectedPatch == "unverified" || !hex64Regex.MatchString(expectedPatch) {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("invalid expected patch_sha256 %q: must be 64 lowercase hex characters", a.ExpectedPatchSHA256), nil)
	}

	extraEnv := map[string]string{"CODEX_HOME": a.CodexHome}
	env := BuildAllowlistedEnv(extraEnv)
	args := []string{"cairn-advice", "--capabilities"}
	res, err := RunIsolatedProcess(ctx, a.CLIPath, args, nil, env, "", nil)
	if err != nil {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("failed to run codex capabilities probe: %v", err), err)
	}
	if res.ExitCode != 0 {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("codex capabilities probe exited with code %d", res.ExitCode), nil)
	}

	dec := json.NewDecoder(bytes.NewReader(res.Stdout))
	dec.DisallowUnknownFields()
	var caps CodexCapabilities
	if err := dec.Decode(&caps); err != nil {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("failed to decode codex capabilities: %v", err), err)
	}
	var trailing json.RawMessage
	if err := dec.Decode(&trailing); !errors.Is(err, io.EOF) {
		return nil, NewAdviceError(ErrSubscriptionRequired, "extra trailing content after capabilities object", nil)
	}

	if caps.Schema != CodexAdviceCapabilitiesSchema {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("unexpected capabilities schema %q, expected %q", caps.Schema, CodexAdviceCapabilitiesSchema), nil)
	}
	if caps.RequestSchema != CodexAdviceRequestSchema {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("unexpected request schema %q, expected %q", caps.RequestSchema, CodexAdviceRequestSchema), nil)
	}
	if caps.ResultSchema != CodexAdviceResultSchema {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("unexpected result schema %q, expected %q", caps.ResultSchema, CodexAdviceResultSchema), nil)
	}
	if caps.Provider != "openai" {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("unexpected provider %q, expected 'openai'", caps.Provider), nil)
	}
	if caps.AuthMethod != "chatgpt_subscription" {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("unexpected auth_method %q, expected 'chatgpt_subscription'", caps.AuthMethod), nil)
	}
	if !caps.OneRequest || caps.Tools || caps.Retries {
		return nil, NewAdviceError(ErrSubscriptionRequired, "invalid capabilities flags: one_request must be true, tools and retries must be false", nil)
	}
	if caps.UpstreamCommit != RequiredCodexUpstreamCommit {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("upstream_commit mismatch: expected %q, got %q", RequiredCodexUpstreamCommit, caps.UpstreamCommit), nil)
	}
	if caps.PatchSHA256 == "unverified" || !hex64Regex.MatchString(caps.PatchSHA256) {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("refusing unverified or invalid patch_sha256 %q", caps.PatchSHA256), nil)
	}
	if caps.PatchSHA256 != expectedPatch {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("patch_sha256 mismatch: manifest expected %q, binary reported %q", expectedPatch, caps.PatchSHA256), nil)
	}

	return &caps, nil
}

// BuildAdvicePrompt constructs the prompt text wrapping the canonical Snapshot JSON
// with explicit bounded instructions and strict JSON schema rules.
func BuildAdvicePrompt(snapshot *Snapshot) (string, error) {
	if snapshot == nil {
		return "", NewAdviceError(ErrInvalidProposal, "nil snapshot", nil)
	}
	snapJSON, err := json.Marshal(snapshot)
	if err != nil {
		return "", NewAdviceError(ErrInvalidProposal, "failed to marshal snapshot", err)
	}

	prompt := fmt.Sprintf(`You are a work coordination advisor. Analyze the following work item snapshot and return coordination actions strictly conforming to the JSON schema.

Input Snapshot:
%s

Instructions:
1. Output ONLY a single JSON object. Do not include markdown code fences, preambles, or trailing commentary.
2. The JSON object must match this schema exactly:
{
  "summary": "Concise summary (max 2048 bytes)",
  "actions": [
    // Each action is a FLAT object. Do NOT nest inside "progress", "blocker", or "artifact" objects.
    // Action type 1: annotate_progress
    {
      "action_type": "annotate_progress",
      "rationale": "Short rationale (max 1024 bytes)",
      "text": "Progress update text (max 2048 bytes)",
      "source_references": ["<source_id_from_snapshot>"]
    },
    // Action type 2: annotate_blocker (create new blocker)
    {
      "action_type": "annotate_blocker",
      "rationale": "Short rationale (max 1024 bytes)",
      "blocker_action": "create",
      "text": "Blocker description (max 2048 bytes)",
      "source_references": ["<source_id_from_snapshot>"]
    },
    // Action type 3: annotate_blocker (resolve existing blocker)
    {
      "action_type": "annotate_blocker",
      "rationale": "Short rationale (max 1024 bytes)",
      "blocker_action": "resolve",
      "existing_blocker_id": "<existing_blocker_id_from_snapshot>",
      "expected_revision": 1,
      "text": "Optional resolution note (max 2048 bytes)",
      "source_references": ["<source_id_from_snapshot>"]
    },
    // Action type 4: link_artifact
    {
      "action_type": "link_artifact",
      "rationale": "Short rationale (max 1024 bytes)",
      "artifact_revision_id": "<candidate_revision_uuid_from_snapshot>",
      "role": "input" | "output" | "evidence"
    }
  ]
}
3. All action objects are flat: do NOT nest inside "progress", "blocker", or "artifact" objects.
4. You may propose at most 12 actions. Total normalized output must not exceed 16 KiB.
5. Only reference source IDs, existing blocker IDs, and candidate revision UUIDs present in the input snapshot.
6. If no actions are warranted, return {"summary": "...", "actions": []}.
`, string(snapJSON))

	return prompt, nil
}

// GenerateAdvice executes one generation request against `codex cairn-advice` via RunIsolatedProcess.
func (a *CodexSubscriptionAdapter) GenerateAdvice(ctx context.Context, snapshot *Snapshot, deadline time.Duration, onLaunch func(ProcessIdentity) error) (*Proposal, *ProcessResult, error) {
	if snapshot == nil {
		return nil, nil, NewAdviceError(ErrInvalidProposal, "nil snapshot provided", nil)
	}
	if strings.TrimSpace(a.Model) == "" {
		return nil, nil, NewAdviceError(ErrSubscriptionRequired, "model parameter cannot be empty for codex advice", nil)
	}

	// Verify capabilities and binary provenance first
	if _, err := a.VerifyCapabilities(ctx); err != nil {
		return nil, nil, err
	}

	promptStr, err := BuildAdvicePrompt(snapshot)
	if err != nil {
		return nil, nil, err
	}

	requestUUID := uuid.NewString()
	wireReq := CodexAdviceRequest{
		Schema:    CodexAdviceRequestSchema,
		RequestID: requestUUID,
		Prompt:    promptStr,
	}
	stdinBytes, err := json.Marshal(wireReq)
	if err != nil {
		return nil, nil, NewAdviceError(ErrInvalidProposal, "failed to marshal wire request", err)
	}
	if len(stdinBytes) > MaxCodexStdinBytes {
		return nil, nil, NewAdviceError(ErrInputTooLarge, fmt.Sprintf("stdin payload %d bytes exceeds limit %d", len(stdinBytes), MaxCodexStdinBytes), nil)
	}

	timeoutMs := a.TimeoutMs
	if timeoutMs <= 0 {
		timeoutMs = DefaultCodexTimeoutMs
	}
	if timeoutMs < MinCodexTimeoutMs || timeoutMs > MaxCodexTimeoutMs {
		return nil, nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("timeout_ms %d out of bounds [%d, %d]", timeoutMs, MinCodexTimeoutMs, MaxCodexTimeoutMs), nil)
	}
	if deadline > 0 && int64(deadline/time.Millisecond) < timeoutMs {
		timeoutMs = int64(deadline / time.Millisecond)
		if timeoutMs < MinCodexTimeoutMs {
			timeoutMs = MinCodexTimeoutMs
		}
	}

	maxBytes := a.MaxOutputBytes
	if maxBytes <= 0 {
		maxBytes = DefaultCodexMaxBytes
	}
	if maxBytes < MinCodexMaxBytes || maxBytes > MaxCodexMaxBytes {
		return nil, nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("max_output_bytes %d out of bounds [%d, %d]", maxBytes, MinCodexMaxBytes, MaxCodexMaxBytes), nil)
	}

	args := []string{
		"cairn-advice",
		"--model", a.Model,
		"--timeout-ms", strconv.FormatInt(timeoutMs, 10),
		"--max-output-bytes", strconv.FormatInt(maxBytes, 10),
	}

	extraEnv := map[string]string{"CODEX_HOME": a.CodexHome}
	runCtx := ctx
	if deadline > 0 {
		var cancel context.CancelFunc
		runCtx, cancel = context.WithTimeout(ctx, deadline)
		defer cancel()
	}
	env := BuildAllowlistedEnv(extraEnv)
	res, err := RunIsolatedProcess(runCtx, a.CLIPath, args, stdinBytes, env, "", onLaunch)
	if err != nil {
		var clErr *CleanupUnconfirmedError
		if errors.As(err, &clErr) {
			return nil, res, err
		}
		if res != nil && len(res.Stdout) > 0 {
			decErr := json.NewDecoder(bytes.NewReader(res.Stdout))
			decErr.DisallowUnknownFields()
			var errResult CodexAdviceResult
			if parseErr := decErr.Decode(&errResult); parseErr == nil && !errResult.OK && errResult.Error != nil {
				return nil, res, classifyCodexError(errResult.Error.Code, errResult.Error.Message)
			}
		}
		return nil, res, err
	}

	dec := json.NewDecoder(bytes.NewReader(res.Stdout))
	dec.DisallowUnknownFields()
	var envelope CodexAdviceResult
	if err := dec.Decode(&envelope); err != nil {
		return nil, res, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("failed to parse codex advice envelope: %v", err), err)
	}
	var trailing json.RawMessage
	if err := dec.Decode(&trailing); !errors.Is(err, io.EOF) {
		return nil, res, NewAdviceError(ErrInvalidProposal, "extra trailing content after codex advice envelope", nil)
	}

	// Terminal envelope schema checks
	if envelope.Schema != CodexAdviceResultSchema {
		return nil, res, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("unexpected schema %q, expected %q", envelope.Schema, CodexAdviceResultSchema), nil)
	}
	if !envelope.OK {
		if envelope.Error != nil {
			return nil, res, classifyCodexError(envelope.Error.Code, envelope.Error.Message)
		}
		return nil, res, NewAdviceError(ErrModelExecutionFailed, "codex advice returned ok:false without error detail", nil)
	}

	if envelope.RequestID == nil || *envelope.RequestID != requestUUID {
		reqVal := "null"
		if envelope.RequestID != nil {
			reqVal = *envelope.RequestID
		}
		return nil, res, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("request_id mismatch: expected %q, got %q", requestUUID, reqVal), nil)
	}

	// Required route lock checks
	if envelope.Provider != "openai" {
		return nil, res, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("unexpected provider %q, expected 'openai'", envelope.Provider), nil)
	}
	if envelope.AuthMethod != "chatgpt_subscription" {
		return nil, res, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("unexpected auth_method %q, expected 'chatgpt_subscription'", envelope.AuthMethod), nil)
	}
	if envelope.Model != a.Model {
		return nil, res, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("unexpected model %q, expected %q", envelope.Model, a.Model), nil)
	}
	if strings.TrimSpace(envelope.Result) == "" {
		return nil, res, NewAdviceError(ErrInvalidProposal, "missing result string in successful codex advice envelope", nil)
	}

	// Parse and validate application proposal independently with existing strict bounded parser
	proposal, err := ParseProposal(envelope.Result, snapshot)
	if err != nil {
		return nil, res, err
	}

	return proposal, res, nil
}

func classifyCodexError(code, msg string) error {
	switch strings.ToUpper(code) {
	case "AUTH_UNAVAILABLE", "AUTH_UNSUPPORTED":
		return NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("%s: %s", code, msg), nil)
	case "POLICY_REFUSED":
		return NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("policy refused: %s", msg), nil)
	case "QUOTA_EXHAUSTED":
		return NewAdviceError(ErrQuotaExhausted, fmt.Sprintf("quota exhausted: %s", msg), nil)
	case "TIMEOUT":
		return NewAdviceError(ErrModelTimeout, fmt.Sprintf("model execution timed out: %s", msg), nil)
	case "OUTPUT_LIMIT":
		return NewAdviceError(ErrOutputLimit, fmt.Sprintf("output limit exceeded: %s", msg), nil)
	case "INVALID_REQUEST":
		return NewAdviceError(ErrInvalidProposal, fmt.Sprintf("invalid request: %s", msg), nil)
	case "CANCELLED":
		return NewAdviceError(ErrCancelled, fmt.Sprintf("operation cancelled: %s", msg), nil)
	case "PROVIDER_ERROR":
		return NewAdviceError(ErrProviderError, fmt.Sprintf("provider error: %s", msg), nil)
	default:
		return NewAdviceError(ErrModelExecutionFailed, fmt.Sprintf("%s: %s", code, msg), nil)
	}
}
