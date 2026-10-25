package siwc

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	// These are local attempt stop/check thresholds. SIWC preview does not
	// accept Responses max_output_tokens, so output usage is checked afterward.
	DirectAttemptMaxWallTime = 180 * time.Second
	DirectAttemptMaxTokens   = int64(125000)
	DirectAttemptMaxOutput   = int64(1200)
	localOnlySentinel        = "amber harbor"
)

var ErrAdmissionUnavailable = errors.New("SIWC account mapping, quota, and direct-route proof are unavailable")
var ErrAttemptAlreadyClaimed = errors.New("direct source-fit attempt was already claimed; retry is refused")

func supportedDirectManifestHash(digest string) bool {
	switch digest {
	case "0a4e44eb95ce2a26a48d9731a87fef1d75bf3b2d9958925f70ffb226a323f5bf", // v1
		"dffecf6c44de456354d0dbe254704717a2ca0231c291b212f775f0fd49d57c85": // v2
		return true
	default:
		return false
	}
}

// DirectAttemptConfig selects exactly one case and arm from a frozen panel.
// The expected digest must be obtained from the frozen manifest before calling
// the runner. The manifest is fixture input only; its provider route is not
// inherited by this SIWC runner.
type DirectAttemptConfig struct {
	ManifestPath string
	ManifestSHA  string
	CaseID       string
	Arm          string
	OutputDir    string
	Model        string
	Effort       string
}

// DirectAttemptReceipt contains no source bodies or credentials. Answer is
// retained for independent review, including when a post-run usage check fails.
type DirectAttemptReceipt struct {
	Schema           string          `json:"schema"`
	Status           string          `json:"status"`
	TaskOutcome      string          `json:"task_outcome"`
	CaseID           string          `json:"case_id"`
	Arm              string          `json:"arm"`
	ManifestSHA256   string          `json:"manifest_sha256"`
	Model            string          `json:"requested_model"`
	ServedModel      string          `json:"served_model,omitempty"`
	ResponseIDs      []string        `json:"response_ids,omitempty"`
	Reason           string          `json:"reason,omitempty"`
	Sources          []SourceReceipt `json:"sources"`
	Usage            *Usage          `json:"usage,omitempty"`
	ElapsedMillis    int64           `json:"elapsed_millis,omitempty"`
	ToolCallsAllowed bool            `json:"tool_calls_allowed"`
	ToolCallsMade    int             `json:"tool_calls_made"`
	Answer           string          `json:"answer,omitempty"`
}

type SourceReceipt struct {
	Label       string `json:"label"`
	Extent      string `json:"extent"`
	Bytes       int    `json:"bytes"`
	SHA256      string `json:"sha256"`
	Disposition string `json:"disposition"`
}

type selectedDirectCase struct {
	ID           string
	Task         string
	SourceLabels []string
	Arms         []string
}

// DirectAttemptRunner has no production admission implementation yet. The
// unexported gate is replaceable only by package-local offline tests; callers
// cannot set a boolean or supply an arbitrary proof object.
type DirectAttemptRunner struct {
	client    *ResponsesClient
	config    DirectAttemptConfig
	admission directAdmission
	timeout   time.Duration
	mu        sync.Mutex
	used      bool
}

type directAdmission interface {
	Check(context.Context, Credentials, string) error
}

type unavailableDirectAdmission struct{}

func (unavailableDirectAdmission) Check(context.Context, Credentials, string) error {
	return ErrAdmissionUnavailable
}

func NewDirectAttemptRunner(client *ResponsesClient, config DirectAttemptConfig) (*DirectAttemptRunner, error) {
	if client == nil {
		return nil, errors.New("Responses client is required")
	}
	if strings.TrimSpace(config.ManifestPath) == "" || strings.TrimSpace(config.ManifestSHA) == "" || strings.TrimSpace(config.CaseID) == "" || config.Arm != "direct" || strings.TrimSpace(config.OutputDir) == "" {
		return nil, errors.New("manifest path/hash, case, direct arm, and private output directory are required")
	}
	if config.Model != "gpt-6-luna" || config.Effort != "medium" {
		return nil, errors.New("direct SIWC attempt requires the frozen gpt-6-luna model and medium reasoning effort")
	}
	return &DirectAttemptRunner{client: client, config: config, admission: unavailableDirectAdmission{}, timeout: DirectAttemptMaxWallTime}, nil
}

// Run prepares one exact direct-source prompt and records a body-free receipt.
// Until a verified SIWC route/account/quota adapter is added, the public
// constructor always produces a runner that records not_attempted/unscored.
func (r *DirectAttemptRunner) Run(ctx context.Context) (DirectAttemptReceipt, string, error) {
	if r == nil || r.client == nil || r.admission == nil {
		return DirectAttemptReceipt{}, "", errors.New("direct attempt runner is not initialized")
	}
	r.mu.Lock()
	if r.used {
		r.mu.Unlock()
		return DirectAttemptReceipt{}, "", ErrAttemptAlreadyClaimed
	}
	r.used = true
	r.mu.Unlock()

	digest, selectedCase, prompt, sourceReceipts, err := loadDirectCase(r.config)
	if err != nil {
		return DirectAttemptReceipt{}, "", err
	}
	receipt := DirectAttemptReceipt{
		Schema: "cairn.siwc-direct-attempt/1", Status: "not_attempted_unscored", TaskOutcome: "unknown",
		CaseID: selectedCase.ID, Arm: r.config.Arm, ManifestSHA256: digest, Model: r.config.Model,
		Reason: "missing_account_mapping_quota_and_real_route_proof", Sources: markDisposition(sourceReceipts, "planned_not_delivered"),
		ToolCallsAllowed: false, ToolCallsMade: 0,
	}
	if _, err := claimDirectAttempt(r.config.OutputDir, r.config.CaseID, digest); err != nil {
		return receipt, "", err
	}
	if err := r.admission.Check(ctx, r.client.Credentials, r.config.Model); err != nil {
		path, writeErr := writeDirectReceipt(r.config.OutputDir, r.config.CaseID, digest, receipt)
		if writeErr != nil {
			return receipt, "", fmt.Errorf("admission refused and receipt could not be retained: %w", writeErr)
		}
		return receipt, path, ErrAdmissionUnavailable
	}

	timeout := r.timeout
	if timeout <= 0 {
		timeout = DirectAttemptMaxWallTime
	}
	attemptCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	start := time.Now()
	result, runErr := r.client.Run(attemptCtx, r.config.Model, prompt, RunOptions{ReasoningEffort: r.config.Effort, UseCairnTools: false})
	receipt.ElapsedMillis = time.Since(start).Milliseconds()
	if runErr != nil {
		receipt.Status = "failed_unscored"
		receipt.Reason = "responses_run_failed"
		receipt.Sources = markDisposition(sourceReceipts, "delivery_unknown_after_attempt")
	} else {
		receipt.Answer = result.Text
		receipt.ServedModel = result.ServedModel
		receipt.ResponseIDs = append([]string(nil), result.ResponseIDs...)
		usage := result.Usage
		receipt.Usage = &usage
		receipt.Sources = markDisposition(sourceReceipts, "delivered_complete")
		receipt.Status = "completed_unscored"
		receipt.Reason = "independent_task_review_required"
		if checkErr := validateDirectResult(result, r.config.Model); checkErr != nil {
			receipt.Status = "failed_unscored"
			receipt.Reason = checkErr.Error()
		}
		if strings.Contains(strings.ToLower(result.Text), localOnlySentinel) {
			receipt.Status = "failed_unscored"
			receipt.Reason = "local_only_sentinel_in_answer"
		}
	}
	path, writeErr := writeDirectReceipt(r.config.OutputDir, r.config.CaseID, digest, receipt)
	if writeErr != nil {
		return receipt, "", fmt.Errorf("retain attempt receipt: %w", writeErr)
	}
	if runErr != nil {
		return receipt, path, errors.New("Responses attempt failed; sanitized receipt retained")
	}
	if receipt.Status == "failed_unscored" {
		return receipt, path, fmt.Errorf("post-run validation failed: %s", receipt.Reason)
	}
	return receipt, path, nil
}

type frozenDirectManifest struct {
	Records map[string]struct {
		Eligibility string `json:"eligibility"`
		BodyParts   []struct {
			Text   string `json:"text"`
			Repeat int    `json:"repeat"`
		} `json:"body_parts"`
	} `json:"records"`
	Cases []struct {
		ID           string   `json:"id"`
		Task         string   `json:"task"`
		SourceLabels []string `json:"source_labels"`
		Arms         []string `json:"arms"`
	} `json:"cases"`
}

func loadDirectCase(config DirectAttemptConfig) (string, selectedDirectCase, string, []SourceReceipt, error) {
	var emptyCase selectedDirectCase
	file, err := os.Open(config.ManifestPath)
	if err != nil {
		return "", emptyCase, "", nil, fmt.Errorf("read frozen source-fit manifest: %w", err)
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, 5<<20+1))
	if err != nil {
		return "", emptyCase, "", nil, fmt.Errorf("read frozen source-fit manifest: %w", err)
	}
	if len(data) > 5<<20 {
		return "", emptyCase, "", nil, errors.New("frozen source-fit manifest exceeds size limit")
	}
	sum := sha256.Sum256(data)
	digest := hex.EncodeToString(sum[:])
	want := strings.TrimPrefix(strings.ToLower(config.ManifestSHA), "sha256:")
	if digest != want {
		return digest, emptyCase, "", nil, errors.New("frozen source-fit manifest digest mismatch")
	}
	if !supportedDirectManifestHash(want) {
		return digest, emptyCase, "", nil, errors.New("manifest hash is not an approved frozen v1/v2 source-fit fixture")
	}
	var manifest frozenDirectManifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return digest, emptyCase, "", nil, errors.New("invalid frozen source-fit manifest")
	}
	var header struct {
		Schema string `json:"schema"`
	}
	if err := json.Unmarshal(data, &header); err != nil || header.Schema != "cairn.source-fit-panel/1" || manifest.Records == nil || len(manifest.Cases) == 0 {
		return digest, emptyCase, "", nil, errors.New("unsupported frozen source-fit manifest schema")
	}
	var selected selectedDirectCase
	for _, item := range manifest.Cases {
		if item.ID == config.CaseID {
			selected = selectedDirectCase{item.ID, item.Task, item.SourceLabels, item.Arms}
			break
		}
	}
	if selected.ID == "" || !contains(selected.Arms, "direct") || strings.TrimSpace(selected.Task) == "" {
		return digest, emptyCase, "", nil, errors.New("selected direct case is absent or invalid in frozen manifest")
	}
	var prompt strings.Builder
	prompt.WriteString("Answer the task using only the complete direct sources below. If no source is supplied or the sources do not answer the task, say that the information is unavailable. Do not infer a local-only source.\n\nTask:\n")
	prompt.WriteString(selected.Task)
	prompt.WriteString("\n\nDirect sources (exact whole source text):\n")
	receipts := make([]SourceReceipt, 0, len(selected.SourceLabels))
	seen := make(map[string]bool)
	for _, label := range selected.SourceLabels {
		if !validSourceLabel(label) || seen[label] {
			return digest, emptyCase, "", nil, errors.New("frozen case has an invalid or duplicate direct source label")
		}
		seen[label] = true
		record, ok := manifest.Records[label]
		if !ok || (record.Eligibility != "shareable_optional" && record.Eligibility != "mandatory") || len(record.BodyParts) == 0 {
			return digest, emptyCase, "", nil, errors.New("direct source is missing or ineligible")
		}
		var body strings.Builder
		for _, part := range record.BodyParts {
			repeat := part.Repeat
			if repeat == 0 {
				repeat = 1
			}
			if repeat < 1 || repeat > 10000 || part.Text == "" {
				return digest, emptyCase, "", nil, errors.New("invalid frozen source body part")
			}
			for i := 0; i < repeat; i++ {
				if body.Len()+len(part.Text) > 1<<20 {
					return digest, emptyCase, "", nil, errors.New("frozen direct source exceeds size limit")
				}
				body.WriteString(part.Text)
			}
		}
		bodyText := body.String()
		if strings.Contains(strings.ToLower(bodyText), localOnlySentinel) {
			return digest, emptyCase, "", nil, errors.New("local-only sentinel present in selected direct source")
		}
		bodyHash := sha256.Sum256([]byte(bodyText))
		bodyDigest := hex.EncodeToString(bodyHash[:])
		fmt.Fprintf(&prompt, "\n[Source label: %s; extent: whole; bytes: %d; sha256: %s]\n", label, len([]byte(bodyText)), bodyDigest)
		prompt.WriteString(bodyText)
		receipts = append(receipts, SourceReceipt{Label: label, Extent: "whole", Bytes: len([]byte(bodyText)), SHA256: bodyDigest})
	}
	return digest, selected, prompt.String(), receipts, nil
}

func validateDirectResult(result Result, requestedModel string) error {
	if strings.TrimSpace(result.Text) == "" || result.ServedModel != requestedModel || len(result.ResponseIDs) != 1 {
		return errors.New("answer_model_or_response_count_invalid")
	}
	u := result.Usage
	if u.InputTokens <= 0 || u.OutputTokens <= 0 || u.TotalTokens != u.InputTokens+u.OutputTokens || u.TotalTokens > DirectAttemptMaxTokens || u.OutputTokens > DirectAttemptMaxOutput {
		return errors.New("usage_missing_inconsistent_or_over_limit")
	}
	if u.CachedInputTokens != nil && (*u.CachedInputTokens < 0 || *u.CachedInputTokens > u.InputTokens) {
		return errors.New("usage_cache_count_invalid")
	}
	if u.ReasoningTokens != nil && (*u.ReasoningTokens < 0 || *u.ReasoningTokens > u.OutputTokens) {
		return errors.New("usage_reasoning_count_invalid")
	}
	return nil
}

func markDisposition(items []SourceReceipt, disposition string) []SourceReceipt {
	out := append([]SourceReceipt(nil), items...)
	for i := range out {
		out[i].Disposition = disposition
	}
	return out
}

func writeDirectReceipt(outputDir, caseID, manifestSHA string, receipt DirectAttemptReceipt) (string, error) {
	if err := ensureOwnerOnlyDir(outputDir); err != nil {
		return "", err
	}
	name := fmt.Sprintf("%s-direct-%s.json", safeName(caseID), manifestSHA[:12])
	path := filepath.Join(outputDir, name)
	encoded, err := json.MarshalIndent(receipt, "", "  ")
	if err != nil {
		return "", err
	}
	dirFD, err := openOwnerOnlyDirectory(outputDir)
	if err != nil {
		return "", err
	}
	defer syscall.Close(dirFD)
	fileFD, err := syscall.Openat(dirFD, name, syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0600)
	if err != nil {
		return "", fmt.Errorf("create unique owner-only receipt: %w", err)
	}
	f := os.NewFile(uintptr(fileFD), path)
	_, writeErr := f.Write(append(encoded, '\n'))
	closeErr := f.Close()
	if writeErr != nil {
		_ = syscall.Unlinkat(dirFD, name)
		return "", writeErr
	}
	if closeErr != nil {
		_ = syscall.Unlinkat(dirFD, name)
		return "", closeErr
	}
	return path, nil
}

func claimDirectAttempt(outputDir, caseID, manifestSHA string) (string, error) {
	if err := ensureOwnerOnlyDir(outputDir); err != nil {
		return "", err
	}
	name := fmt.Sprintf("%s-direct-%s.claim", safeName(caseID), manifestSHA[:12])
	path := filepath.Join(outputDir, name)
	claim, err := json.Marshal(struct {
		Schema         string `json:"schema"`
		CaseID         string `json:"case_id"`
		Arm            string `json:"arm"`
		ManifestSHA256 string `json:"manifest_sha256"`
	}{"cairn.siwc-direct-attempt-claim/1", caseID, "direct", manifestSHA})
	if err != nil {
		return "", err
	}
	dirFD, err := openOwnerOnlyDirectory(outputDir)
	if err != nil {
		return "", err
	}
	defer syscall.Close(dirFD)
	fd, err := syscall.Openat(dirFD, name, syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0600)
	if err != nil {
		if errors.Is(err, syscall.EEXIST) {
			return "", ErrAttemptAlreadyClaimed
		}
		return "", fmt.Errorf("claim one-attempt execution: %w", err)
	}
	f := os.NewFile(uintptr(fd), path)
	if _, err := f.Write(append(claim, '\n')); err != nil {
		_ = f.Close()
		_ = syscall.Unlinkat(dirFD, name)
		return "", err
	}
	if err := f.Close(); err != nil {
		_ = syscall.Unlinkat(dirFD, name)
		return "", err
	}
	return path, nil
}

func ensureOwnerOnlyDir(path string) error {
	fd, err := openOwnerOnlyDirectory(path)
	if err != nil {
		return err
	}
	return syscall.Close(fd)
}

func openOwnerOnlyDirectory(path string) (int, error) {
	if strings.TrimSpace(path) == "" {
		return -1, errors.New("private output directory is required")
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return -1, err
	}
	if abs == string(filepath.Separator) {
		return -1, errors.New("filesystem root cannot be used as private output")
	}
	fd, err := syscall.Open(string(filepath.Separator), syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_CLOEXEC, 0)
	if err != nil {
		return -1, err
	}
	components := strings.Split(strings.TrimPrefix(abs, string(filepath.Separator)), string(filepath.Separator))
	for _, component := range components {
		if component == "" || component == "." || component == ".." {
			_ = syscall.Close(fd)
			return -1, errors.New("private output path is not canonical")
		}
		if err := rejectGitDirectory(fd); err != nil {
			_ = syscall.Close(fd)
			return -1, err
		}
		nextFD, openErr := syscall.Openat(fd, component, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
		if errors.Is(openErr, syscall.ENOENT) {
			if mkdirErr := syscall.Mkdirat(fd, component, 0700); mkdirErr != nil && !errors.Is(mkdirErr, syscall.EEXIST) {
				_ = syscall.Close(fd)
				return -1, mkdirErr
			}
			nextFD, openErr = syscall.Openat(fd, component, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
		}
		if openErr != nil {
			_ = syscall.Close(fd)
			if errors.Is(openErr, syscall.ELOOP) || errors.Is(openErr, syscall.ENOTDIR) {
				return -1, errors.New("private output path cannot contain symlink ancestors")
			}
			return -1, openErr
		}
		_ = syscall.Close(fd)
		fd = nextFD
	}
	if err := rejectGitDirectory(fd); err != nil {
		_ = syscall.Close(fd)
		return -1, err
	}
	var info syscall.Stat_t
	if err := syscall.Fstat(fd, &info); err != nil {
		_ = syscall.Close(fd)
		return -1, err
	}
	if info.Uid != uint32(os.Getuid()) {
		_ = syscall.Close(fd)
		return -1, errors.New("private output directory is not owned by current user")
	}
	if info.Mode&0077 != 0 {
		_ = syscall.Close(fd)
		return -1, errors.New("private output directory must be owner-only (0700 or stricter)")
	}
	return fd, nil
}

func rejectGitDirectory(dirFD int) error {
	markerFD, err := syscall.Openat(dirFD, ".git", syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC|syscall.O_NONBLOCK, 0)
	if err == nil {
		_ = syscall.Close(markerFD)
		return errors.New("private output directory cannot be inside a Git checkout")
	}
	if errors.Is(err, syscall.ENOENT) {
		return nil
	}
	return fmt.Errorf("cannot verify private output is outside a Git checkout: %w", err)
}

func validSourceLabel(label string) bool {
	if label == "" {
		return false
	}
	for _, r := range label {
		if !((r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '-' || r == '_') {
			return false
		}
	}
	return true
}

func safeName(name string) string {
	var b strings.Builder
	for _, r := range name {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '-' || r == '_' {
			b.WriteRune(r)
		} else {
			b.WriteByte('_')
		}
	}
	if b.Len() == 0 {
		return "attempt"
	}
	return b.String()
}
