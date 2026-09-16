package wakeup

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestAgyLatestStepControlsRateLimitObservation(t *testing.T) {
	var failure *core.ProviderFailure
	s := newProviderStream("agy", func(f *core.ProviderFailure) error { failure = f; return nil })
	write := func(line string) {
		t.Helper()
		if _, err := s.Write([]byte(line + "\n")); err != nil {
			t.Fatal(err)
		}
	}
	write(`{"event":"init","conversation_id":"fixture-conversation"}`)
	write(`{"event":"step_update","step_update":{"conversation_id":"fixture-conversation","step_index":1,"state":"DONE","step_type":"error_message"}}`)
	result := `{"event":"result","result":{"conversation_id":"fixture-conversation","status":"ERROR","error":"API error (attempt 2): Error 429, Message: Resource has been exhausted (e.g. check quota)., Status: RESOURCE_EXHAUSTED, Details: []"}}`
	write(result)
	if failure == nil || failure.Harness != "agy" || failure.Source != "native-diagnostic" || failure.Kind != "rate_limit" || failure.Code != "agy_http_429" || failure.Status != 429 {
		t.Fatalf("missing native rate-limit observation: %+v", failure)
	}
	// Agy retains the previous error in its result even after a successful model
	// response. Step ordering, rather than result.status alone, proves recovery.
	write(`{"event":"step_update","step_update":{"conversation_id":"fixture-conversation","step_index":2,"state":"DONE","step_type":"agent_response"}}`)
	write(result)
	if failure != nil {
		t.Fatalf("stale result error suspended recovered account: %+v", failure)
	}
	write(`{"event":"step_update","step_update":{"conversation_id":"fixture-conversation","step_index":1,"state":"DONE","step_type":"error_message"}}`)
	write(result)
	if failure != nil {
		t.Fatal("out-of-order old error revived the candidate")
	}
	write(`{"event":"init","conversation_id":"child"}`)
	write(`{"event":"step_update","step_update":{"conversation_id":"child","step_index":10,"state":"DONE","step_type":"error_message"}}`)
	write(strings.ReplaceAll(result, "fixture-conversation", "child"))
	if failure != nil {
		t.Fatal("child conversation replaced the native stream owner")
	}
	write(`{"event":"step_update","step_update":{"conversation_id":"fixture-conversation","step_index":3,"state":"DONE","step_type":"error_message"}}`)
	write(result)
	if failure == nil {
		t.Fatal("later rate limit after recovery was lost")
	}
	write(strings.ReplaceAll(result, "Error 429", "Error 500"))
	if failure != nil {
		t.Fatal("different result failure did not clear rate limit")
	}
}

func TestAgyRateLimitRequiresCurrentNativeDiagnostic(t *testing.T) {
	const diagnostic = "API error (attempt 2): Error 429, Message: Resource has been exhausted (e.g. check quota)., Status: RESOURCE_EXHAUSTED, Details: []"
	for _, test := range []struct {
		name, message, status, session string
		steps                          string
		want                           bool
	}{
		{"native retry", diagnostic, "ERROR", "native", `{"step_index":2,"state":"DONE","step_type":"error_message"}`, true},
		{"different failure", "agent executor error: generating and executing: Error 400, Message: quota 429 RESOURCE_EXHAUSTED, Status: INVALID_ARGUMENT, Details: []", "ERROR", "native", `{"step_index":2,"state":"DONE","step_type":"error_message"}`, false},
		{"quoted diagnostic", "tool returned: " + diagnostic, "ERROR", "native", `{"step_index":2,"state":"DONE","step_type":"error_message"}`, false},
		{"successful result", diagnostic, "SUCCESS", "native", `{"step_index":2,"state":"DONE","step_type":"error_message"}`, false},
		{"foreign conversation", diagnostic, "ERROR", "child", `{"step_index":2,"state":"DONE","step_type":"error_message"}`, false},
		{"running step", diagnostic, "ERROR", "native", `{"step_index":2,"state":"RUNNING","step_type":"error_message"}`, false},
		{"missing index", diagnostic, "ERROR", "native", `{"state":"DONE","step_type":"error_message"}`, false},
		{"tool text", diagnostic, "ERROR", "native", `{"step_index":2,"state":"DONE","step_type":"tool_result"}`, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			var failure *core.ProviderFailure
			s := newProviderStream("agy", func(f *core.ProviderFailure) error { failure = f; return nil })
			write := func(value any) {
				t.Helper()
				data, err := json.Marshal(value)
				if err != nil {
					t.Fatal(err)
				}
				if _, err := s.Write(append(data, '\n')); err != nil {
					t.Fatal(err)
				}
			}
			write(map[string]any{"event": "init", "conversation_id": "native"})
			var step map[string]any
			if err := json.Unmarshal([]byte(test.steps), &step); err != nil {
				t.Fatal(err)
			}
			step["conversation_id"] = "native"
			step["text_delta"] = diagnostic // Never classify arbitrary step text.
			write(map[string]any{"event": "step_update", "step_update": step})
			if failure != nil {
				t.Fatal("step text classified as provider failure")
			}
			write(map[string]any{"event": "result", "result": map[string]string{"conversation_id": test.session, "status": test.status, "error": test.message}})
			if (failure != nil) != test.want {
				t.Fatalf("failure=%+v, want classification=%v", failure, test.want)
			}
		})
	}
}

func TestCodexSubscriptionLimitAndRecovery(t *testing.T) {
	// Captured from the installed executable against a loopback provider returning
	// usage_limit_reached. Plan and reset-time variants change the native wording.
	messages := []string{
		"You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Dec 31st, 2029 4:00 PM.",
		"You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again later.",
		"You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Dec 31st, 2029 4:00 PM.",
		"You've hit your usage limit. To get more access now, send a request to your admin or try again at Dec 31st, 2029 4:00 PM.",
		"You've hit your usage limit. Upgrade to Plus to continue using Codex (https://chatgpt.com/explore/plus), or try again at Dec 31st, 2029 4:00 PM.",
	}
	for _, message := range messages {
		t.Run(message, func(t *testing.T) {
			var observed []*core.ProviderFailure
			s := newProviderStream("codex", func(f *core.ProviderFailure) error {
				observed = append(observed, f)
				return nil
			})
			write := func(value any) {
				t.Helper()
				data, err := json.Marshal(value)
				if err != nil {
					t.Fatal(err)
				}
				if _, err = s.Write(append(data, '\n')); err != nil {
					t.Fatal(err)
				}
			}
			write(map[string]any{"type": "error", "message": message})
			write(map[string]any{"type": "item.completed", "item": map[string]string{"type": "command_execution", "aggregated_output": message}})
			if len(observed) != 0 {
				t.Fatal("intermediate or tool text classified as quota")
			}
			write(map[string]any{"type": "turn.failed", "error": map[string]string{"message": message}})
			if len(observed) != 1 || observed[0] == nil || observed[0].Harness != "codex" || observed[0].Source != "native-diagnostic" || observed[0].Code != "codex_usage_limit_reached" || observed[0].Kind != "quota" || observed[0].RetryAt != nil {
				t.Fatalf("subscription quota not retained without guessing a reset instant: %+v", observed)
			}
			write(map[string]string{"type": "turn.completed"})
			if len(observed) != 2 || observed[1] != nil {
				t.Fatal("successful retry did not clear candidate")
			}
			write(map[string]any{"type": "turn.failed", "error": map[string]string{"message": "You've hit your usage limit. An unrecognized diagnostic."}})
			if len(observed) != 2 {
				t.Fatal("unrecognized wording classified")
			}
		})
	}
}

func TestCodexStreamExactDiagnostics(t *testing.T) {
	var observed []*core.ProviderFailure
	record := func(f *core.ProviderFailure) error {
		observed = append(observed, f)
		return nil
	}

	// 1. Positive: Quota exceeded
	s := newProviderStream("codex", record)
	lineQuota := `{"type":"turn.failed","error":{"message":"Quota exceeded. Check your plan and billing details."}}` + "\n"
	if _, err := s.Write([]byte(lineQuota)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 1 || observed[0] == nil {
		t.Fatalf("expected 1 failure observation, got %+v", observed)
	}
	if observed[0].Harness != "codex" || observed[0].Source != "native-diagnostic" || observed[0].Kind != "quota" || observed[0].Code != "codex_quota_exceeded" {
		t.Fatalf("unexpected codex quota failure: %+v", observed[0])
	}

	// 2. Positive: 429 Too Many Requests
	observed = nil
	s = newProviderStream("codex", record)
	line429 := `{"type":"turn.failed","error":{"message":"exceeded retry limit, last status: 429 Too Many Requests"}}` + "\n"
	if _, err := s.Write([]byte(line429)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 1 || observed[0] == nil {
		t.Fatalf("expected 1 failure observation, got %+v", observed)
	}
	if observed[0].Harness != "codex" || observed[0].Source != "native-diagnostic" || observed[0].Kind != "rate_limit" || observed[0].Code != "codex_http_429" || observed[0].Status != 429 {
		t.Fatalf("unexpected codex 429 failure: %+v", observed[0])
	}

	// 3. Negative: Non-terminal type: "error" (should not classify)
	observed = nil
	s = newProviderStream("codex", record)
	lineError := `{"type":"error","message":"exceeded retry limit, last status: 429 Too Many Requests"}` + "\n"
	if _, err := s.Write([]byte(lineError)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 0 {
		t.Fatalf("non-terminal error should not classify: %+v", observed)
	}

	// 4. Negative: 401 Unauthorized
	observed = nil
	line401 := `{"type":"turn.failed","error":{"message":"unexpected status 401 Unauthorized"}}` + "\n"
	if _, err := s.Write([]byte(line401)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 0 {
		t.Fatalf("auth error should not classify as quota: %+v", observed)
	}

	// 5. Negative: 500 High demand
	observed = nil
	line500 := `{"type":"turn.failed","error":{"message":"We're currently experiencing high demand, which may cause temporary errors."}}` + "\n"
	if _, err := s.Write([]byte(line500)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 0 {
		t.Fatalf("server 500 error should not classify as quota: %+v", observed)
	}

	// 6. Negative: Substring/broad matching check
	observed = nil
	lineSub := `{"type":"turn.failed","error":{"message":"Quota exceeded but not the exact string"}}` + "\n"
	if _, err := s.Write([]byte(lineSub)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 0 {
		t.Fatalf("broad substring match must not classify: %+v", observed)
	}

	// 7. Negative: Tool output containing diagnostic text
	observed = nil
	lineTool := `{"type":"item.completed","item":{"type":"custom_tool_call_output","output":"exceeded retry limit, last status: 429 Too Many Requests"}}` + "\n"
	if _, err := s.Write([]byte(lineTool)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 0 {
		t.Fatalf("tool output must never classify as provider failure: %+v", observed)
	}
}

func TestClaudeStreamRetryRecoveryAndLater500(t *testing.T) {
	var observed []*core.ProviderFailure
	record := func(f *core.ProviderFailure) error {
		observed = append(observed, f)
		return nil
	}

	s := newProviderStream("claude", record)

	// Step 1: Candidate 429 retry arrives -> persists candidate
	retry429 := `{"type":"system","subtype":"api_retry","attempt":1,"max_retries":10,"retry_delay_ms":500,"error_status":429,"error":"rate_limit"}` + "\n"
	if _, err := s.Write([]byte(retry429)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 1 || observed[0] == nil || observed[0].Code != "claude_rate_limit" || observed[0].Status != 429 {
		t.Fatalf("step 1 failed: %+v", observed)
	}

	// Step 2: Retry recovery: a successful non-error assistant message arrives -> candidate cleared
	assistantOk := `{"type":"assistant","message":{"id":"msg_1","role":"assistant","content":[]},"is_api_error_message":false}` + "\n"
	if _, err := s.Write([]byte(assistantOk)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 2 || observed[1] != nil {
		t.Fatalf("step 2 recovery failed to clear: %+v", observed)
	}

	// Step 3: Another candidate arrives
	if _, err := s.Write([]byte(retry429)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 3 || observed[2] == nil {
		t.Fatalf("step 3 candidate failed: %+v", observed)
	}

	// Step 4: Later 500 server error arrives -> candidate cleared
	retry500 := `{"type":"system","subtype":"api_retry","attempt":2,"max_retries":10,"retry_delay_ms":1000,"error_status":500,"error":"server_error"}` + "\n"
	if _, err := s.Write([]byte(retry500)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 4 || observed[3] != nil {
		t.Fatalf("step 4 later 500 failed to clear candidate: %+v", observed)
	}
}

func TestClaudeStreamTerminalApiErrorAndSuccessResult(t *testing.T) {
	var observed []*core.ProviderFailure
	record := func(f *core.ProviderFailure) error {
		observed = append(observed, f)
		return nil
	}

	// Case A: api_retry 429 followed by terminal api_error -> retained!
	s := newProviderStream("claude", record)
	retry := `{"type":"system","subtype":"api_retry","attempt":1,"max_retries":10,"retry_delay_ms":500,"error_status":429,"error":"rate_limit"}` + "\n"
	assistantErr := `{"type":"assistant","message":{"id":"msg_2"},"is_api_error_message":true}` + "\n"
	terminalApiErr := `{"type":"result","subtype":"success","is_error":true,"terminal_reason":"api_error","result":"Rate limit reached"}` + "\n"

	for _, line := range []string{retry, assistantErr, terminalApiErr} {
		if _, err := s.Write([]byte(line)); err != nil {
			t.Fatal(err)
		}
	}
	if len(observed) != 1 || observed[0] == nil || observed[0].Code != "claude_rate_limit" {
		t.Fatalf("terminal api_error should retain candidate: %+v", observed)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}

	// Case B: api_retry 429 followed by successful result -> cleared!
	observed = nil
	s = newProviderStream("claude", record)
	terminalSuccess := `{"type":"result","subtype":"success","is_error":false,"terminal_reason":"end_turn","result":"Task finished"}` + "\n"

	for _, line := range []string{retry, terminalSuccess} {
		if _, err := s.Write([]byte(line)); err != nil {
			t.Fatal(err)
		}
	}
	if len(observed) != 2 || observed[0] == nil || observed[1] != nil {
		t.Fatalf("successful result should clear candidate: %+v", observed)
	}
}

func TestClaudeToolOutputIgnored(t *testing.T) {
	var observed []*core.ProviderFailure
	record := func(f *core.ProviderFailure) error {
		observed = append(observed, f)
		return nil
	}

	s := newProviderStream("claude", record)
	toolResult := `{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_1","content":"api_retry error: rate_limit 429","is_error":true}]}}` + "\n"
	if _, err := s.Write([]byte(toolResult)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 0 {
		t.Fatalf("tool result must never trigger provider failure: %+v", observed)
	}
}

func TestOpenCodeStreamAPIError(t *testing.T) {
	var observed []*core.ProviderFailure
	record := func(f *core.ProviderFailure) error {
		observed = append(observed, f)
		return nil
	}

	s := newProviderStream("opencode", record)

	// 1. Rate limit 429
	line429 := `{"type":"error","error":{"name":"APIError","data":{"message":"Rate limit exceeded: 429","statusCode":429,"isRetryable":true,"responseHeaders":{"content-type":"application/json"},"responseBody":"{}","metadata":{}}}}` + "\n"
	if _, err := s.Write([]byte(line429)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 1 || observed[0] == nil {
		t.Fatalf("expected opencode 429 observation: %+v", observed)
	}
	if observed[0].Harness != "opencode" || observed[0].Source != "native-event" || observed[0].Kind != "rate_limit" || observed[0].Code != "opencode_http_429" || observed[0].Status != 429 {
		t.Fatalf("unexpected opencode 429 failure: %+v", observed[0])
	}

	// Idle describes the session lifecycle, not a successful provider retry.
	lineComplete := `{"type":"session.idle"}` + "\n"
	if _, err := s.Write([]byte(lineComplete)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 1 || observed[0] == nil {
		t.Fatalf("idle must not clear a failed provider request: %+v", observed)
	}

	// HTTP 402 reports billing, without proving a particular quota type.
	line402 := `{"type":"error","error":{"name":"APIError","data":{"message":"Payment Required","statusCode":402,"isRetryable":false}}}` + "\n"
	if _, err := s.Write([]byte(line402)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 2 || observed[1] == nil || observed[1].Kind != "billing" || observed[1].Code != "opencode_http_402" || observed[1].Status != 402 {
		t.Fatalf("expected opencode 402 billing failure: %+v", observed)
	}

	// 4. Non-quota error (500)
	observed = nil
	s = newProviderStream("opencode", record)
	line500 := `{"type":"error","error":{"name":"APIError","data":{"message":"Internal Server Error","statusCode":500,"isRetryable":true}}}` + "\n"
	if _, err := s.Write([]byte(line500)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 0 {
		t.Fatalf("opencode 500 should not classify as provider quota/rate_limit: %+v", observed)
	}
}

func TestOversizedToolLineThenTerminalFailure(t *testing.T) {
	var observed []*core.ProviderFailure
	record := func(f *core.ProviderFailure) error {
		observed = append(observed, f)
		return nil
	}

	s := newProviderStream("codex", record)

	// Create a line of 1.5 MiB (larger than maxLineBytes = 1 MiB)
	oversized := []byte(`{"type":"item.completed","output":"` + strings.Repeat("A", 1500000) + `"}` + "\n")
	terminalFailure := []byte(`{"type":"turn.failed","error":{"message":"Quota exceeded. Check your plan and billing details."}}` + "\n")

	// Write oversized line in chunks
	chunkSize := 64 * 1024
	for i := 0; i < len(oversized); i += chunkSize {
		end := min(i+chunkSize, len(oversized))
		if _, err := s.Write(oversized[i:end]); err != nil {
			t.Fatalf("write oversized chunk failed: %v", err)
		}
	}

	if len(observed) != 0 {
		t.Fatalf("oversized line should be discarded, but observed: %+v", observed)
	}

	// Now write the terminal failure line
	if _, err := s.Write(terminalFailure); err != nil {
		t.Fatalf("write terminal failure failed: %v", err)
	}

	if len(observed) != 1 || observed[0] == nil || observed[0].Code != "codex_quota_exceeded" {
		t.Fatalf("terminal failure after oversized line was not classified: %+v", observed)
	}
}

func TestChunkBoundaries(t *testing.T) {
	var observed []*core.ProviderFailure
	record := func(f *core.ProviderFailure) error {
		observed = append(observed, f)
		return nil
	}

	s := newProviderStream("claude", record)
	fullLine := `{"type":"system","subtype":"api_retry","attempt":1,"max_retries":10,"retry_delay_ms":500,"error_status":429,"error":"rate_limit"}` + "\n"

	// Feed 1 byte at a time
	for i := 0; i < len(fullLine); i++ {
		if _, err := s.Write([]byte{fullLine[i]}); err != nil {
			t.Fatal(err)
		}
	}

	if len(observed) != 1 || observed[0] == nil || observed[0].Code != "claude_rate_limit" {
		t.Fatalf("1-byte chunking failed: %+v", observed)
	}
}

func TestTrailingLineWithoutNewlineOnClose(t *testing.T) {
	var observed []*core.ProviderFailure
	record := func(f *core.ProviderFailure) error {
		observed = append(observed, f)
		return nil
	}

	s := newProviderStream("codex", record)
	lineWithoutNewline := `{"type":"turn.failed","error":{"message":"Quota exceeded. Check your plan and billing details."}}`

	if _, err := s.Write([]byte(lineWithoutNewline)); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 0 {
		t.Fatalf("line without newline should not be parsed before Close: %+v", observed)
	}

	if err := s.Close(); err != nil {
		t.Fatalf("Close failed: %v", err)
	}
	if len(observed) != 1 || observed[0] == nil || observed[0].Code != "codex_quota_exceeded" {
		t.Fatalf("trailing line should be parsed on Close: %+v", observed)
	}
}

func TestCallbackFailures(t *testing.T) {
	expectedErr := errors.New("simulated durable observation failure")
	failCallback := func(f *core.ProviderFailure) error {
		return expectedErr
	}

	s := newProviderStream("codex", failCallback)
	line := `{"type":"turn.failed","error":{"message":"Quota exceeded. Check your plan and billing details."}}` + "\n"

	n, err := s.Write([]byte(line))
	if !errors.Is(err, expectedErr) {
		t.Fatalf("expected Write to return callback error, got n=%d err=%v", n, err)
	}

	// Subsequent Write should immediately return the retained error
	_, err = s.Write([]byte("some more text\n"))
	if !errors.Is(err, expectedErr) {
		t.Fatalf("expected subsequent Write to return retained error, got %v", err)
	}

	// Close should return the retained error
	if err = s.Close(); !errors.Is(err, expectedErr) {
		t.Fatalf("expected Close to return retained error, got %v", err)
	}

	// Test callback failure on trailing line in Close()
	s2 := newProviderStream("codex", failCallback)
	lineTrailing := `{"type":"turn.failed","error":{"message":"Quota exceeded. Check your plan and billing details."}}`
	if _, err = s2.Write([]byte(lineTrailing)); err != nil {
		t.Fatal(err)
	}
	if err = s2.Close(); !errors.Is(err, expectedErr) {
		t.Fatalf("expected Close() on trailing line to return callback error, got %v", err)
	}
}

func TestMalformedAndUnrelatedLines(t *testing.T) {
	var observed []*core.ProviderFailure
	record := func(f *core.ProviderFailure) error {
		observed = append(observed, f)
		return nil
	}

	s := newProviderStream("codex", record)
	for _, raw := range []string{
		"not json at all\n",
		"{broken json\n",
		"12345\n",
		`{"type":"session.created","id":"ses_1"}` + "\n",
		`{"random":"field"}` + "\n",
		"\n",
		"\r\n",
	} {
		if _, err := s.Write([]byte(raw)); err != nil {
			t.Fatalf("writing %q caused unexpected error: %v", raw, err)
		}
	}
	if len(observed) != 0 {
		t.Fatalf("malformed/unrelated lines should produce zero observations: %+v", observed)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
}
