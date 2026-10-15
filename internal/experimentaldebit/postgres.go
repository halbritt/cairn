//go:build ownerpg

package experimentaldebit

import (
	"context"
	_ "embed"
	"errors"
	"github.com/jackc/pgx/v5"
	"math"
)

// Not registered in core/schema or Store.Migrate.
//
//go:embed schema.sql
var experimentalSchema string

type beginner interface {
	BeginTx(context.Context, pgx.TxOptions) (pgx.Tx, error)
}
type postgresStore struct{ db beginner }
type pgLifecycle struct{ pgx.Tx }

func (p pgLifecycle) Rollback(ctx context.Context) error {
	e := p.Tx.Rollback(ctx)
	if errors.Is(e, pgx.ErrTxClosed) {
		return nil
	}
	return e
}
func transaction(ctx context.Context, db beginner, work func(pgx.Tx) error) error {
	tx, e := db.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.Serializable})
	if e != nil {
		return &transactionFailure{Stage: "begin_unknown"}
	}
	return finishTransaction(ctx, pgLifecycle{tx}, func() error { return work(tx) })
}
func loadMeta(ctx context.Context, tx pgx.Tx, lock string) (Book, int64, error) {
	b := Book{Balances: map[string]Balance{}, Slots: map[string]Slot{}}
	if lock != "UPDATE" && lock != "SHARE" {
		return b, 0, fail("lock_shape")
	}
	e := tx.QueryRow(ctx, `SELECT revision,generation,recovery,principal,incarnation,active,currency,units_per_currency FROM cairn_debit_experiment.ledger WHERE id=1 FOR `+lock).Scan(&b.Revision, &b.Generation, &b.Recovery, &b.Principal, &b.Incarnation, &b.Active, &b.Currency, &b.UnitsPerCurrency)
	if e != nil {
		return b, 0, &transactionFailure{Stage: "load_unknown"}
	}
	var now int64
	if e = tx.QueryRow(ctx, `SELECT floor(extract(epoch FROM clock_timestamp()))::bigint`).Scan(&now); e != nil {
		return b, 0, &transactionFailure{Stage: "clock_unknown"}
	}
	return b, now, validateBook(b)
}
func loadSlot(ctx context.Context, tx pgx.Tx, b *Book, key string) error {
	var raw []byte
	var quarantined bool
	var attempt string
	e := tx.QueryRow(ctx, `SELECT document,quarantined,attempt FROM cairn_debit_experiment.slot WHERE key=$1`, key).Scan(&raw, &quarantined, &attempt)
	if e != nil {
		return &transactionFailure{Stage: "slot_load_unknown"}
	}
	slot, e := decodeSlot(raw)
	if e != nil {
		return e
	}
	if attempt != slot.Attempt {
		return fail("attempt_corrupt")
	}
	slot.Quarantined = quarantined
	b.Slots[key] = slot
	amounts, e := charges(slot.Debit)
	if e != nil {
		return e
	}
	keys := make([]string, 0, len(amounts))
	for k := range amounts {
		keys = append(keys, k)
	}
	rows, e := tx.Query(ctx, `SELECT key,remaining,committed FROM cairn_debit_experiment.balance WHERE key=ANY($1)`, keys)
	if e != nil {
		return &transactionFailure{Stage: "balance_load_unknown"}
	}
	defer rows.Close()
	for rows.Next() {
		var key string
		var balance Balance
		if e = rows.Scan(&key, &balance.Remaining, &balance.Committed); e != nil {
			return &transactionFailure{Stage: "balance_scan_unknown"}
		}
		b.Balances[key] = balance
	}
	if rows.Err() != nil {
		return &transactionFailure{Stage: "balance_rows_unknown"}
	}
	return validateBook(*b)
}
func (p *postgresStore) read(ctx context.Context, slot string) (snap snapshot, err error) {
	err = transaction(ctx, p.db, func(tx pgx.Tx) error {
		b, _, e := loadMeta(ctx, tx, "SHARE")
		if e != nil {
			return e
		}
		if slot != "" {
			if e = loadSlot(ctx, tx, &b, slot); e != nil {
				return e
			}
		}
		snap = snapshot{b.Revision, identity(b), b.Slots[slot]}
		return nil
	})
	return
}
func save(ctx context.Context, tx pgx.Tx, b Book, c command, event any) error {
	meta, e := checkedBytes(event, MetadataBound)
	if e != nil {
		return e
	}
	if _, e = tx.Exec(ctx, `UPDATE cairn_debit_experiment.ledger SET revision=$1,generation=$2,recovery=$3,incarnation=$4,active=$5 WHERE id=1`, b.Revision, b.Generation, b.Recovery, b.Incarnation, b.Active); e != nil {
		return &transactionFailure{Stage: "write_unknown"}
	}
	if c.Op == "recover" {
		// Bulk retirement changes no identity, phase, debit, observation or balance.
		if _, e = tx.Exec(ctx, `UPDATE cairn_debit_experiment.slot SET quarantined=true,document=jsonb_set(document,'{Quarantined}','true'::jsonb)`); e != nil {
			return &transactionFailure{Stage: "retire_unknown"}
		}
	} else {
		raw, e := checkedBytes(b.Slots[c.Slot], SourceBound)
		if e != nil {
			return e
		}
		if _, e = tx.Exec(ctx, `UPDATE cairn_debit_experiment.slot SET document=$1::jsonb,quarantined=$2 WHERE key=$3`, string(raw), b.Slots[c.Slot].Quarantined, c.Slot); e != nil {
			return &transactionFailure{Stage: "slot_write_unknown"}
		}
	}
	if c.Op == "debit" {
		for key, v := range b.Balances {
			if _, e = tx.Exec(ctx, `UPDATE cairn_debit_experiment.balance SET remaining=$1,committed=$2 WHERE key=$3`, v.Remaining, v.Committed, key); e != nil {
				return &transactionFailure{Stage: "balance_write_unknown"}
			}
		}
	}
	if _, e = tx.Exec(ctx, `INSERT INTO cairn_debit_experiment.audit(revision,event) VALUES($1,$2::jsonb)`, b.Revision, string(meta)); e != nil {
		return &transactionFailure{Stage: "audit_unknown"}
	}
	return nil
}
func (p *postgresStore) apply(ctx context.Context, c command) (ack acknowledgment, err error) {
	err = transaction(ctx, p.db, func(tx pgx.Tx) error {
		b, now, e := loadMeta(ctx, tx, "UPDATE")
		if e != nil {
			return e
		}
		if c.Op != "recover" {
			if e = loadSlot(ctx, tx, &b, c.Slot); e != nil {
				return e
			}
		}
		after, a, e := transition(b, c, now)
		if e != nil {
			return e
		}
		event := struct {
			Operation, CommandSHA, StateSHA string
			Revision                        int64
		}{c.Op, a.CommandSHA, a.StateSHA, a.Revision}
		if e = save(ctx, tx, after, c, event); e != nil {
			return e
		}
		ack = a
		return nil
	})
	if err != nil {
		ack = acknowledgment{}
	}
	return
}
func (p *postgresStore) dispatch(ctx context.Context, c command, call func() error) error {
	// Healthy cooperative clients order fences behind this guard. Connection loss
	// cannot revoke remote egress, and no atomicity with a provider is claimed.
	return transaction(ctx, p.db, func(tx pgx.Tx) error {
		b, now, e := loadMeta(ctx, tx, "SHARE")
		if e != nil {
			return e
		}
		if e = loadSlot(ctx, tx, &b, c.Slot); e != nil {
			return e
		}
		if e = dispatchAllowed(b, c, now); e != nil {
			return e
		}
		return call()
	})
}
func (p *postgresStore) observe(ctx context.Context, c command, obs Observation) error {
	return transaction(ctx, p.db, func(tx pgx.Tx) error {
		b, _, e := loadMeta(ctx, tx, "UPDATE")
		if e != nil {
			return e
		}
		if e = loadSlot(ctx, tx, &b, c.Slot); e != nil {
			return e
		}
		s := b.Slots[c.Slot]
		if s.Attempt != c.Attempt || digest(s.Binding) != c.BindingSHA || digest(s.Debit) != c.DebitSHA || s.Phase != Consumed {
			return fail("observation_mismatch")
		}
		if obs.Outcome != "complete" && obs.Outcome != "unknown" {
			return fail("outcome")
		}
		if s.Outcome != "" {
			return fail("already_observed")
		}
		if b.Revision == math.MaxInt64 {
			return fail("revision_bound")
		}
		s.Outcome = obs.Outcome
		s.Observation = obs
		b.Slots[c.Slot] = s
		b.Revision++
		if e = validateBook(b); e != nil {
			return e
		}
		return save(ctx, tx, b, c, struct {
			SlotSHA     string
			Observation Observation
		}{digest(c.Slot), obs})
	})
}
func (p *postgresStore) page(ctx context.Context, after string) (page Page, err error) {
	if after != "" && !name(after) {
		return page, fail("cursor_shape")
	}
	err = transaction(ctx, p.db, func(tx pgx.Tx) error {
		// Bound both the row fetch and emitted page, never total retained history.
		rows, e := tx.Query(ctx, `SELECT key,document,quarantined FROM cairn_debit_experiment.slot WHERE key>$1 ORDER BY key LIMIT 128`, after)
		if e != nil {
			return &transactionFailure{Stage: "page_unknown"}
		}
		defer rows.Close()
		page.Next = after
		count := 0
		full := false
		for rows.Next() {
			var key string
			var raw []byte
			var quarantined bool
			if e = rows.Scan(&key, &raw, &quarantined); e != nil {
				return &transactionFailure{Stage: "page_scan_unknown"}
			}
			slot, e := decodeSlot(raw)
			if e != nil {
				return e
			}
			next, ok := appendPage(page, Retained{key, slot.Phase, quarantined, slot.Observation})
			if !ok {
				full = true
				break
			}
			page = next
			count++
		}
		if rows.Err() != nil {
			return &transactionFailure{Stage: "page_rows_unknown"}
		}
		page.Done = !full && count < 128
		if full && count == 0 {
			return fail("metadata_record_bound")
		}
		return nil
	})
	if err != nil {
		page = Page{}
	}
	return
}
