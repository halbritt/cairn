package core

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type WorkFeature struct {
	FeatureName           string     `json:"feature_name"`
	Status                string     `json:"status"` // active, disabled, retired
	RetiredAt             *time.Time `json:"retired_at,omitempty"`
	UpdatedAt             time.Time  `json:"updated_at"`
	PendingCount          int        `json:"pending_count"`
	UnsafeDowngradeReason string     `json:"unsafe_downgrade_reason,omitempty"`
}

type SetWorkFeatureStatusRequest struct {
	RequestID   string `json:"request_id,omitempty"`
	FeatureName string `json:"feature_name"`
	Status      string `json:"status"` // active, disabled, retired
}

func (s *Store) ListWorkFeatures(ctx context.Context) ([]WorkFeature, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	rows, err := s.pool.Query(ctx, `SELECT feature_name, status, retired_at, updated_at FROM cairn.work_feature ORDER BY feature_name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	features := make([]WorkFeature, 0)
	for rows.Next() {
		var f WorkFeature
		if err := rows.Scan(&f.FeatureName, &f.Status, &f.RetiredAt, &f.UpdatedAt); err != nil {
			return nil, err
		}
		features = append(features, f)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for i := range features {
		count, reason, err := countRequiredWorkFeatureState(ctx, s.pool, features[i].FeatureName)
		if err != nil {
			return nil, err
		}
		features[i].PendingCount = count
		features[i].UnsafeDowngradeReason = reason
	}
	return features, nil
}

type queryRower interface {
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
}

func countRequiredWorkFeatureState(ctx context.Context, q queryRower, featureName string) (int, string, error) {
	switch featureName {
	case "work-view/1":
		var count int
		err := q.QueryRow(ctx, `SELECT CASE WHEN to_regclass('cairn.work_project') IS NOT NULL
			THEN (SELECT count(*) FROM cairn.work_project WHERE status = 'active')
			ELSE 0 END`).Scan(&count)
		if err != nil {
			return 0, "", err
		}
		if count > 0 {
			return count, fmt.Sprintf("%d active work project(s) require this feature", count), nil
		}
		return 0, "", nil

	case "work-artifacts/1":
		var tblExists bool
		err := q.QueryRow(ctx, `SELECT to_regclass('cairn.work_artifact_link') IS NOT NULL`).Scan(&tblExists)
		if err != nil {
			return 0, "", err
		}
		if tblExists {
			var count int
			err := q.QueryRow(ctx, `SELECT count(*) FROM cairn.work_artifact_link WHERE state = 'active'`).Scan(&count)
			if err != nil {
				return 0, "", err
			}
			if count > 0 {
				return count, fmt.Sprintf("%d active work artifact link(s) require this feature", count), nil
			}
		}
		var adviceActExists bool
		if err := q.QueryRow(ctx, `SELECT to_regclass('cairn.work_advice_action') IS NOT NULL`).Scan(&adviceActExists); err != nil {
			return 0, "", err
		}
		if adviceActExists {
			var unsettled int
			err := q.QueryRow(ctx, `SELECT count(*) FROM cairn.work_advice_action WHERE action_type = 'link_artifact' AND status = 'pending'`).Scan(&unsettled)
			if err != nil {
				return 0, "", err
			}
			if unsettled > 0 {
				return unsettled, fmt.Sprintf("%d unsettled link_artifact advice action(s) require this feature", unsettled), nil
			}
		}
		return 0, "", nil

	case "coordination-waits/1":
		var waitExists, outboxExists bool
		if err := q.QueryRow(ctx, `SELECT to_regclass('cairn.agent_wait') IS NOT NULL`).Scan(&waitExists); err != nil {
			return 0, "", err
		}
		if err := q.QueryRow(ctx, `SELECT to_regclass('cairn.agent_wait_outbox') IS NOT NULL`).Scan(&outboxExists); err != nil {
			return 0, "", err
		}
		var count int
		if waitExists {
			var c int
			if err := q.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_wait WHERE state = 'waiting'`).Scan(&c); err != nil {
				return 0, "", err
			}
			count += c
		}
		if outboxExists {
			var c int
			if err := q.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_wait_outbox WHERE state = 'pending'`).Scan(&c); err != nil {
				return 0, "", err
			}
			count += c
		}
		if count > 0 {
			return count, fmt.Sprintf("%d pending coordination wait(s) require this feature", count), nil
		}
		return 0, "", nil

	case "next-turn-notices/1":
		var count int
		var tblExists bool
		err := q.QueryRow(ctx, `SELECT to_regclass('cairn.agent_organic_notice') IS NOT NULL`).Scan(&tblExists)
		if err != nil {
			return 0, "", err
		}
		if tblExists {
			var c int
			err := q.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_organic_notice n LEFT JOIN cairn.agent_delivery d ON d.delivery_id = n.reserved_delivery_id WHERE n.state = 'queued' OR (n.state IN ('admitted', 'flushed') AND (d.delivery_id IS NULL OR d.state IN ('pending', 'leased')))`).Scan(&c)
			if err != nil {
				return 0, "", err
			}
			count += c
		}

		var hasAdmissionKind bool
		err = q.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = 'cairn' AND table_name = 'agent_session_attempt' AND column_name = 'admission_kind')`).Scan(&hasAdmissionKind)
		if err != nil {
			return 0, "", err
		}
		if hasAdmissionKind {
			var c int
			err := q.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_session_attempt WHERE admission_kind = 'organic' AND finished_at IS NULL`).Scan(&c)
			if err != nil {
				return 0, "", err
			}
			count += c
		}

		if count > 0 {
			return count, fmt.Sprintf("%d pending next turn notice(s) require this feature", count), nil
		}
		return 0, "", nil

	case "work-advice/1":
		var jobExists, propExists, actExists bool
		if err := q.QueryRow(ctx, `SELECT to_regclass('cairn.work_advice_job') IS NOT NULL`).Scan(&jobExists); err != nil {
			return 0, "", err
		}
		if err := q.QueryRow(ctx, `SELECT to_regclass('cairn.work_advice_proposal') IS NOT NULL`).Scan(&propExists); err != nil {
			return 0, "", err
		}
		if err := q.QueryRow(ctx, `SELECT to_regclass('cairn.work_advice_action') IS NOT NULL`).Scan(&actExists); err != nil {
			return 0, "", err
		}
		var count int
		if jobExists {
			var c int
			if err := q.QueryRow(ctx, `SELECT count(*) FROM cairn.work_advice_job WHERE status IN ('pending', 'running', 'cleanup_unconfirmed', 'uncertain')`).Scan(&c); err != nil {
				return 0, "", err
			}
			count += c
		}
		if propExists && actExists {
			var c int
			if err := q.QueryRow(ctx, `
				SELECT count(*)
				FROM cairn.work_advice_proposal p
				WHERE p.status IN ('pending', 'partially_applied')
				  AND EXISTS (
				      SELECT 1 FROM cairn.work_advice_action a
				      WHERE a.proposal_uuid = p.proposal_uuid AND a.status = 'pending'
				  )
			`).Scan(&c); err != nil {
				return 0, "", err
			}
			count += c
		} else if propExists {
			var c int
			if err := q.QueryRow(ctx, `SELECT count(*) FROM cairn.work_advice_proposal WHERE status IN ('pending', 'partially_applied')`).Scan(&c); err != nil {
				return 0, "", err
			}
			count += c
		}
		if count > 0 {
			return count, fmt.Sprintf("%d in-flight work advice job/proposal(s) require this feature", count), nil
		}
		return 0, "", nil

	default:
		return 0, "", nil
	}
}

func (s *Store) GetWorkFeatureStatus(ctx context.Context, featureName string) (string, error) {
	if featureName == "" {
		featureName = "work-view/1"
	}
	var status string
	err := s.pool.QueryRow(ctx, `SELECT status FROM cairn.work_feature WHERE feature_name = $1`, featureName).Scan(&status)
	if errors.Is(err, pgx.ErrNoRows) {
		return "disabled", nil
	}
	if err != nil {
		return "", err
	}
	return status, nil
}

func (s *Store) getWorkFeatureStatusTx(ctx context.Context, tx pgx.Tx, featureName string) (string, error) {
	if featureName == "" {
		featureName = "work-view/1"
	}
	var status string
	err := tx.QueryRow(ctx, `SELECT status FROM cairn.work_feature WHERE feature_name = $1`, featureName).Scan(&status)
	if errors.Is(err, pgx.ErrNoRows) {
		return "disabled", nil
	}
	if err != nil {
		return "", err
	}
	return status, nil
}

func (s *Store) SetWorkFeatureStatus(ctx context.Context, req SetWorkFeatureStatusRequest) error {
	if err := s.requireOperator(); err != nil {
		return err
	}
	if req.FeatureName == "" {
		return failure("INVALID_REQUEST", "feature_name is required")
	}
	policy := s.effectivePolicy()
	if !policy.SupportedWorkFeatures[req.FeatureName] {
		return failure("INVALID_REQUEST", fmt.Sprintf("unsupported work feature %q", req.FeatureName))
	}
	if req.Status != "active" && req.Status != "disabled" && req.Status != "retired" {
		return failure("INVALID_REQUEST", fmt.Sprintf("invalid feature status %q: must be active, disabled, or retired", req.Status))
	}
	if req.RequestID == "" {
		req.RequestID = uuid.NewString()
	}
	_, err := mutate(ctx, s, "work-feature-set", req.RequestID, req, func(tx pgx.Tx) (any, error) {
		if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended('work-feature:' || $1, 0))`, req.FeatureName); err != nil {
			return nil, err
		}
		var current string
		err := tx.QueryRow(ctx, `SELECT status FROM cairn.work_feature WHERE feature_name = $1 FOR UPDATE`, req.FeatureName).Scan(&current)
		if errors.Is(err, pgx.ErrNoRows) {
			current = "disabled"
		} else if err != nil {
			return nil, err
		}
		if current == "retired" && req.Status != "retired" {
			return nil, failure("FEATURE_RETIRED", fmt.Sprintf("feature %q is already retired and cannot be reactivated", req.FeatureName))
		}
		if req.Status == "retired" {
			if req.FeatureName == "work-artifacts/1" {
				if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock_shared(hashtextextended('work-feature:work-advice/1', 0))`); err != nil {
					return nil, err
				}
				adviceStatus, err := s.getWorkFeatureStatusForShareTx(ctx, tx, "work-advice/1")
				if err != nil {
					return nil, err
				}
				if adviceStatus == "active" {
					return nil, failure("INVALID_REQUEST", "cannot retire work-artifacts/1 while work-advice/1 is active: retire dependent advice first")
				}
			}
			count, reason, err := countRequiredWorkFeatureState(ctx, tx, req.FeatureName)
			if err != nil {
				return nil, err
			}
			if count > 0 {
				return nil, failure("INVALID_REQUEST", fmt.Sprintf("cannot retire feature %q: %s", req.FeatureName, reason))
			}
		}
		var retiredAt *time.Time
		if req.Status == "retired" {
			now := time.Now()
			retiredAt = &now
		}
		_, err = tx.Exec(ctx, `INSERT INTO cairn.work_feature (feature_name, status, retired_at, updated_at)
			VALUES ($1, $2, $3, clock_timestamp())
			ON CONFLICT (feature_name) DO UPDATE
			SET status = EXCLUDED.status,
			    retired_at = CASE WHEN EXCLUDED.status = 'retired' THEN clock_timestamp() ELSE cairn.work_feature.retired_at END,
			    updated_at = clock_timestamp()`, req.FeatureName, req.Status, retiredAt)
		return nil, err
	})
	return err
}

func (s *Store) getWorkFeatureStatusForShareTx(ctx context.Context, tx pgx.Tx, featureName string) (string, error) {
	if featureName == "" {
		featureName = "work-view/1"
	}
	var status string
	err := tx.QueryRow(ctx, `SELECT status FROM cairn.work_feature WHERE feature_name = $1 FOR SHARE`, featureName).Scan(&status)
	if errors.Is(err, pgx.ErrNoRows) {
		return "disabled", nil
	}
	if err != nil {
		return "", err
	}
	return status, nil
}

func (s *Store) lockAndCheckFeatureAdmissionTx(ctx context.Context, tx pgx.Tx, featureName string) error {
	if err := s.checkWorkSchemaFloorTx(ctx, tx); err != nil {
		return err
	}
	if featureName == "" {
		featureName = "work-view/1"
	}
	if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock_shared(hashtextextended('work-feature:' || $1, 0))`, featureName); err != nil {
		return err
	}
	status, err := s.getWorkFeatureStatusForShareTx(ctx, tx, featureName)
	if err != nil {
		return err
	}
	if status == "retired" {
		return failure("FEATURE_RETIRED", fmt.Sprintf("%s feature is retired", featureName))
	}
	if status != "active" {
		return failure("FEATURE_DISABLED", fmt.Sprintf("%s feature is disabled", featureName))
	}
	return nil
}

func (s *Store) checkWorkAdmissionTx(ctx context.Context, tx pgx.Tx) error {
	return s.lockAndCheckFeatureAdmissionTx(ctx, tx, "work-view/1")
}

func (s *Store) checkWorkReadableTx(ctx context.Context, tx pgx.Tx) error {
	return s.checkWorkSchemaFloorTx(ctx, tx)
}

func (s *Store) checkWorkFeatureTx(ctx context.Context, tx pgx.Tx) error {
	return s.checkWorkAdmissionTx(ctx, tx)
}

func (s *Store) checkFeatureEnabledTx(ctx context.Context, tx pgx.Tx, featureName string) error {
	return s.lockAndCheckFeatureAdmissionTx(ctx, tx, featureName)
}

func (s *Store) checkWorkArtifactFeatureTx(ctx context.Context, tx pgx.Tx) error {
	return s.lockAndCheckFeatureAdmissionTx(ctx, tx, "work-artifacts/1")
}

func (s *Store) checkWorkAdviceFeatureTx(ctx context.Context, tx pgx.Tx) error {
	return s.lockAndCheckFeatureAdmissionTx(ctx, tx, "work-advice/1")
}
