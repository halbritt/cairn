# Recovering task-file handoff guidance — 2026-09-09

The question “How do I avoid losing line breaks when handing a task to another
agent?” did not return the existing OpenCode procedure in either five-result
lexical or semantic indexes. A separate newline/CRLF search returned no notes.
The pulled OpenCode and Codex procedures omitted `--prompt-file`; the OpenCode
procedure did already point to the correct source document. This was a missing
usage detail in saved guidance, not evidence that the older memory had no value.

Updated the existing OpenCode procedure from v11 to v12 with the supported
`cairn agent start --prompt-file ./task.md` route, stdin guidance, exact newline
behavior, input limits and source pointers. Its earlier prose and metadata remain,
with the previous version retained. Exact revision retry and fresh pulls matched.
The same question then ranked it first in both modes. A targeted 897-byte pull
recovered the complete added guidance using the existing preview source location.
No ranking algorithm, runtime feature or new memory record was needed.

The note was checked against `docs/compact-start.md` and current launcher source.
Since the original task-file implementation, that source has only gained a phase
flag. The retained live startup artifact matches its recorded hash and still ends
in all 57 original task bytes, including two trailing newlines. The recent full
integration also exercised the task-file carriers. Existing verification was
reused; no new launcher or answering-model trial was run for this note correction.

This is an observed correction of a retrieval coverage gap. The query was selected
because it missed, and the same agent diagnosed and revised the note. Ranking
improvement here is not an independent relevance benchmark, accepted model task or
proof of incremental task value. The [metadata](task-handoff-guidance-2026-09-09.json)
retains versions, body hashes, comparison and local evidence paths; operational
note bodies remain outside Git.
