# Swarm view acceptance fixture

`scripts/swarm_view_fixture.py` checks the swarm view page
([docs/swarm-view.md](../../../docs/swarm-view.md)) against a newly built
candidate, a fresh PostgreSQL cluster and a real Chromium. Nothing is mocked:
every row comes from the real CLI and API, every HTTP reply comes from the real
`cairn swarm serve`, and no row is inserted by hand.

Run it from a checkout with PostgreSQL, Go and the installed UIPass
environment (which provides Playwright and Chromium):

```sh
uv run --project "$HOME/git/uipass" --frozen python -B \
  scripts/swarm_view_fixture.py --output "$HOME/.cache/swarm-ui-runs/RUN"
```

`--candidate DIR` builds another checkout (default: this one). The output
directory must be new or empty and outside the checkout. The fixture prints one
JSON line with the page address (token in the fragment), then either runs the
browser assertions or, with `--hold`, keeps everything up for a person or an
agent to drive a browser. Interrupt a held fixture to stop the server and
remove the cluster and its runtime directory.

## Isolation

The candidate is built with `go build -buildvcs=false` into a private runtime
directory; the report records the source revision, dirty paths and binary hash.
After the build the fixture clears its process environment and keeps only
`PATH`, `LANG`, `TZ`, a private `HOME`, `TMPDIR` and the Playwright browser
path. `CAIRN_HOME` and `CAIRN_DATABASE_URL` name the fixture's own state
directory and database, so the live store and the installed state directory
cannot be reached. The swarm server gets its own token file and bindings
directory under the runtime directory.

## What it seeds

One campaign, `swarm-fixture`, in a fresh collection:

- a root, a lead and three workers registered as sessions with different
  harness names (claude, codex, opencode, agy);
- a description and role labels when `team set-campaign --description` and
  `team set-member --role-label` exist (CAIRN-828); otherwise skipped and
  recorded in `report.json` under `campaign_metadata_flags`;
- a request answered with a response; a three-recipient grouped request with a
  group-closure wait and one counted reply; a direct request held under a live
  lease with a direct-reply wait; a failed delivery (`processing_failed`); a
  superseded notice and its pending successor;
- a board question on the `agent-board` topic and two notices addressed to the
  operator principal;
- ledger items of each kind: assignments (through `ledger send`), an active
  hold, an escalated blocker, a decision owned by the root and one owned by the
  lead;
- a worker that leaves with a pending notice (work sent to a stopped session);
- a root coordinator handoff to a fresh session (`ledger handoff`);
- a notice whose summary carries markup and is long.

- a workstream decision, a missed checkpoint and a timed-out wait (through a
  real `schedule-serve --once` sweep), and a handoff due (a compaction limit of
  1 and one `ledger compact` record), so ten needs-you row types appear;
  `owner_turn` and `stalled_agent` cannot be produced and are listed in the
  report;
- a session in no campaign with a message no campaign claims;
- for the context room: a private bindings directory with a Claude and an
  OpenCode binding, session state files whose process is a long-lived `sleep`
  child of the fixture, and sample Claude transcripts, so the real transcript
  reader measures one member at 92% of its window (near the limit), one at 42%,
  and reports the OpenCode member as unknown.

A step that the candidate refuses is listed under `seed_gaps` and does not stop
the run; the checks that need it then fail on their own.

## Browser assertions

Each is a named entry in `report.json`. A check whose data comes from a slice
that has not landed carries a `slice:` category and fails, so the report shows
what is missing instead of passing on an empty section. A check that needs a
store-side field not yet on the branch carries a `waiting:` category and says
which field. The current checks cover sign-in, regions, live updates, reloads
and restarts, every `reset` code, a database outage, sign-out in another tab,
the context room, who works on what, header counts, waiting facts, needs-you
rows and their copyable commands (each is run and must exit 0), the cancel
preview and cancel, a refused cancel, acknowledge on the inbox head, send,
who can be written to, and the paper cuts found in browser acceptance.

- Sign-in by fragment, and the token leaves the address bar.
- The four regions render; agent rows carry harness, context room and pane
  cells; no server text becomes markup; a tracker identifier becomes a link
  when the server is started with `--plane-url`.
- A new event appears without a reload, exactly once.
- A reload, and a server restart on the same port (the page signs in again
  through its token field), neither lose nor repeat an event, including one
  published while the server was down.
- Needs you lists the board question and the operator-addressed event and not
  the lead-owned decision (`slice:CAIRN-831`).
- The cancel preview names the exact recipient, the direct-reply and
  group-closure waits and the counted replies before the confirm control is
  enabled; after confirming, the sibling deliveries and the counted reply are
  unchanged in the store, read through the CLI (`slice:CAIRN-830+833`).
- A refused action shows the store's reason: the send route's refusal, or,
  when sends are accepted, an acknowledge of the newer operator delivery, which
  the server refuses because it claims in order.
- No control starts, stops or focuses a session, sends keys, manages a
  worktree, triggers compaction or selects a model, and "settle" appears
  nowhere.
- At phone width the page does not scroll sideways.

Screenshots (`desktop-campaign.png`, `desktop-final.png`, `phone-campaign.png`,
`phone-landing.png`, and one per failed check) go to the output directory,
never into the repository. `review.md` summarises the run; it is not a visual
acceptance claim, so inspect the images.
