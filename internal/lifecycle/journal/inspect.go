package journal

import (
	"errors"
	"os"
	"strings"
)

// JournalStatus is an observation, not a replay or a normalized validity verdict.
// Presence and truthiness are separate because legacy readers disagree on null,
// empty lists and other falsy, non-object slots. Inspect never acquires a lock.
type JournalStatus struct {
	Exists, NonObject bool
	ReadErr, ParseErr error
	// Direct errors retain read_text() semantics even when Path.exists() is false.
	DirectReadErr, DirectParseErr error
	Schema, AttemptID, DeliveryID string
	DeliveryKind                  string
	Session                       Session
	SessionNonObject              bool
	CompletionKind                string
	CompletionRequestID           string
	CompletionStatus              string
	CompletionPresent             bool
	CompletionNonObject           bool
	CompletionTruthy              bool
	HasUsableFinishReplyRef       bool
	ResponseStatus                string
	ResponsePresent               bool
	ResponseNonObject             bool
	ResponseTruthy                bool
	HasFinishReply                bool
	UnmaterializedFinishReply     bool
	PendingReplay, ReplyPossible  bool
	ClaimOnly                     bool
	journal                       map[string]any
	requestedAttempt              string
}

func Inspect(stateDir, attemptID string) JournalStatus {
	target := path(stateDir, attemptID)
	s := JournalStatus{requestedAttempt: attemptID, Exists: exists(target)}
	data, err := readBytes(target)
	if err != nil {
		s.DirectReadErr = err
		if s.Exists {
			s.ReadErr = err
		}
		s.ClaimOnly = errors.Is(err, os.ErrNotExist)
		return s
	}
	v, err := decode(data)
	if err != nil {
		s.DirectParseErr = err
		if s.Exists {
			s.ParseErr = err
		}
		return s
	}
	j, ok := value(v).(map[string]any)
	if !ok {
		s.NonObject = true
		return s
	}
	s.journal = j
	s.Schema, s.AttemptID, s.DeliveryID = text(j["schema"]), text(j["attempt_id"]), text(j["delivery_id"])
	kind := journalKind(stateDir, attemptID, j)
	s.DeliveryKind = text(kind)
	if session, ok := value(j["session"]).(map[string]any); ok {
		s.Session = Session{AgentID: text(session["agent_id"]), ExecutionID: text(session["execution_id"])}
	} else if _, present := j["session"]; present {
		s.SessionNonObject = true
	}
	completion, completionObject := value(j["completion"]).(map[string]any)
	_, s.CompletionPresent = j["completion"]
	s.CompletionNonObject = s.CompletionPresent && !completionObject
	s.CompletionTruthy = truth(j["completion"])
	s.CompletionKind, s.CompletionRequestID, s.CompletionStatus = text(completion["kind"]), text(completion["request_id"]), text(completion["status"])
	s.HasUsableFinishReplyRef = finishRef(completion) != nil
	response, responseObject := value(j["response"]).(map[string]any)
	_, s.ResponsePresent = j["response"]
	s.ResponseNonObject = s.ResponsePresent && !responseObject
	s.ResponseTruthy, s.ResponseStatus = truth(j["response"]), text(response["status"])
	s.HasFinishReply = truth(j["finish_reply"])
	s.UnmaterializedFinishReply = s.HasFinishReply && !s.ResponseTruthy
	s.PendingReplay = s.CompletionStatus == "pending" || s.ResponseStatus == "pending" ||
		s.UnmaterializedFinishReply && s.CompletionStatus == "committed"
	s.ReplyPossible = (kind == nil || equal(kind, "request")) && !s.HasFinishReply && !s.ResponseTruthy &&
		s.CompletionStatus == "committed" && value(completion["result"]) != nil
	s.ClaimOnly = true
	for key := range j {
		switch key {
		case "schema", "attempt_id", "delivery_id", "kind", "session":
		default:
			s.ClaimOnly = false
		}
	}
	if stored, present := j["attempt_id"]; present && !equal(stored, attemptID) {
		s.ClaimOnly = false
	}
	return s
}

func (s JournalStatus) readError() error {
	if s.DirectReadErr != nil {
		return s.DirectReadErr
	}
	return s.DirectParseErr
}

// Committed preserves journal_committed's escaping non-object completion error,
// including falsy completions. It does not reuse CompletionState's policy.
func (s JournalStatus) Committed() (bool, error) {
	if err := s.readError(); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return false, nil
		}
		return false, err
	}
	if s.NonObject {
		return false, &NonObjectError{"journal"}
	}
	if s.CompletionNonObject {
		return false, &NonObjectError{"completion"}
	}
	return s.CompletionStatus == "committed", nil
}

func (s JournalStatus) CompletionState() (string, error) {
	if !s.Exists {
		return "absent", nil
	}
	if s.readError() != nil {
		return "unknown", nil
	}
	if s.CompletionNonObject && s.CompletionTruthy {
		return "", &NonObjectError{"completion"}
	}
	switch s.CompletionStatus {
	case "committed", "pending", "refused":
		return s.CompletionStatus, nil
	}
	return "absent", nil
}

func (s JournalStatus) ArgvMentionsAgent(agentID string) (bool, error) {
	if err := s.readError(); err != nil {
		return false, err
	}
	if s.NonObject {
		return false, &NonObjectError{"journal"}
	}
	for _, name := range []string{"completion", "response"} {
		slot, err := orObject(s.journal[name], name)
		if err != nil {
			return false, err
		}
		argv, present := slot["argv"]
		if !present {
			continue
		}
		switch a := value(argv).(type) {
		case []any:
			for _, arg := range a {
				if equal(arg, agentID) {
					return true, nil
				}
			}
		case []string:
			for _, arg := range a {
				if arg == agentID {
					return true, nil
				}
			}
		case string:
			if strings.Contains(a, agentID) {
				return true, nil
			}
		case map[string]any:
			if _, found := a[agentID]; found {
				return true, nil
			}
		default:
			return false, &TypeError{name + ".argv"}
		}
	}
	return false, nil
}

// MatchesAgent reproduces seeding's short circuit: an owner match never reads
// malformed slots, and an explicit different owner never falls back to argv.
func (s JournalStatus) MatchesAgent(agentID string) (bool, error) {
	if err := s.readError(); err != nil {
		return false, err
	}
	if s.NonObject {
		return false, &NonObjectError{"journal"}
	}
	session, err := orObject(s.journal["session"], "session")
	if err != nil {
		return false, err
	}
	owner := value(session["agent_id"])
	if equal(owner, agentID) {
		return true, nil
	}
	if owner != nil {
		return false, nil
	}
	return s.ArgvMentionsAgent(agentID)
}

// ConfirmsAck is the compact STALE_LEASE exception. Extra journal fields are
// allowed; an extra session field changes dict equality and cannot confirm it.
func (s JournalStatus) ConfirmsAck(deliveryID string, session Session, requestID string) bool {
	if s.readError() != nil || s.NonObject || s.Schema != Schema || s.AttemptID != s.requestedAttempt ||
		s.DeliveryID != deliveryID || s.CompletionNonObject {
		return false
	}
	expected := map[string]any{"agent_id": session.AgentID, "execution_id": session.ExecutionID}
	return equal(s.journal["session"], expected) && s.CompletionKind == "ack" &&
		s.CompletionRequestID == requestID && s.CompletionStatus == "committed"
}

// SettledJournalIndex preserves the compatibility discrepancy: missing files
// count as settled, and an unmaterialized finish_reply is not itself unsettled.
func SettledJournalIndex(stateDir string, state map[string]any) bool {
	v, present := state["inbox_journals"]
	if !present {
		return true
	}
	ids, err := stringList(v, "inbox_journals")
	if err != nil {
		return false
	}
	for _, id := range ids {
		s := Inspect(stateDir, id)
		if !s.Exists {
			continue
		}
		if s.readError() != nil || s.NonObject {
			return false
		}
		if s.CompletionTruthy && (s.CompletionNonObject || s.CompletionStatus != "committed" && s.CompletionStatus != "refused") ||
			s.ResponseTruthy && (s.ResponseNonObject || s.ResponseStatus != "committed" && s.ResponseStatus != "refused") {
			return false
		}
	}
	return true
}
