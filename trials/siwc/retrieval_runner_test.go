package siwc

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type receiptFakeTools struct {
	fakeCairnTools
	receipts []TrialDeliveryReceipt
}

func (f *receiptFakeTools) Receipts() []TrialDeliveryReceipt {
	return append([]TrialDeliveryReceipt(nil), f.receipts...)
}

func TestRetrievalArmsUsePinnedFixtureAndDistinctPromptInputs(t *testing.T) {
	path, digest := frozenV2(t)
	for _, arm := range []string{"current", "hints", "author_split"} {
		config := DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "buried_live_qualification", Arm: arm, OutputDir: privateOutputDir(t), Model: "gpt-6-luna", Effort: "medium"}
		got, selected, prompt, sources, err := loadRetrievalCase(config)
		if err != nil {
			t.Fatal(err)
		}
		if got != digest || selected.ID != config.CaseID || strings.Contains(strings.ToLower(prompt), localOnlySentinel) {
			t.Fatalf("bad retrieval case arm=%s", arm)
		}
		switch arm {
		case "current":
			if len(sources) != 1 || sources[0].Label != "relay_mixed" || strings.Contains(prompt, "Retrieval hint:") {
				t.Fatalf("current arm drift: %+v", sources)
			}
		case "hints":
			if !strings.Contains(prompt, "Retrieval hint:") || len(sources) != 1 || sources[0].Label != "relay_mixed" {
				t.Fatalf("hints arm drift: %+v", sources)
			}
		case "author_split":
			if len(sources) != 1 || sources[0].Label != "relay_current_extract" || sources[0].Bytes != 310 {
				t.Fatalf("author split source drift: %+v", sources)
			}
		}
	}
	config := DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "required_conflict", Arm: "current", OutputDir: t.TempDir()}
	_, _, _, conflictSources, err := loadRetrievalCase(config)
	if err != nil || len(conflictSources) != 2 || conflictSources[0].Label == conflictSources[1].Label {
		t.Fatalf("mandatory conflicting source pair was not retained: %+v %v", conflictSources, err)
	}
}

func TestRetrievalRunnerAcceptsBothFrozenManifestVersions(t *testing.T) {
	v1Path, v1Digest := frozenV1(t)
	v2Path, v2Digest := frozenV2(t)
	for _, frozen := range []struct{ path, digest string }{{v1Path, v1Digest}, {v2Path, v2Digest}} {
		config := DirectAttemptConfig{ManifestPath: frozen.path, ManifestSHA: frozen.digest, CaseID: "buried_live_qualification", Arm: "hints", OutputDir: t.TempDir()}
		got, selected, prompt, _, err := loadRetrievalCase(config)
		if err != nil || got != frozen.digest || selected.ID != config.CaseID || !strings.Contains(prompt, "Retrieval hint:") {
			t.Fatalf("frozen manifest was not accepted: path=%s digest=%s selected=%+v err=%v", frozen.path, got, selected, err)
		}
	}
}

func TestOfflineRetrievalAttemptUsesToolsAndRetainsOnlyReceiptMetadata(t *testing.T) {
	path, digest := frozenV2(t)
	tools := &receiptFakeTools{}
	client := validResponseClient(t, tools, func(_ *http.Request, body map[string]any) (*http.Response, error) {
		input, _ := body["input"].([]any)
		if body["tools"] == nil || body["store"] != false || body["stream"] != true {
			t.Fatalf("retrieval request missing tool contract: %#v", body)
		}
		if len(input) == 1 {
			return streamResponse(t, "resp-search", []any{map[string]any{"id": "fc1", "type": "function_call", "call_id": "call1", "name": "cairn_search", "namespace": "cairn", "arguments": "{\"query\":\"buried relay allowance\"}"}}, 10, 2, 12, -1, -1), nil
		}
		return streamResponse(t, "resp-answer", []any{map[string]any{"id": "answer", "type": "message", "content": []any{map[string]any{"type": "output_text", "text": "Use 512 KiB; 2048 KiB is offline history."}}}}, 100, 15, 115, 20, 3), nil
	})
	// A completed retrieval must include an exact whole-source receipt.
	// The fake store reports an unknown whole-source digest, which must fail closed.
	body := "Cairn fixture complete body"
	size := len(body)
	tools.receipts = []TrialDeliveryReceipt{{RecordID: "00000000-0000-4000-8000-000000000001", Version: 1, Extent: "full_body", TotalBytes: &size, SourceSHA256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", DeliveredSHA256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", DeliveredBytes: size}}
	out := privateOutputDir(t)
	runner, err := newRetrievalAttemptRunner(client, DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "buried_live_qualification", Arm: "current", OutputDir: out, Model: "gpt-6-luna", Effort: "medium"})
	if err != nil {
		t.Fatal(err)
	}
	// Admission remains closed publicly; only this offline package test injects a fake.
	runner.gate = offlineAdmission{}
	_, _, runErr := runner.Run(context.Background())
	if runErr == nil || !strings.Contains(runErr.Error(), "validation failed") {
		t.Fatalf("unmatched source digest was not refused: %v", runErr)
	}
	files, err := filepath.Glob(filepath.Join(out, "*.json"))
	if err != nil || len(files) != 1 {
		t.Fatalf("sanitized failed receipt missing: %v %v", files, err)
	}
	data, err := os.ReadFile(files[0])
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), body) || strings.Contains(string(data), "amber harbor") {
		t.Fatal("receipt retained source body or local-only marker")
	}
}

func TestPublicRetrievalRunnerKeepsAdmissionUnavailable(t *testing.T) {
	path, digest := frozenV2(t)
	tools := &fakeCairnTools{}
	requests := 0
	client := validResponseClient(t, tools, func(*http.Request, map[string]any) (*http.Response, error) {
		requests++
		t.Fatal("provider request made while admission unavailable")
		return nil, nil
	})
	runner, err := newRetrievalAttemptRunner(client, DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "current_correction", Arm: "current", OutputDir: privateOutputDir(t), Model: "gpt-6-luna", Effort: "medium"})
	if err != nil {
		t.Fatal(err)
	}
	receipt, receiptPath, err := runner.Run(context.Background())
	if err == nil || receipt.Status != "not_attempted_unscored" || receiptPath == "" || requests != 0 {
		t.Fatalf("public admission gate opened: %+v %q %v", receipt, receiptPath, err)
	}
}

func TestOfflineRetrievalAcceptsExactWholeFixtureReceipt(t *testing.T) {
	path, digest := frozenV2(t)
	config := DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "current_correction", Arm: "current", OutputDir: privateOutputDir(t), Model: "gpt-6-luna", Effort: "medium"}
	_, _, _, expected, err := loadRetrievalCase(config)
	if err != nil {
		t.Fatal(err)
	}
	tools := &receiptFakeTools{}
	var receipts []TrialDeliveryReceipt
	for i, source := range expected {
		byteCount := source.Bytes
		receipts = append(receipts, TrialDeliveryReceipt{RecordID: fmt.Sprintf("00000000-0000-4000-8000-%012d", i+1), Version: 1, Extent: "full_body", TotalBytes: &byteCount, SourceSHA256: source.SHA256, DeliveredSHA256: source.SHA256, DeliveredBytes: byteCount})
	}
	tools.receipts = receipts
	if err := validateFixtureDelivery(config, selectedDirectCase{ID: "current_correction", SourceLabels: []string{"export_correction", "old_export"}}, receipts[:1]); err == nil {
		t.Fatal("attempt accepted when one selected source was missing")
	}
	client := validResponseClient(t, tools, func(_ *http.Request, body map[string]any) (*http.Response, error) {
		input, _ := body["input"].([]any)
		if len(input) == 1 {
			return streamResponse(t, "search", []any{map[string]any{"id": "fc1", "type": "function_call", "call_id": "call1", "name": "cairn_search", "namespace": "cairn", "arguments": "{\"query\":\"export correction\"}"}}, 10, 2, 12, -1, -1), nil
		}
		return streamResponse(t, "answer", []any{map[string]any{"id": "answer", "type": "message", "content": []any{map[string]any{"type": "output_text", "text": "Use POST /v2/export; /v1/export is historical."}}}}, 100, 15, 115, 20, 3), nil
	})
	runner, err := newRetrievalAttemptRunner(client, config)
	if err != nil {
		t.Fatal(err)
	}
	runner.gate = offlineAdmission{}
	receipt, receiptPath, err := runner.Run(context.Background())
	if err != nil || receipt.Status != "completed_unscored" || receipt.TaskOutcome != "unknown" || receipt.ToolCallsMade != 1 || receipt.Answer == "" {
		t.Fatalf("exact fixture receipt not accepted: %+v %v", receipt, err)
	}
	assertOwnerOnlyArtifact(t, config.OutputDir, receiptPath)
	data, err := os.ReadFile(receiptPath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), "Cairn export correction, current version 2") || strings.Contains(string(data), "body_parts") {
		t.Fatal("receipt retained source body")
	}
}

func TestPublicRetrievalConstructorRejectsCustomTools(t *testing.T) {
	path, digest := frozenV2(t)
	requests := 0
	client := validResponseClient(t, &fakeCairnTools{}, func(*http.Request, map[string]any) (*http.Response, error) { requests++; return nil, nil })
	_, err := NewRetrievalAttemptRunner(client, DirectAttemptConfig{ManifestPath: path, ManifestSHA: digest, CaseID: "current_correction", Arm: "current", OutputDir: privateOutputDir(t), Model: "gpt-6-luna", Effort: "medium"})
	if err == nil || !strings.Contains(err.Error(), "only the authenticated disposable TrialStoreTools") || requests != 0 {
		t.Fatalf("custom tool adapter accepted: err=%v requests=%d", err, requests)
	}
}
