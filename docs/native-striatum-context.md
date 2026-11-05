# Integrate Cairn with Striatum

A Striatum integration can attach a Cairn context package to a task, carry its
source identity through build admission, and record the resulting execution.
This is an integration contract for orchestration developers. For agents that
only need memory tools, start with [MCP setup](mcp/overview.md).

## Preserve the retrieval and execution boundary

The integration needs a trusted host that authenticates the observer and supplies
the task's scope. Keep these responsibilities explicit:

1. Compile a package with the exact repository, task, run, query, and applicable
   context pins. Retain its receipt and semantic seal.
2. Bind that package to the intended build input. Verify the rendered context
   against its retained identity before admitting it.
3. Recheck current eligibility before launch. Newly revoked or forgotten content
   must not enter a new execution merely because an earlier package existed.
4. Record observed process results separately from task acceptance. A process
   exit, delivery receipt, or model citation alone does not establish a repair.

Cairn supplies [authenticated execution](authenticated-runner.md),
[retained package execution](retained-execution.md), and
[run retrieval and outcome observations](use-outcome-loop.md). The orchestration
host is responsible for its own input contracts and acceptance rules.

## Check compatibility before enabling the integration

The September 2026 integration was tested against specific Cairn and Striatum
revisions. Its observation/build contracts and production adoption were separate
from merging the implementation. Those checks do not establish compatibility
with the Striatum installation you are using today.

Inspect the current Striatum adapter and accepted catalog before deployment.
Confirm which Cairn package schemas and retrieval options it accepts; do not
silently discard unsupported options. A native supervisor already responsible
for launch and outcome recording should remain the execution owner rather than
being wrapped in a second `cairn run` process.

## Historical evidence

The [September integration record](history/native-striatum-context-2026-09.md)
retains the architecture, exact revisions, component checks, adoption constraints,
and model-task results. Its two model comparisons delivered context but produced
no admitted repair. Treat those as bounded experimental results.

<a id="shared-rfc-adoption-check-2026-09-10"></a>
The [RFC adoption check](history/native-striatum-context-2026-09.md#shared-rfc-adoption-check-2026-09-10)
describes the contract-acceptance dependencies observed on September 10.

<a id="evidence-and-next-contract-work"></a>
The [contract proposal history](history/native-striatum-context-2026-09.md#evidence-and-next-contract-work)
records the proposed observation and build changes.

<a id="current-build-verification-2026-09-10"></a>
The [component verification](history/native-striatum-context-2026-09.md#current-build-verification-2026-09-10)
provides the original reproduction command and its limits.
