# CAIRN-221 compact advance response shape, 2026-10-25

The disposable CAIRN-221 helper probe now writes a sanitized response summary
to `advance-shape.json` immediately after parsing each real `inbox compact
advance` result and before checking its shape. It retains only response keys,
schema, success/status, the type of `next`, the remainder reason, batch index
and limit, and process return code. It omits the cue text, handle, delivery and
record IDs, token paths, and stderr. The temporary report lives under the
fresh disposable PostgreSQL root.

Run: `scripts/check_native_notice_batch.py` against the fresh disposable
PostgreSQL cluster and local Cairn API created for the probe. At source
`3204805`, all four observed calls returned process code 0 and `ok: true`:

| Result | Keys and values |
|---|---|
| Successor item 2 | `schema=cairn.compact-inbox-batch/1`, `next` is a string, `batch_index=2`, `batch_limit=3` |
| Successor item 3 | `schema=cairn.compact-inbox-batch/1`, `next` is a string, `batch_index=3`, `batch_limit=3` |
| Batch limit | `schema=cairn.compact-inbox-batch/1`, `next=null`, `remainder=batch_limit` |
| Urgent preemption | `schema=cairn.compact-inbox-batch/1`, `next=null`, `remainder=priority_waiting` |

The successful payload shape is:

```json
{"schema":"cairn.compact-inbox-batch/1","ok":true,"next":"<compact cue>","batch_index":2,"batch_limit":3}
```

The terminal payload shape is:

```json
{"schema":"cairn.compact-inbox-batch/1","ok":true,"next":null,"remainder":"batch_limit"}
```

The fixture's regular assertions also confirmed the batch-limit remainder
leaves the fourth notice pending, and urgent preemption leaves both waiting
notices pending. There is no Store/helper defect indicated by this observation.
An actor continuation must branch on `next`: process the cue string as the
successor notice context, or stop when it is null and record `remainder` as the
batch stop reason. It must not index a successor attempt from the advance
payload.

Validation: the targeted provider-free disposable probe passed against its
fresh local API and PostgreSQL database. No provider, provider authentication,
OpenRouter, deployed binding, or production database was used. This establishes
the compact command response and Store/helper transitions only; it does not
establish actor continuation, accepted task outcomes, paired usage or latency,
or rollout acceptance.
