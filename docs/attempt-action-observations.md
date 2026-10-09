# Action observations from failed attempts

`scripts/trial_attempt_actions.py` supplies a separate bounded projection of
native-reported action and terminal permission-denial facts. It can retain these
facts when the success-only `Frames` reader refuses a native error result and
rolls back its action sink. The existing reader and retention contracts stay
unchanged. This module has no executor or persistence entry point.

The owner supplies the expected session, workspace, exact declared required
command strings and capture timestamps. Each accepted chunk contributes to the
stream digest and byte count. A well-formed event commits its action facts as a
batch. A malformed event preserves earlier observations, marks coverage unknown
and retains no raw input or exception text. Interruptions propagate; the owner
must record the interruption as uncertain observation. Coverage describes this
projection of delivered bytes, not a complete provider conversation or process
exit. Missing terminal output remains a separate owner observation.

Denials are processed even on native error results. Matching requires an observed
call with the same tool and unambiguous hashed ID. Required-operation labels use
exact command equality against caller declarations. A compound command or native
denial payload cannot supply that match. The selected output contains command
hashes, fixed categories, declared operation labels and reported action states;
it omits raw commands, tool contents, final answers and private transcript text.
It neither validates the effective native permission matcher nor authenticates
the caller's declared required operations.
If a denial also reports command input, contradictory or malformed input marks
coverage unknown and prevents a required-operation inference. Absent input stays
explicitly unobserved; matching still describes the reported tool and call ID.
Each denial preserves the prior reported action state and reported command exit
before reconciliation, so the denial cannot erase a conflicting earlier result.
Repeated denials of the same call mark coverage unknown and remain visible.

An empty action list or an unfinished action can describe actor incompletion.
These facts do not alone mark projection coverage unknown. Structural, identity,
timing, path or bounded-observation faults do. Action status and coverage remain
observations; the module never decides cell validity, provider admission or task
grades. Root must bind the stream, session, occurrence, workspace and actual owned
wait/release observations, then establish agreement of the frozen prompt,
implementation permission profile and rendered settings. Only that owner can
apply the required in-profile denial rule or distinguish a known actor budget
stop from observer/control uncertainty. Serialized hashes and flags supply no
runtime authentication.

Defaults are 4 MiB total, 1 MiB per line, 2048 frames and 256 action/denial rows.
Explicit source-only limits may be selected up to 64 MiB total, 8 MiB per line and
20,000 frames; they must be pinned with the eventual reader profile. These caps
bound observation storage and do not widen the original memory, tool, final,
search/pull, hook or actor budgets. Chunk size remains 65,536 bytes. Hitting any
cap marks coverage unknown; later chunks still contribute to the digest. An
oversized/nonbyte chunk is not accepted into that digest and is also unknown.

Constructed tests cover failure-reader rollback, exact required-command matching,
unmatched/ambiguous denials, empty and unfinished actor work, transactional frame
refusal, malformed JSON and bounded output. No native provider attempt or actual
owner bridge is established by these tests. The module is not mounted or invoked
by the provider-free supervisor fixture, and no runtime admission is granted.
