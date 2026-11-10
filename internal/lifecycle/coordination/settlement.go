package coordination

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// JournalAttemptInspector extends JournalBoundary with side-effect-free per-attempt
// inspection required by unacknowledged turn-end reminder evaluation.
type JournalAttemptInspector interface {
	AttemptCommandsAbsent(config, attempt *value.Value) (bool, error)
	JournalCompletionState(config, attempt *value.Value) (string, error)
}

// CompletionScope evaluates the admission-pinned presentation policy on state
// and attempt, matching coordination.py completion_scope(state, attempt).
func CompletionScope(state, attempt *value.Value) ([]string, error) {
	if state == nil || state.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: "session"}
	}
	policy := state.Get("inbox_result_policy")
	if policy == nil {
		return []string{}, nil
	}
	if policy.Kind != value.Object {
		return nil, nativeRefusal("INVALID_HOST", "invalid inbox result policy")
	}
	claim := policy.Get("claim_request_id")
	if !canonicalUUIDValue(claim) {
		return nil, nativeRefusal("INVALID_HOST", "invalid inbox result policy identity")
	}
	intent, err := nativeObj(state.Get("inbox_intent"), "inbox_intent")
	if err != nil {
		return nil, err
	}
	if !nativeEqual(claim, intent.Get("request_id")) {
		return []string{}, nil
	}
	if attempt == nil || attempt.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: "inbox_attempt"}
	}
	version := policy.Get("version")
	if !nativeHasKeys(policy, "claim_request_id", "version") || version == nil || version.Kind != value.Int || version.Integer != "1" || !nativeEqual(claim, attempt.Get("attempt_id")) || !nativeEqual(intent.Get("session"), attempt.Get("session")) {
		return nil, nativeRefusal("INVALID_HOST", "unsupported or mismatched inbox result policy")
	}
	deliveryObj, err := nativeRequired(attempt, "delivery")
	if err != nil {
		return nil, err
	}
	delivery, err := nativeRequired(deliveryObj, "delivery_id")
	if err != nil {
		return nil, err
	}
	if !canonicalUUIDValue(delivery) {
		return nil, nativeRefusal("INVALID_HOST", "invalid inbox result delivery identity")
	}
	return []string{"--task", "coordination-result/" + string(delivery.String), "--run", "*"}, nil
}

func canonicalUUIDValue(v *value.Value) bool {
	if v == nil || v.Kind != value.String {
		return false
	}
	s := string(v.String)
	u, err := uuid.Parse(s)
	return err == nil && u.String() == s
}

// SettleInbox reconciles what the store and turn state prove after a journal flush.
func (s *NativeSession) SettleInbox(ctx context.Context) (bool, error) {
	if e := s.mapping(); e != nil {
		return false, e
	}
	if value.Truth(s.Record.Get("inbox_turn_ended")) {
		return s.endHost(ctx, false)
	}
	return s.releaseHost(ctx, "delivery_completed", false)
}

// UnacknowledgedTurnEndReminder surfaces one bounded turn-end reminder when an
// active attempt has no journaled command, matching coordination.py.
func (s *NativeSession) UnacknowledgedTurnEndReminder(ctx context.Context, observation *value.Value) (string, error) {
	if e := s.mapping(); e != nil {
		return "", e
	}
	if s.Config == nil || s.Config.Kind != value.Object {
		return "", &journal.NonObjectError{Field: "config"}
	}
	if !value.Truth(s.Config.Get("remind_unacknowledged_turn_end")) {
		return "", nil
	}
	if value.Truth(s.Record.Get("inbox_turn_ended")) || value.Truth(s.Record.Get("inbox_close")) || value.Truth(s.Record.Get("ending")) || value.Truth(s.Record.Get("retired")) {
		return "", nil
	}
	intent, attempt := s.Record.Get("inbox_intent"), s.Record.Get("inbox_attempt")
	if intent == nil || intent.Kind != value.Object || attempt == nil || attempt.Kind != value.Object {
		return "", nil
	}
	attemptID := attempt.Get("attempt_id")
	if !value.Truth(attemptID) || !nativeEqual(intent.Get("request_id"), attemptID) {
		return "", nil
	}
	agent, err := nativeObj(s.Record.Get("agent"), "agent")
	if err != nil {
		return "", err
	}
	ref, err := s.sessionRef(agent)
	if err != nil {
		return "", err
	}
	if !nativeEqual(attempt.Get("session"), ref) || value.Truth(attempt.Get("finished_at")) || value.Truth(attempt.Get("cancel")) || value.Truth(attempt.Get("lease_lapsed")) {
		return "", nil
	}
	activePrompt, err := nativeObj(s.Record.Get("active_prompt"), "active_prompt")
	if err != nil {
		return "", err
	}
	if value.Truth(activePrompt.Get("joined")) {
		return "", nil
	}
	if s.Host == nil || s.Host.Journal == nil {
		return "", &value.ProfileUnavailable{}
	}
	inspector, ok := s.Host.Journal.(JournalAttemptInspector)
	if !ok {
		return "", &value.ProfileUnavailable{}
	}
	absent, err := inspector.AttemptCommandsAbsent(s.Config, attempt)
	if err != nil || !absent {
		return "", err
	}
	if attemptID.Kind != value.String {
		return "", &journal.TypeError{Field: "attempt_id"}
	}
	hasSurrogate := !validUTF8Runes(attemptID.String)
	attemptText := string(attemptID.String)
	dir, err := s.reminderStateDir()
	if err != nil {
		return "", err
	}
	target, err := s.reminderInboxTarget(dir, attemptID)
	if err != nil {
		if sourceReadCaught(err) {
			return "", nil
		}
		return "", err
	}
	contextVal, err := s.loadReminderContext(target)
	if err != nil || contextVal == nil || contextVal.Kind != value.Object || !nativeEqual(contextVal.Get("attempt_id"), attemptID) {
		return "", err
	}
	fallback := ""
	if cs := contextVal.Get("compact_source_unavailable"); cs != nil && cs.Kind == value.Bool && cs.Boolean {
		fc := contextVal.Get("compact_fallback_control")
		if fc == nil || fc.Kind != value.String || len(fc.String) == 0 {
			return "", nativeRefusal("INVALID_HOST", "unavailable source has no settlement instructions")
		}
		hasSurrogate = hasSurrogate || !validUTF8Runes(fc.String)
		fallback = string(fc.String)
	}
	deliveryObj, err := nativeObj(attempt.Get("delivery"), "delivery")
	if err != nil {
		return "", err
	}
	eventObj, err := nativeObj(deliveryObj.Get("event"), "event")
	if err != nil {
		return "", err
	}
	kindVal := nativeOr(nativeOr(eventObj.Get("kind"), contextVal.Get("kind")), value.Text("inbox"))
	kind := nativeText(kindVal)
	var reminder string
	switch {
	case fallback != "":
		reminder = fmt.Sprintf("Cairn inbox reminder: attempt %s still needs an explicit outcome. %s Ending the turn again without settlement will record this delivery as processing_failed.", attemptText, fallback)
	case (kind == "response" || kind == "notice") && contextVal.Get("acknowledgement") != nil && contextVal.Get("acknowledgement").Kind == value.Array:
		ackCmd, surr, e := shlexJoinValue(contextVal.Get("acknowledgement"))
		if e != nil {
			return "", e
		}
		hasSurrogate = hasSurrogate || surr
		reminder = fmt.Sprintf("Cairn inbox reminder: this turn received a %s (attempt %s) that has not been acknowledged yet. Run the exact acknowledgement command before ending your turn: %s. Ending the turn again without acknowledging will record this delivery as processing_failed.", kind, attemptText, ackCmd)
	case contextVal.Get("completion") != nil && contextVal.Get("completion").Kind == value.Array:
		if kindVal != nil && kindVal.Kind == value.String && !validUTF8Runes(kindVal.String) {
			hasSurrogate = true
		}
		cmdVal := contextVal.Get("finish_reply")
		hasFinishReply := value.Truth(cmdVal)
		if !hasFinishReply {
			cmdVal = contextVal.Get("completion")
		}
		compCmd, surr, e := shlexJoinValue(cmdVal)
		if e != nil {
			return "", e
		}
		hasSurrogate = hasSurrogate || surr
		replyHint := ""
		if !hasFinishReply && contextVal.Get("response") != nil && contextVal.Get("response").Kind == value.Array {
			respCmd, respSurr, e := shlexJoinValue(contextVal.Get("response"))
			if e != nil {
				return "", e
			}
			hasSurrogate = hasSurrogate || respSurr
			replyHint = fmt.Sprintf(" Then send the reply with %s --version RESULT_VERSION RESULT_RECORD_UUID.", respCmd)
		}
		reminder = fmt.Sprintf("Cairn inbox reminder: this turn received a %s (attempt %s) that has no journaled completion yet. Run the exact command with a concise selected result on stdin before ending your turn: %s.%s Ending the turn again without completion will record this delivery as processing_failed.", kind, attemptText, compCmd, replyHint)
	default:
		return "", nil
	}
	prior := s.Record.Get("inbox_ack_reminder_revision")
	if nativeEqual(s.Record.Get("inbox_ack_reminded"), attemptID) {
		if (prior == nil || prior.Kind != value.Object || !nativeEqual(prior.Get("attempt_id"), attemptID)) && fallback == "" {
			return "", nil
		}
	}
	jState, err := inspector.JournalCompletionState(s.Config, attempt)
	if err != nil {
		return "", err
	}
	if hasSurrogate {
		return "", &value.Error{Class: "UnicodeEncodeError", Message: "surrogates not allowed"}
	}
	revision := reminderRevisionSHA256(attemptText, jState, reminder)
	if prior != nil && prior.Kind == value.Object && nativeEqual(prior.Get("attempt_id"), attemptID) && value.SameText(prior.Get("sha256"), revision) {
		return "", nil
	}
	if _, err = s.renewHost(ctx, attempt); err != nil {
		return "", err
	}
	if value.Truth(attempt.Get("lease_lapsed")) {
		return "", nil
	}
	s.Record.Set("inbox_ack_reminded", attemptID)
	s.Record.Set("inbox_ack_reminder_revision", nativeMap("attempt_id", attemptID, "sha256", revision))
	s.Record.Set("delivered_since_idle", value.Boolean(true))
	if value.SameText(s.Config.Get("harness"), "claude") && observation != nil && observation.Kind == value.Object && value.Truth(observation.Get("native_turn_id")) {
		s.Record.Set("active_prompt", nativeMap("id", observation.Get("native_turn_id")))
	}
	if err = s.Save(); err != nil {
		return "", err
	}
	return reminder, nil
}

func validUTF8Runes(points []rune) bool {
	for _, r := range points {
		if !utf8.ValidRune(r) {
			return false
		}
	}
	return true
}

func (s *NativeSession) reminderStateDir() (string, error) {
	v, err := nativeRequired(s.Config, "state_dir")
	if err != nil {
		return "", err
	}
	if s.Host != nil && s.Host.Profile != nil {
		return sourcePathText(s.Host.Profile, v)
	}
	if v.Kind != value.String {
		return "", &journal.TypeError{Field: "state_dir"}
	}
	if !validUTF8Runes(v.String) {
		return "", &value.ProfileUnavailable{}
	}
	return string(v.String), nil
}

func (s *NativeSession) reminderInboxTarget(dir string, attemptID *value.Value) (string, error) {
	if s.Host != nil && s.Host.Profile != nil {
		name, err := sourcePathText(s.Host.Profile, attemptID)
		if err != nil {
			return "", err
		}
		return sourceJoin(sourceJoin(dir, "inbox"), name+".json"), nil
	}
	if !validUTF8Runes(attemptID.String) {
		return "", &value.Error{Class: "UnicodeEncodeError", Message: "surrogates not allowed"}
	}
	return sourceJoin(sourceJoin(dir, "inbox"), string(attemptID.String)+".json"), nil
}

func (s *NativeSession) loadReminderContext(path string) (*value.Value, error) {
	reader := s.Host.ReadContext
	if reader == nil {
		reader = s.Host.Load
	}
	if reader == nil {
		return nil, &value.ProfileUnavailable{}
	}
	v, err := reader(path)
	if err != nil {
		if sourceReadCaught(err) {
			return nil, nil
		}
		return nil, err
	}
	return v, nil
}

func shlexJoinValue(v *value.Value) (string, bool, error) {
	if v == nil || v.Kind != value.Array {
		return "", false, &journal.TypeError{Field: "command"}
	}
	hasSurrogate := false
	parts := make([]string, len(v.Items))
	for i, item := range v.Items {
		if item == nil || item.Kind != value.String {
			return "", false, &journal.TypeError{Field: "command"}
		}
		if !validUTF8Runes(item.String) {
			hasSurrogate = true
		}
		parts[i] = shlexQuote(string(item.String))
	}
	return strings.Join(parts, " "), hasSurrogate, nil
}

func shlexQuote(s string) string {
	if s == "" {
		return "''"
	}
	for _, r := range s {
		if !((r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || strings.ContainsRune("_@%+=:,./-", r)) {
			return "'" + strings.ReplaceAll(s, "'", "'\"'\"'") + "'"
		}
	}
	return s
}

func reminderRevisionSHA256(attemptID, journalState, reminder string) string {
	var b bytes.Buffer
	b.WriteString(`{"attempt_id":`)
	writeUTF8JSONString(&b, attemptID)
	b.WriteString(`,"journal_state":`)
	writeUTF8JSONString(&b, journalState)
	b.WriteString(`,"reminder":`)
	writeUTF8JSONString(&b, reminder)
	b.WriteByte('}')
	sum := sha256.Sum256(b.Bytes())
	return hex.EncodeToString(sum[:])
}

func writeUTF8JSONString(b *bytes.Buffer, s string) {
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"', '\\':
			b.WriteByte('\\')
			b.WriteByte(byte(r))
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		default:
			if r < 0x20 {
				fmt.Fprintf(b, `\u%04x`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
}
