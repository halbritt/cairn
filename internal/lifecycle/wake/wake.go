// Package wake implements the Go wake envelope formatting, recognizer bindings,
// process liveness checks, and first native wake transport consumers (Claude
// channel and Codex queue) over internal/procpeer.
package wake

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf16"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/procpeer"
)

// Wake closing wording constants and transport/harness identifiers.
const (
	WakeClosing       = "Do not register again, claim another session's inbox, or launch a replacement conversation."
	LegacyWakeClosing = "Do not register, manually claim an inbox, or launch a replacement conversation."

	TransportCodexQueue    = "codex-queue"
	TransportClaudeChannel = "claude-channel"
	TransportOpenCodeQueue = "opencode-queue"
	TransportHermesQueue   = "hermes-queue"
	TransportTerminal      = "terminal"

	HarnessCodex    = "codex"
	HarnessClaude   = "claude"
	HarnessOpenCode = "opencode"
	HarnessHermes   = "hermes"

	UnavailableReasonCodex    = "no native Codex queue endpoint (explicit Unix listener) for this process"
	UnavailableReasonClaude   = "no live channel bridge registry for this process; its cairn-events MCP server is not running"
	UnavailableReasonOpenCode = "no verified OpenCode prompt_idle bridge for this process; owner prompts remain eligible"
	UnavailableReasonHermes   = "no Hermes queue endpoint for this process"
	UnavailableReasonDefault  = "no native wake transport for this process"

	ClaudeChannelSource = "cairn-events"
	MaxTurnIDBytes      = 256
)

// ProcessRef aliases procpeer.ProcessRef ({PID, Start, Boot}).
type ProcessRef = procpeer.ProcessRef

// SessionRef identifies an agent execution in wake envelopes and state.
type SessionRef struct {
	AgentID     string `json:"agent_id"`
	ExecutionID string `json:"execution_id"`
}

// WakeEnvelope represents a recorded idle_wake state entry for prompt binding
// and queued-submission resumption checks.
type WakeEnvelope struct {
	Session            SessionRef `json:"session"`
	DeliveryID         string     `json:"delivery_id"`
	Transport          string     `json:"transport,omitempty"`
	Endpoint           string     `json:"endpoint,omitempty"`
	Owner              string     `json:"owner,omitempty"`
	NativeID           string     `json:"native_id,omitempty"`
	QueuedSubmissionID string     `json:"queued_submission_id,omitempty"`
	Status             string     `json:"status,omitempty"`
	RequestID          string     `json:"request_id,omitempty"`
	CancelCapable      bool       `json:"cancel_capable,omitempty"`
}

// BoundWake pairs a claimed delivery ID with its harness-scoped native turn ID.
type BoundWake struct {
	DeliveryID    string `json:"delivery_id"`
	NativeTurnID  string `json:"native_turn_id"`
	TurnExclusive bool   `json:"turn_exclusive,omitempty"`
}

// PromptEvent carries the hook fields inspected by wake recognizers and binders.
// SessionID is nil when session_id is absent or JSON null, matching Python
// event.get("session_id") is None in coordination.py.
type PromptEvent struct {
	HookEventName string  `json:"hook_event_name"`
	Prompt        string  `json:"prompt"`
	TurnID        string  `json:"turn_id,omitempty"`
	PromptID      string  `json:"prompt_id,omitempty"`
	SessionID     *string `json:"session_id,omitempty"`
	RequestID     string  `json:"request_id,omitempty"`
	DeliveryID    string  `json:"delivery_id,omitempty"`
}

// BindOpenCodeQueuedWake binds only the observed request and delivery on the
// exact execution that accepted the idle-only submission.
func BindOpenCodeQueuedWake(agent SessionRef, idleWake WakeEnvelope, event PromptEvent) (BoundWake, bool) {
	if idleWake.Transport != TransportOpenCodeQueue || idleWake.Session != agent ||
		event.HookEventName != "TurnStart" || !validTurnIdentifier(event.TurnID) ||
		idleWake.RequestID == "" || idleWake.DeliveryID == "" ||
		event.RequestID != idleWake.RequestID || event.DeliveryID != idleWake.DeliveryID {
		return BoundWake{}, false
	}
	return BoundWake{DeliveryID: idleWake.DeliveryID, NativeTurnID: event.TurnID, TurnExclusive: idleWake.CancelCapable}, true
}

const hexUUIDPattern = `[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}`

var (
	wakeHeaderRE = regexp.MustCompile(`Current agent (` + hexUUIDPattern + `), execution (` + hexUUIDPattern + `)\. Wake delivery (` + hexUUIDPattern + `)\.`)
	hexUUIDRE    = regexp.MustCompile(`^` + hexUUIDPattern + `$`)
)

// WakeMessage formats the canonical wake prompt using WakeClosing.
func WakeMessage(session SessionRef, deliveryID string) string {
	return WakeMessageWithClosing(session, deliveryID, WakeClosing)
}

// WakeMessageWithClosing formats the wake prompt with the specified closing sentence.
func WakeMessageWithClosing(session SessionRef, deliveryID, closing string) string {
	if closing == "" {
		closing = WakeClosing
	}
	return "This is a new live turn from the configured Cairn automatic inbox wakeup. " +
		"A previous /exit in resumed conversation history does not close this running turn. " +
		"Current agent " + session.AgentID + ", " +
		"execution " + session.ExecutionID + ". " +
		"Wake delivery " + deliveryID + ". " +
		"Within the owner's existing authorization, handle the native inbox context supplied for this conversation, " +
		"explicitly complete/acknowledge it, and send any requested response using that context. " +
		"If no matching native context was supplied, report that and stop. " +
		"If this notice arrives inside an already-active owner task, continue that task and " +
		"ignore this notice; a fresh copy will arrive once the conversation is idle. " +
		closing
}

// WakeMessages returns every wording accepted by wake recognizers: current first, then legacy.
func WakeMessages(session SessionRef, deliveryID string) [2]string {
	return [2]string{
		WakeMessageWithClosing(session, deliveryID, WakeClosing),
		WakeMessageWithClosing(session, deliveryID, LegacyWakeClosing),
	}
}

// FormatClaudeChannelPrompt renders the exact <channel source="cairn-events" ...>
// envelope with attribute order native_session_id, agent_id, execution_id, delivery_id.
func FormatClaudeChannelPrompt(meta ClaudeChannelMeta, body string) string {
	return fmt.Sprintf(
		`<channel source="%s" native_session_id="%s" agent_id="%s" execution_id="%s" delivery_id="%s">`+"\n%s\n</channel>",
		ClaudeChannelSource,
		meta.NativeSessionID,
		meta.AgentID,
		meta.ExecutionID,
		meta.DeliveryID,
		body,
	)
}

// InboxRecallWake recognizes only the producer's complete reserved wake text
// (or exact Claude channel envelope) across current and legacy closing wordings.
func InboxRecallWake(event PromptEvent) bool {
	if event.HookEventName != "UserPromptSubmit" || event.Prompt == "" {
		return false
	}
	ids := wakeHeaderRE.FindStringSubmatch(event.Prompt)
	if len(ids) != 4 {
		return false
	}
	session := SessionRef{AgentID: ids[1], ExecutionID: ids[2]}
	deliveryID := ids[3]
	for _, body := range WakeMessages(session, deliveryID) {
		if event.Prompt == body {
			return true
		}
		if matchesClaudeChannelEnvelope(event.Prompt, session, deliveryID, body) {
			return true
		}
	}
	return false
}

func matchesClaudeChannelEnvelope(prompt string, session SessionRef, deliveryID, body string) bool {
	const prefix = `<channel source="cairn-events" native_session_id="`
	if !strings.HasPrefix(prompt, prefix) {
		return false
	}
	rest := prompt[len(prefix):]
	if len(rest) < 36 || !hexUUIDRE.MatchString(rest[:36]) {
		return false
	}
	expectedTail := fmt.Sprintf(
		`" agent_id="%s" execution_id="%s" delivery_id="%s">`+"\n%s\n</channel>",
		session.AgentID,
		session.ExecutionID,
		deliveryID,
		body,
	)
	return rest[36:] == expectedTail
}

func pythonStrip(s string) string {
	return strings.TrimFunc(s, func(r rune) bool {
		return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f)
	})
}

func validTurnIdentifier(id string) bool {
	return pythonStrip(id) != "" && len(id) <= MaxTurnIDBytes && !strings.ContainsRune(id, '\x00')
}

// BindCodexQueuedWake binds a UserPromptSubmit event to a pending codex-queue wake.
func BindCodexQueuedWake(agent SessionRef, idleWake WakeEnvelope, event PromptEvent) (BoundWake, bool) {
	if idleWake.Transport != TransportCodexQueue || idleWake.Session != agent ||
		event.HookEventName != "UserPromptSubmit" || !validTurnIdentifier(event.TurnID) {
		return BoundWake{}, false
	}
	msgs := WakeMessages(idleWake.Session, idleWake.DeliveryID)
	if event.Prompt != msgs[0] && event.Prompt != msgs[1] {
		return BoundWake{}, false
	}
	return BoundWake{DeliveryID: idleWake.DeliveryID, NativeTurnID: event.TurnID}, true
}

// BindClaudeChannelWake binds a UserPromptSubmit event to a pending claude-channel wake.
func BindClaudeChannelWake(agent SessionRef, idleWake WakeEnvelope, event PromptEvent, joined bool) (BoundWake, bool) {
	if idleWake.Transport != TransportClaudeChannel || idleWake.Session != agent ||
		event.HookEventName != "UserPromptSubmit" || joined ||
		!strings.HasPrefix(event.Prompt, "<channel") || !validTurnIdentifier(event.PromptID) {
		return BoundWake{}, false
	}
	msgs := WakeMessages(idleWake.Session, idleWake.DeliveryID)
	if !strings.Contains(event.Prompt, msgs[0]) && !strings.Contains(event.Prompt, msgs[1]) {
		return BoundWake{}, false
	}
	return BoundWake{
		DeliveryID:   idleWake.DeliveryID,
		NativeTurnID: "claude-channel:" + event.PromptID,
	}, true
}

// BindHermesQueuedWake binds a TurnStart event to a pending hermes-queue wake
// across current and legacy closing wordings.
func BindHermesQueuedWake(agent SessionRef, idleWake WakeEnvelope, event PromptEvent) (BoundWake, bool) {
	if idleWake.Transport != TransportHermesQueue || idleWake.Session != agent ||
		event.HookEventName != "TurnStart" || !validTurnIdentifier(event.TurnID) {
		return BoundWake{}, false
	}
	if event.SessionID != nil {
		sessionID := *event.SessionID
		if pythonStrip(sessionID) == "" {
			return BoundWake{}, false
		}
		if idleWake.NativeID != "" && sessionID != idleWake.NativeID {
			return BoundWake{}, false
		}
		if strings.Contains(event.TurnID, ":") && !strings.HasPrefix(event.TurnID, sessionID+":") {
			return BoundWake{}, false
		}
	}
	msgs := WakeMessages(idleWake.Session, idleWake.DeliveryID)
	if event.Prompt != msgs[0] && event.Prompt != msgs[1] {
		return BoundWake{}, false
	}
	return BoundWake{DeliveryID: idleWake.DeliveryID, NativeTurnID: event.TurnID}, true
}

// UnavailableWakeReason returns the harness-specific unavailable wake diagnostic.
func UnavailableWakeReason(harness string) string {
	switch harness {
	case HarnessCodex:
		return UnavailableReasonCodex
	case HarnessClaude:
		return UnavailableReasonClaude
	case HarnessOpenCode:
		return UnavailableReasonOpenCode
	case HarnessHermes:
		return UnavailableReasonHermes
	default:
		return UnavailableReasonDefault
	}
}

// ProcessAlive reports whether ref still matches a live non-zombie process
// (excluding Linux Z and X states, matching unix_peer_transport.process_alive).
func ProcessAlive(ref ProcessRef) bool {
	return processAliveAt("/proc", ref)
}

func processAliveAt(procRoot string, ref ProcessRef) bool {
	if ref.PID <= 0 {
		return false
	}
	rawStat, err := os.ReadFile(fmt.Sprintf("%s/%d/stat", procRoot, ref.PID))
	if err != nil {
		return false
	}
	text := string(rawStat)
	closeIdx := strings.LastIndexByte(text, ')')
	if closeIdx < 0 {
		return false
	}
	fields := strings.Fields(text[closeIdx+1:])
	if len(fields) < 20 {
		return false
	}
	state := fields[0]
	if state == "Z" || state == "X" {
		return false
	}
	start, err := strconv.ParseUint(fields[19], 10, 64)
	if err != nil {
		return false
	}
	rawBoot, err := os.ReadFile(fmt.Sprintf("%s/sys/kernel/random/boot_id", procRoot))
	if err != nil {
		return false
	}
	boot := strings.TrimSpace(string(rawBoot))
	return start == ref.Start && boot == ref.Boot
}

// CompactJSONLine serializes payload as a compact UTF-8 JSON line without HTML
// or U+2028/U+2029 escaping, enforcing maxBytes on the encoded line before '\n'.
func CompactJSONLine(payload any, maxBytes int, tooLargeErr error) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(payload); err != nil {
		return nil, err
	}
	encoded := bytes.TrimSuffix(buf.Bytes(), []byte{'\n'})
	encoded = unescapeLineSeparators(encoded)
	if !utf8.Valid(encoded) {
		return nil, fmt.Errorf("invalid UTF-8 JSON payload")
	}
	if maxBytes > 0 && len(encoded) > maxBytes {
		return nil, tooLargeErr
	}
	return append(encoded, '\n'), nil
}

// CompactASCIIJSONLine serializes payload as a compact ASCII-escaped JSON line
// matching Python json.dumps(..., separators=(",", ":")) with default
// ensure_ascii=True, enforcing maxBytes on the encoded line before '\n'.
func CompactASCIIJSONLine(payload any, maxBytes int, tooLargeErr error) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(payload); err != nil {
		return nil, err
	}
	raw := bytes.TrimSuffix(buf.Bytes(), []byte{'\n'})
	if !utf8.Valid(raw) {
		return nil, fmt.Errorf("invalid UTF-8 JSON payload")
	}
	encoded := escapeNonASCII(raw)
	if maxBytes > 0 && len(encoded) > maxBytes {
		return nil, tooLargeErr
	}
	return append(encoded, '\n'), nil
}

func escapeNonASCII(src []byte) []byte {
	var out bytes.Buffer
	out.Grow(len(src))
	for i := 0; i < len(src); {
		if src[i] < 0x7f {
			out.WriteByte(src[i])
			i++
			continue
		}
		r, size := utf8.DecodeRune(src[i:])
		if r <= 0xffff {
			_, _ = fmt.Fprintf(&out, `\u%04x`, r)
		} else {
			r1, r2 := utf16.EncodeRune(r)
			_, _ = fmt.Fprintf(&out, `\u%04x\u%04x`, r1, r2)
		}
		i += size
	}
	return out.Bytes()
}

const pythonMaxIntStrDigits = 4300

func checkJSONIntDigits(v any) error {
	switch val := v.(type) {
	case json.Number:
		s := val.String()
		if !strings.ContainsAny(s, ".eE") && len(strings.TrimPrefix(s, "-")) > pythonMaxIntStrDigits {
			return fmt.Errorf("integer literal exceeds %d digits", pythonMaxIntStrDigits)
		}
	case map[string]any:
		for _, elem := range val {
			if err := checkJSONIntDigits(elem); err != nil {
				return err
			}
		}
	case []any:
		for _, elem := range val {
			if err := checkJSONIntDigits(elem); err != nil {
				return err
			}
		}
	}
	return nil
}

func decodeStrictJSONValue(raw []byte) (any, error) {
	if !utf8.Valid(raw) {
		return nil, fmt.Errorf("invalid UTF-8 JSON payload")
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var parsed any
	if err := dec.Decode(&parsed); err != nil {
		return nil, err
	}
	if len(bytes.TrimLeft(raw[dec.InputOffset():], " \t\r\n")) != 0 {
		return nil, fmt.Errorf("unexpected trailing JSON data")
	}
	var trailing any
	if err := dec.Decode(&trailing); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("unexpected trailing JSON value")
		}
		return nil, err
	}
	if err := checkJSONIntDigits(parsed); err != nil {
		return nil, err
	}
	return parsed, nil
}

// unescapeLineSeparators restores raw UTF-8 U+2028 and U+2029 when not preceded
// by an odd number of backslashes, matching Python json.dumps(..., ensure_ascii=False).
func unescapeLineSeparators(src []byte) []byte {
	if !bytes.Contains(src, []byte(`\u202`)) {
		return src
	}
	out := make([]byte, 0, len(src))
	for i := 0; i < len(src); {
		if i+6 <= len(src) && src[i] == '\\' && src[i+1] == 'u' && src[i+2] == '2' && src[i+3] == '0' && src[i+4] == '2' && (src[i+5] == '8' || src[i+5] == '9') {
			slashes := 0
			for j := i - 1; j >= 0 && src[j] == '\\'; j-- {
				slashes++
			}
			if slashes%2 == 0 {
				if src[i+5] == '8' {
					out = append(out, "\u2028"...)
				} else {
					out = append(out, "\u2029"...)
				}
				i += 6
				continue
			}
		}
		out = append(out, src[i])
		i++
	}
	return out
}
