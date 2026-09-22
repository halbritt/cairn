# Hermes owned-request model-loop canary

`scripts/check_hermes_owned_model_loop.py` exercises a real `AIAgent`, the native
OpenAI HTTP client, real terminal tools, and the reviewed Cairn coordination
plugin against a scripted loopback model. It uses actual native queue admission,
owner steering, exact-token abort, and process-scope APIs. The CLI carrier holds
an already constructed agent; its admission/control methods are not replaced.

This is a controlled native integration check. It does not measure an external
model or test full interactive CLI startup. Its local lifecycle process records
selected hook metadata instead of contacting a Cairn API. It neither registers
an agent nor claims an operational inbox.

Run static preflight first, with a clean, pinned native checkout:

```sh
python3 scripts/check_hermes_owned_model_loop.py --preflight \
  --native-root /absolute/pinned-hermes \
  --harness-root /absolute/reviewed-harness/scripts/opencode-native-harness
```

Execution requires the separately reviewed network namespace harness. Supply an
absolute Python executable with Hermes dependencies and the absolute fixture
path:

```sh
/absolute/reviewed-harness/scripts/opencode-native-harness/netns-isolate.sh \
  /absolute/hermes-venv/bin/python \
  /absolute/cairn/scripts/check_hermes_owned_model_loop.py --run \
  --native-root /absolute/pinned-hermes \
  --harness-root /absolute/reviewed-harness/scripts/opencode-native-harness
```

The fixture re-verifies namespace identity, loopback-only routing, dropped
credentials/capabilities, and the exact initial environment before adding its
own configuration. It then permits the current user's local systemd bus for
caller-executed `systemd-run --scope` launches. A real contained child must prove
it inherited the isolated namespace before any model request runs. This harness
is not a filesystem sandbox. The fixture uses fresh `HOME`, `HERMES_HOME`, XDG
paths, and working directory; it does not load the owner's native profile.

The configured profile uses local terminal execution, a direct OpenRouter-style
chat-completions client with a synthetic key and loopback URL, only the reviewed
coordination plugin, manual tool approvals, and disabled tirith, memory,
checkpoints, compression, and background review. These are fixture settings,
not recommended changes to an owner's profile. Coverage initialization remains
the actual native policy's responsibility.

The assertions cover:

- A real no-tool model response and complete natural cleanup.
- Cancellation during a real foreground terminal call, with positive scope cleanup.
- A detached background child surviving its launcher, retained in the owned
  scope after native turn completion, and exact-token cleanup.
- An injected verifier `PermissionError`, whose incomplete-inventory result
  remains unknown even after subsequent successful physical cleanup.
- Real owner steering during a model request, local cancellation refusal after
  revocation, and natural quiescence closing tool admission.
- A subsequent ordinary owner model turn, which the retained revoked token
  cannot interrupt.

The fixture records pinned source hashes, namespace evidence, selected native
snapshots, and final process/worker cleanup in `hermes-canary.json` under the
harness's private fixture directory. Every recorded owned scope must end in a
positively verified terminated state. The deliberately degraded case must still
retain an unknown aggregate scan because its capture gap is irreversible.

Full CLI credential refresh, route resolution, first agent construction, image
preprocessing, and context expansion precede `owned_conversation` in the native
CLI. This fixture deliberately constructs its real agent before admission and
does not establish coverage of those startup paths. A native CLI entry fence
and a separate real-CLI canary are required before claiming that broader path.
