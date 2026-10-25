package siwc

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type fakeCairnTools struct {
	searches, pulls int
	lastSearch      json.RawMessage
}

func (f *fakeCairnTools) Search(_ context.Context, args json.RawMessage) (json.RawMessage, error) {
	f.searches++
	if !json.Valid(args) {
		return nil, errors.New("invalid search arguments")
	}
	f.lastSearch = append(f.lastSearch[:0], args...)
	return json.RawMessage(`{"receipt_id":"receipt-1","index":[]}`), nil
}
func (f *fakeCairnTools) Pull(_ context.Context, args json.RawMessage) (json.RawMessage, error) {
	f.pulls++
	if !json.Valid(args) {
		return nil, errors.New("invalid pull arguments")
	}
	return json.RawMessage(`{"record":{"body":"selected source"}}`), nil
}

func TestDirectResponsesRunsOnlyCairnSearchPullAndReportsUsageAndIDs(t *testing.T) {
	tools := &fakeCairnTools{}
	responseRequests := 0
	client := validResponseClient(t, tools, func(req *http.Request, body map[string]any) (*http.Response, error) {
		responseRequests++
		if body["model"] != "gpt-6-luna" || body["store"] != false || body["stream"] != true {
			t.Fatalf("wrong direct Responses contract: %#v", body)
		}
		if body["previous_response_id"] != nil {
			t.Fatal("direct route must send explicit input history")
		}
		if body["reasoning"].(map[string]any)["effort"] != "low" {
			t.Fatalf("reasoning effort was not frozen: %#v", body["reasoning"])
		}
		catalog := body["tools"].([]any)
		if len(catalog) != 1 {
			t.Fatalf("tool namespace count = %d", len(catalog))
		}
		namespace := catalog[0].(map[string]any)
		if namespace["type"] != "namespace" || namespace["name"] != "cairn" {
			t.Fatalf("wrong namespace: %#v", namespace)
		}
		functions := namespace["tools"].([]any)
		if len(functions) != 2 {
			t.Fatalf("function count = %d", len(functions))
		}
		got := []string{functions[0].(map[string]any)["name"].(string), functions[1].(map[string]any)["name"].(string)}
		if strings.Join(got, ",") != "cairn_search,cairn_pull" {
			t.Fatalf("unexpected offered functions: %v", got)
		}
		pullParameters := functions[1].(map[string]any)["parameters"].(map[string]any)
		pullProperties := pullParameters["properties"].(map[string]any)
		if _, ok := pullProperties["span"]; !ok {
			t.Fatal("pull schema dropped optional source span")
		}
		if _, ok := pullProperties["opening_bytes"]; !ok {
			t.Fatal("pull schema dropped optional source opening")
		}
		if responseRequests == 1 {
			return streamResponse(t, "resp-search", []any{
				map[string]any{"id": "msg-interim", "type": "message", "role": "assistant", "content": []any{map[string]any{"type": "output_text", "text": "interim must not be counted"}}},
				map[string]any{"id": "fc-1", "type": "function_call", "namespace": "cairn", "call_id": "call-1", "name": "cairn_search", "arguments": "{\"query\":\"topic\",\"semantic\":true}"},
			}, 3, 2, 5, 1, 1), nil
		}
		requestBody, _ := json.Marshal(body)
		if !strings.Contains(string(requestBody), `"call_id":"call-1"`) || !strings.Contains(string(requestBody), `"type":"function_call_output"`) {
			t.Fatalf("follow-up omitted function exchange")
		}
		return streamResponse(t, "resp-final", []any{map[string]any{"id": "msg-final", "type": "message", "role": "assistant", "content": []any{map[string]any{"type": "output_text", "text": "accepted answer"}}}}, 7, 4, 11, 2, 3), nil
	})
	result, err := client.Run(context.Background(), "gpt-6-luna", "answer from indexed sources", RunOptions{ReasoningEffort: "low", UseCairnTools: true})
	if err != nil {
		t.Fatal(err)
	}
	if result.Text != "accepted answer" {
		t.Fatalf("final answer %q", result.Text)
	}
	if result.ServedModel != "gpt-6-luna" {
		t.Fatalf("served model evidence %q", result.ServedModel)
	}
	if strings.Join(result.ResponseIDs, ",") != "resp-search,resp-final" {
		t.Fatalf("response IDs %v", result.ResponseIDs)
	}
	if result.Usage.InputTokens != 10 || result.Usage.OutputTokens != 6 || result.Usage.TotalTokens != 16 {
		t.Fatalf("usage totals %+v", result.Usage)
	}
	if result.Usage.CachedInputTokens == nil || *result.Usage.CachedInputTokens != 3 || result.Usage.ReasoningTokens == nil || *result.Usage.ReasoningTokens != 4 {
		t.Fatalf("detailed usage %+v", result.Usage)
	}
	if tools.searches != 1 || tools.pulls != 0 || responseRequests != 2 {
		t.Fatalf("tool/response counts: %+v requests=%d", tools, responseRequests)
	}
	if !strings.Contains(string(tools.lastSearch), `"semantic":true`) {
		t.Fatalf("semantic-unavailable test argument was dropped: %s", tools.lastSearch)
	}
}

func TestNoToolsArmOmitsAllFunctionsAndRequiresFinalAnswer(t *testing.T) {
	client := validResponseClient(t, nil, func(_ *http.Request, body map[string]any) (*http.Response, error) {
		if _, ok := body["tools"]; ok {
			t.Fatal("no-tools arm included tools")
		}
		return streamResponse(t, "resp-no-tools", []any{map[string]any{"id": "msg", "type": "message", "role": "assistant", "content": []any{map[string]any{"type": "output_text", "text": "direct answer"}}}}, 1, 1, 2, -1, -1), nil
	})
	result, err := client.Run(context.Background(), "gpt-6-luna", "answer directly", RunOptions{ReasoningEffort: "none"})
	if err != nil {
		t.Fatal(err)
	}
	if result.Text != "direct answer" || result.Usage.CachedInputTokens != nil || result.Usage.ReasoningTokens != nil {
		t.Fatalf("unexpected direct result: %+v", result)
	}

	empty := validResponseClient(t, nil, func(_ *http.Request, body map[string]any) (*http.Response, error) {
		return streamResponse(t, "resp-empty", []any{map[string]any{"id": "msg", "type": "message", "role": "assistant", "content": []any{}}}, 1, 1, 2, -1, -1), nil
	})
	if _, err := empty.Run(context.Background(), "gpt-6-luna", "answer", RunOptions{ReasoningEffort: "none"}); err == nil || !strings.Contains(err.Error(), "empty final answer") {
		t.Fatalf("empty answer accepted: %v", err)
	}
}

func TestResponsesRedirectNeverResendsPromptBody(t *testing.T) {
	client := validResponseClient(t, nil, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		return &http.Response{StatusCode: http.StatusTemporaryRedirect, Header: http.Header{"Location": []string{"https://attacker.example/collect"}}, Body: io.NopCloser(strings.NewReader(""))}, nil
	})
	var urls []string
	base := client.httpClient.Transport
	client.httpClient.Transport = roundTripFunc(func(req *http.Request) (*http.Response, error) {
		urls = append(urls, req.URL.String())
		return base.RoundTrip(req)
	})
	if _, err := client.Run(context.Background(), "gpt-6-luna", "private prompt", RunOptions{ReasoningEffort: "low"}); err == nil {
		t.Fatal("redirect response accepted")
	}
	if len(urls) != 2 || urls[1] != ResponsesEndpoint {
		t.Fatalf("request body was redirected: %v", urls)
	}
}

func TestResponsesRejectsUnresolvedModelAlias(t *testing.T) {
	client := validResponseClient(t, nil, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		response := streamResponse(t, "resp-alias", []any{map[string]any{"id": "msg", "type": "message", "content": []any{map[string]any{"type": "output_text", "text": "answer"}}}}, 1, 1, 2, -1, -1)
		body, _ := io.ReadAll(response.Body)
		response.Body.Close()
		return &http.Response{StatusCode: http.StatusOK, Header: response.Header, Body: io.NopCloser(strings.NewReader(strings.Replace(string(body), `"model":"gpt-6-luna"`, `"model":"unmapped-alias"`, 1)))}, nil
	})
	if _, err := client.Run(context.Background(), "gpt-6-luna", "answer", RunOptions{ReasoningEffort: "low"}); err == nil || !strings.Contains(err.Error(), "does not match requested model") {
		t.Fatalf("unresolved served-model alias accepted: %v", err)
	}
}

func TestFunctionArgumentsAreValidatedBeforeCairnDispatch(t *testing.T) {
	for name, call := range map[string]struct{ function, arguments string }{
		"unknown-search-field": {"cairn_search", `{"query":"q","extra":true}`},
		"wrong-semantic-type":  {"cairn_search", `{"query":"q","semantic":"yes"}`},
		"missing-pull-id":      {"cairn_pull", `{"request_id":"r","receipt_id":"p"}`},
		"invalid-span":         {"cairn_pull", `{"request_id":"r","receipt_id":"p","handle":"h","span":{"offset":0,"length":0}}`},
		"invalid-opening":      {"cairn_pull", `{"request_id":"r","receipt_id":"p","handle":"h","span":{"offset":0,"length":1},"opening_bytes":769}`},
	} {
		t.Run(name, func(t *testing.T) {
			tools := &fakeCairnTools{}
			client := validResponseClient(t, tools, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
				return streamResponse(t, "resp-invalid-args", []any{map[string]any{"id": "fc", "type": "function_call", "namespace": "cairn", "call_id": "call", "name": call.function, "arguments": call.arguments}}, 1, 1, 2, -1, -1), nil
			})
			if _, err := client.Run(context.Background(), "gpt-6-luna", "query", RunOptions{ReasoningEffort: "low", UseCairnTools: true}); err == nil {
				t.Fatal("invalid tool arguments accepted")
			}
			if tools.searches != 0 || tools.pulls != 0 {
				t.Fatalf("invalid arguments reached Cairn: %+v", tools)
			}
		})
	}
}

func TestModelCatalogAndCredentialsFailClosedWithoutFallback(t *testing.T) {
	requests := 0
	client := validResponseClient(t, &fakeCairnTools{}, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		requests++
		return streamResponse(t, "resp", []any{map[string]any{"id": "msg", "type": "message", "role": "assistant", "content": []any{map[string]any{"type": "output_text", "text": "should not run"}}}}, 1, 1, 2, -1, -1), nil
	})
	client.httpClient = &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
		if req.URL.Host != "api.openai.com" || req.URL.Path != "/v1/models" {
			t.Fatalf("unexpected/fallback endpoint: %s", req.URL)
		}
		if req.Method != http.MethodGet || req.Header.Get("Authorization") != "Bearer access-in-memory" {
			t.Fatalf("bad catalog request: %#v", req)
		}
		return jsonResponse(http.StatusOK, map[string]any{"models": []any{map[string]any{"slug": "gpt-6-luna", "visibility": "list"}, map[string]any{"slug": "hidden-model", "visibility": "hidden"}}}), nil
	})}
	if _, err := client.Run(context.Background(), "gpt-6-astra", "answer", RunOptions{ReasoningEffort: "low", UseCairnTools: true}); err == nil || !strings.Contains(err.Error(), "not listed") {
		t.Fatalf("unlisted model was accepted: %v", err)
	}
	if requests != 0 {
		t.Fatalf("response call occurred after model refusal: %d", requests)
	}

	client.Credentials.expiresAt = time.Now().Add(-time.Minute)
	client.httpClient = &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
		t.Fatalf("expired token made request to %s", req.URL)
		return nil, nil
	})}
	if _, err := client.Run(context.Background(), "gpt-6-luna", "answer", RunOptions{ReasoningEffort: "low"}); err == nil || !strings.Contains(err.Error(), "expired") {
		t.Fatalf("expired token accepted: %v", err)
	}
	if _, err := client.Run(context.Background(), "gpt-6-luna", "answer", RunOptions{ReasoningEffort: "low", UseCairnTools: true}); err == nil || !strings.Contains(err.Error(), "expired") {
		t.Fatalf("expired token accepted in tools arm: %v", err)
	}
	client.Credentials.expiresAt = time.Now().Add(time.Hour)
	client.Credentials.accessToken = ""
	if _, err := client.Run(context.Background(), "gpt-6-luna", "answer", RunOptions{ReasoningEffort: "low"}); err == nil || !strings.Contains(err.Error(), "credentials") {
		t.Fatalf("missing access token accepted: %v", err)
	}
	forged := validResponseClient(t, nil, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		t.Fatal("unverified credential reached the provider")
		return nil, nil
	})
	forged.Credentials.validated = false
	forged.Credentials.accessToken = "sk-provider-key-is-not-siwc"
	if _, err := forged.Run(context.Background(), "gpt-6-luna", "answer", RunOptions{ReasoningEffort: "low"}); err == nil || !strings.Contains(err.Error(), "credentials") {
		t.Fatalf("fabricated/API-key credentials accepted: %v", err)
	}
}

func TestUnexpectedFunctionAndResponseErrorsAreTerminal(t *testing.T) {
	tools := &fakeCairnTools{}
	responseCalls := 0
	client := validResponseClient(t, tools, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		responseCalls++
		return streamResponse(t, "resp-unexpected", []any{map[string]any{"id": "fc", "type": "function_call", "call_id": "call", "name": "web_search", "arguments": "{}"}}, 1, 1, 2, -1, -1), nil
	})
	if _, err := client.Run(context.Background(), "gpt-6-luna", "search", RunOptions{ReasoningEffort: "low", UseCairnTools: true}); err == nil || !strings.Contains(err.Error(), "unsupported function") {
		t.Fatalf("unexpected function call accepted: %v", err)
	}
	if responseCalls != 1 || tools.searches != 0 || tools.pulls != 0 {
		t.Fatalf("fallback or tool invocation occurred: calls=%d tools=%+v", responseCalls, tools)
	}

	failed := validResponseClient(t, nil, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		return &http.Response{StatusCode: http.StatusForbidden, Header: http.Header{"x-request-id": []string{"request-1"}}, Body: io.NopCloser(strings.NewReader("sensitive failure body"))}, nil
	})
	_, err := failed.Run(context.Background(), "gpt-6-luna", "answer", RunOptions{ReasoningEffort: "none"})
	if err == nil || strings.Contains(err.Error(), "sensitive failure body") {
		t.Fatalf("error did not fail closed/sanitize: %v", err)
	}
}

func TestFunctionCallsFromOtherNamespacesAreRejected(t *testing.T) {
	tools := &fakeCairnTools{}
	client := validResponseClient(t, tools, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		return streamResponse(t, "resp-wrong-namespace", []any{map[string]any{"id": "fc", "type": "function_call", "namespace": "external", "call_id": "call", "name": "cairn_search", "arguments": "{\"query\":\"x\"}"}}, 1, 1, 2, -1, -1), nil
	})
	if _, err := client.Run(context.Background(), "gpt-6-luna", "search", RunOptions{ReasoningEffort: "low", UseCairnTools: true}); err == nil || !strings.Contains(err.Error(), "invalid function call") {
		t.Fatalf("foreign namespace call accepted: %v", err)
	}
	if tools.searches != 0 {
		t.Fatalf("foreign function call reached Cairn: %+v", tools)
	}
}

func TestDuplicateFunctionCallIDsFailBeforeDuplicateDispatch(t *testing.T) {
	tools := &fakeCairnTools{}
	client := validResponseClient(t, tools, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		return streamResponse(t, "resp-duplicate-call", []any{
			map[string]any{"id": "fc-1", "type": "function_call", "namespace": "cairn", "call_id": "same-call", "name": "cairn_search", "arguments": "{\"query\":\"one\"}"},
			map[string]any{"id": "fc-2", "type": "function_call", "namespace": "cairn", "call_id": "same-call", "name": "cairn_pull", "arguments": "{\"receipt_id\":\"r\",\"handle\":\"h\",\"request_id\":\"id\"}"},
		}, 1, 1, 2, -1, -1), nil
	})
	if _, err := client.Run(context.Background(), "gpt-6-luna", "search", RunOptions{ReasoningEffort: "low", UseCairnTools: true}); err == nil || !strings.Contains(err.Error(), "duplicate function call ID") {
		t.Fatalf("duplicate call ID accepted: %v", err)
	}
	if tools.searches != 0 || tools.pulls != 0 {
		t.Fatalf("ambiguous calls reached Cairn: %+v", tools)
	}
}

func TestDuplicateFunctionCallIDAcrossResponsesIsRejected(t *testing.T) {
	tools := &fakeCairnTools{}
	turn := 0
	client := validResponseClient(t, tools, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		turn++
		if turn == 1 {
			return streamResponse(t, "resp-1", []any{map[string]any{"id": "fc-1", "type": "function_call", "call_id": "same-call", "name": "cairn_search", "arguments": "{\"query\":\"one\"}"}}, 1, 1, 2, -1, -1), nil
		}
		return streamResponse(t, "resp-2", []any{map[string]any{"id": "fc-2", "type": "function_call", "call_id": "same-call", "name": "cairn_search", "arguments": "{\"query\":\"again\"}"}}, 1, 1, 2, -1, -1), nil
	})
	if _, err := client.Run(context.Background(), "gpt-6-luna", "search", RunOptions{ReasoningEffort: "low", UseCairnTools: true}); err == nil || !strings.Contains(err.Error(), "duplicate function call ID") {
		t.Fatalf("cross-response duplicate call ID accepted: %v", err)
	}
	if tools.searches != 1 {
		t.Fatalf("duplicate call was dispatched: %+v", tools)
	}
}

func TestMalformedOrMissingUsageAndDuplicateResponseIDsFailClosed(t *testing.T) {
	for name, stream := range map[string]string{
		"missing-usage":            `data: {"type":"response.completed","response":{"id":"r1","status":"completed","output":[]}}` + "\n\n",
		"inconsistent-usage":       `data: {"type":"response.completed","response":{"id":"r1","status":"completed","output":[],"usage":{"input_tokens":3,"output_tokens":2,"total_tokens":99}}}` + "\n\n",
		"zero-usage":               `data: {"type":"response.completed","response":{"id":"r1","status":"completed","output":[],"usage":{"input_tokens":0,"output_tokens":0,"total_tokens":0}}}` + "\n\n",
		"cached-exceeds-input":     `data: {"type":"response.completed","response":{"id":"r1","status":"completed","output":[],"usage":{"input_tokens":3,"output_tokens":2,"total_tokens":5,"input_tokens_details":{"cached_tokens":4}}}}` + "\n\n",
		"reasoning-exceeds-output": `data: {"type":"response.completed","response":{"id":"r1","status":"completed","output":[],"usage":{"input_tokens":3,"output_tokens":2,"total_tokens":5,"output_tokens_details":{"reasoning_tokens":3}}}}` + "\n\n",
		"malformed-event":          "data: {not-json}\n\n",
		"unterminated-event":       `data: {"type":"response.completed","response":{"id":"r1","status":"completed","output":[],"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}`,
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := readCompletedResponse(strings.NewReader(stream)); err == nil {
				t.Fatal("invalid stream accepted")
			}
		})
	}

	turn := 0
	client := validResponseClient(t, &fakeCairnTools{}, func(_ *http.Request, _ map[string]any) (*http.Response, error) {
		turn++
		if turn == 1 {
			return streamResponse(t, "same-id", []any{map[string]any{"id": "fc", "type": "function_call", "call_id": "call", "name": "cairn_search", "arguments": "{\"query\":\"x\"}"}}, 1, 1, 2, -1, -1), nil
		}
		return streamResponse(t, "same-id", []any{map[string]any{"id": "msg", "type": "message", "role": "assistant", "content": []any{map[string]any{"type": "output_text", "text": "answer"}}}}, 1, 1, 2, -1, -1), nil
	})
	if _, err := client.Run(context.Background(), "gpt-6-luna", "answer", RunOptions{ReasoningEffort: "low", UseCairnTools: true}); err == nil || !strings.Contains(err.Error(), "duplicate response ID") {
		t.Fatalf("duplicate response ID accepted: %v", err)
	}
}

func validResponseClient(t *testing.T, tools CairnTools, responder func(*http.Request, map[string]any) (*http.Response, error)) *ResponsesClient {
	t.Helper()
	client := &ResponsesClient{Credentials: Credentials{clientID: "oaiapp_fixture", subject: "account-subject", accessToken: "access-in-memory", scopes: append([]string(nil), requiredScopes...), expiresAt: time.Now().Add(time.Hour), validated: true}, Tools: tools}
	client.httpClient = &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
		if req.URL.Host != "api.openai.com" {
			return nil, fmt.Errorf("unexpected provider/endpoint: %s", req.URL)
		}
		if req.Header.Get("Authorization") != "Bearer access-in-memory" {
			return nil, errors.New("missing bearer access token")
		}
		if req.URL.Path == "/v1/models" {
			return jsonResponse(http.StatusOK, map[string]any{"models": []any{map[string]any{"slug": "gpt-6-luna", "visibility": "list"}}}), nil
		}
		if req.URL.Path != "/v1/responses" || req.Method != http.MethodPost {
			return nil, fmt.Errorf("unexpected endpoint %s %s", req.Method, req.URL)
		}
		var body map[string]any
		if err := json.NewDecoder(req.Body).Decode(&body); err != nil {
			return nil, err
		}
		return responder(req, body)
	})}
	return client
}

func streamResponse(t *testing.T, id string, output []any, inputTokens, outputTokens, totalTokens, cachedTokens, reasoningTokens int64) *http.Response {
	t.Helper()
	usage := map[string]any{"input_tokens": inputTokens, "output_tokens": outputTokens, "total_tokens": totalTokens}
	if cachedTokens >= 0 {
		usage["input_tokens_details"] = map[string]any{"cached_tokens": cachedTokens}
	}
	if reasoningTokens >= 0 {
		usage["output_tokens_details"] = map[string]any{"reasoning_tokens": reasoningTokens}
	}
	event, err := json.Marshal(map[string]any{"type": "response.completed", "response": map[string]any{"id": id, "model": "gpt-6-luna", "status": "completed", "output": output, "usage": usage}})
	if err != nil {
		t.Fatal(err)
	}
	body := "data: " + string(event) + "\n\n"
	return &http.Response{StatusCode: http.StatusOK, Header: http.Header{"Content-Type": []string{"text/event-stream"}}, Body: io.NopCloser(strings.NewReader(body))}
}
