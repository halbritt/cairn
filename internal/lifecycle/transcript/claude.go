package transcript

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"unicode"

	"github.com/halbritt/cairn/internal/lifecycle/coordination"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/lifecycle/wake"
	"github.com/halbritt/cairn/internal/procpeer"
)

func posixPath(s string) string {
	prefix := ""
	if strings.HasPrefix(s, "/") {
		prefix = "/"
	}
	if strings.HasPrefix(s, "//") && !strings.HasPrefix(s, "///") {
		prefix = "//"
	}
	parts := []string{}
	for _, part := range strings.Split(s, "/") {
		if part != "" && part != "." {
			parts = append(parts, part)
		}
	}
	if len(parts) == 0 {
		if prefix != "" {
			return prefix
		}
		return "."
	}
	return prefix + strings.Join(parts, "/")
}
func posixParent(s string) string {
	s = posixPath(s)
	i := strings.LastIndex(s, "/")
	if i < 0 {
		return "."
	}
	if i == 0 {
		return "/"
	}
	if i == 1 && strings.HasPrefix(s, "//") {
		return "//"
	}
	return s[:i]
}
func ClaudeProjectSlug(workspace string) (string, error) {
	if !strings.HasPrefix(workspace, "/") {
		return "", invalid("native session file cannot be derived from the binding")
	}
	var b strings.Builder
	// The pinned source applies re.sub to the original workspace string,
	// including repeated slashes and dot components, without Path normalization.
	for _, c := range workspace {
		if c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' {
			b.WriteRune(c)
		} else {
			b.WriteByte('-')
		}
	}
	return b.String(), nil
}
func claudePath(cfg ClaudeConfig, state SessionState) (string, error) {
	if !strings.HasPrefix(cfg.ConfigHome, "/") || !uuidPattern.MatchString(state.NativeSessionID) {
		return "", invalid("native session file cannot be derived from the binding")
	}
	slug, e := ClaudeProjectSlug(state.Workspace)
	if e != nil {
		return "", e
	}
	return strings.TrimRight(posixPath(cfg.ConfigHome), "/") + "/projects/" + slug + "/" + state.NativeSessionID + ".jsonl", nil
}

// ClaudeTranscriptPath derives the path; no directory enumeration or caller
// supplied transcript path is used. At additionally verifies the file owner.
func ClaudeTranscriptPath(cfg ClaudeConfig, state SessionState) (string, error) {
	return claudePath(cfg, state)
}
func ClaudeTranscriptPathAt(cfg ClaudeConfig, state SessionState, uid uint32) (string, error) {
	p, e := claudePath(cfg, state)
	if e != nil {
		return "", e
	}
	f, st, e := openOwner(p, uid)
	if e != nil {
		return "", invalid("native transcript is not a readable bounded owner file", e)
	}
	defer f.Close()
	if st.Size <= 0 || st.Size > ClaudeTranscriptLimit {
		return "", invalid("native transcript is not a readable bounded owner file")
	}
	return p, nil
}
func FindDaemonWorkerAt(procRoot string, cfg ClaudeConfig, state SessionState, uid uint32) (procpeer.ProcessRef, error) {
	p, e := claudePath(cfg, state)
	if e != nil {
		return procpeer.ProcessRef{}, e
	}
	dirs := coordination.ClaudeExeDirs(cfg.Harness, cfg.HasProcessExeDirs, cfg.ProcessExeDirs, cfg.HomeDir)
	entries, e := os.ReadDir(procRoot)
	if e != nil {
		return procpeer.ProcessRef{}, invalid("daemon process directory is unreadable", e)
	}
	found := []procpeer.ProcessRef{}
	for _, entry := range entries {
		pid, e := strconv.Atoi(entry.Name())
		if e != nil || pid < 2 {
			continue
		}
		view, e := coordination.ReadProcViewAt(procRoot, pid)
		if e != nil {
			continue
		}
		allowed := false
		for _, dir := range dirs {
			if posixParent(view.Exe) == dir {
				allowed = true
				break
			}
		}
		if view.Exe != "" && allowed && len(view.Argv) > 2 && view.Argv[1] == "--resume" && view.Argv[2] == p && coordination.DaemonWorkerOfAt(procRoot, view.Ref, state.Process) {
			found = append(found, view.Ref)
		}
	}
	if len(found) != 1 {
		return procpeer.ProcessRef{}, invalid(fmt.Sprintf("expected exactly one daemon worker for this conversation, found %d", len(found)))
	}
	return found[0], nil
}
func FindDaemonWorker(cfg ClaudeConfig, state SessionState) (procpeer.ProcessRef, error) {
	return FindDaemonWorkerAt("/proc", cfg, state, uint32(os.Getuid()))
}

func DaemonStartClaimEvidence(cfg ClaudeConfig, state SessionState, attempt Attempt, profile *value.SourceReaderProfile) (map[string]any, error) {
	return NewReader(profile).DaemonStartClaimEvidence(cfg, state, attempt)
}
func (r Reader) DaemonStartClaimEvidence(cfg ClaudeConfig, state SessionState, attempt Attempt) (map[string]any, error) {
	return DaemonStartClaimEvidenceAt(cfg, state, attempt, r.ExpectedUID, r.Profile)
}
func DaemonStartClaimEvidenceAt(cfg ClaudeConfig, state SessionState, attempt Attempt, uid uint32, profile *value.SourceReaderProfile) (map[string]any, error) {
	p, e := claudePath(cfg, state)
	if e != nil {
		return nil, e
	}
	f, st, e := openOwner(p, uid)
	if e != nil {
		return nil, invalid("native transcript is not a readable bounded owner file", e)
	}
	defer f.Close()
	if st.Size > ClaudeTranscriptLimit {
		return nil, invalid("native transcript is not a readable bounded owner file")
	}
	raw, e := readAt(f, 0, st.Size)
	if e != nil {
		return nil, invalid("native transcript has an unreadable record", e)
	}
	claimed, e := claimTime(attempt.CreatedAt)
	if e != nil {
		return nil, e
	}
	records := []*Record{}
	last := -1
	// bytes.splitlines recognizes LF, CR and CRLF, without a trailing empty line.
	lines := bytes.Split(bytes.ReplaceAll(bytes.ReplaceAll(raw, []byte("\r\n"), []byte("\n")), []byte("\r"), []byte("\n")), []byte("\n"))
	if len(raw) == 0 {
		lines = nil
	} else if len(lines[len(lines)-1]) == 0 {
		lines = lines[:len(lines)-1]
	}
	for _, line := range lines {
		record, e := decodeObject(profile, SiteDaemonStartClaim, line)
		if e != nil {
			var te *TranscriptError
			if errors.As(e, &te) {
				return nil, invalid("native transcript has a non-object record", e)
			}
			if sourceReadError(e, "UnicodeDecodeError", "ValueError", "JSONDecodeError") {
				return nil, invalid("native transcript has an unreadable record", e)
			}
			return nil, e
		}
		records = append(records, record)
		timestamp := field(record, "timestamp")
		if timestamp != nil && timestamp.Kind == value.String {
			last = len(records) - 1
		}
	}
	if last < 0 {
		return nil, invalid("native transcript has no timestamped turn record")
	}
	t, e := NativeTime(field(records[last], "timestamp"))
	if e != nil {
		return nil, e
	}
	if epoch(t) >= claimed {
		return nil, invalid("native transcript records activity at or after the claim")
	}
	allowed := map[string]bool{"last-prompt": true, "cost-state": true, "ai-title": true, "agent-name": true, "custom-title": true, "summary": true, "artifact-comment-monitor": true, "artifact-autoreact-ledger": true}
	for _, record := range records[last+1:] {
		if !allowed[text(field(record, "type"))] || field(record, "timestamp") != nil {
			return nil, invalid("native transcript records a turn or unknown record after the claim")
		}
	}
	return map[string]any{"last_turn_record_at": field(records[last], "timestamp"), "records_after": len(records) - last - 1, "transcript_bytes": len(raw)}, nil
}

var refusalCommand = regexp.MustCompile(`^(/[^\s\]]+) (/[^\s\]]+/coordination\.py) hook --config (/[^\s\]]+)$`)

func resolveLoose(p string) (string, error) {
	p, e := filepath.Abs(p)
	if e != nil {
		return "", e
	}
	rest := []string{}
	for {
		resolved, e := filepath.EvalSymlinks(p)
		if e == nil {
			for i := len(rest) - 1; i >= 0; i-- {
				resolved = filepath.Join(resolved, rest[i])
			}
			return resolved, nil
		}
		if !os.IsNotExist(e) {
			return "", e
		}
		parent := filepath.Dir(p)
		if parent == p {
			return "", e
		}
		rest = append(rest, filepath.Base(p))
		p = parent
	}
}
func NativeRefusalProof(cfg ClaudeConfig, state SessionState, attempt Attempt, ls []Locator, profile *value.SourceReaderProfile) (map[string]any, error) {
	return NewReader(profile).NativeRefusalProof(cfg, state, attempt, ls)
}
func (r Reader) NativeRefusalProof(cfg ClaudeConfig, state SessionState, attempt Attempt, ls []Locator) (map[string]any, error) {
	return NativeRefusalProofAt(cfg, state, attempt, ls, r.ExpectedUID, r.Profile)
}
func NativeRefusalProofAt(cfg ClaudeConfig, state SessionState, attempt Attempt, ls []Locator, uid uint32, profile *value.SourceReaderProfile) (map[string]any, error) {
	if len(ls) != 3 || !contiguous(ls) {
		return nil, invalid("native records are not one contiguous enqueue, dequeue, refusal sequence")
	}
	p, e := claudePath(cfg, state)
	if e != nil {
		return nil, e
	}
	records := make([]*Record, 3)
	for i, l := range ls {
		records[i], e = ReadNativeRecordAt(p, l, uid, profile)
		if e != nil {
			return nil, e
		}
	}
	first, second, third := records[0], records[1], records[2]
	channel := ""
	for _, message := range wake.WakeMessages(wake.SessionRef{AgentID: state.AgentID, ExecutionID: state.ExecutionID}, attempt.Delivery.DeliveryID) {
		candidate := wake.FormatClaudeChannelPrompt(wake.ClaudeChannelMeta{NativeSessionID: state.NativeSessionID, AgentID: state.AgentID, ExecutionID: state.ExecutionID, DeliveryID: attempt.Delivery.DeliveryID}, message)
		if stringIs(field(first, "content"), candidate) {
			channel = candidate
		}
	}
	if !keys(first, "type", "operation", "timestamp", "sessionId", "content") || !stringIs(field(first, "type"), "queue-operation") || !stringIs(field(first, "operation"), "enqueue") || !stringIs(field(first, "sessionId"), state.NativeSessionID) || channel == "" {
		return nil, invalid("native enqueue record is not this exact channel wake")
	}
	if !keys(second, "type", "operation", "timestamp", "sessionId") || !stringIs(field(second, "type"), "queue-operation") || !stringIs(field(second, "operation"), "dequeue") || !stringIs(field(second, "sessionId"), state.NativeSessionID) {
		return nil, invalid("native dequeue record shape is unknown")
	}
	_, after, hasPrefix := strings.Cut(text(field(third, "content")), "UserPromptSubmit operation blocked by hook:\n[")
	command, _, _ := strings.Cut(after, "]: ")
	match := refusalCommand.FindStringSubmatch(command)
	validCommand := hasPrefix && match != nil
	if validCommand {
		for _, path := range match[1:] {
			if strings.ContainsFunc(path, pythonWhitespace) {
				validCommand = false
				break
			}
		}
	}
	if validCommand {
		a, ea := resolveLoose(match[3])
		b, eb := resolveLoose(cfg.ConfigPath)
		validCommand = ea == nil && eb == nil && a == b
	}
	if !keys(third, NativeRefusalKeys...) || !stringIs(field(third, "type"), "system") || !stringIs(field(third, "subtype"), "informational") || !stringIs(field(third, "level"), "warning") || !isBool(field(third, "preventContinuation"), true) || !isBool(field(third, "isSidechain"), false) || !isBool(field(third, "isMeta"), false) || !stringIs(field(third, "sessionId"), state.NativeSessionID) || !validCommand || !stringIs(field(third, "content"), "UserPromptSubmit operation blocked by hook:\n["+command+"]: "+NativeRefusalMessage+"\n\n\nOriginal prompt: "+channel) {
		return nil, invalid("native refusal record is not this binding's post-claim refusal of this wake")
	}
	times := make([]float64, 3)
	for i, record := range records {
		t, e := NativeTime(field(record, "timestamp"))
		if e != nil {
			return nil, e
		}
		times[i] = epoch(t)
	}
	claimed, e := claimTime(attempt.CreatedAt)
	if e != nil {
		return nil, e
	}
	if !(times[0] <= times[1] && times[1] <= claimed && claimed <= times[2]) || times[2]-times[0] > NativeHookTimeoutSeconds || attempt.Delivery.Attempts != 1 || attempt.Delivery.State != "leased" {
		return nil, invalid("native chronology does not bracket the single claim of this delivery")
	}
	return map[string]any{"refusal_uuid": field(third, "uuid"), "refused_at": field(third, "timestamp"), "claude_version": field(third, "version"), "records": ls}, nil
}

func sameInode(a, b syscall.Stat_t) bool { return a.Dev == b.Dev && a.Ino == b.Ino }

func pythonWhitespace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
