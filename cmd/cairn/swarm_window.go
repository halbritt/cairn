package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"syscall"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/transcript"
	"github.com/halbritt/cairn/internal/lifecycle/wake"
)

type swarmWindow struct {
	UsedTokens         int64      `json:"used_tokens"`
	WindowTokens       int64      `json:"window_tokens,omitempty"`
	Model              string     `json:"model,omitempty"`
	MeasuredAt         time.Time  `json:"measured_at"`
	Compactions        int        `json:"compactions"`
	CompactionsPartial bool       `json:"compactions_partial,omitempty"`
	LastCompactionAt   *time.Time `json:"last_compaction_at,omitempty"`
	Source             string     `json:"source"`
	NearLimit          bool       `json:"near_limit"`
}

type swarmWindowObservation struct {
	ObservedAt    time.Time              `json:"observed_at"`
	Status        string                 `json:"status"`
	Error         string                 `json:"error,omitempty"`
	Sessions      int                    `json:"sessions"`
	NearLimitRule string                 `json:"near_limit_rule"`
	Windows       map[string]swarmWindow `json:"windows"`
	Unknown       map[string]string      `json:"unknown"`
}

type swarmWindowReader func(ctx context.Context) swarmWindowObservation
type swarmTranscriptOpener func(*os.Root, string) (*os.File, error)

type swarmTranscriptAccess struct {
	open   swarmTranscriptOpener
	stat   func(*os.File) (os.FileInfo, error)
	reader func(*os.File) io.ReaderAt
}

const (
	// Codex's default auto-compact threshold is 90% of the context window.
	// Use that threshold as a common indicator, independent of harness overrides.
	swarmNearLimitRule = "Near limit means the last request used at least 90% of the model context window."
	swarmWindowBlock   = 64 << 10
	swarmWindowMaxTail = 2 << 20
)

type cachedSwarmWindow struct {
	window swarmWindow
	reason string
}

type swarmWindowFile struct {
	home, directory, relative, harness string
	heldDescriptor                     string
	heldIdentity                       swarmTranscriptIdentity
	processAlive                       func() bool
}

type swarmTranscriptIdentity struct {
	device, inode uint64
}

type swarmTranscriptStamp struct {
	identity swarmTranscriptIdentity
	size     int64
	modified time.Time
	changed  time.Time
	harness  string
}

func newTranscriptWindowReader(bindingsDir, procRoot string) swarmWindowReader {
	return transcriptWindowReader(bindingsDir, procRoot, swarmTranscriptAccess{
		open: openSwarmTranscript,
		stat: (*os.File).Stat,
	})
}

func openSwarmTranscript(root *os.Root, name string) (*os.File, error) {
	return root.OpenFile(name, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
}

func transcriptWindowReader(bindingsDir, procRoot string, access swarmTranscriptAccess) swarmWindowReader {
	if procRoot == "" {
		procRoot = "/proc"
	}
	paths := transcript.NewReader(transcript.NewCoordinationProfile("/", os.Getenv, os.LookupEnv))
	paths.ProcRoot = procRoot
	// The cache only saves re-reading transcript bytes. Every observation
	// opens and verifies the current descriptor before reusing a measurement.
	cache := make(map[swarmTranscriptStamp]cachedSwarmWindow)
	gate := make(chan struct{}, 1)
	return func(ctx context.Context) swarmWindowObservation {
		observation := swarmWindowObservation{
			ObservedAt: time.Now().UTC(), Status: "unavailable", NearLimitRule: swarmNearLimitRule,
			Windows: make(map[string]swarmWindow), Unknown: make(map[string]string),
		}
		select {
		case gate <- struct{}{}:
			defer func() { <-gate }()
		case <-ctx.Done():
			observation.Error = ctx.Err().Error()
			return observation
		}
		scan, err := swarmHerdrSessions(ctx, bindingsDir, procRoot)
		observation.Sessions = len(scan.sessions)
		if err != nil {
			observation.Error = err.Error()
			return observation
		}
		resolver := paths
		resolver.Alive = scan.client.ProcessAlive
		next := make(map[swarmTranscriptStamp]cachedSwarmWindow)
		for _, session := range scan.sessions {
			if ctx.Err() != nil {
				observation.Unknown[session.AgentID] = "Context read canceled"
				continue
			}
			file, reason := findSwarmWindowFile(resolver, session)
			if reason != "" {
				observation.Unknown[session.AgentID] = reason
				continue
			}
			current := file.measure(ctx, access, cache, next)
			if current.reason != "" {
				observation.Unknown[session.AgentID] = current.reason
			} else {
				window := current.window
				if window.LastCompactionAt != nil {
					compacted := *window.LastCompactionAt
					window.LastCompactionAt = &compacted
				}
				observation.Windows[session.AgentID] = window
			}
		}
		cache = next
		observation.Status = "ok"
		return observation
	}
}

func findSwarmWindowFile(resolver transcript.Reader, session swarmLocalSession) (swarmWindowFile, string) {
	state := transcript.SessionState{
		NativeSessionID: session.NativeSessionID, Workspace: session.Workspace, Process: session.Process,
	}
	file := swarmWindowFile{home: session.ConfigHome, harness: session.Harness}
	var path string
	var err error
	switch session.Harness {
	case wake.HarnessClaude:
		file.directory = "projects"
		path, err = transcript.ClaudeTranscriptPath(transcript.ClaudeConfig{ConfigHome: session.ConfigHome}, state)
	case wake.HarnessCodex:
		file.directory = "sessions"
		path, err = resolver.CodexRolloutPath(transcript.CodexConfig{ConfigHome: session.ConfigHome}, state)
	case "agy":
		return swarmWindowFile{}, "Antigravity trajectory metadata has no supported per-request decoder"
	case wake.HarnessOpenCode:
		return swarmWindowFile{}, "OpenCode usage is in its SQLite message store, which this reader does not decode"
	case wake.HarnessHermes:
		return swarmWindowFile{}, "Hermes CLI transcripts have no last-request input count"
	default:
		return swarmWindowFile{}, "Harness has no supported per-request usage format"
	}
	if err != nil {
		return swarmWindowFile{}, "Session transcript could not be resolved"
	}
	relative, err := filepath.Rel(filepath.Join(file.home, file.directory), path)
	if err != nil || !filepath.IsLocal(relative) {
		return swarmWindowFile{}, "Session transcript is outside its configured directory"
	}
	file.relative = relative
	if session.Harness == wake.HarnessCodex {
		file.heldDescriptor = findSwarmRolloutDescriptor(resolver.ProcRoot, session.Process.PID, path)
		if file.heldDescriptor == "" {
			return swarmWindowFile{}, "Codex process no longer holds the session rollout"
		}
		file.processAlive = func() bool { return resolver.Alive(session.Process) }
		info, err := os.Stat(file.heldDescriptor)
		if err != nil {
			return swarmWindowFile{}, "Codex process no longer holds the session rollout"
		}
		held, owned := file.stamped(info)
		if !owned || !file.processAlive() {
			return swarmWindowFile{}, "Codex process no longer holds the session rollout"
		}
		file.heldIdentity = held.identity
	}
	return file, ""
}

func findSwarmRolloutDescriptor(procRoot string, pid int, path string) string {
	directory := filepath.Join(procRoot, strconv.Itoa(pid), "fd")
	entries, err := os.ReadDir(directory)
	if err != nil {
		return ""
	}
	for _, entry := range entries {
		fd := filepath.Join(directory, entry.Name())
		if target, err := os.Readlink(fd); err == nil && target == path {
			return fd
		}
	}
	return ""
}

func (file swarmWindowFile) stamped(info os.FileInfo) (swarmTranscriptStamp, bool) {
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) || !info.Mode().IsRegular() {
		return swarmTranscriptStamp{}, false
	}
	return swarmTranscriptStamp{
		identity: swarmTranscriptIdentity{device: uint64(stat.Dev), inode: stat.Ino},
		size:     info.Size(),
		modified: info.ModTime().UTC(),
		changed:  time.Unix(stat.Ctim.Sec, stat.Ctim.Nsec).UTC(),
		harness:  file.harness,
	}, true
}

func (file swarmWindowFile) matchesHeldDescriptor(identity swarmTranscriptIdentity) bool {
	if file.heldDescriptor == "" {
		return true
	}
	if identity != file.heldIdentity {
		return false
	}
	info, err := os.Stat(file.heldDescriptor)
	if err != nil {
		return false
	}
	held, ok := file.stamped(info)
	return ok && held.identity == identity && file.processAlive()
}

func (file swarmWindowFile) openRoot() (*os.Root, string) {
	home, err := filepath.EvalSymlinks(file.home)
	if err != nil {
		return nil, "Session transcript directory could not be opened"
	}
	directory := filepath.Join(home, file.directory)
	parent, err := filepath.EvalSymlinks(filepath.Join(directory, filepath.Dir(file.relative)))
	if err != nil {
		return nil, "Session transcript directory could not be opened"
	}
	if rel, err := filepath.Rel(directory, parent); err != nil || !filepath.IsLocal(rel) {
		return nil, "Session transcript is outside its configured directory"
	}
	info, err := os.Lstat(directory)
	if err != nil || !info.IsDir() {
		return nil, "Session transcript directory could not be opened"
	}
	root, err := os.OpenRoot(directory)
	if err != nil {
		return nil, "Session transcript directory could not be opened"
	}
	opened, err := root.Stat(".")
	if err != nil || !os.SameFile(info, opened) {
		root.Close()
		return nil, "Session transcript directory changed while it was opened"
	}
	return root, ""
}

func (file swarmWindowFile) measure(ctx context.Context, access swarmTranscriptAccess, cache, next map[swarmTranscriptStamp]cachedSwarmWindow) cachedSwarmWindow {
	fail := func(reason string) cachedSwarmWindow { return cachedSwarmWindow{reason: reason} }
	root, reason := file.openRoot()
	if reason != "" {
		return fail(reason)
	}
	defer root.Close()
	if ctx.Err() != nil {
		return fail("Context read canceled")
	}
	f, err := access.open(root, file.relative)
	if err != nil {
		return fail("Session transcript could not be opened read-only")
	}
	defer f.Close()
	opened, err := access.stat(f)
	if err != nil || !opened.Mode().IsRegular() {
		return fail("Session transcript is not a regular file")
	}
	current, owned := file.stamped(opened)
	if !owned {
		return fail("Session transcript is not owned by this user")
	}
	if !file.matchesHeldDescriptor(current.identity) {
		return fail("Codex process no longer holds the session rollout")
	}
	if cached, ok := next[current]; ok {
		return cached
	}
	if cached, ok := cache[current]; ok {
		next[current] = cached
		return cached
	}
	var input io.ReaderAt = f
	if access.reader != nil {
		input = access.reader(f)
	}
	window, err := readSwarmWindowTail(ctx, input, current.size, file.harness)
	if err != nil {
		if ctx.Err() != nil {
			return fail("Context read canceled")
		}
		return fail("Session transcript could not be read")
	}
	if !file.matchesHeldDescriptor(current.identity) {
		return fail("Codex process no longer holds the session rollout")
	}
	measured := cachedSwarmWindow{window: window}
	if window.MeasuredAt.IsZero() {
		measured.reason = "No per-request usage found in the bounded transcript tail"
	}
	next[current] = measured
	return measured
}

type swarmWindowRecord struct {
	Type      string `json:"type"`
	Subtype   string `json:"subtype"`
	Timestamp string `json:"timestamp"`
	Message   struct {
		Model string `json:"model"`
		Usage *struct {
			Input *int64 `json:"input_tokens"`
			Read  int64  `json:"cache_read_input_tokens"`
			Write int64  `json:"cache_creation_input_tokens"`
		} `json:"usage"`
	} `json:"message"`
	Payload struct {
		Type  string `json:"type"`
		Model string `json:"model"`
		Info  *struct {
			Window int64 `json:"model_context_window"`
			Last   struct {
				Input *int64 `json:"input_tokens"`
			} `json:"last_token_usage"`
		} `json:"info"`
	} `json:"payload"`
}

var swarmModelID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:/-]{0,95}$`)

func readSwarmWindowTail(ctx context.Context, file io.ReaderAt, size int64, harness string) (swarmWindow, error) {
	window := swarmWindow{Source: "claude transcript"}
	if harness == wake.HarnessCodex {
		window.Source = "codex rollout"
	}
	// Usage comes from the newest complete usage record. Count compactions across
	// the inspected tail, which covers the whole file for smaller transcripts.
	tail := make([]byte, min(size, swarmWindowMaxTail))
	base := size - int64(len(tail))
	window.CompactionsPartial = base > 0
	remaining := len(tail)
	for end := len(tail); end > 0; {
		if err := ctx.Err(); err != nil {
			return swarmWindow{}, err
		}
		start := max(0, end-swarmWindowBlock)
		if _, err := file.ReadAt(tail[start:end], base+int64(start)); err != nil {
			return swarmWindow{}, err
		}
		for remaining > start {
			newline := bytes.LastIndexByte(tail[start:remaining], '\n')
			if newline < 0 {
				break
			}
			separator := start + newline
			window.readRecord(tail[separator+1:remaining], harness)
			remaining = separator
		}
		end = start
	}
	if base == 0 && remaining > 0 {
		window.readRecord(tail[:remaining], harness)
	}
	if window.WindowTokens == 0 {
		window.WindowTokens = swarmModelWindow(window.Model)
	}
	window.NearLimit = window.WindowTokens > 0 && window.UsedTokens >= window.WindowTokens-window.WindowTokens/10
	return window, ctx.Err()
}

func (window *swarmWindow) readRecord(line []byte, harness string) {
	var record swarmWindowRecord
	if len(line) == 0 || json.Unmarshal(line, &record) != nil {
		return
	}
	if record.Type == "compacted" && harness == wake.HarnessCodex ||
		record.Type == "system" && record.Subtype == "compact_boundary" && harness == wake.HarnessClaude {
		window.Compactions++
		if window.Compactions == 1 {
			if at, err := time.Parse(time.RFC3339Nano, record.Timestamp); err == nil {
				window.LastCompactionAt = &at
			}
		}
		return
	}
	if !window.MeasuredAt.IsZero() {
		// The nearest earlier Codex turn context supplies the model for this usage.
		if harness == wake.HarnessCodex && window.Model == "" && record.Type == "turn_context" &&
			swarmModelID.MatchString(record.Payload.Model) {
			window.Model = record.Payload.Model
		}
		return
	}
	switch harness {
	case wake.HarnessClaude:
		usage := record.Message.Usage
		if record.Type != "assistant" || usage == nil || usage.Input == nil ||
			record.Message.Model == "<synthetic>" {
			return
		}
		input := *usage.Input
		if input < 0 || usage.Read < 0 || usage.Write < 0 ||
			usage.Read > math.MaxInt64-input || usage.Write > math.MaxInt64-input-usage.Read {
			return
		}
		if at, err := time.Parse(time.RFC3339Nano, record.Timestamp); err == nil {
			window.UsedTokens = input + usage.Read + usage.Write
			window.MeasuredAt = at
			if swarmModelID.MatchString(record.Message.Model) {
				window.Model = record.Message.Model
			}
		}
	case wake.HarnessCodex:
		info := record.Payload.Info
		if record.Type != "event_msg" || record.Payload.Type != "token_count" || info == nil ||
			info.Last.Input == nil || *info.Last.Input <= 0 {
			return
		}
		if at, err := time.Parse(time.RFC3339Nano, record.Timestamp); err == nil {
			// Codex input_tokens already includes cached input.
			window.UsedTokens = *info.Last.Input
			window.WindowTokens, window.MeasuredAt = max(0, info.Window), at
		}
	}
}

func swarmModelWindow(model string) int64 {
	// https://platform.claude.com/docs/en/build-with-claude/context-windows
	switch model {
	case "claude-fable-5-2", "claude-fable-5-1", "claude-fable-5",
		"claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
		"claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6", "claude-haiku-5-5":
		return 1_000_000
	case "claude-opus-4-5-20251101", "claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001":
		return 200_000
	default:
		return 0
	}
}
