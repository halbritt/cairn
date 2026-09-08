# Authenticated runner verification

The existing process runner can now use an authenticated Unix client in place of
a direct Store. The CLI exposes this as `cairn agent ... run`; context registration
is available to observer profiles through the API. Process execution remains on
the client host. See the [contract](../authenticated-runner.md).

Disposable PostgreSQL and Unix socket tests establish:

- An observer delivers the compiled memory package and transient task prompt to
  a real child process, registers the context copy and records its outcome. The
  receipt belongs to the observer, rather than an operator or agent identity.
- Saved context excludes the task prompt. Run reports show one observed run and
  preserve unknown task acceptance. Reusing the run request refuses a second
  process.
- Agent-only profiles cannot bind a run or register managed context. A mismatch
  between requested and configured destination refuses before launch, in either
  direction.
- A fault that closes the socket after a committed launch claim is not retried.
  No child starts. The wrapper returns the known receipt/seal, and an explicit
  repeat refuses with `RUN_ALREADY_STARTED`.
- A fault after a committed outcome leaves the exact pending JSON on the host.
  Retrying it returns the original observation ID. The child ran once; retrying
  the run itself cannot start it again.
- Token reads reject public, empty, oversized, symlink and FIFO sources before
  contacting a server. The client has bounded requests/responses and no automatic
  mutation retries or HTTP redirects.

The lost-claim receipt assertion initially failed against the existing runner:
its error result discarded a successfully compiled receipt. Returning receipt
metadata from binding/claim errors corrected that defect without treating an
ambiguous response as launch permission.

The installed-style CLI test runs with an unusable `CAIRN_DATABASE_URL` while
only the server has a working disposable DSN. Its child confirms context/prompt
arrival and absence of `CAIRN_*` environment variables. It verifies stdout versus
receipt stderr, nonzero-exit mapping, timeout mapping, duplicate-run refusal and
three recorded process outcomes with unknown task acceptance.

UTC integration/race, formatting/vet and the existing lifecycle drill pass. The
lifecycle checks retain managed context ownership, deletion and restore fencing
through the new narrow runner/registrar interfaces. No schema migration or
operator endpoint was introduced.

These are real process and protocol tests, not evidence of model obedience,
independent task acceptance or improved repair outcomes. Striatum's sealed input
contract and host-owned attempt/terminal integration remain separate work. Its
RFC 0015 recall/knowledge route is still in flight according to the inspected
decision index; an accepted target catalog entry alone does not establish a
native Cairn admission path.

The engineering review used validated Pincite release
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`, corpus
`corpus-2026-07-12-a11702cc9217`, packet `pkt-9865f9af7957c9d5`.
The local typed receipt retains source and test evidence, the selected
consumer-owned interface and authority boundaries, and nonmaterial missing
obligations. Two evidence passes were completed; no native host acceptance or
model-performance conclusion follows from this review.


## Installed host execution

Commit `6538462f6a4f0c40c4fe3b3475d6dfc691c2ed21` passed
[CI](https://github.com/halbritt/cairn/actions/runs/34219047361) and was built from
a clean local clone with Go 1.25.0. The installed binary and running API have
SHA-256 `ecc2306e56662ab74a51321b86b0786f9b848f7dc2ea7844f4a2c52fec419940`.
Schema remains 024. Both services are active; an authenticated existing-record
read passed and the record-version digest was unchanged.

A real Cairn `make check` invocation then ran through the existing scoped observer
profile. The client had an unusable database address. Its child received and
parsed the context containing all four scoped records, completed the existing
verification command, and returned exit zero. The observer run report retained
its exact revision, binding and one process outcome, with task acceptance still
unknown. Record versions again remained unchanged. No model was invoked, policy
adopted or fixture instruction created. The context copy and observation are
retained as ordinary host-run evidence; no private memory bodies are in this report.
