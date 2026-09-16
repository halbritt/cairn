# Native automatic wakeup account trials — September 16

Coordination v1 remains incomplete. These trials extend the earlier
[ai-newsroom result](idle-session-wakeups-2026-09-16.md). They establish selected
live exchanges, not general task reliability or interactive cancellation.
Times below use America/Los_Angeles (UTC−07:00).

## Automatic exchanges

Each successful request was published after the target returned idle. The
presence service supplied the wakeup; the parent sent no terminal prompt after
publication. The recipient read its exact source, explicitly completed once and
published a correlated reply. The parent read the result and acknowledged the
reply. Native holds finished with `delivery_completed`.

| Target | Request event | Observed result |
| --- | --- | --- |
| Codex-two, existing ai-newsroom | `129faeac-923f-45da-ad24-84031c9cebfe` | Earlier automatic exchange passed; same conversation/process returned idle. |
| Agy, owner-offered agent | `80b2ee8f-1be8-451f-8ba7-37148039e210` | One handling attempt; reply collected 09:56:45. Result `57b2f74b-ffae-4d76-9d25-3a0a55d98191/1`. |
| Hermes, owned test conversation | `d00721ec-b9ed-4597-8299-db5e9982fe1e` | One handling attempt; reply collected 09:57:57. Result `cfae0f39-7e76-4145-97c2-7907610a1b93/1`. |
| OpenCode, owned local-model conversation | `62872997-b675-4000-b488-3731ea13c9ab` | One handling attempt; reply collected 10:11:55. Result `72cff8f4-1c96-4e6c-a6e0-e7e053157af5/1`; same native PID/execution checked after handling. |
| Claude-two, fresh owned Fable 5.1 conversation | `0be15e44-7ab6-4950-b483-f83d02621b67` | One handling attempt; reply collected 10:46:33; hold finished 10:46:34. Result `2b31456f-9aa1-48a5-928e-c737b2c44486/1` matched the supplied identity and repository HEAD. Parent read/acknowledged 10:47:05 and checked the same native conversation returned done. |

Hermes' initial PID was recorded, but was not independently rechecked at the
final observation. Its same-conversation result and native hold are verified;
do not strengthen that to a final PID check. OpenCode needed its missing Herdr
integration installed, then the owned idle conversation resumed before the
request was published. The installer now checks the selected account's Herdr
integration rather than assuming that another account's installation covers it.

## Failed and pending account trials

The resumed Claude-two conversation refused ordinary profile-based source reads
or completion despite the owner's authorization. Requests
`8f7ccc88-9416-4da7-b720-ab8395d1d0fd`,
`0d5938ea-e673-4427-96a2-96285dec3036`, and
`d1f88101-fa5b-4ca6-bce9-5c35d95771bb` retain `processing_failed` outcomes.
Clarifying the wake prompt, installing standing workflow guidance, and changing
the model in that conversation did not resolve the refusals. They were distinct
probes, not silent replays of a failed delivery.

The fresh Claude-two baseline loaded the coordination guidance from startup and
received explicit initial authorization for later bounded inbox checks. Its
success does not repair or explain away the resumed-conversation failures.

Claude-one reported a usage limit with automatic continuation scheduled for
11:10. Codex-one reported a usage limit and offered usage credits. At the 10:43
observation neither had completed its test setup, and no inbox request had been
published to either. No credits were purchased. Their live trials remain open.

## Native Codex queue and control investigation

Commit `b45bf25` adds the explicit Unix-listener queue route and account installer
preflight. It passed 135 Python tests and `make check`; the final response-shape
guard also passed the five queue tests. The Python watcher/helper were installed
and the presence service restarted successfully. Store/API/schema remained at
`e81057b` / 047; no store changes or production database tests were performed.

An owned Codex 0.154.0 app-server and remote TUI retained an unsubmitted draft
through native queue calls. Repeated `clientUserMessageId` after consumption
created another turn, so Cairn's retained marker must suppress replay. Adding
after an interrupted turn could leave input paused; explicit start of that exact
queued item resumed it. A start attempted during an active 20-second command
was refused, and the queued message ran after the active turn completed.

The first full request, `841162ec-ca28-4efe-94ed-b1c0f5896b52`, woke automatically
but needed three parent approvals for local source read, completion and reply:
the probe had been created with a read-only sandbox. Its repository inspection
was also outside the isolated conversation's standing workspace restriction and
was correctly omitted. This is an assisted, partial result, not an unattended
acceptance. Result `d96e99c2-cf6c-4c17-8379-c54afc3b8764/1` was read and acknowledged.
The draft remained; the native hold finished. Subsequent-turn settings were
corrected only for this owned probe, and a narrower unattended request
`21aa0389-d7ff-4fd0-aa90-d74b9e419943` passed without parent input after
publication. It completed once at 10:52:15, its hold finished at 10:52:19, and
its reply collected at 10:52:24. The parent read result
`c62c98c8-abb0-4cb3-8a05-45beb8a1ce39/1`, checked the same PID/start/boot and
agent/execution returned idle, observed the unchanged draft, and acknowledged
the reply at 10:53. This establishes the narrower unattended exchange on the
explicit native listener. The native start can race automatic queue consumption
and return an uncertain-start diagnostic; the retained marker prevents a resend
and native completion reconciles it.

An exact `turn/interrupt` stopped a native turn but left its long-running shell
command alive. A subscribed native `item/started` event identified the turn's
command item/process; interrupt followed by termination of that bound background
terminal stopped the owned probe. This is experimental evidence for a stop
adapter, not implemented Cairn cancellation. Request-exclusive turn mapping,
tool cleanup, crash recovery and the other harnesses remain required work.

Selected local artifacts are under `/tmp/cairn-*-automatic-acceptance`,
`/tmp/cairn-claude-two-fresh-acceptance`, `/tmp/cairn-codex-native-control`, and
`/tmp/cairn-codex-native-queue-acceptance`. Raw model output and operational state
are not committed.
