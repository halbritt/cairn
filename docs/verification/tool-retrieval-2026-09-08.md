# Model-directed retrieval, 2026-09-08

An actual OpenCode agent recovered from Cairn's `storage` search miss, found the
relevant note with a different query, pulled its full body and answered the storage
questions correctly with that source's UUID. It left an unsupported subscription
price unavailable. Directly supplying the notes also produced a correct answer.

The run exposed a practical tool problem: the model confused the receipt UUID
with other UUIDs in the index response. A follow-up that showed a complete pull
command beside each result completed the same answers with no failed pulls.
This supports that presentation choice in the experimental adapter. It does not
establish a general advantage, better coding outcomes, or durable lesson transfer.

## Prospective task and conditions

The [preset](../../trials/retrieval-tools/scenario.json) and controller were
committed at `be71b2c` before model calls. Twelve exact Cairn documentation passages
come from the frozen [retrieval workload](../../core/testdata/retrieval-quality.json).
Questions concern the user's storage topic: default data home, its environment
override, whether `CAIRN_DATABASE_URL` relocates the local-store directory, and
a monthly price absent from the supplied sources. Expected fields and source
contact checks were fixed in the preset.

This is a small development task, overlapping the earlier retrieval questions.
It is not held out or independently judged. The three initial conditions used
the same source bytes and model:

- No memory: no source notes or retrieval tool.
- Direct context: all twelve notes and their source UUIDs in the prompt.
- Tool retrieval: notes available through the existing hosted agent index/pull
  route, starting with the observed `storage` query; later queries chosen by the model.

Each condition had a fresh empty workspace and OpenCode home in the existing
filesystem sandbox. The host created shareable ordinary A notes in an owned
disposable PostgreSQL database. The agent profile had hosted destination and
repository scope, with an unusable client database address. The helper's only
commands were search and pull. No operational memory or private Council content
was used. The notes remain testimony, not promoted authority.

The installed Cairn binary was `a154257`; OpenCode was 1.18.21. The existing
`deepseek/deepseek-v4-flash-0731` OpenRouter route used its bounded parent-held
credential relay. Each arm allowed ten model requests, 8,192 output tokens per
request and 240 process seconds. The tool allowed eight memory calls. Reported
model requests can include harness background requests, not just visible agent
steps. No model or provider setting was changed between conditions.

## Observations

| Condition | Source answers | Memory calls / failed pulls | Process time | Reported cost, USD |
| --- | --- | --- | --- | --- |
| No memory | No structured answer; inconclusive control | 0 / 0 | 6.023 s | At least 0.00015828 |
| Direct context | All expected fields and source UUID | 0 / 0 | 22.306 s | 0.000672948 |
| Original tool response | All expected fields and pulled source UUID | 8 / 3 | 26.030 s | 0.0021249504 |
| Paired pull commands, follow-up | All expected fields and pulled source UUID | 8 / 0 | 30.009 s | 0.0018624636 |

The no-memory condition emitted raw, unexecuted DSML tool-call text rather than
the requested answer. One relay request ended with `BrokenPipeError` and no usage
cost. Its process exit zero does not count as task success, and its recorded cost
is incomplete. The failed control was retained, not rerun until it passed. These
results cannot establish an answer-quality advantage over a functioning no-memory
baseline.

The original tool run refined `storage` to `cairn home directory`. It then passed
a record UUID, a handle UUID and an index request UUID as the receipt identifier
in three unsuccessful pulls (`NOT_FOUND`, `NOT_FOUND`, `STALE_HANDLE`). Its fourth
pull supplied the real receipt and handle, returned the exact storage paragraph,
and was followed by a correct answer citing that record. This is an observed
interface-use failure, not an authority or storage failure.

The [follow-up preset](../../trials/retrieval-tools/paired-pulls.json) was committed
at `763d6e8` before one additional tool-only run. The experimental helper now pairs
each ordered index entry with its complete `/opt/memory pull RECEIPT HANDLE`
command. It joins handles by exact record/version and retains the summary, digest,
class/kind, mandatory list, expiry and remaining limits. Canonical API responses
remain unchanged and are retained separately in the private trial log.

That agent refined its query to `database`, pulled the right note immediately,
then used its remaining calls to investigate the unsupported price. The change
removed failed pulls in this sample, but total calls stayed at eight and elapsed
time increased. Different query choices, fresh UUIDs, stochastic generation and
provider behavior also differ across runs. Lower reported cost in the follow-up
does not establish a causal cost improvement.

The [derived observations](tool-retrieval-2026-09-08.json) retain source-criteria
checks, query sequences, error codes, binary/controller identities, times and
provider-reported costs. No model prose or credentials are committed. Actual
OpenCode tool-event output was compared with the raw API responses: paired
results preserved all index fields and order, matched the correct receipt/handle,
and were smaller than the raw responses in this corpus. The mandatory list was
empty here; this is not a general mandatory-instruction presentation test.

## Reproduction and limits

The committed wrapper owns the entire database lifetime. First verify the real
sandbox, socket and client route without making a model call:

```sh
bash scripts/trial-retrieval-tools.sh \
  --output /tmp/cairn-tool-preflight \
  --cairn /path/to/cairn --opencode /path/to/opencode \
  --paired-pulls --preflight-only
```

Use a new output directory for each invocation. Removing `--preflight-only`
executes the three model conditions through the existing configured OpenRouter
credential. `--arm tool_retrieval --paired-pulls` runs only the follow-up condition.
The limits are a fixed trial preset, not a general harness configuration surface.
Normal completion removes model session homes and disposable credential files;
private output/metadata logs remain in the explicitly requested directory.

The unpaired and paired sandbox/API preflights and existing Python tests pass.
The first preflight incorrectly expected an empty `index` property instead of
its documented omission on the wire; the corrected preflight passed before
model execution. The product binary, schema, ranker and operational services
were unchanged during this experiment.

This demonstrates a completed source-answering task through explicit OpenCode
tool use after a lexical miss. It does not complete native Striatum ingress,
multi-harness deployment, relevance evaluation on a larger corpus, a successful
coding recurrence, or transfer of a lesson learned on an earlier task. Broader
packaging of the paired response needs its own consumer and budget checks. The
controller records process and tool observations externally; these dynamic
retrieval receipts are not yet joined to a host attempt and task assessment.
The next work should extend this observed behavior into actual harness use and a
separate task, rather than add retrieval infrastructure without an observed need.

The bounded implementation decision used Pincite packet `pkt-519448b4c3c7adec`,
SHA-256 `519448b4c3c7adec3c9b6abdbc7a98e3f80333e407d35f42e9835e4117e18b92`,
corpus `corpus-2026-07-12-a11702cc9217`, doctrine `doctrine-f6bbb5196a3f8bf9`,
retriever `retriever-ec995ecdd083b2c8`. Repository precedence, evidence before
intervention, scoped input selection and bounded presentation informed it. The
validated local decision receipt retains sixteen nonmaterial obligations about
unchanged persisted identity and authorization, generic UI/configuration surfaces,
and named procedures. General harness packaging remains outside this experiment.
