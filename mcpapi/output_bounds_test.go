package mcpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestBoundedToolOutputsSpoolAndPreserveOptOutAndStrictRetrieval(t *testing.T) {
	spool := t.TempDir()
	receiptID := uuid.NewString()
	handleID := uuid.NewString()
	evidenceID := uuid.NewString()
	eventID := uuid.NewString()
	agentID := uuid.NewString()
	execID := uuid.NewString()
	recordID := uuid.NewString()

	largeReason := "HEAD_REASON: first diagnostic\n" +
		strings.Repeat("filler detail line for assessment payload\n", 320) +
		"ERROR: failed validation check at step 42\n" +
		strings.Repeat("trailing context padding\n", 120) +
		"TAIL_REASON: final review conclusion"
	largeEvidence := "EVIDENCE_HEAD: start\n" +
		strings.Repeat("evidence bulk line\n", 750) +
		"panic: assertion failed in evidence log\n" +
		strings.Repeat("evidence tail pad\n", 200) +
		"EVIDENCE_TAIL: end"

	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/assessments":
			assessments := []core.Assessment{{
				ReceiptID:   receiptID,
				Version:     1,
				TaskOutcome: "unknown",
				Witness:     "testimony",
				Observer:    "agent:test",
				Method:      "test/1",
				Reason:      largeReason,
			}}
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": assessments})
		case "/v1/expand-evidence":
			payload := map[string]any{
				"receipt_id":        receiptID,
				"credits_remaining": 2,
				"bytes_remaining":   4000,
				"evidence": map[string]any{
					"evidence_id": evidenceID,
					"sha256":      strings.Repeat("a", 64),
					"body":        largeEvidence,
				},
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": payload})
		case "/v1/agent-directory":
			agents := make([]core.AgentInstance, 0, 60)
			for i := range 60 {
				agents = append(agents, core.AgentInstance{
					Ordinal:     int64(i + 1),
					DisplayName: "agent-" + string(rune('a'+(i%26))),
					AgentID:     agentID,
					ExecutionID: execID,
					Inbox:       "agent/" + agentID,
					Online:      true,
					LastSeen:    time.Unix(1700000000, 0).UTC(),
					Metadata: core.AgentMetadata{
						Harness:     "codex",
						Project:     "cairn",
						TaskSummary: strings.Repeat("task summary detail ", 15),
					},
				})
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": core.AgentDirectoryPage{Agents: agents}})
		case "/v1/event-inspect":
			deliveries := make([]core.AgentDelivery, 0, 50)
			for range 50 {
				deliveries = append(deliveries, core.AgentDelivery{
					DeliveryID: uuid.NewString(),
					Consumer:   "agent/" + agentID,
					State:      "completed",
					Code:       "delivery_diagnosis_line_" + strings.Repeat("d", 220),
					Diagnosis: &core.DeliveryDiagnosis{
						Stage:      "handled",
						Recipient:  core.DiagnosisRecipient{Kind: "agent", AgentID: agentID, ExecutionID: execID},
						ComputedAt: time.Unix(1700000000, 0).UTC(),
					},
				})
			}
			status := core.EventStatus{
				Event: core.AgentEvent{
					EventID:     eventID,
					Kind:        "request",
					From:        "agent/" + agentID,
					CreatedAt:   time.Unix(1700000000, 0).UTC(),
					Ref:         core.RecordVersionRef{RecordID: recordID, Version: 1},
					Destination: core.EventDestination{Type: "agent", Name: "agent/" + agentID},
				},
				Deliveries: deliveries,
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": status})
		case "/v1/history":
			body := strings.Repeat("h", 6000)
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": map[string]any{
				"historical": true, "record_id": recordID, "current_version": 1,
				"versions": []any{map[string]any{"version": 1, "body": body, "payload_available": true}},
			}})
		default:
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
	})

	boundedCfg := Config{
		Scope:           core.Scope{Repo: "repo", TaskID: "task", RunID: "run"},
		AvailableTokens: 16000,
		MaxTokens:       160000,
		OutputMaxBytes:  4096,
		ToolOutputDir:   spool,
		SearchView:      "compact/1",
		Messaging:       true,
		NativeSessionID: "ses_test",
		HostAncestors:   func() ([]HostProcess, error) { return nil, nil },
	}
	server, err := NewServer(client, boundedCfg)
	if err != nil {
		t.Fatal(err)
	}
	session := connect(t, context.Background(), server)

	checkBounded := func(toolName string, args map[string]any, wantSnippets ...string) {
		t.Helper()
		res, callErr := session.CallTool(context.Background(), &mcp.CallToolParams{Name: toolName, Arguments: args})
		if callErr != nil {
			t.Fatalf("%s: %v", toolName, callErr)
		}
		if res.IsError {
			t.Fatalf("%s returned error: %s", toolName, res.Content[0].(*mcp.TextContent).Text)
		}
		wire, _ := json.Marshal(res)
		if len(wire) > 4096 {
			t.Fatalf("%s wire length %d > 4096", toolName, len(wire))
		}
		var out boundedToolPayload
		text := res.Content[0].(*mcp.TextContent).Text
		if err := json.Unmarshal([]byte(text), &out); err != nil {
			t.Fatalf("%s invalid JSON %s: %v", toolName, text, err)
		}
		if !out.Truncated || out.TotalBytes <= 4096 || out.OmittedBytes <= 0 || out.FullOutputPath == "" {
			t.Fatalf("%s missing truncation metadata: %+v", toolName, out)
		}
		info, err := os.Stat(out.FullOutputPath)
		if err != nil || info.Mode().Perm() != 0o600 {
			t.Fatalf("%s spool permissions/stat: %v %v", toolName, info, err)
		}
		fullBytes, err := os.ReadFile(out.FullOutputPath)
		if err != nil {
			t.Fatal(err)
		}
		if !json.Valid(fullBytes) {
			t.Fatalf("%s spooled file is not valid JSON", toolName)
		}
		for _, snippet := range wantSnippets {
			if !strings.Contains(string(fullBytes), snippet) {
				t.Fatalf("%s spooled file missing %q", toolName, snippet)
			}
		}
	}

	checkBounded("cairn_assessments", map[string]any{"receipt_id": receiptID}, "HEAD_REASON", "TAIL_REASON")
	checkBounded("cairn_pull_evidence", map[string]any{
		"request_id":      uuid.NewString(),
		"receipt_id":      receiptID,
		"handle":          handleID,
		"evidence_id":     evidenceID,
		"expected_sha256": strings.Repeat("a", 64),
	}, "EVIDENCE_HEAD", "EVIDENCE_TAIL")
	checkBounded("cairn_agents", map[string]any{"limit": 60}, "task summary detail")
	checkBounded("cairn_event_status", map[string]any{"event_id": eventID, "agent_id": agentID, "execution_id": execID}, "delivery_diagnosis_line")

	// Verify strict retrieval keeps BUDGET_REFUSED on cairn_history.
	histRes, err := session.CallTool(context.Background(), &mcp.CallToolParams{
		Name:      "cairn_history",
		Arguments: map[string]any{"record_id": recordID, "version": 1, "available_tokens": 2000, "memory_budget_bytes": 2000},
	})
	if err != nil || !histRes.IsError || !strings.Contains(histRes.Content[0].(*mcp.TextContent).Text, "BUDGET_REFUSED") {
		t.Fatalf("cairn_history should remain strict on BUDGET_REFUSED: %+v %v", histRes, err)
	}

	// Verify explicit UnboundedOutput opt-out returns complete payload.
	unboundedCfg := boundedCfg
	unboundedCfg.OutputMaxBytes = 0
	unboundedCfg.UnboundedOutput = true
	unboundedServer, err := NewServer(client, unboundedCfg)
	if err != nil {
		t.Fatal(err)
	}
	unboundedSession := connect(t, context.Background(), unboundedServer)
	fullRes, err := unboundedSession.CallTool(context.Background(), &mcp.CallToolParams{
		Name:      "cairn_assessments",
		Arguments: map[string]any{"receipt_id": receiptID},
	})
	if err != nil || fullRes.IsError {
		t.Fatalf("unbounded opt-out failed: %+v %v", fullRes, err)
	}
	var gotAssessments []core.Assessment
	if err := json.Unmarshal([]byte(fullRes.Content[0].(*mcp.TextContent).Text), &gotAssessments); err != nil || len(gotAssessments) != 1 || gotAssessments[0].Reason != largeReason {
		t.Fatalf("opt-out did not return full assessments payload: %v", err)
	}

	// Verify CAIRN_UNBOUNDED_TOOL_OUTPUT=1 also opts out.
	t.Setenv("CAIRN_UNBOUNDED_TOOL_OUTPUT", "1")
	envOptOutServer, err := NewServer(client, boundedCfg)
	if err != nil {
		t.Fatal(err)
	}
	envSession := connect(t, context.Background(), envOptOutServer)
	envRes, err := envSession.CallTool(context.Background(), &mcp.CallToolParams{
		Name:      "cairn_assessments",
		Arguments: map[string]any{"receipt_id": receiptID},
	})
	if err != nil || envRes.IsError || !strings.Contains(envRes.Content[0].(*mcp.TextContent).Text, "TAIL_REASON: final review conclusion") {
		t.Fatalf("CAIRN_UNBOUNDED_TOOL_OUTPUT=1 opt-out failed: %+v %v", envRes, err)
	}
}
