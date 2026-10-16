# SDK diagnostic launch mapping

`scripts/trial_sdk_launch.py` validates one decoded SDK spawn tuple and returns
an immutable native launch plan. It performs no process, IPC or SDK operation.
The fixed profile in `trial_sdk_launch_profile.py` is the selected synthetic
offline diagnostic: six tools, eight turns, Sonnet/high, empty setting sources,
isolated settings/MCP paths and a synthetic backend environment. This profile
does not establish full evaluation-workload compatibility.

Construct `LaunchMapper(selected_session, reserve, release)` in the trusted
serialized owner. The selected session must be a canonical lowercase v4 UUID.
`consume()` accepts one ordinary built-in dictionary with exactly `command`,
`args`, `cwd` and `env`; attempts are consumed even when validation fails.
This API expects plain decoded data, not arbitrary Python objects with custom
equality or hashing behavior. The AbortSignal stays outside the serialized tuple.

Every required flag and value must match the fixed profile exactly once.
Known separated and combined value forms and their original order are preserved.
One exact appended diagnostic instruction is optional. Existing print flags,
aliases, unknown or repeated flags, changed environment and foreign sessions
refuse before hashing. The mapper changes only `/unavailable/claude` to
`/native/claude` and prepends one `-p`.

The plan contains argv including executable, cwd, an immutable environment
snapshot, and whether the original initialize message must carry the instruction.
It also contains independent canonical byte counts and SHA-256 values for the
original tuple, the print-only proposal and native mapping. Canonical metadata is
`[command,args_without_argv0,cwd,sorted_env]`. The frozen 30-argument test fixture
has lengths 1128, 1133 and 1128 bytes respectively. These are representation
correlations, not sender authentication or actual launch evidence.

Hashing reserves each complete quoted ASCII token before encoding it; all fixed
profile tokens fit 512 bytes. `reserve(n)` returns exactly True with all requested
credit or False with none, and cannot fail after committing credit. `release(n)`
returns it once. Unknown grant outcomes refuse. Release uncertainty sets
`cleanup_unknown`, refuses the plan and is never retried. The owner must supply
these callbacks from the unchanged aggregate raw pool. No whole serialized tuple
is created; Unicode strings and parser/reference graphs remain working
allocations under the aggregate process-memory limit. This is not a measured
physical-memory bound.

The future native owner must still validate the complete original initialize
message, exactly one instruction transport, hooks, user and profile before
forwarding. It must establish authenticated IPC, actual exec and same-child wait,
and preserve original SDK bytes. In particular, a 512-byte write completion must
mean actual native acceptance; buffering part of a longer unvalidated line is
not a write completion. The pure mapper resolves none of these runtime contracts.

Run `python3 -B -m unittest discover -s scripts -p 'test_trial_sdk_launch.py'`
for the authored and independent tuple, mutation, one-use and credit-failure
checks. All original trial budgets, full CAIRN-107 criteria and capture HOLD
remain unchanged.
