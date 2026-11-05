# Save and correct useful notes

Save information that another session can act on: a decision and its reason, a
verified repair procedure, a recurring preference, or a handoff for unfinished
work. Include the project, conditions, and a way to check the claim. Avoid raw
conversation logs and temporary output that will not help later work.

## Save a note

Use an existing profile and its provisioned collection. This example uses the
default socket and token paths; connection overrides go after `cairn agent`.

```sh
capture_request="$(python3 -c 'import uuid; print(uuid.uuid4())')"
cairn agent remember --repo "$CAIRN_COLLECTION" \
  --request-id "$capture_request" --kind procedure --shareable \
  'Example project: run database tests against disposable clusters. Check the test runner configuration before changing the database URL.'
```

Keep the request UUID and exact arguments if you need to retry a lost response.
A changed request with the same UUID returns `IDEMPOTENCY_CONFLICT`; generating
a new UUID creates new intent. Use `--stdin < note.txt` for an explicitly selected
text file. A body must contain 1–65,536 bytes of nonblank UTF-8 text.

The MCP equivalent is `cairn_remember` with `request_id`, `body`, `kind`,
`shareable`, and optional `scope`. Ordinary capture creates Class A testimony:
authentication identifies the writer, but does not establish truth or grant
permission to act.

## Choose scope and sharing

Repository scope is the default for reusable guidance. In the CLI it uses the
provisioned `--repo` with task and run wildcards. Set `--task TASK` for guidance
limited to a task, and add `--run RUN` for a particular run. MCP accepts
`scope: "repository"`, `"task"`, or `"run"` using the host's configured labels.
These labels control retrieval applicability; they are not execution attestation
or independent access-control boundaries.

The collection's `repo` value may be shared by several projects. Include the
actual project in the note so readers can judge applicability.

Notes default to `local` sensitivity. Use `--shareable` in the CLI or
`shareable: true` in MCP only for content suitable for hosted delivery. A hosted
profile cannot read a local note. Local destination permits local content under
the profile's other access checks; it does not imply unrestricted access.

Kinds such as `note`, `decision`, `preference`, `lesson`, and `procedure` help
organize and filter notes. Choose the label that describes the content. A kind
does not certify a claim or make an instruction authoritative.

## Correct an existing note

Search and pull the current note before editing it. Correct the existing record
when the same decision or procedure has changed, keeping unrelated information
and explaining what superseded the old guidance.

For MCP, replace the body with `cairn_edit`:

```json
{
  "request_id": "NEW_REQUEST_UUID",
  "record_id": "RECORD_UUID",
  "expected_version": 3,
  "body": "Example project: the updated procedure and its verification context."
}
```

Replace UUID placeholders with actual IDs. Supply exactly one edit mode:
`body`, `append`, `replace`, `set_sections`, `draft`, or `evidence_citations`.
Body edits preserve stored metadata. `replace` takes `old_text` and `new_text`,
and the old text must occur exactly once. `set_sections` maps Markdown `##`
heading titles to replacement section bodies.

The CLI JSON interface differs: `cairn agent revise` replaces a body,
`append` and `replace` make targeted changes, and `edit` requires a complete
`draft`. Use their `--help` output for request shapes. Do not send the MCP
example unchanged to `agent edit`.

`VERSION_CONFLICT` means someone changed the record after your read. Pull the
current version and reconcile it before making a new request. Earlier versions
remain in [record history](../record-history.md), subject to forgetting and
access checks.

## Evidence and lifecycle changes

Attach [evidence citations](../evidence-citations.md) when a note needs a retained
source. Citations require the source identity and digest; they do not promote an
ordinary note into a qualified claim. Operator workflows for
[supersession](../supersession.md), [ordinary deletion](../ordinary-delete.md),
and [audited forgetting](../deletion.md) have separate contracts. Choose the
operation for the intended effect; editing a body does not erase history.
