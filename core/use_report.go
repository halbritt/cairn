package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

type UseReportRequest struct {
	Repo     string `json:"repo"`
	RecordID string `json:"record_id,omitempty"`
	TaskID   string `json:"task_id,omitempty"`
	RunID    string `json:"run_id,omitempty"`
	Limit    int    `json:"limit"`
	Offset   int    `json:"offset"`
}
type UseRow struct {
	RunReceiptID      string    `json:"run_receipt_id,omitempty"`
	RunLinkObserver   string    `json:"run_link_observer,omitempty"`
	RunLinkMethod     string    `json:"run_link_method,omitempty"`
	ExposureKind      string    `json:"exposure_kind"`
	UsageWitness      string    `json:"usage_witness"`
	UsageMethod       string    `json:"usage_method"`
	UsageCoverage     string    `json:"usage_coverage"`
	ExpansionObserved bool      `json:"expansion_observed"`
	TaskClass         string    `json:"task_class"`
	BindingID         string    `json:"binding_id"`
	CapabilityID      string    `json:"capability_id"`
	AssessmentVersion int       `json:"assessment_version"`
	AssessmentWitness string    `json:"assessment_witness"`
	FailureDomain     string    `json:"failure_domain"`
	FailureKind       string    `json:"failure_kind"`
	ErrorSignature    string    `json:"error_signature_sha256"`
	ReceiptID         string    `json:"receipt_id"`
	RecordID          string    `json:"record_id"`
	Version           int       `json:"version"`
	Scope             Scope     `json:"scope"`
	Purpose           string    `json:"purpose"`
	UsedAt            time.Time `json:"used_at"`
	Delivery          string    `json:"best_observed_delivery"`
	Usage             string    `json:"usage"`
	ProcessState      string    `json:"process_state"`
	ExitCode          *int      `json:"exit_code"`
	DurationMS        *int64    `json:"duration_ms"`
	TaskOutcome       string    `json:"task_outcome"`
	CurrentVersion    int       `json:"current_version"`
	CurrentLifecycle  string    `json:"current_lifecycle"`
}
type UseReport struct {
	Rows           []UseRow `json:"rows"`
	More           bool     `json:"more"`
	NextOffset     int      `json:"next_offset"`
	Interpretation string   `json:"interpretation"`
}

// One row is one exposed record version in one retrieval. Aggregate each
// observation stream before joining, so repeated delivery/citation observations
// cannot multiply the unit of analysis. Absence of H0 use telemetry is unknown.
func (s *Store) UseReport(ctx context.Context, req UseReportRequest) (UseReport, error) {
	if req.Repo == "" || req.Limit < 1 || req.Limit > 200 || req.Offset < 0 {
		return UseReport{}, failure("INVALID_REQUEST", "repo, limit 1-200 and nonnegative offset required")
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return UseReport{}, err
	}
	if req.RecordID != "" {
		if err := validID(req.RecordID); err != nil {
			return UseReport{}, err
		}
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return UseReport{}, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT u.receipt_id::text,u.record_id::text,u.version,r.scope,u.purpose,u.used_at,
 CASE WHEN d.contact THEN 'delivered' WHEN d.available THEN 'available' WHEN d.failed THEN 'failed' ELSE 'unknown' END,
 COALESCE(g.signal,CASE WHEN d.contact AND c.coverage='complete' THEN 'delivered_only' ELSE 'unknown' END),
 COALESCE(o.process_state,'unknown'),o.exit_code,o.duration_ms,COALESCE(a.task_outcome,o.task_outcome,'unknown'),m.current_version,m.lifecycle,COALESCE(b.task_class,'unknown'),COALESCE(b.binding_id,'unknown'),COALESCE(b.capability_id,'unknown'),COALESCE(a.version,0),COALESCE(a.witness,'unknown'),COALESCE(a.failure_domain,'unknown'),COALESCE(a.failure_kind,''),COALESCE(a.detail->>'error_signature_sha256',''),COALESCE(g.witness,'unknown'),COALESCE(g.method,''),COALESCE(c.coverage,'unknown'),u.exposure_kind,COALESCE(rr.run_receipt_id::text,''),COALESCE(rr.observer,''),COALESCE(rr.method,''),COALESCE(g.expansion_observed,false)
 FROM cairn.record_use u JOIN cairn.retrieval_receipt r USING(receipt_id)
 JOIN cairn.memory_record m ON m.record_id=u.record_id
 LEFT JOIN cairn.run_retrieval rr ON rr.retrieval_receipt_id=u.receipt_id
 LEFT JOIN cairn.run_outcome o ON o.receipt_id=COALESCE(rr.run_receipt_id,u.receipt_id)
 LEFT JOIN cairn.run_binding b ON b.receipt_id=COALESCE(rr.run_receipt_id,u.receipt_id)
 LEFT JOIN LATERAL (SELECT * FROM cairn.run_assessment WHERE receipt_id=COALESCE(rr.run_receipt_id,u.receipt_id) ORDER BY version DESC LIMIT 1) a ON true
 LEFT JOIN LATERAL (SELECT bool_or(assurance='delivered') contact,bool_or(assurance='available') available,bool_or(assurance='failed') failed FROM cairn.delivery_receipt WHERE receipt_id=u.receipt_id) d ON true
 LEFT JOIN LATERAL (SELECT signal,witness,method,bool_or(signal='expanded' AND witness='instrumented') OVER () AS expansion_observed FROM cairn.usage_observation WHERE receipt_id=u.receipt_id AND record_id=u.record_id AND version=u.version ORDER BY CASE signal WHEN 'cited' THEN 3 WHEN 'expanded' THEN 2 ELSE 1 END DESC,observed_at DESC,observation_id DESC LIMIT 1) g ON true
 LEFT JOIN LATERAL (SELECT coverage FROM cairn.usage_coverage WHERE receipt_id=u.receipt_id ORDER BY sequence DESC LIMIT 1) c ON true
 WHERE r.scope->>'repo'=$1 AND ($2='' OR u.record_id::text=$2)
 AND ($5='' OR r.scope->>'task_id'=$5) AND ($6='' OR r.scope->>'run_id'=$6)
 ORDER BY u.used_at,u.receipt_id,u.record_id,u.version LIMIT $3 OFFSET $4`, req.Repo, req.RecordID, req.Limit+1, req.Offset, req.TaskID, req.RunID)
	if err != nil {
		return UseReport{}, err
	}
	report := UseReport{Rows: []UseRow{}, Interpretation: "One row per exposed record/version/receipt. Repeated observations do not multiply rows. Explicit host-linked retrievals retain their own exposure and usage but join the host run outcome and latest assessment. Exit zero is not acceptance; citations are testimony, inferred use is not citation, and missing telemetry is unknown. Associations do not establish causal benefit."}
	for rows.Next() {
		var row UseRow
		if err = rows.Scan(&row.ReceiptID, &row.RecordID, &row.Version, &row.Scope, &row.Purpose, &row.UsedAt, &row.Delivery, &row.Usage, &row.ProcessState, &row.ExitCode, &row.DurationMS, &row.TaskOutcome, &row.CurrentVersion, &row.CurrentLifecycle, &row.TaskClass, &row.BindingID, &row.CapabilityID, &row.AssessmentVersion, &row.AssessmentWitness, &row.FailureDomain, &row.FailureKind, &row.ErrorSignature, &row.UsageWitness, &row.UsageMethod, &row.UsageCoverage, &row.ExposureKind, &row.RunReceiptID, &row.RunLinkObserver, &row.RunLinkMethod, &row.ExpansionObserved); err != nil {
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
