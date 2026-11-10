package coordination

import (
	"context"
	"errors"
	"io"
	"math"
	"math/big"
	"strconv"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const NativeSessionSchema = "cairn.native-session/1"
const BatchLimit = 3
const LeaseSeconds = 90
const SettlementPending = "turn_ended_pending"

type FlushScope string

const (
	FlushAttempt FlushScope = "attempt"
	FlushAll     FlushScope = "all"
)

var AttemptKeys = []string{"inbox_intent", "inbox_attempt", "inbox_close", "inbox_completion", "inbox_response", "inbox_turn_ended", "inbox_result_policy", "inbox_ack_reminded", "inbox_ack_reminder_revision", "batch_advance_intent", "held_refusal_observation", "cancel_turn_end", "cancel_submission", "cancel_owner_join", "cancel_scan_report", "cancel_final_report", "tool_calls", "opencode_turn_since", "opencode_request_endpoint", "organic_root", "organic_advance_intent", "organic_claim_intent", "inbox_intent_kind"}
var BatchNextKeysCleared = []string{"inbox_close", "inbox_completion", "inbox_response", "inbox_ack_reminded", "inbox_ack_reminder_revision"}
var BatchAdvanceFinalRefusals = []string{"DELIVERY_ACTIVE", "INVALID_REQUEST", "STALE_SESSION", "NOT_FOUND", "AUTHORITY_DENIED", "IDEMPOTENCY_CONFLICT"}
var RetiredSessionCodes = []string{"STALE_SESSION", "NOT_FOUND"}

type JournalBoundary interface {
	Flush(context.Context, *value.Value, *value.Value, FlushScope) (bool, error)
	Committed(*value.Value, *value.Value) (bool, error)
	TurnEndDiagnostic(*value.Value, *value.Value) error
}
type NativeCallOptions struct {
	Session *value.Value
	Timeout time.Duration
}
type Host struct {
	Profile      *value.SourceReaderProfile
	Services     *ServiceBindings
	ReadConfig   func(string) (*value.Value, error)
	ReadContext  func(string) (*value.Value, error)
	WriteContext func(string, *value.Value) error
	Call         func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error)
	Persist      func(string, *value.Value) error
	Journal      JournalBoundary
	SessionRef   func(*value.Value) (*value.Value, error)
	NewID        func() string
	Log          func(string) error
	Locate       func(*value.Value, string) (string, error)
	Lock         func(string) (io.Closer, error)
	Exists       func(string) (bool, error)
	Load         func(string) (*value.Value, error)
	Recover      func(context.Context, *NativeSession) (*value.Value, error)
	Release      func(context.Context, *NativeSession, string, bool) (bool, error)
	Renew        func(context.Context, *NativeSession, *value.Value) (*value.Value, error)
	EndTurn      func(context.Context, *NativeSession, bool) (bool, error)
}
type NativeSession struct {
	Config *value.Value
	Path   string
	Record *value.Value
	Host   *Host
}

func (s *NativeSession) Attempt() *value.Value { return s.Record.Get("inbox_attempt") }
func (s *NativeSession) Intent() *value.Value  { return s.Record.Get("inbox_intent") }
func (s *NativeSession) Save() error {
	if s.Host == nil || s.Host.Persist == nil {
		return &value.ProfileUnavailable{}
	}
	return s.Host.Persist(s.Path, s.Record)
}
func (s *NativeSession) id() string {
	if s.Host.NewID != nil {
		return s.Host.NewID()
	}
	return uuid.NewString()
}
func (s *NativeSession) call(ctx context.Context, op string, body *value.Value, options NativeCallOptions) (*value.Value, error) {
	if s.Host == nil || s.Host.Call == nil {
		return nil, &value.ProfileUnavailable{}
	}
	return s.Host.Call(ctx, s.Config, op, body, options)
}
func (s *NativeSession) sessionRef(agent *value.Value) (*value.Value, error) {
	if s.Host.SessionRef != nil {
		return s.Host.SessionRef(agent)
	}
	a, e := nativeRequired(agent, "agent_id")
	if e != nil {
		return nil, e
	}
	x, e := nativeRequired(agent, "execution_id")
	if e != nil {
		return nil, e
	}
	return nativeMap("agent_id", a, "execution_id", x), nil
}
func (s *NativeSession) recoverHost(ctx context.Context) (*value.Value, error) {
	if s.Host.Recover != nil {
		return s.Host.Recover(ctx, s)
	}
	return s.Recover(ctx)
}
func (s *NativeSession) releaseHost(ctx context.Context, reason string, fenced bool) (bool, error) {
	if s.Host.Release != nil {
		return s.Host.Release(ctx, s, reason, fenced)
	}
	return s.Release(ctx, reason, fenced)
}
func (s *NativeSession) endHost(ctx context.Context, fenced bool) (bool, error) {
	if s.Host.EndTurn != nil {
		return s.Host.EndTurn(ctx, s, fenced)
	}
	return s.EndTurn(ctx, fenced)
}
func (s *NativeSession) renewHost(ctx context.Context, attempt *value.Value) (*value.Value, error) {
	if s.Host.Renew != nil {
		return s.Host.Renew(ctx, s, attempt)
	}
	return s.Renew(ctx, attempt)
}
func (s *NativeSession) mapping() error {
	if s.Record == nil || s.Record.Kind != value.Object {
		return &journal.NonObjectError{Field: "session"}
	}
	return nil
}
func nativeRefusal(code, message string) error {
	return &CoordinationError{Code: code, Message: message}
}
func nativeCode(err error) string {
	var c *CoordinationError
	if errors.As(err, &c) {
		return c.Code
	}
	var j *journal.Error
	if errors.As(err, &j) {
		return j.Code
	}
	return ""
}
func nativeContains(xs []string, s string) bool {
	for _, x := range xs {
		if x == s {
			return true
		}
	}
	return false
}
func nativeCaught(err error) bool {
	return nativeCode(err) != "" || journal.Classified(err) || sourceClass(err, "OSError", "ValueError", "JSONDecodeError", "UnicodeDecodeError", "UnicodeEncodeError", "KeyError", "TypeError")
}
func sourceClass(err error, classes ...string) bool {
	class := value.ErrorClass(err)
	for _, c := range classes {
		if class == c {
			return true
		}
	}
	return false
}
func nativeNone(v *value.Value) bool { return v == nil || v.Kind == value.Null }
func nativeText(v *value.Value) string {
	if v == nil || v.Kind != value.String {
		return ""
	}
	return string(v.String)
}
func nativeObj(v *value.Value, field string) (*value.Value, error) {
	if !value.Truth(v) {
		return value.Mapping(), nil
	}
	if v.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: field}
	}
	return v, nil
}

// nativeDefaultObj models get(key, {}), whose default applies only to absence.
func nativeDefaultObj(v *value.Value, field string) (*value.Value, error) {
	if v == nil {
		return value.Mapping(), nil
	}
	if v.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: field}
	}
	return v, nil
}
func nativeRequired(v *value.Value, key string) (*value.Value, error) {
	if v == nil || v.Kind != value.Object {
		return nil, &journal.TypeError{Field: key}
	}
	x := v.Get(key)
	if x == nil {
		return nil, &journal.KeyError{Key: key}
	}
	return x, nil
}
func nativeMap(kv ...any) *value.Value {
	m := value.Mapping()
	for i := 0; i < len(kv); i += 2 {
		v := kv[i+1]
		var item *value.Value
		switch x := v.(type) {
		case *value.Value:
			item = x
		case string:
			item = value.Text(x)
		case int:
			item = value.IntegerText(strconv.Itoa(x))
		case bool:
			item = value.Boolean(x)
		case nil:
			item = &value.Value{Kind: value.Null}
		default:
			panic("unsupported native mapping template type")
		}
		if item == nil {
			item = &value.Value{Kind: value.Null}
		}
		m.Set(kv[i].(string), item)
	}
	return m
}
func nativeClone(v *value.Value) *value.Value {
	if v == nil {
		return nil
	}
	out := *v
	out.String = append([]rune(nil), v.String...)
	out.Items = nil
	out.Members = nil
	for _, item := range v.Items {
		out.Items = append(out.Items, nativeClone(item))
	}
	for _, m := range v.Members {
		out.Members = append(out.Members, value.Member{Key: append([]rune(nil), m.Key...), Value: nativeClone(m.Value)})
	}
	return &out
}
func nativePop(v *value.Value, keys ...string) {
	for _, key := range keys {
		for i, m := range v.Members {
			if value.SameText(&value.Value{Kind: value.String, String: m.Key}, key) {
				v.Members = append(v.Members[:i], v.Members[i+1:]...)
				break
			}
		}
	}
}
func nativeEqual(a, b *value.Value) bool {
	if nativeNone(a) || nativeNone(b) {
		return nativeNone(a) && nativeNone(b)
	}
	number := func(v *value.Value) (*big.Rat, bool) {
		switch v.Kind {
		case value.Int:
			n, ok := new(big.Int).SetString(v.Integer, 10)
			if ok {
				return new(big.Rat).SetInt(n), true
			}
		case value.Bool:
			if v.Boolean {
				return big.NewRat(1, 1), true
			}
			return big.NewRat(0, 1), true
		case value.Float:
			r := new(big.Rat).SetFloat64(v.Number)
			return r, r != nil
		}
		return nil, false
	}
	if a.Kind == value.Float && b.Kind == value.Float {
		return a.Number == b.Number || math.IsNaN(a.Number) && math.IsNaN(b.Number)
	}
	if x, ok := number(a); ok {
		y, ok := number(b)
		return ok && x.Cmp(y) == 0
	}
	if a.Kind != b.Kind {
		return false
	}
	switch a.Kind {
	case value.String:
		if len(a.String) != len(b.String) {
			return false
		}
		for i, r := range a.String {
			if r != b.String[i] {
				return false
			}
		}
		return true
	case value.Array:
		if len(a.Items) != len(b.Items) {
			return false
		}
		for i, x := range a.Items {
			if !nativeEqual(x, b.Items[i]) {
				return false
			}
		}
		return true
	case value.Object:
		if len(a.Members) != len(b.Members) {
			return false
		}
		for _, m := range a.Members {
			found := false
			for _, n := range b.Members {
				if nativeEqual(&value.Value{Kind: value.String, String: m.Key}, &value.Value{Kind: value.String, String: n.Key}) {
					found = nativeEqual(m.Value, n.Value)
					break
				}
			}
			if !found {
				return false
			}
		}
		return true
	}
	return false
}
func nativeIncrement(v *value.Value) (*value.Value, error) {
	if v == nil {
		return nil, &journal.TypeError{Field: "batch_index"}
	}
	switch v.Kind {
	case value.Int:
		n, ok := new(big.Int).SetString(v.Integer, 10)
		if ok {
			return value.IntegerText(n.Add(n, big.NewInt(1)).String()), nil
		}
	case value.Bool:
		if v.Boolean {
			return value.IntegerText("2"), nil
		}
		return value.IntegerText("1"), nil
	case value.Float:
		return &value.Value{Kind: value.Float, Number: v.Number + 1}, nil
	}
	return nil, &journal.TypeError{Field: "batch_index"}
}
func nativeHasKeys(v *value.Value, keys ...string) bool {
	if v == nil || v.Kind != value.Object || len(v.Members) != len(keys) {
		return false
	}
	for _, key := range keys {
		if v.Get(key) == nil {
			return false
		}
	}
	return true
}
func nativeOr(v, fallback *value.Value) *value.Value {
	if value.Truth(v) {
		return v
	}
	return fallback
}

func (s *NativeSession) AdmitInbox(ctx context.Context, binding *value.Value) (*value.Value, error) {
	if e := s.mapping(); e != nil {
		return nil, e
	}
	agent, e := nativeRequired(s.Record, "agent")
	if e != nil {
		return nil, e
	}
	ref, e := s.sessionRef(agent)
	if e != nil {
		return nil, e
	}
	intent := nativeMap("request_id", s.id(), "session", ref)
	if value.Truth(binding) {
		if binding.Kind != value.Object {
			return nil, &journal.TypeError{Field: "binding"}
		}
		for _, m := range binding.Members {
			for _, existing := range intent.Members {
				if nativeEqual(&value.Value{Kind: value.String, String: m.Key}, &value.Value{Kind: value.String, String: existing.Key}) {
					return nil, &journal.TypeError{Field: "duplicate admission keyword"}
				}
			}
			intent.Members = append(intent.Members, m)
		}
	}
	s.Record.Set("inbox_intent", intent)
	s.Record.Set("inbox_intent_kind", value.Text("ordinary"))
	s.Record.Set("inbox_result_policy", nativeMap("claim_request_id", intent.Get("request_id"), "version", 1))
	if e = s.Save(); e != nil {
		return nil, e
	}
	return s.recoverHost(ctx)
}
func (s *NativeSession) AdmitOrganicNotice(ctx context.Context, turn, origin, attestation *value.Value) (*value.Value, error) {
	if e := s.mapping(); e != nil {
		return nil, e
	}
	agent, e := nativeRequired(s.Record, "agent")
	if e != nil {
		return nil, e
	}
	ref, e := s.sessionRef(agent)
	if e != nil {
		return nil, e
	}
	request := nativeMap("session", ref, "native_owner_turn_id", turn, "owner_input_origin", origin, "owner_input_attestation", attestation)
	pending := s.Record.Get("organic_claim_intent")
	fresh := nativeNone(pending)
	if !fresh {
		if pending.Kind != value.Object {
			return nil, &journal.NonObjectError{Field: "organic_claim_intent"}
		}
		if !nativeEqual(pending.Without("request_id"), request) {
			return nil, nativeRefusal("INTENT_PENDING", "another signed observation is unresolved")
		}
	} else {
		intent := nativeMap("request_id", s.id())
		for _, m := range request.Members {
			intent.Members = append(intent.Members, m)
		}
		s.Record.Set("organic_claim_intent", intent)
		if e = s.Save(); e != nil {
			return nil, e
		}
	}
	return s.recoverOrganicClaim(ctx, fresh)
}
func (s *NativeSession) recoverOrganicClaim(ctx context.Context, fresh bool) (*value.Value, error) {
	request, e := nativeRequired(s.Record, "organic_claim_intent")
	if e != nil {
		return nil, e
	}
	if request.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: "organic_claim_intent"}
	}
	ref, e := s.sessionRef(s.Record.Get("agent"))
	if e != nil {
		return nil, e
	}
	if !nativeEqual(request.Get("session"), ref) {
		return nil, nativeRefusal("STALE_SESSION", "signed observation left its execution")
	}
	result, e := s.call(ctx, "session-organic-notice-claim", request, NativeCallOptions{})
	if e != nil {
		if fresh && nativeContains(append(append([]string(nil), BatchAdvanceFinalRefusals...), "FEATURE_DISABLED", "VERSION_CONFLICT", "CONFLICT", "INPUT_ID_REUSED"), nativeCode(e)) {
			nativePop(s.Record, "organic_claim_intent")
			if save := s.Save(); save != nil {
				return nil, save
			}
		}
		return nil, e
	}
	if result == nil || result.Kind != value.Object || !nativeContains([]string{"none", "admitted", "exhausted"}, nativeText(result.Get("decision"))) {
		return nil, nativeRefusal("INVALID_HOST", "signed observation returned an invalid result")
	}
	selected := result.Get("attempt")
	if !nativeNone(selected) {
		owned, e := nativeOrganicOwnership(selected, request.Get("session"), request.Get("native_owner_turn_id"), result.Get("root_id"), true)
		if e != nil {
			return nil, e
		}
		if !owned {
			return nil, nativeRefusal("INVALID_HOST", "signed observation changed item ownership")
		}
		old := s.Record.Get("inbox_attempt")
		if value.Truth(old) && !nativeEqual(old.Get("attempt_id"), selected.Get("attempt_id")) {
			return nil, nativeRefusal("INVALID_HOST", "signed observation replaced an existing inbox hold")
		}
		if !value.Truth(old) {
			s.Record.Set("inbox_attempt", selected)
			s.Record.Set("organic_root", result.Get("root_id"))
			s.Record.Set("inbox_intent", nativeMap("request_id", selected.Get("attempt_id"), "session", request.Get("session"), "native_turn_id", selected.Get("native_turn_id")))
			s.Record.Set("inbox_intent_kind", value.Text("organic"))
			s.Record.Set("inbox_result_policy", nativeMap("claim_request_id", selected.Get("attempt_id"), "version", 1))
		}
	}
	nativePop(s.Record, "organic_claim_intent")
	if e = s.Save(); e != nil {
		return nil, e
	}
	return result, nil
}
func nativeOrganicOwnership(selected, session, turn, root *value.Value, checkRoot bool) (bool, error) {
	if selected == nil || selected.Kind != value.Object {
		return false, nil
	}
	if !nativeEqual(selected.Get("session"), session) || !nativeEqual(selected.Get("native_turn_id"), turn) || !value.SameText(selected.Get("admission_kind"), "organic") {
		return false, nil
	}
	if checkRoot {
		if !value.Truth(root) || !nativeEqual(selected.Get("organic_root"), root) {
			return false, nil
		}
	} else if !value.Truth(selected.Get("organic_root")) {
		return false, nil
	}
	if !value.Truth(selected.Get("attempt_id")) || value.Truth(selected.Get("finished_at")) || value.Truth(selected.Get("turn_exclusive")) {
		return false, nil
	}
	return nativeNoticeDelivery(selected.Get("delivery"))
}
func nativeNoticeDelivery(raw *value.Value) (bool, error) {
	delivery, e := nativeObj(raw, "delivery")
	if e != nil {
		return false, e
	}
	event, e := nativeDefaultObj(delivery.Get("event"), "event")
	if e != nil {
		return false, e
	}
	return value.SameText(event.Get("kind"), "notice"), nil
}
func (s *NativeSession) AdvanceOrganicNotice(ctx context.Context, request *value.Value) (*value.Value, error) {
	if e := s.mapping(); e != nil {
		return nil, e
	}
	old, e := nativeObj(s.Attempt(), "inbox_attempt")
	if e != nil {
		return nil, e
	}
	if !value.SameText(old.Get("admission_kind"), "organic") || !value.Truth(old.Get("organic_root")) {
		return nil, nativeRefusal("INVALID_REQUEST", "no organic notice is held")
	}
	pending := s.Record.Get("organic_advance_intent")
	fresh := nativeNone(pending)
	if fresh {
		if !value.Truth(request) {
			request = nativeMap("request_id", s.id(), "session", old.Get("session"), "attempt_id", old.Get("attempt_id"), "root_id", old.Get("organic_root"), "version", 1)
		}
		s.Record.Set("organic_advance_intent", request)
		if e = s.Save(); e != nil {
			return nil, e
		}
	} else if !nativeNone(request) && !nativeEqual(request, pending) {
		return nil, nativeRefusal("SESSION_BUSY", "another notice handoff is unresolved")
	}
	return s.AdvanceBatch(ctx, fresh)
}
func (s *NativeSession) Recover(ctx context.Context) (*value.Value, error) {
	if e := s.mapping(); e != nil {
		return nil, e
	}
	if value.Truth(s.Record.Get("organic_claim_intent")) {
		if _, e := s.recoverOrganicClaim(ctx, false); e != nil {
			return nil, e
		}
	}
	if value.Truth(s.Record.Get("batch_advance_intent")) || value.Truth(s.Record.Get("organic_advance_intent")) {
		if _, e := s.AdvanceBatch(ctx, false); e != nil {
			return nil, e
		}
	}
	intent := s.Intent()
	if value.Truth(intent) && !value.Truth(s.Attempt()) {
		if intent.Kind != value.Object {
			return nil, &journal.NonObjectError{Field: "inbox_intent"}
		}
		organic := value.SameText(s.Record.Get("inbox_intent_kind"), "organic") || intent.Get("native_owner_turn_id") != nil
		operation := "session-inbox-claim"
		if organic {
			operation = "session-organic-notice-claim"
		}
		result, e := s.call(ctx, operation, intent, NativeCallOptions{})
		if e != nil {
			return nil, e
		}
		if result == nil || result.Kind != value.Object {
			if !organic || value.Truth(result) {
				return nil, &journal.NonObjectError{Field: "claim result"}
			}
		}
		if value.Truth(result.Get("attempt")) {
			s.Record.Set("inbox_attempt", result.Get("attempt"))
			if organic {
				root := result.Get("root_id")
				if root == nil {
					root = &value.Value{Kind: value.Null}
				}
				s.Record.Set("organic_root", root)
			}
		} else {
			nativePop(s.Record, "inbox_intent", "inbox_intent_kind")
		}
		if e = s.Save(); e != nil {
			return nil, e
		}
	}
	return s.Attempt(), nil
}
func (s *NativeSession) RefreshAttempt(attempt *value.Value) (*value.Value, error) {
	if e := s.mapping(); e != nil {
		return nil, e
	}
	s.Record.Set("inbox_attempt", attempt)
	return attempt, s.Save()
}
func (s *NativeSession) AdvanceBatch(ctx context.Context, freshOrganic bool) (*value.Value, error) {
	if e := s.mapping(); e != nil {
		return nil, e
	}
	organic := value.Truth(s.Record.Get("organic_advance_intent"))
	key, operation := "batch_advance_intent", "session-inbox-advance"
	if organic {
		key, operation = "organic_advance_intent", "session-organic-notice-advance"
		if value.Truth(s.Record.Get("batch_advance_intent")) {
			return nil, nativeRefusal("SESSION_BUSY", "two notice handoffs are unresolved")
		}
	}
	request := s.Record.Get(key)
	fields := []string{"request_id", "session", "attempt_id", "version"}
	if organic {
		fields = append(fields, "root_id")
	}
	if !nativeHasKeys(request, fields...) {
		return nil, nativeRefusal("INVALID_HOST", "native batch advance intent is malformed")
	}
	old, e := nativeObj(s.Attempt(), "inbox_attempt")
	if e != nil {
		return nil, e
	}
	if !nativeEqual(request.Get("attempt_id"), old.Get("attempt_id")) || !nativeEqual(request.Get("session"), old.Get("session")) || !nativeEqual(request.Get("version"), value.IntegerText("1")) || !value.Truth(old.Get("native_turn_id")) || organic != value.SameText(old.Get("admission_kind"), "organic") || organic && !nativeEqual(request.Get("root_id"), old.Get("organic_root")) {
		return nil, nativeRefusal("STALE_SESSION", "native batch advance left its active turn")
	}
	result, e := s.call(ctx, operation, request, NativeCallOptions{})
	if e != nil {
		if (!organic || freshOrganic) && nativeContains(BatchAdvanceFinalRefusals, nativeCode(e)) {
			nativePop(s.Record, key)
			if save := s.Save(); save != nil {
				return nil, save
			}
		}
		return nil, e
	}
	if result == nil || result.Kind != value.Object || result.Get("attempt") == nil || result.Get("remainder") != nil && result.Get("remainder").Kind != value.String {
		return nil, nativeRefusal("INVALID_HOST", "native batch advance returned an invalid result")
	}
	next := result.Get("attempt")
	if !nativeNone(next) {
		expected, e := nativeIncrement(nativeOr(old.Get("batch_index"), value.IntegerText("1")))
		if e != nil {
			return nil, e
		}
		if next.Kind != value.Object || !nativeEqual(next.Get("attempt_id"), request.Get("request_id")) || !nativeEqual(next.Get("session"), request.Get("session")) || !nativeEqual(next.Get("native_turn_id"), old.Get("native_turn_id")) || !nativeEqual(next.Get("batch_root"), nativeOr(old.Get("batch_root"), old.Get("attempt_id"))) || !nativeEqual(next.Get("batch_index"), expected) || !nativeEqual(next.Get("batch_limit"), value.IntegerText("3")) || value.Truth(next.Get("finished_at")) || organic && (!value.SameText(next.Get("admission_kind"), "organic") || !nativeEqual(next.Get("organic_root"), request.Get("root_id"))) {
			return nil, nativeRefusal("INVALID_HOST", "native batch advance changed item ownership")
		}
		notice, e := nativeNoticeDelivery(next.Get("delivery"))
		if e != nil {
			return nil, e
		}
		if !notice {
			return nil, nativeRefusal("INVALID_HOST", "native batch advance changed item ownership")
		}
		s.Record.Set("inbox_attempt", next)
		s.Record.Set("inbox_intent", nativeMap("request_id", next.Get("attempt_id"), "session", request.Get("session"), "native_turn_id", next.Get("native_turn_id")))
		s.Record.Set("inbox_result_policy", nativeMap("claim_request_id", next.Get("attempt_id"), "version", 1))
		kind := "ordinary"
		if organic {
			kind = "organic"
		}
		s.Record.Set("inbox_intent_kind", value.Text(kind))
		nativePop(s.Record, BatchNextKeysCleared...)
	}
	nativePop(s.Record, key)
	if e = s.Save(); e != nil {
		return nil, e
	}
	return result, nil
}
func (s *NativeSession) Renew(ctx context.Context, attempt *value.Value) (*value.Value, error) {
	delivery, e := nativeRequired(attempt, "delivery")
	if e != nil {
		return nil, e
	}
	if value.Truth(attempt.Get("lease_lapsed")) {
		return delivery, nil
	}
	id, e := nativeRequired(delivery, "delivery_id")
	if e != nil {
		return nil, e
	}
	lease, e := nativeRequired(delivery, "lease_id")
	if e != nil {
		return nil, e
	}
	session, e := nativeRequired(attempt, "session")
	if e != nil {
		return nil, e
	}
	result, e := s.call(ctx, "event-renew", nativeMap("delivery_id", id, "lease_id", lease, "lease_seconds", LeaseSeconds), NativeCallOptions{Session: session})
	if e != nil {
		if nativeCode(e) != "STALE_LEASE" {
			return nil, e
		}
		attempt.Set("lease_lapsed", value.Boolean(true))
		if s.Host.Log == nil {
			return nil, &value.ProfileUnavailable{}
		}
		binding, err := nativeRequired(s.Config, "binding")
		if err != nil {
			return nil, err
		}
		id, err := nativeRequired(attempt, "attempt_id")
		if err != nil {
			return nil, err
		}
		if err = s.Host.Log("Cairn presence " + nativeText(binding) + ": lease lapsed for attempt " + nativeText(id) + "; the hold remains until completion or reconciliation"); err != nil {
			return nil, err
		}

	} else {
		delivery = result
	}
	attempt.Set("delivery", delivery)
	return delivery, s.Save()
}
func (s *NativeSession) MarkTurnEnded() error {
	if e := s.mapping(); e != nil {
		return e
	}
	if !value.Truth(s.Record.Get("inbox_turn_ended")) {
		s.Record.Set("inbox_turn_ended", value.Boolean(true))
		return s.Save()
	}
	return nil
}
func (s *NativeSession) MarkDelivered(delivered bool) error {
	if e := s.mapping(); e != nil {
		return e
	}
	s.Record.Set("delivered_since_idle", value.Boolean(delivered))
	return s.Save()
}
func (s *NativeSession) Release(ctx context.Context, reason string, fenced bool) (bool, error) {
	if e := s.mapping(); e != nil {
		return false, e
	}
	if !fenced {
		if _, e := s.recoverHost(ctx); e != nil {
			return false, e
		}
	}
	intent := s.Intent()
	if !value.Truth(intent) {
		return true, nil
	}
	session, e := nativeRequired(intent, "session")
	if e != nil {
		return false, e
	}
	attempt, e := nativeRequired(intent, "request_id")
	if e != nil {
		return false, e
	}
	request := nativeMap("session", session, "attempt_id", attempt, "reason", reason)
	saved := s.Record.Get("inbox_close")
	if saved == nil {
		saved = value.Mapping()
	}
	if saved.Kind != value.Object {
		return false, &journal.NonObjectError{Field: "inbox_close"}
	}
	if nativeEqual(saved.Get("attempt_id"), attempt) && !value.SameText(saved.Get("reason"), reason) {
		_, e := s.call(ctx, "session-inbox-reconcile", saved, NativeCallOptions{})
		if e != nil {
			if nativeCode(e) == "DELIVERY_ACTIVE" && value.SameText(saved.Get("reason"), "delivery_completed") {
				saved = value.Mapping()
				nativePop(s.Record, "inbox_close")
				if e = s.Save(); e != nil {
					return false, e
				}
			} else if !(fenced && nativeCode(e) == "NOT_FOUND") {
				return false, e
			}
		}
		if value.Truth(saved) {
			return s.Clear()
		}
	}
	same := true
	for _, m := range request.Members {
		if !nativeEqual(saved.Get(nativeText(&value.Value{Kind: value.String, String: m.Key})), m.Value) {
			same = false
		}
	}
	if !same {
		close := nativeMap("request_id", s.id())
		close.Members = append(close.Members, request.Members...)
		s.Record.Set("inbox_close", close)
		if e = s.Save(); e != nil {
			return false, e
		}
	}
	_, e = s.call(ctx, "session-inbox-reconcile", s.Record.Get("inbox_close"), NativeCallOptions{})
	if e != nil {
		if nativeCode(e) == "DELIVERY_ACTIVE" && reason == "delivery_completed" {
			nativePop(s.Record, "inbox_close")
			if e = s.Save(); e != nil {
				return false, e
			}
			return false, nil
		}
		if !(fenced && nativeCode(e) == "NOT_FOUND") {
			return false, e
		}
	}
	return s.Clear()
}
func (s *NativeSession) Clear() (bool, error) {
	if e := s.mapping(); e != nil {
		return false, e
	}
	nativePop(s.Record, AttemptKeys...)
	return true, s.Save()
}
func (s *NativeSession) EndTurn(ctx context.Context, fenced bool) (bool, error) {
	if s.Host.Journal == nil {
		return false, &value.ProfileUnavailable{}
	}
	committed, e := s.Host.Journal.Committed(s.Config, s.Record)
	if e != nil {
		return false, e
	}
	if committed {
		released, e := s.releaseHost(ctx, "delivery_completed", fenced)
		if e != nil {
			return false, e
		}
		if released {
			return true, nil
		}
	}
	if !fenced {
		if e = s.Host.Journal.TurnEndDiagnostic(s.Config, s.Record); e != nil {
			return false, e
		}
		if e = s.Save(); e != nil {
			return false, e
		}
	}
	return s.releaseHost(ctx, "turn_ended", fenced)
}
func (s *NativeSession) Settle(ctx context.Context, kind string, evidence, prompt *value.Value, strict bool) (*value.Value, error) {
	if e := s.mapping(); e != nil {
		return nil, e
	}
	if evidence == nil || evidence.Kind != value.Object {
		return nil, &journal.TypeError{Field: "evidence"}
	}
	s.Record.Set(kind, &value.Value{Kind: value.Object, Members: append([]value.Member(nil), evidence.Members...)})
	s.Record.Set("inbox_turn_ended", value.Boolean(true))
	active, e := nativeObj(s.Record.Get("active_prompt"), "active_prompt")
	if e != nil && !nativeNone(prompt) {
		return nil, e
	}
	if !nativeNone(prompt) && nativeEqual(active.Get("id"), prompt) {
		nativePop(s.Record, "active_prompt")
	}
	s.Record.Set("delivered_since_idle", value.Boolean(false))
	if e = s.Save(); e != nil {
		return nil, e
	}
	settled, failed := false, false
	if s.Host.Journal == nil {
		return nil, &value.ProfileUnavailable{}
	}
	settled, e = s.Host.Journal.Flush(ctx, s.Config, s.Record, FlushAttempt)
	if e == nil && settled {
		settled, e = s.endHost(ctx, false)
	}
	if e != nil {
		if !nativeCaught(e) {
			return nil, e
		}
		settled, failed = false, true
	}
	outcome := s.Record.Get(kind)
	if outcome == nil || outcome.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: kind}
	}
	if failed || strict && !settled {
		outcome.Set("settlement", value.Text(SettlementPending))
	} else if settled && value.SameText(outcome.Get("settlement"), "pending") {
		close, e := nativeObj(s.Record.Get("inbox_close"), "inbox_close")
		if e != nil {
			return nil, e
		}
		reason := close.Get("reason")
		if reason == nil {
			reason = value.Text("turn_ended")
		}
		outcome.Set("settlement", reason)
	}
	if e = s.Save(); e != nil {
		return nil, e
	}
	return outcome, nil
}
func (s *NativeSession) Retire(ctx context.Context, reason string, timeout time.Duration) error {
	if reason == "" {
		reason = "process_exited"
	}
	if timeout == 0 {
		timeout = 4 * time.Second
	}
	if e := s.mapping(); e != nil {
		return e
	}
	s.Record.Set("ending", value.Boolean(true))
	if s.Record.Get("ending_reason") == nil {
		s.Record.Set("ending_reason", value.Text(reason))
	}
	if e := s.Save(); e != nil {
		return e
	}
	if !value.Truth(s.Record.Get("agent")) {
		registration, e := nativeRequired(s.Record, "registration")
		if e != nil {
			return e
		}
		agent, e := s.call(ctx, "agent-register", registration, NativeCallOptions{Timeout: timeout})
		if e != nil {
			return e
		}
		s.Record.Set("agent", agent)
		if e = s.Save(); e != nil {
			return e
		}
	}
	if value.Truth(s.Record.Get("organic_claim_intent")) || value.Truth(s.Record.Get("organic_advance_intent")) {
		if _, e := s.Recover(ctx); e != nil {
			if !nativeContains([]string{"NOT_FOUND", "AUTHORITY_DENIED"}, nativeCode(e)) {
				return e
			}
			if e = s.retirementOrganicControl(ctx); e != nil {
				return e
			}
		}
	}
	if s.Host.Journal == nil {
		return &value.ProfileUnavailable{}
	}
	flushed, e := s.Host.Journal.Flush(ctx, s.Config, s.Record, FlushAll)
	if e != nil {
		return e
	}
	if !flushed {
		return nativeRefusal("INTENT_PENDING", "a journaled inbox command is still uncertain; the session stays until it settles")
	}
	ref, e := s.sessionRef(s.Record.Get("agent"))
	if e != nil {
		return e
	}
	if _, e = s.call(ctx, "agent-leave", ref, NativeCallOptions{Timeout: timeout}); e != nil && !nativeContains(RetiredSessionCodes, nativeCode(e)) {
		return e
	}
	if _, e = s.releaseHost(ctx, nativeText(s.Record.Get("ending_reason")), true); e != nil {
		return e
	}
	s.Record.Set("retired", value.Boolean(true))
	return s.Save()
}
func (s *NativeSession) retirementOrganicControl(ctx context.Context) error {
	pending, advance := s.Record.Get("organic_claim_intent"), s.Record.Get("organic_advance_intent")
	old, e := nativeObj(s.Attempt(), "inbox_attempt")
	if e != nil {
		return e
	}
	ref, e := s.sessionRef(s.Record.Get("agent"))
	if e != nil {
		return e
	}
	result, e := s.call(ctx, "session-inbox-control", ref, NativeCallOptions{})
	if e != nil {
		return e
	}
	selected := result.Get("attempt")
	turn := old.Get("native_turn_id")
	if value.Truth(pending) {
		turn = pending.Get("native_owner_turn_id")
	}
	owned, e := nativeOrganicOwnership(selected, ref, turn, nil, false)
	if e != nil {
		return e
	}
	if !owned || !value.Truth(turn) {
		return nativeRefusal("INTENT_PENDING", "Store did not prove the exact organic hold for retirement")
	}
	if value.Truth(advance) {
		index := old.Get("batch_index")
		if index == nil {
			index = value.IntegerText("1")
		}
		expected, e := nativeIncrement(index)
		if e != nil {
			return e
		}
		if !nativeEqual(selected.Get("organic_root"), advance.Get("root_id")) || nativeEqual(selected.Get("attempt_id"), old.Get("attempt_id")) || !nativeEqual(selected.Get("batch_root"), nativeOr(old.Get("batch_root"), old.Get("attempt_id"))) || !nativeEqual(selected.Get("batch_index"), expected) {
			return nativeRefusal("INTENT_PENDING", "Store did not prove the uncertain organic handoff")
		}
	} else if value.Truth(old) && !nativeEqual(old.Get("attempt_id"), selected.Get("attempt_id")) {
		return nativeRefusal("INTENT_PENDING", "Store control differs from the previously held item")
	}
	if !nativeEqual(old.Get("attempt_id"), selected.Get("attempt_id")) {
		nativePop(s.Record, BatchNextKeysCleared...)
	}
	s.Record.Set("inbox_attempt", selected)
	s.Record.Set("inbox_intent", nativeMap("request_id", selected.Get("attempt_id"), "session", selected.Get("session"), "native_turn_id", selected.Get("native_turn_id")))
	s.Record.Set("inbox_intent_kind", value.Text("organic"))
	s.Record.Set("organic_root", selected.Get("organic_root"))
	s.Record.Set("inbox_result_policy", nativeMap("claim_request_id", selected.Get("attempt_id"), "version", 1))
	nativePop(s.Record, "organic_claim_intent", "organic_advance_intent")
	return s.Save()
}

func OpenSession(config *value.Value, nativeID string, host *Host, create bool) (session *NativeSession, release func() error, err error) {
	if host == nil || host.Locate == nil {
		return nil, nil, &value.ProfileUnavailable{}
	}
	path, e := host.Locate(config, nativeID)
	if e != nil {
		return nil, nil, e
	}
	if host.Lock == nil {
		return nil, nil, &value.ProfileUnavailable{}
	}
	lock, e := host.Lock(path)
	if e != nil {
		return nil, nil, e
	}
	release = lock.Close
	ok := false
	defer func() {
		if !ok {
			_ = release()
		}
	}()
	var record *value.Value
	if create {
		if host.Exists == nil {
			return nil, nil, &value.ProfileUnavailable{}
		}
		exists, e := host.Exists(path)
		if e != nil {
			return nil, nil, e
		}
		if !exists {
			record = value.Mapping()
		}
	}
	if record == nil {
		if host.Load == nil {
			return nil, nil, &value.ProfileUnavailable{}
		}
		record, e = host.Load(path)
		if e != nil {
			return nil, nil, e
		}
	}
	ok = true
	return &NativeSession{Config: config, Path: path, Record: record, Host: host}, release, nil
}

type NativeCall struct {
	Operation string
	Body      *value.Value
	Options   NativeCallOptions
}
type MemoryResponse func(*value.Value) (*value.Value, error)
type MemoryHost struct {
	Host
	Calls                        []NativeCall
	Persisted                    []*value.Value
	Logged                       []string
	Responses                    map[string]MemoryResponse
	Contexts                     map[string]*value.Value
	FlushResult, CommittedResult bool
	CommandsAbsentResult         bool
	CompletionStateResult        string
}
type memoryJournal struct{ host *MemoryHost }

func (m memoryJournal) Flush(context.Context, *value.Value, *value.Value, FlushScope) (bool, error) {
	return m.host.FlushResult, nil
}
func (m memoryJournal) Committed(*value.Value, *value.Value) (bool, error) {
	return m.host.CommittedResult, nil
}
func (memoryJournal) TurnEndDiagnostic(*value.Value, *value.Value) error { return nil }
func (m memoryJournal) AttemptCommandsAbsent(*value.Value, *value.Value) (bool, error) {
	return m.host.CommandsAbsentResult, nil
}
func (m memoryJournal) JournalCompletionState(*value.Value, *value.Value) (string, error) {
	if m.host.CompletionStateResult != "" {
		return m.host.CompletionStateResult, nil
	}
	return "absent", nil
}
func NewMemoryHost(responses map[string]MemoryResponse) *MemoryHost {
	m := &MemoryHost{Responses: responses, FlushResult: true, CommandsAbsentResult: true, CompletionStateResult: "absent"}
	m.Host.Journal = memoryJournal{m}
	m.Host.ReadContext = func(path string) (*value.Value, error) {
		if m.Contexts != nil && m.Contexts[path] != nil {
			return nativeClone(m.Contexts[path]), nil
		}
		return nil, &value.Error{Class: "OSError", Message: "context not found"}
	}
	m.Host.Call = func(_ context.Context, _ *value.Value, op string, body *value.Value, options NativeCallOptions) (*value.Value, error) {
		m.Calls = append(m.Calls, NativeCall{op, nativeClone(body), options})
		if fn := m.Responses[op]; fn != nil {
			return fn(body)
		}
		return nil, nil
	}
	m.Host.Persist = func(_ string, record *value.Value) error {
		m.Persisted = append(m.Persisted, nativeClone(record))
		return nil
	}
	m.Host.Log = func(s string) error { m.Logged = append(m.Logged, s); return nil }
	return m
}
