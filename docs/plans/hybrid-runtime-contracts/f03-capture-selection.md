# F03 provider-selected capture frontend

The unbound Go memory entry now connects the existing capture admission,
transcript snapshot and locks to checkpoint/candidate retrieval, a selected
provider subprocess, `memoryclient.CommitCaptureSelection`, and the existing
final state CAS. This unit does not enable a bound capture callback or change
installed hook rows. Configurations without a `claude` selector remain at the
explicit substantive-capture unavailable boundary.

The process oracle is the complete retained Python frontend at
`integrations/lifecycle/memory.py`, SHA-256
`0e1076bf999f880b09923783985206985fe093f1b4ee7a348ceb5ae968e1c1c2`
on base `1bda72a657b4fa5ccaeb2e09ad62a1af8a950bf0`. Its capture, candidate,
selector and selected-write bodies run unchanged. The paired fixture substitutes
only an external local selector executable and a Unix API service. No real
provider is called.

The selector gets the Source prompt/schema and excerpt, with hooks, tools,
settings sources and MCP disabled in the same argv. It inherits the caller
environment with `CLAUDECODE` removed and `CAIRN_LIFECYCLE_CHILD=1`, and runs in
a fresh, subsequently removed empty directory. Its direct child is cleaned up
on failure. The 35-second deadline is also bounded by the caller context; each
output pipe is read concurrently and capped independently at 1 MiB plus one
overflow-detection byte. Stdout must be UTF-8 JSON object data. Process errors,
reported provider errors and invalid selections do not advance capture state.

The supported selected-write domain remains MC1 from
[f03-capture-commit.md](f03-capture-commit.md): valid Unicode and RFC JSON,
string record IDs and integer versions. Non-RFC values and unpaired surrogates
are explicit domain refusals; this is not all-input CPython reconstruction.
The provider output is not accepted as a caller-supplied capture result.

The existing client plans every write before the first mutation. Creates and
revisions retain Source order and UUID identities. A confirmed earlier write
survives a later failure, while the frontend digest and markers remain
uncommitted. A successful final note, including a no-op, supplies the
workstream. The existing final CAS merges independent recall fields and refuses
capture/binding conflicts; API writes cannot be rolled back by that refusal.

The capture report runs after the capture and shared locks are released.
Selected model names, duration, usage and cost pass the Source filters. Reports
are best effort and never change capture success or retry eligibility. SDK
diagnostics report the actual Go image hash; the Source CLI reports the Python
body hash. Those implementation IDs intentionally differ.

The first ten process pairs cover null selection, new durable/checkpoint
writes, revision, no-op, prewrite invalidation, invalid provider JSON,
reported/nonzero provider errors, bounded metadata and a late write failure.
Timeout, retry, concurrent state and transcript cases are required before the
ready handoff. Passing fixtures establish the tested frontend relation, not
provider usefulness, server idempotency/CAS, runtime acceptance or permission
to activate the entry. Rollback is to the retained Python hook path; no live
configuration is changed by this unit.
