# Architecture

Cairn keeps selected knowledge available across agent sessions and provides a
durable inbox for coordinating work. PostgreSQL holds records, revisions,
authority state, retrieval receipts, and delivery state. Clients reach that store
through an authenticated API; CLI, MCP, and native integrations adapt it to the
agent's workflow.

## Follow a memory request

1. A client authenticates with a provisioned token. The API establishes the
   principal, collection access, and delivery destination.
2. Search applies scope, currentness, sensitivity, conflict, and authority checks
   before ranking eligible records. It returns required context and a bounded
   index of optional sources.
3. The client pulls selected sources using the returned receipt and handles.
   Expansion rechecks eligibility and consumes the receipt's byte and call budget.
4. The agent checks the source against the current task. A saved note remains
   fallible testimony; retrieval does not verify the note or authorize an action.

The receipt records what Cairn exposed. It does not prove that the agent read,
used, or benefited from that material. See [index and pull](../index-and-pull.md)
and [use observations](../use-outcome-loop.md).

## Where to change the code

| Directory | Responsibility |
| --- | --- |
| `core/` | Store transactions, records, retrieval, authority, coordination, and ordered SQL migrations in `core/schema/` |
| `localapi/` | Authentication, HTTP transport, operation catalog, and API client |
| `mcpapi/` | MCP tools, argument validation, and bounded result presentation |
| `cmd/cairn/` | CLI dispatch, configuration generation, and operator commands |
| `runner/` | Process launch and observation around retained context |
| `artifacts/` | Managed evidence storage and verification |
| `semantic/` | Local semantic scoring and embedding interfaces |
| `integrations/` | Harness adapters and service templates |
| `scripts/` | Installers, lifecycle hooks, disposable fixtures, and validation |
| `internal/` | Shared implementation details, including API contracts and refusal classification |

Go requirements are declared in [go.mod](../../go.mod). The deployment target
is PostgreSQL 16 or newer; integration checks use a disposable cluster. Apply
schema migrations with `cairn migrate` during an operator-managed upgrade.
Migration files, rather than a copied schema number in this guide, define the
current schema.

## Preserve the boundaries

Ordinary notes, qualified claims, and authorized instructions have different
contracts. Store-owned writer and observation fields must come from the
established channel. A payload field, a scope label, or a caller-selected flag
cannot authenticate an identity.

Request-ID operations retain the caller's result for retries and reject changed
intent under the same UUID. Record edits also require the current
`expected_version`; a conflict requires rereading and reconciling. Other
operations, such as heartbeat and lease renewal, have their own retry contracts.
Consult the [operation catalog](../../localapi/operations.go) and
[API contract](../api-contract.md) before changing one.

Restore fencing coordinates recovery admission and invalidates old retrieval
state through database generations. Restoring a dump alone cannot account for
later revocations or forgotten data. Follow [restore admission](../restore-admission.md)
and [recovery reapplication](../recovery-reapplication.md).

## Extending Cairn

Start from the relevant store operation, then update its CLI/API/MCP surfaces and
contract fixtures together. Use [testing](testing.md) for validation requirements
and [contributing](contributing.md) for repository conventions. The
[lifecycle capability map](../reference/lifecycle-slices.md) distinguishes core
inspection helpers from integrations that actually enforce a decision.
