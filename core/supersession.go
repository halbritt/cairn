package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

type SupersedeRequest struct {
	RequestID       string           `json:"request_id"`
	RecordID        string           `json:"record_id"`
	ExpectedVersion int              `json:"expected_version"`
	Replacement     RecordVersionRef `json:"replacement"`
	GrantID         string           `json:"grant_id,omitempty"`
	PreviewID       string           `json:"preview_id"`
	Reason          string           `json:"reason"`
}

type Supersession struct {
	RecordID        string           `json:"record_id"`
	PreviousVersion int              `json:"previous_version"`
	RetiredVersion  int              `json:"retired_version"`
	Replacement     RecordVersionRef `json:"replacement"`
	Actor           string           `json:"actor"`
	Reason          string           `json:"reason"`
	CreatedAt       time.Time        `json:"created_at"`
	EventID         string           `json:"event_id,omitempty"`
}

func (s *Store) Supersede(ctx context.Context, req SupersedeRequest) (Supersession, error) {
	if err := validID(req.RecordID); err != nil {
		return Supersession{}, err
	}
	if err := validID(req.Replacement.RecordID); err != nil {
		return Supersession{}, err
	}
	if err := reasonValid(req.Reason); err != nil {
		return Supersession{}, err
	}
	if req.RecordID == req.Replacement.RecordID || req.ExpectedVersion < 1 || req.Replacement.Version < 1 {
		return Supersession{}, failure("INVALID_REQUEST", "distinct records and positive expected versions required")
	}
	var scope Scope
	result, err := privileged(ctx, s, "supersede", req.RequestID, req, func(tx pgx.Tx) (Supersession, error) {
		old, err := readRecord(ctx, tx, req.RecordID)
		if err != nil {
			return Supersession{}, err
		}
		scope = old.Scope
		if err = s.checkRepo(scope.Repo); err != nil {
			return Supersession{}, err
		}
		replacement, err := readRecord(ctx, tx, req.Replacement.RecordID)
		if err != nil {
			return Supersession{}, err
		}
		if err = s.checkRepo(replacement.Scope.Repo); err != nil {
			return Supersession{}, err
		}
		if old.Lifecycle != "active" || replacement.Lifecycle != "active" {
			return Supersession{}, failure("VERSION_CONFLICT", "supersession requires two active records")
		}
		if old.Class == "C" || replacement.Class == "C" || (old.Class == "B" && replacement.Class != "B") {
			return Supersession{}, failure("AUTHORITY_DENIED", "B requires an independently qualified B replacement; C changes require retraction and issuance")
		}
		var chain []Grant
		if old.Class == "B" {
			if req.GrantID == "" {
				return Supersession{}, failure("AUTHORITY_DENIED", "B supersession requires live correct authority")
			}
			chain, err = s.authorize(ctx, tx, req.GrantID, "correct", scope.Repo)
			if err != nil {
				return Supersession{}, err
			}
		} else if req.GrantID != "" {
			return Supersession{}, failure("INVALID_REQUEST", "ordinary supersession does not use a grant")
		}
		// Grants precede subject locks, as in other authority transitions. Pin the
		// replacement's existing authority too; supersession does not issue it anew.
		if replacement.Class == "B" {
			var id string
			if err = tx.QueryRow(ctx, `SELECT grant_id::text FROM cairn.record_authority WHERE record_id=$1 AND version=$2`, replacement.RecordID, replacement.Version).Scan(&id); err != nil {
				return Supersession{}, err
			}
			if _, err = grantChain(ctx, tx, id, true); err != nil {
				return Supersession{}, err
			}
		}
		first, second := RecordVersionRef{req.RecordID, req.ExpectedVersion}, req.Replacement
		if first.RecordID > second.RecordID {
			first, second = second, first
		}
		a, err := lockRecord(ctx, tx, first.RecordID, first.Version)
		if err != nil {
			return Supersession{}, err
		}
		b, err := lockRecord(ctx, tx, second.RecordID, second.Version)
		if err != nil {
			return Supersession{}, err
		}
		old, replacement = a, b
		if old.RecordID != req.RecordID {
			old, replacement = b, a
		}
		if scope.Repo != replacement.Scope.Repo || (scope.TaskID != "*" && scope.TaskID != replacement.Scope.TaskID) || (scope.RunID != "*" && scope.RunID != replacement.Scope.RunID) || (old.Sensitivity == "local" && replacement.Sensitivity != "local") || !applicabilityContains(old.Pins, replacement.Pins) {
			return Supersession{}, failure("AUTHORITY_DENIED", "replacement cannot broaden source scope, applicability or sensitivity")
		}
		if replacement.Pins != nil {
			var now time.Time
			if err = tx.QueryRow(ctx, `SELECT clock_timestamp()`).Scan(&now); err != nil {
				return Supersession{}, err
			}
			if (replacement.Pins.ValidFrom != nil && now.Before(*replacement.Pins.ValidFrom)) || (replacement.Pins.ValidUntil != nil && !now.Before(*replacement.Pins.ValidUntil)) {
				return Supersession{}, failure("INAPPLICABLE", "replacement is outside its validity interval")
			}
		}
		purpose := "planning"
		if replacement.Class == "A" {
			purpose = "context"
		}
		_, reason, err := eligible(ctx, tx, replacement, purpose)
		if err != nil {
			return Supersession{}, err
		}
		if reason != "" {
			return Supersession{}, failure(reason, "replacement is not currently eligible")
		}
		refs, err := dependentVersions(ctx, tx, old.RecordID)
		if err != nil {
			return Supersession{}, err
		}
		var instruction, conflict bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM jsonb_to_recordset($1) d(record_id uuid,version integer) JOIN cairn.memory_record m ON m.record_id=d.record_id AND m.current_version=d.version WHERE m.class='C' AND m.lifecycle='active'), EXISTS(SELECT 1 FROM jsonb_to_recordset($1) d(record_id uuid,version integer) JOIN cairn.conflict_member c ON c.record_id=d.record_id AND c.version=d.version JOIN cairn.conflict_group g USING(conflict_id) WHERE g.resolved_event IS NULL)`, refs).Scan(&instruction, &conflict); err != nil {
			return Supersession{}, err
		}
		if conflict {
			return Supersession{}, failure("OPEN_CONFLICT", "resolve the open conflict on the source or a known dependent")
		}
		if instruction {
			return Supersession{}, failure("DEPENDENCY_CONFLICT", "revise the dependent C instruction before superseding its support")
		}
		if err = s.checkRetractionPreview(ctx, tx, RetractRequest{RecordID: req.RecordID, PreviewID: req.PreviewID}); err != nil {
			return Supersession{}, err
		}
		draft := old.Draft
		draft.Relations = nil
		next, err := advanceRecord(ctx, tx, old, draft, old.Class, "superseded")
		if err != nil {
			return Supersession{}, err
		}
		var event *string
		if old.Class == "B" {
			if err = recordAuthority(ctx, tx, "supersede", old.Version, next, chain, req.Reason, IssueRequest{}); err != nil {
				return Supersession{}, err
			}
			if err = tx.QueryRow(ctx, `SELECT event_id::text FROM cairn.record_authority WHERE record_id=$1 AND version=$2`, next.RecordID, next.Version).Scan(&event); err != nil {
				return Supersession{}, err
			}
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.record_supersession(record_id,previous_version,retired_version,replacement_id,replacement_version,event_id,reason) VALUES($1,$2,$3,$4,$5,$6,$7)`, old.RecordID, old.Version, next.Version, replacement.RecordID, replacement.Version, event, req.Reason); err != nil {
			return Supersession{}, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.supersession_affected(superseded_id,record_id,version) SELECT $1,d.record_id,d.version FROM jsonb_to_recordset($2) d(record_id uuid,version integer)`, old.RecordID, refs); err != nil {
			return Supersession{}, err
		}
		return readSupersession(ctx, tx, old.RecordID)
	})
	if durablePolicyRefusal(err) {
		err = s.retainRefusal(ctx, req, Refusal{RequestID: req.RequestID, Operation: "supersede", Scope: scope, Considered: []RecordVersionRef{{req.RecordID, req.ExpectedVersion}, req.Replacement}, TraceComplete: false}, err)
	}
	return result, err
}

func (s *Store) Supersession(ctx context.Context, id string) (Supersession, error) {
	if err := validID(id); err != nil {
		return Supersession{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return Supersession{}, err
	}
	defer tx.Rollback(ctx)
	record, err := readRecord(ctx, tx, id)
	if err != nil {
		return Supersession{}, err
	}
	if err = s.checkRepo(record.Scope.Repo); err != nil {
		return Supersession{}, err
	}
	result, err := readSupersession(ctx, tx, id)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}
func readSupersession(ctx context.Context, tx pgx.Tx, id string) (Supersession, error) {
	var result Supersession
	err := tx.QueryRow(ctx, `SELECT record_id::text,previous_version,retired_version,replacement_id::text,replacement_version,actor,reason,created_at,COALESCE(event_id::text,'') FROM cairn.record_supersession WHERE record_id=$1`, id).Scan(&result.RecordID, &result.PreviousVersion, &result.RetiredVersion, &result.Replacement.RecordID, &result.Replacement.Version, &result.Actor, &result.Reason, &result.CreatedAt, &result.EventID)
	if err == pgx.ErrNoRows {
		return result, failure("NOT_FOUND", "record has no supersession")
	}
	return result, err
}
