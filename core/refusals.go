package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"slices"
	"strings"
	"time"
)

type Refusal struct {
	ExplanationVersion int                   `json:"explanation_version"`
	AvailableTokens    int                   `json:"available_tokens,omitempty"`
	OptionalLimit      int                   `json:"optional_limit,omitempty"`
	Ranking            string                `json:"ranking,omitempty"`
	Candidates         []CandidateEvaluation `json:"candidates,omitempty"`
	CandidatesCount    *int                  `json:"candidates_count,omitempty"`
	ID                 string                `json:"refusal_id"`
	RequestID          string                `json:"request_id"`
	Operation          string                `json:"operation"`
	Scope              Scope                 `json:"scope"`
	Destination        string                `json:"destination,omitempty"`
	Code               string                `json:"code"`
	Message            string                `json:"message"`
	QuerySHA256        string                `json:"query_sha256,omitempty"`
	Considered         []RecordVersionRef    `json:"considered"`
	ConsideredCount    int                   `json:"considered_count"`
	TraceComplete      bool                  `json:"trace_complete"`
	Snapshot           string                `json:"snapshot,omitempty"`
	ObservedAt         time.Time             `json:"observed_at"`
}

func durablePolicyRefusal(err error) bool {
	switch Code(err) {
	case "OPEN_CONFLICT", "DEPENDENCY_CONFLICT", "BUDGET_REFUSED", "POLICY_UNENFORCEABLE":
		return true
	}
	return false
}

// The refused transaction has no committed effect. This separate transaction
// retains the observed denial, not a new authority decision. Later retries may
// succeed under changed state; identical refusal observations are grouped.
func (s *Store) retainRefusal(ctx context.Context, request any, r Refusal, cause error) error {
	if !durablePolicyRefusal(cause) {
		return cause
	}
	encoded, err := json.Marshal(request)
	if err != nil {
		return err
	}
	digest := sha256.Sum256(encoded)
	r.Code = Code(cause)
	var typed *Error
	if !errors.As(cause, &typed) {
		return cause
	}
	r.Message = typed.Message
	save := func() (string, error) {
		tx, err := s.begin(ctx)
		if err != nil {
			return "", err
		}
		defer tx.Rollback(ctx)
		key := s.channel.Principal + ":" + r.Operation + ":" + r.RequestID + ":" + hex.EncodeToString(digest[:]) + ":" + r.Code
		if err = lock(ctx, tx, "refusal:"+key); err != nil {
			return "", err
		}
		var existing string
		err = tx.QueryRow(ctx, `SELECT refusal_id::text FROM cairn.refusal WHERE caller=$1 AND operation=$2 AND request_id=$3 AND request_digest=$4 AND code=$5`, s.channel.Principal, r.Operation, r.RequestID, digest[:], r.Code).Scan(&existing)
		if err == nil {
			return existing, tx.Commit(ctx)
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return "", err
		}
		r.ID = uuid.NewString()
		if err = tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&r.ObservedAt); err != nil {
			return "", err
		}
		if r.Considered == nil {
			r.Considered = []RecordVersionRef{}
		}
		slices.SortFunc(r.Considered, func(a, b RecordVersionRef) int {
			if c := strings.Compare(a.RecordID, b.RecordID); c != 0 {
				return c
			}
			return a.Version - b.Version
		})
		r.ConsideredCount = len(r.Considered)
		if len(r.Considered) > 1000 {
			r.Considered = r.Considered[:1000]
		}
		slices.SortFunc(r.Candidates, func(a, b CandidateEvaluation) int {
			if c := strings.Compare(a.RecordID, b.RecordID); c != 0 {
				return c
			}
			return a.Version - b.Version
		})
		candidatesCount := len(r.Candidates)
		r.CandidatesCount = &candidatesCount
		if len(r.Candidates) > 1000 {
			r.Candidates = r.Candidates[:1000]
		}
		_, err = tx.Exec(ctx, `INSERT INTO cairn.refusal(refusal_id,operation,request_id,request_digest,code,detail) VALUES($1,$2,$3,$4,$5,$6)`, r.ID, r.Operation, r.RequestID, digest[:], r.Code, r)
		if err != nil {
			return "", err
		}
		return r.ID, tx.Commit(ctx)
	}
	id, err := save()
	if err != nil {
		return &Error{Code: "REFUSAL_UNRECORDED", Message: "operation was refused but its durable observation could not commit", Cause: errors.Join(cause, err)}
	}
	return &Error{Code: typed.Code, Message: typed.Message, Cause: cause, RefusalID: id}
}
func (s *Store) Refusal(ctx context.Context, id string) (Refusal, error) {
	if err := validID(id); err != nil {
		return Refusal{}, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return Refusal{}, err
	}
	defer tx.Rollback(ctx)
	var r Refusal
	var caller string
	err = tx.QueryRow(ctx, `SELECT caller,detail FROM cairn.refusal WHERE refusal_id=$1`, id).Scan(&caller, &r)
	if errors.Is(err, pgx.ErrNoRows) {
		return r, failure("NOT_FOUND", "refusal not found")
	}
	if err != nil {
		return r, err
	}
	if caller != s.channel.Principal {
		return Refusal{}, failure("AUTHORITY_DENIED", "refusal belongs to another caller")
	}
	if err = s.checkRepo(r.Scope.Repo); err != nil {
		return Refusal{}, err
	}
	return r, tx.Commit(ctx)
}
