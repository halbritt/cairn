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
