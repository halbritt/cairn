package transcript

import (
	"bufio"
	"bytes"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"

	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/procpeer"
)

func CodexRolloutPath(cfg CodexConfig, state SessionState) (string, error) {
	r := NewReader(nil)
	return r.CodexRolloutPath(cfg, state)
}
func (r Reader) CodexRolloutPath(cfg CodexConfig, state SessionState) (string, error) {
	if cfg.Runtime == nil {
		cfg.Runtime = r.Runtime
	}
	if r.AliveError != nil {
		var observed error
		path, err := CodexRolloutPathAt(r.ProcRoot, cfg, state, r.ExpectedUID, func(ref procpeer.ProcessRef) bool {
			alive, e := r.AliveError(ref)
			observed = e
			return alive && e == nil
		})
		if observed != nil {
			return "", observed
		}
		return path, err
	}
	return CodexRolloutPathAt(r.ProcRoot, cfg, state, r.ExpectedUID, r.Alive)
}
func CodexRolloutPathAt(root string, cfg CodexConfig, state SessionState, uid uint32, alive func(procpeer.ProcessRef) bool) (string, error) {
	return codexRolloutPathAt(root, cfg, state, uid, alive, os.Stat)
}
func codexRolloutPathAt(procRoot string, cfg CodexConfig, state SessionState, uid uint32, alive func(procpeer.ProcessRef) bool, fdStat func(string) (os.FileInfo, error)) (string, error) {
	if !filepath.IsAbs(cfg.ConfigHome) || !uuidPattern.MatchString(state.NativeSessionID) || state.Process.PID < 2 || alive == nil || !alive(state.Process) {
		return "", invalid("Codex rollout owner is unavailable")
	}
	root, e := resolveLoose(strings.TrimRight(posixPath(cfg.ConfigHome), "/")+"/sessions", cfg.Runtime)
	if e != nil {
		return "", e
	}
	dir := filepath.Join(procRoot, strconv.Itoa(state.Process.PID), "fd")
	entries, e := os.ReadDir(dir)
	if e != nil {
		return "", e
	}
	paths := map[string]bool{}
	for _, entry := range entries {
		descriptor := filepath.Join(dir, entry.Name())
		target, e := os.Readlink(descriptor)
		if os.IsNotExist(e) {
			continue
		}
		if e != nil {
			return "", e
		}
		target = posixPath(target)
		name := filepath.Base(target)
		if !strings.HasPrefix(name, "rollout-") || !strings.HasSuffix(name, "-"+state.NativeSessionID+".jsonl") {
			continue
		}
		resolved, err := resolveLoose(target, cfg.Runtime)
		if err != nil {
			return "", err
		}
		rel, e := filepath.Rel(root, target)
		if !filepath.IsAbs(target) || e != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || resolved != target || !noSymlinkComponents(target) {
			return "", invalid("Codex rollout is outside its configured session directory")
		}
		f, info, e := openOwner(target, uid)
		if e != nil {
			return "", ownerRefusal(e, "Codex rollout is not an owner-controlled regular file")
		}
		f.Close()
		opened, e := fdStat(descriptor)
		if e != nil {
			return "", e
		}
		st, ok := opened.Sys().(*syscall.Stat_t)
		if !ok || !sameInode(info, *st) {
			return "", invalid("Codex rollout path no longer names its open file")
		}
		paths[target] = true
	}
	if len(paths) != 1 || !alive(state.Process) {
		return "", invalid("Codex owner does not hold exactly one matching rollout")
	}
	for p := range paths {
		return p, nil
	}
	panic("unreachable unique rollout")
}
func noSymlinkComponents(path string) bool {
	if filepath.Clean(path) != path {
		return false
	}
	prefix := "/"
	for _, part := range strings.Split(strings.TrimPrefix(path, "/"), "/") {
		prefix = filepath.Join(prefix, part)
		info, e := os.Lstat(prefix)
		if e != nil || info.Mode()&os.ModeSymlink != 0 {
			return false
		}
	}
	return true
}
func statOwner(path string, uid uint32) (syscall.Stat_t, error) {
	f, st, e := openOwner(path, uid)
	if e == nil {
		f.Close()
	}
	return st, e
}
func (r Reader) recheck(cfg CodexConfig, state SessionState, path string, before syscall.Stat_t) error {
	p, e := r.CodexRolloutPath(cfg, state)
	if e != nil {
		return e
	}
	after, e := statOwner(path, r.ExpectedUID)
	if e != nil {
		return e
	}
	if p != path || !sameInode(before, after) {
		return invalid("Codex rollout owner changed during verification", e)
	}
	return nil
}
func (r Reader) records(cfg CodexConfig, state SessionState, ls []Locator) (string, syscall.Stat_t, []*Record, error) {
	p, e := r.CodexRolloutPath(cfg, state)
	if e != nil {
		return "", syscall.Stat_t{}, nil, e
	}
	st, e := statOwner(p, r.ExpectedUID)
	if e != nil {
		return "", st, nil, ownerRefusal(e, "Codex rollout is not an owner-controlled regular file")
	}
	records := make([]*Record, len(ls))
	for i, l := range ls {
		records[i], e = ReadNativeRecordAt(p, l, r.ExpectedUID, r.Profile)
		if e != nil {
			return "", st, nil, e
		}
	}
	return p, st, records, nil
}

func LatestCodexTurnEnd(cfg CodexConfig, state SessionState, profile *value.SourceReaderProfile) (*NativeRecord, error) {
	return NewReader(profile).LatestCodexTurnEnd(cfg, state)
}
func (r Reader) LatestCodexTurnEnd(cfg CodexConfig, state SessionState) (*NativeRecord, error) {
	p, e := r.CodexRolloutPath(cfg, state)
	if e != nil {
		return nil, e
	}
	f, st, e := openOwner(p, r.ExpectedUID)
	if e != nil {
		return nil, ownerRefusal(e, "Codex rollout is not an owner-controlled regular file")
	}
	defer f.Close()
	base := max(int64(0), st.Size-CodexTurnEndLimit)
	tail, e := readAt(f, base, st.Size-base)
	if e != nil {
		return nil, e
	}
	end := bytes.LastIndexByte(tail, '\n') + 1
	start := 0
	if base > 0 {
		start = bytes.IndexByte(tail, '\n') + 1
	}
	for position := end; position > start; {
		lineStart := bytes.LastIndexByte(tail[start:position-1], '\n') + 1 + start
		line := tail[lineStart:position]
		position = lineStart
		if !bytes.Contains(line, []byte(`"task_started"`)) && !bytes.Contains(line, []byte(`"task_complete"`)) {
			continue
		}
		record, e := StrictNativeLine(line, r.Profile)
		if e != nil {
			if strictSkip(e) {
				continue
			}
			return nil, e
		}
		payload := object(field(record, "payload"))
		if !stringIs(field(record, "type"), "event_msg") || payload == nil {
			continue
		}
		if stringIs(field(payload, "type"), "task_started") {
			return nil, nil
		}
		if !stringIs(field(payload, "type"), "task_complete") {
			continue
		}
		if len(line) > NativeRecordLimit {
			return nil, invalid("Codex turn end record exceeds its read limit")
		}
		l := pin(line, base+int64(lineStart))
		pinned, e := ReadNativeRecordAt(p, l, r.ExpectedUID, r.Profile)
		if e != nil {
			return nil, e
		}
		if !sameRecord(pinned, record) {
			return nil, invalid("Codex turn end record changed while reading")
		}
		return &NativeRecord{record, l}, nil
	}
	return nil, nil
}

func FindCodexRefusalLocators(cfg CodexConfig, state SessionState, turn string, profile *value.SourceReaderProfile) ([]Locator, error) {
	return NewReader(profile).FindCodexRefusalLocators(cfg, state, turn)
}

// FindCodexRefusalLocators implements CAIRN-423: bounded buffers over the whole
// captured finite snapshot. Exhaustion/change is unknown (nil), never acceptance.
// The monotonic budget is cooperative; it cannot interrupt filesystem I/O.
func (r Reader) FindCodexRefusalLocators(cfg CodexConfig, state SessionState, turn string) ([]Locator, error) {
	if r.Now == nil {
		return nil, nil
	}
	deadline := r.Now().Add(r.ScanDuration)
	p, e := r.CodexRolloutPath(cfg, state)
	if e != nil {
		var profile *value.ProfileUnavailable
		if errors.As(e, &profile) {
			return nil, e
		}
		return nil, nil
	}
	f, before, e := openOwner(p, r.ExpectedUID)
	if e != nil {
		return nil, nil
	}
	defer f.Close()
	reader := bufio.NewReaderSize(io.NewSectionReader(f, 0, before.Size), NativeRecordLimit+1)
	offset, lineNumber, lastMatch := int64(0), int64(0), int64(-1)
	skipping := false
	matches := []Locator{}
	for offset < before.Size {
		if !r.Now().Before(deadline) {
			return nil, nil
		}
		start := offset
		raw, err := reader.ReadSlice('\n')
		if len(raw) == 0 {
			return nil, nil
		}
		offset += int64(len(raw))
		complete := raw[len(raw)-1] == '\n'
		if err != nil && !errors.Is(err, bufio.ErrBufferFull) && !errors.Is(err, io.EOF) {
			return nil, nil
		}
		if skipping || len(raw) > NativeRecordLimit {
			skipping = !complete
			if complete {
				lineNumber++
			}
			continue
		}
		if !complete {
			if errors.Is(err, bufio.ErrBufferFull) {
				skipping = true
				continue
			}
			return nil, nil
		}
		record, err := ParsePlainJSONObject(raw, r.Profile)
		if err == nil {
			payload := object(field(record, "payload"))
			if payload != nil && (stringIs(field(payload, "turn_id"), turn) || stringIs(field(payload, "root_turn_id"), turn)) {
				if len(matches) == 3 || (len(matches) > 0 && lineNumber != lastMatch+1) {
					return nil, nil
				}
				matches = append(matches, pin(raw, start))
				lastMatch = lineNumber
			}
		}
		if err != nil && !strictSkip(err) && !sourceReadError(err, "UnicodeDecodeError", "ValueError", "JSONDecodeError", "RecursionError") {
			return nil, err
		}
		lineNumber++
	}
	var after syscall.Stat_t
	if skipping || !r.Now().Before(deadline) || syscall.Fstat(int(f.Fd()), &after) != nil || !sameSnapshot(before, after) {
		return nil, nil
	}
	rechecked, e := r.CodexRolloutPath(cfg, state)
	if e != nil {
		var profile *value.ProfileUnavailable
		if errors.As(e, &profile) {
			return nil, e
		}
		return nil, nil
	}
	if rechecked != p {
		return nil, nil
	}
	after, e = statOwner(p, r.ExpectedUID)
	if e != nil || !sameSnapshot(before, after) || !r.Now().Before(deadline) || len(matches) != 3 {
		return nil, nil
	}
	return matches, nil
}
func sameSnapshot(a, b syscall.Stat_t) bool {
	return sameInode(a, b) && a.Size == b.Size && a.Mtim == b.Mtim && a.Ctim == b.Ctim && a.Uid == b.Uid && a.Mode == b.Mode
}
