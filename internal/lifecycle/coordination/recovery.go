package coordination

import (
	"context"
	"errors"
	"math/big"
	"os"
	"regexp"
	"strings"
	"syscall"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// TranscriptEvidence is implemented on transcript's side of the dependency:
// transcript already uses coordination's process helpers. No reverse import,
// second verifier or duplicate journal state machine is introduced here.
type EvidenceKind string

const (
	EvidenceDaemonWorker  EvidenceKind = "daemon_worker"
	EvidenceDaemonClaim   EvidenceKind = "daemon_start_claim"
	EvidenceNativeRefusal EvidenceKind = "native_refusal"
	EvidenceCodexRefusal  EvidenceKind = "codex_refusal"
	EvidenceCodexEnded    EvidenceKind = "codex_ended"
	EvidenceCodexQueue    EvidenceKind = "codex_queue"
	EvidenceCodexLocators EvidenceKind = "codex_refusal_locators"
	EvidenceCodexLatest   EvidenceKind = "codex_latest_end"
)

type EvidenceRequest struct {
	Config, State, Attempt, Recorded, Wake, Locators *value.Value
	TurnID                                           string
}
type TranscriptEvidence interface {
	Read(context.Context, EvidenceKind, EvidenceRequest) (*value.Value, error)
}
type RecoveryHooks struct {
	Alive                func(*value.Value) (bool, error)
	Daemon               func(*value.Value) (bool, error)
	GrantState           func(context.Context, *value.Value, *value.Value, *value.Value) (string, error)
	GrantEvidence        func(context.Context, *value.Value, *value.Value, *value.Value) (string, *value.Value, error)
	PreGrantLedgerAbsent func(context.Context, *value.Value, *value.Value, *value.Value, *value.Value) (bool, error)
	ProbeCodex           func(context.Context, *value.Value, *value.Value, bool) (*value.Value, error)
	ObserveTime          func(*value.Value, float64) (*value.Value, *value.Value, error)
}
type RecoveryEngine struct {
	Host     *Host
	Journal  *SessionJournalAdapter
	Evidence TranscriptEvidence
	Hooks    RecoveryHooks
	Now      func() time.Time
}
type RecoveryRequest struct {
	NativeSessionID, AttemptID, NativeTurnID, DeliveryID, QueuedSubmissionID string
	Records                                                                  *value.Value
	CheckOnly                                                                bool
	Outcome                                                                  *value.Value
	Session                                                                  *NativeSession
}

func (e *RecoveryEngine) now() (*value.Value, error) {
	if e.Now == nil {
		return nil, &value.ProfileUnavailable{}
	}
	t := e.Now()
	return &value.Value{Kind: value.Float, Number: float64(t.Unix()) + float64(t.Nanosecond())/1e9}, nil
}
func (e *RecoveryEngine) alive(process *value.Value) (bool, error) {
	if e.Hooks.Alive == nil {
		return false, &value.ProfileUnavailable{}
	}
	return e.Hooks.Alive(process)
}
func daemonProcessError(err error) bool {
	var path *os.PathError
	var call *os.SyscallError
	var errno syscall.Errno
	var invalidValue *journal.ValueError
	return errors.As(err, &path) || errors.As(err, &call) || errors.As(err, &errno) || errors.As(err, &invalidValue) || sourceClass(err, "OSError", "ValueError", "JSONDecodeError", "UnicodeDecodeError", "UnicodeEncodeError", "IndexError")
}
func (e *RecoveryEngine) grant(ctx context.Context, s *NativeSession, attempt *value.Value) (string, error) {
	if e.Hooks.GrantState == nil {
		return "", &value.ProfileUnavailable{}
	}
	return e.Hooks.GrantState(ctx, s.Config, s.Record, attempt)
}
func (e *RecoveryEngine) evidence(ctx context.Context, kind EvidenceKind, s *NativeSession, attempt, recorded, wake, locators *value.Value, turn string) (*value.Value, error) {
	if e.Evidence == nil {
		return nil, &value.ProfileUnavailable{}
	}
	return e.Evidence.Read(ctx, kind, EvidenceRequest{s.Config, s.Record, attempt, recorded, wake, locators, turn})
}
func (e *RecoveryEngine) withSession(config *value.Value, id, missing string, fn func(*NativeSession) (*value.Value, error)) (*value.Value, error) {
	if missing == "" {
		s, release, err := OpenSession(config, id, e.Host, false)
		if err != nil {
			return nil, err
		}
		result, err := fn(s)
		closeErr := release()
		if err != nil {
			return nil, err
		}
		return result, closeErr
	}
	if e.Host == nil || e.Host.Locate == nil || e.Host.Lock == nil || e.Host.Exists == nil || e.Host.Load == nil {
		return nil, &value.ProfileUnavailable{}
	}
	path, err := e.Host.Locate(config, id)
	if err != nil {
		return nil, err
	}
	lock, err := e.Host.Lock(path)
	if err != nil {
		return nil, err
	}
	defer lock.Close()
	exists, err := e.Host.Exists(path)
	if err != nil {
		return nil, err
	}
	if !exists {
		return nil, nativeRefusal(missing, "no session state for this native session ID")
	}
	record, err := e.Host.Load(path)
	if err != nil {
		return nil, err
	}
	return fn(&NativeSession{config, path, record, e.Host})
}
func recoveryIdentity(s *NativeSession, id string) (agent *value.Value, ended bool, err error) {
	if err = s.mapping(); err != nil {
		return
	}
	agent, err = nativeDefaultObj(s.Record.Get("agent"), "agent")
	if err != nil {
		return
	}
	ended = !value.SameText(agent.Get("native_session_id"), id) || value.Truth(s.Record.Get("ending")) || value.Truth(s.Record.Get("retired"))
	return
}
func (e *RecoveryEngine) RefusedWakeUnprocessed(s *NativeSession, turn string) (bool, error) {
	intent, attempt := s.Intent(), s.Attempt()
	if intent == nil || attempt == nil || intent.Kind != value.Object || attempt.Kind != value.Object {
		return false, nil
	}
	if !value.SameText(intent.Get("native_turn_id"), turn) || !value.SameText(attempt.Get("native_turn_id"), turn) || !nativeEqual(intent.Get("request_id"), attempt.Get("attempt_id")) || !nativeEqual(intent.Get("session"), attempt.Get("session")) {
		return false, nil
	}
	ref, err := s.sessionRef(s.Record.Get("agent"))
	if err != nil {
		return false, err
	}
	if !nativeEqual(attempt.Get("session"), ref) || value.Truth(attempt.Get("finished_at")) || value.Truth(attempt.Get("cancel")) || value.Truth(attempt.Get("lease_lapsed")) {
		return false, nil
	}
	active, err := nativeObj(s.Record.Get("active_prompt"), "active_prompt")
	if err != nil {
		return false, err
	}
	if value.Truth(active.Get("joined")) || value.Truth(s.Record.Get("tool_calls")) || value.Truth(attempt.Get("tools")) || value.Truth(s.Record.Get("inbox_close")) || value.Truth(s.Record.Get("inbox_turn_ended")) {
		return false, nil
	}
	if e.Journal == nil {
		return false, &value.ProfileUnavailable{}
	}
	return e.Journal.AttemptCommandsAbsent(s.Config, attempt)
}
func (e *RecoveryEngine) RefusedClaudeWakeSettleable(s *NativeSession, turn string) (bool, error) {
	if !value.SameText(s.Config.Get("harness"), "claude") || !strings.HasPrefix(turn, "claude-channel:") {
		return false, nil
	}
	return e.RefusedWakeUnprocessed(s, turn)
}
func (e *RecoveryEngine) SettleRefusedClaudeWake(ctx context.Context, s *NativeSession, turn, origin string, requireActive bool, proof *value.Value) (*value.Value, error) {
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
	diagnostic := nativeMap("phase", "bridge_after_claim", "origin", origin, "error_class", "CoordinationError", "cause_class", nil, "attempt_id", attempt.Get("attempt_id"), "delivery_id", delivery.Get("delivery_id"), "native_turn_id", turn, "at", at)
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
func (e *RecoveryEngine) RecoverDaemonStartClaim(ctx context.Context, config *value.Value, r RecoveryRequest) (*value.Value, error) {
	return e.withSession(config, r.NativeSessionID, "", func(s *NativeSession) (*value.Value, error) {
		held := nativeRefusal("INVALID_REQUEST", "held state is not an untouched turn-less start claim")
		if !value.SameText(config.Get("harness"), "claude") {
			return nil, held
		}
		_, ended, err := recoveryIdentity(s, r.NativeSessionID)
		if err != nil {
			return nil, err
		}
		if ended {
			return nil, held
		}
		attempt, err := nativeObj(s.Attempt(), "inbox_attempt")
		if err != nil {
			return nil, err
		}
		if !value.SameText(attempt.Get("attempt_id"), r.AttemptID) {
			return nil, held
		}
		intent, err := nativeObj(s.Intent(), "inbox_intent")
		if err != nil {
			return nil, err
		}
		if !value.SameText(intent.Get("request_id"), r.AttemptID) || value.Truth(attempt.Get("native_turn_id")) || value.Truth(intent.Get("native_turn_id")) {
			return nil, held
		}
		ref, err := s.sessionRef(s.Record.Get("agent"))
		if err != nil {
			return nil, err
		}
		untouched := nativeEqual(attempt.Get("session"), ref)
		for _, v := range []*value.Value{attempt.Get("finished_at"), attempt.Get("cancel"), attempt.Get("tools"), s.Record.Get("tool_calls"), s.Record.Get("active_prompt"), s.Record.Get("inbox_close"), s.Record.Get("inbox_turn_ended")} {
			untouched = untouched && !value.Truth(v)
		}
		if untouched {
			if e.Journal == nil {
				return nil, &value.ProfileUnavailable{}
			}
			untouched, err = e.Journal.AttemptCommandsAbsent(config, attempt)
			if err != nil {
				return nil, err
			}
		}
		if !untouched {
			return nil, held
		}
		if e.Hooks.Daemon == nil {
			return nil, &value.ProfileUnavailable{}
		}
		daemon, err := e.Hooks.Daemon(s.Record.Get("process"))
		if err != nil {
			if daemonProcessError(err) {
				return nil, nativeRefusal("INVALID_REQUEST", "bound process is not a live Claude daemon")
			}
			return nil, err
		}
		alive, err := e.alive(s.Record.Get("process"))
		if err != nil {
			return nil, err
		}
		if !daemon || !alive {
			return nil, nativeRefusal("INVALID_REQUEST", "bound process is not a live Claude daemon")
		}
		worker, err := e.evidence(ctx, EvidenceDaemonWorker, s, attempt, nil, nil, nil, "")
		if err != nil {
			return nil, err
		}
		evidence, err := e.evidence(ctx, EvidenceDaemonClaim, s, attempt, nil, nil, nil, "")
		if err != nil {
			return nil, err
		}
		process := s.Record.Get("process")
		proof := nativeMap("schema", "cairn.daemon-start-claim/1", "daemon", nativeMap("pid", process.Get("pid"), "start", process.Get("start"), "boot", process.Get("boot")), "worker", nativeMap("pid", worker.Get("pid"), "start", worker.Get("start"), "boot", worker.Get("boot")))
		if evidence == nil || evidence.Kind != value.Object {
			return nil, &journal.TypeError{Field: "daemon evidence"}
		}
		proof.Members = append(proof.Members, evidence.Members...)
		if r.CheckOnly {
			return nativeMap("status", "eligible", "attempt_id", r.AttemptID, "worker", proof.Get("worker"), "native_record", proof), nil
		}
		s.Record.Set("process", worker)
		at, err := e.now()
		if err != nil {
			return nil, err
		}
		proof.Set("attempt_id", value.Text(r.AttemptID))
		proof.Set("at", at)
		proof.Set("settlement", value.Text("pending"))
		return s.Settle(ctx, "daemon_start_claim", proof, nil, true)
	})
}
func (e *RecoveryEngine) RecoverRefusedWake(ctx context.Context, config *value.Value, r RecoveryRequest) (*value.Value, error) {
	return e.withSession(config, r.NativeSessionID, "", func(s *NativeSession) (*value.Value, error) {
		held := nativeRefusal("INVALID_REQUEST", "no host-recorded refusal names this held attempt and prompt")
		_, ended, err := recoveryIdentity(s, r.NativeSessionID)
		if err != nil {
			return nil, err
		}
		if ended {
			return nil, held
		}
		alive, err := e.alive(s.Record.Get("process"))
		if err != nil {
			return nil, err
		}
		if !alive {
			return nil, held
		}
		attempt, err := nativeObj(s.Attempt(), "inbox_attempt")
		if err != nil {
			return nil, err
		}
		if !value.SameText(attempt.Get("attempt_id"), r.AttemptID) {
			return nil, held
		}
		recorded, err := nativeObj(s.Record.Get("wake_refusal_diagnostic"), "wake_refusal_diagnostic")
		if err != nil {
			return nil, err
		}
		if !value.SameText(recorded.Get("phase"), "bridge_after_claim") || !value.SameText(recorded.Get("origin"), "hook") || !value.SameText(recorded.Get("settlement"), "held") || !value.SameText(recorded.Get("attempt_id"), r.AttemptID) || !value.SameText(recorded.Get("native_turn_id"), r.NativeTurnID) {
			return nil, held
		}
		delivery, err := nativeObj(attempt.Get("delivery"), "delivery")
		if err != nil {
			return nil, err
		}
		if !nativeEqual(recorded.Get("delivery_id"), delivery.Get("delivery_id")) {
			return nil, held
		}
		eligible, err := e.RefusedClaudeWakeSettleable(s, r.NativeTurnID)
		if err != nil {
			return nil, err
		}
		if !eligible {
			return nil, nativeRefusal("INVALID_REQUEST", "held attempt is not exactly this refused, unprocessed prompt")
		}
		grant, err := e.grant(ctx, s, attempt)
		if err != nil {
			return nil, err
		}
		if grant != "absent" {
			return nil, nativeRefusal("INVALID_REQUEST", "held attempt cannot be proven unprocessed (grant "+grant+")")
		}
		nativePop(s.Record, "wake_refusal_diagnostic")
		return e.SettleRefusedClaudeWake(ctx, s, r.NativeTurnID, "operator_recorded", false, nil)
	})
}
func (e *RecoveryEngine) RecoverNativeRefusedWake(ctx context.Context, config *value.Value, r RecoveryRequest) (*value.Value, error) {
	return e.withSession(config, r.NativeSessionID, "", func(s *NativeSession) (*value.Value, error) {
		held := nativeRefusal("INVALID_REQUEST", "held state does not name exactly one finished-owner claim for this attempt")
		attempt, err := nativeObj(s.Attempt(), "inbox_attempt")
		if err != nil {
			return nil, err
		}
		turn := nativeText(attempt.Get("native_turn_id"))
		_, ended, err := recoveryIdentity(s, r.NativeSessionID)
		if err != nil {
			return nil, err
		}
		if ended {
			return nil, held
		}
		alive, err := e.alive(s.Record.Get("process"))
		if err != nil {
			return nil, err
		}
		if !alive || !value.SameText(attempt.Get("attempt_id"), r.AttemptID) || !strings.HasPrefix(turn, "claude-channel:") {
			return nil, held
		}
		intent, err := nativeObj(s.Intent(), "inbox_intent")
		if err != nil {
			return nil, err
		}
		if !value.SameText(intent.Get("native_turn_id"), turn) {
			return nil, held
		}
		active, err := nativeObj(s.Record.Get("active_prompt"), "active_prompt")
		if err != nil {
			return nil, err
		}
		if value.SameText(active.Get("id"), turn) {
			return nil, held
		}
		proof, err := e.evidence(ctx, EvidenceNativeRefusal, s, attempt, nil, nil, r.Records, turn)
		if err != nil {
			return nil, err
		}
		eligible, err := e.RefusedClaudeWakeSettleable(s, turn)
		if err != nil {
			return nil, err
		}
		if !eligible {
			return nil, nativeRefusal("INVALID_REQUEST", "held attempt is not exactly this refused, unprocessed claim")
		}
		grant, err := e.grant(ctx, s, attempt)
		if err != nil {
			return nil, err
		}
		if grant != "absent" {
			return nil, nativeRefusal("INVALID_REQUEST", "held attempt cannot be proven unprocessed (grant "+grant+")")
		}
		return e.SettleRefusedClaudeWake(ctx, s, turn, "native_record", false, proof)
	})
}

var recoveryUUID = regexp.MustCompile(`^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$`)

func (e *RecoveryEngine) codexRefusalReady(s *NativeSession, id, attemptID string) (attempt, recorded *value.Value, ready bool, err error) {
	attempt, err = nativeObj(s.Attempt(), "inbox_attempt")
	if err != nil {
		return
	}
	turn := nativeText(attempt.Get("native_turn_id"))
	if !value.SameText(s.Config.Get("harness"), "codex") {
		return
	}
	_, ended, identityErr := recoveryIdentity(s, id)
	if identityErr != nil {
		err = identityErr
		return
	}
	if ended {
		return
	}
	ready, err = e.alive(s.Record.Get("process"))
	if !ready || err != nil {
		return
	}
	ready = value.SameText(attempt.Get("attempt_id"), attemptID) && recoveryUUID.MatchString(turn)
	if !ready {
		return
	}
	recorded, err = nativeObj(s.Record.Get("wake_refusal_diagnostic"), "wake_refusal_diagnostic")
	if err != nil {
		return
	}
	ready = value.SameText(recorded.Get("phase"), "bridge_after_claim") && value.SameText(recorded.Get("origin"), "hook") && value.SameText(recorded.Get("settlement"), "held") && value.SameText(recorded.Get("attempt_id"), attemptID) && value.SameText(recorded.Get("native_turn_id"), turn)
	if !ready {
		return
	}
	delivery, deliveryErr := nativeDefaultObj(attempt.Get("delivery"), "delivery")
	if deliveryErr != nil {
		err = deliveryErr
		return
	}
	ready = nativeEqual(recorded.Get("delivery_id"), delivery.Get("delivery_id"))
	if !ready {
		return
	}
	ready, err = e.RefusedWakeUnprocessed(s, turn)
	return
}
func (e *RecoveryEngine) RecoverCodexRefusedWake(ctx context.Context, config *value.Value, r RecoveryRequest) (*value.Value, error) {
	return e.withSession(config, r.NativeSessionID, "", func(s *NativeSession) (*value.Value, error) {
		attempt, recorded, ready, err := e.codexRefusalReady(s, r.NativeSessionID, r.AttemptID)
		if err != nil {
			return nil, err
		}
		if !ready {
			return nil, nativeRefusal("INVALID_REQUEST", "held state is not this unprocessed Codex hook refusal")
		}
		grant, err := e.grant(ctx, s, attempt)
		if err != nil {
			return nil, err
		}
		fallback := false
		if grant == "unknown" {
			if e.Hooks.PreGrantLedgerAbsent == nil {
				return nil, &value.ProfileUnavailable{}
			}
			fallback, err = e.Hooks.PreGrantLedgerAbsent(ctx, config, s.Record, attempt, recorded)
			if err != nil {
				return nil, err
			}
			if fallback {
				grant = "absent"
			}
		}
		if grant != "absent" {
			return nil, nativeRefusal("INVALID_REQUEST", "Codex attempt has a present or unknown memory grant")
		}
		proof, err := e.evidence(ctx, EvidenceCodexRefusal, s, attempt, recorded, nil, r.Records, nativeText(attempt.Get("native_turn_id")))
		if err != nil {
			return nil, err
		}
		if r.CheckOnly {
			out := nativeMap("status", "eligible", "attempt_id", r.AttemptID, "grant", "absent", "native_record", proof)
			if fallback {
				out.Set("grant_evidence", value.Text("pre_grant_refusal_without_ledger"))
			}
			return out, nil
		}
		out := nativeClone(recorded)
		out.Set("settlement", value.Text("turn_ended"))
		out.Set("grant", value.Text("absent"))
		out.Set("recovery_origin", value.Text("codex_native_records"))
		out.Set("native_record", proof)
		if fallback {
			out.Set("grant_evidence", value.Text("pre_grant_refusal_without_ledger"))
		}
		return s.Settle(ctx, "wake_refusal_diagnostic", out, nil, true)
	})
}
func (e *RecoveryEngine) AutoSettleCodexRefusedWake(ctx context.Context, s *NativeSession) (bool, error) {
	attempt, err := nativeObj(s.Attempt(), "inbox_attempt")
	if err != nil {
		return false, err
	}
	turn := nativeText(attempt.Get("native_turn_id"))
	id := attempt.Get("attempt_id")
	if !value.SameText(s.Config.Get("harness"), "codex") || value.Truth(s.Record.Get("ending")) || value.Truth(s.Record.Get("retired")) {
		return false, nil
	}
	alive, err := e.alive(nativeOr(s.Record.Get("process"), value.Mapping()))
	if err != nil {
		return false, err
	}
	if !alive || !value.Truth(id) || !recoveryUUID.MatchString(turn) {
		return false, nil
	}
	recorded, err := nativeObj(s.Record.Get("wake_refusal_diagnostic"), "wake_refusal_diagnostic")
	if err != nil {
		return false, err
	}
	if !value.SameText(recorded.Get("phase"), "bridge_after_claim") || !value.SameText(recorded.Get("origin"), "hook") || !value.SameText(recorded.Get("settlement"), "held") || !nativeEqual(recorded.Get("attempt_id"), id) || !value.SameText(recorded.Get("native_turn_id"), turn) {
		return false, nil
	}
	delivery, err := nativeObj(attempt.Get("delivery"), "delivery")
	if err != nil {
		return false, err
	}
	if !nativeEqual(recorded.Get("delivery_id"), delivery.Get("delivery_id")) {
		return false, nil
	}
	ready, err := e.RefusedWakeUnprocessed(s, turn)
	if err != nil {
		return false, err
	}
	if !ready {
		return false, nil
	}
	grant, err := e.grant(ctx, s, attempt)
	if err != nil {
		return false, err
	}
	if grant != "absent" {
		return false, nil
	}
	locators, err := e.evidence(ctx, EvidenceCodexLocators, s, attempt, nil, nil, nil, turn)
	if err != nil {
		if nativeCaught(err) {
			return false, nil
		}
		return false, err
	}
	if !value.Truth(locators) {
		return false, nil
	}
	proof, err := e.evidence(ctx, EvidenceCodexRefusal, s, attempt, recorded, nil, locators, turn)
	if err != nil {
		if nativeCaught(err) {
			return false, nil
		}
		return false, err
	}
	out := nativeClone(recorded)
	out.Set("settlement", value.Text("turn_ended"))
	out.Set("grant", value.Text("absent"))
	out.Set("recovery_origin", value.Text("codex_native_records"))
	out.Set("native_record", proof)
	_, err = s.Settle(ctx, "wake_refusal_diagnostic", out, nil, true)
	return err == nil, err
}
func (e *RecoveryEngine) RecoverCodexEndedTurn(ctx context.Context, config *value.Value, r RecoveryRequest) (*value.Value, error) {
	return e.withSession(config, r.NativeSessionID, "", func(s *NativeSession) (*value.Value, error) {
		held := nativeRefusal("INVALID_REQUEST", "held state is not this unsettled Codex attempt")
		attempt, err := nativeObj(s.Attempt(), "inbox_attempt")
		if err != nil {
			return nil, err
		}
		turn := nativeText(attempt.Get("native_turn_id"))
		if !value.SameText(config.Get("harness"), "codex") {
			return nil, held
		}
		_, ended, err := recoveryIdentity(s, r.NativeSessionID)
		if err != nil {
			return nil, err
		}
		if ended {
			return nil, held
		}
		alive, err := e.alive(s.Record.Get("process"))
		if err != nil {
			return nil, err
		}
		if !alive || !value.SameText(attempt.Get("attempt_id"), r.AttemptID) || !recoveryUUID.MatchString(turn) {
			return nil, held
		}
		intent, err := nativeObj(s.Intent(), "inbox_intent")
		if err != nil {
			return nil, err
		}
		if !value.SameText(intent.Get("request_id"), r.AttemptID) || !value.SameText(intent.Get("native_turn_id"), turn) {
			return nil, held
		}
		ref, err := s.sessionRef(s.Record.Get("agent"))
		if err != nil {
			return nil, err
		}
		if !nativeEqual(attempt.Get("session"), ref) || value.Truth(attempt.Get("finished_at")) || value.Truth(attempt.Get("cancel")) || value.Truth(s.Record.Get("inbox_close")) || value.Truth(s.Record.Get("inbox_turn_ended")) {
			return nil, held
		}
		if e.Hooks.ProbeCodex == nil {
			return nil, &value.ProfileUnavailable{}
		}
		health, err := e.Hooks.ProbeCodex(ctx, config, s.Record, true)
		if err != nil {
			return nil, err
		}
		health, err = nativeObj(health, "native health")
		if err != nil {
			return nil, err
		}
		status := nativeText(health.Get("status_type"))
		if !nativeContains([]string{"systemError", "idle"}, status) {
			if status == "" {
				status = "unavailable"
			}
			return nil, nativeRefusal("INVALID_REQUEST", "native Codex thread is not proven inactive ("+status+")")
		}
		proof, err := e.evidence(ctx, EvidenceCodexEnded, s, attempt, nil, nil, r.Records, turn)
		if err != nil {
			return nil, err
		}
		proof.Set("thread_status", health.Get("status_type"))
		if r.CheckOnly {
			return nativeMap("status", "eligible", "attempt_id", r.AttemptID, "native_record", proof), nil
		}
		at, err := e.now()
		if err != nil {
			return nil, err
		}
		proof.Set("attempt_id", value.Text(r.AttemptID))
		proof.Set("at", at)
		proof.Set("settlement", value.Text("pending"))
		out, err := s.Settle(ctx, "codex_ended_turn", proof, nil, true)
		if err != nil {
			return nil, err
		}
		out = nativeClone(out)
		out.Set("recovery_origin", value.Text("codex_ended_turn"))
		return out, nil
	})
}
func (e *RecoveryEngine) CheckCodexNativeHealth(ctx context.Context, config *value.Value, id string) (*value.Value, error) {
	return e.withSession(config, id, "INVALID_REQUEST", func(s *NativeSession) (*value.Value, error) {
		if err := s.mapping(); err != nil {
			return nil, err
		}
		if !value.SameText(config.Get("harness"), "codex") {
			return nil, nativeRefusal("INVALID_REQUEST", "state is not an active Codex session for this native ID")
		}
		agent, err := nativeDefaultObj(s.Record.Get("agent"), "agent")
		if err != nil {
			return nil, err
		}
		if !value.SameText(agent.Get("native_session_id"), id) || value.Truth(s.Record.Get("retired")) {
			return nil, nativeRefusal("INVALID_REQUEST", "state is not an active Codex session for this native ID")
		}
		if e.Hooks.ProbeCodex == nil {
			return nil, &value.ProfileUnavailable{}
		}
		health, err := e.Hooks.ProbeCodex(ctx, config, s.Record, true)
		if err != nil {
			return nil, err
		}
		return health, s.Save()
	})
}

func (e *RecoveryEngine) codexStoreDelivery(ctx context.Context, s *NativeSession, deliveryID string, eventID *value.Value) (*value.Value, *value.Value, error) {
	ref, err := s.sessionRef(s.Record.Get("agent"))
	if err != nil {
		return nil, nil, err
	}
	var readyID *value.Value
	ready, err := s.call(ctx, "session-inbox-ready", ref, NativeCallOptions{Timeout: time.Second})
	if err != nil && nativeCode(err) == "" {
		return nil, nil, err
	}
	if err == nil && ready != nil && ready.Kind == value.Object {
		readyID = ready.Get("delivery_id")
		if !value.Truth(eventID) && value.SameText(readyID, deliveryID) && ready.Get("event_id") != nil && ready.Get("event_id").Kind == value.String {
			eventID = ready.Get("event_id")
		}
	}
	if eventID == nil || eventID.Kind != value.String || !value.Truth(eventID) {
		return nil, readyID, nil
	}
	inspected, err := s.call(ctx, "event-inspect", nativeMap("event_id", eventID, "limit", 100), NativeCallOptions{Session: ref, Timeout: time.Second})
	if err != nil {
		if nativeCode(err) != "" {
			return nil, readyID, nil
		}
		return nil, nil, err
	}
	if inspected == nil || inspected.Kind != value.Object {
		return nil, readyID, nil
	}
	event, err := nativeObj(inspected.Get("event"), "event")
	if err != nil {
		return nil, nil, err
	}
	repo := event.Get("repo")
	if !nativeNone(repo) && !nativeEqual(repo, s.Config.Get("repo")) {
		return nil, readyID, nil
	}
	deliveries := inspected.Get("deliveries")
	if !value.Truth(deliveries) {
		return nil, readyID, nil
	}
	if deliveries.Kind != value.Array {
		if deliveries.Kind == value.Object || deliveries.Kind == value.String {
			return nil, readyID, nil
		}
		return nil, nil, &journal.TypeError{Field: "deliveries"}
	}
	var matches []*value.Value
	for _, d := range deliveries.Items {
		if d != nil && d.Kind == value.Object && value.SameText(d.Get("delivery_id"), deliveryID) {
			matches = append(matches, d)
		}
	}
	if len(matches) != 1 {
		return nil, readyID, nil
	}
	consumer := matches[0].Get("consumer")
	if !nativeNone(consumer) && !nativeEqual(consumer, s.Record.Get("agent").Get("inbox")) {
		return nil, readyID, nil
	}
	return matches[0], readyID, nil
}
func (e *RecoveryEngine) ReconcileCodexQueueWake(ctx context.Context, config *value.Value, r RecoveryRequest) (*value.Value, error) {
	if r.Outcome != nil && !nativeContains([]string{"admitted_active", "completed", "cancelled", "never_admitted", "unknown"}, nativeText(r.Outcome)) {
		return nil, nativeRefusal("INVALID_REQUEST", "unsupported Codex queue reconciliation outcome")
	}
	return e.withSession(config, r.NativeSessionID, "INVALID_REQUEST", func(s *NativeSession) (*value.Value, error) {
		if err := s.mapping(); err != nil {
			return nil, err
		}
		if !value.SameText(config.Get("harness"), "codex") {
			return nil, nativeRefusal("INVALID_REQUEST", "state is not an active Codex session for this native ID")
		}
		agent, err := nativeObj(s.Record.Get("agent"), "agent")
		if err != nil {
			return nil, err
		}
		if !value.SameText(agent.Get("native_session_id"), r.NativeSessionID) || value.Truth(s.Record.Get("ending")) || value.Truth(s.Record.Get("retired")) {
			return nil, nativeRefusal("INVALID_REQUEST", "state is not an active Codex session for this native ID")
		}
		wake, err := nativeObj(s.Record.Get("idle_wake"), "idle_wake")
		if err != nil {
			return nil, err
		}
		attempt, err := nativeObj(s.Attempt(), "inbox_attempt")
		if err != nil {
			return nil, err
		}
		intent, err := nativeObj(s.Intent(), "inbox_intent")
		if err != nil {
			return nil, err
		}
		ref, err := s.sessionRef(agent)
		if err != nil {
			return nil, err
		}
		delivery, err := nativeObj(attempt.Get("delivery"), "delivery")
		if err != nil {
			return nil, err
		}
		wakeMatches := value.SameText(wake.Get("transport"), "codex-queue") && nativeEqual(wake.Get("session"), ref) && value.SameText(wake.Get("native_id"), r.NativeSessionID) && value.SameText(wake.Get("delivery_id"), r.DeliveryID) && value.SameText(wake.Get("queued_submission_id"), r.QueuedSubmissionID)
		attemptMatches := value.SameText(nativeOr(delivery.Get("delivery_id"), intent.Get("delivery_id")), r.DeliveryID) && nativeEqual(nativeOr(attempt.Get("session"), intent.Get("session")), ref)
		if !wakeMatches && !attemptMatches {
			return nil, nativeRefusal("INVALID_REQUEST", "retained Codex wake does not match delivery and queued submission")
		}
		if e.Hooks.ProbeCodex == nil {
			return nil, &value.ProfileUnavailable{}
		}
		health, err := e.Hooks.ProbeCodex(ctx, config, nativeClone(s.Record), true)
		if err != nil {
			return nil, err
		}
		var rollout *value.Value
		if value.Truth(r.Records) {
			target := wake
			if !wakeMatches {
				target = nativeMap("delivery_id", r.DeliveryID, "queued_submission_id", r.QueuedSubmissionID, "attempted_at", 0, "session", ref)
			}
			rollout, err = e.evidence(ctx, EvidenceCodexQueue, s, nil, nil, target, r.Records, "")
			if err != nil {
				return nil, err
			}
		}
		store, ready, err := e.codexStoreDelivery(ctx, s, r.DeliveryID, wake.Get("event_id"))
		if err != nil {
			return nil, err
		}
		grant := "absent"
		if value.Truth(config.Get("inbox_recall_binding")) {
			synthetic := nativeMap("attempt_id", nativeOr(attempt.Get("attempt_id"), nativeOr(wake.Get("request_id"), value.Text(""))), "delivery", nativeMap("delivery_id", r.DeliveryID), "native_turn_id", nativeOr(attempt.Get("native_turn_id"), value.Text("")))
			grant, err = e.grant(ctx, s, synthetic)
			if err != nil {
				return nil, err
			}
		}
		resolved := "unknown"
		reason := "retained queue item is absent from thread/queue/list without non-admission proof"
		next := "supply pinned rollout boundary records or verify store completion before clearing the retained wake"
		healthObj, err := nativeObj(health, "native health")
		if err != nil {
			return nil, err
		}
		rolloutObj, err := nativeObj(rollout, "rollout")
		if err != nil {
			return nil, err
		}
		storeObj, err := nativeObj(store, "store delivery")
		if err != nil {
			return nil, err
		}
		if attemptMatches || value.Truth(healthObj.Get("queued_present")) || value.SameText(rolloutObj.Get("outcome"), "admitted_active") {
			resolved = "admitted_active"
			reason = "pinned rollout records show an active queue-triggered turn"
			if attemptMatches {
				reason = "delivery is already admitted in an active inbox attempt"
			} else if value.Truth(healthObj.Get("queued_present")) {
				reason = "submission remains present in thread/queue/list"
			}
			next = "allow the active or queued turn to complete and settle normally; do not replay"
		} else if nativeContains([]string{"handled", "completed"}, nativeText(storeObj.Get("state"))) || value.SameText(rolloutObj.Get("outcome"), "completed") {
			resolved = "completed"
			reason = "delivery was already completed; stale retained wake marker can be cleared"
			next = "subsequent inbox traffic is released without replaying completed work"
		} else if nativeContains([]string{"failed", "cancelled", "ignored", "expired"}, nativeText(storeObj.Get("state"))) || value.Truth(storeObj.Get("cancel")) || value.SameText(rolloutObj.Get("outcome"), "cancelled") {
			resolved = "cancelled"
			reason = "delivery or queue turn was cancelled/failed; stale retained wake marker can be cleared"
			next = "subsequent inbox traffic is released without replaying cancelled work"
		} else if value.SameText(rolloutObj.Get("outcome"), "never_admitted") {
			attempts := storeObj.Get("attempts")
			safe := value.Truth(health) && value.Truth(healthObj.Get("process_alive")) && value.SameText(healthObj.Get("status_type"), "idle") && healthObj.Get("queued_present") != nil && healthObj.Get("queued_present").Kind == value.Bool && !healthObj.Get("queued_present").Boolean && grant == "absent" && !nativeNone(store) && value.SameText(storeObj.Get("state"), "pending") && attempts != nil && attempts.Kind == value.Int && attempts.Integer == "0" && value.SameText(ready, r.DeliveryID)
			for _, key := range []string{"inbox_intent", "inbox_attempt", "inbox_result_policy", "inbox_close", "active_prompt"} {
				safe = safe && !value.Truth(s.Record.Get(key))
			}
			if safe {
				resolved = "never_admitted"
				reason = "proven never admitted across the queue attempt window with zero store attempts and absent grant"
				next = "subsequent inbox traffic is released for ordinary delivery"
			} else if value.SameText(r.Outcome, "never_admitted") {
				return nil, nativeRefusal("INVALID_REQUEST", "Codex wake cannot be proven never_admitted")
			}
		}
		if resolved == "unknown" {
			if value.Truth(health) && value.SameText(healthObj.Get("status_type"), "active") {
				reason = "retained queue item is absent from thread/queue/list while native owner work is active; missing queue membership does not prove non-admission"
				next = "allow active owner work to reach a turn boundary and inspect rollout/store outcome before reconciling; do not interrupt or replay"
			} else if !value.Truth(health) || !value.Truth(healthObj.Get("process_alive")) || value.SameText(healthObj.Get("status_type"), "unavailable") {
				reason = "native process or queue endpoint is unavailable; historical queue admission remains unknown"
				next = "inspect store delivery state or hand off under a new identity without replaying uncertain work"
			}
		}
		if r.Outcome != nil && !value.SameText(r.Outcome, resolved) {
			return nil, nativeRefusal("INVALID_REQUEST", "Codex wake outcome is "+resolved+", not "+nativeText(r.Outcome))
		}
		at, err := e.now()
		if err != nil {
			return nil, err
		}
		status := "reconciled"
		if r.CheckOnly {
			status = "eligible"
		} else if resolved == "unknown" {
			status = "unresolved"
		}
		result := nativeMap("schema", "cairn.codex-queue-reconciliation/1", "status", status, "outcome", resolved, "delivery_id", r.DeliveryID, "queued_submission_id", r.QueuedSubmissionID, "grant", grant, "native_health", health, "reason", reason, "next_action", next, "at", at)
		if rollout != nil {
			result.Set("native_record", rollout)
		}
		if resolved == "unknown" {
			result.Set("reconciliation_required", value.Text("queue_item_missing"))
		}
		if r.CheckOnly {
			return result, nil
		}
		if health == nil {
			health = &value.Value{Kind: value.Null}
		}
		s.Record.Set("native_health", health)
		switch resolved {
		case "never_admitted", "completed", "cancelled":
			nativePop(s.Record, "idle_wake", "wake_refusal", "wake_reconciliation")
			s.Record.Set("wake_reconciled", result)
		case "admitted_active":
			if attemptMatches {
				nativePop(s.Record, "idle_wake", "wake_refusal")
			} else if value.Truth(health) && value.Truth(healthObj.Get("queued_present")) && wakeMatches {
				wake.Set("status", value.Text("queued"))
				nativePop(wake, "reconciliation_required")
				nativePop(s.Record, "wake_refusal")
			}
			s.Record.Set("wake_reconciliation", result)
		default:
			if wakeMatches {
				wake.Set("status", value.Text("refused"))
				wake.Set("reconciliation_required", value.Text("queue_item_missing"))
			}
			s.Record.Set("wake_reconciliation", result)
		}
		return result, s.Save()
	})
}

var diagnosticClasses = []string{"BlockingIOError", "PermissionError", "FileNotFoundError", "OSError", "ValueError", "KeyError", "TypeError", "JSONDecodeError", "CoordinationError", "HookError", "BudgetRefused", "RequiredContextRefused", "ContextRefused", "OtherError"}
var causeCodes = []string{"TIMEOUT", "API_UNAVAILABLE", "API_CONNECTION_FAILED", "AUTHORITY_DENIED", "NOT_FOUND", "PAYLOAD_UNAVAILABLE", "OPEN_CONFLICT", "POLICY_UNENFORCEABLE", "VERSION_CONFLICT"}
var causeReasons = []string{"NONZERO_EXIT", "INVALID_JSON", "OUTPUT_LIMIT", "START_FAILED", "NOT_CONFIRMED", "SEARCH_STATUS", "NOT_HOSTED"}

func (e *RecoveryEngine) HeldAttemptRefusalObservation(ctx context.Context, s *NativeSession, now *value.Value) (*value.Value, error) {
	if s.Record == nil || s.Record.Kind != value.Object || value.Truth(s.Record.Get("ending")) || value.Truth(s.Record.Get("retired")) {
		return nil, nil
	}
	agent, err := nativeObj(s.Record.Get("agent"), "agent")
	if err != nil {
		return nil, err
	}
	process, err := nativeObj(s.Record.Get("process"), "process")
	if err != nil {
		return nil, err
	}
	intent, err := nativeObj(s.Intent(), "inbox_intent")
	if err != nil {
		return nil, err
	}
	attempt, err := nativeObj(s.Attempt(), "inbox_attempt")
	if err != nil {
		return nil, err
	}
	recorded, err := nativeObj(s.Record.Get("wake_refusal_diagnostic"), "wake_refusal_diagnostic")
	if err != nil {
		return nil, err
	}
	delivery, err := nativeObj(attempt.Get("delivery"), "delivery")
	if err != nil {
		return nil, err
	}
	id, deliveryID, turn := attempt.Get("attempt_id"), delivery.Get("delivery_id"), attempt.Get("native_turn_id")
	if !value.Truth(agent.Get("agent_id")) || !value.Truth(agent.Get("execution_id")) {
		return nil, nil
	}
	ref, err := s.sessionRef(agent)
	if err != nil {
		return nil, err
	}
	alive, err := e.alive(process)
	if err != nil {
		return nil, err
	}
	if !alive || !value.Truth(id) || !value.Truth(deliveryID) || !value.Truth(turn) || !nativeEqual(intent.Get("request_id"), id) || !nativeEqual(intent.Get("session"), ref) || !nativeEqual(attempt.Get("session"), ref) || !nativeEqual(intent.Get("native_turn_id"), turn) || !value.SameText(recorded.Get("phase"), "bridge_after_claim") || !nativeContains([]string{"held", SettlementPending}, nativeText(recorded.Get("settlement"))) || !nativeEqual(recorded.Get("attempt_id"), id) || !nativeEqual(recorded.Get("delivery_id"), deliveryID) || !nativeEqual(recorded.Get("native_turn_id"), turn) {
		return nil, nil
	}
	errorClass := nativeText(recorded.Get("error_class"))
	if !nativeContains(diagnosticClasses, errorClass) {
		errorClass = "OtherError"
	}
	cause := recorded.Get("cause_class")
	if !nativeNone(cause) && !nativeContains(diagnosticClasses, nativeText(cause)) {
		cause = value.Text("OtherError")
	}
	grant := nativeText(recorded.Get("grant"))
	if !nativeContains([]string{"absent", "present", "unknown", "not_checked"}, grant) {
		grant = "unknown"
	}
	grantKind := recorded.Get("grant_kind")
	if !nativeNone(grantKind) && !nativeContains([]string{"inbox_delivery", "ordinary_context", "unadmitted_wake", "mixed", "unknown"}, nativeText(grantKind)) {
		grantKind = value.Text("unknown")
	}
	turnState := "unknown"
	harness := nativeText(s.Config.Get("harness"))
	if harness == "claude" {
		active, err := nativeObj(s.Record.Get("active_prompt"), "active_prompt")
		if err != nil {
			return nil, err
		}
		turnState = "ended"
		if nativeEqual(active.Get("id"), turn) {
			turnState = "active"
		}
	} else if harness == "codex" {
		turnState = "active_or_unproven"
		locators, err := e.evidence(ctx, EvidenceCodexLocators, s, attempt, nil, nil, nil, nativeText(turn))
		if err != nil {
			var profile *value.ProfileUnavailable
			if errors.As(err, &profile) {
				return nil, err
			}
		} else if value.Truth(locators) {
			turnState = "ended_proven"
		}
	}
	evidenceState := "held_unsettled"
	switch {
	case value.SameText(recorded.Get("settlement"), SettlementPending):
		evidenceState = "reconcile_pending"
	case grant == "present" && value.SameText(grantKind, "ordinary_context"):
		evidenceState = "ordinary_grant_present_manual_review"
	case grant == "present":
		evidenceState = "grant_present_manual_review"
	case harness == "codex":
		evidenceState = "awaiting_codex_turn_end"
		if turnState == "ended_proven" {
			evidenceState = "codex_rollout_ready"
		}
	case harness == "claude":
		evidenceState = "eligible_for_recorded_recovery"
		if turnState == "active" {
			evidenceState = "claude_active_prompt"
		}
	}
	if now == nil {
		now, err = e.now()
		if err != nil {
			return nil, err
		}
	}
	var observed, age *value.Value
	at := recorded.Get("at")
	if at != nil && (at.Kind == value.Int || at.Kind == value.Float || at.Kind == value.Bool) {
		if e.Hooks.ObserveTime == nil {
			return nil, &value.ProfileUnavailable{}
		}
		n, err := nativeSeconds(now)
		if err != nil {
			return nil, err
		}
		observed, age, err = e.Hooks.ObserveTime(at, n)
		if err != nil {
			return nil, err
		}
	}
	activity := "unknown"
	metadata, err := nativeObj(agent.Get("metadata"), "metadata")
	if err != nil {
		return nil, err
	}
	if nativeContains([]string{"idle", "busy"}, nativeText(metadata.Get("state"))) {
		activity = nativeText(metadata.Get("state"))
	}
	origin := nativeText(recorded.Get("origin"))
	if !nativeContains([]string{"hook", "operator_recorded", "native_record"}, origin) {
		origin = "unknown"
	}
	hold := "leased"
	if value.Truth(attempt.Get("lease_lapsed")) {
		hold = "lapsed"
	}
	obs := nativeMap("schema", "cairn.held-attempt-refusal/1", "authority", "diagnostic_only", "store_hold", hold, "session", ref, "native_session_id", agent.Get("native_session_id"), "attempt_id", id, "delivery_id", deliveryID, "native_turn_id", turn, "phase", "bridge_after_claim", "origin", origin, "settlement", recorded.Get("settlement"), "error_class", errorClass, "cause_class", cause, "grant", grant, "grant_kind", grantKind, "session_activity", activity, "native_turn_state", turnState, "freshness", "current_attempt", "evidence_state", evidenceState, "observed_at", observed, "age_seconds", age)
	for _, entry := range []struct {
		key     string
		allowed []string
	}{{"cause_code", causeCodes}, {"cause_reason", causeReasons}} {
		if nativeContains(entry.allowed, nativeText(recorded.Get(entry.key))) {
			obs.Set(entry.key, recorded.Get(entry.key))
		}
	}
	sanitizeRecallMeta(recorded, obs)
	copyInboxRefusalDetails(recorded, obs)
	return obs, nil
}
func nativeSeconds(v *value.Value) (float64, error) {
	if v == nil {
		return 0, &journal.TypeError{Field: "timestamp"}
	}
	switch v.Kind {
	case value.Float:
		return v.Number, nil
	case value.Int:
		n, _, err := big.ParseFloat(v.Integer, 10, 64, big.ToNearestEven)
		if err != nil {
			return 0, &journal.ValueError{Message: err.Error()}
		}
		f, _ := n.Float64()
		return f, nil
	case value.Bool:
		if v.Boolean {
			return 1, nil
		}
		return 0, nil
	}
	return 0, &journal.TypeError{Field: "timestamp"}
}
func sanitizeRecallMeta(raw, out *value.Value) {
	for _, entry := range []struct {
		key     string
		allowed []string
	}{{"recall_phase", []string{"required_search", "task_source", "optional_search"}}, {"recall_operation", []string{"search", "hosted", "history"}}} {
		if nativeContains(entry.allowed, nativeText(raw.Get(entry.key))) {
			out.Set(entry.key, raw.Get(entry.key))
		}
	}
	for _, key := range []string{"recall_attempts", "recall_max_attempts", "deadline_remaining_ms"} {
		v := raw.Get(key)
		if v == nil || v.Kind != value.Int {
			continue
		}
		n, ok := new(big.Int).SetString(v.Integer, 10)
		low, high := int64(1), int64(16)
		if key == "deadline_remaining_ms" {
			low, high = 0, 60000
		}
		if ok && n.IsInt64() && n.Int64() >= low && n.Int64() <= high {
			out.Set(key, v)
		}
	}
}
func (e *RecoveryEngine) InspectHeldRefusal(ctx context.Context, config *value.Value, r RecoveryRequest) (*value.Value, error) {
	if e.Host == nil || e.Host.Locate == nil || e.Host.Lock == nil || e.Host.Exists == nil || e.Host.Load == nil {
		return nil, &value.ProfileUnavailable{}
	}
	path, err := e.Host.Locate(config, r.NativeSessionID)
	if err != nil {
		return nil, err
	}
	lock, err := e.Host.Lock(path)
	if err != nil {
		return nil, err
	}
	defer lock.Close()
	exists, err := e.Host.Exists(path)
	if err != nil {
		return nil, err
	}
	if !exists {
		return nil, nativeRefusal("NOT_FOUND", "no coordination state for this native session")
	}
	record, err := e.Host.Load(path)
	if err != nil {
		return nil, err
	}
	s := &NativeSession{config, path, record, e.Host}
	obs, err := e.HeldAttemptRefusalObservation(ctx, s, nil)
	if err != nil {
		return nil, err
	}
	if obs == nil || r.AttemptID != "" && !value.SameText(obs.Get("attempt_id"), r.AttemptID) {
		return nil, nativeRefusal("NOT_FOUND", "no active held-attempt refusal diagnostic matches this session")
	}
	return obs, nil
}
func (e *RecoveryEngine) VerifyTurnRecovery(ctx context.Context, config *value.Value, harness, mode string, r RecoveryRequest) (*value.Value, error) {
	switch harness {
	case "opencode", "hermes":
		return nil, nativeRefusal("INVALID_REQUEST", "unsupported "+map[string]string{"opencode": "OpenCode", "hermes": "Hermes"}[harness]+" offline transcript recovery mode: "+mode)
	case "codex":
		switch mode {
		case "refused_wake", "codex_refused_wake", "recover-codex-refused-wake":
			return e.RecoverCodexRefusedWake(ctx, config, r)
		case "ended_turn", "codex_ended_turn", "recover-codex-ended-turn":
			return e.RecoverCodexEndedTurn(ctx, config, r)
		case "refused_wake_proof", "codex_refusal_proof", "ended_turn_proof", "codex_ended_turn_proof", "queue_rollout_proof", "codex_queue_rollout_proof", "find_refusal_locators", "find_codex_refusal_locators", "latest_turn_end", "latest_codex_turn_end", "auto_settle_refused_wake", "auto_settle_codex_refused_wake":
			return e.dispatchEvidence(ctx, config, harness, mode, r)
		}
		return nil, nativeRefusal("INVALID_REQUEST", "unsupported Codex recovery mode: "+mode)
	case "claude":
		switch mode {
		case "refused_wake", "recover-refused-wake":
			return e.RecoverRefusedWake(ctx, config, r)
		case "native_refused_wake", "recover-native-refused-wake":
			return e.RecoverNativeRefusedWake(ctx, config, r)
		case "daemon_start_claim", "recover-daemon-start-claim":
			return e.RecoverDaemonStartClaim(ctx, config, r)
		case "native_refusal_proof", "refused_wake_proof", "daemon_start_claim_evidence", "daemon_start_claim_proof":
			return e.dispatchEvidence(ctx, config, harness, mode, r)
		}
		return nil, nativeRefusal("INVALID_REQUEST", "unsupported Claude recovery mode: "+mode)
	}
	return nil, nativeRefusal("INVALID_REQUEST", "unsupported recovery harness: "+harness)
}
func (e *RecoveryEngine) dispatchEvidence(ctx context.Context, config *value.Value, harness, mode string, r RecoveryRequest) (*value.Value, error) {
	s := r.Session
	if s == nil {
		return nil, &journal.TypeError{Field: "recovery state"}
	}
	return func(s *NativeSession) (*value.Value, error) {
		if mode == "auto_settle_refused_wake" || mode == "auto_settle_codex_refused_wake" {
			ok, err := e.AutoSettleCodexRefusedWake(ctx, s)
			return value.Boolean(ok), err
		}
		kind := EvidenceCodexRefusal
		switch mode {
		case "native_refusal_proof", "refused_wake_proof":
			if harness == "claude" {
				kind = EvidenceNativeRefusal
			}
		case "daemon_start_claim_evidence", "daemon_start_claim_proof":
			kind = EvidenceDaemonClaim
		case "ended_turn_proof", "codex_ended_turn_proof":
			kind = EvidenceCodexEnded
		case "queue_rollout_proof", "codex_queue_rollout_proof":
			kind = EvidenceCodexQueue
		case "find_refusal_locators", "find_codex_refusal_locators":
			kind = EvidenceCodexLocators
		case "latest_turn_end", "latest_codex_turn_end":
			kind = EvidenceCodexLatest
		}
		return e.evidence(ctx, kind, s, s.Attempt(), s.Record.Get("wake_refusal_diagnostic"), s.Record.Get("idle_wake"), r.Records, r.NativeTurnID)
	}(s)
}

func copyInboxRefusalDetails(raw, out *value.Value) {
	reasons := map[string][]string{
		"bridge": {}, "loader": {"member_shape", "member_owner", "member_changed", "descriptor", "member_hash", "role", "entrypoint_path", "entrypoint_hash"},
		"binding":         {"manifest_owner", "config_owner", "config_hash", "schema", "disabled", "coordination_count", "config_homes", "profile", "engine_contract"},
		"identity":        {"project", "native_session", "native_turn", "claude_owner", "claude_envelope", "wake_session", "attempt_live", "attempt_session", "attempt_owner", "wake_delivery"},
		"inbox_ledger":    {"marker", "missing", "shape", "required_status", "refresh_counter", "pending_charge", "active_grant", "folded", "delivery_identity", "consumed"},
		"required_search": {"hosted_destination"}, "task_source": {"source_identity", "source_version"}, "turn_budget": {"retired_turn"}, "command_context": {"source_identity", "compact_fallback"}, "required_render": {"required_context"}, "optional_search": {"hosted_destination"}, "optional_render": {"task_context"}, "final_render": {"output_budget", "allowance_budget"}, "lease_revalidation": {}, "grant_persistence": {},
	}
	stage := nativeText(raw.Get("refusal_stage"))
	allowed, ok := reasons[stage]
	if !ok {
		return
	}
	reason := nativeText(raw.Get("refusal_reason"))
	if reason != "unknown" && !nativeContains(allowed, reason) {
		reason = "unknown"
	}
	out.Set("refusal_stage", value.Text(stage))
	out.Set("refusal_reason", value.Text(reason))
	if !(stage == "required_render" && reason == "required_context" || stage == "optional_render" && reason == "task_context" || stage == "final_render" && (reason == "output_budget" || reason == "allowance_budget")) {
		return
	}
	framing := 1 + len(`{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":""}}`)
	for _, bound := range []struct {
		key     string
		maximum int64
	}{{"configured_budget_bytes", 9500}, {"budget_bytes", 9500}, {"prior_charge_bytes", int64(2*65536 + framing)}, {"native_allowance_bytes", 9500}} {
		v := raw.Get(bound.key)
		if v == nil || v.Kind != value.Int {
			continue
		}
		n, ok := new(big.Int).SetString(v.Integer, 10)
		if ok && n.IsInt64() && n.Sign() >= 0 && n.Int64() <= bound.maximum {
			out.Set(bound.key, v)
		}
	}
}
