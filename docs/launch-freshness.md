# Fresh memory at launch

`ClaimRun`, including the authenticated `claim-run` operation, rechecks a retained
package before reserving its launch. Editing or retracting selected memory,
revoking its authority, changing its eligibility, or adding required context can
make a previously valid package stale.

The check uses the compiler's existing scope, applicability, destination,
conflict, authority and evidence rules in one serializable database snapshot.
It verifies the retained semantic seal, selected record facts and mandatory
context. For an index, it checks each indexed version and body digest against
current eligible records. Existing restore-generation, policy, payload and
observed-attempt checks still apply.

A changed selection or mandatory context returns `STALE_PACKAGE`. Binding
conflicts and unsupported required runtime enforcement retain their existing
refusal codes. Refusal does not consume a launch claim. Compile with a new
request ID to obtain current context; do not substitute that context under an
older receipt or seal.

The check does not rerank the package or insert newly optional notes. Query text
is not reconstructed from its digest. The retained bytes, seal and historical
replay remain unchanged. A full-body selection whose evidence or attribution
facts changed needs fresh compilation even if it remains eligible for advisory
use; an index continues to expose only its original pointer facts, and subsequent
pulls perform their own checks.

This establishes eligibility at the database snapshot used by the launch claim.
It cannot continuously enforce memory changes after launch or observe what a
provider reads. It uses the package's declared scope and context; the host still
owns checking its actual workspace and binding. Hosts should claim immediately
before launch. The process wrapper
refuses before starting its child when this check fails.

Launch claiming, run binding, outcome recording and dynamic retrieval association
use serializable transactions to preserve the existing mutually exclusive
execution/retrieval roles and single execution per observed host attempt. Only
explicit database serialization/deadlock aborts are retried internally, with a
bound. An ambiguous network or commit response is not retried into a launch;
use [run status](run-status.md) for inspection.

This check supports delayed native input delivery, but does not itself implement
Striatum's observation producer, staged receipt consumption or host correspondence.
Those requirements remain in the [native context direction](native-striatum-context.md).

[Verification](verification/launch-freshness-2026-09-08.md) records the reproduced
failures, race-test correction, checked behavior and installed build.
