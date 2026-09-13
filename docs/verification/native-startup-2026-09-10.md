# Native startup investigation — 2026-09-10

An isolated OpenCode startup completed in 7.2 seconds with no Cairn connection.
It did not reproduce the earlier 60-second timeout in the native context tests.
The cause of that timeout remains unknown. No product code, timeout, dependency
configuration or host networking was changed.

The original failure occurred on the first existing capture call, before testing
the new search argument. Two subsequent complete native runs passed. This
investigation used OpenCode 1.18.21 with fresh HOME/XDG directories, a synthetic
echo tool and a non-serving local model endpoint. The process exited zero and
returned the exact fixture text. Its 7.2-second duration includes tracing overhead
and is one diagnostic observation, not a startup performance benchmark.

The system-call trace records external TLS activity during setup, without a
Cairn executable or model-endpoint call. OpenCode's pinned
[configuration source](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/config/config.ts)
starts installation of the matching plugin SDK in its configuration directories
and retains the dependency tasks for later joining. The downloaded source's Git
blob identity matches the previously retained v1.18.21 tree.

This identifies startup dependency work as something to distinguish from a Cairn
API request during diagnosis. It does not prove that installation, registry
latency, IPv6 or another dependency caused the original timeout. Increasing the
deadline, seeding packages or changing network routing would require stronger
evidence. Revisit on a timed-out run with its startup trace retained.

The [manifest](native-startup-2026-09-10.json) retains source identities, diagnostic
artifacts and the decision to abstain from causal attribution or a speculative
repair. The original failed check remains unchanged. No answering-model task,
memory-value claim or additional harness qualification is added.
