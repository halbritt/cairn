// Package experimentaldebit is an isolated source candidate, not a production owner.
package experimentaldebit

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"regexp"
)

const SourceBound = 262144
const MetadataBound = 65536
const BodyBound = 1048576

var ErrUnavailable = errors.New("provider_execution_unavailable")

type refusal string

func (r refusal) Error() string { return string(r) }
func fail(code string) error    { return refusal(code) }

type Pins struct{ Request, Plan, Jobs, Companion, Mapping, Capacity, Prices, Spend, PriceBasis, Pacing string }
type Binding struct {
	Pins                                                                       Pins
	Hold, Job, Instance, Role, Model, Account, Provider, Endpoint, Tier, Route string
	Currency                                                                   string
	UnitsPerCurrency                                                           int64
	BodyBytes                                                                  int64
	BodySHA                                                                    string
}
type Balance struct{ Remaining, Committed int64 }
type Allocation struct {
	Key    string
	Amount int64
}

// Allocation meanings are qualified upstream, never inferred from a turn count.
type Debit struct {
	Spend             int64
	Counters          [5]int64
	CurrencyKeys      [2]string // global and account, distinct qualified balances
	CounterKeys       [5]string // one distinct key for each per-job counter
	Resources, Pacing []Allocation
}
type Phase string

const (
	Selected Phase = "selected"
	Armed    Phase = "armed"
	Debited  Phase = "debited"
	Consumed Phase = "consumed"
)

type Slot struct {
	Observation           Observation
	Attempt               string
	Binding               Binding
	Debit                 Debit
	NotBefore, ValidUntil int64
	Phase                 Phase
	Quarantined           bool
	Outcome               string
}
type Book struct {
	Currency                       string
	UnitsPerCurrency               int64
	Revision, Generation, Recovery int64
	Principal, Incarnation         string
	Active                         bool
	Balances                       map[string]Balance
	Slots                          map[string]Slot
}
type Identity struct {
	Principal, Incarnation string
	Generation, Recovery   int64
}
type command struct {
	Report               Observation
	Op, Slot, Attempt    string
	ExpectedRevision     int64
	Identity             Identity
	BindingSHA, DebitSHA string
}
type acknowledgment struct {
	CommandSHA, StateSHA                   string
	Before, Revision, Generation, Recovery int64
	Incarnation                            string
	Phase                                  Phase
}
type snapshot struct {
	Revision int64
	Identity Identity
	Slot     Slot
}
type Observation struct {
	Phase                                                    Phase
	Quarantined                                              bool
	Outcome                                                  string
	Failure                                                  string
	CleanupUnknown, StorageCleanupUnknown, QuarantineUnknown bool
}

var nameRE = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$`)

func name(s string) bool { return nameRE.MatchString(s) }
func hash(s string) bool {
	b, e := hex.DecodeString(s)
	return e == nil && len(b) == 32 && hex.EncodeToString(b) == s
}
func digest(v any) string {
	b, _ := json.Marshal(v)
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}
func bodyHash(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
func checkedBytes(v any, bound int) ([]byte, error) {
	b, e := json.Marshal(v)
	if e != nil {
		return nil, fail("encoding")
	}
	if len(b) > bound {
		return nil, fail("representation_bound")
	}
	return b, nil
}
func cloneBook(b Book) (Book, error) {
	if e := validateBook(b); e != nil {
		return Book{}, e
	}
	after := b
	after.Balances = make(map[string]Balance, len(b.Balances))
	after.Slots = make(map[string]Slot, len(b.Slots))
	for k, v := range b.Balances {
		after.Balances[k] = v
	}
	for k, v := range b.Slots {
		v.Debit.Resources = append([]Allocation(nil), v.Debit.Resources...)
		v.Debit.Pacing = append([]Allocation(nil), v.Debit.Pacing...)
		after.Slots[k] = v
	}
	return after, nil
}
func decodeSlot(raw []byte) (Slot, error) {
	var s Slot
	if len(raw) > SourceBound {
		return s, fail("representation_bound")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if e := d.Decode(&s); e != nil {
		return s, fail("slot_decode")
	}
	var tail any
	if e := d.Decode(&tail); e != io.EOF {
		return s, fail("slot_tail")
	}
	return s, validateSlot(s)
}
func validateSlot(s Slot) error {
	if !name(s.Attempt) || s.NotBefore < 0 || s.NotBefore >= s.ValidUntil {
		return fail("slot_shape")
	}
	if e := validateBinding(s.Binding); e != nil {
		return e
	}
	if _, e := charges(s.Debit); e != nil {
		return e
	}
	switch s.Phase {
	case Selected, Armed, Debited, Consumed:
	default:
		return fail("phase")
	}
	switch s.Outcome {
	case "", "complete", "unknown":
	default:
		return fail("outcome")
	}
	if s.Outcome != "" && s.Phase != Consumed {
		return fail("outcome_phase")
	}
	if len(s.Observation.Failure) > 64 {
		return fail("observation_shape")
	}
	_, e := checkedBytes(s, SourceBound)
	return e
}
func validateBinding(b Binding) error {
	for _, s := range []string{b.Pins.Request, b.Pins.Plan, b.Pins.Jobs, b.Pins.Companion, b.Pins.Mapping, b.Pins.Capacity, b.Pins.Prices, b.Pins.Spend, b.Pins.PriceBasis, b.Pins.Pacing, b.Route, b.BodySHA} {
		if !hash(s) {
			return fail("binding_hash")
		}
	}
	for _, s := range []string{b.Hold, b.Job, b.Instance, b.Role, b.Model, b.Account, b.Provider, b.Endpoint, b.Tier} {
		if !name(s) {
			return fail("binding_identity")
		}
	}
	switch b.Role {
	case "CURRENT", "CAND", "primary", "secondary", "selector", "control", "retry", "dispute", "auxiliary":
	default:
		return fail("binding_role")
	}
	if b.Currency != "USD" || b.UnitsPerCurrency < 1 {
		return fail("currency_shape")
	}
	if b.BodyBytes < 1 || b.BodyBytes > BodyBound {
		return fail("body_bound")
	}
	return nil
}
func charges(d Debit) (map[string]int64, error) {
	if d.Spend < 0 || d.Counters[0] < 1 || len(d.Resources) == 0 || len(d.Pacing) == 0 || len(d.Resources) > 4096 || len(d.Pacing) > 4096 {
		return nil, fail("debit_shape")
	}
	sums := map[string]int64{}
	add := func(k string, n int64) error {
		if !name(k) || n < 0 {
			return fail("allocation_shape")
		}
		if _, ok := sums[k]; ok {
			return fail("duplicate_allocation")
		}
		sums[k] = n
		return nil
	}
	for _, k := range d.CurrencyKeys {
		if e := add(k, d.Spend); e != nil {
			return nil, e
		}
	}
	for i, k := range d.CounterKeys {
		if e := add(k, d.Counters[i]); e != nil {
			return nil, e
		}
	}
	for _, group := range [][]Allocation{d.Resources, d.Pacing} {
		for _, a := range group {
			if e := add(a.Key, a.Amount); e != nil {
				return nil, e
			}
		}
	}
	return sums, nil
}
func validateBook(b Book) error {
	if b.Currency != "USD" || b.UnitsPerCurrency < 1 {
		return fail("ledger_currency")
	}
	if b.Revision < 0 || b.Generation < 0 || b.Recovery < 0 || !name(b.Principal) || !name(b.Incarnation) {
		return fail("book_shape")
	}
	for k, v := range b.Balances {
		if !name(k) || v.Remaining < 0 || v.Committed < 0 || v.Committed > math.MaxInt64-v.Remaining {
			return fail("balance_shape")
		}
	}
	seen := map[string]bool{}
	for key, s := range b.Slots {
		if !name(key) || !name(s.Attempt) || seen[s.Attempt] || s.NotBefore < 0 || s.NotBefore >= s.ValidUntil {
			return fail("slot_shape")
		}
		seen[s.Attempt] = true
		if s.Binding.Currency != b.Currency || s.Binding.UnitsPerCurrency != b.UnitsPerCurrency {
			return fail("ledger_currency_mismatch")
		}
		if e := validateSlot(s); e != nil {
			return e
		}
		amounts, e := charges(s.Debit)
		if e != nil {
			return e
		}
		for k := range amounts {
			if _, ok := b.Balances[k]; !ok {
				return fail("allocation_missing")
			}
		}
		switch s.Phase {
		case Selected, Armed, Debited, Consumed:
		default:
			return fail("phase")
		}
		switch s.Outcome {
		case "", "complete", "unknown":
		default:
			return fail("outcome")
		}
		if s.Outcome != "" && s.Phase != Consumed {
			return fail("outcome_phase")
		}
	}
	return nil
}
func identity(b Book) Identity { return Identity{b.Principal, b.Incarnation, b.Generation, b.Recovery} }
func validateCommand(b Book, c command, now int64) error {
	if c.ExpectedRevision != b.Revision || b.Revision == math.MaxInt64 {
		return fail("revision_conflict")
	}
	if c.Identity != identity(b) || !b.Active {
		return fail("sender_fenced")
	}
	s, ok := b.Slots[c.Slot]
	if !ok || s.Attempt != c.Attempt || s.Quarantined {
		return fail("slot_unavailable")
	}
	if digest(s.Binding) != c.BindingSHA || digest(s.Debit) != c.DebitSHA {
		return fail("binding_mismatch")
	}
	if now < s.NotBefore || now >= s.ValidUntil {
		return fail("allocation_expired")
	}
	return nil
}

// transition is shared by the actual SQL transaction and pure tests. No after-JSON input.
func transition(before Book, c command, now int64) (Book, acknowledgment, error) {
	var ack acknowledgment
	after, e := cloneBook(before)
	if e != nil {
		return Book{}, ack, e
	}
	if now < 0 {
		return Book{}, ack, fail("clock_unknown")
	}
	if c.Op == "recover" {
		if c.ExpectedRevision != before.Revision || before.Revision == math.MaxInt64 || before.Generation == math.MaxInt64 || c.Identity.Principal != before.Principal || c.Identity.Recovery != before.Recovery || !name(c.Identity.Incarnation) || c.Identity.Incarnation == before.Incarnation {
			return Book{}, ack, fail("recovery_conflict")
		}
		after.Generation++
		after.Incarnation = c.Identity.Incarnation
		after.Active = true
		for k, s := range after.Slots {
			s.Quarantined = true
			after.Slots[k] = s
		}
	} else if c.Op == "quarantine" {
		// A stale owner may close its own slot but cannot reactivate any generation.
		if c.Identity.Principal != before.Principal {
			return Book{}, ack, fail("owner_mismatch")
		}
		s, ok := after.Slots[c.Slot]
		if !ok || s.Attempt != c.Attempt {
			return Book{}, ack, fail("slot_unavailable")
		}
		if before.Revision == math.MaxInt64 {
			return Book{}, ack, fail("revision_bound")
		}
		s.Quarantined = true
		s.Observation = c.Report
		after.Slots[c.Slot] = s
	} else {
		if e = validateCommand(before, c, now); e != nil {
			return Book{}, ack, e
		}
		s := after.Slots[c.Slot]
		switch c.Op {
		case "arm":
			if s.Phase != Selected {
				return Book{}, ack, fail("already_armed")
			}
			s.Phase = Armed
		case "debit":
			if s.Phase != Armed {
				return Book{}, ack, fail("not_armed")
			}
			amounts, _ := charges(s.Debit)
			for key, n := range amounts {
				v := after.Balances[key]
				if v.Remaining < n || v.Committed > math.MaxInt64-n {
					return Book{}, ack, fail("allocation_insufficient")
				}
			}
			for key, n := range amounts {
				v := after.Balances[key]
				v.Remaining -= n
				v.Committed += n
				after.Balances[key] = v
			}
			s.Phase = Debited
		case "consume":
			if s.Phase != Debited {
				return Book{}, ack, fail("not_debited")
			}
			s.Phase = Consumed
		default:
			return Book{}, ack, fail("operation")
		}
		after.Slots[c.Slot] = s
	}
	after.Revision++
	if e = validateBook(after); e != nil {
		return Book{}, ack, e
	}
	phase := after.Slots[c.Slot].Phase
	ack = acknowledgment{digest(c), digest(after), before.Revision, after.Revision, after.Generation, after.Recovery, after.Incarnation, phase}
	return after, ack, nil
}
func dispatchAllowed(b Book, c command, now int64) error {
	// Consumption has already incremented the book revision; unrelated newer work is allowed.
	c.ExpectedRevision = b.Revision
	if e := validateCommand(b, c, now); e != nil {
		return e
	}
	if b.Slots[c.Slot].Phase != Consumed {
		return fail("not_consumed")
	}
	return nil
}
