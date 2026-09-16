package core

import (
	"context"
	"errors"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type ReissueEventRequest struct {
	RequestID              string `json:"request_id"`
	Repo                   string `json:"repo"`
	DeliveryID             string `json:"delivery_id"`
	Reason                 string `json:"reason"`
	AcceptUncertainEffects bool   `json:"accept_uncertain_effects"`
}

// ReissueEvent creates a traceable new request event from a failed delivery.
// It requires an unscoped operator channel, preserves failed delivery and attempt
// history, and links the original delivery to the new event in a durable trace table.
func (s *Store) ReissueEvent(ctx context.Context, req ReissueEventRequest) (AgentEvent, error) {
	if !s.channel.Operator || s.channel.Repo != "" {
		return AgentEvent{}, failure("AUTHORITY_DENIED", "event reissue requires an unscoped operator channel")
	}
	if err := validID(req.RequestID); err != nil {
		return AgentEvent{}, err
	}
	if err := validID(req.DeliveryID); err != nil {
		return AgentEvent{}, err
	}
	if strings.TrimSpace(req.Repo) == "" || req.Repo == "*" || len(req.Repo) > 256 {
		return AgentEvent{}, failure("INVALID_REQUEST", "event collection repository required")
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return AgentEvent{}, err
	}
	if err := reasonValid(req.Reason); err != nil {
		return AgentEvent{}, err
	}
	if !req.AcceptUncertainEffects {
		return AgentEvent{}, failure("INVALID_REQUEST", "prior effects may exist; reissue requires accept_uncertain_effects=true")
	}

	return mutate(ctx, s, "event-reissue", req.RequestID, req, func(tx pgx.Tx) (AgentEvent, error) {
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return AgentEvent{}, err
		}

		var (
			deliveryID       string
			eventID          string
			consumer         string
			state            string
			repo             string
			kind             string
			recordID         string
			version          int
			sensitivity      string
			destinationType  string
			destinationName  string
			poolRequirements *PoolRequirements
		)
		err := tx.QueryRow(ctx, `SELECT d.delivery_id::text, d.event_id::text, d.consumer, d.state, e.repo, e.kind, e.record_id::text, e.version, e.sensitivity, e.destination_type, e.destination_name, e.pool_requirements FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE d.delivery_id=$1 FOR UPDATE OF d`, req.DeliveryID).Scan(
			&deliveryID, &eventID, &consumer, &state, &repo, &kind, &recordID, &version, &sensitivity, &destinationType, &destinationName, &poolRequirements,
		)
		if errors.Is(err, pgx.ErrNoRows) {
			return AgentEvent{}, failure("NOT_FOUND", "delivery not found")
		}
		if err != nil {
			return AgentEvent{}, err
		}
		if repo != req.Repo {
			return AgentEvent{}, failure("INVALID_REQUEST", "delivery does not belong to the specified repository")
		}
		if state != "failed" {
			return AgentEvent{}, failure("INVALID_REQUEST", "only failed deliveries can be reissued")
		}
		if kind != "request" {
			return AgentEvent{}, failure("INVALID_REQUEST", "only request deliveries can be reissued")
		}

		var activeAttempt bool
		err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE delivery_id=$1 AND finished_at IS NULL UNION ALL SELECT 1 FROM cairn.agent_session_attempt WHERE delivery_id=$1 AND finished_at IS NULL)`, req.DeliveryID).Scan(&activeAttempt)
		if err != nil {
			return AgentEvent{}, err
		}
		if activeAttempt {
			return AgentEvent{}, failure("VERSION_CONFLICT", "delivery has active or unfinished attempts")
		}

		var alreadyReissued bool
		err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_event_reissue WHERE delivery_id=$1)`, req.DeliveryID).Scan(&alreadyReissued)
		if err != nil {
			return AgentEvent{}, err
		}
		if alreadyReissued {
			return AgentEvent{}, failure("VERSION_CONFLICT", "delivery already reissued")
		}

		var currentSensitivity string
		err = tx.QueryRow(ctx, `SELECT m.sensitivity FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id WHERE m.record_id=$1 AND v.version=$2 AND v.repo=$3 AND m.lifecycle='active' AND v.payload_deleted_by IS NULL FOR SHARE OF m`, recordID, version, req.Repo).Scan(&currentSensitivity)
		if errors.Is(err, pgx.ErrNoRows) {
			return AgentEvent{}, failure("NOT_FOUND", "source version unavailable")
		}
		if err != nil {
			return AgentEvent{}, err
		}
		if currentSensitivity == "shareable" && sensitivity == "local" {
			return AgentEvent{}, failure("DESTINATION_PROHIBITED", "a shareable event cannot disclose a local causal parent")
		}

		destType := destinationType
		destName := destinationName
		var poolReqs *PoolRequirements
		if destinationType == "pool" {
			poolReqs = poolRequirements
			if err := s.admitPool(ctx, tx, PublishEventRequest{
				Repo:        req.Repo,
				Destination: EventDestination{Type: destType, Name: destName},
				Pool:        poolReqs,
			}, currentSensitivity); err != nil {
				return AgentEvent{}, err
			}
		} else {
			destType = "agent"
			destName = consumer
		}

		newEventID := uuid.NewString()
		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_event(event_id,repo,publisher,kind,record_id,version,sensitivity,destination_type,destination_name,causation_id,correlation_id,resolved_session,pool_requirements) VALUES($1,$2,$3,'request',$4,$5,$6,$7,$8,$9::uuid,$10::uuid,NULL,$11)`,
			newEventID, req.Repo, s.channel.Principal, recordID, version, currentSensitivity, destType, destName, eventID, req.RequestID, poolReqs,
		)
		if err != nil {
			return AgentEvent{}, err
		}

		if destType == "pool" {
			_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_pool_request(event_id) VALUES($1)`, newEventID)
		} else {
			// Direct and topic reissue deliver only to the failed consumer.
			newDeliveryID := uuid.NewString()
			_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_delivery(delivery_id,event_id,consumer) VALUES($1,$2,$3)`, newDeliveryID, newEventID, consumer)
		}
		if err != nil {
			return AgentEvent{}, err
		}

		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_event_reissue(delivery_id,event_id,reissued_by,reason) VALUES($1,$2,$3,$4)`, req.DeliveryID, newEventID, s.channel.Principal, req.Reason)
		if err != nil {
			return AgentEvent{}, err
		}

		return s.readEvent(ctx, tx, newEventID, Destination{Name: "local", AllowLocal: true})
	})
}
