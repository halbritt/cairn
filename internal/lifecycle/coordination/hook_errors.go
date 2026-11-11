package coordination

import (
	"context"
	"errors"
	"io"
	"os"
	"strconv"
	"strings"
	"syscall"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/sys/unix"
)

const (
	SiteStateRead = SiteSessionRead
	SiteCueRead   = SiteSessionRead
)

func nativeList(items ...*value.Value) *value.Value {
	return &value.Value{Kind: value.Array, Items: items}
}

func nativeInt64(n int64) *value.Value {
	return value.IntegerText(strconv.FormatInt(n, 10))
}

func isOSPathErr(err error) bool {
	if err == nil {
		return false
	}
	var pe *os.PathError
	var le *os.LinkError
	var se *os.SyscallError
	return errors.Is(err, os.ErrNotExist) || errors.Is(err, os.ErrPermission) || errors.As(err, &pe) || errors.As(err, &le) || errors.As(err, &se)
}

func isCoordErr(err error) bool {
	var ce *CoordinationError
	var je *journal.Error
	return errors.As(err, &ce) || errors.As(err, &je)
}

func statMatchesUID(st os.FileInfo, uid uint32) bool {
	if st == nil {
		return false
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	return ok && sys.Uid == uid
}

func bootTicksNow() (*value.Value, error) {
	var ts unix.Timespec
	if err := unix.ClockGettime(unix.CLOCK_BOOTTIME, &ts); err != nil {
		return nil, err
	}
	ticks := int64((float64(ts.Sec) + float64(ts.Nsec)/1e9) * 100)
	return nativeInt64(ticks), nil
}

func (c *CLI) sameProcess(current, recorded *value.Value) (bool, error) {
	return nativeSameProcess(current, recorded)
}

func isCanonicalUUID(s string) bool {
	u, err := uuid.Parse(s)
	return err == nil && u.String() == s
}

func validUUIDText(s string) bool {
	return isCanonicalUUID(s)
}

func validNativeSessionID(s string) bool {
	return nativeIDValid(value.Text(s))
}

func (c *CLI) sessionRef(agent *value.Value) (*value.Value, error) {
	return (&NativeSession{Host: c.Host}).sessionRef(agent)
}

func (c *CLI) call(ctx context.Context, config *value.Value, op string, body *value.Value, opts NativeCallOptions) (*value.Value, error) {
	if c == nil || c.Host == nil || c.Host.Call == nil {
		return nil, &value.ProfileUnavailable{}
	}
	return c.Host.Call(ctx, config, op, body, opts)
}

func (c *CLI) writeState(path string, v *value.Value) error {
	if c != nil && c.Host != nil && c.Host.Persist != nil {
		return c.Host.Persist(path, v)
	}
	return sourceStateWriter(FileHostOptions{Profile: c.profile()}, SiteSessionWrite)(path, v)
}

func (c *CLI) readContextJSON(path string) (*value.Value, error) {
	if c != nil && c.Host != nil && c.Host.ReadContext != nil {
		return c.Host.ReadContext(path)
	}
	return c.read(path, SiteInboxContextRead)
}

func (c *CLI) statePath(config *value.Value, native string) (string, error) {
	if c == nil || c.Host == nil || c.Host.Locate == nil {
		return "", &value.ProfileUnavailable{}
	}
	return c.Host.Locate(config, native)
}

func (c *CLI) locate(config *value.Value, native string) (string, error) {
	return c.statePath(config, native)
}

func (c *CLI) lockState(path string) (io.Closer, error) {
	if c != nil && c.Host != nil && c.Host.Lock != nil {
		return c.Host.Lock(path)
	}
	lease, err := AcquireSessionLock(path)
	if err != nil {
		return nil, err
	}
	return sessionLease{lease: lease}, nil
}

func (c *CLI) lock(path string) (io.Closer, error) {
	return c.lockState(path)
}

func (c *CLI) pathText(v *value.Value) (string, error) {
	return sourcePathText(c.profile(), v)
}

func (c *CLI) lookupEnv(key string) (string, bool) {
	if c != nil && c.LookupEnv != nil {
		return c.LookupEnv(key)
	}
	if c != nil && c.Env != nil {
		if v := c.Env(key); v != "" {
			return v, true
		}
		return "", false
	}
	return os.LookupEnv(key)
}

func (c *CLI) grants() GrantBindings {
	g := GrantBindings{Profile: c.profile(), UID: c.UID}
	if c.Host != nil && c.Host.Services != nil && c.Host.Services.GrantLoader != nil {
		g.Loader = c.Host.Services.GrantLoader
	}
	if c.Host != nil && c.Host.Services != nil && c.Host.Services.MemoryStateDir != nil {
		g.MemoryStateDir = c.Host.Services.MemoryStateDir
		return g
	}
	g.MemoryStateDir = func(cfg *value.Value) (*value.Value, error) {
		if c.Compatibility != nil {
			mem, err := c.Compatibility.Validate(c.profile(), cfg, false)
			if err != nil {
				return nil, err
			}
			if mem != nil && mem.Kind == value.Object {
				return mem.Get("state_dir"), nil
			}
		}
		mem, err := c.validateManifestMemoryConfig(cfg)
		if err != nil {
			return nil, err
		}
		if mem == nil || mem.Kind != value.Object {
			return nil, &value.Error{Class: "ValueError", Message: "invalid memory config"}
		}
		return mem.Get("state_dir"), nil
	}
	return g
}

// SubprocessError models Python subprocess.SubprocessError for activation and hook boundaries.
type SubprocessError struct {
	Message string
	Cause   error
}

func (e *SubprocessError) Error() string {
	if e != nil && e.Message != "" {
		return e.Message
	}
	return "subprocess error"
}

func (e *SubprocessError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Cause
}

// InboxRecallError carries closed-vocabulary refusal metadata from the inbox_recall bridge.
type InboxRecallError struct {
	Class             string
	Code              string
	Reason            string
	Message           string
	RecallPhase       string
	RecallOperation   string
	RecallAttempts    int
	RecallMaxAttempts int
	DeadlineRemaining *int
	RefusalStage      string
	RefusalReason     string
	Cause             error
}

func (e *InboxRecallError) Error() string {
	if e != nil && e.Message != "" {
		return e.Message
	}
	if e != nil && e.Class != "" {
		return e.Class
	}
	return "inbox recall error"
}

func (e *InboxRecallError) ErrorClass() string {
	if e != nil && e.Class != "" {
		return e.Class
	}
	return "HookError"
}

func (e *InboxRecallError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Cause
}


func nativePromptRefused(message string) *CoordinationError {
	return &CoordinationError{Code: "NATIVE_PROMPT_REFUSED", Message: message}
}

func nativePromptRefusedWithCause(message string, cause error) *CoordinationError {
	return &CoordinationError{Code: "NATIVE_PROMPT_REFUSED", Message: message, Cause: cause}
}

func isNativePromptRefused(err error) bool {
	var ce *CoordinationError
	return errors.As(err, &ce) && ce != nil && ce.Code == "NATIVE_PROMPT_REFUSED"
}

func refusalErrorClass(err error) string {
	if err == nil {
		return "OtherError"
	}
	var name string
	switch e := err.(type) {
	case *CoordinationError:
		name = "CoordinationError"
	case *journal.Error:
		name = "CoordinationError"
	case *InboxRecallError:
		name = e.ErrorClass()
	default:
		name = sourceErrorName(err)
	}
	if nativeContains(diagnosticClasses, name) {
		return name
	}
	return "OtherError"
}

func annotateInboxRefusal(err error, stage string) error {
	if err == nil {
		return nil
	}
	switch e := err.(type) {
	case *CoordinationError:
		e.RefusalStage = stage
		if strings.TrimSpace(e.RefusalReason) == "" {
			if stage == "event_renew" {
				e.RefusalReason = "renew_failed"
			} else if stage == "inbox_recall_bridge" {
				e.RefusalReason = "recall_refused"
			}
		}
		return e
	case *InboxRecallError:
		e.RefusalStage = stage
		if strings.TrimSpace(e.RefusalReason) == "" {
			if stage == "event_renew" {
				e.RefusalReason = "renew_failed"
			} else if stage == "inbox_recall_bridge" {
				e.RefusalReason = "recall_refused"
			}
		}
		return e
	default:
		return err
	}
}

func refusalStageAndReason(err error) (string, string) {
	switch e := err.(type) {
	case *CoordinationError:
		if e != nil {
			return e.RefusalStage, e.RefusalReason
		}
	case *InboxRecallError:
		if e != nil {
			return e.RefusalStage, e.RefusalReason
		}
	}
	return "", ""
}

func refusalCauseDetails(cause, wrapper error) *value.Value {
	details := value.Mapping()
	stage, refusalReason := refusalStageAndReason(wrapper)
	if stage == "" {
		stage, _ = refusalStageAndReason(cause)
	}
	if refusalReason == "" {
		_, refusalReason = refusalStageAndReason(cause)
	}
	if stage != "" && refusalReason != "" {
		copyInboxRefusalDetails(nativeMap("refusal_stage", stage, "refusal_reason", refusalReason), details)
	}
	if cause == nil {
		return details
	}
	var code, reason string
	switch e := cause.(type) {
	case *InboxRecallError:
		if e != nil {
			code, reason = e.Code, e.Reason
			raw := nativeMap("recall_phase", e.RecallPhase, "recall_operation", e.RecallOperation)
			if e.RecallAttempts > 0 {
				raw.Set("recall_attempts", nativeInt64(int64(e.RecallAttempts)))
			}
			if e.RecallMaxAttempts > 0 {
				raw.Set("recall_max_attempts", nativeInt64(int64(e.RecallMaxAttempts)))
			}
			if e.DeadlineRemaining != nil {
				raw.Set("deadline_remaining_ms", nativeInt64(int64(*e.DeadlineRemaining)))
			}
			sanitizeRecallMeta(raw, details)
		}
	case *CoordinationError:
		if e != nil {
			code = e.Code
		}
	case *journal.Error:
		if e != nil {
			code = e.Code
		}
	}
	if nativeContains(causeCodes, code) {
		details.Set("cause_code", value.Text(code))
	}
	if nativeContains(causeReasons, reason) {
		details.Set("cause_reason", value.Text(reason))
	}
	return details
}

func (e *RecoveryEngine) SettleRefusedClaudeWakeWithError(ctx context.Context, s *NativeSession, turn string, exc error, origin string, requireActive bool, proof *value.Value) (*value.Value, error) {
	attempt, err := nativeObj(s.Attempt(), "inbox_attempt")
	if err != nil {
		return nil, err
	}
	delivery, err := nativeObj(attempt.Get("delivery"), "delivery")
	if err != nil {
		return nil, err
	}
	at, err := e.now()
	if err != nil {
		return nil, err
	}
	errorClass := refusalErrorClass(exc)
	cause := errors.Unwrap(exc)
	var causeClass any
	if cause != nil {
		causeClass = refusalErrorClass(cause)
	}
	diagnostic := nativeMap("phase", "bridge_after_claim", "origin", origin, "error_class", errorClass, "cause_class", causeClass, "attempt_id", attempt.Get("attempt_id"), "delivery_id", delivery.Get("delivery_id"), "native_turn_id", turn, "at", at)
	details := refusalCauseDetails(cause, exc)
	for _, m := range details.Members {
		diagnostic.Set(string(m.Key), m.Value)
	}
	if proof != nil {
		diagnostic.Set("native_record", proof)
	}
	eligible, err := e.RefusedClaudeWakeSettleable(s, turn)
	if err != nil {
		return nil, err
	}
	if eligible && requireActive {
		active, err := nativeObj(s.Record.Get("active_prompt"), "active_prompt")
		if err != nil {
			return nil, err
		}
		eligible = value.SameText(active.Get("id"), turn)
	}
	grant := "not_checked"
	var kind *value.Value
	if eligible {
		if e.Hooks.GrantEvidence == nil {
			return nil, &value.ProfileUnavailable{}
		}
		grant, kind, err = e.Hooks.GrantEvidence(ctx, s.Config, s.Record, attempt)
		if err != nil {
			return nil, err
		}
		if grant == "busy" {
			grant = "unknown"
		}
	}
	diagnostic.Set("grant", value.Text(grant))
	if kind != nil {
		diagnostic.Set("grant_kind", kind)
	}
	if !eligible || grant != "absent" {
		diagnostic.Set("settlement", value.Text("held"))
		s.Record.Set("wake_refusal_diagnostic", diagnostic)
		return diagnostic, s.Save()
	}
	diagnostic.Set("settlement", value.Text("turn_ended"))
	return s.Settle(ctx, "wake_refusal_diagnostic", diagnostic, value.Text(turn), true)
}
