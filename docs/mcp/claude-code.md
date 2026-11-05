# Claude Code Integration Guide

This guide explains how to connect Anthropic's Claude Code CLI to Cairn using the Model Context Protocol (MCP).

---

## 1. Generating Configuration (`cairn claude-config`)

Cairn includes a configuration generator that produces verified Claude MCP configuration blocks:

```sh
cairn claude-config \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" \
  --task "documentation" \
  --run "run-1"
```

To enable token-saving compact tool profiles:

```sh
cairn claude-config \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" \
  --task "documentation" \
  --run "run-1" \
  --tool-profile compact/1
```

---

## 2. Installing in Claude Configuration

Add the generated JSON block to your user Claude configuration (`~/.claude.json`) or project-level configuration (`.claude.json`):

```json
{
  "mcpServers": {
    "cairn": {
      "command": "/usr/local/bin/cairn",
      "args": [
        "mcp",
        "--socket", "/home/halbritt/.local/share/cairn/api.sock",
        "--token-file", "/home/halbritt/.local/share/cairn/hosted-agent.token",
        "--repo", "/home/halbritt/git/cairn",
        "--task", "documentation",
        "--run", "run-1",
        "--tool-profile", "compact/1"
      ]
    }
  }
}
```

---

## 3. Using Cairn in Claude Code

Once configured, Claude Code automatically gains access to all 10 Cairn MCP tools:

- **Recalling Prior Decisions**: Claude searches memory at the start of a task to check established architecture rules and owner preferences.
- **Pulling Exact Guidelines**: Claude inspects candidates using `cairn_pull` to follow repository conventions.
- **Recording Lessons**: When resolving complex bugs, Claude records the fix with `cairn_remember`.
- **Handoffs**: Before exiting or pausing, Claude records structured handoffs for subsequent sessions.

---

## Related Documentation

- [MCP Overview](overview.md): Architecture and tool profiles.
- [Codex Integration](codex.md): Setup for OpenAI Codex.
- [MCP Tools Reference](../reference/mcp-tools.md): Complete parameter catalog.
