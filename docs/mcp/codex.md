# OpenAI Codex Integration Guide

This guide explains how to connect OpenAI Codex CLI to Cairn using Model Context Protocol (MCP) TOML configuration.

---

## 1. Generating Configuration (`cairn codex-config`)

Cairn generates verified Codex MCP configuration blocks:

### Thread-Scoped Configuration

For interactive sessions where task and run scopes follow the Codex conversation thread:

```sh
cairn codex-config \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" \
  --codex-thread
```

### Explicit Task-Scoped Configuration

For automated batch runs with fixed task and run identifiers:

```sh
cairn codex-config \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" \
  --task "bugfix-102" \
  --run "attempt-1"
```

### Compact Tool Profiles

Add `--tool-profile compact/1` or `--tool-profile compact/2` to reduce prompt overhead:

```sh
cairn codex-config \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" \
  --codex-thread \
  --tool-profile compact/1
```

---

## 2. Adding to `.codex/config.toml`

Append the generated TOML snippet to your project's `.codex/config.toml` or global `~/.codex/config.toml`:

```toml
[mcp.servers.cairn]
command = "/usr/local/bin/cairn"
args = [
  "mcp",
  "--socket", "/home/halbritt/.local/share/cairn/api.sock",
  "--token-file", "/home/halbritt/.local/share/cairn/hosted-agent.token",
  "--repo", "/home/halbritt/git/cairn",
  "--codex-thread",
  "--tool-profile", "compact/1"
]
```

---

## 3. Workflow in Codex Sessions

Codex interacts with Cairn seamlessly:
1. **Initial Search**: Queries repository memory for relevant decisions using `cairn_search`.
2. **Targeted Read**: Expands specific candidate notes using `cairn_pull`.
3. **Capture**: Saves new insights or updated technical procedures using `cairn_remember`.

---

## Related Documentation

- [MCP Overview](overview.md): Architecture and tool compaction.
- [Claude Code Integration](claude-code.md): Setup for Claude Code CLI.
- [CLI Reference](../reference/cli.md): CLI options and flags.
