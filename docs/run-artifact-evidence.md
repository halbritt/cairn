# Fingerprint selected task files

Add `--artifact LABEL=PATH` to `cairn run` or authenticated `cairn agent run` to
retain a file's SHA-256 and byte count after the observed process stops. This
helps a later reviewer identify the output they examined without putting its
contents into memory. Selection is explicit; runs without this flag do no file
fingerprinting.

For example, from a Go project with a provisioned local observer:

```sh
cairn agent --token-file ~/.local/share/cairn/observer.token run \
  --repo "$PWD" --dir "$PWD" --task build --run build-1 \
  --destination local --artifact executable=./app \
  -- go build -o ./app .
```

Repeat `--artifact` for up to 16 files, with a combined limit of 64 MiB read per
run. Labels must be distinct, 1–128 characters from ASCII letters, digits and
`_.-`. Paths may be absolute or relative to `--dir` (the current directory by
default). Paths must be nonblank, at most 4096 bytes and contain no NUL. The first
`=` separates label from path; later `=` characters belong to the path.

The runner resolves paths before launch and reads files only after committing
the process outcome. Only regular files are accepted. A final symlink is refused;
symlinks in intermediate directories follow normal filesystem resolution.
Directories, FIFOs, missing files and over-limit selections fail explicitly.
Existing files and empty files are allowed, including after nonzero process exits.
Launch failure does not trigger file reads.

## What is retained

The run result's optional `artifact_evidence` contains the captured evidence ID,
manifest digest, witness and state. The evidence body uses
`cairn.run-artifact-fingerprints/1` and contains:

- The run receipt ID, committed outcome ID, process state and observation-start time.
- Each selected label, byte count and file SHA-256, ordered by label.
- An interpretation stating the limits of the observation.

Actual paths and file contents are omitted. Choose labels suitable for retention.
The evidence is **local by default**, including for a hosted run. To permit hosted
delivery of the manifest, explicitly add `--share-artifact-evidence`. That flag
requires a file selection and never includes file contents. Normal evidence
eligibility and access checks still govern later delivery.

The authenticated observer supplies the existing `instrumented` witness. The
runner writes the run IDs into the manifest; these are host observations within
the body, not new database foreign keys. Ordinary evidence capture cannot acquire
an observer role by supplying those fields. `artifact_evidence.sha256` hashes the
manifest body; the SHA-256 inside each file entry hashes that file's observed bytes.

A fingerprint does not establish that the process created or changed the file,
that its contents are correct, or that memory improved the task. Files may predate
the run or have other writers. The reader detects size and modification-time
changes around hashing but provides no atomic filesystem snapshot. A five-second
finish context and streaming byte limit bound the work; a blocked filesystem
system call is not guaranteed to be interruptible by that context.

Task assessment remains separate. A reviewer can supply the returned `evidence_id`
in the existing `assess-run.evidence_ids` list alongside the reason and method
supporting their assessment. Capturing fingerprints leaves task outcome unknown.
Qualitative and cumulative value review remains supported by the
[use/outcome workflow](use-outcome-loop.md#qualitative-and-cumulative-review).

## Failures and retry

If any selected file cannot be fingerprinted, the runner returns
`ARTIFACT_EVIDENCE_FAILED`. Its result retains the observed process state, exit
code when available and committed outcome ID. No partial manifest is submitted.
An evidence error therefore does not mean the task process failed or never ran.

Before evidence capture, the runner writes the exact metadata-only request to
`artifact-evidence.pending.json` in its existing receipt directory. Successful
capture renames it to `artifact-evidence.json`. If the response is lost, inspect
the reported result and retry that exact request under the same observer:

```sh
cairn agent --token-file ~/.local/share/cairn/observer.token evidence \
  < /path/to/receipt/artifact-evidence.pending.json
```

The existing request identity returns the original evidence ID after an already
committed capture. Preserve the confirmation; this retry command does not rename
the file. Do not rerun the task to recover an ambiguous capture response. If
capture succeeded but the rename failed, the run result still exposes its evidence
ID. File-read failure occurs before a capture-ready request exists.

Selected files remain caller-owned. Cairn neither stores their contents nor deletes
them. Metadata request copies follow the existing local run-directory custody and
backup handling, outside database-only retention. For a retained-package execution,
the current `--artifact` selection is read after that execution; old file contents
or manifests are not automatically replayed.

The feature uses the existing evidence endpoint and storage format. No migration
or new server endpoint is required. See the
[verification record](verification/run-artifact-evidence-2026-09-09.md).
