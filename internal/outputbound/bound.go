// Package outputbound bounds oversized tool/command outputs entering agent
// context while spooling complete raw outputs atomically to private storage.
package outputbound

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
)

const (
	// DefaultOutputMaxBytes is the default conservative UTF-8 byte cap for
	// bounded non-strict tool and command outputs (~3,000 conservative tokens).
	DefaultOutputMaxBytes = 12000

	EnvUnboundedToolOutput = "CAIRN_UNBOUNDED_TOOL_OUTPUT"
	EnvToolOutputMaxBytes  = "CAIRN_TOOL_OUTPUT_MAX_BYTES"
	EnvToolOutputDir       = "CAIRN_TOOL_OUTPUT_DIR"
)

var diagRegexp = regexp.MustCompile(`(?i)(error|panic|fatal|fail|exception|traceback|assert|exit code|signal|refused|stack)`)

// ExecMeta preserves command/tool execution metadata across bounding.
type ExecMeta struct {
	ExitCode  *int   `json:"exit_code,omitempty"`
	Signal    string `json:"signal,omitempty"`
	ErrorCode string `json:"error_code,omitempty"`
}

// Result describes either an unmodified output or a bounded excerpt with its
// atomic 0600 full-output spool path.
type Result struct {
	Truncated      bool   `json:"truncated"`
	Output         string `json:"output"`
	Excerpt        string `json:"excerpt,omitempty"`
	FullOutputPath string `json:"full_output_path,omitempty"`
	TotalBytes     int    `json:"total_bytes"`
	OmittedBytes   int    `json:"omitted_bytes,omitempty"`
	ExitCode       *int   `json:"exit_code,omitempty"`
	Signal         string `json:"signal,omitempty"`
	ErrorCode      string `json:"error_code,omitempty"`
}

func isTruthy(raw string) bool {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "1", "true", "yes":
		return true
	default:
		return false
	}
}

// ResolveMaxBytes resolves the effective UTF-8 byte cap using:
// 1. CAIRN_UNBOUNDED_TOOL_OUTPUT=1/true/yes -> 0 (unbounded)
// 2. explicit flagVal == 0 -> 0 (unbounded) or flagVal > 0 -> flagVal
// 3. envNames in order (plus CAIRN_TOOL_OUTPUT_MAX_BYTES fallback)
// 4. DefaultOutputMaxBytes
// Pass flagVal < 0 when no explicit flag override was provided.
func ResolveMaxBytes(flagVal int, envNames ...string) int {
	return ResolveMaxBytesWithLookup(flagVal, os.Getenv, envNames...)
}

// ResolveMaxBytesWithLookup resolves the effective byte cap using getenv.
func ResolveMaxBytesWithLookup(flagVal int, getenv func(string) string, envNames ...string) int {
	if getenv == nil {
		getenv = os.Getenv
	}
	if isTruthy(getenv(EnvUnboundedToolOutput)) {
		return 0
	}
	if flagVal == 0 {
		return 0
	}
	if flagVal > 0 {
		return flagVal
	}
	seenFallback := false
	check := append([]string{}, envNames...)
	for _, name := range check {
		if name == EnvToolOutputMaxBytes {
			seenFallback = true
			break
		}
	}
	if !seenFallback {
		check = append(check, EnvToolOutputMaxBytes)
	}
	for _, name := range check {
		if name == "" {
			continue
		}
		raw := strings.TrimSpace(getenv(name))
		if raw == "" {
			continue
		}
		if n, err := strconv.Atoi(raw); err == nil && n >= 0 {
			return n
		}
	}
	return DefaultOutputMaxBytes
}

// DefaultSpoolDir resolves the private directory used for full tool outputs.
// Resolution order: $CAIRN_TOOL_OUTPUT_DIR -> $XDG_STATE_HOME/cairn/tool-output
// -> $HOME/.local/share/cairn/tool-output. Never uses /tmp.
func DefaultSpoolDir() string {
	return DefaultSpoolDirWithLookup(os.Getenv)
}

// DefaultSpoolDirWithLookup resolves the private spool directory using getenv.
func DefaultSpoolDirWithLookup(getenv func(string) string) string {
	if getenv == nil {
		getenv = os.Getenv
	}
	if dir := strings.TrimSpace(getenv(EnvToolOutputDir)); dir != "" && filepath.IsAbs(dir) {
		return dir
	}
	if xdg := strings.TrimSpace(getenv("XDG_STATE_HOME")); xdg != "" && filepath.IsAbs(xdg) {
		return filepath.Join(xdg, "cairn", "tool-output")
	}
	home := strings.TrimSpace(getenv("HOME"))
	if home == "" {
		if h, err := os.UserHomeDir(); err == nil {
			home = h
		}
	}
	if home == "" {
		return ""
	}
	return filepath.Join(home, ".local", "share", "cairn", "tool-output")
}

func sanitizeExt(ext string) string {
	var b strings.Builder
	for _, r := range ext {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') {
			b.WriteRune(r)
		}
	}
	if b.Len() == 0 {
		return "log"
	}
	return b.String()
}

// SpoolRaw atomically writes raw bytes (0600, private directory) and returns the path.
func SpoolRaw(raw []byte, spoolDir, ext string) (string, error) {
	if spoolDir == "" {
		spoolDir = DefaultSpoolDir()
	}
	if spoolDir == "" {
		return "", fmt.Errorf("cannot resolve tool output spool directory")
	}
	if err := os.MkdirAll(spoolDir, 0o700); err != nil {
		return "", err
	}
	info, err := os.Lstat(spoolDir)
	if err != nil {
		return "", err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return "", fmt.Errorf("spool directory %q must be a real directory", spoolDir)
	}
	name := fmt.Sprintf("%s-%s.%s", time.Now().UTC().Format("20060102T150405Z"), uuid.NewString(), sanitizeExt(ext))
	finalPath := filepath.Join(spoolDir, name)
	tmp, err := os.CreateTemp(spoolDir, ".spool-*")
	if err != nil {
		return "", err
	}
	tmpPath := tmp.Name()
	defer os.Remove(tmpPath)
	if err := tmp.Chmod(0o600); err != nil {
		_ = tmp.Close()
		return "", err
	}
	for len(raw) > 0 {
		n, wErr := tmp.Write(raw)
		if wErr != nil {
			_ = tmp.Close()
			return "", wErr
		}
		raw = raw[n:]
	}
	if err := tmp.Close(); err != nil {
		return "", err
	}
	if err := os.Rename(tmpPath, finalPath); err != nil {
		return "", err
	}
	return finalPath, nil
}

// ExcerptWithSpool returns raw unmodified when maxBytes <= 0 or len(raw) <= maxBytes.
// Otherwise it atomically spools raw (0600) to spoolDir/<utc>-<uuid>.<ext> and
// constructs a clean UTF-8/line-boundary head + middle diagnostic + tail excerpt.
func ExcerptWithSpool(raw []byte, maxBytes int, spoolDir, ext string, meta ExecMeta) (Result, error) {
	total := len(raw)
	if maxBytes <= 0 || total <= maxBytes {
		return Result{
			Truncated:  false,
			Output:     string(raw),
			TotalBytes: total,
			ExitCode:   meta.ExitCode,
			Signal:     meta.Signal,
			ErrorCode:  meta.ErrorCode,
		}, nil
	}
	fullPath, err := SpoolRaw(raw, spoolDir, ext)
	if err != nil {
		return Result{}, err
	}
	excerpt, omitted := BuildExcerpt(raw, maxBytes, fullPath)
	return Result{
		Truncated:      true,
		Output:         excerpt,
		Excerpt:        excerpt,
		FullOutputPath: fullPath,
		TotalBytes:     total,
		OmittedBytes:   omitted,
		ExitCode:       meta.ExitCode,
		Signal:         meta.Signal,
		ErrorCode:      meta.ErrorCode,
	}, nil
}

// BuildExcerpt builds a bounded UTF-8 excerpt (~35% head, ~30% unique diagnostic
// lines from the omitted middle, ~35% tail) referencing fullPath.
func BuildExcerpt(raw []byte, maxBytes int, fullPath string) (string, int) {
	total := len(raw)
	if maxBytes <= 0 || total <= maxBytes {
		return string(raw), 0
	}
	text := string(raw)
	if !utf8.Valid(raw) {
		text = strings.ToValidUTF8(text, "\uFFFD")
	}

	footerTemplate := fmt.Sprintf("\n[... omitted %d of %d bytes; full output: %s ...]\n", total, total, fullPath)
	diagHeader := "\n[... diagnostic lines from omitted middle ...]\n"
	markerReserve := len(footerTemplate) + len(diagHeader)
	if maxBytes <= markerReserve+32 {
		// Minimal compact excerpt for very small caps.
		compactFooter := fmt.Sprintf("\n[... full output (%d bytes): %s ...]", total, fullPath)
		if len(compactFooter) >= maxBytes {
			trimmed, _, _ := truncatePrefixUTF8(compactFooter, maxBytes)
			return trimmed, max(total-len(trimmed), 1)
		}
		avail := maxBytes - len(compactFooter)
		head, headEnd, _ := truncatePrefixUTF8(text, avail)
		return head + compactFooter, max(total-headEnd, 1)
	}

	contentBudget := maxBytes - markerReserve
	headBudget := contentBudget * 35 / 100
	diagBudget := contentBudget * 30 / 100
	tailBudget := contentBudget - headBudget - diagBudget

	head, headEnd, _ := truncatePrefixUTF8(text, headBudget)
	tail, tailStartRel := truncateSuffixUTF8(text[headEnd:], tailBudget)
	tailStart := headEnd + tailStartRel

	middle := text[headEnd:tailStart]
	diags, diagLen := extractDiagnosticLines(middle, diagBudget)

	if len(diags) == 0 && diagBudget > 0 {
		// Reallocate unused diagnostic budget evenly to head and tail.
		extraHead := diagBudget / 2
		extraTail := diagBudget - extraHead
		head, headEnd, _ = truncatePrefixUTF8(text, headBudget+extraHead)
		tail, tailStartRel = truncateSuffixUTF8(text[headEnd:], tailBudget+extraTail)
		tailStart = headEnd + tailStartRel
		diags, diagLen = extractDiagnosticLines(text[headEnd:tailStart], diagBudget/4)
	}

	included := len(head) + diagLen + len(tail)
	omitted := max(total-included, 1)
	footer := fmt.Sprintf("\n[... omitted %d of %d bytes; full output: %s ...]\n", omitted, total, fullPath)

	var out strings.Builder
	out.WriteString(head)
	if len(diags) > 0 {
		out.WriteString(diagHeader)
		out.WriteString(strings.Join(diags, "\n"))
	}
	out.WriteString(footer)
	out.WriteString(tail)

	assembled := out.String()
	if len(assembled) > maxBytes {
		assembled, _, _ = truncatePrefixUTF8(assembled, maxBytes)
	}
	return assembled, omitted
}

func extractDiagnosticLines(middle string, maxBytes int) ([]string, int) {
	if maxBytes <= 0 || middle == "" {
		return nil, 0
	}
	seen := make(map[string]struct{})
	var lines []string
	used := 0
	for _, line := range strings.Split(middle, "\n") {
		trimmed := strings.TrimSpace(strings.TrimRight(line, "\r"))
		if trimmed == "" || !diagRegexp.MatchString(trimmed) {
			continue
		}
		if _, dup := seen[trimmed]; dup {
			continue
		}
		seen[trimmed] = struct{}{}
		sep := 0
		if len(lines) > 0 {
			sep = 1
		}
		remaining := maxBytes - used - sep
		if remaining <= 0 {
			break
		}
		clean := strings.TrimRight(line, "\r")
		if len(clean) > remaining {
			clean, _, _ = truncatePrefixUTF8(clean, remaining)
			if clean == "" {
				break
			}
		}
		lines = append(lines, clean)
		used += sep + len(clean)
	}
	return lines, used
}

func truncatePrefixUTF8(text string, maxBytes int) (prefix string, included int, omitted int) {
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

func truncateSuffixUTF8(text string, maxBytes int) (suffix string, start int) {
	total := len(text)
	if maxBytes <= 0 || total == 0 {
		return "", total
	}
	if total <= maxBytes {
		return text, 0
	}
	start = total - maxBytes
	for start < total && !utf8.RuneStart(text[start]) {
		start++
	}
	if start >= total {
		return "", total
	}
	// Prefer starting after a newline in the first half of the tail window.
	windowEnd := start + min(256, (total-start)/2)
	if nl := strings.IndexByte(text[start:windowEnd], '\n'); nl >= 0 {
		cand := start + nl + 1
		if cand < total {
			start = cand
		}
	}
	return text[start:], start
}
