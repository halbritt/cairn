package siwc

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type offlineAdmission struct{}

func (offlineAdmission) Check(context.Context, Credentials, string) error { return nil }

func frozenV2(t *testing.T) (string, string) {
	t.Helper()
	path := filepath.Join("..", "source-fit", "codex-luna-2026-10-25-v2", "FROZEN.json")
	return path, "dffecf6c44de456354d0dbe254704717a2ca0231c291b212f775f0fd49d57c85"
}

func frozenV1(t *testing.T) (string, string) {
	t.Helper()
	path := filepath.Join("..", "source-fit", "codex-luna-2026-10-25", "FROZEN.json")
	return path, "0a4e44eb95ce2a26a48d9731a87fef1d75bf3b2d9958925f70ffb226a323f5bf"
}

func privateOutputDir(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "private-output")
	if err := os.Mkdir(path, 0700); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestDirectContextUsesOnlyFrozenEligibleSourceLabels(t *testing.T) {
	path, digest := frozenV2(t)
	config := DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "buried_live_qualification", Arm: "direct", OutputDir: t.TempDir()}
	_, selected, prompt, sources, err := loadDirectCase(config)
	if err != nil {
		t.Fatal(err)
	}
	if selected.ID != config.CaseID || len(sources) != 1 || sources[0].Label != "relay_mixed" || sources[0].Bytes != 11482 || sources[0].Extent != "whole" {
		t.Fatalf("unexpected selected source receipts: %+v", sources)
	}
	if !strings.Contains(prompt, "Current qualification (2026-10-20): the live relay admission allowance is 512 KiB") || !strings.Contains(prompt, "2048 KiB is historical and offline only") {
		t.Fatal("direct prompt omitted exact buried source qualifications")
	}
	if strings.Contains(strings.ToLower(prompt), localOnlySentinel) || strings.Contains(prompt, "trail_atlas") {
		t.Fatal("direct prompt contains ineligible source content")
	}
}

func TestBothFrozenManifestVersionsArePinnedAndReadable(t *testing.T) {
	v1Path, v1Hash := frozenV1(t)
	v2Path, v2Hash := frozenV2(t)
	for _, item := range []struct{ path, digest string }{{v1Path, v1Hash}, {v2Path, v2Hash}} {
		config := DirectAttemptConfig{ManifestPath: item.path, ManifestSHA: item.digest, CaseID: "buried_live_qualification", Arm: "direct", OutputDir: privateOutputDir(t)}
		_, selected, prompt, sources, err := loadDirectCase(config)
		if err != nil {
			t.Fatalf("manifest %s: %v", item.path, err)
		}
		if selected.ID != config.CaseID || len(sources) != 1 || sources[0].Bytes != 11482 || !strings.Contains(prompt, "512 KiB") {
			t.Fatalf("manifest %s produced incomplete direct source: case=%+v sources=%+v", item.path, selected, sources)
		}
	}
}

func TestInaccessibleCaseBuildsTaskOnlyContext(t *testing.T) {
	path, digest := frozenV2(t)
	config := DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "inaccessible_source", Arm: "direct", OutputDir: t.TempDir()}
	_, _, prompt, sources, err := loadDirectCase(config)
	if err != nil {
		t.Fatal(err)
	}
	if len(sources) != 0 || strings.Contains(strings.ToLower(prompt), localOnlySentinel) {
		t.Fatalf("inaccessible case received source content: sources=%+v", sources)
	}
}

func TestManifestDigestMismatchRefusesContext(t *testing.T) {
	path, digest := frozenV2(t)
	config := DirectAttemptConfig{ManifestPath: path, ManifestSHA: strings.Repeat("0", len(digest)), CaseID: "current_correction", Arm: "direct", OutputDir: t.TempDir()}
	if _, _, _, _, err := loadDirectCase(config); err == nil || !strings.Contains(err.Error(), "digest mismatch") {
		t.Fatalf("changed manifest accepted: %v", err)
	}
}

func TestRunnerRequiresExplicitModelAndEffort(t *testing.T) {
	path, digest := frozenV2(t)
	client := validResponseClient(t, &fakeCairnTools{}, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		t.Fatal("configuration refusal reached Responses")
		return nil, nil
	})
	_, err := NewDirectAttemptRunner(client, DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "current_correction", Arm: "direct", OutputDir: privateOutputDir(t)})
	if err == nil || !strings.Contains(err.Error(), "frozen gpt-6-luna model and medium") {
		t.Fatalf("missing frozen model/effort accepted: %v", err)
	}
}

func TestOwnerOnlyOutputRejectsGitAndSymlinkAncestors(t *testing.T) {
	root := t.TempDir()
	repo := filepath.Join(root, "repo")
	if err := os.Mkdir(repo, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(repo, ".git"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := ensureOwnerOnlyDir(filepath.Join(repo, "private-output")); err == nil || !strings.Contains(err.Error(), "Git checkout") {
		t.Fatalf("repository-local answer path accepted: %v", err)
	}
	real := filepath.Join(root, "real")
	if err := os.Mkdir(real, 0700); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "link")
	if err := os.Symlink(real, link); err != nil {
		t.Fatal(err)
	}
	if err := ensureOwnerOnlyDir(filepath.Join(link, "private-output")); err == nil || !strings.Contains(err.Error(), "symlink ancestors") {
		t.Fatalf("symlink ancestor accepted: %v", err)
	}
}

func TestPublicRunnerFailsUnscoredWithoutRouteProof(t *testing.T) {
	path, digest := frozenV2(t)
	providerRequests := 0
	client := validResponseClient(t, &fakeCairnTools{}, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		providerRequests++
		t.Fatal("Responses was invoked without a real-route admission gate")
		return nil, nil
	})
	out := privateOutputDir(t)
	runner, err := NewDirectAttemptRunner(client, DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "current_correction", Arm: "direct", OutputDir: out, Model: "gpt-6-luna", Effort: "medium"})
	if err != nil {
		t.Fatal(err)
	}
	receipt, receiptPath, err := runner.Run(context.Background())
	if err == nil || !strings.Contains(err.Error(), "proof are unavailable") {
		t.Fatalf("missing admission proof did not block: receipt=%+v err=%v", receipt, err)
	}
	if providerRequests != 0 || receipt.Status != "not_attempted_unscored" || receipt.TaskOutcome != "unknown" || receiptPath == "" {
		t.Fatalf("unexpected blocked run: requests=%d receipt=%+v path=%q", providerRequests, receipt, receiptPath)
	}
	if _, _, secondErr := runner.Run(context.Background()); secondErr == nil || !strings.Contains(secondErr.Error(), "already claimed") {
		t.Fatalf("same runner allowed a second attempt: %v", secondErr)
	}
	retryRunner, err := NewDirectAttemptRunner(client, DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "current_correction", Arm: "direct", OutputDir: out, Model: "gpt-6-luna", Effort: "medium"})
	if err != nil {
		t.Fatal(err)
	}
	if _, _, retryErr := retryRunner.Run(context.Background()); retryErr == nil || !strings.Contains(retryErr.Error(), "already claimed") {
		t.Fatalf("second runner bypassed the durable attempt claim: %v", retryErr)
	}
	if providerRequests != 0 {
		t.Fatalf("blocked or duplicate attempt reached provider: %d", providerRequests)
	}
	assertOwnerOnlyArtifact(t, out, receiptPath)
	data, err := os.ReadFile(receiptPath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), "Cairn export correction, current version 2") || strings.Contains(string(data), "access-in-memory") {
		t.Fatal("receipt retained source body or credential")
	}
	claims, err := filepath.Glob(filepath.Join(out, "*.claim"))
	if err != nil || len(claims) != 1 {
		t.Fatalf("durable one-attempt claim missing: claims=%v err=%v", claims, err)
	}
	claimInfo, err := os.Stat(claims[0])
	if err != nil || claimInfo.Mode().Perm() != 0600 {
		t.Fatalf("claim is not owner-only: info=%v err=%v", claimInfo, err)
	}
}

func TestOfflineOneAttemptUsesNoToolsAndRetainsBodyFreeReceipt(t *testing.T) {
	path, digest := frozenV2(t)
	tools := &fakeCairnTools{}
	client := validResponseClient(t, tools, func(_ *http.Request, body map[string]any) (*http.Response, error) {
		if body["tools"] != nil || body["store"] != false || body["stream"] != true {
			t.Fatalf("direct source request has wrong tool/storage contract: %#v", body)
		}
		encoded, _ := json.Marshal(body["input"])
		if !strings.Contains(string(encoded), "POST /v2/export") || !strings.Contains(string(encoded), "POST /v1/export") {
			t.Fatal("direct context omitted the frozen complete sources")
		}
		return streamResponse(t, "resp-direct", []any{map[string]any{"id": "answer", "type": "message", "content": []any{map[string]any{"type": "output_text", "text": "Use POST /v2/export. POST /v1/export is historical."}}}}, 100, 15, 115, 20, 3), nil
	})
	out := privateOutputDir(t)
	runner, err := NewDirectAttemptRunner(client, DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "current_correction", Arm: "direct", OutputDir: out, Model: "gpt-6-luna", Effort: "medium"})
	if err != nil {
		t.Fatal(err)
	}
	runner.admission = offlineAdmission{}
	receipt, receiptPath, err := runner.Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if tools.searches != 0 || tools.pulls != 0 || receipt.ToolCallsAllowed || receipt.ToolCallsMade != 0 {
		t.Fatalf("direct arm invoked tools: %+v tools=%+v", receipt, tools)
	}
	if receipt.Status != "completed_unscored" || receipt.TaskOutcome != "unknown" || receipt.Answer == "" || len(receipt.Sources) != 2 || receipt.Usage == nil {
		t.Fatalf("incomplete direct receipt: %+v", receipt)
	}
	assertOwnerOnlyArtifact(t, out, receiptPath)
	data, err := os.ReadFile(receiptPath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), "Cairn export correction, current version 2") || strings.Contains(string(data), "Cairn export procedure, historical version 1") || strings.Contains(string(data), "body_parts") {
		t.Fatal("receipt retained a source body")
	}
}

func TestPostRunUsageLimitRetainsAnswerAndMarksUnscored(t *testing.T) {
	path, digest := frozenV2(t)
	client := validResponseClient(t, &fakeCairnTools{}, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		return streamResponse(t, "resp-over-limit", []any{map[string]any{"id": "answer", "type": "message", "content": []any{map[string]any{"type": "output_text", "text": "answer retained"}}}}, 100, DirectAttemptMaxOutput+1, 100+DirectAttemptMaxOutput+1, -1, -1), nil
	})
	out := privateOutputDir(t)
	runner, err := NewDirectAttemptRunner(client, DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "semantic_unavailable", Arm: "direct", OutputDir: out, Model: "gpt-6-luna", Effort: "medium"})
	if err != nil {
		t.Fatal(err)
	}
	runner.admission = offlineAdmission{}
	receipt, receiptPath, err := runner.Run(context.Background())
	if err == nil || receipt.Status != "failed_unscored" || receipt.Reason != "usage_missing_inconsistent_or_over_limit" || receipt.Answer != "answer retained" {
		t.Fatalf("usage overrun not retained/unscored: receipt=%+v err=%v", receipt, err)
	}
	assertOwnerOnlyArtifact(t, out, receiptPath)
}

func TestAttemptContextTimeoutStopsResponsesAndRetainsSanitizedReceipt(t *testing.T) {
	path, digest := frozenV2(t)
	client := validResponseClient(t, &fakeCairnTools{}, func(req *http.Request, _ map[string]any) (*http.Response, error) {
		<-req.Context().Done()
		return nil, req.Context().Err()
	})
	out := privateOutputDir(t)
	runner, err := NewDirectAttemptRunner(client, DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "semantic_unavailable", Arm: "direct", OutputDir: out, Model: "gpt-6-luna", Effort: "medium"})
	if err != nil {
		t.Fatal(err)
	}
	runner.admission = offlineAdmission{}
	runner.timeout = 10 * time.Millisecond
	receipt, receiptPath, err := runner.Run(context.Background())
	if err == nil || receipt.Status != "failed_unscored" || receipt.Reason != "responses_run_failed" || receiptPath == "" {
		t.Fatalf("timeout was not retained as failed/unscored: receipt=%+v err=%v", receipt, err)
	}
	assertOwnerOnlyArtifact(t, out, receiptPath)
	data, readErr := os.ReadFile(receiptPath)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if strings.Contains(string(data), "context deadline exceeded") || strings.Contains(string(data), "access-in-memory") {
		t.Fatal("failure receipt retained raw error or credentials")
	}
}

func TestProviderFailureDoesNotClaimSourcesWereNotDelivered(t *testing.T) {
	path, digest := frozenV2(t)
	requestReached := false
	client := validResponseClient(t, &fakeCairnTools{}, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		requestReached = true
		return &http.Response{StatusCode: http.StatusInternalServerError, Body: io.NopCloser(strings.NewReader("sensitive provider error body")), Header: make(http.Header)}, nil
	})
	out := privateOutputDir(t)
	runner, err := NewDirectAttemptRunner(client, DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "current_correction", Arm: "direct", OutputDir: out, Model: "gpt-6-luna", Effort: "medium"})
	if err != nil {
		t.Fatal(err)
	}
	runner.admission = offlineAdmission{}
	receipt, receiptPath, err := runner.Run(context.Background())
	if err == nil || !requestReached || receipt.Status != "failed_unscored" || receiptPath == "" {
		t.Fatalf("provider failure was not retained: reached=%t receipt=%+v err=%v", requestReached, receipt, err)
	}
	for _, source := range receipt.Sources {
		if source.Disposition != "delivery_unknown_after_attempt" {
			t.Fatalf("request error overclaimed delivery state: %+v", source)
		}
	}
	data, readErr := os.ReadFile(receiptPath)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if strings.Contains(string(data), "sensitive provider error body") {
		t.Fatal("receipt retained raw provider error body")
	}
}

func assertOwnerOnlyArtifact(t *testing.T, out, path string) {
	t.Helper()
	info, err := os.Stat(out)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm()&0077 != 0 {
		t.Fatalf("output directory mode is not owner-only: %o", info.Mode().Perm())
	}
	info, err = os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0600 {
		t.Fatalf("receipt mode = %o, want 600", info.Mode().Perm())
	}
}
