# Receipt-checked ordinary memory entry

The selected construction interface is Release agent-1270's 88e057bc/v1, under
Root ad940a23/v1. The private image selects this entry when the leading argument
is `--receipt`. The existing bare `--config` entry remains outside staged hook
selection.

The actual Go vector is `IMAGE memory` followed by the eight R02 selection
flags: `--receipt`, `--receipt-sha256`, `--role memory`, `--entrypoint`,
`--config`, `--hook-timeout 13`, `--launch-root` and `--shell`. The original
bounded event bytes remain stdin; public output is the ordinary hook result.
V comes from `os.Args`. Entry time is captured before parsing and verification;
H=13 establishes D once for verification, stdin and body/SDK work. This direct
path accepts no external deadline or descriptor. L uses R02's shell quoting and
content-addressed script formula with the actual Go vector. Runtime verification
checks L's private ownership, mode and exact bytes.

The receipt's complete closure, alias/role/config selection, selected and
executing image identity, disabled manifest and exact ordered descriptors are
checked before any profile is trusted. Every pinned profile must match its
exact digest and already be unbound. The executing descriptor stays open through
the body. No activation is resolved, binding removed or bound generation
downgraded. OpenCode `agent_tools` SessionStart and UserPromptSubmit are the
only newly reached cells. The body receives the checked config snapshot and
original environment/cwd, retaining ordinary admission and SDK behavior.

The process corpus uses Release's committed 6fe25e21 actual R02 installer,
UnboundFixture and complete installed Python verifier/closure. Python and Go
receive the same issued receipt; each uses its own actual V and L. The fixture's
`source_revision` label is opaque provenance. The test log records independent
installed Source closure and Go image hashes, plus the receipt digest.

Twenty-two tests cover Start/prompt streams, normalized state and ordered API
payloads; changed receipt, closure, configs, manifest and image; missing and
malformed data; selection flags; mixed or enabled generations; symlinks; disabled
and native-session guards; and the existing OpenCode account-variable behavior.
Only independently generated request UUIDs and recall timestamps/durations are
normalized. The default issued fixture's Source SDK origin reports `basis=reported` and
`component=lifecycle-memory` without an implementation ID. The Go SDK origin
also reports the independently checked executable digest. The corpus asserts
that difference explicitly; Source attribution comes from its checked closure.

The B1 repair validates the eight long option names and their separate or joined
values before Go flag parsing. Separate values starting with a dash, single-dash
option spellings and invalid UTF-8 in the actual vector refuse before launch or
configuration checks. Timeout text must use decimal digits with an optional
fraction/exponent before its numeric value is checked against 13. The paired
corpus reproduces the former hex-float, single-dash and dash-value false accepts,
then checks refusal plus joined options and decimal timeout success. It also
exercises invalid filesystem bytes in receipt, launch-root and shell selections
through both actual processes. Python emits an uncaught UnicodeEncodeError for
those paths; Go emits the fixed compatibility-preflight refusal. The test asserts
each diagnostic separately, together with exit 1 and no API or state effects.

The shared receipt identity escapes DEL as `\u007f`, matching Python's
`ensure_ascii=True`. Actual-process pairs cover refusal of the former raw-DEL
snapshot identity and success of the canonical escaped identity with a DEL
alias. The canonical DEL `.py` alias also reports its independently checked
Source member digest in SDK attribution; the corpus checks that exact digest.
Other identity and Unicode handling stays unchanged.

Additional Go-only negatives cover changed L, a same-bytes executable with a
forged selected argv, and the original deadline while stdin remains open. Those
checks are not claimed as paired Python timing or descriptor evidence. Optional
trace-file failure timing is outside this finite corpus. Retained ordinary,
activation and R04 suites remain separate checks.

Reviewer N2 remains a nonblocking diagnostic difference: a changed manifest
containing invalid JSON produces Python activation/ValueError and Go
compatibility-preflight refusal. Both refuse before any effect. This repair
does not broaden that failure-phase cleanup.

This is a private consumer candidate requiring the existing agent-1279 review
and Foundation integration. Release owns switching staged rows after the
reviewed consumer lands. Current rows remain checked Python launchers. Public
lifecycle enablement, live apply, provider/capture/tool/bound expansion and the
Coordination constructor remain outside this change.
