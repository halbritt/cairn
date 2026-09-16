package wakeup

import (
	"bytes"
	"encoding/json"
	"errors"
	"regexp"
	"strings"

	"github.com/halbritt/cairn/core"
)

const maxLineBytes = 1024 * 1024 // 1 MiB line limit

const (
	codexMsgQuota = "Quota exceeded. Check your plan and billing details."
	codexMsg429   = "exceeded retry limit, last status: 429 Too Many Requests"
)

// providerStream observes native runner stdout streams for provider quota,
// rate-limit events. It maintains bounded line memory (max 1 MiB),
// ignores tool output and malformed JSON, and invokes changed synchronously on
// candidate failure transitions.
type providerStream struct {
	harness     string
	changed     func(*core.ProviderFailure) error
	buf         bytes.Buffer
	oversized   bool
	retainedErr error
	current     *core.ProviderFailure
	closed      bool
	agySession  string
	agyStep     int
	agyError    bool
}

func newProviderStream(harness string, changed func(*core.ProviderFailure) error) *providerStream {
	return &providerStream{
		harness: harness,
		changed: changed,
	}
}

func (s *providerStream) Write(p []byte) (int, error) {
	if s.retainedErr != nil {
		return 0, s.retainedErr
	}
	if s.closed {
		return 0, errors.New("provider stream is closed")
	}

	total := len(p)
	for len(p) > 0 {
		if s.retainedErr != nil {
			return total - len(p), s.retainedErr
		}

		if s.oversized {
			idx := bytes.IndexByte(p, '\n')
			if idx == -1 {
				// The remainder of chunk p is still part of the oversized line.
				return total, nil
			}
			// Found newline ending the oversized line; discard and resume.
			s.oversized = false
			s.buf.Reset()
			p = p[idx+1:]
			continue
		}

		idx := bytes.IndexByte(p, '\n')
		if idx == -1 {
			// No newline in remaining chunk. Check if appending exceeds maxLineBytes.
			if s.buf.Len()+len(p) > maxLineBytes {
				s.oversized = true
				s.buf.Reset()
				return total, nil
			}
			s.buf.Write(p)
			return total, nil
		}

		// Newline found at idx.
		if s.buf.Len()+idx > maxLineBytes {
			// Line exceeded limit; discard through newline.
			s.buf.Reset()
			p = p[idx+1:]
			continue
		}

		s.buf.Write(p[:idx])
		line := s.buf.Bytes()
		err := s.parseLine(line)
		s.buf.Reset()
		p = p[idx+1:]

		if err != nil {
			s.retainedErr = err
			return total - len(p), s.retainedErr
		}
	}

	return total, nil
}

func (s *providerStream) Close() error {
	if s.closed {
		return s.retainedErr
	}
	s.closed = true

	if s.retainedErr != nil {
		return s.retainedErr
	}

	if !s.oversized && s.buf.Len() > 0 {
		line := s.buf.Bytes()
		if len(line) <= maxLineBytes {
			if err := s.parseLine(line); err != nil {
				s.retainedErr = err
				return err
			}
		}
		s.buf.Reset()
	}

	return s.retainedErr
}

func (s *providerStream) notify(f *core.ProviderFailure) error {
	if f == nil && s.current == nil {
		return nil
	}
	if f != nil {
		if err := f.Validate(); err != nil {
			s.retainedErr = err
			return err
		}
	}
	s.current = f
	if s.changed != nil {
		if err := s.changed(f); err != nil {
			s.retainedErr = err
			return err
		}
	}
	return nil
}

func (s *providerStream) parseLine(raw []byte) error {
	raw = bytes.TrimSuffix(raw, []byte("\r"))
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 || raw[0] != '{' {
		return nil
	}

	switch s.harness {
	case "codex":
		return s.parseCodex(raw)
	case "claude":
		return s.parseClaude(raw)
	case "opencode":
		return s.parseOpenCode(raw)
	case "agy":
		return s.parseAgy(raw)
	default:
		return nil
	}
}

var agyRateLimit = regexp.MustCompile(`^API error \(attempt [1-9][0-9]{0,5}\): Error 429, Message: [^\r\n]+, Status: RESOURCE_EXHAUSTED, Details: \[[^\r\n]*\]$`)

func (s *providerStream) parseAgy(raw []byte) error {
	var line struct {
		Event        string `json:"event"`
		Conversation string `json:"conversation_id"`
		Step         *struct {
			Conversation string `json:"conversation_id"`
			Index        *int   `json:"step_index"`
			State        string `json:"state"`
			Type         string `json:"step_type"`
		} `json:"step_update"`
		Result *struct {
			Conversation string `json:"conversation_id"`
			Status       string `json:"status"`
			Error        string `json:"error"`
		} `json:"result"`
	}
	if err := json.Unmarshal(raw, &line); err != nil {
		return nil
	}
	switch line.Event {
	case "init":
		if s.agySession != "" && s.agySession != line.Conversation {
			return nil
		}
		s.agySession, s.agyStep, s.agyError = line.Conversation, -1, false
		return s.notify(nil)
	case "step_update":
		step := line.Step
		if step == nil || s.agySession == "" || step.Conversation != s.agySession || step.Index == nil || *step.Index < 0 || *step.Index < s.agyStep {
			return nil
		}
		s.agyStep = *step.Index
		s.agyError = step.Type == "error_message" && step.State == "DONE"
		// Agy's final result can retain a 429 from before a successful response.
		// Only the latest step can make that diagnostic current. Step text itself
		// never supplies a provider classification.
		return s.notify(nil)
	case "result":
		result := line.Result
		if result == nil || s.agySession == "" || result.Conversation != s.agySession {
			return nil
		}
		if s.agyError && result.Status == "ERROR" && agyRateLimit.MatchString(result.Error) {
			return s.notify(&core.ProviderFailure{Harness: "agy", Source: "native-diagnostic", Kind: "rate_limit", Code: "agy_http_429", Status: 429})
		}
		return s.notify(nil)
	}
	return nil
}

type codexLine struct {
	Type  string      `json:"type"`
	Error *codexError `json:"error"`
}

type codexError struct {
	Message string `json:"message"`
}

func (s *providerStream) parseCodex(raw []byte) error {
	var line codexLine
	if err := json.Unmarshal(raw, &line); err != nil {
		return nil
	}

	// Classify only terminal turn.failed with observed native diagnostics.
	if line.Type != "turn.failed" || line.Error == nil {
		if line.Type == "turn.completed" && s.current != nil {
			return s.notify(nil)
		}
		return nil
	}

	switch line.Error.Message {
	case codexMsgQuota:
		return s.notify(&core.ProviderFailure{
			Harness: "codex",
			Source:  "native-diagnostic",
			Kind:    "quota",
			Code:    "codex_quota_exceeded",
		})
	case codexMsg429:
		return s.notify(&core.ProviderFailure{
			Harness: "codex",
			Source:  "native-diagnostic",
			Kind:    "rate_limit",
			Code:    "codex_http_429",
			Status:  429,
		})
	default:
		if codexSubscriptionLimit(line.Error.Message) {
			return s.notify(&core.ProviderFailure{
				Harness: "codex", Source: "native-diagnostic", Kind: "quota", Code: "codex_usage_limit_reached",
			})
		}
		return s.notify(nil)
	}
}

// These plan-specific templates were observed from the installed Codex binary
// receiving usage_limit_reached. Its localized display time is deliberately not
// converted into retry_at; a human-formatted date lacks an unambiguous timezone.
func codexSubscriptionLimit(message string) bool {
	for _, prefix := range []string{
		"You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again ",
		"You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again ",
		"You've hit your usage limit. To get more access now, send a request to your admin or try again ",
		"You've hit your usage limit. Upgrade to Plus to continue using Codex (https://chatgpt.com/explore/plus), or try again ",
	} {
		if suffix, ok := strings.CutPrefix(message, prefix); ok {
			return suffix == "later." || (strings.HasPrefix(suffix, "at ") && len(suffix) > len("at .") && len(suffix) < 128 && strings.HasSuffix(suffix, ".") && !strings.ContainsAny(suffix, "\r\n"))
		}
	}
	return false
}

type claudeLine struct {
	Type              string `json:"type"`
	Subtype           string `json:"subtype"`
	ErrorStatus       int    `json:"error_status"`
	Error             string `json:"error"`
	IsApiErrorMessage bool   `json:"is_api_error_message"`
	IsError           bool   `json:"is_error"`
	TerminalReason    string `json:"terminal_reason"`
}

func (s *providerStream) parseClaude(raw []byte) error {
	var line claudeLine
	if err := json.Unmarshal(raw, &line); err != nil {
		return nil
	}

	switch line.Type {
	case "system":
		if line.Subtype == "api_retry" {
			if line.ErrorStatus == 429 && line.Error == "rate_limit" {
				return s.notify(&core.ProviderFailure{
					Harness: "claude",
					Source:  "native-event",
					Kind:    "rate_limit",
					Code:    "claude_rate_limit",
					Status:  429,
				})
			}
			// Clear on other api_retry failure (e.g. 500 server_error, 529 overloaded)
			return s.notify(nil)
		}
	case "assistant":
		// Clear on successful non-error assistant
		if !line.IsApiErrorMessage {
			return s.notify(nil)
		}
	case "result":
		// Retain on terminal api_error; clear on successful result
		if line.TerminalReason != "api_error" || !line.IsError {
			return s.notify(nil)
		}
	}
	return nil
}

type opencodeLine struct {
	Type  string         `json:"type"`
	Error *opencodeError `json:"error"`
}

type opencodeError struct {
	Name string             `json:"name"`
	Data *opencodeErrorData `json:"data"`
}

type opencodeErrorData struct {
	StatusCode int `json:"statusCode"`
}

func (s *providerStream) parseOpenCode(raw []byte) error {
	var line opencodeLine
	if err := json.Unmarshal(raw, &line); err != nil {
		return nil
	}

	if line.Type == "error" && line.Error != nil && line.Error.Name == "APIError" && line.Error.Data != nil {
		if line.Error.Data.StatusCode == 429 {
			return s.notify(&core.ProviderFailure{
				Harness: "opencode",
				Source:  "native-event",
				Kind:    "rate_limit",
				Code:    "opencode_http_429",
				Status:  429,
			})
		}
		if line.Error.Data.StatusCode == 402 {
			return s.notify(&core.ProviderFailure{
				Harness: "opencode",
				Source:  "native-event",
				Kind:    "billing",
				Code:    "opencode_http_402",
				Status:  402,
			})
		}
		return s.notify(nil)
	}

	return nil
}
