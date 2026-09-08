# Retained execution verification

Code commit `93bc66a972222fa47dec97e87beecdb5e82b8a82` adds execution of an
observer-owned retained package through the existing Unix API, runner and CLI.
The host supplies its receipt and expected semantic seal. Loading preserves the
original receipt, nonce and selection; execution uses the existing binding,
freshness, process and outcome path. See [the interface](../retained-execution.md).

## Discriminating checks

The positive case captures a body package, adds optional memory, then executes
the original selection. Repeating the original compile instead refuses because
its newly computed selection differs. A store wrapper that fails every compile
call proves that retained execution performs no compile call. Real stdin and
argv children receive the original rendered context, and managed context bytes
and the outcome remain attached to the original receipt.

Core, runner, authenticated Unix API and CLI checks cover:

- Owner, observer role, repository, destination, seal and body-package checks.
- Scope, query, purpose, available budget and all declared run-context pins.
- Partial or explicitly empty receipt flags refusing before fallback compilation.
- Selected memory changing between package load and launch claim: no child starts.
- A second execution of the same receipt refusing without another child.
- Local and hosted profiles, with no database access from the Unix API client.

`make test-integration`, `make check`, all 17 Python tests and the final targeted
runner tests passed. [CI run 34291788540](https://github.com/halbritt/cairn/actions/runs/34291788540)
passed on the exact code commit. Tests used disposable PostgreSQL clusters.

## Installed state

The installed CLI and running API executable both have SHA-256
`756cd084be0430eb9059d401aa45dc1974be1e2c52030711a9c9151990be3089`.
The executable's Go build metadata names the code commit above and
`vcs.modified=false`. API PID 179782 and the store were active after installation;
ordinary authenticated hosted-agent retrieval succeeded. No schema or profile
changed. These are installation-time observations, not persistent health claims.

The [machine-readable record](retained-execution-2026-09-08.json) retains the
checks and log digests. Local installation details, the previous executable
copies and the actual retrieval response remain under
`/tmp/cairn-retained-execution-` and `/tmp/cairn-before-retained-execution-`.

## Remaining scope

This verifies Cairn's retained execution interface. It does not establish native
Striatum delivery, actual Striatum host correspondence or a model-task benefit.
Eligibility is checked at the launch transaction's snapshot; the host remains
responsible for physical workspace and binding declarations.

Striatum's existing supervisor already renders ordinary inputs and owns provider
execution. Its native adapter should use the authenticated package, binding,
claim, delivery and outcome operations directly at that boundary. Putting the
generic Cairn process wrapper around it would add another supervisor and append
memory outside the existing input rendering. Native acquisition and admitted
producer/consumer contracts still require implementation.

Validated doctrine packet `pkt-6ecce711886311eb` supported existing ownership,
consumer interfaces and preservation boundaries. Its private decision record
classifies 20 unmatched routing obligations as nonmaterial to the bounded Cairn
claims; native integration and real-task benefit remain explicit open claims.
