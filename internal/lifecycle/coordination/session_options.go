package coordination

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/sys/unix"
)

const sessionInboxOptionsSchema = "cairn.session-inbox-options/1"

type SessionInboxOptionsRequest struct {
	NativeSessionID    string
	HasNativeSessionID bool
	AgentID            string
	CompactInbox       bool
	NoticeBatch        bool
	Clear              bool
}

func (c *CLI) sessionOptionsPath(config *value.Value, nativeID string) (string, error) {
	binding, err := nativeRequired(config, "binding")
	if err != nil {
		return "", err
	}
	if binding.Kind != value.String {
		return "", &journal.TypeError{Field: "binding"}
	}
	for _, r := range binding.String {
		if !utf8.ValidRune(r) {
			return "", &value.Error{Class: "UnicodeEncodeError", Message: "session binding is not UTF-8 encodable"}
		}
	}
	if !utf8.ValidString(nativeID) {
		return "", &value.Error{Class: "UnicodeEncodeError", Message: "native session ID is not UTF-8 encodable"}
	}
	dir, err := c.pathText(config.Get("state_dir"))
	if err != nil {
		return "", err
	}
	key := sha256.Sum256([]byte(string(binding.String) + "\x00" + nativeID))
	return filepath.Join(dir, "session-options", hex.EncodeToString(key[:])+".json"), nil
}

func isSymlinkPath(path string) bool {
	info, err := os.Lstat(path)
	return err == nil && info.Mode()&os.ModeSymlink != 0
}

func (c *CLI) readSessionInboxOptions(config *value.Value, nativeID string) (*value.Value, error) {
	if !validNativeSessionID(nativeID) {
		return nil, nil
	}
	path, err := c.sessionOptionsPath(config, nativeID)
	if err != nil {
		return nil, err
	}
	if isSymlinkPath(filepath.Dir(path)) {
		return nil, nativeRefusal("INVALID_HOST", "session inbox options must be owner-only")
	}
	fd, err := unix.Open(path, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0)
	if err != nil {
		if errors.Is(err, syscall.ENOENT) {
			return nil, nil
		}
		if errors.Is(err, syscall.ELOOP) {
			return nil, nativeRefusal("INVALID_HOST", "session inbox options must be owner-only")
		}
		return nil, &os.PathError{Op: "open", Path: path, Err: err}
	}
	file := os.NewFile(uintptr(fd), path)
	defer func() { _ = file.Close() }()
	var st syscall.Stat_t
	if err := syscall.Fstat(int(file.Fd()), &st); err != nil {
		return nil, &os.PathError{Op: "fstat", Path: path, Err: err}
	}
	if st.Mode&syscall.S_IFMT != syscall.S_IFREG || st.Uid != c.uid() || st.Mode&0o077 != 0 {
		return nil, nativeRefusal("INVALID_HOST", "session inbox options must be owner-only")
	}
	raw, err := io.ReadAll(io.LimitReader(file, 65537))
	if err != nil {
		return nil, err
	}
	if len(raw) > 65536 {
		return nil, nativeRefusal("INVALID_HOST", "session inbox options exceed limit")
	}
	entry, err := value.DecodeTextWithObjectPolicy(raw, "utf-8", 0, value.DuplicateReject)
	if err != nil {
		return nil, err
	}
	return validateSessionInboxOptionsEntry(config, nativeID, entry)
}

func validateSessionInboxOptionsEntry(config *value.Value, nativeID string, entry *value.Value) (*value.Value, error) {
	if entry == nil || entry.Kind != value.Object || !value.SameText(entry.Get("schema"), sessionInboxOptionsSchema) || !nativeEqual(entry.Get("binding"), config.Get("binding")) || !value.SameText(entry.Get("native_session_id"), nativeID) {
		return nil, nativeRefusal("INVALID_HOST", "session inbox options are malformed")
	}
	for _, m := range entry.Members {
		switch string(m.Key) {
		case "schema", "binding", "native_session_id", "compact_inbox_version", "notice_batch_version", "updated_at":
		default:
			return nil, nativeRefusal("INVALID_HOST", "session inbox options are malformed")
		}
	}
	if upd := entry.Get("updated_at"); upd != nil {
		if (upd.Kind != value.Int && upd.Kind != value.Float) || (upd.Kind == value.Float && (math.IsNaN(upd.Number) || math.IsInf(upd.Number, 0))) {
			return nil, nativeRefusal("INVALID_HOST", "session inbox options timestamp is invalid")
		}
		if upd.Kind == value.Int {
			if _, err := strconv.ParseFloat(upd.Integer, 64); err != nil {
				return nil, nativeRefusal("INVALID_HOST", "session inbox options timestamp is invalid")
			}
		}
	}
	capable := nativeContains([]string{"codex", "claude"}, nativeText(config.Get("harness"))) && value.Truth(config.Get("native_delivery"))
	cv := entry.Get("compact_inbox_version")
	if !capable || cv == nil || cv.Kind != value.Int || cv.Integer != "1" {
		return nil, nativeRefusal("INVALID_CONFIG", "compact_inbox_version 1 requires Codex or Claude native delivery")
	}
	if bv := entry.Get("notice_batch_version"); bv != nil && (bv.Kind != value.Int || bv.Integer != "1") {
		return nil, nativeRefusal("INVALID_CONFIG", "notice_batch_version 1 requires compact Codex or Claude native delivery")
	}
	return entry, nil
}

func (c *CLI) effectiveSessionInboxOptions(config, nativeID *value.Value) (bool, bool, error) {
	capable := nativeContains([]string{"codex", "claude"}, nativeText(config.Get("harness"))) && value.Truth(config.Get("native_delivery"))
	compact := capable && nativeEqual(config.Get("compact_inbox_version"), value.IntegerText("1"))
	batch := compact && nativeEqual(config.Get("notice_batch_version"), value.IntegerText("1"))
	if nativeID == nil || nativeID.Kind != value.String {
		return compact, batch, nil
	}
	override, err := c.readSessionInboxOptions(config, string(nativeID.String))
	if err != nil {
		return false, false, err
	}
	if override != nil {
		compact = true
		batch = batch || nativeEqual(override.Get("notice_batch_version"), value.IntegerText("1"))
	}
	return compact, batch, nil
}

func (c *CLI) resolveSessionTarget(config *value.Value, req SessionInboxOptionsRequest) (string, error) {
	if req.HasNativeSessionID || req.AgentID == "" {
		if !validNativeSessionID(req.NativeSessionID) {
			return "", nativeRefusal("INVALID_REQUEST", "invalid native session ID")
		}
		return req.NativeSessionID, nil
	}
	agentID := req.AgentID
	if !isCanonicalUUID(agentID) {
		return "", nativeRefusal("INVALID_REQUEST", "invalid Cairn agent ID")
	}
	dir, err := c.pathText(config.Get("state_dir"))
	if err != nil {
		return "", err
	}
	entries, err := os.ReadDir(dir)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		if filepath.Ext(e.Name()) == ".json" {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	var matches []string
	for _, name := range names {
		candidate := filepath.Join(dir, name)
		info, err := os.Lstat(candidate)
		if err != nil || info.Mode()&os.ModeSymlink != 0 || !statMatchesUID(info, c.uid()) || info.Mode().Perm()&0o077 != 0 {
			continue
		}
		rec, err := c.read(candidate, SiteSessionRead)
		if err != nil || rec == nil || rec.Kind != value.Object || value.Truth(rec.Get("retired")) || value.Truth(rec.Get("ending")) {
			continue
		}
		agent := rec.Get("agent")
		if agent == nil || agent.Kind != value.Object || !value.SameText(agent.Get("agent_id"), agentID) {
			continue
		}
		nid := nativeText(agent.Get("native_session_id"))
		expected, err := c.statePath(config, nid)
		if err == nil && validNativeSessionID(nid) && filepath.Clean(expected) == filepath.Clean(candidate) {
			matches = append(matches, nid)
		}
	}
	if len(matches) == 0 {
		return "", nativeRefusal("NOT_FOUND", "no active native session found for agent_id on this binding")
	}
	if len(matches) > 1 {
		return "", nativeRefusal("CONFLICT", "multiple active native sessions match agent_id; pass --native-session-id")
	}
	return matches[0], nil
}

func (c *CLI) ConfigureSessionInbox(ctx context.Context, config *value.Value, req SessionInboxOptionsRequest) (*value.Value, error) {
	capable := nativeContains([]string{"codex", "claude"}, nativeText(config.Get("harness"))) && value.Truth(config.Get("native_delivery"))
	if req.CompactInbox && !capable {
		return nil, nativeRefusal("INVALID_CONFIG", "compact_inbox_version 1 requires Codex or Claude native delivery")
	}
	if req.NoticeBatch && !req.CompactInbox {
		return nil, nativeRefusal("INVALID_CONFIG", "notice_batch_version 1 requires compact Codex or Claude native delivery")
	}
	nativeID, err := c.resolveSessionTarget(config, req)
	if err != nil {
		return nil, err
	}
	sPath, err := c.statePath(config, nativeID)
	if err != nil {
		return nil, err
	}
	optPath, err := c.sessionOptionsPath(config, nativeID)
	if err != nil {
		return nil, err
	}
	lock, err := c.lockStateWait(ctx, sPath, 3*time.Second)
	if err != nil {
		return nil, err
	}
	defer func() { _ = lock.Close() }()
	if isSymlinkPath(filepath.Dir(optPath)) || isSymlinkPath(optPath) {
		return nil, nativeRefusal("INVALID_HOST", "session inbox options must be owner-only")
	}
	var status string
	if req.Clear {
		if err := os.Remove(optPath); err != nil && !errors.Is(err, os.ErrNotExist) {
			return nil, err
		}
		status = "cleared"
	} else {
		now := c.clock()
		nowFloat := float64(now.Unix()) + float64(now.Nanosecond())/1e9
		rec := nativeMap(
			"schema", sessionInboxOptionsSchema,
			"binding", config.Get("binding"),
			"native_session_id", nativeID,
			"compact_inbox_version", 1,
			"updated_at", nowFloat,
		)
		if req.NoticeBatch {
			rec.Set("notice_batch_version", value.IntegerText("1"))
		}
		if err := c.writeState(optPath, rec); err != nil {
			return nil, err
		}
		status = "configured"
	}
	compact, batch, err := c.effectiveSessionInboxOptions(config, value.Text(nativeID))
	if err != nil {
		return nil, err
	}
	var compactVal, batchVal *value.Value
	if compact {
		compactVal = value.IntegerText("1")
	}
	if batch {
		batchVal = value.IntegerText("1")
	}
	return nativeMap(
		"schema", sessionInboxOptionsSchema,
		"status", status,
		"binding", config.Get("binding"),
		"native_session_id", nativeID,
		"options_path", optPath,
		"compact_inbox_version", compactVal,
		"notice_batch_version", batchVal,
	), nil
}
