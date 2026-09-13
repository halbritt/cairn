package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

type EvidenceImpactRequest struct {
	EvidenceID   string `json:"evidence_id"`
	RecordOffset int    `json:"record_offset"`
	UseOffset    int    `json:"use_offset"`
}

type EvidenceDependent struct {
	RecordVersionRef
	Via              []RecordRelation `json:"via,omitempty"`
	Class            string           `json:"version_class"`
	Direct           bool             `json:"direct_evidence_reference"`
	CurrentVersion   int              `json:"current_version"`
	CurrentLifecycle string           `json:"current_lifecycle"`
}

type EvidenceImpactUse struct {
	Impact
	ExposureKind string `json:"exposure_kind"`
}

type EvidenceImpact struct {
	Evidence         Evidence            `json:"evidence"`
	Records          []EvidenceDependent `json:"records"`
	RecordsTruncated bool                `json:"records_truncated"`
	NextRecordOffset int                 `json:"next_record_offset"`
	Uses             []EvidenceImpactUse `json:"uses"`
	UsesTruncated    bool                `json:"uses_truncated"`
	NextUseOffset    int                 `json:"next_use_offset"`
	Coverage         string              `json:"coverage"`
}

// UNION deduplicates versions reached through multiple explicit paths. A newer
// version is included only if it carries its own evidence or relation link.
const evidenceDependents = `WITH RECURSIVE affected(record_id,version) AS (
 SELECT e.record_id,e.version FROM cairn.evidence_ref e
 JOIN cairn.record_version v ON v.record_id=e.record_id AND v.version=e.version
 WHERE e.evidence_id=$1 AND v.repo=$2
 UNION
 SELECT r.from_id,r.from_version FROM cairn.record_relation r
 JOIN affected a ON r.to_id=a.record_id AND r.to_version=a.version
 JOIN cairn.record_version v ON v.record_id=r.from_id AND v.version=r.from_version
 WHERE v.repo=$2
) `

// InspectEvidenceImpact is protected local inspection, not a mutation preview or
// qualification decision. Pages share one snapshot per call, not across calls.
func (s *Store) InspectEvidenceImpact(ctx context.Context, req EvidenceImpactRequest) (EvidenceImpact, error) {
	if err := validID(req.EvidenceID); err != nil {
		return EvidenceImpact{}, err
	}
	if req.RecordOffset < 0 || req.UseOffset < 0 {
		return EvidenceImpact{}, failure("INVALID_REQUEST", "nonnegative record and use offsets required")
	}
	// Output pagination does not bound recursive traversal work. Apply the API's
	// deadline to direct core/CLI callers too; a timeout returns no partial report.
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return EvidenceImpact{}, err
	}
	defer tx.Rollback(ctx)
	doc, err := s.readEvidenceTx(ctx, tx, req.EvidenceID)
	if err != nil {
		return EvidenceImpact{}, err
	}
	result := EvidenceImpact{Evidence: doc.Evidence, Records: []EvidenceDependent{}, Uses: []EvidenceImpactUse{}, Coverage: "Direct evidence references and transitive explicit versioned relations, including historical versions. Uses are recorded exposures, not causal influence or proof of execution. Source labels, replacement alone and undeclared derivations add no edges. Each page reads current state; concurrent changes can shift offsets."}
	const pageSize = 100
	rows, err := tx.Query(ctx, evidenceDependents+`SELECT a.record_id::text,a.version,v.version_class,
 EXISTS(SELECT 1 FROM cairn.evidence_ref e WHERE e.evidence_id=$1 AND e.record_id=a.record_id AND e.version=a.version),
 m.current_version,m.lifecycle,
 COALESCE((SELECT jsonb_agg(jsonb_build_object('record_id',link.to_id,'version',link.to_version,'relation',link.relation)
 ORDER BY link.to_id,link.to_version,link.relation) FROM cairn.record_relation link
 JOIN affected parent ON parent.record_id=link.to_id AND parent.version=link.to_version
 WHERE link.from_id=a.record_id AND link.from_version=a.version),'[]'::jsonb)
 FROM affected a
 JOIN cairn.record_version v USING(record_id,version)
 JOIN cairn.memory_record m USING(record_id)
 ORDER BY a.record_id,a.version LIMIT 101 OFFSET $3`, req.EvidenceID, doc.Repo, req.RecordOffset)
	if err != nil {
		return EvidenceImpact{}, err
	}
	result.Records, err = pgx.CollectRows(rows, func(row pgx.CollectableRow) (EvidenceDependent, error) {
		var r EvidenceDependent
		err := row.Scan(&r.RecordID, &r.Version, &r.Class, &r.Direct, &r.CurrentVersion, &r.CurrentLifecycle, &r.Via)
		return r, err
	})
	if err != nil {
		return EvidenceImpact{}, err
	}
	result.RecordsTruncated = len(result.Records) > pageSize
	if result.RecordsTruncated {
		result.Records = result.Records[:pageSize]
	}
	result.NextRecordOffset = req.RecordOffset + len(result.Records)
	rows, err = tx.Query(ctx, evidenceDependents+`SELECT u.record_id::text,u.receipt_id::text,u.version,u.purpose,r.scope,u.exposure_kind
 FROM affected a JOIN cairn.record_use u USING(record_id,version)
 JOIN cairn.retrieval_receipt r USING(receipt_id)
 ORDER BY u.used_at,u.receipt_id,u.record_id,u.version LIMIT 101 OFFSET $3`, req.EvidenceID, doc.Repo, req.UseOffset)
	if err != nil {
		return EvidenceImpact{}, err
	}
	result.Uses, err = pgx.CollectRows(rows, func(row pgx.CollectableRow) (EvidenceImpactUse, error) {
		var use EvidenceImpactUse
		err := row.Scan(&use.RecordID, &use.ReceiptID, &use.Version, &use.Purpose, &use.Scope, &use.ExposureKind)
		return use, err
	})
	if err != nil {
		return EvidenceImpact{}, err
	}
	result.UsesTruncated = len(result.Uses) > pageSize
	if result.UsesTruncated {
		result.Uses = result.Uses[:pageSize]
	}
	result.NextUseOffset = req.UseOffset + len(result.Uses)
	return result, tx.Commit(ctx)
}
