// Package core owns Cairn's transactional memory. It is an experimental trusted
// host library, not an authentication endpoint or a consequential decision API.
package core

import (
	"errors"
	"strings"
	"time"

	"github.com/google/uuid"
)

type Error struct {
	Code    string
	Message string
	Cause   error
}

func (e *Error) Error() string           { return e.Code + ": " + e.Message }
func (e *Error) Unwrap() error           { return e.Cause }
func failure(code, message string) error { return &Error{Code: code, Message: message} }
func Code(err error) string {
	var e *Error
	if errors.As(err, &e) {
		return e.Code
	}
	return "STORE_ERROR"
}

// Channel is supplied by the trusted embedding host after authentication. Never
// construct it from request JSON. Instrumented permits service-owned events.
type Channel struct {
	Principal    string
	Instrumented bool
	Operator     bool
	Repo         string
}

type Scope struct {
	Repo   string `json:"repo"`
	TaskID string `json:"task_id"`
	RunID  string `json:"run_id"`
}

func (s Scope) validate() error {
	for _, v := range []string{s.Repo, s.TaskID, s.RunID} {
		if strings.TrimSpace(v) == "" || len(v) > 256 {
			return failure("INVALID_REQUEST", "scope requires explicit repo, task_id and run_id (1-256 bytes)")
		}
	}
	if s.Repo == "*" {
		return failure("INVALID_REQUEST", "repository cannot be global")
	}
	return nil
}

func (s *Store) checkRepo(repo string) error {
	if s.channel.Repo != "" && s.channel.Repo != repo {
		return failure("AUTHORITY_DENIED", "repository outside authenticated channel scope")
	}
	return nil
}

type Draft struct {
	Relations          []RecordRelation `json:"relations,omitempty"`
	Pins               *Applicability   `json:"pins,omitempty"`
	Sensitivity        string           `json:"sensitivity,omitempty"`
	Kind               string           `json:"kind"`
	Body               string           `json:"body"`
	Scope              Scope            `json:"scope"`
	AttributedProducer string           `json:"attributed_producer,omitempty"`
	AttemptID          string           `json:"attempt_id,omitempty"`
	ResultRef          string           `json:"result_ref,omitempty"`
	ClaimType          string           `json:"claim_type"`
}

func (d Draft) validate() error {
	if err := validateRelations(d.Relations); err != nil {
		return err
	}
	if err := d.Pins.validate(); err != nil {
		return err
	}
	if err := d.Scope.validate(); err != nil {
		return err
	}
	switch d.Kind {
	case "note", "observation", "claim", "lesson", "procedure", "decision", "preference":
	default:
		return failure("INVALID_REQUEST", "unsupported ordinary record kind")
	}
	if strings.TrimSpace(d.Body) == "" || len(d.Body) > 65536 {
		return failure("INVALID_REQUEST", "body must contain 1-65536 bytes")
	}
	if len(d.AttributedProducer) > 256 || len(d.ResultRef) > 512 {
		return failure("INVALID_REQUEST", "attribution metadata too large")
	}
	switch d.ClaimType {
	case "self", "completion", "partial":
	default:
		return failure("INVALID_REQUEST", "claim_type must be self, completion or partial")
	}
	if d.ClaimType == "self" && (d.AttributedProducer != "" || d.AttemptID != "" || d.ResultRef != "") {
		return failure("INVALID_REQUEST", "self claim must not contain delegation attribution")
	}
	if d.ClaimType != "self" && strings.TrimSpace(d.AttributedProducer) == "" {
		return failure("INVALID_REQUEST", "delegated claim requires attributed_producer")
	}
	if d.AttemptID != "" {
		return validID(d.AttemptID)
	}
	return nil
}
func validID(id string) error {
	parsed, err := uuid.Parse(id)
	if err != nil || parsed == uuid.Nil || parsed.String() != id {
		return failure("INVALID_REQUEST", "expected a nonzero canonical UUID")
	}
	return nil
}

type CreateRequest struct {
	RequestID string `json:"request_id"`
	Draft     Draft  `json:"draft"`
}
type EditRequest struct {
	RequestID       string `json:"request_id"`
	RecordID        string `json:"record_id"`
	ExpectedVersion int    `json:"expected_version"`
	Draft           Draft  `json:"draft"`
}
type Record struct {
	RecordID    string `json:"record_id"`
	Version     int    `json:"version"`
	Class       string `json:"class"`
	Lifecycle   string `json:"lifecycle"`
	Sensitivity string `json:"sensitivity"`
	Draft
	ObservedWriter   string    `json:"observed_writer"`
	Witness          string    `json:"witness"`
	WrittenAt        time.Time `json:"written_at"`
	AttributionState string    `json:"attribution_state"`
}
type SpawnRequest struct {
	RequestID  string `json:"request_id"`
	AttemptID  string `json:"attempt_id"`
	Dispatcher string `json:"dispatcher"`
	Delegate   string `json:"delegate"`
	Scope      Scope  `json:"scope"`
}
type TerminalRequest struct {
	RequestID string `json:"request_id"`
	AttemptID string `json:"attempt_id"`
	State     string `json:"state"`
	ResultRef string `json:"result_ref,omitempty"`
}
type Attempt struct {
	AttemptID string `json:"attempt_id"`
	State     string `json:"state"`
}
