package memoryhook

import (
	"context"
	"errors"
	"math/big"
	"os"
	"path/filepath"
	"syscall"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/statefile"
)

var captureFields = []string{"captured_digest", "captured_messages", "capture_snapshot_marker", "codex_capture_marker", "capture_pending", "last_capture"}

func captureEventName(event *value.Value) bool {
	return contains([]string{"PreCompact", "SessionEnd", "Stop"}, text(event.Get("hook_event_name")))
}
func captureState(path string) (*value.Value, error) {
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return value.Mapping(), nil
	}
	if err != nil {
		return nil, err
	}
	state, err := value.DecodeText(raw, "utf-8", 0)
	if err == nil && state.Kind != value.Object {
		panic("unexpected memory state shape")
	}
	return state, err
}

// Equality follows JSON value equality for capture-owned state; object order
// does not confer identity. Absent members stay distinct from explicit null.
func captureEqual(a, b *value.Value) bool {
	if a == nil || b == nil {
		return a == b || a == nil && b.Kind == value.Null || b == nil && a.Kind == value.Null
	}
	rational := func(v *value.Value) *big.Rat {
		switch v.Kind {
		case value.Bool:
			if v.Boolean {
				return big.NewRat(1, 1)
			}
			return new(big.Rat)
		case value.Int:
			n, ok := new(big.Int).SetString(v.Integer, 10)
			if ok {
				return new(big.Rat).SetInt(n)
			}
		case value.Float:
			return new(big.Rat).SetFloat64(v.Number)
		}
		return nil
	}
	if x, y := rational(a), rational(b); x != nil && y != nil {
		return x.Cmp(y) == 0
	}
	if a.Kind != b.Kind {
		return false
	}
	switch a.Kind {
	case value.Null:
		return true
	case value.String:
		return string(a.String) == string(b.String)
	case value.Array:
		if len(a.Items) != len(b.Items) {
			return false
		}
		for i, v := range a.Items {
			if !captureEqual(v, b.Items[i]) {
				return false
			}
		}
		return true
	case value.Object:
		if len(a.Members) != len(b.Members) {
			return false
		}
		for _, m := range a.Members {
			v := b.Get(string(m.Key))
			if v == nil || !captureEqual(m.Value, v) {
				return false
			}
		}
		return true
	}
	return false
}
func captureOwned(state *value.Value) *value.Value {
	out := value.Mapping()
	for _, key := range captureFields {
		if v := state.Get(key); v != nil {
			out.Set(key, v)
		}
	}
	return out
}
func captureBinding(state *value.Value) *value.Value {
	if v := state.Get("binding"); v != nil {
		return v
	}
	return array(nil, value.Text(""))
}
func captureEventState(state, event *value.Value, project string) *value.Value {
	next := array(event.Get("project_path"), value.Text(text(event.Get("workstream"))))
	if !captureEqual(captureBinding(state), next) {
		state = value.Mapping(value.Field("binding", next))
	}
	if topic := text(event.Get("workstream")); topic != "" {
		state.Set("workstream", value.Text(workstreamPrefix(project)+topic))
	}
	return state
}
func captureStatus(state *value.Value, outcome string) {
	state.Set("last_capture", value.Mapping(value.Field("at", floating(float64(time.Now().UnixNano())/1e9)), value.Field("outcome", value.Text(outcome)), value.Field("records", array()), value.Field("selector_calls", integer(0))))
}

func runCapture(ctx context.Context, config, event *value.Value, directory string, started time.Time, trace map[string]any) (result Result) {
	observed := value.Mapping()
	boundary := func(err error) Result { return failure(event, err) }
	for _, key := range []string{"repo", "cairn", "socket", "token_file", "state_dir"} {
		if config.Get(key) == nil {
			return boundary(&value.Error{Class: "KeyError", Message: key})
		}
	}
	dir, err := pathValue(config.Get("state_dir"))
	if err != nil {
		return boundary(err)
	}
	if err = os.MkdirAll(dir, 0700); err != nil {
		return boundary(err)
	}
	session := text(event.Get("session_id"))
	if value.SameText(config.Get("harness"), "hermes") {
		session = hashText(session)
	}
	path := filepath.Join(dir, session+".json")
	lockPath := filepath.Join(dir, session+".lock")
	trace["stage"] = "lock_attempt"
	captureEntered := false
	var output *value.Value
	defer func() {
		if observed.Get("outcome") == nil {
			observed.Set("outcome", value.Text("error"))
		}
		captureReport(ctx, config, event, started, observed)
	}()
	err = statefile.LegacyLock(ctx, filepath.Join(dir, session+".capture.lock"), 0, func() error {
		captureEntered = true
		var original, state *value.Value
		locked := false
		err := statefile.LegacyLock(ctx, lockPath, 0, func() error {
			locked = true
			trace["stage"] = "locked"
			var e error
			original, e = captureState(path)
			if e != nil {
				return e
			}
			project, e := projectRoot(event, directory)
			if e != nil {
				return e
			}
			state = captureEventState(clone(original), event, project)
			return nil
		})
		if !locked && errors.Is(err, syscall.EAGAIN) {
			trace["cause"] = "lock_busy"
			return fail("a memory hook is already running for this session")
		}
		if err != nil {
			return err
		}
		codex := value.SameText(config.Get("harness"), "codex")
		transcript := event.Get("transcript_path")
		missing := codex && (transcript == nil || transcript.Kind != value.String || len(transcript.String) == 0)
		if missing {
			state.Set("capture_pending", value.Mapping(value.Field("reason", value.Text("transcript_unavailable")), value.Field("hook_event", event.Get("hook_event_name"))))
			captureStatus(state, "transcript_unavailable")
			observed.Set("outcome", value.Text("transcript_unavailable"))
			output = value.Mapping(value.Field("systemMessage", value.Text("Cairn capture is pending because Codex supplied no transcript path. It can retry if a later Stop or PreCompact hook provides a valid path.")))
		} else {
			var short bool
			if value.SameText(event.Get("hook_event_name"), "Stop") && !value.Truth(state.Get("capture_pending")) {
				messages, offsets, e := captureConversation(text(transcript))
				if e != nil {
					return e
				}
				count := len(messages.Items)
				marker := state.Get("codex_capture_marker")
				resolvedPath, e := resolved(text(transcript), directory)
				if e != nil {
					return e
				}
				info, e := os.Stat(text(transcript))
				if e != nil {
					return e
				}
				if marker != nil && marker.Kind == value.Object && value.SameText(marker.Get("path"), resolvedPath) && marker.Get("offset") != nil && (marker.Get("offset").Kind == value.Int || marker.Get("offset").Kind == value.Bool) {
					at := captureOffset(marker.Get("offset"))
					if at <= info.Size() {
						count = 0
						for _, end := range offsets {
							if end > at {
								count++
							}
						}
					}
				}
				short = count < 6
			}
			if short {
				trace["outcome"] = "early_return:stop_too_little_dialogue"
				observed.Set("outcome", value.Text("too_little_dialogue"))
				output = value.Mapping()
			} else {
				trace["stage"] = "capture"
				prior := state.Get("capture_snapshot_marker")
				state = state.Without("capture_snapshot_marker")
				output, err = captureNoProvider(config, event, state, observed, directory)
				if err != nil {
					return err
				}
				outcome := text(state.Get("last_capture").Get("outcome"))
				observed.Set("outcome", value.Text(outcome))
				if contains([]string{"unchanged", "courtesy", "saved", "nothing_selected"}, outcome) {
					state = state.Without("capture_pending")
				}
				if codex && value.Truth(state.Get("capture_snapshot_marker")) {
					state.Set("codex_capture_marker", state.Get("capture_snapshot_marker"))
				} else if prior != nil {
					state.Set("capture_snapshot_marker", prior)
				}
			}
		}
		wait := time.Duration(0)
		name, harness := text(event.Get("hook_event_name")), text(config.Get("harness"))
		known := harness == "codex" && contains([]string{"Stop", "PreCompact"}, name) || contains([]string{"claude", "hermes", "opencode"}, harness) && contains([]string{"PreCompact", "SessionEnd"}, name)
		if known {
			wait = min(5*time.Second, time.Until(started.Add(145*time.Second)))
			if end, ok := ctx.Deadline(); ok {
				wait = min(wait, time.Until(end.Add(-5*time.Second)))
			}
		}
		commitEntered := false
		err = captureCommitLock(ctx, lockPath, wait, func() error {
			commitEntered = true
			current, e := captureState(path)
			if e != nil {
				return e
			}
			changes := !captureEqual(captureBinding(state), captureBinding(current))
			if !captureEqual(captureBinding(current), captureBinding(original)) || !captureEqual(captureOwned(current), captureOwned(original)) || changes && !captureEqual(current, original) {
				return fail("capture state changed before commit; selected notes may already be saved")
			}
			project, e := projectRoot(event, directory)
			if e != nil {
				return e
			}
			workstreamUnchanged := captureEqual(current.Get("workstream"), original.Get("workstream"))
			if changes {
				current = captureEventState(current, event, project)
			}
			if workstreamUnchanged && state.Get("workstream") != nil {
				current.Set("workstream", state.Get("workstream"))
			}
			for _, key := range captureFields {
				if v := state.Get(key); v != nil {
					current.Set(key, v)
				} else {
					current = current.Without(key)
				}
			}
			if e = saveState(path, current); e != nil {
				return e
			}
			trace["stage"] = "saved"
			if value.SameText(config.Get("harness"), "hermes") {
				status := value.Mapping()
				for _, key := range []string{"last_recall", "last_capture", "workstream"} {
					if v := current.Get(key); v != nil {
						status.Set(key, v)
					}
				}
				output.Set("cairn_status", status)
			}
			return nil
		})
		if err != nil {
			observed.Set("outcome", value.Text("state_commit_error"))
			if !commitEntered && (errors.Is(err, syscall.EAGAIN) || errors.Is(err, context.DeadlineExceeded)) {
				trace["cause"] = "lock_busy"
				return fail("capture could not commit state; selected notes may already be saved")
			}
		}
		return err
	})
	if !captureEntered && errors.Is(err, syscall.EAGAIN) {
		err = fail("a capture is already running for this session")
	}
	if err != nil {
		return boundary(err)
	}
	raw, err := encoded(output, false)
	if err != nil {
		return boundary(err)
	}
	return Result{Stdout: string(raw) + "\n"}
}

// Capture's final commit has Source's 50 ms cadence and never tries again after
// its wait expires. The shared LegacyLock has a different retry policy.
func captureCommitLock(ctx context.Context, path string, wait time.Duration, run func() error) error {
	lock, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0666)
	if err != nil {
		return err
	}
	defer lock.Close()
	deadline := time.Now().Add(wait)
	for {
		err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			return run()
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			return err
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return err
		}
		timer := time.NewTimer(min(50*time.Millisecond, remaining))
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
		if !time.Now().Before(deadline) {
			return err
		}
	}
}

func captureOffset(v *value.Value) int64 {
	if v.Kind == value.Bool {
		if v.Boolean {
			return 1
		}
		return 0
	}
	n, _ := new(big.Int).SetString(v.Integer, 10)
	if n == nil {
		return 0
	}
	if !n.IsInt64() {
		if n.Sign() > 0 {
			return 1<<63 - 1
		}
		return -1 << 63
	}
	return n.Int64()
}

func captureNoProvider(config, event, state, observed *value.Value, directory string) (*value.Value, error) {
	codex := value.SameText(config.Get("harness"), "codex") && event.Get("messages") == nil
	var messages *value.Value
	var offsets []int64
	var err error
	if supplied := event.Get("messages"); supplied != nil {
		messages, err = captureBounded(supplied)
	} else {
		if event.Get("transcript_path") == nil {
			return nil, &value.Error{Class: "KeyError", Message: "transcript_path"}
		}
		path, e := pathValue(event.Get("transcript_path"))
		if e != nil {
			return nil, e
		}
		messages, offsets, err = captureConversation(path)
	}
	if err != nil {
		return nil, err
	}
	if len(messages.Items) == 0 {
		captureStatus(state, "empty")
		return value.Mapping(), nil
	}
	if codex {
		path, err := resolved(text(event.Get("transcript_path")), directory)
		if err != nil {
			return nil, err
		}
		state.Set("capture_snapshot_marker", value.Mapping(value.Field("path", value.Text(path)), value.Field("offset", value.IntegerText(new(big.Int).SetInt64(offsets[len(offsets)-1]).String()))))
	}
	fingerprint := func(dialogue *value.Value) (string, error) {
		schema, e := value.DecodeText([]byte(captureSchemaJSON), "utf-8", 0)
		if e != nil {
			return "", e
		}
		raw, e := encoded(array(value.Text(capturePrompt), schema, config.Get("model"), dialogue), false)
		return hashText(string(raw)), e
	}
	digest, err := fingerprint(messages)
	if err != nil {
		return nil, err
	}
	observed.Set("snapshot_sha256", value.Text(digest))
	if value.SameText(state.Get("captured_digest"), digest) {
		observed.Set("admission_candidate", value.Text("confirmed_duplicate"))
		captureStatus(state, "unchanged")
		return value.Mapping(), nil
	}
	count := 0
	if previous := state.Get("captured_messages"); previous != nil {
		switch previous.Kind {
		case value.Int, value.Bool:
			count = int(captureOffset(previous))
		case value.Float:
			if previous.Number > 0 && previous.Number <= float64(len(messages.Items)) {
				return nil, &value.Error{Class: "TypeError", Message: "capture prefix requires an integer"}
			}
		default:
			return nil, &value.Error{Class: "TypeError", Message: "capture prefix requires a number"}
		}
	}
	next := messages
	if count > 0 && count <= len(messages.Items) {
		prefix := array(messages.Items[:count]...)
		hash, e := fingerprint(prefix)
		if e != nil {
			return nil, e
		}
		if value.SameText(state.Get("captured_digest"), hash) {
			next = array(messages.Items[count:]...)
			transport := len(next.Items) > 0
			for _, m := range next.Items {
				transport = transport && value.SameText(m.Get("role"), "user") && bindingTaskless(m.Get("text"))
			}
			if transport {
				observed.Set("admission_candidate", value.Text("transport_only"))
			}
		}
	}
	if captureCourtesy(next) {
		state.Set("captured_digest", value.Text(digest))
		state.Set("captured_messages", integer(len(messages.Items)))
		captureStatus(state, "courtesy")
		return value.Mapping(), nil
	}
	return nil, fail("provider-selected substantive capture remains unavailable")
}
