package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

type RunReportRequest struct {
	Repo           string `json:"repo"`
	PolicyRevision string `json:"policy_revision,omitempty"`
	Limit          int    `json:"limit"`
	Offset         int    `json:"offset"`
}

type RunRow struct {
	AttemptID         string    `json:"attempt_id,omitempty"`
	ReceiptID         string    `json:"receipt_id"`
	PolicyRevision    string    `json:"policy_revision"`
	Scope             Scope     `json:"scope"`
	CompiledAt        time.Time `json:"compiled_at"`
	LaunchClaimed     bool      `json:"launch_claimed"`
	BindingObserved   bool      `json:"binding_observed"`
	OutcomeObserved   bool      `json:"outcome_observed"`
	TaskClass         string    `json:"task_class"`
	BindingID         string    `json:"binding_id"`
	CapabilityID      string    `json:"capability_id"`
	Revision          string    `json:"revision"`
	WorkspaceSHA256   string    `json:"workspace_sha256"`
	CommandSHA256     string    `json:"command_sha256"`
	ExposureRows      int       `json:"exposure_rows"`
	ProcessState      string    `json:"process_state"`
	ExitCode          *int      `json:"exit_code"`
	DurationMS        *int64    `json:"duration_ms"`
	TaskOutcome       string    `json:"task_outcome"`
	AssessmentVersion int       `json:"assessment_version"`
	AssessmentWitness string    `json:"assessment_witness"`
	AssessmentMethod  string    `json:"assessment_method"`
	FailureDomain     string    `json:"failure_domain"`
	FailureKind       string    `json:"failure_kind"`
	ErrorSignature    string    `json:"error_signature_sha256"`
}

type RunReport struct {
	Rows           []RunRow `json:"rows"`
	More           bool     `json:"more"`
	NextOffset     int      `json:"next_offset"`
	Interpretation string   `json:"interpretation"`
}

func (s *Store) RunReport(ctx context.Context, req RunReportRequest) (RunReport, error) {
	if err := s.checkRepo(req.Repo); err != nil {
		return RunReport{}, err
	}
	if req.Repo == "" || req.Limit < 1 || req.Limit > 200 || req.Offset < 0 {
		return RunReport{}, failure("INVALID_REQUEST", "repo, limit 1-200 and nonnegative offset required")
	}
	if req.PolicyRevision != "" && req.PolicyRevision != "local-loop/1" {
		if err := validID(req.PolicyRevision); err != nil {
			return RunReport{}, err
		}
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return RunReport{}, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT r.receipt_id::text,COALESCE(p.revision_id::text,'local-loop/1'),r.scope,r.created_at,r.launch_claimed,b.receipt_id IS NOT NULL,o.receipt_id IS NOT NULL,
 COALESCE(b.task_class,'unknown'),COALESCE(b.binding_id,'unknown'),COALESCE(b.capability_id,'unknown'),COALESCE(b.revision,''),COALESCE(b.workspace_sha256,''),COALESCE(b.command_sha256,''),
 (SELECT count(*) FROM cairn.record_use WHERE receipt_id=r.receipt_id),
 COALESCE(o.process_state,'unknown'),o.exit_code,o.duration_ms,COALESCE(a.task_outcome,o.task_outcome,'unknown'),
 COALESCE(a.version,0),COALESCE(a.witness,'unknown'),COALESCE(a.detail->>'method',''),COALESCE(a.failure_domain,'unknown'),COALESCE(a.failure_kind,''),COALESCE(a.detail->>'error_signature_sha256',''),COALESCE(b.attempt_id::text,'')
 FROM cairn.retrieval_receipt r LEFT JOIN cairn.run_outcome o USING(receipt_id)
 LEFT JOIN cairn.retrieval_policy p USING(receipt_id)
 LEFT JOIN cairn.run_binding b USING(receipt_id)
 LEFT JOIN LATERAL (SELECT * FROM cairn.run_assessment WHERE receipt_id=r.receipt_id ORDER BY version DESC LIMIT 1) a ON true
 WHERE r.scope->>'repo'=$1 AND (r.launch_claimed OR o.receipt_id IS NOT NULL)
 AND ($4='' OR COALESCE(p.revision_id::text,'local-loop/1')=$4)
 ORDER BY r.created_at,r.receipt_id LIMIT $2 OFFSET $3`, req.Repo, req.Limit+1, req.Offset, req.PolicyRevision)
	if err != nil {
		return RunReport{}, err
	}
	report := RunReport{Rows: []RunRow{}, Interpretation: "One row per receipt with a launch claim or observed process outcome, including zero-memory runs. A launch claim reserves execution; it does not prove a process started. Latest assessments override process-derived task outcomes. Exposure rows include index pointers and do not prove delivery or benefit. Pure retrieval and binding/assessment-only receipts are outside this population. Associations do not establish causal benefit."}
	for rows.Next() {
		var row RunRow
		if err = rows.Scan(&row.ReceiptID, &row.PolicyRevision, &row.Scope, &row.CompiledAt, &row.LaunchClaimed, &row.BindingObserved, &row.OutcomeObserved, &row.TaskClass, &row.BindingID, &row.CapabilityID, &row.Revision, &row.WorkspaceSHA256, &row.CommandSHA256, &row.ExposureRows, &row.ProcessState, &row.ExitCode, &row.DurationMS, &row.TaskOutcome, &row.AssessmentVersion, &row.AssessmentWitness, &row.AssessmentMethod, &row.FailureDomain, &row.FailureKind, &row.ErrorSignature, &row.AttemptID); err != nil {
			rows.Close()
			return report, err
		}
		report.Rows = append(report.Rows, row)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return report, err
	}
	report.More = len(report.Rows) > req.Limit
	if report.More {
		report.Rows = report.Rows[:req.Limit]
	}
	report.NextOffset = req.Offset + len(report.Rows)
	return report, tx.Commit(ctx)
}
