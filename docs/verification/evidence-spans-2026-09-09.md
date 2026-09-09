# Bounded evidence reads

Captured evidence can contain up to 1 MiB, while an index session normally
allows 24,000 shared expansion bytes. Whole-object pulls therefore left some
successfully captured sources inaccessible through the agent interface.

An optional byte span now lets the agent select part of an attached object.
The response retains the full object's identity and checks, but puts selected
bytes, their SHA-256, offset, exclusive end and total source length in a separate
`span`. Ordinary whole-object requests retain their existing JSON/retry identity.
No database migration, new storage service or automatic source capture is added.

## Verification

`make test-integration` passed against disposable PostgreSQL with the race
detector, with these optional checks enabled:

```sh
CAIRN_PREVIOUS_BINARY=/path/to/previous/cairn \
CAIRN_OPENCODE_TOOLS_BINARY=/path/to/opencode \
  make test-integration
```

- A 63,044-byte source was explicitly captured and attached to a promoted claim.
  Whole-object expansion returned `BUDGET_REFUSED`; the CLI/API then returned its
  exact 31-byte tail and checksum, clipped at EOF, with three credits remaining.
  The full-object digest stayed present and the whole body was absent.
- Core tests also read the prefix, check separate full/span checksums, account
  for byte consumption, and verify one observation per successful range. A
  source change outside the prefix invalidates its cached retry.
- Byte ranges that split a UTF-8 character return exact base64. An explicitly
  captured binary object works too. Negative, zero, oversized and out-of-source
  ranges refuse; failed requests spend no credits. A range too large for the
  context budget refuses intact.
- Identical retries return the same response without another spend. Changing
  the range under a used UUID conflicts. Whole and span modes both exercise
  caller/destination checks, evidence sensitivity changes, withdrawn claims,
  and existing cached-response deletion exclusion/purge.
- Independent MCP stdio calls and actual native OpenCode custom-tool calls read
  spans, retry them and reject changed intent. Native validation rejects malformed
  span arguments. These checks invoke no answering model.
- The previously installed clean binary, `6e604ce`, created a whole-object
  expansion in the disposable database. The candidate returned the identical
  cached result for the same request, with no span field or additional spend.

`make test` passed all Go packages and 26 Python tests. `make check` passed vet
and formatting. Final integration output is retained at
`/tmp/cairn-evidence-span-integration-c.log`; the adjacent JSON records hashes.
The first two integration attempts exposed fixture errors (a tail-length
expectation and a missing explicit repository flag), corrected before the final
run. Their logs remain under the same prefix.

## Limits

Spans consume the existing four shared credits and encoded-response byte budget.
They do not allow arbitrary streaming of the entire object into one context.
The store still reads and hashes the full captured object before disclosing a
range. This bounds delivered bytes, not storage I/O.

These are retrieval ranges, not persisted claim-level as-cited span relations.
L4 remains partial for that lifecycle and managed artifacts above 1 MiB. No
source label is fetched, no protected operational source was used in these
checks, and no downstream model-task benefit is established by the fixture.
