package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type EvidenceRequest struct {
	RequestID   string `json:"request_id"`
	Repo        string `json:"repo"`
	Body        string `json:"body"`
	Source      string `json:"source"`
	Sensitivity string `json:"sensitivity"`
}
type Evidence struct {
	ID      string `json:"evidence_id"`
	Digest  string `json:"sha256"`
	Witness string `json:"witness"`
	State   string `json:"state"`
}

func (s *Store) CaptureEvidence(ctx context.Context, req EvidenceRequest) (Evidence, error) {
	if err := s.checkRepo(req.Repo); err != nil {
		return Evidence{}, err
	}
	if req.Repo == "" || req.Repo == "*" || len(req.Repo) > 256 || len(req.Body) == 0 || len(req.Body) > 1048576 || strings.TrimSpace(req.Source) == "" || len(req.Source) > 512 {
		return Evidence{}, failure("INVALID_REQUEST", "bounded evidence body, source label and exact repository required")
	}
	if req.Sensitivity == "" {
		req.Sensitivity = "local"
	}
	if req.Sensitivity != "local" && req.Sensitivity != "shareable" {
		return Evidence{}, failure("INVALID_REQUEST", "unknown sensitivity")
	}
	return mutate(ctx, s, "capture-evidence", req.RequestID, req, func(tx pgx.Tx) (Evidence, error) {
		id := uuid.NewString()
		digest := sha256.Sum256([]byte(req.Body))
		witness := "testimony"
		if s.channel.Instrumented {
			witness = "instrumented"
		}
		_, err := tx.Exec(ctx, `INSERT INTO cairn.evidence(evidence_id,repo,body,digest,source,witness,sensitivity) VALUES($1,$2,$3,$4,$5,$6,$7)`, id, req.Repo, []byte(req.Body), digest[:], req.Source, witness, req.Sensitivity)
		return Evidence{id, hex.EncodeToString(digest[:]), witness, "resolvable"}, err
	})
}

func linkEvidence(ctx context.Context, tx pgx.Tx, r Record, ids []string) error {
	if len(ids) == 0 || len(ids) > 32 {
		return failure("EVIDENCE_UNAVAILABLE", "1-32 captured evidence references required")
	}
	for _, id := range ids {
		if err := validID(id); err != nil {
			return err
		}
		var body, digest []byte
		var repo, sensitivity, state string
		err := tx.QueryRow(ctx, `SELECT body,digest,repo,sensitivity,state FROM cairn.evidence WHERE evidence_id=$1 FOR SHARE`, id).Scan(&body, &digest, &repo, &sensitivity, &state)
		if err == pgx.ErrNoRows {
			return failure("EVIDENCE_UNAVAILABLE", "evidence not found")
		}
		if err != nil {
			return err
		}
		actual := sha256.Sum256(body)
		if state != "resolvable" || !bytes.Equal(actual[:], digest) || repo != r.Scope.Repo {
			return failure("EVIDENCE_UNAVAILABLE", "evidence unavailable, divergent or outside scope")
		}
		if r.Sensitivity == "shareable" && sensitivity != "shareable" {
			return failure("DESTINATION_PROHIBITED", "shareable claim cannot expose local evidence metadata")
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.evidence_ref(record_id,version,evidence_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING`, r.RecordID, r.Version, id); err != nil {
			return err
		}
	}
	return nil
}

func supportingEvidence(ctx context.Context, tx pgx.Tx, id string, version int) ([]Evidence, error) {
	rows, err := tx.Query(ctx, `SELECT e.evidence_id::text,e.body,e.digest,e.witness,e.state FROM cairn.evidence e JOIN cairn.evidence_ref r USING(evidence_id) WHERE r.record_id=$1 AND r.version=$2 ORDER BY e.evidence_id`, id, version)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []Evidence{}
	for rows.Next() {
		var e Evidence
		var body, digest []byte
		if err = rows.Scan(&e.ID, &body, &digest, &e.Witness, &e.State); err != nil {
			return nil, err
		}
		actual := sha256.Sum256(body)
		e.Digest = hex.EncodeToString(digest)
		if !bytes.Equal(actual[:], digest) {
			e.State = "divergent"
		}
		result = append(result, e)
	}
	return result, rows.Err()
}
