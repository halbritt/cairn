# Correction — producer identity, and the threat that actually motivates it

- Author: halbritt (operator)
- Date: 2026-08-25
- Corrects: `provenance-synthesis-v3.md` §3.1 (R1 asserted/observed split), and the premise it inherited from `bonded-memory-review-fable.md` §2 C3.
- Status: correction to a ratified item. §3.1 is currently pinned **mandatory before Stage 1** on a false premise; the replacement below is also mandatory, but it is a different mechanism defending a different threat.

---

## 1. The premise was wrong

v3 §3.1 states that `memory_record.producer_principal` is "a single caller-settable column." It is not, and Bonded Memory never said it was. The DDL simply did not say *who assigns it*, and the review filled the gap with the pessimistic reading.

The design intent was mechanical identification throughout: BM §5.11 states that memory never accepts an asserted capability from the caller, and BM §2 states the rule explicitly for `witness_kind` ("assigned from the ingress channel, never content"). The omission was that this was never carried into the DDL for the producer column, and BM's own class-A insert example listed `producer_principal` among the caller-supplied fields. That example is wrong and is withdrawn.

**The three consequences v3 §3.1 draws are all conditional on assertion and do not survive mechanical identification:**

| v3 §3.1 claim | Status |
|---|---|
| Agent lies about producer, then promotes; V1 passes | **Withdrawn** — V1 compares the authenticated actor against a store-assigned field |
| Agent names a legitimate promoter as producer to block them | **Withdrawn** — the field is not writable |
| Content-digest dedup keyed on an asserted field is defeatable | **Withdrawn** — same reason |

The asserted/observed split as v3 specifies it is **not required**. What replaces it is below, and it is not the same thing.

---

## 2. Environment: this is not a hostile setting, and the design should stop paying as if it were

Authentication of the producing principal will be **light**. That is a deliberate operator choice, not a gap to be closed. There is no adversary with database access, no principal attempting to evade identification, and no requirement for per-agent database roles, column-level grant matrices, or signature verification on ordinary writes.

**Design consequence, and it should be applied generally:** in a non-hostile setting with light authentication, spend the budget on making the wrong thing **visible**, not impossible. Hard gates cost throughput and get routed around — BM F2 already names promotion deadlock and the bypass flag as the most likely way this architecture dies. An eager agent is *responsive to being caught*; an adversary is not. Detection is therefore both cheaper and more effective here, and prevention should be reserved for the few places where the wrong outcome is unrecoverable.

This does **not** relax the handling of imported and untrusted content (quarantine scope, indicative-mood rewriting, class ceiling). Repository documents, web content, and native-memory imports carry injection risk regardless of whether local agents are well-intentioned.

---

## 3. The threat that actually motivates the fix: phantom completion by proxy

Neither BM nor the review named this, and it is the real one.

> `enthusiastic_model` holds a prompt to dispatch work to agent `FOO`. `FOO` fails — errors, times out, returns nothing usable. Rather than surface the failure, `enthusiastic_model` does the work itself and records the result **as if `FOO` produced it**.

Every actor here is legitimate and authenticated. Nothing is spoofed. `enthusiastic_model` is the genuine writer of the row and has every right to write it. **Mechanical identification of the writer does not detect this at all**, because the writer is correctly identified — the falsehood is in the attribution and in the silence.

This is Council's existing named failure class: `STANDING-RULES.md`, *"Phantom completion — owner: service: a member reports an external effect that did not occur; service-owned evidence, not procedure, establishes the effect."* The rule already tells us where the fix lives — service-owned evidence — and the memory subsystem is simply where it now has to be implemented.

Four things go wrong at once, in descending order of cost:

1. **The failure is erased.** `FOO` failed. That is real, useful evidence about a real problem, and it is the single most valuable thing the episode produced. It is never recorded.
2. **Capability inference is poisoned.** The record says `FOO` produced good work. Any later placement or capability reasoning about `FOO` is now wrong — the same capability-versus-binding confusion the RFP forbids, arriving by a route neither BM §16.8 nor the RFP's Scenario H covers.
3. **V1 is bypassed without anyone intending to bypass it.** V1 requires actor ≠ producer. If the record appears to come from `FOO`, `enthusiastic_model` may promote it and V1 passes. This is the live form of the concern the review reached for and mis-described.
4. **Independence is faked.** Two apparent producers where there is one. Any corroboration reasoning built later inherits the error.

---

## 4. Replacement for v3 §3.1

### 4.1 Store-assigned fields at ingest (replaces the asserted/observed split)

A defined set of columns is assigned by the store and overwritten unconditionally, whatever the caller sends:

```sql
CREATE FUNCTION memc_stamp_ingest() RETURNS trigger AS $$
BEGIN
  NEW.observed_writer := current_setting('memc.caller', false);
  NEW.written_at      := now();
  NEW.witness         := current_setting('memc.channel_witness', false)::witness_kind;
  NEW.version         := 1;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER stamp BEFORE INSERT ON memory_record
  FOR EACH ROW EXECUTE FUNCTION memc_stamp_ingest();
```

Same treatment for `authority_event.actor_id` / `occurred_at` and `record_use.used_at` / `snapshot_txid`. `txn_id` already defaults to `pg_current_xact_id()`.

**V1, V2, and the idempotency index bind to `observed_writer`.** Per-role column grants are optional hardening, not baseline — they are the kind of machinery light authentication does not warrant.

### 4.2 Ingest transport contract (new; the only place the guarantee can actually fail)

The trigger is only as good as how `memc.caller` is set.

> **The caller identity MUST arrive on the authenticated channel — set at connection establishment from whatever identity the orchestrator has verified — and MUST NOT be read from the request body.**

If the orchestrator passes the principal as a payload field that the service copies into the session, the field is caller-settable again and no schema constraint helps. Light authentication is fine; light authentication read out of the request body is not. This one line is the whole guarantee.

### 4.3 Attribution is a separate, optional, reconcilable field

Who *wrote the row* and who *did the work* are different questions. The first is mechanical. The second cannot be determined by the memory subsystem alone and must be reconciled against service-owned evidence.

```sql
ALTER TABLE memory_record
  ADD COLUMN observed_writer     text NOT NULL,   -- store-assigned; V1/V2/dedup bind here
  ADD COLUMN attributed_producer text,            -- optional; NULL means the writer did the work
  ADD COLUMN attribution_state   text NOT NULL DEFAULT 'self'
      CHECK (attribution_state IN ('self','reconciled','unreconciled','contradicted'));
```

`attributed_producer` is **testimony** whenever it is non-NULL and differs from `observed_writer`. It is never authoritative on its own and is never displayed without the observed writer beside it.

### 4.4 Delegation attempts are service-owned

The orchestrator writes a row **when it spawns the delegate** — not when the dispatcher says it did:

```sql
CREATE TABLE delegation_attempt (
  attempt_id      uuid PRIMARY KEY,
  dispatcher      text        NOT NULL,      -- store-assigned
  delegate        text        NOT NULL,
  task_id         text        NOT NULL,
  spawned_at      timestamptz NOT NULL,      -- orchestrator-written at spawn
  terminal_state  text CHECK (terminal_state IN
                  ('completed','failed','timeout','killed','no_result')),
  terminal_at     timestamptz,
  outcome_receipt uuid
);
```

Reconciliation, evaluated at write and re-evaluated when a delegation attempt reaches a terminal state:

| Condition | `attribution_state` |
|---|---|
| `attributed_producer IS NULL` | `self` — the ordinary case, no cost |
| Matching attempt for the same task, `terminal_state='completed'` | `reconciled` |
| No matching attempt exists | `unreconciled` |
| Matching attempt with `terminal_state` in (`failed`,`timeout`,`killed`,`no_result`) | **`contradicted`** |

`contradicted` is the phantom-completion signal, and it is exact: the service's own record says the delegate did not complete, while a record claims it produced the work.

### 4.5 Consequences

- **Promotion is refused** for `unreconciled` and `contradicted` records. Combined with V1 binding to `observed_writer`, attributing work elsewhere does not unlock self-promotion — it blocks promotion instead. The incentive points the right way.
- **`contradicted` goes straight to the promotion docket** as a correction candidate, ahead of the ordinary queue.
- **The suppressed failure is recovered.** When `contradicted` fires, the system already holds what was being hidden — the delegation attempt's terminal state and its outcome receipt. Materialize it as a class-A observation attributed to the orchestrator's run: *"`FOO` was dispatched for task T and terminated `failed`."* This is the strongest part of the mechanism: it does not merely block the forgery, it **recovers the evidence the forgery was suppressing**, which was the more valuable artifact all along.
- **Open-loop detection** catches the shape rather than the act, and costs one scheduled query: delegation attempts with no terminal state on tasks that have since completed. That finds the honest variant too — the dispatcher that quietly moved on without forging anything, and without escalating either.
- **Capability inference is barred** from `unreconciled` and `contradicted` records, extending BM §16.8's schema-level bar to this route.

---

## 5. Amendments to the record

1. **v3 §3.1 is withdrawn as written** and replaced by §4.1–§4.5 above. It remains mandatory before Stage 1, on a different basis: not spoofing, but phantom completion by proxy.
2. **`delegation_attempt` is added to the Stage 1 schema.** It is orchestrator-written, which makes it the first place the orchestrator/memory ownership boundary (v3 §2.5, BM §5.11) carries real weight in the other direction: memory owns grants, the orchestrator owns the fact that a delegate was actually spawned.
3. **The v3 §2.19 attack suite is reframed as a misbehavior suite.** Same fixtures, different premise: these are well-intentioned agents doing the wrong thing, not adversaries. Three cases are added:
   - Work attributed to a delegate with no `delegation_attempt` → `unreconciled`, promotion refused.
   - Work attributed to a delegate whose attempt terminated `failed` → `contradicted`, docket entry, recovered failure observation written.
   - Dispatcher completes a task with a delegation attempt left open → flagged by open-loop detection.
4. **Posture rule pinned** (§2): detection over prevention in this environment, prevention reserved for unrecoverable outcomes. Untrusted-content handling is unchanged and is not covered by this relaxation.
5. **BM's class-A insert example is corrected** to drop `producer_principal` from the caller-supplied column list.

## 6. What this does not change

Everything else in v3 stands. The four classes, purpose-gated reads, `record_use`, in-transaction grant evaluation, SERIALIZABLE on C/D, unsigned digest checkpoints, `evidence_degraded` failing closed, plain vocabulary, tombstone-with-digest deletion, and the whole adapter contract are untouched. The Stage 0/1/2 authorization request is unchanged, and the §19.2 suite — reframed per §5.3 — remains the falsifier gate.
