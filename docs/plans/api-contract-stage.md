# Multi-machine API contract, compatibility and latency

Status: implemented and locally accepted; see the
[integrated verification record](../verification/api-contract-stage.md) for
commands, results and measurement limits. Baseline: `0fb09c3` (the deployed
runtime is `b46a0e1`; the later baseline commits document that release).

## Goal

Make Cairn's multi-machine API explicitly specified, safely evolvable and
measurably responsive while preserving authentication, durability and retry
guarantees. Keep enrollment and authentication simple. A protocol migration
must earn its cost through measured improvements.

## Work and ownership

| Work | Owner | Required result |
| --- | --- | --- |
| Formal contract | Agent 200 | Machine-readable operations, request/response shapes, errors, authentication and retry semantics; reproducible generation and drift checks |
| Compatibility | Agent 204 | Declared support policy, enforcement and tests of supported and unsupported combinations, including legacy transition |
| Connection cost and transport choice | Agent 201 | Reproducible current HTTP, improved HTTP and Protobuf/gRPC comparison; any adopted optimization retains retry guarantees |
| Notification latency | Agent 203 | Measured latency stages and distributions, distinguishing transport, polling and host scheduling |
| Integration and acceptance | Agent 112 | Review interfaces and safety claims, integrate commits, run local gates, record evidence and limits |

Each implementation uses an isolated worktree based on the baseline. Production
services and credentials stay outside experiments. All database experiments use
disposable PostgreSQL clusters. No GitHub Actions are required.

## Acceptance requirements

### Formal contract

- Every supported remote route has a machine-readable request and success
  response definition, including nested objects, arrays, optionality and nulls.
  Local-only routes are explicitly classified so an inventory cannot silently
  make them remotely available.
- The specification describes the error envelope and transport errors, bearer
  authentication, store-owned role/destination restrictions, session identity
  headers, encoded size limits and operation-specific retry semantics.
- Runtime JSON acceptance and contract constraints agree. Constraints that
  require current store state are named explicitly instead of being represented
  as unconditional schema guarantees.
- A deterministic generation/check command catches stale artifacts and route or
  type changes. Tests exercise both accepted and rejected representative wire
  messages against real handlers; generated self-consistency alone is not proof
  that a contract describes the implementation.

### Compatibility

- Protocol version support is distinct from VCS build identity. The declared
  policy explains requests, responses, unknown fields, optional additions,
  required additions, changed semantics, errors and supported version ranges.
- An unsupported combination fails clearly before executing the incompatible
  operation. Compatibility checks do not infer authority from a version claim.
- Tests use independently retained old-version fixtures or clients and cover
  both directions of permitted version skew, the legacy deployed behavior,
  malformed metadata and unsupported combinations. Current client/server tests
  alone do not demonstrate version compatibility.
- Enrollment, normal requests and a server change after enrollment have a
  coherent enforcement path. Diagnostic compatibility claims cannot promise
  more than the checks and fixtures establish.

### Performance evidence

- Connection benchmarks compare current connection-per-call HTTP, improved
  HTTP and experimental Protobuf/gRPC under stated payload, concurrency, TLS,
  warmup and sample settings. Report distributions, errors and connection counts
  rather than only a favorable aggregate throughput number.
- Separate transport-only measurements from store work. Record environment,
  versions and reproducible commands. Loopback and simulated network delay are
  labelled; neither is presented as an observed cross-machine result.
- Notification measurements distinguish durable publication, API observation,
  host polling, native delivery admission and harness scheduling. A controlled
  scheduler fixture is labelled separately from a real harness measurement.
  An unobserved stage remains explicitly unmeasured.
- Reducing latency must not introduce duplicate effects, silent loss, broader
  authority, unbounded responses or transparent replay of an uncertain write.
  Fault tests cover stale pooled connections and response loss after commit.

### Transport decision

- Compare the measured benefit of reuse separately from encoding and protocol
  changes. Describe experimental gRPC parity and gaps, including authentication,
  bounds, retry behavior and event semantics.
- Adopt a migration only if evidence supports benefits worth implementation,
  rollout and operating costs. Retaining HTTP is a valid outcome when supported
  by the comparison; omitting the comparison is not.
- Record the decision, measured evidence, limitations and conditions that would
  justify revisiting it. Production migration is not implied by a benchmark.

## Integration gates

1. Review all four results against the requirements above and resolve shared
   wire-contract, version-header and relay changes before merging.
2. Run contract generation/drift checks and compatibility tests on the integrated
   tree, including legacy fixtures and failure cases.
3. Run `make check`, `make test-integration` and the Python lifecycle tests on the
   integrated tree. The integration script owns its disposable database.
4. Reproduce connection and notification benchmarks on the integrated tree and
   retain selected methodology/results suitable for a concise verification
   report. Do not commit raw operational logs, credentials or model output.
5. Write a requirement-by-requirement completion record with exact revisions,
   commands, outcomes and remaining measurement limitations.

Completion requires evidence for each section, not merely agent acknowledgment,
schema generation or a passing current-version test suite.

## Independent historical peer check

`bash scripts/test-api-version-skew.sh --output /tmp/cairn-api-skew.json` builds
the immutable baseline revision in a temporary local clone and this checkout's
candidate binary. It tests all eight combinations of historical/candidate CLI,
relay and central API over verified loopback TLS. Each API uses its own database
in a runner-owned temporary PostgreSQL cluster. Clients and relays are given an
unusable database address so they cannot bypass the API.

The fixture `fixtures/api-compatibility/legacy-v1-matrix.json` explicitly names
every combination as `client/relay/server`. Its initial all-OK expectation
records the baseline behavior; review it against the declared compatibility
policy when version enforcement lands. The probe checks creation, exact request
replay, changed-intent rejection, append replay, current reads and durable
request counts. Expected incompatibility must leave no mutation request row.

The initial harness run passed all eight combinations before candidate runtime
changes. This validates the harness against the baseline, not the next-stage
version policy. The runner now requires a clean committed candidate checkout
and builds both binaries in temporary clones with real `.git` directories, so
Go can stamp the versions even on toolchains that overlook linked-worktree
files. Both binaries must report their exact clean pinned revisions or the
probe refuses to proceed.
