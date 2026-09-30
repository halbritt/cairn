package core

import (
	"context"
	"sort"
	"testing"
)

// mergeRecovery retains exactly the union of known expectations. Contradictory
// claims about one identity cannot be resolved by whichever export arrived last.
//
// The union has no count ceiling; a single exported record is bounded separately
// (see splitRecovery), so merging many sources cannot be refused for size.
func mergeRecovery(target *RecoveryRecord, source RecoveryRecord) error {
	merger := newRecoveryMerger(target)
	if err := merger.add(source); err != nil {
		return err
	}
	merger.finish()
	return nil
}

// recoveryMerger keeps the identity maps across sources so merging many retained
// sources costs one pass over each, not one rebuild of the union per source.
type recoveryMerger struct {
	target      *RecoveryRecord
	audit       map[string]string
	withdrawals map[string]RecoveryWithdrawal
	contexts    map[string]RecoveryContext
}

func newRecoveryMerger(target *RecoveryRecord) *recoveryMerger {
	m := &recoveryMerger{target: target, audit: map[string]string{}, withdrawals: map[string]RecoveryWithdrawal{}, contexts: map[string]RecoveryContext{}}
	for _, member := range target.Audit {
		m.audit[member.EventID] = member.Digest
	}
	for _, w := range target.Withdrawals {
		m.withdrawals[w.EventID] = w
	}
	for _, c := range target.Contexts {
		m.contexts[c.RecordID+":"+c.ReceiptID] = c
	}
	return m
}

func (m *recoveryMerger) add(source RecoveryRecord) error {
	if m.target.RootGrantID != source.RootGrantID {
		return failure("INTEGRITY_FAILURE", "recovery expectations have different roots")
	}
	for _, member := range source.Audit {
		if old, ok := m.audit[member.EventID]; ok {
			if old != member.Digest {
				return failure("INTEGRITY_FAILURE", "conflicting recovery audit expectations")
			}
		} else {
			m.target.Audit = append(m.target.Audit, member)
			m.audit[member.EventID] = member.Digest
		}
	}
	for _, w := range source.Withdrawals {
		if old, ok := m.withdrawals[w.EventID]; ok {
			if old != w {
				return failure("INTEGRITY_FAILURE", "conflicting recovery withdrawal expectations")
			}
		} else {
			m.target.Withdrawals = append(m.target.Withdrawals, w)
			m.withdrawals[w.EventID] = w
		}
	}
	for _, c := range source.Contexts {
		key := c.RecordID + ":" + c.ReceiptID
		if old, ok := m.contexts[key]; ok {
			if old != c {
				return failure("INTEGRITY_FAILURE", "conflicting recovery custody expectations")
			}
		} else {
			m.target.Contexts = append(m.target.Contexts, c)
			m.contexts[key] = c
		}
	}
	return nil
}

// finish restores the canonical order after all sources have been added.
func (m *recoveryMerger) finish() {
	target := m.target
	sort.Slice(target.Audit, func(i, j int) bool { return target.Audit[i].EventID < target.Audit[j].EventID })
	sort.Slice(target.Withdrawals, func(i, j int) bool { return target.Withdrawals[i].EventID < target.Withdrawals[j].EventID })
	sort.Slice(target.Contexts, func(i, j int) bool {
		a, b := target.Contexts[i], target.Contexts[j]
		if a.RecordID != b.RecordID {
			return a.RecordID < b.RecordID
		}
		return a.ReceiptID < b.ReceiptID
	})
}

// Exercise the actual staged transport with a complete synthetic union. The
// test oracle may hold all rows; the production stream may not.
func splitRecoveryStagedForTest(t *testing.T, store *Store, full RecoveryRecord) ([]RecoveryRecord, error) {
	t.Helper()
	ctx := context.Background()
	tx, err := store.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	staged, err := newRecoveryStage(ctx, tx)
	if err != nil {
		return nil, err
	}
	if staged.root != full.RootGrantID {
		return nil, failure("INTEGRITY_FAILURE", "test union root mismatch")
	}
	staged.captured = full.CapturedAt
	for _, item := range []struct {
		kind, key string
		value     any
	}{
		{"audit", "x->>'event_id'", full.Audit}, {"withdrawal", "x->>'event_id'", full.Withdrawals}, {"context", "(x->>'record_id')||':'||(x->>'receipt_id')", full.Contexts},
	} {
		if _, err = tx.Exec(ctx, `INSERT INTO pg_temp.cairn_recovery_expected SELECT $1,`+item.key+`,x FROM jsonb_array_elements($2::jsonb) x`, item.kind, item.value); err != nil {
			return nil, err
		}
	}
	var records []RecoveryRecord
	_, err = streamStagedRecovery(ctx, staged, func(record RecoveryRecord) error { records = append(records, record); return nil })
	return records, err
}
