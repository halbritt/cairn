# CAIRN-223 native tool-recovery preflight, 2026-10-24

The frozen source-fit pilot reported a denied Cairn search in its buried-source
case. Its runner did not retain the tool result. The subsequent search/pull
allowlist had only syntax checks, leaving native tool exposure unverified.

`bash scripts/preflight-source-fit-tools.sh` now builds the current Cairn CLI,
starts a disposable PostgreSQL store and Cairn API, and launches the installed
Claude Code CLI with the source-fit runner's exact MCP, restricted-mode, tool,
and permission flags. A bounded loopback Messages endpoint scripts one search,
one pull using the search result's complete `pull_arguments`, then a final
text response. The endpoint makes no model inference. The child has an isolated
home/config and a loopback proxy for nonlocal traffic; this check does not use
the subscription service, OpenRouter, installed bindings, or operational memory.

Local result with Claude Code 2.1.310 and panel SHA-256
`d1acec4b07892215831a7e46330afa04c5e70c9132d15ca4060b41a6a399f909`:

| Check | Result |
| --- | --- |
| Initial native request offered `mcp__cairn__cairn_search` and `mcp__cairn__cairn_pull` | Passed |
| Scripted native search and pull produced successful tool results, with both calls in the runner's stream | Passed |
| Search exposed only the shareable synthetic record; pull returned its complete body | Passed |
| Local-only synthetic record stayed out of hosted tool results | Passed |
| Provider task result, subscription-route permission, task cost | Unverified |

This establishes that the frozen CLI flags and disposable Cairn server can
discover and execute the two tools in a local scripted session. The loopback
transport uses a fixture API key to direct the CLI to the local endpoint, so it
does not establish that the `claude.ai` subscription session will make the
same choice or receive the same permission treatment. It also cannot show that
a model will notice insufficient context, search, use the whole qualification,
and answer the independent task correctly. The pilot's reported denial remains
an unresolved provider observation, not a proved cause or a repaired task
outcome.

The original 15-arm freeze remains stopped at eight admissions, including its
failed/unscored overrun, with seven arms unrun. Any provider-backed recovery
test needs a new predeclared panel, route/model/effort pin, token reserve and
acceptance checks. No task benefit or token saving follows from this preflight.
