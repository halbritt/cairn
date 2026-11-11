package coordination

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func sourceContains(container, key *value.Value) (bool, error) {
	if container == nil {
		return false, &journal.TypeError{Field: "membership"}
	}
	switch container.Kind {
	case value.Object:
		return valueMember(container, key) != nil, nil
	case value.Array:
		for _, x := range container.Items {
			if nativeEqual(x, key) {
				return true, nil
			}
		}
		return false, nil
	case value.String:
		if key == nil || key.Kind != value.String {
			return false, &journal.TypeError{Field: "membership"}
		}
		return strings.Contains(journalText(container.String), journalText(key.String)), nil
	}
	return false, &journal.TypeError{Field: "membership"}
}

func (c *CLI) callerDescends(owner *value.Value) (bool, error) {
	if c.CallerDescends != nil {
		return c.CallerDescends(owner)
	}
	pid := nativeMap("pid", os.Getpid()).Get("pid")
	for i := 0; i < 64; i++ {
		current, err := nativeProcessReference(c.ProcRoot, pid)
		if err != nil {
			if errors.Is(err, os.ErrNotExist) || errors.Is(err, syscall.ESRCH) {
				return false, nil
			}
			return false, err
		}
		bound, err := nativeRequired(owner, "pid")
		if err != nil {
			return false, err
		}
		if nativeEqual(current.Get("pid"), bound) {
			return nativeSameProcess(current, owner)
		}
		parent, err := nativeRequired(current, "parent")
		if err != nil {
			return false, err
		}
		n, ok := nonnegativeInt(parent)
		if !ok {
			return false, &journal.TypeError{Field: "parent"}
		}
		if n.Sign() == 0 || n.IsInt64() && n.Int64() <= 1 || nativeEqual(parent, pid) {
			return false, nil
		}
		pid = parent
	}
	return false, nil
}

func (c *CLI) exactInboxSource(ctx context.Context, config, attempt, ref *value.Value) (*value.Value, error) {
	repo, err := nativeRequired(config, "repo")
	if err != nil {
		return nil, err
	}
	if ref == nil || ref.Kind != value.Object {
		return nil, &journal.TypeError{Field: "source reference"}
	}
	if ref.Get("repo") != nil {
		return nil, &journal.TypeError{Field: "duplicate repo keyword"}
	}
	request := nativeMap("repo", repo)
	for _, m := range ref.Members {
		request.Members = append(request.Members, m)
	}
	session, err := nativeRequired(attempt, "session")
	if err != nil {
		return nil, err
	}
	history, err := c.Host.Call(ctx, config, "history", request, NativeCallOptions{Session: session})
	if err != nil {
		return nil, err
	}
	if history == nil || history.Kind != value.Object {
		return nil, nativeRefusal("SOURCE_UNAVAILABLE", "inbox source identity differs")
	}
	refID, err := nativeRequired(ref, "record_id")
	if err != nil {
		return nil, err
	}
	versions := history.Get("versions")
	if history == nil || history.Kind != value.Object || !nativeEqual(history.Get("record_id"), refID) || !exactTrue(history.Get("historical")) || versions == nil || versions.Kind != value.Array || len(versions.Items) != 1 {
		return nil, nativeRefusal("SOURCE_UNAVAILABLE", "inbox source identity differs")
	}
	version := versions.Items[0]
	if version == nil || version.Kind != value.Object {
		return nil, nativeRefusal("SOURCE_UNAVAILABLE", "inbox source version is malformed")
	}
	refVersion, err := nativeRequired(ref, "version")
	if err != nil {
		return nil, err
	}
	body := version.Get("body")
	if !nativeEqual(version.Get("version"), refVersion) || !exactTrue(version.Get("payload_available")) || body == nil || body.Kind != value.String || !nativeNone(version.Get("span")) {
		return nil, nativeRefusal("SOURCE_UNAVAILABLE", "inbox source is not an exact whole version")
	}
	for _, r := range body.String {
		if !utf8.ValidRune(r) {
			return nil, &value.Error{Class: "UnicodeEncodeError", Message: "inbox source body is not UTF-8 encodable"}
		}
	}
	raw := []byte(string(body.String))
	sum := sha256.Sum256(raw)
	if !nativeEqual(version.Get("body_bytes"), nativeMap("n", len(raw)).Get("n")) || !value.SameText(version.Get("body_sha256"), hex.EncodeToString(sum[:])) {
		return nil, nativeRefusal("SOURCE_UNAVAILABLE", "inbox source is not an exact whole version")
	}
	return body, nil
}
func exactTrue(v *value.Value) bool { return v != nil && v.Kind == value.Bool && v.Boolean }

func compactFresh(issued *value.Value, now float64) (bool, error) {
	if issued.Kind == value.Int {
		n, ok := new(big.Int).SetString(issued.Integer, 10)
		if !ok {
			return false, &value.Error{Class: "ValueError", Message: "invalid integer"}
		}
		current := new(big.Rat).SetFloat64(now)
		start := new(big.Rat).SetInt(n)
		if start.Cmp(current) > 0 {
			return false, nil
		}
		end := new(big.Rat).SetInt(new(big.Int).Add(n, big.NewInt(3600)))
		return current.Cmp(end) < 0, nil
	}
	return issued.Number <= now && now < issued.Number+3600, nil
}

func (c *CLI) CompactCommand(ctx context.Context, argv []string, out, errOut io.Writer) (int, error) {
	const prog = "cairn-inbox"
	a, err := parseCLI(argv, prog, map[string]int{"--code": 1})
	if err != nil {
		return 0, err
	}
	if len(a.position) < 2 {
		missing := "operation, handle"
		if len(a.position) == 1 {
			missing = "handle"
		}
		return 0, parseFail(prog, "the following arguments are required: "+missing)
	}
	if len(a.position) > 3 {
		return 0, parseFail(prog, "unrecognized arguments: "+strings.Join(a.position[3:], " "))
	}
	op, handle := a.position[0], a.position[1]
	disposition := ""
	if len(a.position) == 3 {
		disposition = a.position[2]
	}
	if err = choiceCLI(prog, "operation", op, []string{"read", "inspect", "settle", "advance"}); err != nil {
		return 0, err
	}
	if disposition != "" {
		if err = choiceCLI(prog, "disposition", disposition, []string{"handled", "ignored", "failed"}); err != nil {
			return 0, err
		}
	}
	if a.has("--code") {
		if err = choiceCLI(prog, "--code", a.get("--code"), []string{"unsupported_kind", "source_unavailable", "processing_failed"}); err != nil {
			return 0, err
		}
	}
	if !hexText([]rune(handle), 16) {
		return 0, nativeRefusal("INVALID_REQUEST", "invalid compact inbox handle")
	}
	if (op == "settle") != (disposition != "") {
		return 0, nativeRefusal("INVALID_REQUEST", "settlement requires a disposition")
	}
	if op != "settle" && a.has("--code") {
		return 0, nativeRefusal("INVALID_REQUEST", "reason code applies only to settlement")
	}
	root, err := c.compactRoot()
	if err != nil {
		return 0, err
	}
	if c.Compatibility != nil {
		if err = c.Compatibility.BindLookup(c.profile(), root, nil, nil); err != nil {
			return 0, err
		}
	}
	// Read directory entries rather than treating an arbitrary configured root
	// as a glob pattern. pathlib.glob treats that root literally too.
	candidates := []string{}
	stateRoot := sourceJoin(root, "state")
	bindings, err := os.ReadDir(stateRoot)
	if sourceOSError(err) {
		bindings = nil
		err = nil
	}
	if err != nil {
		return 0, err
	}
	for _, binding := range bindings {
		candidate := sourceJoin(sourceJoin(sourceJoin(stateRoot, binding.Name()), "compact"), handle+".json")
		exists, e := sourceExists(candidate, false)
		if e != nil && !sourceOSError(e) {
			return 0, e
		}
		if exists {
			candidates = append(candidates, candidate)
		}
	}
	direct := sourceJoin(sourceJoin(stateRoot, "compact"), handle+".json")
	exists, err := sourceExists(direct, false)
	if err != nil && !sourceOSError(err) {
		return 0, err
	}
	if exists {
		candidates = append(candidates, direct)
	}
	if len(candidates) != 1 {
		return 0, nativeRefusal("NOT_FOUND", "compact inbox handle is absent or ambiguous")
	}
	index := candidates[0]
	if err = c.ownerOnly(index, "compact inbox handle must be owner-only"); err != nil {
		return 0, err
	}
	entry, err := c.read(index, SiteCompactIndexRead)
	if err != nil {
		return 0, err
	}
	if entry == nil || entry.Kind != value.Object {
		return 0, nativeRefusal("INVALID_HOST", "compact inbox handle is malformed")
	}
	nowTime := c.clock()
	now := float64(nowTime.Unix()) + float64(nowTime.Nanosecond())/1e9
	issued := entry.Get("issued_at")
	valid := value.SameText(entry.Get("schema"), "cairn.compact-inbox-handle/1") && issued != nil && (issued.Kind == value.Int || issued.Kind == value.Float)
	if valid {
		fresh, e := compactFresh(issued, now)
		if e != nil {
			return 0, e
		}
		valid = fresh
	}
	if !valid {
		return 0, nativeRefusal("HANDLE_EXPIRED", "compact inbox handle expired or invalid")
	}
	binding, err := nativeRequired(entry, "binding")
	if err != nil {
		return 0, err
	}
	if !boundedName(binding, true) {
		return 0, nativeRefusal("INVALID_HOST", "invalid compact inbox binding")
	}
	configPath := sourceJoin(sourceJoin(root, "bindings"), nativeText(binding)+".json")
	if c.Compatibility != nil {
		configPath = purePosixPath(c.Compatibility.ConfigPath)
		b := nativeText(binding)
		if err = c.Compatibility.BindLookup(c.profile(), root, &b, &configPath); err != nil {
			return 0, err
		}
	}
	if err = c.ownerOnly(configPath, "compact inbox binding must be owner-only"); err != nil {
		return 0, err
	}
	config, err := c.loadConfig(configPath)
	if err != nil {
		return 0, err
	}
	dir, err := c.resolve(config.Get("state_dir"))
	if err != nil {
		return 0, err
	}
	stateValue, err := nativeRequired(entry, "state")
	if err != nil {
		return 0, err
	}
	statePath, err := c.resolve(stateValue)
	if err != nil {
		return 0, err
	}
	contextValue, err := nativeRequired(entry, "context")
	if err != nil {
		return 0, err
	}
	contextPath, err := c.resolve(contextValue)
	if err != nil {
		return 0, err
	}
	resolvedIndex, err := c.resolve(value.Text(index))
	if err != nil {
		return 0, err
	}
	if sourceJoin(sourceJoin(dir, "compact"), handle+".json") != resolvedIndex || filepath.Dir(statePath) != dir || filepath.Dir(contextPath) != sourceJoin(dir, "inbox") {
		return 0, nativeRefusal("INVALID_HOST", "compact inbox handle left its binding")
	}
	state, err := c.read(statePath, SiteCompactStateRead)
	if err != nil {
		return 0, err
	}
	native, err := c.read(contextPath, SiteCompactContextRead)
	if err != nil {
		return 0, err
	}
	if state == nil || state.Kind != value.Object || native == nil || native.Kind != value.Object {
		return 0, nativeRefusal("INVALID_HOST", "compact inbox state or context is malformed")
	}
	agent, err := nativeObj(state.Get("agent"), "agent")
	if err != nil {
		return 0, err
	}
	nativeID := agent.Get("native_session_id")
	owner := state.Get("process")
	if !value.Truth(owner) {
		owner = value.Mapping()
	}
	for _, key := range []string{"pid", "start", "boot"} {
		present, e := sourceContains(owner, value.Text(key))
		if e != nil {
			return 0, e
		}
		if !present {
			return 0, nativeRefusal("STALE_SESSION", "compact inbox native owner is missing")
		}
	}
	stale := func() (int, error) {
		return 0, nativeRefusal("STALE_SESSION", "compact inbox handle no longer belongs to this live attempt")
	}
	if value.Truth(state.Get("ending")) || value.Truth(state.Get("retired")) {
		return stale()
	}
	alive, err := c.engine().alive(owner)
	if err != nil {
		return 0, err
	}
	if !alive {
		return stale()
	}
	boundNative, err := nativeRequired(entry, "native_session_id")
	if err != nil {
		return 0, err
	}
	if !nativeEqual(nativeID, boundNative) || !value.SameText(native.Get("compact_handle"), handle) || !nativeEqual(native.Get("compact_issued_at"), issued) {
		return stale()
	}
	attempt, err := nativeObj(state.Get("inbox_attempt"), "inbox_attempt")
	if err != nil {
		return 0, err
	}
	if !nativeEqual(attempt.Get("attempt_id"), native.Get("attempt_id")) {
		return stale()
	}
	session, err := (&NativeSession{Host: c.Host}).sessionRef(agent)
	if err != nil {
		return 0, err
	}
	if !nativeEqual(attempt.Get("session"), session) || !nativeEqual(native.Get("agent_id"), agent.Get("agent_id")) || !nativeEqual(native.Get("execution_id"), agent.Get("execution_id")) || value.Truth(attempt.Get("lease_lapsed")) || value.Truth(attempt.Get("cancel")) || value.Truth(attempt.Get("finished_at")) {
		return stale()
	}
	descends, err := c.callerDescends(owner)
	if err != nil {
		return 0, err
	}
	if !descends {
		return 0, nativeRefusal("NOT_THIS_SESSION", "compact inbox caller is outside the native process tree")
	}
	envKey := "CLAUDE_SESSION_ID"
	if value.SameText(config.Get("harness"), "codex") {
		envKey = "CODEX_THREAD_ID"
	}
	if env := c.env(envKey); env != "" && !value.SameText(nativeID, env) {
		return 0, nativeRefusal("NOT_THIS_SESSION", "compact inbox handle belongs to another native conversation")
	}
	delivery, err := nativeRequired(attempt, "delivery")
	if err != nil {
		return 0, err
	}
	event, err := nativeRequired(delivery, "event")
	if err != nil {
		return 0, err
	}
	for _, pair := range []struct {
		left     *value.Value
		leftKey  string
		right    *value.Value
		rightKey string
	}{{native, "delivery_id", delivery, "delivery_id"}, {native, "lease_id", delivery, "lease_id"}, {native, "event_id", event, "event_id"}, {native, "source", event, "ref"}, {native, "kind", event, "kind"}, {native, "sender", event, "from"}} {
		left, e := nativeRequired(pair.left, pair.leftKey)
		if e != nil {
			return 0, e
		}
		right, e := nativeRequired(pair.right, pair.rightKey)
		if e != nil {
			return 0, e
		}
		if !nativeEqual(left, right) {
			return 0, nativeRefusal("INVALID_HOST", "compact inbox delivery changed")
		}
	}
	if !nativeEqual(native.Get("native_turn_id"), attempt.Get("native_turn_id")) || !nativeContains([]string{"notice", "response"}, nativeText(event.Get("kind"))) {
		return 0, nativeRefusal("INVALID_HOST", "compact inbox delivery changed")
	}
	if op == "advance" {
		return c.advanceCompact(ctx, config, statePath, native, attempt, event, out)
	}
	renewed, err := c.Host.Call(ctx, config, "event-renew", nativeMap("delivery_id", delivery.Get("delivery_id"), "lease_id", delivery.Get("lease_id"), "lease_seconds", 90), NativeCallOptions{Session: attempt.Get("session")})
	if err != nil {
		var ce *CoordinationError
		if !errors.As(err, &ce) {
			return 0, err
		}
		saved := value.Mapping()
		if op == "settle" && ce.Code == "STALE_LEASE" {
			id, e := requiredText(attempt, "attempt_id")
			if e != nil {
				return 0, e
			}
			v, e := c.read(sourceJoin(sourceJoin(dir, "intents"), id+".json"), SiteCompactJournalRead)
			if e != nil {
				if !sourceReadCaught(e) {
					return 0, e
				}
			} else {
				saved = v
			}
		}
		slot := saved.Get("completion")
		if slot == nil || slot.Kind != value.Object || !value.SameText(saved.Get("schema"), "cairn.inbox-intent/1") || !nativeEqual(saved.Get("attempt_id"), attempt.Get("attempt_id")) || !nativeEqual(saved.Get("delivery_id"), delivery.Get("delivery_id")) || !nativeEqual(saved.Get("session"), attempt.Get("session")) || !value.SameText(slot.Get("kind"), "ack") {
			return 0, err
		}
		ids, e := nativeRequired(native, "request_ids")
		if e != nil {
			return 0, e
		}
		completionID, e := nativeRequired(ids, "completion")
		if e != nil {
			return 0, e
		}
		if !nativeEqual(slot.Get("request_id"), completionID) || !value.SameText(slot.Get("status"), "committed") {
			return 0, err
		}
	} else {
		if renewed == nil || renewed.Kind != value.Object {
			return 0, &journal.NonObjectError{Field: "renewed delivery"}
		}
		if !nativeEqual(renewed.Get("delivery_id"), delivery.Get("delivery_id")) || !nativeEqual(renewed.Get("lease_id"), delivery.Get("lease_id")) || !value.SameText(renewed.Get("state"), "leased") || !nativeEqual(renewed.Get("event"), event) {
			return 0, nativeRefusal("STALE_LEASE", "compact inbox delivery is no longer leased")
		}
	}
	var body *value.Value
	if !(op == "settle" && disposition == "failed" && a.get("--code") == "source_unavailable") {
		ref, err := nativeRequired(event, "ref")
		if err != nil {
			return 0, err
		}
		body, err = c.exactInboxSource(ctx, config, attempt, ref)
		if err != nil {
			return 0, err
		}
	}
	if op == "read" {
		return 0, c.output(out, nativeMap("schema", "cairn.compact-inbox/1", "ok", true, "kind", event.Get("kind"), "sender", event.Get("from"), "source", event.Get("ref"), "body", body), false)
	}
	if op == "inspect" {
		return 0, c.output(out, nativeMap("schema", "cairn.compact-inbox/1", "ok", true, "context", native), false)
	}
	command := []string{"--context", contextPath, "ack", "--disposition", disposition}
	if a.has("--code") {
		command = append(command, "--code", a.get("--code"))
	}
	var captured bytes.Buffer
	status, err := c.InboxCommand(ctx, command, strings.NewReader(""), &captured, errOut)
	if err != nil {
		return 0, err
	}
	reply, err := c.profile().Decode(SiteCompactReplyRead, captured.Bytes())
	if err != nil {
		if sourceClass(err, "ValueError", "JSONDecodeError", "UnicodeDecodeError") || journal.Classified(err) {
			return 0, nativeRefusal("API_UNAVAILABLE", "settlement returned no structured confirmation")
		}
		return 0, err
	}
	if reply == nil || reply.Kind != value.Object {
		return 0, &journal.NonObjectError{Field: "settlement reply"}
	}
	if status != 0 || !exactTrue(reply.Get("ok")) {
		code := reply.Get("status")
		if code == nil {
			code = value.Text("API_UNAVAILABLE")
		}
		if status == 0 {
			status = 7
		}
		return status, c.output(out, nativeMap("schema", "cairn.compact-inbox/1", "ok", false, "status", code, "message", "settlement refused or uncertain; retry the same disposition and code"), false)
	}
	data := reply.Get("data")
	if data == nil || data.Kind != value.Object || !value.SameText(data.Get("state"), disposition) {
		return 7, c.output(out, nativeMap("schema", "cairn.compact-inbox/1", "ok", false, "status", "CONFIRMATION_MISMATCH", "message", "settlement response did not confirm the selected disposition"), false)
	}
	return 0, c.output(out, nativeMap("schema", "cairn.compact-inbox/1", "ok", true, "status", data.Get("state"), "attempts", data.Get("attempts"), "handle", handle), false)
}

func (c *CLI) advanceCompact(ctx context.Context, config *value.Value, path string, native, attempt, event *value.Value, out io.Writer) (int, error) {
	if !nativeEqual(config.Get("notice_batch_version"), value.IntegerText("1")) || !value.SameText(event.Get("kind"), "notice") || !value.Truth(attempt.Get("native_turn_id")) {
		return 0, nativeRefusal("UNSUPPORTED_CONTROL", "notice batching is unavailable for this native turn")
	}
	requestID := native.Get("batch_advance_request_id")
	if requestID == nil || requestID.Kind != value.String {
		return 0, nativeRefusal("INVALID_HOST", "native batch advance identity is absent")
	}
	if c.Host == nil || c.Host.Lock == nil || c.Host.Load == nil {
		return 0, &value.ProfileUnavailable{}
	}
	lock, err := c.Host.Lock(path)
	if err != nil {
		return 0, err
	}
	defer lock.Close()
	state, err := c.read(path, SiteCompactStateRead)
	if err != nil {
		return 0, err
	}
	fresh, err := nativeDefaultObj(state.Get("inbox_attempt"), "inbox_attempt")
	if err != nil {
		return 0, err
	}
	intent, err := nativeDefaultObj(state.Get("inbox_intent"), "inbox_intent")
	if err != nil {
		return 0, err
	}
	oldID, err := nativeRequired(attempt, "attempt_id")
	if err != nil {
		return 0, err
	}
	if !nativeEqual(fresh.Get("attempt_id"), oldID) || !nativeEqual(intent.Get("request_id"), oldID) {
		return 0, nativeRefusal("STALE_SESSION", "native batch item changed during advance")
	}
	request := nativeMap("request_id", requestID, "session", attempt.Get("session"), "attempt_id", oldID, "version", 1)
	s := &NativeSession{Config: config, Record: state, Path: path, Host: c.Host}
	var result *value.Value
	if value.SameText(attempt.Get("admission_kind"), "organic") {
		root, err := nativeRequired(attempt, "organic_root")
		if err != nil {
			return 0, err
		}
		request.Set("root_id", root)
		result, err = s.AdvanceOrganicNotice(ctx, request)
	} else {
		old := state.Get("batch_advance_intent")
		if !nativeNone(old) && !nativeEqual(old, request) {
			return 0, nativeRefusal("SESSION_BUSY", "another notice handoff is unresolved")
		}
		state.Set("batch_advance_intent", request)
		if err = s.Save(); err != nil {
			return 0, err
		}
		result, err = s.AdvanceBatch(ctx, false)
	}
	if err != nil {
		return 0, err
	}
	next, err := nativeRequired(result, "attempt")
	if err != nil {
		return 0, err
	}
	if nativeNone(next) {
		remainder, err := nativeRequired(result, "remainder")
		if err != nil {
			return 0, err
		}
		return 0, c.output(out, nativeMap("schema", "cairn.compact-inbox-batch/1", "ok", true, "next", nil, "remainder", remainder), false)
	}
	var text *value.Value
	obs := nativeMap("event", "PostToolUse", "phase", "active", "native_turn_id", attempt.Get("native_turn_id"))
	if c.NextContext != nil {
		text, err = c.NextContext(ctx, config, state, path, obs)
	} else if c.Compatibility != nil {
		text, err = c.RenderActiveAttemptContext(ctx, config, state, path, obs)
	} else {
		return 0, &value.ProfileUnavailable{}
	}
	if err != nil {
		return 0, err
	}
	if !value.Truth(text) {
		return 0, nativeRefusal("INVALID_HOST", "advanced notice has no native context")
	}
	index, err := nativeRequired(next, "batch_index")
	if err != nil {
		return 0, err
	}
	return 0, c.output(out, nativeMap("schema", "cairn.compact-inbox-batch/1", "ok", true, "next", text, "batch_index", index, "batch_limit", 3), false)
}
