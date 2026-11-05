# OpenCode Integration Guide

This guide covers integrating OpenCode with Cairn, using both the native tools adapter and the MCP configuration generator.

---

## 1. Native Tools Installation (`cairn opencode-install`)

Cairn provides a dedicated native adapter (`integrations/opencode/`) that integrates directly into OpenCode projects without requiring external daemon wrappers:

```sh
cairn opencode-install \
  --project "$HOME/git/cairn" \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn"
```

### Options:
- `--project PATH`: Target project directory (defaults to current directory).
- `--replace`: Overwrite existing adapter and configuration files if versions differ.
- `--recent-files`: Install optional session-local recent-file search hints.
- `--tokens N`: Set memory input room per tool result (default: 32000).

---

## 2. Generating OpenCode Configuration (`cairn opencode-config`)

Alternatively, generate OpenCode JSON configuration:

```sh
cairn opencode-config \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" \
  --task "testing" \
  --run "run-1"
```

To restrict OpenCode to memory operations only (omitting coordination tools):

```sh
cairn opencode-config \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" \
  --task "testing" \
  --run "run-1" \
  --memory-only
```

---

## 3. Verifying OpenCode Setup

Start OpenCode in your configured project:

```sh
opencode
```

Verify that Cairn tools (`cairn_search`, `cairn_pull`, `cairn_remember`) are listed in the available tools directory.

---

## Related Documentation

- [MCP Overview](overview.md): Architecture and tool profiles.
- [Claude Code Integration](claude-code.md): Claude Code integration.
- [MCP Tools Reference](../reference/mcp-tools.md): Complete tools catalog.
