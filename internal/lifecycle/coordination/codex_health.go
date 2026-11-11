package coordination

import (
	"context"
	"errors"
	"math"
	"os"
	"reflect"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/lifecycle/wake"
	"github.com/halbritt/cairn/internal/procpeer"
)

type CodexHealthBindings struct {
	ProcRoot   string
	Now        func() time.Time
	Alive      func(*value.Value) (bool, error)
	SessionRef func(*value.Value) (*value.Value, error)
	Endpoint   func(context.Context, *value.Value, *value.Value) (*value.Value, error)
	Inspector  func(context.Context, *value.Value, *value.Value, *value.Value, *value.Value) (*value.Value, error)
}

func (b CodexHealthBindings) reference(agent *value.Value) (*value.Value, error) {
	if b.SessionRef != nil {
		return b.SessionRef(agent)
	}
	return (&NativeSession{Host: &Host{}}).sessionRef(agent)
}
func sourceSeconds(v *value.Value) (float64, error) {
	if v == nil {
		return 0, timeFailure("TypeError", "not a number")
	}
	switch v.Kind {
	case value.Bool:
		if v.Boolean {
			return 1, nil
		}
		return 0, nil
	case value.Float:
		return v.Number, nil
	case value.Int:
		n, err := strconv.ParseFloat(v.Integer, 64)
		if err != nil {
			return 0, timeFailure("OverflowError", "int too large to convert to float")
		}
		return n, nil
	}
	return 0, timeFailure("TypeError", "not a number")
}
func (b CodexHealthBindings) endpoint(_ context.Context, config, process *value.Value) (*value.Value, error) {
	if !value.SameText(config.Get("harness"), "codex") {
		return nil, nil
	}
	pid, err := nativeRequired(process, "pid")
	if err != nil {
		return nil, err
	}
	name, err := procName(pid)
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(strings.TrimRight(procRoot(b.ProcRoot), "/") + "/" + name + "/cmdline")
	if err != nil {
		return nil, nil
	}
	if !utf8.Valid(raw) {
		return nil, &value.Error{Class: "UnicodeDecodeError", Message: "Codex command line is not UTF-8"}
	}
	endpoint, ok := wake.ParseCodexQueueArgv("codex", strings.Split(string(raw), "\x00"))
	if !ok {
		return nil, nil
	}
	return nativeMap("endpoint", endpoint.Endpoint, "owner", endpoint.OwnerMode), nil
}
func (b CodexHealthBindings) inspect(ctx context.Context, endpoint, process, native, queued *value.Value) (*value.Value, error) {
	pid, err := nativeRequired(process, "pid")
	if err != nil {
		return nil, err
	}
	start, err := nativeRequired(process, "start")
	if err != nil {
		return nil, err
	}
	boot, err := nativeRequired(process, "boot")
	if err != nil {
		return nil, err
	}
	if pid.Kind != value.Int || boot.Kind != value.String || native == nil || native.Kind != value.String {
		return nil, &journal.TypeError{Field: "Codex process reference"}
	}
	p, err := strconv.Atoi(pid.Integer)
	if err != nil {
		return nil, err
	}
	s, err := codexPeerStart(start)
	if err != nil {
		return nil, err
	}
	address, err := nativeRequired(endpoint, "endpoint")
	if err != nil {
		return nil, err
	}
	if address.Kind != value.String {
		return nil, &journal.TypeError{Field: "Codex endpoint"}
	}
	owner := procpeer.ProcessOwner
	if value.SameText(endpoint.Get("owner"), "uid") {
		owner = procpeer.UserOwner
	}
	inspection, err := wake.CodexInspectThread(ctx, nativeText(address), wake.ProcessRef{PID: p, Start: s, Boot: nativeText(boot)}, nativeText(native), owner, nativeText(queued))
	if err != nil {
		return nil, err
	}
	ids := &value.Value{Kind: value.Array}
	for _, id := range inspection.QueuedIDs {
		ids.Items = append(ids.Items, value.Text(id))
	}
	var updated, qp *value.Value
	if inspection.UpdatedAt != nil {
		updated = value.IntegerText(strconv.FormatInt(*inspection.UpdatedAt, 10))
	}
	if inspection.QueuedPresent != nil {
		qp = value.Boolean(*inspection.QueuedPresent)
	}
	return nativeMap("status_type", inspection.StatusType, "updated_at", updated, "queue_count", inspection.QueueCount, "queued_ids", ids, "queued_present", qp, "peer", nativeMap("pid", inspection.Peer.PID, "start", value.IntegerText(strconv.FormatUint(inspection.Peer.Start, 10)), "boot", inspection.Peer.Boot)), nil
}
func sourceErrorName(err error) string {
	var ir *InboxRecallError
	if errors.As(err, &ir) && ir != nil {
		return ir.ErrorClass()
	}
	var decode *journal.UnicodeDecodeError
	if errors.As(err, &decode) {
		return "UnicodeDecodeError"
	}
	var errno syscall.Errno
	if errors.As(err, &errno) {
		switch errno {
		case syscall.EAGAIN, syscall.EALREADY, syscall.EINPROGRESS:
			return "BlockingIOError"
		case syscall.ECHILD:
			return "ChildProcessError"
		case syscall.EPIPE, syscall.ESHUTDOWN:
			return "BrokenPipeError"
		case syscall.ECONNABORTED:
			return "ConnectionAbortedError"
		case syscall.ECONNREFUSED:
			return "ConnectionRefusedError"
		case syscall.ECONNRESET:
			return "ConnectionResetError"
		case syscall.EEXIST:
			return "FileExistsError"
		case syscall.ENOENT:
			return "FileNotFoundError"
		case syscall.EINTR:
			return "InterruptedError"
		case syscall.EISDIR:
			return "IsADirectoryError"
		case syscall.ENOTDIR:
			return "NotADirectoryError"
		case syscall.EACCES, syscall.EPERM:
			return "PermissionError"
		case syscall.ESRCH:
			return "ProcessLookupError"
		case syscall.ETIMEDOUT:
			return "TimeoutError"
		default:
			return "OSError"
		}
	}
	if class := value.ErrorClass(err); class != "GoError" && class != "" {
		return class
	}
	var key *journal.KeyError
	var typ *journal.TypeError
	var object *journal.NonObjectError
	var path *os.PathError
	var call *os.SyscallError
	if errors.As(err, &key) {
		return "KeyError"
	}
	if errors.As(err, &typ) {
		return "TypeError"
	}
	if errors.As(err, &object) {
		return "AttributeError"
	}
	if errors.As(err, &path) || errors.As(err, &call) || errors.As(err, &errno) {
		return "OSError"
	}
	t := reflect.TypeOf(err)
	if t != nil && t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	if t == nil {
		return "Exception"
	}
	return t.Name()
}

func sourceOSError(err error) bool {
	if err == nil {
		return false
	}
	var path *os.PathError
	var call *os.SyscallError
	var errno syscall.Errno
	return errors.As(err, &path) || errors.As(err, &call) || errors.As(err, &errno) ||
		sourceClass(err, "OSError", "BlockingIOError", "ChildProcessError", "BrokenPipeError", "ConnectionError", "ConnectionAbortedError", "ConnectionRefusedError", "ConnectionResetError", "FileExistsError", "FileNotFoundError", "InterruptedError", "IsADirectoryError", "NotADirectoryError", "PermissionError", "ProcessLookupError", "TimeoutError")
}

func codexPeerStart(start *value.Value) (uint64, error) {
	if start != nil && start.Kind == value.Float && start.Number >= 0 && start.Number < math.Exp2(64) && math.Trunc(start.Number) == start.Number {
		return uint64(start.Number), nil
	}
	if start == nil || start.Kind != value.Int {
		return 0, &journal.TypeError{Field: "Codex process reference"}
	}
	return strconv.ParseUint(start.Integer, 10, 64)
}
func sourceList(v *value.Value) (*value.Value, error) {
	out := &value.Value{Kind: value.Array}
	if !value.Truth(v) {
		return out, nil
	}
	switch v.Kind {
	case value.Array:
		out.Items = append(out.Items, v.Items...)
	case value.String:
		for _, r := range v.String {
			out.Items = append(out.Items, &value.Value{Kind: value.String, String: []rune{r}})
		}
	case value.Object:
		for _, m := range v.Members {
			out.Items = append(out.Items, &value.Value{Kind: value.String, String: append([]rune(nil), m.Key...)})
		}
	default:
		return nil, &journal.TypeError{Field: "queued_ids"}
	}
	return out, nil
}

func (b CodexHealthBindings) ProbeCodex(ctx context.Context, config, state *value.Value, force bool) (*value.Value, error) {
	agent, process := state.Get("agent"), state.Get("process")
	if !value.SameText(config.Get("harness"), "codex") || agent == nil || agent.Kind != value.Object || process == nil || process.Kind != value.Object {
		return nil, nil
	}
	clock := b.Now
	if clock == nil {
		clock = time.Now
	}
	t := clock()
	now := float64(t.Unix()) + float64(t.Nanosecond())/1e9
	procID := value.Mapping()
	for _, key := range []string{"pid", "start", "boot"} {
		if v := process.Get(key); v != nil {
			procID.Set(key, v)
		}
	}
	existing := state.Get("native_health")
	if !force && existing != nil && existing.Kind == value.Object {
		ref, err := b.reference(agent)
		if err != nil {
			return nil, err
		}
		if nativeEqual(existing.Get("session"), ref) && nativeEqual(existing.Get("process"), procID) {
			epoch := existing.Get("observed_at_epoch")
			if epoch != nil && (epoch.Kind == value.Int || epoch.Kind == value.Float || epoch.Kind == value.Bool) {
				then, err := sourceSeconds(epoch)
				if err != nil {
					return nil, err
				}
				if 0 <= now-then && now-then < 60 {
					return existing, nil
				}
			}
		}
	}
	aliveFn := b.Alive
	if aliveFn == nil {
		aliveFn = NewProcessHooks(b.ProcRoot).Alive
	}
	alive, err := aliveFn(process)
	if err != nil {
		return nil, err
	}
	var endpoint *value.Value
	if alive {
		discover := b.Endpoint
		if discover == nil {
			discover = b.endpoint
		}
		endpoint, err = discover(ctx, config, process)
		if err != nil {
			return nil, err
		}
	}
	if alive && !value.Truth(endpoint) && !force {
		return nil, nil
	}
	status := "unavailable"
	var updated, qp, peer, reason *value.Value
	queueCount := value.IntegerText("0")
	queuedIDs := &value.Value{Kind: value.Array}
	if !alive {
		reason = value.Text("native process is not alive")
	} else if !value.Truth(endpoint) {
		reason = value.Text("no native Codex queue endpoint (explicit Unix listener) for this process")
	} else {
		inspect := b.Inspector
		if inspect == nil {
			inspect = b.inspect
		}
		inspection, err := func() (*value.Value, error) {
			wake, err := nativeObj(state.Get("idle_wake"), "idle_wake")
			if err != nil {
				return nil, err
			}
			native, err := nativeRequired(agent, "native_session_id")
			if err != nil {
				return nil, err
			}
			return inspect(ctx, endpoint, process, native, wake.Get("queued_submission_id"))
		}()
		if err == nil {
			if inspection == nil || inspection.Kind != value.Object {
				err = &journal.NonObjectError{Field: "Codex inspection"}
			} else {
				status = "unknown"
				candidate := nativeText(inspection.Get("status_type"))
				if nativeContains([]string{"active", "idle", "systemError"}, candidate) {
					status = candidate
				}
				updated = inspection.Get("updated_at")
				if v := inspection.Get("queue_count"); v != nil {
					queueCount = v
				}
				ids, listErr := sourceList(inspection.Get("queued_ids"))
				err = listErr
				if err == nil {
					queuedIDs = ids
					if v := inspection.Get("queued_present"); v != nil && v.Kind == value.Bool {
						qp = v
					}
					peer = inspection.Get("peer")
				}
			}
		}
		if err != nil {
			var unavailable *value.ProfileUnavailable
			if errors.As(err, &unavailable) {
				return nil, err
			}
			var coordination *CoordinationError
			if errors.As(err, &coordination) || wake.IsCodexQueueUnavailable(err) {
				status = "unavailable"
				reason = value.Text(err.Error())
			} else {
				status = "unknown"
				reason = value.Text(sourceErrorName(err))
			}
		}
	}
	metadata, err := nativeObj(agent.Get("metadata"), "metadata")
	if err != nil {
		return nil, err
	}
	reported := metadata.Get("state")
	if reported == nil {
		reported = value.Text("unknown")
	}
	freshness := "unverified"
	if value.SameText(reported, "busy") && nativeContains([]string{"systemError", "idle"}, status) {
		freshness = "stale_busy"
	} else if value.SameText(reported, "idle") && nativeContains([]string{"systemError", "active"}, status) {
		freshness = "stale_idle"
	} else if nativeContains([]string{"active", "idle"}, status) {
		freshness = "current"
	}
	actions := map[string]string{"systemError": "review the pending delivery and conversation state before owner-authorized recovery; do not automatically claim, restart, or replay", "unavailable": "verify the native process and app-server socket before retrying wake delivery; do not automatically claim or replay", "unknown": "re-probe native health before acting; do not automatically claim, restart, or replay", "active": "none; native turn is active", "idle": "inspect session turn state; native thread is idle while metadata reports busy"}
	if value.SameText(reported, "idle") {
		actions["idle"] = "none; native thread is idle"
	}
	ref, err := b.reference(agent)
	if err != nil {
		return nil, err
	}
	native, err := nativeRequired(agent, "native_session_id")
	if err != nil {
		return nil, err
	}
	observed, err := utcTimestamp(&value.Value{Kind: value.Float, Number: now})
	if err != nil {
		return nil, err
	}
	liveness := "dead"
	if alive {
		liveness = "alive"
	}
	readiness := status
	if nativeContains([]string{"active", "idle"}, status) {
		readiness = "ready"
	} else if status == "systemError" {
		readiness = "error"
	}
	health := nativeMap("schema", "cairn.codex-native-health/1", "session", ref, "native_session_id", native, "process", procID, "process_liveness", liveness, "process_alive", alive, "status", status, "status_type", status, "native_readiness", readiness, "task_metadata", nativeMap("reported_state", reported, "context_revision", agent.Get("context_revision"), "freshness", freshness), "updated_at", updated, "queue_count", queueCount, "queued_ids", queuedIDs, "queued_present", qp, "observed_at", observed, "observed_at_epoch", &value.Value{Kind: value.Float, Number: now}, "next_action", actions[status])
	if !nativeNone(peer) {
		health.Set("peer", peer)
	}
	if !nativeNone(reason) {
		health.Set("reason", reason)
	}
	state.Set("native_health", health)
	return health, nil
}
