# Source openings beside note spans

A context-index receipt can bind a 96-byte source-opening policy. **By default
no receipt does:** the API binds it only for authenticated profiles the operator
has enabled (see [Enabling binding](#enabling-binding-default-off)). A subsequent
partial `cairn_pull` of a bound receipt includes an exact prefix of the same
current note, without requiring the agent to request context separately. This
does not change ranking or enlarge the receipt's byte allowance. The internal
policy is sealed into the receipt. Receipts that were not bound keep their
previous response and retry behavior; neither upgrading nor enabling binding adds
openings to them.

Optional `opening_bytes` on a note pull overrides the prefix length with 1–768
bytes and requires an explicit `span`. Zero or omission uses the receipt's policy
(which is no opening on older receipts). CLI positional pulls use
`--opening-bytes N` with `--offset` / `--length`. JSON, MCP and OpenCode expose
`opening_bytes`.

The response's `source_opening` starts at zero and clips before the requested
span so the pieces never overlap. If the passage starts at zero, the opening is
omitted. The requested passage is unchanged and still clips only at EOF. Both
pieces belong to `selection.record` and the full-source hash in
`span.source_sha256`. Each has its own byte boundaries and SHA256; a split UTF-8
character uses `body_base64`. `record.body` remains empty.

The pair consumes one expansion credit and charges both serialized pieces to
the existing receipt budget. If it cannot fit, the whole request refuses without
spending bytes or credit. It does not silently discard context, shorten the
passage, enlarge the allowance or make a second read. A caller can choose a
smaller opening or another range with a new request UUID. An identical retry
returns the original response after current access and source checks. Whole
pulls remain unchanged. Instructions and competing positions still require
whole delivery.

Unknown receipt policies fail integrity checks. Old readers that do not know
the sealed field cannot reproduce a new receipt's seal. Historical recompile
preserves the policy. A repeated lexical search whose binding differs from the
original request's, because binding was enabled or disabled in between, returns
`STALE_PACKAGE`, as with existing presentation-policy upgrades; issue a new
search UUID. Semantic retries retain their earlier sealed policy in both
directions.

The prefix is exact source text, not an inferred project label, applicability
judgment or authority. Qualifications can lie elsewhere in the note. Read the
complete current note before editing it or relying on broader claims. Hooks
validate an API-supplied pair and do not fetch a duplicate opening; their older
whole-read excerpt path remains separate.

An optional-field prototype showed no native uptake in its development trial.
Receipt binding still requires evaluation before promotion: exact
byte and budget tests do not establish better source application, task outcomes
or fleet adoption. Both API and adapter versions matter; older APIs can reject
an explicit `opening_bytes` field and running MCP sessions can retain old tool
catalogs until a new session initializes.

## Enabling binding (default off)

- **Setting.** `CAIRN_SOURCE_OPENING_BINDING` names the authenticated profile
  principals (the `principal` values in `identities.json`) whose new context-index
  receipts bind the policy: exact principals separated by commas, with no leading
  or trailing whitespace in each entry. Internal spaces are part of the exact
  principal name; entries are never trimmed.
  Unset or empty enables none. It is deliberately not an `identities.json` member:
  an older binary refuses to start on an unknown identity field but ignores an
  unknown environment variable.
- **When it takes effect.** The setting is read once when `cairn serve` starts.
  Editing the environment changes nothing until the server is restarted, which the
  operator performs separately. A restart also clears the volatile
  [client observations](client-observations.md).
- **Malformed means refuse, not guess.** Empty entries, leading or trailing
  whitespace in entries, duplicates,
  control characters, more entries than the identity limit, or a principal that is
  not a configured identity stop `cairn serve` at startup with
  `invalid source-opening binding configuration`. A wildcard is just a name. The
  set is never widened or partially applied.
- **Granularity.** One setting per authenticated profile principal. Every session
  of a shared profile (for example the hosted agent profile) changes together; a
  trial needs its own principal. Agent-session requests (`--agent-id`) follow the
  authenticated profile that carries them, not `agent/<id>`.
- **Who never binds.** The operator CLI that opens the database directly and any
  embedding host bind nothing unless the host sets `core.Channel.SourceOpeningBinding`
  itself. There is no request or payload field for it.
- **Unchanged by the setting.** Ranking, selection, budgets and costs; the reader
  guard and the `426` refusal below; explicit `opening_bytes`; whole-note reads;
  and every receipt that already exists.

## Disabling versus downgrading

Two lifetimes matter. A **live handle** is a receipt's expansion session, which
ends 15 minutes after the receipt was created. A **retained package** is the
sealed receipt body with its replay, recompile, retry contract and cached replies;
nothing purges it.

- **Disabling binding** (setting removed, then a restart) stops new receipts from
  binding. Receipts already bound keep their policy: until their live handles end
  they still refuse undeclared readers on offset-above-zero pulls, and their
  retained packages replay, recompile and retry as before on this build.
- **Downgrading the binary** to one that predates the sealed field is a different
  operation. Such a binary cannot reproduce a bound receipt's seal: expansion
  (span and whole reads), replay and recompile return `INTEGRITY_FAILURE` and a
  retry of the compile request returns `STALE_PACKAGE`; receipts that were never
  bound stay readable. Waiting 15 minutes or disabling binding does not change
  this, because retained packages outlive live handles indefinitely. Once any
  receipt has been bound, this build (or a later one) is the lowest safe binary.
  Downgrading is safe only if binding was never enabled.
- **Verification.** A bound body contains the sealed text key `span_context`;
  screening `semantic_body` for those bytes finds bound receipts (a screen, not a
  proof). Combine it with the recorded enable time.

## Capability declarations and reader readiness

The authenticated `/v1/version` response carries an optional
`source_opening_capabilities` object (`cairn.source-opening-capabilities/1`) with
two required booleans: `reader_guard` (the server refuses a paired read to a reader
that did not declare support) and `receipt_binding` (new context-index receipts
for this authenticated principal bind the policy now). It is separate from the
closed `retrieval_capabilities/1` object. Clients declare `reader_capabilities`
(`cairn.reader-capabilities/1`, boolean `source_opening`) inside their
`cairn.client-diagnostics/1` header; the Go CLI and MCP facade of this build
declare it. `cairn_client_info` reports the facade's own `source_opening`
(`registered_pull_contract`) and the API's `source_opening` and
`source_opening_binding` (`api_source_opening_capabilities_v1`), and `cairn clients`
shows each cohort's reported `reader_capabilities`. A missing, malformed or
unrecognized declaration is `unknown`, never ready; an older server or client
simply omits the member. All of this is a reported claim: an older relay drops the
diagnostics header, observations are volatile (24 hours, one API process, cleared
by a restart) and never exhaustive, and no declaration proves that a running
facade, relay or hook copy is ready.

Readiness has to hold for every reader of the profile before binding is enabled
for it, because a receipt created by one reader can be expanded by another:

- **MCP facade.** A running facade keeps the binary it started with; replacing
  the file does not upgrade it. An older facade decodes the response without
  `source_opening` and is refused with `426` for bound receipts.
- **CLI and per-call readers.** The CLI process starts per call, so replacing the
  binary is enough. An older CLI cannot declare support and loses the
  offset-above-zero passage through `PROTOCOL_UNSUPPORTED`.
- **Relays.** An older relay does not forward the declaration, so capable clients
  behind it are refused until it is upgraded.
- **Lifecycle hook copies.** In the tested bound Claude and ambient routes an
  older hook copy with a capable CLI preserves the pair and makes no extra
  opening read under the current two-call eager limit, but does not validate the
  pair and may keep a misleading unavailable label; the current copy validates the
  pair and omits the label. This is not a wider compatibility claim.

## Readers and relays

An API client receiving a paired expansion must declare
`Cairn-Accept-Source-Opening: 1`. The current Go client sends it for note pulls
when decoding into `core.Expansion` or `json.RawMessage`, including the current
MCP facade and CLI. Other response types do not implicitly declare support.
Raw HTTP consumers must preserve both pieces before sending the declaration.
This header conveys response support only; it grants no access or authority and
does not change request identity.

The API refuses a paired read with `426 PROTOCOL_UNSUPPORTED` when the declaration
is absent. The status and code are unchanged by the binding setting. This check follows current access and integrity validation, precedes
cached delivery and charging, and leaves the original request UUID reusable after
upgrading the reader. Malformed or duplicate declarations return
`400 INVALID_REQUEST`. Whole reads, spans starting at zero, and old-receipt spans
without an explicit opening remain available without the declaration.

Updated relays forward the declaration but never supply it themselves. Older
relays may strip it, causing a refusal even for a capable client. Upgrading the
relay permits the same request to succeed. This does not require a wire protocol
version increase; existing clients and servers retain unrelated operations.
Installing a new binary does not upgrade running MCP facades. Such facades must
start with the new binary to receive paired expansions. Once binding is enabled
for a profile, an API that has it will refuse their paired reads rather than
charge for discarded context; with binding off (the default) no receipt carries
the policy and nothing is refused.

Reader readiness and these declarations do not establish that openings improve
relevance or task outcomes, and they do not make any deployment ready.
