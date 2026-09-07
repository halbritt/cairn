package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

type RunBindingRequest struct {
	RequestID       string `json:"request_id"`
	ReceiptID       string `json:"receipt_id"`
	TaskClass       string `json:"task_class"`
	BindingID       string `json:"binding_id"`
	CapabilityID    string `json:"capability_id"`
	CommandSHA256   string `json:"command_sha256"`
	Revision        string `json:"revision"`
	WorkspaceSHA256 string `json:"workspace_sha256"`
}

func (s *Store) BindRun(ctx context.Context, req RunBindingRequest) (Observation, error) {
	if !s.channel.Instrumented {
		return Observation{}, failure("AUTHORITY_DENIED", "run binding requires a service channel")
	}
	for _, v := range []string{req.TaskClass, req.BindingID, req.CapabilityID} {
		if strings.TrimSpace(v) == "" || len(v) > 256 {
			return Observation{}, failure("INVALID_REQUEST", "bounded task class, binding and capability labels required; use unknown explicitly")
		}
	}
	if !digestValid(req.CommandSHA256) || len(req.Revision) > 256 || (req.WorkspaceSHA256 != "" && !digestValid(req.WorkspaceSHA256)) {
		return Observation{}, failure("INVALID_REQUEST", "invalid command or workspace digest or revision")
	}
	return mutate(ctx, s, "bind-run", req.RequestID, req, func(tx pgx.Tx) (Observation, error) {
		if err := s.receiptAccess(ctx, tx, req.ReceiptID); err != nil {
			return Observation{}, err
		}
		var launched bool
		if err := tx.QueryRow(ctx, `SELECT launch_claimed FROM cairn.retrieval_receipt WHERE receipt_id=$1 FOR UPDATE`, req.ReceiptID).Scan(&launched); err != nil {
			return Observation{}, err
		}
		if launched {
			return Observation{}, failure("RUN_ALREADY_STARTED", "bind run identity before claiming launch")
		}
		tag, err := tx.Exec(ctx, `INSERT INTO cairn.run_binding(receipt_id,task_class,binding_id,capability_id,command_sha256,revision,workspace_sha256) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING`, req.ReceiptID, req.TaskClass, req.BindingID, req.CapabilityID, strings.ToLower(req.CommandSHA256), req.Revision, strings.ToLower(req.WorkspaceSHA256))
		if err != nil {
			return Observation{}, err
		}
		if tag.RowsAffected() != 1 {
			return Observation{}, failure("VERSION_CONFLICT", "run already bound; retry original request")
		}
		return Observation{req.ReceiptID}, nil
	})
}

type AssessmentRequest struct {
	RequestID       string   `json:"request_id"`
	ReceiptID       string   `json:"receipt_id"`
	ExpectedVersion int      `json:"expected_version"`
	TaskOutcome     string   `json:"task_outcome"`
	FailureDomain   string   `json:"failure_domain"`
	FailureKind     string   `json:"failure_kind"`
	ErrorSignature  string   `json:"error_signature_sha256,omitempty"`
	Method          string   `json:"method"`
	EvidenceIDs     []string `json:"evidence_ids"`
	Reason          string   `json:"reason"`
}
type Assessment struct {
	ReceiptID      string    `json:"receipt_id"`
	Version        int       `json:"version"`
	TaskOutcome    string    `json:"task_outcome"`
	FailureDomain  string    `json:"failure_domain"`
	FailureKind    string    `json:"failure_kind"`
	ErrorSignature string    `json:"error_signature_sha256,omitempty"`
	Method         string    `json:"method"`
	EvidenceIDs    []string  `json:"evidence_ids"`
	Reason         string    `json:"reason"`
	Witness        string    `json:"witness"`
	Observer       string    `json:"observer"`
	ObservedAt     time.Time `json:"observed_at"`
}

func (s *Store) AssessRun(ctx context.Context, req AssessmentRequest) (Assessment, error) {
	if req.ExpectedVersion < 0 || strings.TrimSpace(req.Method) == "" || len(req.Method) > 256 || len(req.FailureKind) > 128 || len(req.EvidenceIDs) > 32 || (req.ErrorSignature != "" && !digestValid(req.ErrorSignature)) {
		return Assessment{}, failure("INVALID_REQUEST", "invalid assessment metadata")
	}
	if err := reasonValid(req.Reason); err != nil {
		return Assessment{}, err
	}
	switch req.TaskOutcome {
	case "accepted", "rejected", "not_attempted", "unknown":
	default:
		return Assessment{}, failure("INVALID_REQUEST", "unknown task outcome")
	}
	switch req.FailureDomain {
	case "none", "binding", "capability", "task", "unknown":
	default:
		return Assessment{}, failure("INVALID_REQUEST", "unknown failure domain")
	}
	if req.FailureDomain == "binding" {
		if req.TaskOutcome != "not_attempted" && req.TaskOutcome != "unknown" {
			return Assessment{}, failure("INVALID_REQUEST", "binding failure cannot establish task or capability failure")
		}
		switch req.FailureKind {
		case "quota", "credentials", "transport", "adapter", "unknown":
		default:
			return Assessment{}, failure("INVALID_REQUEST", "unknown binding failure kind")
		}
	}
	if (req.TaskOutcome == "accepted" && req.FailureDomain != "none") || ((req.FailureDomain == "capability" || req.FailureDomain == "task") && req.TaskOutcome != "rejected") {
		return Assessment{}, failure("INVALID_REQUEST", "inconsistent task outcome and failure domain")
	}
	if req.TaskOutcome != "unknown" && len(req.EvidenceIDs) == 0 {
		return Assessment{}, failure("EVIDENCE_UNAVAILABLE", "a task assessment requires explicitly selected evidence")
	}
	return mutate(ctx, s, "assess-run", req.RequestID, req, func(tx pgx.Tx) (Assessment, error) {
		if err := s.receiptAccess(ctx, tx, req.ReceiptID); err != nil {
			return Assessment{}, err
		}
		var repo string
		if err := tx.QueryRow(ctx, `SELECT scope->>'repo' FROM cairn.retrieval_receipt WHERE receipt_id=$1 FOR UPDATE`, req.ReceiptID).Scan(&repo); err != nil {
			return Assessment{}, err
		}
		var version int
		if err := tx.QueryRow(ctx, `SELECT COALESCE(max(version),0) FROM cairn.run_assessment WHERE receipt_id=$1`, req.ReceiptID).Scan(&version); err != nil {
			return Assessment{}, err
		}
		if version != req.ExpectedVersion {
			return Assessment{}, failure("VERSION_CONFLICT", "assessment version changed")
		}
		for _, id := range req.EvidenceIDs {
			if err := checkAssessmentEvidence(ctx, tx, id, repo); err != nil {
				return Assessment{}, err
			}
		}
		witness := "testimony"
		if s.channel.Instrumented {
			witness = "instrumented"
		}
		result := Assessment{ReceiptID: req.ReceiptID, Version: version + 1, TaskOutcome: req.TaskOutcome, FailureDomain: req.FailureDomain, FailureKind: req.FailureKind, ErrorSignature: strings.ToLower(req.ErrorSignature), Method: req.Method, EvidenceIDs: req.EvidenceIDs, Reason: req.Reason, Witness: witness, Observer: s.channel.Principal}
		if err := tx.QueryRow(ctx, `SELECT transaction_timestamp()`).Scan(&result.ObservedAt); err != nil {
			return result, err
		}
		if _, err := tx.Exec(ctx, `INSERT INTO cairn.run_assessment(receipt_id,version,task_outcome,failure_domain,failure_kind,detail) VALUES($1,$2,$3,$4,$5,$6)`, req.ReceiptID, result.Version, req.TaskOutcome, req.FailureDomain, req.FailureKind, result); err != nil {
			return result, err
		}
		for _, id := range req.EvidenceIDs {
			if _, err := tx.Exec(ctx, `INSERT INTO cairn.assessment_evidence(receipt_id,version,evidence_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING`, req.ReceiptID, result.Version, id); err != nil {
				return result, err
			}
		}
		return result, nil
	})
}
func checkAssessmentEvidence(ctx context.Context, tx pgx.Tx, id, repo string) error {
	if err := validID(id); err != nil {
		return err
	}
	var body, digest []byte
	var sourceRepo, state string
	err := tx.QueryRow(ctx, `SELECT body,digest,repo,state FROM cairn.evidence WHERE evidence_id=$1 FOR SHARE`, id).Scan(&body, &digest, &sourceRepo, &state)
	if err == pgx.ErrNoRows {
		return failure("EVIDENCE_UNAVAILABLE", "assessment evidence missing")
	}
	if err != nil {
		return err
	}
	actual := sha256.Sum256(body)
	if sourceRepo != repo || state != "resolvable" || !bytes.Equal(actual[:], digest) {
		return failure("EVIDENCE_UNAVAILABLE", "assessment evidence unavailable or outside repository")
	}
	return nil
}
func (s *Store) Assessments(ctx context.Context, receiptID string) ([]Assessment, error) {
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	if err = s.receiptAccess(ctx, tx, receiptID); err != nil {
		return nil, err
	}
	rows, err := tx.Query(ctx, `SELECT detail FROM cairn.run_assessment WHERE receipt_id=$1 ORDER BY version LIMIT 1001`, receiptID)
	if err != nil {
		return nil, err
	}
	results := []Assessment{}
	for rows.Next() {
		var a Assessment
		if err = rows.Scan(&a); err != nil {
			rows.Close()
			return nil, err
		}
		results = append(results, a)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	if len(results) > 1000 {
		return nil, failure("BUDGET_REFUSED", "assessment history exceeds 1000 versions")
	}
	return results, tx.Commit(ctx)
}
