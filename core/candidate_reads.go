package core

import (
	"context"

	"github.com/jackc/pgx/v5"
)

const candidateReadChunkSize = 256

type candidateRecord struct {
	record Record
	// True only when all database-dependent checks for an A/context source
	// have empty inputs in this retrieval's repeatable-read snapshot.
	ordinaryContext bool
}

// readCandidateChunk loads every scoped source, without a relevance prefilter.
// Bounded chunks avoid per-note round trips while retaining the same snapshot
// and the compiler's existing privacy, applicability and authority decisions.
func readCandidateChunk(ctx context.Context, tx pgx.Tx, ids []string) ([]candidateRecord, error) {
	rows, err := tx.Query(ctx, `SELECT v.record_id::text,v.version,m.class,m.lifecycle,m.sensitivity,
 v.kind,v.body,v.repo,v.task_id,v.run_id,v.attributed_producer,COALESCE(v.attempt_id::text,''),v.result_ref,v.claim_type,
 v.observed_writer,v.witness,v.written_at,v.attribution_state,p.pins,e.entities,
 COALESCE((SELECT jsonb_agg(jsonb_build_object('record_id',r.to_id::text,'version',r.to_version,'relation',r.relation)
   ORDER BY r.to_id,r.to_version,r.relation) FROM cairn.record_relation r
   WHERE r.from_id=m.record_id AND r.from_version=v.version),'[]'::jsonb),
 m.class='A'
 AND NOT EXISTS(SELECT 1 FROM cairn.scope_authorization a WHERE a.record_id=m.record_id)
 AND NOT EXISTS(SELECT 1 FROM cairn.deletion_dependency d WHERE d.record_id=m.record_id AND d.version=v.version)
 AND NOT EXISTS(SELECT 1 FROM cairn.conflict_member c WHERE c.record_id=m.record_id)
 AND NOT EXISTS(SELECT 1 FROM cairn.evidence_ref f WHERE f.record_id=m.record_id AND f.version=v.version)
 FROM cairn.memory_record m
 JOIN cairn.version_attribution v ON v.record_id=m.record_id AND v.version=m.current_version
 LEFT JOIN cairn.record_applicability p ON p.record_id=m.record_id AND p.version=v.version
 LEFT JOIN cairn.record_entities e ON e.record_id=m.record_id AND e.version=v.version
 WHERE m.record_id=ANY($1::uuid[]) ORDER BY m.record_id`, ids)
	if err != nil {
		return nil, err
	}
	result, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (candidateRecord, error) {
		var item candidateRecord
		r := &item.record
		err := row.Scan(&r.RecordID, &r.Version, &r.Class, &r.Lifecycle, &r.Sensitivity,
			&r.Kind, &r.Body, &r.Scope.Repo, &r.Scope.TaskID, &r.Scope.RunID, &r.AttributedProducer,
			&r.AttemptID, &r.ResultRef, &r.ClaimType, &r.ObservedWriter, &r.Witness, &r.WrittenAt,
			&r.AttributionState, &r.Pins, &r.Entities, &r.Relations, &item.ordinaryContext)
		r.Draft.Sensitivity = r.Sensitivity
		return item, err
	})
	if err != nil {
		return nil, err
	}
	if len(result) != len(ids) {
		return nil, failure("NOT_FOUND", "candidate record not found")
	}
	return result, nil
}

func (c candidateRecord) eligible(ctx context.Context, tx pgx.Tx, purpose string, advisory bool) (Selection, string, error) {
	if c.ordinaryContext && purpose == "context" {
		return Selection{Record: c.record, Evidence: []Evidence{}, Authority: []Grant{}}, "", nil
	}
	return eligibleWithAdvisory(ctx, tx, c.record, purpose, advisory)
}
