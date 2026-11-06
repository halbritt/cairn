package core

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
)

// EventAdmissionRefusal contains only safe dimensions and retry guidance.
// RetryAfterSeconds is backoff guidance, never a promise of admission.
type EventAdmissionRefusal struct {
	Dimension         string `json:"dimension"`
	PolicyRevision    int64  `json:"policy_revision"`
	RetryAfterSeconds int    `json:"retry_after_seconds"`
	Retryable         bool   `json:"retryable"`
}

// No operator flag can enable an incomplete writer set.
func eventAdmissionEnforcementReady() bool { return false }

type eventAdmissionSnapshot struct {
	Consumers []string
	Policy    EventAdmissionPolicyState
	Closure   bool
}

type eventProducerIdentity struct{ Principal, Owner string }

func (s *Store) eventProducerIdentityTx(ctx context.Context, tx pgx.Tx) (eventProducerIdentity, error) {
	out := eventProducerIdentity{Principal: s.channel.Principal, Owner: s.channel.Principal}
	if s.session != nil {
		session, err := currentAgentSession(ctx, tx, s.session.Ref, s.session.Profile, s.channel.Repo, false, s.session.Destination)
		if err != nil {
			return out, err
		}
		if err = activeAgent(session); err != nil {
			return out, err
		}
		out.Owner = session.ProfileOwner
	}
	return out, nil
}
func admissionRefusal(dimension string, revision int64) *Error {
	details := &EventAdmissionRefusal{Dimension: dimension, PolicyRevision: revision, RetryAfterSeconds: 1, Retryable: true}
	guidance := "retry unchanged intent after accepted work drains"
	if dimension == "topic_recipients" {
		guidance = "topic membership or policy must change before retrying unchanged intent"
	}
	return &Error{Code: "EVENT_ADMISSION_REFUSED", Message: fmt.Sprintf("event admission dimension %s at policy revision %d; %s", dimension, revision, guidance), Admission: details}
}

// prepareEventAdmissionTx is called after source authorization, under the
// collection publication lock. A nil snapshot preserves the existing writer.
func (s *Store) prepareEventAdmissionTx(ctx context.Context, tx pgx.Tx, req PublishEventRequest, closure *validatedClosure) (*eventAdmissionSnapshot, error) {
	policy, err := admissionPolicyTx(ctx, tx, req.Repo)
	if err != nil {
		return nil, err
	}
	if policy.Policy.Mode != "enforce" {
		return nil, nil
	}
	if !eventAdmissionEnforcementReady() {
		return nil, failure("FEATURE_UNAVAILABLE", "event admission enforcement requires every delivery writer")
	}

	if closure == nil {
		generation, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return nil, err
		}
		reconciled, err := closureReconciledTx(ctx, tx, req.Repo, generation)
		if err != nil {
			return nil, err
		}
		if !reconciled {
			return nil, failure("CLOSURE_UNRECONCILED", "new event admission waits for complete closure reconciliation in the current restore generation")
		}
	}
	return s.checkEventAdmissionTx(ctx, tx, req, policy, closure)
}

// checkEventAdmissionTx owns the SQL evaluator independently of the readiness
// gate. The collection lock protects the snapshot through delivery insertion.
func (s *Store) checkEventAdmissionTx(ctx context.Context, tx pgx.Tx, req PublishEventRequest, policy EventAdmissionPolicyState, closure *validatedClosure) (*eventAdmissionSnapshot, error) {
	out := &eventAdmissionSnapshot{Policy: policy, Consumers: []string{}}
	if policy.Policy.Limits == nil {
		return nil, failure("FEATURE_UNAVAILABLE", "enforced event admission requires finite limits")
	}
	limits := policy.Policy.Limits
	identity, err := s.eventProducerIdentityTx(ctx, tx)
	if err != nil {
		return nil, err
	}
	if closure != nil {
		if req.Destination.Type != "agent" || req.Destination.Name != closure.Recipient || req.Repo != closure.Repo || identity.Principal != closure.Sender || (req.Kind != "response" && req.Kind != "failure") {
			return nil, failure("INVALID_REQUEST", "closure does not match this publication")
		}
		out.Consumers = []string{closure.Recipient}
		out.Closure = true
		return out, nil
	}
	switch req.Destination.Type {
	case "agent":
		out.Consumers = []string{req.Destination.Name}
	case "topic":
		rows, err := tx.Query(ctx, `SELECT consumer FROM cairn.agent_subscription WHERE repo=$1 AND topic=$2 AND active ORDER BY consumer LIMIT $3`, req.Repo, req.Destination.Name, limits.MaxTopicRecipients+1)
		if err != nil {
			return nil, err
		}
		for rows.Next() {
			var consumer string
			if err = rows.Scan(&consumer); err != nil {
				rows.Close()
				return nil, err
			}
			out.Consumers = append(out.Consumers, consumer)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return nil, err
		}
		if int64(len(out.Consumers)) > limits.MaxTopicRecipients {
			return nil, admissionRefusal("topic_recipients", policy.Revision)
		}
	case "pool": // The unassigned pool obligation costs one collection unit.
	default:
		return nil, failure("INVALID_REQUEST", "unsupported event admission destination")
	}
	cost := int64(len(out.Consumers))
	if req.Destination.Type == "pool" {
		cost = 1
	}
	if cost == 0 {
		return out, nil
	}
	if cost > limits.MaxCollectionOutstanding {
		return nil, admissionRefusal("collection_outstanding", policy.Revision)
	}
	// A consumed closure remains charged to its protected reserve until its
	// delivery settles. It must not also consume ordinary queue capacity.
	var collection int64
	err = tx.QueryRow(ctx, `SELECT count(*) FROM (
 SELECT 1 FROM (
 SELECT 1 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id)
 WHERE e.repo=$1 AND d.state IN ('pending','leased') AND NOT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation r WHERE r.consumed_delivery_id=d.delivery_id AND r.state='delivered')
 UNION ALL SELECT 1 FROM cairn.agent_organic_notice n WHERE n.repo=$1 AND n.state='queued'
 UNION ALL SELECT 1 FROM cairn.agent_pool_request p JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND p.delivery_id IS NULL AND p.closed_at IS NULL
 ) normal LIMIT $2
 ) bounded`, req.Repo, limits.MaxCollectionOutstanding+1).Scan(&collection)
	if err != nil {
		return nil, err
	}
	if collection+cost > limits.MaxCollectionOutstanding {
		return nil, admissionRefusal("collection_outstanding", policy.Revision)
	}
	if len(out.Consumers) == 0 {
		return out, nil
	}
	var recipientFull, pairFull bool
	err = tx.QueryRow(ctx, `SELECT COALESCE(bool_or(total.n >= $4),false),COALESCE(bool_or(pair.n >= $5),false)
 FROM unnest($2::text[]) target(consumer)
 CROSS JOIN LATERAL (
 SELECT count(*) n FROM (
 SELECT 1 FROM (
 SELECT 1 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=target.consumer AND d.state IN ('pending','leased') AND NOT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation r WHERE r.consumed_delivery_id=d.delivery_id AND r.state='delivered')
 UNION ALL SELECT 1 FROM cairn.agent_organic_notice n WHERE n.repo=$1 AND n.recipient_inbox=target.consumer AND n.state='queued'
 ) normal LIMIT $4
 ) bounded
 ) total
 CROSS JOIN LATERAL (
 SELECT count(*) n FROM (
 SELECT 1 FROM (
 SELECT 1 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=target.consumer AND e.publisher=$3 AND d.state IN ('pending','leased') AND NOT EXISTS(SELECT 1 FROM cairn.agent_closure_reservation r WHERE r.consumed_delivery_id=d.delivery_id AND r.state='delivered')
 UNION ALL SELECT 1 FROM cairn.agent_organic_notice n WHERE n.repo=$1 AND n.recipient_inbox=target.consumer AND n.publisher_principal=$3 AND n.state='queued'
 ) normal LIMIT $5
 ) bounded
 ) pair`, req.Repo, out.Consumers, identity.Principal, limits.MaxRecipientOutstanding, limits.MaxProducerRecipientOutstanding).Scan(&recipientFull, &pairFull)
	if err != nil {
		return nil, err
	}
	if recipientFull {
		return nil, admissionRefusal("recipient_outstanding", policy.Revision)
	}
	if pairFull {
		return nil, admissionRefusal("producer_recipient_outstanding", policy.Revision)
	}
	return out, nil
}

// One row per collection/dimension bounds refusal retention independently of
// attempts, correlation churn and newly registered producer sessions.
func (s *Store) retainEventAdmissionRefusal(ctx context.Context, repo string, err error) error {
	var refusal *Error
	if !errors.As(err, &refusal) || refusal.Admission == nil {
		return err
	}
	diagnosticCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Second)
	defer cancel()
	tx, recordErr := s.begin(diagnosticCtx)
	if recordErr == nil {
		defer tx.Rollback(diagnosticCtx)
		_, recordErr = tx.Exec(diagnosticCtx, `INSERT INTO cairn.agent_event_admission_refusal(repo,dimension,policy_revision,retry_after_seconds) VALUES($1,$2,$3,$4)
 ON CONFLICT(repo,dimension) DO UPDATE SET refusal_count=CASE WHEN cairn.agent_event_admission_refusal.refusal_count<9223372036854775807 THEN cairn.agent_event_admission_refusal.refusal_count+1 ELSE cairn.agent_event_admission_refusal.refusal_count END,policy_revision=EXCLUDED.policy_revision,retry_after_seconds=EXCLUDED.retry_after_seconds,last_at=clock_timestamp()`, repo, refusal.Admission.Dimension, refusal.Admission.PolicyRevision, refusal.Admission.RetryAfterSeconds)
		if recordErr == nil {
			recordErr = tx.Commit(diagnosticCtx)
		}
	}
	if recordErr != nil {
		return &Error{Code: refusal.Code, Message: refusal.Message + "; refusal diagnostic could not be retained", Admission: refusal.Admission, Cause: errors.Join(err, recordErr)}
	}
	return err
}

type EventAdmissionRefusalCount struct {
	Dimension         string    `json:"dimension"`
	PolicyRevision    int64     `json:"policy_revision"`
	RefusalCount      int64     `json:"refusal_count"`
	RetryAfterSeconds int       `json:"retry_after_seconds"`
	LastAt            time.Time `json:"last_at"`
}

func (s *Store) EventAdmissionRefusals(ctx context.Context, req EventAdmissionPolicyQuery) ([]EventAdmissionRefusalCount, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return nil, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT dimension,policy_revision,refusal_count,retry_after_seconds,last_at FROM cairn.agent_event_admission_refusal WHERE repo=$1 ORDER BY dimension`, repo)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []EventAdmissionRefusalCount{}
	for rows.Next() {
		var count EventAdmissionRefusalCount
		if err = rows.Scan(&count.Dimension, &count.PolicyRevision, &count.RefusalCount, &count.RetryAfterSeconds, &count.LastAt); err != nil {
			return nil, err
		}
		out = append(out, count)
	}
	if err = rows.Err(); err != nil {
		return nil, err
	}
	rows.Close()
	return out, tx.Commit(ctx)
}
