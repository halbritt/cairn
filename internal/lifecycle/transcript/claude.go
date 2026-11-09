package transcript

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"slices"
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
	return claudeProjectSlugPoints([]rune(workspace))
}
func claudeProjectSlugPoints(workspace []rune) (string, error) {
	if len(workspace) == 0 || workspace[0] != '/' {
		return "", invalid("native session file cannot be derived from the binding")
	}
	var b strings.Builder
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
	if state.Raw != nil {
		workspace := state.Raw.Get("workspace")
		if workspace == nil || workspace.Kind != value.String {
			return "", invalid("native session file cannot be derived from the binding")
		}
	}
	if !strings.HasPrefix(cfg.ConfigHome, "/") || !uuidPattern.MatchString(state.NativeSessionID) {
		return "", invalid("native session file cannot be derived from the binding")
	}
	points := state.WorkspacePoints
	if points == nil {
		points = []rune(state.Workspace)
	}
	slug, e := claudeProjectSlugPoints(points)
	if e != nil {
		return "", e
	}
	home := cfg.ConfigHome
	if cfg.ConfigHomePoints != nil {
		if cfg.Runtime == nil || cfg.Runtime.Filesystem == nil {
			return "", &value.ProfileUnavailable{}
		}
		var err error
		home, err = cfg.Runtime.Filesystem.Encode(cfg.ConfigHomePoints)
		if err != nil {
			return "", err
		}
	}
	return strings.TrimRight(posixPath(home), "/") + "/projects/" + slug + "/" + state.NativeSessionID + ".jsonl", nil
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
	view, err := findDaemonWorkerViewAt(procRoot, cfg, state, p)
	return view.Ref, err
}
func findDaemonWorkerViewAt(procRoot string, cfg ClaudeConfig, state SessionState, p string) (coordination.ProcView, error) {
	dirs := coordination.ClaudeExeDirs(cfg.Harness, cfg.HasProcessExeDirs, cfg.ProcessExeDirs, cfg.HomeDir)
	entries, e := os.ReadDir(procRoot)
	if e != nil {
		return coordination.ProcView{}, invalid("daemon process directory is unreadable", e)
	}
	found := []coordination.ProcView{}
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
			found = append(found, view)
		}
	}
	if len(found) != 1 {
		return coordination.ProcView{}, invalid(fmt.Sprintf("expected exactly one daemon worker for this conversation, found %d", len(found)))
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
	if cfg.Runtime == nil {
		cfg.Runtime = r.Runtime
	}
	return DaemonStartClaimEvidenceAt(cfg, state, attempt, r.ExpectedUID, r.Profile)
}
func DaemonStartClaimEvidenceAt(cfg ClaudeConfig, state SessionState, attempt Attempt, uid uint32, profile *value.SourceReaderProfile) (map[string]any, error) {
	if cfg.Runtime == nil {
		cfg.Runtime = NewReader(profile).Runtime
	}
	p, e := claudePath(cfg, state)
	if e != nil {
		return nil, e
	}
	f, st, e := openOwner(p, uid)
	if e != nil {
		return nil, ownerRefusal(e, "native transcript is not a readable bounded owner file")
	}
	defer f.Close()
	if st.Size > ClaudeTranscriptLimit {
		return nil, invalid("native transcript is not a readable bounded owner file")
	}
	raw, e := readAt(f, 0, st.Size)
	if e != nil {
		return nil, e
	}
	claimed, e := claimTime(cfg.Runtime, ClaimDaemon, attempt.CreatedAt)
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
	if !claimed.Aware {
		return nil, &value.Error{Class: "TypeError", Message: "can't compare offset-naive and offset-aware datetimes"}
	}
	if epoch(t) >= claimed.Seconds {
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

func resolveLoose(p string, runtime *SourceRuntime) (string, error) {
	if runtime == nil || runtime.Filesystem == nil {
		return "", &value.ProfileUnavailable{}
	}
	points, err := runtime.Filesystem.Decode(p)
	if err != nil {
		return "", err
	}
	resolved, err := runtime.Filesystem.Resolve(&value.Value{Kind: value.String, String: points})
	if err != nil {
		return "", err
	}
	return runtime.Filesystem.Encode(resolved)
}
func resolvePoints(points []rune, runtime *SourceRuntime) ([]rune, error) {
	if runtime == nil || runtime.Filesystem == nil {
		return nil, &value.ProfileUnavailable{}
	}
	return runtime.Filesystem.Resolve(&value.Value{Kind: value.String, String: points})
}
func indexPoints(s, needle []rune) int {
	for i := 0; i+len(needle) <= len(s); i++ {
		if slices.Equal(s[i:i+len(needle)], needle) {
			return i
		}
	}
	return -1
}
func nativeCommand(content *Record) (command []rune, paths [3][]rune, ok bool) {
	if content == nil || content.Kind != value.String {
		return
	}
	prefix := []rune("UserPromptSubmit operation blocked by hook:\n[")
	i := indexPoints(content.String, prefix)
	if i < 0 {
		return
	}
	rest := content.String[i+len(prefix):]
	end := indexPoints(rest, []rune("]: "))
	if end < 0 {
		command = rest
	} else {
		command = rest[:end]
	}
	parts := [][]rune{}
	start := 0
	for i, r := range command {
		if r == ' ' {
			parts = append(parts, command[start:i])
			start = i + 1
		}
	}
	parts = append(parts, command[start:])
	if len(parts) != 5 || !slices.Equal(parts[2], []rune("hook")) || !slices.Equal(parts[3], []rune("--config")) {
		return
	}
	paths = [3][]rune{parts[0], parts[1], parts[4]}
	for _, p := range paths {
		if len(p) < 2 || p[0] != '/' {
			return
		}
		for _, r := range p {
			if r == ']' || pythonWhitespace(r) {
				return
			}
		}
	}
	suffix := []rune("/coordination.py")
	if len(paths[1]) < len(suffix)+2 || !slices.Equal(paths[1][len(paths[1])-len(suffix):], suffix) {
		return
	}
	ok = true
	return
}
func NativeRefusalProof(cfg ClaudeConfig, state SessionState, attempt Attempt, ls []Locator, profile *value.SourceReaderProfile) (map[string]any, error) {
	return NewReader(profile).NativeRefusalProof(cfg, state, attempt, ls)
}
func (r Reader) NativeRefusalProof(cfg ClaudeConfig, state SessionState, attempt Attempt, ls []Locator) (map[string]any, error) {
	if cfg.Runtime == nil {
		cfg.Runtime = r.Runtime
	}
	return NativeRefusalProofAt(cfg, state, attempt, ls, r.ExpectedUID, r.Profile)
}
func NativeRefusalProofAt(cfg ClaudeConfig, state SessionState, attempt Attempt, ls []Locator, uid uint32, profile *value.SourceReaderProfile) (map[string]any, error) {
	if cfg.Runtime == nil {
		cfg.Runtime = NewReader(profile).Runtime
	}
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
	if state.Raw != nil {
		agent := state.Raw.Get("agent")
		state.AgentID, e = sourceFieldText(agent, "agent_id", false)
		if e != nil {
			return nil, e
		}
		state.ExecutionID, e = sourceFieldText(agent, "execution_id", false)
		if e != nil {
			return nil, e
		}
	}
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
	content := field(third, "content")
	if content != nil && content.Kind != value.String {
		return nil, &value.Error{Class: "AttributeError", Message: "refusal content has no partition method"}
	}
	command, paths, validCommand := nativeCommand(content)
	if validCommand {
		configPoints := cfg.ConfigPathPoints
		if configPoints == nil {
			if cfg.Runtime == nil || cfg.Runtime.Filesystem == nil {
				return nil, &value.ProfileUnavailable{}
			}
			configPoints, e = cfg.Runtime.Filesystem.Decode(cfg.ConfigPath)
			if e != nil {
				return nil, e
			}
		}
		a, err := resolvePoints(paths[2], cfg.Runtime)
		if err != nil {
			return nil, err
		}
		b, err := resolvePoints(configPoints, cfg.Runtime)
		if err != nil {
			return nil, err
		}
		validCommand = slices.Equal(a, b)
	}
	expected := append([]rune("UserPromptSubmit operation blocked by hook:\n["), command...)
	expected = append(expected, []rune("]: "+NativeRefusalMessage+"\n\n\nOriginal prompt: "+channel)...)
	if !keys(third, NativeRefusalKeys...) || !stringIs(field(third, "type"), "system") || !stringIs(field(third, "subtype"), "informational") || !stringIs(field(third, "level"), "warning") || !isBool(field(third, "preventContinuation"), true) || !isBool(field(third, "isSidechain"), false) || !isBool(field(third, "isMeta"), false) || !stringIs(field(third, "sessionId"), state.NativeSessionID) || !validCommand || content == nil || content.Kind != value.String || !slices.Equal(content.String, expected) {
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
	claimed, e := claimTime(cfg.Runtime, ClaimNative, attempt.CreatedAt)
	if e != nil {
		return nil, e
	}
	if !(times[0] <= times[1] && times[1] <= claimed.Seconds && claimed.Seconds <= times[2]) || times[2]-times[0] > NativeHookTimeoutSeconds || attempt.Delivery.Attempts != 1 || attempt.Delivery.State != "leased" {
		return nil, invalid("native chronology does not bracket the single claim of this delivery")
	}
	return map[string]any{"refusal_uuid": field(third, "uuid"), "refused_at": field(third, "timestamp"), "claude_version": field(third, "version"), "records": ls}, nil
}

func sameInode(a, b syscall.Stat_t) bool { return a.Dev == b.Dev && a.Ino == b.Ino }

func pythonWhitespace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
