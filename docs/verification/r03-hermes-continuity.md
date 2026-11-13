# R03 Hermes existing-conversation continuity preparation

This child of `522a13a63b64fc4f960ad0664e9ebe6ef4293fe9` prepares the
CAIRN-305 AC4 experiment. It does not close AC4. The held Hermes input is
`9904fd411587729dcdf7440cfb830e96e9830597` (package 0.20.5), with Python
3.11.15. The checker verifies all files in the retained inventory before
importing its persistence API. It preserves the host HOME and uses only an
owned mode-0700 application home under the evidence cache.

The implemented positive check uses actual `hermes_state.SessionDB`: one
session ID, two synthetic messages, active-turn lease exclusion, foreign
release exclusion, clean database close, history readback, and reopening that
same session. Credential selectors are synthetic local-transport values whose
bytes must remain unchanged. This is a persistence prerequisite, not a native
conversation, gateway, or refresh result.

The supported CLI path is `HermesCLI(resume=...)`, followed by its existing
`_preload_resumed_session()` and `chat()` methods. The held source loads history
through `get_resume_conversations()` and reopens the same session. `chat()`
initializes the agent and refreshes runtime credentials; it therefore crosses
the current native-client admission boundary. There is no qualified memory
provider reload operation in the inspected CLI path. Recreating only a
provider cannot establish existing-conversation continuity.

The next admitted experiment must finish its own idle turn and cleanly
finalize the CLI, then resume the same conversation. A cached selection A
must survive staging B before that idle boundary. Resume must select checked
Go B; explicit retained Python /2 reversal must preserve the same session ID,
history and credential selectors. Callbacks must come from the actual CLI,
with journals and exactly-once effects checked against an owned disposable
Cairn Store. A local scripted conversation transport and selector avoid any
paid model or real provider. The checker returns 75/NOTRUN for `--phase native`
before inspecting input paths or creating state; that switch grants no
authorization and cannot report AC4 closed.

`native-readback-request.json` contains an exact unexecuted `mise` argv for the
first bounded native operation: construct the held CLI against this disposable
home, resume its existing session, check the loaded history and synthetic
credential selectors, then finalize. It verifies the checker and full held
inventory again before importing the CLI. It does not initialize an agent or
call `chat()`. Port 9 is a refusal destination, not a model transport. Root must
pin and admit that request before executing it. A successful readback would
still require a subsequent admitted conversation fixture with emitted
callbacks, checked A/B/reverse selections and real Store effects.

Dependent users to qualify together are the CLI agent and memory manager;
the installed Cairn provider and five registration handles; plugin-manager
hooks and request middleware; lifecycle configuration, selected engine and
retained Python runtime; the Hermes configuration and profile/credential
selectors; SessionDB and its turn lease; Cairn memory state, observation
settings and scripted selector; and any gateway worker, watcher, inherited
hook or shared installation/configuration root using those same paths. The
isolated object fixtures do not prove watcher independence. Shared users join
the R06 activation unit; partial fixtures cannot be added up as unit proof.

The inspected dependency sources are:

| User | Source and boundary |
| --- | --- |
| CLI owner, runtime credentials, session resume | held `cli.py` (`HermesCLI`, `chat`, `_finalize_single_query`) and `hermes_cli/cli_agent_setup_mixin.py` (`_preload_resumed_session`) |
| Session identity, history, leases | held `hermes_state.py` (`SessionDB`) |
| Provider loading and manager shutdown | held `plugins/memory/__init__.py`, `agent/memory_manager.py` and `agent/memory_provider.py` |
| Cairn configuration cached at initialize | `integrations/hermes/__init__.py`, `CairnProvider.initialize`; `cairn-lifecycle.json` is read at initialization |
| Provider callbacks | `pre_llm_call`, `post_llm_call`, `post_tool_call`, `pre_command`, and `llm_request` middleware registrations in that provider |
| Profile control and ordinary MCP connection | `integrations/hermes/controls.py`, `commands.py` and `scripts/install-hermes-integration.py`; the installer explicitly shares one MCP connection within a profile |
| Selected command, configuration, state and observations | `scripts/install-hermes-integration.py`, `integrations/lifecycle`, and the immutable selection receipt; retain H13/H150, eight flags, stdin and literal argv |
| Gateway cached agents, per-turn environment reload and watchers | held `gateway/run.py`; profile-scoped gateway users remain part of the shared unit until R06 proves otherwise |
| Native Cairn wake and hook consumers | `integrations/hermes/coordination.py`, `queue.py`, and `integrations/lifecycle/hermes_queue.py`; no native bridge operation runs here |

The held manager's session-switch callback changes per-session state without
tearing providers down (`agent/memory_manager.py`, `on_session_switch`); it is
not evidence that lifecycle configuration was reloaded. The CLI's
`_reload_mcp` and `_reload_skills` methods likewise do not qualify a Cairn
memory-provider refresh. Gateway cache eviction alone leaves its shared
profile users unqualified. This preparation starts no watcher, so it asserts
no independence from any existing watcher or live installation.

Run the focused safety checks with:

```sh
mise run test:unit -- test_r03_hermes_continuity -v
```

Run the persistence prerequisite under `mise run host:exec` with the exact held
native root, manifest and a fresh child of an owned private evidence directory:

```sh
mise run host:exec -- --timeout 4m --weight 2 -- python3 -B \
  scripts/check_r03_hermes_continuity.py \
  --native-root HELD_NATIVE_INPUTS --manifest HELD_NATIVE_INPUTS_PINS \
  --evidence-root PRIVATE_EVIDENCE_ROOT --output PRIVATE_EVIDENCE_ROOT/FRESH_CASE
```

The retained inventory digest is
`b2910e13d782eef359ce920659518385c6c022e2901d71b765d86617eb9c39c0`.
The packet must supply concrete paths, source/image pins, raw output and
cleanup observations. No client, gateway, production profile or live
conversation may be launched or changed by this preparation.
