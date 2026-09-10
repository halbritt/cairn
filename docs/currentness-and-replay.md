# Currentness and historical recompilation

An ordinary record may include `draft.pins`: `revision` (immutable Git object ID),
`workspace_sha256`, `task_class`, `task_phase`, `binding_id`, `capability_id`, `valid_from` and
`valid_until`. Unspecified constraints are unpinned. Ordinary edits and B
corrections cannot remove or change these constraints; explicit
[scope authorization](scope-authorization.md) can expand applicability through a
separate C decision. A validity interval is half-open.

Compile requests supply matching `context` labels. Missing context, mismatch and
outside-validity omissions have fixed census buckets. A mandatory applicable C
instruction cannot be skipped by omitting required context. These are declared
constraints; matching them does not certify physical workspace state. `search`
and `run` accept revision/workspace/task-class/task-phase/binding/capability flags. The wrapper
uses one consistent tuple for compilation and run metadata.

Pins are conjunctive: any known mismatch makes a record inapplicable, even when
another pin is missing. For example, a record pinned to revision A and task class
`build` does not apply to a declared `review` task with no revision supplied.
If the task class matches and the revision is missing, applicability remains
unknown; a mandatory instruction still refuses. Validity exclusions take
precedence. Private inapplicable instructions stay outside hosted bodies,
omission counts and candidate explanations.

The [precedence repair](verification/applicability-precedence-2026-09-09.md)
corrects an earlier field-order dependency. Historical recompilation retains the
original omission reason and seal. If a fresh compile would now produce a
different package for an old request ID, it returns `STALE_PACKAGE`; use a new
request ID to retrieve under the corrected rule.

Hosted compilation checks applicability before refusing delivery of a private
mandatory instruction. An expired, not-yet-effective or context-mismatched
instruction does not block that run. Matching, unconstrained or unknown
applicability still refuses when the destination cannot receive the instruction.
Private candidates remain absent from hosted packages, omission counts and
candidate explanations. See the [regression verification](verification/private-policy-applicability-2026-09-08.md).

Semantic schema `cairn.semantic/3` seals these context pins. Optional JSON/CBOR
fields preserve old v1/v2 decoding and seals. Legacy receipts remain historical;
a current request after compiler-version changes needs a new request identity.

## Declared task phases

`task_phase` narrows guidance within a task class. For example, a procedure with
`draft.pins` set to `{"task_class":"repair","task_phase":"validation"}` applies
to the validation phase of a repair. A request declaring `implementation` omits
it; a request without phase context cannot establish applicability. Omitting phase
cannot bypass a mandatory instruction. Different known phases do not create an
applicability overlap for otherwise conflicting instructions.

The phase is an exact, case-sensitive label, at most 256 bytes. Empty means no
constraint on a record and unspecified context on a request; `*` is refused.
`implementation` and `validation` are examples, not an enum. The host supplies the
label; Cairn neither advances a workflow nor certifies its actual phase. Retrieval
`purpose` retains its separate authority/read-gate meaning.

```sh
cairn agent --token-file ~/.local/share/cairn/hosted-agent.token search \
  --repo "$PWD" --task repair-task --run validation-run \
  --task-class repair --task-phase validation 'storage checks'
```

The same `--task-phase` declaration is available on local `search`/`run`,
authenticated `agent run`, compact `agent start`, `mcp` and the Codex, OpenCode
and Claude configuration generators. `opencode-install` writes it as
`context.task_phase`. MCP pins stay fixed for that server instance; changing a
workflow phase requires a new declared context, not a fabricated observation.
Retained execution refuses a changed or omitted phase before binding or launch.
Existing handles retain their original declared context and normal freshness
checks; they do not observe later workflow changes.

Ordinary edits cannot remove or change phase pins. Derived guidance must preserve
its source's phase restriction; authorized scope expansion uses the existing
explicit review path. No new authority grant or promotion rule is introduced.

Requests with a nonempty phase use `cairn.semantic/10`, including body, index,
browse, kind-filtered and semantic-discovery retrieval. Requests without a phase
keep their previous schema and retry representation. The optional CBOR field is
omitted when empty, preserving old receipt seals and historical recompilation.

Upgrade every database reader before creating phase-pinned records. Older direct
database readers ignore the new JSON constraint and are unsuitable for those
records; replacing only the CLI while leaving an old API running is insufficient.
Older binaries also cannot execute or recompile a new phase-bearing receipt.
No database migration is needed, but reverting a reader alone after writing new
constraints is unsupported. The [phase verification](verification/task-phase-2026-09-09.md)
records both preserved old packages and this reader-version limit.

New retrievals use `lexical-scope-recency/4`, which also filters fixed question
framing words from lexical matches. Historical recompilation uses the ranking
version retained in each receipt. The [retrieval comparison](verification/question-words-2026-09-08.md)
records the measured improvements and regressions. Reusing a compile request ID
across a ranking upgrade returns `STALE_PACKAGE`; use a new ID for current context.

## Recompile a retained read set

`cairn recompile` accepts:

```json
{"receipt_id":"RECEIPT_UUID","query":"the original query"}
```

The query is transient and must reproduce the retained intent digest. The command
loads the original considered versions and frozen eligibility facts, reads their
retained bytes, recomputes lexical ranking and budget packing, and checks the
result against the original seal. It creates no new exposure and cannot authorize
fresh delivery. Its cutoff is the named retrieval's actual read set, not an
arbitrary timestamp or a reusable PostgreSQL transaction handle.

Explanation version 2 retains body digests and the evidence/authority/attribution
facts needed for eligible candidates. This avoids duplicating their bodies while
preserving earlier gate decisions when evidence or grants change later. Body
corruption or a changed result fails integrity verification. Missing retained
versions or older explanation formats return `REPLAY_INCOMPLETE`; `replay` can
still inspect saved bytes where they remain intact. Neither command can recreate
purged material from a digest.

This implements historical recompilation for read sets captured by this compiler.
It does not invent the missing past observation history of older receipts or
establish measured usefulness on real incidents. Real-history evaluation must
separately distinguish original-time availability from later recurrence scenarios.

Lexical ranker v2 removes a fixed set of English function words. Domain tokens
and negation are preserved. A nonempty query containing only those function words
selects no optional advice and generates no blocked promotion demand. An explicitly
empty query remains an unfiltered scoped browse.

New retrievals use ranker v3. It also recognizes nonempty words separated by
underscores: `CAIRN_HOME` matches `cairn home` or `home`, while retaining the whole
identifier as an additional exact-match term. Partial identifier matches can
return more notes. Versions 1 and 2 retain their original behavior for historical
recompilation. This does not add synonyms, stemming or semantic search; the
[documentation retrieval experiment](verification/retrieval-quality-2026-09-08.md)
records the measured improvement and remaining misses.
