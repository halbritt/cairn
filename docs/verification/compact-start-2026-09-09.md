# Explicit compact startup

`cairn agent start` now supplies an initial memory index and task to a configured
harness. In native OpenCode 1.18.21, stdin delivery preserved the input and the
existing `cairn_pull` tool expanded a source without first calling search. This
adds a usable launch route; model-selected use and task benefit remain unverified.

The [command guide](../compact-start.md) covers setup, ordinary-profile identity,
scope, permissions, input budgets and process behavior. Generic observed/retained
H0 execution remains body-only; no observer credential, authority mutation or
automatic outcome claim is part of this command.

## Observed native behavior

Three isolated native sessions used a disposable Cairn API and scripted loopback
provider responses, with no inference. Each received 2,961 bytes of initial input
under a 16,000-byte limit, including complete required context and a bounded
preview. Unicode, quotation marks, shell-like literal text and newlines in the
task arrived intact.

| Condition | Result |
| --- | --- |
| Pull allowed | A native `cairn_pull` call returned the exact full stored source, leaving three expansion credits. No search call was needed. |
| Pull denied | OpenCode omitted the pull tool from its catalog; no body pull occurred. The explicitly requested preload was still delivered. |
| Source revised after preload | The native pull returned `STALE_HANDLE`; it did not serve the old body. |

The existing tool configuration must use the launcher's API principal. A separate
CLI check refused expansion through another principal. The launcher does not
infer that a supplied tool name is loaded or authorized.

An 8 KiB startup fixture also verified refusal of an oversized whole-body pull,
followed by an exact 128-byte span and identical retry. Initial input room and
the receipt's existing expansion budget are distinct from a complete accounting
of all harness context. The initial byte count is not evidence of net token
savings: later pulls add content and round trips.

## Process and preservation checks

The real CLI/API fixture checked process replacement, literal argv, original
stdin in argv mode, prepared stdin in stdin mode, closed-stdin delivery, exit
status 23 and SIGTERM handling. No separate supervisor is left to recover. Stdin
uses an anonymous Linux memory file, released through descriptor ownership; no
named context file is created. The already pinned x/sys version became a direct
import without changing its version or go.sum.

Invalid intent, unavailable API, foreign repository, local destination and
insufficient input room refused without launching a marker command or echoing
the prepared task. Cairn/PostgreSQL credential environment variables were absent
from the child. Existing argv search presentations still include their CLI pull
commands; only the new startup presentation omits them.

Full disposable PostgreSQL/race integration, Go unit tests, 34 Python tests and
vet/format checks passed. A later focused CLI/API/native run verified the added
Unicode/quote and closed-stdin assertions. CI now includes the authenticated CLI
suite after building; native OpenCode remains an explicit local check. Unsupported
kernel and operating-system resource exhaustion were not fault-injected.

## Failed development checks and the resulting changes

The first fixture incorrectly expected its whole source plus metadata to fit
the 8 KiB receipt and failed on expansion. The final check explicitly verifies
`BUDGET_REFUSED` and then a partial pull; the native whole-body case declares
16 KiB.

The initial native argv attempt timed out after the scripted provider could not
parse the escaped input. Inspection of its retained request and
[pinned OpenCode source](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/cli/cmd/run.ts#L288)
showed that OpenCode wraps arguments containing spaces and escapes quotes. The
fixture's HTTP 500 response caused provider retries. The check now retains its
own errors and responds with 400; production startup gained an explicit stdin
carrier using OpenCode's existing unchanged-input path. No larger timeout or
model budget was used to pass the check. The failed argv evidence is preserved.

The [earlier hook investigation](opencode-startup-hook-2026-09-09.md) remains valid:
an unconditional system hook would run outside normal tool permission checks and
also reach auxiliary requests. This launch is a separate explicit preload. Normal
harness processing may still include its task input in title generation.

The retained-context lesson v3 was read before implementation and informed the
fresh-request and observer-authority choices. The source/transport issue came
from native testing and source inspection. Neither observation establishes an
incremental memory-value or task-acceptance result.

[Metadata](compact-start-2026-09-09.json) retains source, test, native request and
doctrine receipts. E2 remains partial for observed/retained index execution and
combined task budgeting; general durable task value remains open.
