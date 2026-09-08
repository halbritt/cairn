package core

import (
	"context"

	"github.com/jackc/pgx/v5"
	"slices"
	"strings"
)

type RecordRelation struct {
	RecordID string `json:"record_id"`
	Version  int    `json:"version"`
	Relation string `json:"relation"`
}

func validateRelations(refs []RecordRelation) error {
	if len(refs) > 32 {
		return failure("INVALID_REQUEST", "at most 32 versioned relations per record")
	}
	seen := map[RecordRelation]bool{}
	for _, r := range refs {
		if err := validID(r.RecordID); err != nil {
			return err
		}
		if r.Version < 1 || seen[r] {
			return failure("INVALID_REQUEST", "positive versions and distinct relations required")
		}
		seen[r] = true
		switch r.Relation {
		case "derived_from", "specializes", "contradicts":
		default:
			return failure("INVALID_REQUEST", "unsupported relation; lifecycle changes need their own authority path")
		}
	}
	return nil
}
func linkRelations(ctx context.Context, tx pgx.Tx, id string, version int, draft Draft) error {
	if len(draft.Relations) == 0 {
		return nil
	}
	var destinationSensitivity string
	if err := tx.QueryRow(ctx, `SELECT sensitivity FROM cairn.memory_record WHERE record_id=$1`, id).Scan(&destinationSensitivity); err != nil {
		return err
	}

	refs := slices.Clone(draft.Relations)
	slices.SortFunc(refs, func(a, b RecordRelation) int {
		if c := strings.Compare(a.RecordID, b.RecordID); c != 0 {
			return c
		}
		if a.Version != b.Version {
			return a.Version - b.Version
		}
		return strings.Compare(a.Relation, b.Relation)
	})
	for _, ref := range refs {
		// Relation insertion changes the target's impact generation, so take its
		// lifecycle lock before inspecting its restrictions. Targets lock by ID.
		var repo, task, run, sensitivity, lifecycle string
		err := tx.QueryRow(ctx, `SELECT v.repo,v.task_id,v.run_id,m.sensitivity,m.lifecycle FROM cairn.record_version v JOIN cairn.memory_record m USING(record_id) WHERE v.record_id=$1 AND v.version=$2 FOR UPDATE OF m`, ref.RecordID, ref.Version).Scan(&repo, &task, &run, &sensitivity, &lifecycle)
		if err == pgx.ErrNoRows {
			return failure("NOT_FOUND", "referenced record version not found")
		}
		if err != nil {
			return err
		}
		if lifecycle == "tombstoned" {
			return failure("PAYLOAD_UNAVAILABLE", "cannot add a citation to forgotten content")
		}
		if repo != draft.Scope.Repo || (task != "*" && task != draft.Scope.TaskID) || (run != "*" && run != draft.Scope.RunID) || (sensitivity == "local" && destinationSensitivity == "shareable") {
			return failure("AUTHORITY_DENIED", "relation cannot broaden source scope or sensitivity")
		}
		var sourcePins *Applicability
		err = tx.QueryRow(ctx, `SELECT pins FROM cairn.record_applicability WHERE record_id=$1 AND version=$2`, ref.RecordID, ref.Version).Scan(&sourcePins)
		if err != nil && err != pgx.ErrNoRows {
			return err
		}
		if !applicabilityContains(sourcePins, draft.Pins) {
			return failure("AUTHORITY_DENIED", "relation cannot broaden source applicability")
		}
		if ref.RecordID == id && ref.Version >= version {
			return failure("INVALID_REQUEST", "relation must reference a retained earlier version")
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.record_relation(from_id,from_version,to_id,to_version,relation) VALUES($1,$2,$3,$4,$5)`, id, version, ref.RecordID, ref.Version, ref.Relation); err != nil {
			return err
		}
		// The retained target may itself depend on forgotten support. Keep that
		// restriction on new versions without erasing their reviewable content.
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.deletion_dependency(record_id,version,deletion_id)
 SELECT $1,$2,deletion_id FROM cairn.deletion_dependency WHERE record_id=$3 AND version=$4
 ON CONFLICT DO NOTHING`, id, version, ref.RecordID, ref.Version); err != nil {
			return err
		}
	}
	return nil
}
func readRelations(ctx context.Context, tx pgx.Tx, id string, version int) ([]RecordRelation, error) {
	rows, err := tx.Query(ctx, `SELECT to_id::text,to_version,relation FROM cairn.record_relation WHERE from_id=$1 AND from_version=$2 ORDER BY to_id,to_version,relation`, id, version)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(row pgx.CollectableRow) (RecordRelation, error) {
		var r RecordRelation
		err := row.Scan(&r.RecordID, &r.Version, &r.Relation)
		return r, err
	})
}

type DemoteRequest struct {
	RequestID       string `json:"request_id"`
	RecordID        string `json:"record_id"`
	ExpectedVersion int    `json:"expected_version"`
	GrantID         string `json:"grant_id"`
}

func (s *Store) Demote(ctx context.Context, req DemoteRequest) (Record, error) {
	if err := validID(req.RecordID); err != nil {
		return Record{}, err
	}
	var scope Scope
	result, err := privileged(ctx, s, "demote", req.RequestID, req, func(tx pgx.Tx) (Record, error) {
		current, err := readRecord(ctx, tx, req.RecordID)
		if err != nil {
			return Record{}, err
		}
		scope = current.Scope
		// Existing correct authority governs lowering a B claim. Demotion does not
		// issue authority and deliberately creates no C/D audit event.
		if _, err = s.authorize(ctx, tx, req.GrantID, "correct", current.Scope.Repo); err != nil {
			return Record{}, err
		}
		current, err = lockRecord(ctx, tx, req.RecordID, req.ExpectedVersion)
		if err != nil {
			return Record{}, err
		}
		if current.Class != "B" || current.Lifecycle != "active" {
			return Record{}, failure("AUTHORITY_DENIED", "demotion requires an active B record")
		}
		refs, err := dependentVersions(ctx, tx, req.RecordID)
		if err != nil {
			return Record{}, err
		}
		for _, ref := range refs {
			var instruction, conflict bool
			if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.memory_record WHERE record_id=$1 AND current_version=$2 AND class='C' AND lifecycle='active'), EXISTS(SELECT 1 FROM cairn.conflict_member m JOIN cairn.conflict_group g USING(conflict_id) WHERE m.record_id=$1 AND m.version=$2 AND g.resolved_event IS NULL)`, ref.RecordID, ref.Version).Scan(&instruction, &conflict); err != nil {
				return Record{}, err
			}
			if instruction {
				return Record{}, failure("DEPENDENCY_CONFLICT", "an active C record depends on the retained claim; resolve its citation first")
			}
			if conflict {
				return Record{}, failure("OPEN_CONFLICT", "an open conflict cites the record or a known dependent")
			}
		}
		return advanceRecord(ctx, tx, current, current.Draft, "A", "active")
	})
	if durablePolicyRefusal(err) {
		err = s.retainRefusal(ctx, req, Refusal{RequestID: req.RequestID, Operation: "demote", Scope: scope, Considered: []RecordVersionRef{{req.RecordID, req.ExpectedVersion}}, TraceComplete: false}, err)
	}
	return result, err
}

// The starting set includes every retained version, preserving citations to a
// consumed B version after later corrections. Traversal is version-qualified.
func dependentVersions(ctx context.Context, tx pgx.Tx, id string) ([]RecordVersionRef, error) {
	rows, err := tx.Query(ctx, `WITH RECURSIVE dependents(record_id,version) AS (
 SELECT record_id,version FROM cairn.record_version WHERE record_id=$1
 UNION SELECT r.from_id,r.from_version FROM cairn.record_relation r JOIN dependents d ON r.to_id=d.record_id AND r.to_version=d.version
 ) SELECT record_id::text,version FROM dependents LIMIT 1001`, id)
	if err != nil {
		return nil, err
	}
	refs, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (RecordVersionRef, error) {
		var r RecordVersionRef
		err := row.Scan(&r.RecordID, &r.Version)
		return r, err
	})
	if err != nil {
		return nil, err
	}
	if len(refs) > 1000 {
		return nil, failure("BUDGET_REFUSED", "dependency check exceeds 1000 retained versions")
	}
	return refs, nil
}

type RecordVersionRef struct {
	RecordID string `json:"record_id"`
	Version  int    `json:"version"`
}

func applicabilityContains(parent, child *Applicability) bool {
	if parent == nil {
		return true
	}
	if child == nil {
		child = &Applicability{}
	}
	for _, pair := range [][2]string{{parent.Revision, child.Revision}, {parent.WorkspaceSHA256, child.WorkspaceSHA256}, {parent.TaskClass, child.TaskClass}, {parent.BindingID, child.BindingID}, {parent.CapabilityID, child.CapabilityID}} {
		if pair[0] != "" && pair[0] != pair[1] {
			return false
		}
	}
	if parent.ValidFrom != nil && (child.ValidFrom == nil || child.ValidFrom.Before(*parent.ValidFrom)) {
		return false
	}
	if parent.ValidUntil != nil && (child.ValidUntil == nil || child.ValidUntil.After(*parent.ValidUntil)) {
		return false
	}
	return true
}
