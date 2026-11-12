# Private R06 unit control process

The private `cairn-lifecycle` image exposes this finite selection command:

```sh
cairn-lifecycle unit-control inspect --plan /ABSOLUTE/plan.json --sha256 LOWERCASE_SHA256
cairn-lifecycle unit-control switch  --plan /ABSOLUTE/plan.json --sha256 LOWERCASE_SHA256
cairn-lifecycle unit-control reverse --plan /ABSOLUTE/reverse.json --sha256 LOWERCASE_SHA256
```

These are literal positional forms. There is no force, skip-drain, clear-hold,
enable, installation, refresh or service-management option. This entry is not
the public Store dispatcher and does not change public lifecycle registration.
It uses the existing coordination source profile and authenticated file host.
The whole control operation has a 30-second deadline; unknown/error/timeout
refuses. An older API without a required read is unknown.

## Pinned plan

The plan is one UTF-8 JSON object, at most 65,536 bytes, with this shape:

```json
{
  "schema": "cairn.unit-control/1",
  "current": {
    "directory": "/DISPOSABLE/unit/bindings",
    "pins": {"one.json": "CURRENT_CONFIG_SHA256"}
  },
  "next": [
    {"name": "one.json", "path": "/DISPOSABLE/release/one.json", "sha256": "NEXT_CONFIG_SHA256"}
  ],
  "retained": "/DISPOSABLE/unit/retained-old",
  "callbacks": []
}
```

Replace the digest placeholders with actual lowercase 64-character SHA256
values. Pin the complete plan bytes in the command's `--sha256`. The plan must
be owned by the current unchanged UID, mode0600, in a private mode0700 directory.
Paths must be canonical absolute paths with the existing owner-controlled,
non-symlink path checks. Fields use the exact lowercase names shown. Unknown
fields, duplicate JSON keys (including case aliases), trailing values,
unsupported schema and unbounded membership refuse. One to 32 binding names
and at most 256 callback references are accepted.

The current directory must contain exactly its pinned binding JSON files and
the optional existing `.watch.lock`. Every next member must retain the same
name, be data rather than an executable, and be an owner-controlled mode0400
file with the selected digest. The retained destination must be a distinct,
absent sibling. Both current and next configs must validate with their pinned
disabled `/2` or `/3` binding descriptors and configuration profiles. Binding,
harness, repository, coordination/memory state roots, account configuration home,
socket and token-file selector cannot silently change. Token contents are never
plan fields, output fields or inspector evidence.

`callbacks: null` and an omitted census refuse. `[]` means an explicitly
observed empty census, not discovery that every external callback has stopped.
References contain exactly integer `pid`>1, positive uint64 `start`, and
nonempty string `boot`. Invalid or additional fields refuse before API reads.
The command performs no process or process-group signaling. Full census and
native refresh remain release qualification work; no placeholder for another
author's census interface is installed here.

## Receipt and control boundary

`inspect` validates the complete plan and captures the accepted D1-D5 facts
without acquiring the watcher lock or creating a preparation. Its output is
an observation, not an activation capability. `switch` and `reverse` each
capture a new private drain receipt inside their own process and retain it in
memory through `ExchangeWatchBindings`. Serialized fingerprints, `complete`
flags or an old inspect result cannot become that receipt. The plan and next
selection are checked again after capture. The accepted exchange performs its
own immediate final D1-D5 re-read and refuses any changed or unknown fact.

Run an ordinary independent control process outside the affected fixture/unit.
The invoking native conversation must not retain an affected unresolved
attempt. No exemption, borrowed session, replacement conversation or
re-registration is provided. Every affected known native attempt/hold, including
an owning control conversation's affected attempt, remains subject to the same
read-only checks. The disposable driver includes a real active owning-attempt
negative. Unknown external association/coverage does not become a full R06
qualification claim.

The inspector never claims, renews, acknowledges, completes, cancels or settles
an inbox, and never deletes a positive grant. Owning sessions settle their own
existing contexts. Null control does not establish historical terminal close;
the exact historical read and all protected correspondence still apply. Missing,
foreign, unavailable, stale and serialization errors refuse. Lease redaction
does not establish clearance.

## Switch, reverse and reconciliation

`switch` uses the existing whole-directory exchange and hardlinks the existing
watch-lock inode across the active and retained selections. It does not start
either executor. `reverse` uses the same drain and exchange; its next files
must identify a complete retained directory, with exact member names/pins and
the same watch-lock inode as the current directory. It retains the outgoing
selection in a new absent sibling. There is no rollback bypass.

One JSON result is written to stdout with schema `cairn.unit-control-result/1`.
It includes the selected plan digest, operation, current selection and directory
identity, observed fingerprints, exchange selections/preparation, post-operation
directory/lock identities, and the exact error when one occurs. `inspected`
means a receipt was captured, not that publication succeeded.

Exit0 means the requested operation succeeded. Invalid literal syntax exits2;
other refusals/errors exit1. Always inspect `exchange.Published`. Once rename
occurred, a later durability/readback failure retains `Published: true` and
requires reconciliation. A nonzero exit, broken output delivery or leftover
preparation is never an instruction to replay effects. Preserve the plan and
both directories, compare their complete pins and lock identities, and let the
release owner reconcile. No automatic cleanup or retry occurs in the command.

This is the accepted detection/invalidation form of D6. Each fact has its own
final-read-to-rename interval, and the composite file/API read is not atomic.
Activity after a fact's last read, or leaving no retained observable, remains
a limit. It does not establish a universal prevention barrier across refresh
and readback. Full R06, unsupported refresh, unknown external coverage, grant
disposition, final predecessor/image joins and production R07 remain held.

## Disposable command proof

Use the repository's maintained host process task:

```sh
mise run host:exec --timeout 10m --weight 1 -- python3 -B scripts/check_r06_unit_control.py --evidence /NEW/PRIVATE/evidence
```

The driver reuses `check_r06_status_consumer.Fixture`, `OwnedCluster` and
`DisposableHost` without editing them. It builds and pins the actual API and
new control image, preserving normal HOME/tools/auth and creating only
application-owned state. It runs the actual command for inspect/switch/reverse,
checks protected API/state snapshots and shared lock identity, exercises strict
input/census refusals and real active attempt/hold/unavailable/stale failures.
A fixture-owned forwarding executable returns the real read responses and
injects a separately recorded actual claim during the final re-read. No
production command fault/skip option exists. That claim must invalidate the
receipt, preserve old active bytes and retain the complete preparation.
Another fixture actor moves its owned parent after the final read. The command
must preserve `Published: true` on post-rename readback failure, complete active
and retained bytes, and their shared lock identity. It must not replay or repair
the selection automatically.

This proves the command seam with synthetic fixture membership/callback census
and a real API/PostgreSQL backend. It does not run an installed native client,
qualify native refresh, discover complete production membership or grant R07.
Terminal run counts, raw commands/streams/exits, image/compiler/module pins,
RED corrections and cleanup receipts belong to the exact candidate's retained
evidence packet; this document alone is not a runtime PASS receipt.
