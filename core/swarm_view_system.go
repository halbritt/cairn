package core

import (
	"context"

	"github.com/jackc/pgx/v5"
)

// swarmAnnotateSystemEvents uses the publication links written in the same
// transactions as the events. The stopped-delivery link is an exact event ID
// in the store's mutation receipt. An unavailable receipt leaves it unknown.
func swarmAnnotateSystemEvents(ctx context.Context, tx pgx.Tx, repo string, events []SwarmEvent) error {
	if len(events) == 0 {
		return nil
	}
	ids := make([]string, 0, len(events))
	positions := make(map[string]int, len(events))
	for i, event := range events {
		ids = append(ids, event.EventID)
		positions[event.EventID] = i
	}
	rows, err := tx.Query(ctx, `SELECT e.event_id::text, CASE
		WHEN EXISTS (SELECT 1 FROM cairn.agent_wait w JOIN cairn.agent_wait_outbox o USING (wait_uuid)
			WHERE w.reserved_notice_event_uuid=e.event_id AND o.notice_event_uuid=e.event_id AND o.state='published')
			THEN 'wait_notice'
		WHEN EXISTS (SELECT 1 FROM cairn.agent_overdue_checkpoint o
			WHERE o.repo=e.repo AND o.subscriber_consumer=e.destination_name AND o.notice_event_id=e.event_id AND o.state='emitted')
			THEN 'overdue_checkpoint'
		WHEN EXISTS (SELECT 1 FROM cairn.agent_schedule s WHERE s.event_id=e.event_id)
			THEN 'scheduled_event'
		WHEN e.kind='notice' AND e.causation_id IS NOT NULL AND EXISTS (SELECT 1 FROM cairn.mutation_request m
			WHERE m.caller=e.publisher AND m.operation='stopped-delivery-notify'
			AND m.response->>'notification_event_id'=e.event_id::text)
			THEN 'stopped_delivery_notice'
		ELSE '' END
	FROM cairn.agent_event e WHERE e.repo=$1 AND e.event_id=ANY($2::uuid[])`, repo, ids)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var id, system string
		if err := rows.Scan(&id, &system); err != nil {
			return err
		}
		events[positions[id]].System = system
	}
	return rows.Err()
}
