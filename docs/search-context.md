# Declare context for a native memory search

MCP and native OpenCode `cairn_search` accept an optional `context` object for
the current search. Use it when work moves between phases or revisions during a
session and relevant notes have applicability restrictions.

```json
{
  "query": "review procedure",
  "context": {"task_phase": "validation"}
}
```

Inspect the actual task or workspace before declaring context. Supported fields
are `revision`, `workspace_sha256`, `task_class`, `task_phase`, `binding_id` and
`capability_id`, with the same validation as CLI search. These are declarations,
not observations that certify execution or workspace state. Do not supply a
matching value merely to obtain a withheld note.

Each call may fill fields the host configuration left unset. Host-fixed fields
remain in force: a different nonempty value returns `INVALID_REQUEST` before
retrieval. Supplying the same value is allowed. Empty or omitted fields preserve
configured values; they cannot erase a host declaration. To change fixed MCP
context, restart with updated configuration. Native OpenCode reads its connection
settings on each call; those configured values also cannot be replaced by a tool
argument. In OpenCode's settings file the keys remain `binding` and `capability`;
tool arguments use the canonical `binding_id` and `capability_id` names.

Per-call declarations do not carry forward. Repeat the same context on later
pages and exact retries. A new phase or revision requires a new search request
identity. The result reports the effective context, and its receipt retains that
context under the existing compiler contract. Pulls use the original receipt;
they do not become searches for a newly declared phase.

Missing context still withholds restricted optional notes and can refuse unknown
mandatory requirements. Repository identity, session scope, destination,
permissions and source eligibility keep their existing owners and checks.
Declaring context does not change a note's applicability or grant authority.
Capture still requires explicit `pins`; it never inherits search context.

Update the MCP facade binary or reinstall the native OpenCode adapter before
using this argument. Older tools reject it as an unknown field. Existing calls
without `context` retain their configured behavior. This interface supplies
changing context explicitly; automatic workspace observation and demonstrated
task benefit remain separate work.
