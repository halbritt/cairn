package core

import (
	"context"
	"strings"

	"github.com/jackc/pgx/v5"
)

// ReviseRequest changes only the body of an active ordinary note. The expected
// version and supplied body define the intent, including across retries.
type ReviseRequest struct {
	RequestID       string `json:"request_id"`
	RecordID        string `json:"record_id"`
	ExpectedVersion int    `json:"expected_version"`
	Repo            string `json:"repo"`
	Body            string `json:"body"`
}

// AppendRequest adds Body verbatim after the current body. Callers supply any
// separating whitespace; the combined note keeps the existing 64 KiB limit.
type AppendRequest ReviseRequest

type Revision struct {
	RecordID string `json:"record_id"`
	Version  int    `json:"version"`
}

func (s *Store) Revise(ctx context.Context, req ReviseRequest) (Revision, error) {
	return s.revise(ctx, req, false)
}

func (s *Store) Append(ctx context.Context, req AppendRequest) (Revision, error) {
	return s.revise(ctx, ReviseRequest(req), true)
}

func (s *Store) revise(ctx context.Context, req ReviseRequest, appendBody bool) (Revision, error) {
	if err := validID(req.RecordID); err != nil {
		return Revision{}, err
	}
	if req.ExpectedVersion < 1 || strings.TrimSpace(req.Repo) == "" || req.Repo == "*" || len(req.Repo) > 256 || strings.TrimSpace(req.Body) == "" || len(req.Body) > 65536 {
		return Revision{}, failure("INVALID_REQUEST", "revision requires a repository, positive expected_version and 1-65536 body bytes")
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return Revision{}, err
	}
	operation := "revise"
	if appendBody {
		operation = "append"
	}
	return privileged(ctx, s, operation, req.RequestID, req, func(tx pgx.Tx) (Revision, error) {
		old, err := readRecord(ctx, tx, req.RecordID)
		if err != nil {
			return Revision{}, err
		}
		if old.Scope.Repo != req.Repo {
			return Revision{}, failure("AUTHORITY_DENIED", "record is outside the requested repository")
		}
		draft := old.Draft
		draft.Body, draft.Sensitivity = req.Body, old.Sensitivity
		if appendBody {
			draft.Body = old.Body + req.Body
			if len(draft.Body) > 65536 {
				return Revision{}, failure("INVALID_REQUEST", "appended body exceeds 65536 bytes")
			}
		}
		// editVersion takes the existing attempt/record locks and checks the CAS.
		// A concurrent metadata edit makes this serializable transaction retry or
		// refuse; it cannot be silently overwritten with the earlier snapshot.
		record, err := s.editVersion(ctx, tx, EditRequest{req.RequestID, req.RecordID, req.ExpectedVersion, draft})
		if err != nil {
			return Revision{}, err
		}
		return Revision{record.RecordID, record.Version}, nil
	})
}
