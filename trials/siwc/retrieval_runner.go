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
	"strings"
	"time"
)

// RetrievalAttemptRunner executes one offline-capable retrieval arm. Its
// public admission gate is intentionally unavailable; package tests may inject
// a fake gate and fake HTTP transport, but production callers cannot admit it.
type RetrievalAttemptRunner struct {
	client  *ResponsesClient
	config  DirectAttemptConfig
	gate    directAdmission
	timeout time.Duration
}

func NewRetrievalAttemptRunner(client *ResponsesClient, config DirectAttemptConfig) (*RetrievalAttemptRunner, error) {
	if client == nil || client.Tools == nil {
		return nil, errors.New("Responses client and trial Cairn tools are required")
	}
	if _, ok := client.Tools.(*TrialStoreTools); !ok {
		return nil, errors.New("retrieval runner accepts only the authenticated disposable TrialStoreTools adapter")
	}
	return newRetrievalAttemptRunner(client, config)
}

// newRetrievalAttemptRunner is package-private so offline tests can substitute
// a fake transport and fake tools. External callers must pass the public
// concrete TrialStoreTools adapter above.
func newRetrievalAttemptRunner(client *ResponsesClient, config DirectAttemptConfig) (*RetrievalAttemptRunner, error) {
	if client == nil || client.Tools == nil {
		return nil, errors.New("Responses client and trial Cairn tools are required")
	}
	if strings.TrimSpace(config.ManifestPath) == "" || strings.TrimSpace(config.ManifestSHA) == "" || strings.TrimSpace(config.CaseID) == "" || strings.TrimSpace(config.OutputDir) == "" {
		return nil, errors.New("manifest path/hash, case, and private output directory are required")
	}
	if config.Arm != "current" && config.Arm != "hints" && config.Arm != "author_split" {
		return nil, errors.New("retrieval arm must be current, hints, or author_split")
	}
	if config.Model != "gpt-6-luna" || config.Effort != "medium" {
		return nil, errors.New("SIWC attempt requires the frozen gpt-6-luna model and medium reasoning effort")
	}
	return &RetrievalAttemptRunner{client: client, config: config, gate: unavailableDirectAdmission{}, timeout: DirectAttemptMaxWallTime}, nil
}

// Run performs one attempt and retains a selected answer plus body-free source
// receipts. Any uncertainty in fixture identity, extent, local-only exclusion,
// provider result, or usage makes the attempt unscored and stops it.
func (r *RetrievalAttemptRunner) Run(ctx context.Context) (DirectAttemptReceipt, string, error) {
	if r == nil || r.client == nil || r.client.Tools == nil || r.gate == nil {
		return DirectAttemptReceipt{}, "", errors.New("retrieval attempt runner is not initialized")
	}
	digest, selected, prompt, expected, err := loadRetrievalCase(r.config)
	if err != nil {
		return DirectAttemptReceipt{}, "", err
	}
	receipt := DirectAttemptReceipt{Schema: "cairn.siwc-retrieval-attempt/1", Status: "not_attempted_unscored", TaskOutcome: "unknown", CaseID: selected.ID, Arm: r.config.Arm, ManifestSHA256: digest, Model: r.config.Model, Reason: "missing_account_mapping_quota_and_real_route_proof", ToolCallsAllowed: true, Sources: expected}
	if _, err = claimDirectAttempt(r.config.OutputDir, r.config.CaseID+"-"+r.config.Arm, digest); err != nil {
		return receipt, "", err
	}
	if err = r.gate.Check(ctx, r.client.Credentials, r.config.Model); err != nil {
		path, we := writeDirectReceipt(r.config.OutputDir, r.config.CaseID+"-"+r.config.Arm, digest, receipt)
		if we != nil {
			return receipt, "", fmt.Errorf("admission refused and receipt could not be retained: %w", we)
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
	result, runErr := r.client.Run(attemptCtx, r.config.Model, prompt, RunOptions{ReasoningEffort: r.config.Effort, UseCairnTools: true})
	receipt.ElapsedMillis = time.Since(start).Milliseconds()
	toolSourceReceipts := trialReceipts(r.client.Tools)
	if runErr != nil {
		receipt.Status = "failed_unscored"
		receipt.Reason = "responses_or_trial_tool_failed"
		receipt.Sources = append(receipt.Sources, trialSourceReceipts(toolSourceReceipts)...)
	} else {
		receipt.Answer = result.Text
		receipt.ServedModel = result.ServedModel
		receipt.ResponseIDs = append([]string(nil), result.ResponseIDs...)
		usage := result.Usage
		receipt.Usage = &usage
		receipt.ToolCallsMade = result.ToolCalls
		receipt.Status = "completed_unscored"
		receipt.Reason = "independent_task_review_required"
		if err := validateRetrievalResult(result, r.config.Model); err != nil {
			receipt.Status = "failed_unscored"
			receipt.Reason = err.Error()
		}
		if strings.Contains(strings.ToLower(result.Text), localOnlySentinel) {
			receipt.Status = "failed_unscored"
			receipt.Reason = "local_only_sentinel_in_answer"
		}
		if err := validateFixtureDelivery(r.config, selected, toolSourceReceipts); err != nil {
			receipt.Status = "failed_unscored"
			receipt.Reason = "source_delivery_invalid"
		}
		receipt.Sources = append(receipt.Sources, trialSourceReceipts(toolSourceReceipts)...)
	}
	path, writeErr := writeDirectReceipt(r.config.OutputDir, r.config.CaseID+"-"+r.config.Arm, digest, receipt)
	if writeErr != nil {
		return receipt, "", fmt.Errorf("retain attempt receipt: %w", writeErr)
	}
	if runErr != nil {
		return receipt, path, errors.New("Responses retrieval attempt failed; sanitized receipt retained")
	}
	if receipt.Status == "failed_unscored" {
		return receipt, path, fmt.Errorf("retrieval validation failed: %s", receipt.Reason)
	}
	return receipt, path, nil
}

func validateRetrievalResult(result Result, requestedModel string) error {
	if strings.TrimSpace(result.Text) == "" || result.ServedModel != requestedModel || len(result.ResponseIDs) < 1 || len(result.ResponseIDs) > 4 {
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

type retrievalManifest struct {
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
		HintSuffix   string   `json:"hint_suffix"`
		AuthorSplit  *struct {
			Current string `json:"current"`
		} `json:"author_split"`
	} `json:"cases"`
}

func loadRetrievalCase(config DirectAttemptConfig) (string, selectedDirectCase, string, []SourceReceipt, error) {
	// Reuse the direct loader's strict manifest digest/schema/frozen-hash gate.
	copyConfig := config
	copyConfig.Arm = "direct"
	digest, _, _, _, err := loadDirectCase(copyConfig)
	if err != nil {
		return digest, selectedDirectCase{}, "", nil, err
	}
	data, err := readManifest(config.ManifestPath)
	if err != nil {
		return digest, selectedDirectCase{}, "", nil, err
	}
	var m retrievalManifest
	if err := json.Unmarshal(data, &m); err != nil {
		return digest, selectedDirectCase{}, "", nil, errors.New("invalid frozen source-fit manifest")
	}
	var c struct {
		ID           string   `json:"id"`
		Task         string   `json:"task"`
		SourceLabels []string `json:"source_labels"`
		Arms         []string `json:"arms"`
		HintSuffix   string   `json:"hint_suffix"`
		AuthorSplit  *struct {
			Current string `json:"current"`
		} `json:"author_split"`
	}
	for _, item := range m.Cases {
		if item.ID == config.CaseID {
			c = item
			break
		}
	}
	if c.ID == "" || !contains(c.Arms, config.Arm) || strings.TrimSpace(c.Task) == "" {
		return digest, selectedDirectCase{}, "", nil, errors.New("selected retrieval case or arm absent from frozen manifest")
	}
	selected := selectedDirectCase{ID: c.ID, Task: c.Task, SourceLabels: c.SourceLabels, Arms: c.Arms}
	labels := append([]string(nil), c.SourceLabels...)
	if config.Arm == "author_split" {
		if c.AuthorSplit == nil || c.AuthorSplit.Current == "" {
			return digest, selected, "", nil, errors.New("author_split case lacks current extract")
		}
		labels = []string{c.AuthorSplit.Current}
	}
	var prompt strings.Builder
	prompt.WriteString("Answer the task using only eligible sources returned by Cairn search and pull. Pull exact search-returned arguments when a complete source is needed. If the source is unavailable, incomplete, conflicting, or does not answer the task, say so. Never infer or disclose local-only sources.\n\nTask:\n")
	prompt.WriteString(c.Task)
	if config.Arm == "author_split" {
		label := labels[0]
		record := m.Records[label]
		prompt.WriteString("\n\nAuthor-maintained current extract (complete source):\n")
		prompt.WriteString(flattenFixture(record.BodyParts))
	}
	if config.Arm == "hints" {
		if c.HintSuffix == "" {
			return digest, selected, "", nil, errors.New("hints arm lacks frozen hint")
		}
		prompt.WriteString("\n\nRetrieval hint:\n")
		prompt.WriteString(c.HintSuffix)
	}
	var sources []SourceReceipt
	for _, label := range labels {
		rec, ok := m.Records[label]
		if !ok || (rec.Eligibility != "shareable_optional" && rec.Eligibility != "mandatory") {
			return digest, selected, "", nil, errors.New("retrieval fixture source is absent or ineligible")
		}
		body := flattenFixture(rec.BodyParts)
		sum := sha256.Sum256([]byte(body))
		disposition := "planned_not_delivered"
		if config.Arm == "author_split" {
			disposition = "delivered_complete"
		}
		sources = append(sources, SourceReceipt{Label: label, Extent: "expected_fixture", Bytes: len([]byte(body)), SHA256: hex.EncodeToString(sum[:]), Disposition: disposition})
	}
	return digest, selected, prompt.String(), sources, nil
}

func readManifest(path string) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, 5<<20+1))
	if err != nil {
		return nil, err
	}
	if len(data) > 5<<20 {
		return nil, errors.New("frozen source-fit manifest exceeds size limit")
	}
	return data, nil
}

func flattenFixture(parts []struct {
	Text   string `json:"text"`
	Repeat int    `json:"repeat"`
}) string {
	var b strings.Builder
	for _, p := range parts {
		n := p.Repeat
		if n == 0 {
			n = 1
		}
		for i := 0; i < n; i++ {
			b.WriteString(p.Text)
		}
	}
	return b.String()
}

type receiptSource interface{ Receipts() []TrialDeliveryReceipt }

func trialReceipts(tools CairnTools) []TrialDeliveryReceipt {
	if t, ok := tools.(receiptSource); ok {
		return t.Receipts()
	}
	return nil
}
func trialSourceReceipts(items []TrialDeliveryReceipt) []SourceReceipt {
	out := make([]SourceReceipt, 0, len(items))
	for _, r := range items {
		extent := r.Extent
		if extent == "full_body" {
			extent = "whole"
		}
		out = append(out, SourceReceipt{Label: r.RecordID, RecordID: r.RecordID, Version: r.Version, Extent: extent, Bytes: r.DeliveredBytes, SHA256: r.SourceSHA256, Disposition: "delivered"})
	}
	return out
}
func validateFixtureDelivery(config DirectAttemptConfig, c selectedDirectCase, items []TrialDeliveryReceipt) error {
	allowed := map[string]bool{}
	for _, label := range c.SourceLabels {
		allowed[label] = true
	}
	if config.Arm == "author_split" {
		allowed = map[string]bool{}
		data, err := readManifest(config.ManifestPath)
		if err != nil {
			return err
		}
		var raw struct {
			Cases []struct {
				ID          string `json:"id"`
				AuthorSplit *struct {
					Current string `json:"current"`
				} `json:"author_split"`
			} `json:"cases"`
		}
		if json.Unmarshal(data, &raw) != nil {
			return errors.New("invalid fixture")
		}
		for _, item := range raw.Cases {
			if item.ID == c.ID && item.AuthorSplit != nil && item.AuthorSplit.Current != "" {
				allowed[item.AuthorSplit.Current] = true
			}
		}
		if len(allowed) != 1 {
			return errors.New("author_split fixture source unavailable")
		}
	}
	// A receipt's full-source digest must match an exact frozen fixture source.
	data, err := readManifest(config.ManifestPath)
	if err != nil {
		return err
	}
	var m retrievalManifest
	if json.Unmarshal(data, &m) != nil {
		return errors.New("invalid fixture")
	}
	digests := map[string]bool{}
	previewDigests := map[string]bool{}
	localOnlyDigest := ""
	for label, rec := range m.Records {
		body := flattenFixture(rec.BodyParts)
		sum := sha256.Sum256([]byte(body))
		hash := hex.EncodeToString(sum[:])
		if strings.Contains(rec.Eligibility, "local_only") || strings.Contains(strings.ToLower(body), localOnlySentinel) {
			localOnlyDigest = hash
		}
		if rec.Eligibility == "shareable_optional" || rec.Eligibility == "mandatory" {
			previewDigests[hash] = true
			if allowed[label] {
				digests[hash] = true
			}
		}
	}
	full := map[string]bool{}
	for _, item := range items {
		if !trialUUID.MatchString(item.RecordID) || item.Version < 1 || !trialSHA256.MatchString(item.SourceSHA256) || !trialSHA256.MatchString(item.DeliveredSHA256) || item.DeliveredBytes <= 0 {
			return errors.New("source receipt lacks a valid fixture record ID, version, extent, or digest")
		}
		if item.SourceSHA256 == localOnlyDigest {
			return errors.New("local-only source appeared in hosted trial delivery")
		}
		if item.Extent == "preview" && !previewDigests[item.SourceSHA256] {
			return errors.New("unknown source preview identity")
		}
		if item.Extent != "preview" && !digests[item.SourceSHA256] {
			return errors.New("unknown source identity")
		}
		if item.Extent == "partial_span" {
			return errors.New("partial source extent")
		}
		if item.Extent == "full_body" {
			if item.SourceSHA256 != item.DeliveredSHA256 || item.TotalBytes == nil || *item.TotalBytes != item.DeliveredBytes {
				return errors.New("unknown or non-whole source")
			}
			full[item.SourceSHA256] = true
		}
		if item.Extent != "preview" && item.Extent != "full_body" {
			return errors.New("unknown source extent")
		}
	}
	// A source-bearing arm can finish only after a whole-source pull. Inaccessible
	// case intentionally has no eligible digest and must answer from task alone.
	if config.Arm != "author_split" {
		for digest := range digests {
			if !full[digest] {
				return errors.New("expected complete source was not delivered")
			}
		}
	}
	return nil
}
