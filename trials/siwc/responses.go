package siwc

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// CairnTools is the only local capability surface the subscription trial may
// expose. Implementations should call the existing authorized search/pull
// interfaces and return their JSON result; no other tool can be registered.
type CairnTools interface {
	Search(context.Context, json.RawMessage) (json.RawMessage, error)
	Pull(context.Context, json.RawMessage) (json.RawMessage, error)
}

type Usage struct {
	InputTokens       int64  `json:"input_tokens"`
	OutputTokens      int64  `json:"output_tokens"`
	TotalTokens       int64  `json:"total_tokens"`
	CachedInputTokens *int64 `json:"cached_input_tokens"`
	ReasoningTokens   *int64 `json:"reasoning_tokens"`
}

// RunOptions freezes per-arm choices. UseCairnTools=false omits the tool
// namespace for the direct-source/no-tools trial condition.
type RunOptions struct {
	ReasoningEffort string
	UseCairnTools   bool
}

type Result struct {
	Text        string
	ServedModel string
	ResponseIDs []string
	Usage       Usage
	ToolCalls   int
}

type ResponsesClient struct {
	httpClient  *http.Client
	Credentials Credentials
	Tools       CairnTools
}

// ModelCatalog returns selectable model slugs listed for the authorized
// account. Catalog presence is not proof of entitlement; inference can still
// fail, and callers must not substitute a preferred model if it is absent.
func (c *ResponsesClient) ModelCatalog(ctx context.Context) ([]string, error) {
	if err := c.validateCredentials(); err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, ModelsEndpoint, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.Credentials.accessToken)
	resp, err := c.client().Do(req)
	if err != nil {
		return nil, fmt.Errorf("read account model catalog: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("model catalog returned HTTP %d", resp.StatusCode)
	}
	var payload struct {
		Models []struct {
			Slug       string `json:"slug"`
			Visibility string `json:"visibility"`
		} `json:"models"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 2<<20)).Decode(&payload); err != nil {
		return nil, errors.New("invalid account model catalog")
	}
	seen := map[string]bool{}
	models := make([]string, 0, len(payload.Models))
	for _, model := range payload.Models {
		if model.Visibility == "list" && model.Slug != "" && !seen[model.Slug] {
			seen[model.Slug] = true
			models = append(models, model.Slug)
		}
	}
	if len(models) == 0 {
		return nil, errors.New("account model catalog contains no selectable models")
	}
	return models, nil
}

// Run sends one streamed Responses turn. Follow-up requests include the full
// in-memory input and function exchange; they do not use previous_response_id.
// All HTTP and tool errors are terminal. No provider or model fallback exists.
func (c *ResponsesClient) Run(ctx context.Context, model, prompt string, options RunOptions) (Result, error) {
	if err := c.validateCredentials(); err != nil {
		return Result{}, err
	}
	if strings.TrimSpace(prompt) == "" {
		return Result{}, errors.New("prompt is required")
	}
	if !validReasoningEffort(options.ReasoningEffort) {
		return Result{}, errors.New("an explicit supported reasoning effort is required")
	}
	if options.UseCairnTools && c.Tools == nil {
		return Result{}, errors.New("Cairn search and pull tools are required")
	}
	models, err := c.ModelCatalog(ctx)
	if err != nil {
		return Result{}, err
	}
	if !contains(models, model) {
		return Result{}, fmt.Errorf("model %q is not listed for the authorized account", model)
	}
	input := []json.RawMessage{}
	user, _ := json.Marshal(map[string]any{"role": "user", "content": prompt})
	input = append(input, user)
	result := Result{}
	seenIDs := make(map[string]bool)
	seenCallIDs := make(map[string]bool)
	for turn := 0; turn < 4; turn++ {
		response, err := c.createResponse(ctx, model, input, options)
		if err != nil {
			return Result{}, err
		}
		if response.ID == "" || seenIDs[response.ID] {
			return Result{}, errors.New("Responses returned a missing or duplicate response ID")
		}
		if response.Model == "" || response.Model != model {
			return Result{}, fmt.Errorf("Responses served model %q does not match requested model %q", response.Model, model)
		}
		if result.ServedModel != "" && result.ServedModel != response.Model {
			return Result{}, errors.New("Responses changed served model during the run")
		}
		result.ServedModel = response.Model
		seenIDs[response.ID] = true
		result.ResponseIDs = append(result.ResponseIDs, response.ID)
		usage, err := response.Usage.checked()
		if err != nil {
			return Result{}, err
		}
		result.Usage.InputTokens += usage.InputTokens
		result.Usage.OutputTokens += usage.OutputTokens
		result.Usage.TotalTokens += usage.TotalTokens
		if len(result.ResponseIDs) == 1 {
			result.Usage.CachedInputTokens = cloneInt64(usage.CachedInputTokens)
			result.Usage.ReasoningTokens = cloneInt64(usage.ReasoningTokens)
		} else {
			result.Usage.CachedInputTokens = sumKnown(result.Usage.CachedInputTokens, usage.CachedInputTokens)
			result.Usage.ReasoningTokens = sumKnown(result.Usage.ReasoningTokens, usage.ReasoningTokens)
		}
		for _, raw := range response.Output {
			input = append(input, raw)
		}
		calls := make([]outputItem, 0)
		turnText := ""
		for _, raw := range response.Output {
			var item outputItem
			if err := json.Unmarshal(raw, &item); err != nil {
				return Result{}, errors.New("invalid Responses output item")
			}
			if item.Type == "message" {
				for _, content := range item.Content {
					if content.Type == "output_text" {
						turnText += content.Text
					}
				}
			}
			if item.Type == "message" || item.Type == "reasoning" {
				continue
			}
			if item.Type != "function_call" {
				return Result{}, fmt.Errorf("unsupported Responses output item %q", item.Type)
			}
			if !options.UseCairnTools {
				return Result{}, errors.New("model returned a function call while tools are disabled")
			}
			if item.CallID == "" || item.Name == "" || (item.Namespace != "" && item.Namespace != "cairn") || !json.Valid([]byte(item.Arguments)) {
				return Result{}, errors.New("invalid function call item")
			}
			var arguments map[string]json.RawMessage
			if err := json.Unmarshal([]byte(item.Arguments), &arguments); err != nil || arguments == nil {
				return Result{}, errors.New("function call arguments must be a JSON object")
			}
			if err := validateToolArguments(item.Name, arguments); err != nil {
				return Result{}, err
			}
			if seenCallIDs[item.CallID] {
				return Result{}, errors.New("duplicate function call ID")
			}
			seenCallIDs[item.CallID] = true
			switch item.Name {
			case "cairn_search", "cairn_pull":
			default:
				return Result{}, fmt.Errorf("unsupported function call %q", item.Name)
			}
			calls = append(calls, item)
		}
		for _, item := range calls {
			result.ToolCalls++
			var output json.RawMessage
			var err error
			switch item.Name {
			case "cairn_search":
				output, err = c.Tools.Search(ctx, json.RawMessage(item.Arguments))
			case "cairn_pull":
				output, err = c.Tools.Pull(ctx, json.RawMessage(item.Arguments))
			}
			if err != nil {
				return Result{}, fmt.Errorf("%s failed: %w", item.Name, err)
			}
			if !json.Valid(output) {
				return Result{}, fmt.Errorf("%s returned invalid JSON", item.Name)
			}
			toolOutput, _ := json.Marshal(map[string]any{"type": "function_call_output", "call_id": item.CallID, "output": string(output)})
			input = append(input, toolOutput)
		}
		if len(calls) == 0 {
			if strings.TrimSpace(turnText) == "" {
				return Result{}, errors.New("Responses returned an empty final answer")
			}
			result.Text = turnText
			return result, nil
		}
	}
	return Result{}, errors.New("Responses function-call limit exceeded")
}

func validateToolArguments(name string, args map[string]json.RawMessage) error {
	allowed := map[string]map[string]bool{
		"cairn_search": {"query": true, "semantic": true},
		"cairn_pull":   {"request_id": true, "receipt_id": true, "handle": true, "span": true, "opening_bytes": true},
	}
	fields, ok := allowed[name]
	if !ok {
		return fmt.Errorf("unsupported function call %q", name)
	}
	for key := range args {
		if !fields[key] {
			return fmt.Errorf("%s has unsupported argument %q", name, key)
		}
	}
	switch name {
	case "cairn_search":
		var parsed struct {
			Query    *string `json:"query"`
			Semantic *bool   `json:"semantic"`
		}
		encoded, err := json.Marshal(args)
		if err != nil || json.Unmarshal(encoded, &parsed) != nil || parsed.Query == nil || strings.TrimSpace(*parsed.Query) == "" {
			return errors.New("cairn_search requires a nonempty string query and optional boolean semantic")
		}
	case "cairn_pull":
		var parsed struct {
			RequestID *string `json:"request_id"`
			ReceiptID *string `json:"receipt_id"`
			Handle    *string `json:"handle"`
			Span      *struct {
				Offset *int64 `json:"offset"`
				Length *int64 `json:"length"`
			} `json:"span"`
			OpeningBytes *int64 `json:"opening_bytes"`
		}
		encoded, err := json.Marshal(args)
		if err != nil || json.Unmarshal(encoded, &parsed) != nil || parsed.RequestID == nil || strings.TrimSpace(*parsed.RequestID) == "" || parsed.ReceiptID == nil || strings.TrimSpace(*parsed.ReceiptID) == "" || parsed.Handle == nil || strings.TrimSpace(*parsed.Handle) == "" {
			return errors.New("cairn_pull requires nonempty request_id, receipt_id, and handle strings")
		}
		if raw, exists := args["span"]; exists {
			var spanFields map[string]json.RawMessage
			if json.Unmarshal(raw, &spanFields) != nil || spanFields == nil || len(spanFields) != 2 || spanFields["offset"] == nil || spanFields["length"] == nil || parsed.Span == nil || parsed.Span.Offset == nil || parsed.Span.Length == nil || *parsed.Span.Offset < 0 || *parsed.Span.Offset >= 65536 || *parsed.Span.Length <= 0 || *parsed.Span.Length > 65536 {
				return errors.New("cairn_pull span must contain a valid integer offset and positive length")
			}
		}
		if parsed.OpeningBytes != nil && (*parsed.OpeningBytes < 0 || *parsed.OpeningBytes > 768 || parsed.Span == nil) {
			return errors.New("cairn_pull opening_bytes must be 0-768 and requires span")
		}
	}
	return nil
}

func (c *ResponsesClient) createResponse(ctx context.Context, model string, input []json.RawMessage, options RunOptions) (completedResponse, error) {
	body := map[string]any{
		"model": model, "input": input, "store": false, "stream": true,
		"reasoning": map[string]string{"effort": options.ReasoningEffort},
	}
	if options.UseCairnTools {
		body["tools"] = []any{cairnToolNamespace()}
	}
	encoded, err := json.Marshal(body)
	if err != nil {
		return completedResponse{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, ResponsesEndpoint, bytes.NewReader(encoded))
	if err != nil {
		return completedResponse{}, err
	}
	req.Header.Set("Authorization", "Bearer "+c.Credentials.accessToken)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "text/event-stream")
	resp, err := c.client().Do(req)
	if err != nil {
		return completedResponse{}, fmt.Errorf("Responses request failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return completedResponse{}, fmt.Errorf("Responses returned HTTP %d (request %s)", resp.StatusCode, resp.Header.Get("x-request-id"))
	}
	if !strings.HasPrefix(strings.ToLower(resp.Header.Get("Content-Type")), "text/event-stream") {
		return completedResponse{}, errors.New("Responses returned a non-streaming content type")
	}
	return readCompletedResponse(resp.Body)
}

func cairnToolNamespace() map[string]any {
	spanSchema := map[string]any{
		"type":                 "object",
		"properties":           map[string]any{"offset": map[string]string{"type": "integer"}, "length": map[string]string{"type": "integer"}},
		"required":             []string{"offset", "length"},
		"additionalProperties": false,
	}
	searchSchema := map[string]any{
		"type":                 "object",
		"properties":           map[string]any{"query": map[string]string{"type": "string"}, "semantic": map[string]string{"type": "boolean"}},
		"required":             []string{"query"},
		"additionalProperties": false,
	}
	pullSchema := map[string]any{
		"type": "object",
		"properties": map[string]any{
			"request_id":    map[string]string{"type": "string"},
			"receipt_id":    map[string]string{"type": "string"},
			"handle":        map[string]string{"type": "string"},
			"span":          spanSchema,
			"opening_bytes": map[string]string{"type": "integer"},
		},
		"required":             []string{"request_id", "receipt_id", "handle"},
		"additionalProperties": false,
	}
	return map[string]any{
		"type":        "namespace",
		"name":        "cairn",
		"description": "Cairn note retrieval tools.",
		"tools": []any{
			map[string]any{"type": "function", "name": "cairn_search", "description": "Search Cairn notes. Returns eligible indexed results and complete pull_arguments.", "parameters": searchSchema},
			map[string]any{"type": "function", "name": "cairn_pull", "description": "Copy the complete pull_arguments object returned by cairn_search, preserving its span or opening fields.", "parameters": pullSchema},
		},
	}
}

func (c *ResponsesClient) validateCredentials() error {
	if !c.Credentials.validated || c.Credentials.accessToken == "" || c.Credentials.subject == "" || !strings.HasPrefix(c.Credentials.clientID, registeredPrefix) {
		return errors.New("validated SIWC account credentials are required")
	}
	if !hasScopes(strings.Join(c.Credentials.scopes, " "), requiredScopes) {
		return errors.New("SIWC account is missing required scopes")
	}
	if c.Credentials.expiresAt.IsZero() || !time.Now().Before(c.Credentials.expiresAt) {
		return errors.New("SIWC access token is expired or has no verified expiry; reauthorize before inference")
	}
	return nil
}

func validReasoningEffort(effort string) bool {
	switch effort {
	case "none", "low", "medium", "high", "xhigh", "max":
		return true
	default:
		return false
	}
}

func sumKnown(a, b *int64) *int64 {
	if a == nil || b == nil {
		return nil
	}
	sum := *a + *b
	return &sum
}

func (c *ResponsesClient) client() *http.Client {
	if c.httpClient != nil {
		return noRedirectClient(c.httpClient)
	}
	return noRedirectClient(http.DefaultClient)
}
func contains(haystack []string, want string) bool {
	for _, x := range haystack {
		if x == want {
			return true
		}
	}
	return false
}

type completedResponse struct {
	ID     string `json:"id"`
	Model  string `json:"model"`
	Status string `json:"status"`
	Error  *struct {
		Code string `json:"code"`
	} `json:"error"`
	Output []json.RawMessage `json:"output"`
	Usage  responseUsage     `json:"usage"`
}

type responseUsage struct {
	InputTokens  *int64 `json:"input_tokens"`
	OutputTokens *int64 `json:"output_tokens"`
	TotalTokens  *int64 `json:"total_tokens"`
	InputDetails *struct {
		CachedTokens *int64 `json:"cached_tokens"`
	} `json:"input_tokens_details"`
	OutputDetails *struct {
		ReasoningTokens *int64 `json:"reasoning_tokens"`
	} `json:"output_tokens_details"`
}

func (u responseUsage) checked() (Usage, error) {
	if u.InputTokens == nil || u.OutputTokens == nil || u.TotalTokens == nil || *u.InputTokens <= 0 || *u.OutputTokens <= 0 || *u.TotalTokens <= 0 || *u.TotalTokens != *u.InputTokens+*u.OutputTokens {
		return Usage{}, errors.New("Responses usage is missing or inconsistent")
	}
	usage := Usage{InputTokens: *u.InputTokens, OutputTokens: *u.OutputTokens, TotalTokens: *u.TotalTokens}
	if u.InputDetails != nil {
		usage.CachedInputTokens = u.InputDetails.CachedTokens
		if usage.CachedInputTokens != nil && (*usage.CachedInputTokens < 0 || *usage.CachedInputTokens > usage.InputTokens) {
			return Usage{}, errors.New("Responses cache usage is invalid")
		}
	}
	if u.OutputDetails != nil {
		usage.ReasoningTokens = u.OutputDetails.ReasoningTokens
		if usage.ReasoningTokens != nil && (*usage.ReasoningTokens < 0 || *usage.ReasoningTokens > usage.OutputTokens) {
			return Usage{}, errors.New("Responses reasoning usage is invalid")
		}
	}
	return usage, nil
}

type responseEvent struct {
	Type     string            `json:"type"`
	Response completedResponse `json:"response"`
}
type outputItem struct {
	Type      string `json:"type"`
	Namespace string `json:"namespace"`
	CallID    string `json:"call_id"`
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
	Content   []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	} `json:"content"`
}

func readCompletedResponse(r io.Reader) (completedResponse, error) {
	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 4096), 4<<20)
	var data []string
	var completed *completedResponse
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			if len(data) != 0 {
				payload := strings.Join(data, "\n")
				data = nil
				if payload != "[DONE]" {
					var event responseEvent
					if err := json.Unmarshal([]byte(payload), &event); err != nil {
						return completedResponse{}, errors.New("invalid Responses stream event")
					}
					switch event.Type {
					case "response.completed":
						if event.Response.ID == "" || event.Response.Status != "completed" || event.Response.Error != nil || completed != nil {
							return completedResponse{}, errors.New("Responses completed event was incomplete")
						}
						copy := event.Response
						completed = &copy
					case "response.failed":
						return completedResponse{}, errors.New("Responses stream failed")
					case "response.incomplete":
						return completedResponse{}, errors.New("Responses stream was incomplete")
					}
				}
			}
			continue
		}
		if strings.HasPrefix(line, "data:") {
			data = append(data, strings.TrimSpace(strings.TrimPrefix(line, "data:")))
		}
	}
	if err := scanner.Err(); err != nil {
		return completedResponse{}, fmt.Errorf("read Responses stream: %w", err)
	}
	if len(data) != 0 && strings.Join(data, "\n") != "[DONE]" {
		return completedResponse{}, errors.New("Responses stream ended with an unterminated event")
	}
	if completed == nil {
		return completedResponse{}, errors.New("Responses stream ended without response.completed")
	}
	if _, err := completed.Usage.checked(); err != nil {
		return completedResponse{}, err
	}
	return *completed, nil
}

func cloneInt64(v *int64) *int64 {
	if v == nil {
		return nil
	}
	copy := *v
	return &copy
}
