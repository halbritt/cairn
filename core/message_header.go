package core

import (
	"fmt"
	"strings"
	"time"
	"unicode/utf8"
)

const MaxEventSummaryBytes = 280

// MessageHeader represents the structured header of an agent event (what
// changed, action needed, and due deadline) rendered ahead of the body.
type MessageHeader struct {
	Summary      string     `json:"summary,omitempty"`
	ActionNeeded string     `json:"action_needed"`
	Due          *time.Time `json:"due,omitempty"`
	Supersedes   string     `json:"supersedes,omitempty"`
}

func hasDisallowedSummaryRune(s string) bool {
	for _, r := range s {
		if r < 0x20 || r == 0x7f || (r >= 0x80 && r <= 0x9f) ||
			r == 0x061c || r == 0x200e || r == 0x200f ||
			(r >= 0x2028 && r <= 0x202e) || (r >= 0x2066 && r <= 0x2069) {
			return true
		}
	}
	return false
}

func ValidateEventSummary(summary string) error {
	if summary == "" {
		return nil
	}
	if !utf8.ValidString(summary) || hasDisallowedSummaryRune(summary) {
		return failure("INVALID_REQUEST", "event summary must be single-line UTF-8 text without control or bidi characters")
	}
	if strings.TrimSpace(summary) == "" {
		return failure("INVALID_REQUEST", "event summary cannot be blank")
	}
	if len(summary) > MaxEventSummaryBytes {
		return failure("INVALID_REQUEST", fmt.Sprintf("event summary must be at most %d UTF-8 bytes", MaxEventSummaryBytes))
	}
	return nil
}

func (e AgentEvent) Header() *MessageHeader {
	due := e.ExpectNextBy
	if due == nil {
		due = e.ExpectBy
	}
	if due == nil {
		due = e.TaskDeadline
	}
	if e.Summary == "" && e.Action == "" && due == nil && e.Supersedes == "" {
		return nil
	}
	action := e.Action
	if action == "" {
		switch e.Kind {
		case "request":
			action = "complete"
		default:
			action = "review"
		}
	}
	return &MessageHeader{
		Summary:      e.Summary,
		ActionNeeded: action,
		Due:          due,
		Supersedes:   e.Supersedes,
	}
}

// TruncateCleanUTF8 truncates text to at most maxBytes UTF-8 bytes on a clean
// newline, whitespace, or UTF-8 rune boundary, returning the prefix, its byte
// length, and the number of omitted UTF-8 bytes.
func TruncateCleanUTF8(text string, maxBytes int) (prefix string, includedBytes int, omittedBytes int) {
	total := len(text)
	if maxBytes <= 0 {
		return "", 0, total
	}
	if total <= maxBytes {
		return text, total, 0
	}
	cut := maxBytes
	for cut > 0 && !utf8.RuneStart(text[cut]) {
		cut--
	}
	for cut > 0 {
		r, size := utf8.DecodeLastRuneInString(text[:cut])
		if r == utf8.RuneError && size == 1 && (cut < 3 || text[cut-3:cut] != "\xef\xbf\xbd") {
			cut--
			continue
		}
		break
	}
	if cut == 0 {
		return "", 0, total
	}
	half := cut / 2
	searchStart := cut - 256
	if searchStart < half {
		searchStart = half
	}
	window := text[searchStart:cut]
	if nl := strings.LastIndexByte(window, '\n'); nl >= 0 {
		cand := searchStart + nl
		if cand > 0 && text[cand-1] == '\r' {
			cand--
		}
		if cand > 0 {
			cut = cand
		}
	} else if ws := strings.LastIndexAny(window, " \t"); ws >= 0 {
		cand := searchStart + ws
		if cand > 0 {
			cut = cand
		}
	}
	return text[:cut], cut, total - cut
}

func FormatTruncationMarker(omittedBytes, totalBytes int, pullCue string) string {
	return fmt.Sprintf("\n[... truncated %d of %d UTF-8 bytes; pull full source with %s ...]", omittedBytes, totalBytes, pullCue)
}
