package core

import (
	"context"

	"github.com/jackc/pgx/v5"
)

// RequeueSemanticRequest asks the running passage indexer to re-derive every
// active current version under the identity that indexer selected. It names no
// model: the queue is identity-free and the indexer embeds with its own worker.
type RequeueSemanticRequest struct {
	RequestID string `json:"request_id"`
	Reason    string `json:"reason"`
}

// SemanticRequeue reports one queueing pass. Eligible counts active current
// versions whose payload was not deleted; Queued counts jobs this pass added;
// AlreadyQueued counts eligible sources that already had a job, which is left
// as it was (lease, attempts and retry time are not reset).
type SemanticRequeue struct {
	Eligible      int64 `json:"eligible"`
	Queued        int64 `json:"queued"`
	AlreadyQueued int64 `json:"already_queued"`
}

// RequeueSemantic schedules a full re-derivation through the indexer's own job
// path. It never deletes a document: each existing document stays searchable
// until the indexer replaces it in the same transaction that publishes its
// successor. One statement inserts every missing job, so an interrupted call
// queues all or nothing, and a repeated call only adds jobs that are absent.
func (s *Store) RequeueSemantic(ctx context.Context, req RequeueSemanticRequest) (SemanticRequeue, error) {
	if !s.channel.Operator || s.channel.Repo != "" {
		return SemanticRequeue{}, failure("AUTHORITY_DENIED", "semantic requeue requires an unscoped operator channel")
	}
	if err := reasonValid(req.Reason); err != nil {
		return SemanticRequeue{}, err
	}
	return mutate(ctx, s, "semantic-requeue", req.RequestID, req, func(tx pgx.Tx) (SemanticRequeue, error) {
		var result SemanticRequeue
		err := tx.QueryRow(ctx, `WITH eligible AS MATERIALIZED (
 SELECT m.record_id FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
 WHERE m.lifecycle='active' AND v.payload_deleted_by IS NULL
), queued AS (
 INSERT INTO cairn.semantic_job(record_id) SELECT record_id FROM eligible ON CONFLICT(record_id) DO NOTHING RETURNING 1
)
SELECT (SELECT count(*) FROM eligible),(SELECT count(*) FROM queued)`).Scan(&result.Eligible, &result.Queued)
		result.AlreadyQueued = result.Eligible - result.Queued
		return result, err
	})
}
