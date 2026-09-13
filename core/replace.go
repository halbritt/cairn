package core

import (
	"context"
	"strings"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
)

// ReplaceRequest corrects one uniquely matching passage. NewText must be
// supplied; an explicit empty string removes the passage from a nonblank note.
type ReplaceRequest struct {
	RequestID       string  `json:"request_id"`
	RecordID        string  `json:"record_id"`
	ExpectedVersion int     `json:"expected_version"`
	Repo            string  `json:"repo"`
	OldText         string  `json:"old_text"`
	NewText         *string `json:"new_text"`
}

func (s *Store) Replace(ctx context.Context, req ReplaceRequest) (Revision, error) {
	if err := validID(req.RecordID); err != nil {
		return Revision{}, err
	}
	if req.ExpectedVersion < 1 || strings.TrimSpace(req.Repo) == "" || req.Repo == "*" || len(req.Repo) > 256 || len(req.OldText) == 0 || len(req.OldText) > 65536 || req.NewText == nil || len(*req.NewText) > 65536 || !utf8.ValidString(req.OldText) || !utf8.ValidString(*req.NewText) {
		return Revision{}, failure("INVALID_REQUEST", "replace requires a repository, positive expected_version, 1-65536 old_text bytes and explicit new_text of at most 65536 bytes; text must be valid UTF-8")
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return Revision{}, err
	}
	return privileged(ctx, s, "replace", req.RequestID, req, func(tx pgx.Tx) (Revision, error) {
		old, err := readRecord(ctx, tx, req.RecordID)
		if err != nil {
			return Revision{}, err
		}
		if old.Scope.Repo != req.Repo {
			return Revision{}, failure("AUTHORITY_DENIED", "record is outside the requested repository")
		}
		if old.Version != req.ExpectedVersion {
			return Revision{}, failure("VERSION_CONFLICT", "record changed; read the current version and reconcile the replacement")
		}
		if old.Class != "A" || old.Lifecycle != "active" {
			return Revision{}, failure("AUTHORITY_DENIED", "ordinary edit requires an active A record; use an audited transition")
		}
		start := strings.Index(old.Body, req.OldText)
		// Search from the next byte so overlapping matches are ambiguous too.
		if start < 0 || strings.Contains(old.Body[start+1:], req.OldText) {
			return Revision{}, failure("INVALID_REQUEST", "old_text must match exactly one passage; read the current note and supply enough surrounding text to identify it")
		}
		draft := old.Draft
		draft.Body = old.Body[:start] + *req.NewText + old.Body[start+len(req.OldText):]
		draft.Sensitivity = old.Sensitivity
		if strings.TrimSpace(draft.Body) == "" || len(draft.Body) > 65536 {
			return Revision{}, failure("INVALID_REQUEST", "replacement must leave a nonblank body of at most 65536 bytes")
		}
		// The ordinary edit locks and rechecks CAS inside this serializable
		// transaction, preserving metadata and citations if the edit commits.
		record, err := s.editVersion(ctx, tx, EditRequest{req.RequestID, req.RecordID, req.ExpectedVersion, draft})
		if err != nil {
			return Revision{}, err
		}
		return Revision{record.RecordID, record.Version}, nil
	})
}
