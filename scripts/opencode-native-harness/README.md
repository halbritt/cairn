# Isolated native admission harness

Default invocation runs **model-free isolation checks only**:

```sh
scripts/opencode-native-harness/run-harness.sh
```

Each launch creates a unique owner-only `/tmp/cairn-opencode-harness-*` directory;
its path is emitted on stderr. No caller-selected fixture root is accepted.
The launcher clears the environment, uses `sudo -n unshare --net` solely to
create a network namespace and bring up loopback, then drops to the invoking
UID/GID/supplementary groups with all capability sets cleared and no_new_privs.
The fixed privileged setup does not execute supplied workload code.

Before workload exec, the verifier compares the actual namespace with the one
observed inside setup and the still-running outer process's pinned host
namespace/PID/start time. It requires exactly the up loopback interface, empty
IPv4/IPv6 main routing tables, only loopback routes, original credentials,
all-zero capabilities, no_new_privs, and an exact clean environment whose
HOME/XDG/TMPDIR paths point into the unique fixture. Inherited extra descriptors
are closed; socket stdio is refused. Setup or verification failure has no host
fallback. `HARNESS_IN_NETNS`, `HARNESS_FIXTURE_ROOT`, proxies and provider
credentials from the caller cannot bypass or alter setup.

The checks run libc and direct kernel syscall TCP/UDP external connection
attempts, require kernel routing refusal, verify host setns/root setuid fail,
and complete an HTTP streaming mock canary on loopback. They also test setup
failure without fallback and each credential/capability rejection. Selected
results live in `verified.json` and `isolation-checks.json`; logs remain inside
the unique fixture. No native agent/model runs in these checks.

After independent review, an explicitly selected native executable can be
probed. This command runs the same negative checks **inside the same namespace**
before starting native code:

```sh
scripts/opencode-native-harness/run-harness.sh --native /absolute/opencode-binary 1
```

The native fixture uses an empty workspace and isolated config with only the
mock provider enabled, explicit model on every prompt, denied tools, and no
configured plugins. Missing dependencies, unavailable replies, wrong model,
provider, tool calls or absent canary fail the trial; empty output never passes.
Any native observation is recorded separately from kernel isolation evidence.
Native admission behavior is not certified merely because this wrapper passed.

For another reviewed model-free/loopback workload:

```sh
scripts/opencode-native-harness/netns-isolate.sh /absolute/executable argument
```

A Python workload can import the adjacent `verify.py` and call `verify_current()`
at startup before intentionally adding any environment entries. The launcher's
parent remains alive until workload exit, allowing identity re-verification.

This isolates network routes and ambient environment; it is **not a filesystem,
process or Unix-socket sandbox**. The workload still has the original user's
filesystem permissions. Do not read host profiles, invoke host proxies/managers
to start networked work, or copy credentials into the fixture. A local systemd
`--scope` test requires separate reviewed access to the user bus and must retain
this namespace; manager-started services are outside this boundary. Arbitrary
adversarial commands are not covered. Rootless/unavailable sudo setups fail;
there is no LD_PRELOAD or non-kernel fallback.
