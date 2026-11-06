# Work view acceptance fixture

Run the real CLI, Store and local browser against a newly built candidate and a
fresh PostgreSQL cluster. The only substituted service is the Plane CLI process:
`plane.py` returns synthetic Plane envelopes and refuses every command outside
its read allowlist. No model, owner profile or production database is used.

From a checkout containing this fixture, with PostgreSQL, Go, and the installed
UIPass environment (including Chromium):

```sh
uv run --project "$HOME/git/uipass" --frozen python \
  scripts/work_coordination_ui_fixture.py \
  --candidate /absolute/path/to/cairn-candidate \
  --output /tmp/cairn-work-ui-fresh-run
```

The output directory must be new or empty. The candidate is copied to private
build source and fingerprinted; an installed Cairn binary is never substituted.
Optional `--web-overlay /absolute/path/to/reviewed-checkout` copies only
`web/workview/app.js`, `index.html` and `styles.css` into that private source.
The report records the base revision, each overlay hash, the composed input hash
and binary hash. Neither checkout is modified. VCS stamping is disabled for the
private copy; the report owns its build provenance. Each visual state uses
a real server and collection. The script copies the checked-in UIPass manifest
into the private output fixture tree and supplies a synthetic login cookie.
Declared storage paths stay within that tree. It does not insert synthetic Cairn
responses or browser-side data to make an unfinished feature pass. Explicit
response-delay/loss cases forward the real handler's response or drop it.
Use `--retain-binary` when another disposable proof needs the exact same build;
it retains `cairn` in the private output directory and records that path and hash.
For a readonly source capture, pass `--candidate-manifest /path/to/capture.json`.
The `cairn.work-ui-source/1` manifest binds the source directory, original Git
revision and dirty status, every input path/hash, and the complete input digest.
The fixture checks all captured files before staging and after execution. It
refuses missing, extra, changed or symlinked inputs. The capture itself must have
been made with before/after source equality; the fixture records its manifest hash.

`report.json` records source/binary identity, CLI and browser assertions, failures,
and UIPass bundle locations. Each viewport has its own browser mutation journey.
Assertions check retained state through the CLI after browser actions. Missing
controls, unknown commands and HTTP errors fail; none is skipped as unsupported.
Guarded HTTP edits also reject missing, malformed and wrong item guards and
missing, nonpositive and noncurrent revisions without changing the exact row.
`review.md` separates these results from the screenshot inspection that the
reviewing agent must perform. Screenshots and credentials are never committed.

The stale fixture ages only the disposable refresh observation timestamps, then
performs a real failing refresh. The freshness fixture starts with a successful
snapshot 116 seconds old and waits for the real server and browser to cross the
two-minute boundary. It requires a new HTTP read reporting stale data, the stale
badge on both viewports, an unchanged retained snapshot and no Plane invocation.
It does not replace the browser clock. The delayed-freshness case delivers an
actual fresh HTTP response only after its snapshot passes two minutes, then
requires another real read to correct the display.
The `freshness_read_failed` check drops one genuine boundary-read response and
requires an unknown-freshness label with the retained timestamp/cards and a read
error. Its desktop/phone screenshots come from the affected pages. The report
explicitly excludes this page-local fault from UIPass, whose separate navigation
would read a different state; inspect both retained fault screenshots.
The presence case stages only the actual execution's disposable expiry timestamp,
then verifies the qualified status changes at that deadline. It checks the exact
expiry and Plane assignee fields, preserves focused inline and note/association
drafts across the timer and a real blocker-change detail read, and verifies that
the browser did not change session/event/delivery rows. The disabled-project
case uses the real disable command and checks retained details with unavailable
refresh/new-association/annotation actions.
The unavailable fixture
publishes and associates an actual request, then forgets its payload through the
operator preview/forget contract. Every other work transition uses the public
CLI or browser. These cases do not establish native delivery or model behavior.

Before another explicit refresh, the fixture ages `next_refresh_allowed_at` only
after confirming that no refresh is in flight. It never clears a claim to admit
work. The empty-cache failure uses an actual browser Refresh and requires the
retained project error and rate state to appear without a page reload. Typed views
use a real published response group, exact record version and a real local
process run receipt; a successful process is not treated as an accepted task.
Additional desktop/phone screenshots show the typed statuses after scrolling
within the actual detail modal. No record body is rendered in those views.

Use `--scope wc02` for the artifact workflow; the default `--scope wc01` covers
the board, progress, blockers and coordination projections described above.
Scope selection is explicit in `report.json`. Required routes and commands in
either scope fail when absent. Run WC02 against a candidate containing its Store,
CLI and operator HTTP handlers, with the matching browser files.
For a targeted regression check, repeat `--state` for each state to execute,
such as `--state freshness --state stale`. The report lists all excluded states;
a targeted pass does not establish the omitted workflows.

The artifact scope registers all four kinds through real browser forms on both
viewports, links and removes exact revisions, checks persisted state through the
CLI, and refuses missing/wrong item and stale revision guards. A single link
request exercises response loss: the browser interception calls the real handler,
confirms its commit, then drops that response. The manual retry must reuse the
same request UUID and return the same link revision. No synthetic successful
Cairn response is supplied.

Real CLI transitions create 53 link history entries and 56 reverse-impact rows
to exercise continuation without table inserts. An older report revision remains
selected after a newer one is registered. Separate states cover empty links,
forgotten result metadata and disabled artifact admission with retained reads,
unlink and purge. UIPass captures the item links, history and exact-revision
surfaces; the agent must also inspect the retained scrolled screenshots.
Current-read cases purge a source through the real CLI and require a refused
impact read to clear old metadata. Another case forwards a real purge reply only
after browser Back selects a different revision; that reply must not replace the
new view. A malformed trailing JSON body must refuse without registering a row.

## Coordination panels

Use `--scope coordination` with a coherent WC01/WC03/WC04 candidate. The fixture
creates real registered owners, request and response-group
dependencies, waits, cancellation and exact work associations through existing
CLI/API operations. Its current cases check wait membership/provenance/pagination,
an unsupported recipient, empty membership and disabled retained reads. The
transition case waits for a real 30-second GET, forwards an actual delayed reply
after opening another item, removes an exact association, and uses the operator
restore fence to make a retained cursor refuse before reloading the first page.
It checks that these reads preserve an unsaved note and do not mutate coordination
state. The fence case does not perform a database backup/restore. Missing
schema or the selected operator endpoint fails; no successful Cairn reply is
substituted. It compares complete retained coordination rows around browser reads.

This scope does not yet establish complete coordination acceptance. Its report
lists pending native supported-notice lifecycle/pagination, privacy and complete
native restore behavior. Supported quiet-notice setup reuses the verified native
fixture's physically activated launch/session binding; it does not insert a
capability or queue row. Run the separate native panel fixture against the same
immutable source and retained binary:

```sh
uv run --project "$HOME/git/uipass" --frozen python -B \
  tests/fixtures/work_ui/native_queued.py \
  --source /absolute/path/to/captured-source \
  --capture-manifest /absolute/path/to/capture.json \
  --binary /absolute/path/to/retained-cairn \
  --binary-report /absolute/path/to/work-ui-report.json \
  --native-manifest /absolute/path/to/verified-native-manifest.json \
  --output /absolute/path/to/fresh-native-panel-evidence
```

The build report must bind that binary to the same unchanged source with no web
overlay. Native scripts and lifecycle inputs come from the captured candidate;
the adjacent callback fixture has its own recorded hash. The native fixture uses
the verified patched Codex TUI and a local scripted Responses server. Its bounded
callback creates an actual supported backlog, checks exact notice membership and
association provenance across both pages and viewports, expands a known source
row to check that its body is absent, and runs UIPass on the notice views. All
reads must leave the queue and ordinary delivery state unchanged. This callback
does not claim the native fixture's separate full delivery workflow, expiry or
restore acceptance. Inspect its screenshots and retained pending-case list.

## Suggestions fixture

The WC05 fixture is prepared separately in `advice.py`. Browser execution and
visual review require a coherent candidate containing the selected advice API
contract; source checks alone do not establish acceptance.

```sh
uv run --project "$HOME/git/uipass" --frozen python -B \
  tests/fixtures/work_ui/advice.py \
  --candidate /absolute/path/to/immutable-cairn-candidate \
  --candidate-manifest /absolute/path/to/capture.json \
  --output /absolute/path/to/new-external-evidence-directory \
  --retain-binary
```

This reuses `DisposableHost`, actual migrations, the candidate CLI/server,
terminal-code login, and the synthetic Plane boundary. It registers the exact
hash of `advice_provider.py` copied into its owned fixture home. The provider is
an actual subprocess that accepts the adapter's native envelope and records
invocation and canonical-input digests. It has no model or network client and
uses no owner credential. Its protocol envelopes are synthetic test responses;
they do not prove native Codex, subscription authentication or useful model advice.
Those claims require the separate native tests and authorized subscription smoke.

The scenarios exercise explicit first-policy creation and profile/source/artifact
selection; exact retained input inspection; progress plus artifact subset apply,
then the remaining blocker; separate application compensation; blocker resolution
and reopening; rejection and no-suggestions; source/model refusals; source
forgetting; disabled retained reads and Undo; intervening edits; response loss and
exact retry identity; item switching; and resumed polling after a delayed failed
write. Default feature, policy and runtime gates are used through their actual
operator commands. No policy table, role or production configuration is changed
to make the fixture pass. Missing required functionality fails.

Response faults forward an actual response, delay or drop it, or alter one real
application receipt to test rejection. They do not substitute a successful Cairn
workflow. The polling case crosses a real 30-second interval with no fake clock.
Intended work effects are checked through the real CLI, and coordination rows
are compared before and after. The refusal screenshots belong to the actual
failed requests; a separate UIPass navigation would not reproduce those transient
errors without another invocation, so that exclusion is recorded explicitly.
Other retained states use `.uipass-work-advice.yaml` at desktop and phone sizes.

Repeat `--state` to select a bounded scenario. The report lists exclusions and
the provider invocation count. Inspect the actual images and UIPass findings
before claiming visual acceptance. No completed browser evidence is implied by
this prepared fixture.
