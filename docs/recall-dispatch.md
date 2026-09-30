# Session-targeted recall dispatch

`integrations/lifecycle/recall_dispatch.py` lets an explicit list of native
Claude sessions use a different, already-reviewed recall engine or config. Every
other session, and every capture event, keeps the exact original command.

It exists so that named sessions can try a recall mode, such as the
`agent_tools` engine on main, without replacing the shared engine that other
sessions use. It is not evidence that any recall mode is more useful.

## Contract

- **Scope.** Only the `SessionStart` and `UserPromptSubmit` hooks are wrapped.
  `PreCompact`, `SessionEnd`, `PostToolUse` and `PostToolUseFailure` keep the
  original command in settings, so capture, checkpoints and file hints never
  pass through the dispatcher. It also re-checks `hook_event_name` and runs the
  original for any other event.
- **Targeting.** Exact native session IDs only: 1–64 distinct IDs, with no `*`,
  `?`, `[` or `]`, no surrounding spaces, and no control characters. There are
  no patterns or prefixes, and matching is case-sensitive.
- **Not authentication.** The session ID in the hook payload is routing
  metadata supplied by the host. Routing grants nothing: the candidate uses the
  same profile, repository and token file as the original.
- **Pass-through.** The dispatcher reads the payload, chooses a command, writes
  the exact bytes to an in-memory file on stdin, and replaces itself with the
  chosen command (`execv`). The engine therefore receives the identical payload
  and keeps the hook's process ID, stdout, stderr, exit status, signals and
  timeout. The settings timeout is unchanged, and the dispatcher adds about
  19 ms (one Python start-up).
- **Continuity.** A candidate config must match the original's `repo`,
  `socket`, `token_file` and `state_dir`. Session state files and their locks
  stay shared, so capture by the original engine and recall by the candidate
  see the same state. Other keys, such as `model` or `recall_mode`, may differ.
- **Failure behavior.**

| Condition | Result |
|---|---|
| No route file | Original command for everyone (routing off) |
| Payload not JSON, or event not a recall event | Original command |
| Session not listed | Original command |
| Route file present but invalid | Exit 1 with `Cairn recall dispatch: …` on stderr; recall skipped for that event |
| Listed session, but the candidate's interpreter or engine is missing, it has no `--config`, or its shared keys differ | Exit 1 with the reason, for that session only; others unaffected |

A targeted session never silently falls back to the original engine. Exit 1 is
a non-blocking hook error: the prompt continues without recall.

## Route file

```json
{
  "schema": "cairn.recall-dispatch/1",
  "sessions": ["NATIVE_SESSION_ID"],
  "command": ["/usr/bin/python3", "/home/halbritt/.local/share/cairn/claude-hooks-candidate/lifecycle.py",
              "--config", "/home/halbritt/.local/share/cairn/claude-hooks-candidate/config.json"],
  "note": "optional free text"
}
```

## Install plan (root performs it; nothing here is installed by this commit)

1. Install the reviewed candidate engine in its own directory, not over the
   shared one. For example, copy main's `integrations/lifecycle/memory.py` to
   `~/.local/share/cairn/claude-hooks-candidate/lifecycle.py`. Write its
   `config.json` as a copy of `~/.local/share/cairn/claude-hooks/config.json`
   with only the recall keys changed (for example `"recall_mode": "agent_tools"`).
   Keep `repo`, `socket`, `token_file` and `state_dir` identical.
2. Write the route file, for example
   `~/.local/share/cairn/claude-hooks/recall-route.json`, listing only the
   target session IDs.
3. Wrap the recall hooks, passing the existing command exactly as it appears in
   `~/.claude/settings.json`:

   ```sh
   python3 scripts/install-recall-dispatch.py install \
     --settings ~/.claude/settings.json \
     --dest ~/.local/share/cairn/claude-hooks \
     --route ~/.local/share/cairn/claude-hooks/recall-route.json \
     --original '/usr/bin/python3 /home/halbritt/.local/share/cairn/claude-hooks/lifecycle.py --config /home/halbritt/.local/share/cairn/claude-hooks/config.json'
   ```

   The installer refuses an invalid route file, and refuses unless the original
   command appears exactly once on each of the two recall events. It keeps
   `settings.json.before-recall-dispatch`.
4. Claude reads hook settings at session start. Already-running targeted
   sessions pick up the dispatcher only after a restart or resume, which root
   decides. The route file itself is read on every event.

## Rollback

- **Stop routing immediately, settings untouched:** delete or rename the route
  file. Every session then gets the original command on its next event.
- **Remove the wrapper:**

  ```sh
  python3 scripts/install-recall-dispatch.py rollback \
    --settings ~/.claude/settings.json --dest ~/.local/share/cairn/claude-hooks
  ```

  This re-reads settings and restores the exact original command only in hooks
  that run this dispatcher. It leaves other hooks, events and settings,
  including edits made since install, unchanged. Do not restore
  `settings.json.before-recall-dispatch` wholesale if anything else may have
  changed since.

## Residual risks

- Shared state means the candidate engine writes the same session state file
  as the original. A candidate that changes the state format could confuse the
  original's capture. Review the candidate engine's state handling before
  targeting a session.
- An invalid route file disables recall for every session until it is fixed or
  removed, by design: the dispatcher cannot tell which sessions it meant.
- The route file is read on every recall event. A partially written file fails
  visibly for one event; write it atomically (write a temp file, then rename).
- Codex, OpenCode and Hermes are not covered. Their hook layouts differ; add
  them only if needed.
