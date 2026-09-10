package core

import (
	"context"
	"slices"
	"strings"

	"github.com/jackc/pgx/v5"
)

// AdvisoryConflict identifies competing positions without disclosing the
// potentially private opening reason or resolver context. Every named position
// must qualify together before this descriptor can enter a delivered package.
type AdvisoryConflict struct {
	ID      string             `json:"conflict_id"`
	Version int                `json:"version"`
	Members []RecordVersionRef `json:"members"`
}

// Snapshot only groups touching the scoped candidate set. Overflow leaves the
// normal dispute gate in place; an incomplete graph cannot qualify a position.
func advisoryConflictSnapshot(ctx context.Context, tx pgx.Tx, ids []string) ([]AdvisoryConflict, bool, error) {
	rows, err := tx.Query(ctx, `SELECT DISTINCT g.conflict_id::text FROM cairn.conflict_group g
 JOIN cairn.conflict_member m USING(conflict_id)
 WHERE g.resolved_event IS NULL AND m.record_id::text=ANY($1)
 ORDER BY g.conflict_id::text LIMIT 10001`, ids)
	if err != nil {
		return nil, false, err
	}
	groupIDs, err := pgx.CollectRows(rows, pgx.RowTo[string])
	if err != nil || len(groupIDs) > 10000 {
		return nil, false, err
	}
	rows, err = tx.Query(ctx, `SELECT g.conflict_id::text,g.version,m.record_id::text,m.version
 FROM cairn.conflict_group g JOIN cairn.conflict_member m USING(conflict_id)
 WHERE g.conflict_id::text=ANY($1) ORDER BY g.conflict_id,m.record_id LIMIT 160001`, groupIDs)
	if err != nil {
		return nil, false, err
	}
	defer rows.Close()
	groups := []AdvisoryConflict{}
	count := 0
	for rows.Next() {
		var id string
		var version int
		var member RecordVersionRef
		if err = rows.Scan(&id, &version, &member.RecordID, &member.Version); err != nil {
			return nil, false, err
		}
		count++
		if count > 160000 {
			return nil, false, nil
		}
		if len(groups) == 0 || groups[len(groups)-1].ID != id {
			groups = append(groups, AdvisoryConflict{ID: id, Version: version})
		}
		group := &groups[len(groups)-1]
		group.Members = append(group.Members, member)
		if len(group.Members) > 16 {
			return nil, false, nil
		}
	}
	if err = rows.Err(); err != nil {
		return nil, false, err
	}
	if len(groups) != len(groupIDs) {
		return nil, false, nil
	}
	for _, group := range groups {
		if len(group.Members) < 2 {
			return nil, false, nil
		}
	}
	return groups, true, nil
}

// Qualify connected groups before allocation. A counterpart need not match the
// query or requested label, but must pass every actual eligibility gate and
// still be the exact version whose position was disputed.
func qualifyAdvisoryCandidates(p *SemanticPackage, pool map[string]candidate, matched []candidate, groups []AdvisoryConflict, evaluations map[string]*CandidateEvaluation) []candidate {
	parents := map[string]string{}
	var root func(string) string
	root = func(id string) string {
		parent, exists := parents[id]
		if !exists {
			parents[id] = id
			return id
		}
		if parent != id {
			parents[id] = root(parent)
		}
		return parents[id]
	}
	for _, group := range groups {
		for _, member := range group.Members {
			a, b := root(group.Members[0].RecordID), root(member.RecordID)
			if a > b {
				a, b = b, a
			}
			parents[b] = a
		}
	}
	components := map[string][]AdvisoryConflict{}
	for _, group := range groups {
		key := root(group.Members[0].RecordID)
		components[key] = append(components[key], group)
	}
	targets := map[string]bool{}
	for _, c := range matched {
		targets[c.selection.Record.RecordID] = true
	}
	result := []candidate{}
	for _, c := range matched {
		if _, disputed := parents[c.selection.Record.RecordID]; !disputed {
			result = append(result, c)
		}
	}
	for _, component := range components {
		slices.SortFunc(component, func(a, b AdvisoryConflict) int { return strings.Compare(a.ID, b.ID) })
		members := map[string]bool{}
		// Keep overlapping disputes within the same bounded delivery unit as
		// an ordinary dispute. Oversized components remain omitted whole.
		valid, wanted := len(component) <= 16, false
		for _, group := range component {
			for _, ref := range group.Members {
				members[ref.RecordID] = true
				c, exists := pool[ref.RecordID]
				if !exists || c.selection.Record.Version != ref.Version || c.selection.Record.Class == "C" {
					valid = false
				}
				wanted = wanted || targets[ref.RecordID]
			}
		}
		valid = valid && len(members) <= 16
		for id := range members {
			c, exists := pool[id]
			if !exists {
				continue
			}
			e := evaluations[id]
			if !valid {
				if p.Omitted[e.Reason] > 0 {
					p.Omitted[e.Reason]--
				}
				e.Reason = "OPEN_CONFLICT"
				e.Facts = nil
				e.FailureMatch = nil
				p.Omitted[e.Reason]++
				continue
			}
			c.selection.Conflicts = component
			e.Facts.Conflicts = component
			if wanted {
				c.companion = !targets[id]
				if p.Omitted[e.Reason] > 0 {
					p.Omitted[e.Reason]--
				}
				e.Reason = ""
				result = append(result, c)
			}
		}
	}
	return result
}

func conflictKey(groups []AdvisoryConflict) string {
	if len(groups) == 0 {
		return ""
	}
	return groups[0].ID
}

// The best matching member ranks a group. Forced counterparts cannot make an
// unrelated group more relevant. Mandatory instructions remain first, then
// complete disputes before ordinary optional duplicates consume their room.
func allocationUnits(candidates []candidate) []candidate {
	sortCandidates(candidates)
	groups := map[string][]candidate{}
	for _, c := range candidates {
		if key := conflictKey(c.selection.Conflicts); key != "" {
			groups[key] = append(groups[key], c)
		}
	}
	units := []candidate{}
	seen := map[string]bool{}
	for _, c := range candidates {
		key := conflictKey(c.selection.Conflicts)
		if key == "" {
			units = append(units, c)
		} else if !seen[key] && !c.companion {
			seen[key] = true
			c.group = groups[key]
			slices.SortFunc(c.group, func(a, b candidate) int {
				return strings.Compare(a.selection.Record.RecordID, b.selection.Record.RecordID)
			})
			units = append(units, c)
		}
	}
	slices.SortStableFunc(units, func(a, b candidate) int {
		priority := func(c candidate) int {
			if c.selection.Mandatory {
				return 0
			}
			if len(c.group) > 0 {
				return 1
			}
			return 2
		}
		return priority(a) - priority(b)
	})
	return units
}

func unitMembers(c candidate) []candidate {
	if len(c.group) > 0 {
		return c.group
	}
	return []candidate{c}
}

func sameAdvisoryConflicts(a, b []AdvisoryConflict) bool {
	return slices.EqualFunc(a, b, func(x, y AdvisoryConflict) bool {
		return x.ID == y.ID && x.Version == y.Version && slices.Equal(x.Members, y.Members)
	})
}

// Historical grouping uses only facts frozen at the named receipt. Resolution
// or edits since that receipt cannot silently rewrite the original positions.
func frozenAdvisoryGroups(p SemanticPackage, evaluations map[string]*CandidateEvaluation) ([]AdvisoryConflict, error) {
	byID := map[string]AdvisoryConflict{}
	for _, e := range evaluations {
		if e.Facts == nil {
			continue
		}
		for _, group := range e.Facts.Conflicts {
			if !p.AdvisoryConflicts || validID(group.ID) != nil || group.Version < 1 || len(group.Members) < 2 || len(group.Members) > 16 {
				return nil, failure("INTEGRITY_FAILURE", "historical competing positions are invalid")
			}
			previous := ""
			for _, member := range group.Members {
				if validID(member.RecordID) != nil || member.Version < 1 || member.RecordID <= previous {
					return nil, failure("INTEGRITY_FAILURE", "historical competing positions are invalid")
				}
				previous = member.RecordID
			}
			if old, exists := byID[group.ID]; exists && !sameAdvisoryConflicts([]AdvisoryConflict{old}, []AdvisoryConflict{group}) {
				return nil, failure("INTEGRITY_FAILURE", "historical competing positions disagree")
			}
			byID[group.ID] = group
		}
	}
	groups := make([]AdvisoryConflict, 0, len(byID))
	for _, group := range byID {
		groups = append(groups, group)
	}
	slices.SortFunc(groups, func(a, b AdvisoryConflict) int { return strings.Compare(a.ID, b.ID) })
	return groups, nil
}
