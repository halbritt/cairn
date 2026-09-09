# Task files for compact startup

`cairn agent start --prompt-file ./task.md` reads a selected task specification
without shell expansion, newline trimming or consuming the caller's stdin. It
replaces `--prompt`; supplying both refuses. The existing combined initial input
budget includes the file body, which must be nonempty UTF-8 without NUL bytes.
See the [command guide](../compact-start.md#budget-and-lifecycle).

The installed baseline rejected this flag. A 57-byte task file became 55 bytes
through shell command substitution because two trailing newlines were removed.
Callers can still construct an exact argument in a script; the file route removes
that assembly step. This is an input convenience, not evidence of memory benefit.

Go tests verify refusal of empty, invalid, oversized, missing and non-regular
sources before API contact. A nonblocking open allows a FIFO to be rejected
without waiting for a writer; bounded reading detects growth beyond the limit.
The file is closed after reading and remains in place. Concurrent edits are not
a snapshot transaction. No new storage, dependency, observer or recovery behavior
is involved.

The disposable CLI/API suite delivered exact Unicode, quotes, CRLF and trailing
blank lines through both carriers, preserving mandatory memory, caller stdin in
argv mode, environment filtering and the source file. Its Python receiver reads
raw stdin bytes so its own newline conversion cannot mask delivery differences.
All Go tests, 34 Python tests and vet/format passed. Existing native OpenCode
carrier verification remains in the [startup report](compact-start-2026-09-09.md);
no new native model trial or task assessment was performed.

The saved owner value and priority notes were read before selection and checked
against the current roadmap. They reinforced keeping the change in existing
interfaces; the CLI and shell observations supplied the input-specific evidence.
The initial R3 inspection found that guarded retraction previews already exist;
no duplicate guard was added. Memory's incremental contribution remains unknown.

[Metadata](start-task-file-2026-09-09.json) retains source and test hashes, baseline
observations and the validated design receipt. Eight generic Go interface/absence
and recurring-churn obligations are nonmaterial to this private additive reader.

## Local installation

Clean `291557105d036b806a31c3fedee3ff702dc2a95c` is installed as the CLI, SHA-256
`08f9579d251baeed1ee120e76a916ddee09e04ddc0304b2246d1847c86e2b0e2`. A live ordinary-profile startup
delivered all 57 task-file bytes to `/bin/cat`, including two trailing newlines,
within a 2,728-byte combined initial input. The previous CLI is retained locally.
The API remains at cf66e1c/PID 4121278 and was not restarted.

[Exact-source CI](https://github.com/halbritt/cairn/actions/runs/34418879903) passed PostgreSQL/race, Python, vet/build,
history and authenticated startup checks.
