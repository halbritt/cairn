# Skill router

The lifecycle hook can route each owner prompt to one skill and inject that
skill's instructions. It exists for skills that skillpack hides from the model
with `disable-model-invocation: true`. Hidden skills cost no standing context,
but until now nothing could load them unless the owner typed `/name`.

Tracking: Plane project CAIRN, module "Skill router" (CAIRN-25 to CAIRN-31).

## What it does

On `UserPromptSubmit`, and on an OpenCode `SessionStart` that carries the first
prompt, the engine starts `skill_router.py` in a thread beside memory recall.

1. It reads the catalog from `skills_dir`: `name`, `description` and
   `disable-model-invocation` from each `SKILL.md`. The catalog is cached in
   `state_dir/skill-catalog.json` and rebuilt when any `SKILL.md` changes.
2. It asks one System One Choice over the whole catalog plus `none`. TypeSafe
   (`jev-1.13.0`) answers first. If TypeSafe fails or has no key, the local Kev
   server answers. If both fail, nothing is injected.
3. It injects a skill only when all of these hold: confidence is at least
   `threshold`, the winner is not `none`, the winner is a hidden skill, and the
   skill has not been injected in this session. A visible skill that wins is
   left to the model. The full catalog is offered so that a prompt meant for a
   visible skill does not win a hidden one.
4. The injected block holds the `SKILL.md` body without its frontmatter, the
   skill's base directory, and up to ten of its other files. If the block and
   the memory context together exceed `context_chars`, a short pointer that
   tells the model to read the `SKILL.md` replaces the body.

Nothing is injected when even the pointer and memory together would exceed
the installation's `context_bytes`: 9,500 for Claude Code and 12,000 by
default, which matches the Codex hooks' `additionalContextLimit`. The router waits at most twice the per-leg
`timeout` plus half a second, 3.5 seconds by default, inside the hook's
13-second limit.

The room is measured in UTF-8 bytes, like the total, so memory with
multi-byte characters sends a large skill to its pointer instead of dropping
it. If memory recall fails, for example because the index alone exceeds the
budget, a skill that fired is still delivered, followed by one line telling
the model that Cairn memory was unavailable and why; OpenCode gets that line
inside the skill block. The error also goes to stderr. With nothing routed,
the hook fails as before.

The skill block comes before memory in `additionalContext`. For OpenCode the
engine returns it as `cairn_skill`, and the plugin keeps it as its own block on
the owner message, apart from memory's budget and replacement rules.

A `SessionStart` clears the session's loaded skills, because new, resumed or
compacted context no longer holds them.

## Egress

Only these leave the box, and only after every refusal below has passed:

- The first `prompt_chars` characters of the owner prompt.
- The basename of the working directory.
- The catalog's skill names and descriptions.

The router also skips text that opens with a known envelope that a harness
or Cairn submits in the owner's place: Claude Code task and Monitor
notifications, system reminders and channel wrappers, slash-command echoes,
compaction summaries, skill-body loads, the `/loop` expansion, usage-limit and
interrupt notices, and Cairn's own native wake text
(`coordination.wake_message`). Only exact producer openings are listed, never
generic shapes such as a Markdown heading, and only the start of the text is
checked, so a pasted example that contains notification markup still routes.
This is a heuristic on known envelopes, not proof of who wrote the text.
Replayed over a looping striatum-next session's 570 user-side turns, it left
exactly the 22 prompts the owner typed.

The router makes no request when the installation did not enable it, when
`CAIRN_SKILL_ROUTER=0`, when `.cairn-no-memory` applies, when the prompt is a
slash command or empty, or when the working directory or explicit project
resolves inside an `exclude_paths` entry. Symlinks are resolved before that
check. The default exclusion is `~/git/council`.

The TypeSafe key is read from `jev_env_file` for each request and is never
logged. Each routed prompt adds one line to `state_dir/skill-router.jsonl`
with the leg, winner, confidence, top three options, outcome and latency. The
line holds no prompt text.

The Kev fallback URL below uses HTTP without TLS. It receives the same prompt
excerpt and catalog when TypeSafe fails or is unavailable. Configure `kev_url`
with a transport appropriate to the deployment, or set `CAIRN_SKILL_ROUTER=0`
to prevent routing.

## Configuration

Installers copy `skill_router.py` beside the hook script and write
`"skill_router": {"enabled": true}` into the engine's `config.json`. Pass
`--no-skill-router` to any lifecycle installer to leave it off. Set any key
below inside that block to override its default.

| key | default |
|---|---|
| `skills_dir` | `~/git/skillpack/skills` |
| `jev_url` | `https://api.typesafe.ai/v1/systemone` |
| `jev_model` | `jev-1.13.0` |
| `jev_env_file` | `~/.config/typesafe/env` |
| `kev_url` | `http://100.113.63.58:8008/v1/systemone` |
| `timeout` | `1.5` seconds per leg |
| `threshold` | `0.70` |
| `prompt_chars` | `1500` |
| `context_chars` | `9500` |
| `exclude_paths` | `["~/git/council"]` |

The paths and Kev address in this table are defaults for the owner's current
host installation. Other hosts should set their own values in `config.json` or
install with `--no-skill-router`.

`context_chars` is 9,500 because Claude Code delivers about 10,000 characters
of hook `additionalContext` and drops the rest. Measured on 2026-09-24: 9,000
characters arrived whole, and 11,000 lost their tail. The Claude installers
now also set the engine's `context_bytes` to 9,500 (CAIRN-35), so memory alone
cannot be cut off either.

## Rollback

Set `"enabled": false` in the installed `config.json`, set
`CAIRN_SKILL_ROUTER=0`, or reinstall with `--no-skill-router`. A missing or
broken `skill_router.py` leaves memory recall unchanged and records
`router unavailable` as `last_route` in the session's state file. The Hermes
installer copies the module but leaves it disabled.

## Measured behavior

The routing evidence is in the showerthoughts repository under
`jev/research/tiny-model-benchmark-2026-09-22.md`, items 7 and 8. On 34
prompts against a 50-skill catalog, the model's own skill choice scored 0.87
on the 23 prompts whose skill it could see. It scored 0 on the 11 prompts
whose skill skillpack hides. jev scored 0.83 and 0.91 on the same two groups.

Run through this module with the live catalog on 2026-09-24, the router
injected the right skill on 10 of the 11 hidden-skill prompts. It made no
wrong injections and did not fire on any visible-skill prompt. Latency was
170 ms p50 and 247 ms p95.

Deployed on proximal on 2026-09-24 from `af3e54a` to Claude Code (both
accounts), OpenCode and Codex (both homes share `codex-hooks`). Live check,
`scripts/check_skill_router.py HARNESS`, with the prompt "samtools depth exits
141 in the pipeline": the router fired inline for
`samtools-sigpipe-and-depth-fix` at confidence 1.0 through TypeSafe in every
harness, 1.2 to 1.6 seconds including memory recall. In Claude Code, OpenCode
(local Qwen) and the second Codex account the reply applied the skill's fix.
The first Codex account was out of quota, so there only the routing was
verified. Run the checks one at a time: the script accepts any new matching
line in the log, so concurrent routing to the same skill could pass a probe.

This is a small set of prompts written by the same session that wrote the
question. The router's log supports a later comparison with the skills the
model actually loads (CAIRN-30).
