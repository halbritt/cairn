# Shared memory across projects

Cairn is installed for Codex, OpenCode, Agy, and Claude Code at user scope on
the owner's host. Start a fresh agent session in any project and use its Cairn
tools to search, pull, save, and correct shared notes. There is no per-project
registration step.

All four use the existing collection, whose stored name is
`/home/halbritt/git/cairn`. That name is independent of the working directory.
Keeping it preserves the existing notes without copying or migrating them.
Use the default `repository` capture scope for shared notes; include the actual
project in the body when guidance applies only there. Use `shareable: true` for
selected notes intended for these hosted agents.

| Agent | User-level installation |
| --- | --- |
| Codex | `cairn` MCP entry in both `~/.codex/config.toml` and `~/.codex-harm/config.toml` |
| OpenCode | `~/.config/opencode/tools/cairn.ts` and `~/.config/opencode/cairn.json` |
| Agy | `cairn` entry in `~/.gemini/config/mcp_config.json` |
| Claude Code | `cairn` user-scope entry in `~/.claude.json` |

Codex and OpenCode use their native conversation/session labels. Agy and Claude
launch `~/.local/bin/cairn-shared-mcp`, which selects the existing connection and
generates a fresh label for each MCP process. No manual task/run selection is
needed. These labels organize retrievals; ordinary shared notes outlive them.

Existing agent processes can retain their previous tool configuration. Start a
new session after installation. A project's explicit configuration can override
user settings; Cairn's existing project configuration points to the same shared
collection.

## Installed use, 2026-09-11

A fresh Codex app-server in Pincite discovered the globally configured tools,
created a selected setup note, and searched and pulled it. The installed
OpenCode native tool executor in Striatum Next read that note, appended its
setup details, and pulled the saved revision. A normal Claude Code session in
Pincite then read both contributions and added its own through native tools.

A normal Agy session in Pincite used native search, pull, and edit to correct the
saved owner-priority decision to the current cross-project goal, verifying the
new revision afterward. A second Agy session in Striatum Next read the shared
setup note and added its own section. Fresh Codex and OpenCode sessions in the
other projects then pulled the complete four-contributor note at version 4.
This replaces the earlier configuration-only Agy status.
The installed CLI remains `75eb01e` and API `bb600c5`; this setup required no
database change or service restart.

The shared setup record is `ee653497-482e-407e-a714-c0d88bee9708`, version 4;
the corrected priority record is `91c075c2-32d2-4fc7-83dd-68a4956fff8d`, version 3.
The saved Codex procedure was updated to version 12 to replace project-only
installation advice. Selected native results are retained locally under
`/tmp/cairn-shared-access-5B3Pim`; memory bodies and model transcripts are not
part of this repository.

The host registration follows [Codex's user configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
and [Claude Code's user scope](https://code.claude.com/docs/en/mcp).
OpenCode uses its existing native adapter in the global tools directory. Agy's
installed customization guide documents its global MCP configuration at
`~/.gemini/config/mcp_config.json`.
