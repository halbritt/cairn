package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

// LastHandledDeliveries returns, for each named inbox consumer with any
// handled delivery in the repository, the latest handled completion time.
// Failed and ignored deliveries are not handling. Consumers without a handled
// delivery are absent. It is an operator observation for delivery-health
// presentation, not proof of task outcome.
func (s *Store) LastHandledDeliveries(ctx context.Context, repo string, consumers []string) (map[string]time.Time, error) {
	out := map[string]time.Time{}
	if !s.channel.Operator || s.channel.Repo != "" {
		return out, failure("AUTHORITY_DENIED", "delivery activity requires an unscoped operator channel")
	}
	repo, err := s.eventScope(repo, "")
	if err != nil {
		return out, err
	}
	if len(consumers) > 256 {
		return out, failure("INVALID_REQUEST", "delivery activity accepts at most 256 consumers")
	}
	for _, c := range consumers {
		if c == "" || len(c) > 256 {
			return out, failure("INVALID_REQUEST", "invalid delivery consumer")
		}
	}
	if len(consumers) == 0 {
		return out, nil
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(context.Background())
	rows, err := tx.Query(ctx, `SELECT d.consumer,max(d.completed_at) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
 WHERE e.repo=$1 AND d.consumer=ANY($2) AND d.state='handled' GROUP BY d.consumer`, repo, consumers)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var consumer string
		var at time.Time
		if err = rows.Scan(&consumer, &at); err != nil {
			return map[string]time.Time{}, err
		}
		out[consumer] = at
	}
	if err = rows.Err(); err != nil {
		return map[string]time.Time{}, err
	}
	return out, nil
}
