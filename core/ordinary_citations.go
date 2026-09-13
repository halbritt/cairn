package core

import (
	"context"
	"strings"

	"github.com/jackc/pgx/v5"
)

// CiteRequest replaces the source citations of an ordinary note. An explicit
// empty list clears current citations; earlier versions retain their sources.
type CiteRequest struct {
	RequestID         string                    `json:"request_id"`
	RecordID          string                    `json:"record_id"`
	ExpectedVersion   int                       `json:"expected_version"`
	Repo              string                    `json:"repo"`
	EvidenceCitations []EvidenceCitationRequest `json:"evidence_citations"`
}

func (s *Store) Cite(ctx context.Context, req CiteRequest) (Revision, error) {
	if err := validID(req.RecordID); err != nil {
		return Revision{}, err
	}
	if req.ExpectedVersion < 1 || strings.TrimSpace(req.Repo) == "" || req.Repo == "*" || len(req.Repo) > 256 || req.EvidenceCitations == nil || len(req.EvidenceCitations) > 32 {
		return Revision{}, failure("INVALID_REQUEST", "citation update requires a repository, positive expected_version and explicit list of at most 32 citations")
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return Revision{}, err
	}
	return privileged(ctx, s, "cite", req.RequestID, req, func(tx pgx.Tx) (Revision, error) {
		old, err := readRecord(ctx, tx, req.RecordID)
		if err != nil {
			return Revision{}, err
		}
		if old.Scope.Repo != req.Repo {
			return Revision{}, failure("AUTHORITY_DENIED", "record is outside the requested repository")
		}
		r, err := s.editVersionWithCitations(ctx, tx, EditRequest{req.RequestID, req.RecordID, req.ExpectedVersion, old.Draft}, &req.EvidenceCitations)
		if err != nil {
			return Revision{}, err
		}
		return Revision{r.RecordID, r.Version}, nil
	})
}
