#!/usr/bin/env python3
"""Compare full MCP declaration arrays for Claude through a local-only stub."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from check_claude_compact_request_shape import (
    ROOT,
    capture,
    canonical,
    require,
    sha256,
    without_descriptions,
)


EXPECTED_TOOLS = {
    "mcp__cairn__cairn_search",
    "mcp__cairn__cairn_pull",
    "mcp__cairn__cairn_pull_evidence",
    "mcp__cairn__cairn_remember",
    "mcp__cairn__cairn_prepare_note",
    "mcp__cairn__cairn_edit",
    "mcp__cairn__cairn_history",
    "mcp__cairn__cairn_assessments",
    "mcp__cairn__cairn_assess",
    "mcp__cairn__cairn_client_info",
}
RETRIEVAL_TOOLS = {"mcp__cairn__cairn_search", "mcp__cairn__cairn_pull"}
PROFILES = ("", "compact/1", "compact/2")
SOURCE_FILES = (
    "mcpapi/server.go",
    "mcpapi/tool_profile.go",
    "mcpapi/tool_profile_test.go",
    "cmd/cairn/mcp.go",
    "cmd/cairn/mcp_test.go",
    "cmd/cairn/claude_config_test.go",
    "cmd/cairn/codex_config_test.go",
    "cmd/cairn/commands.go",
    "scripts/check_claude_compact_request_shape.py",
    "scripts/check_claude_compact_all_tools.py",
)


def tool_map(request):
    tools = request["tools"]
    result = {tool["name"]: tool for tool in tools}
    require(len(result) == len(tools), "duplicate provider tool name")
    require(set(result) == EXPECTED_TOOLS, "Claude MCP tool inventory changed")
    return result


def profile_summary(request, encoder):
    tools = request["tools"]
    names = [tool["name"] for tool in tools]
    contracts = {
        tool["name"]: without_descriptions(tool["input_schema"])
        for tool in tools
    }
    serialized = canonical(tools)
    return {
        "tool_count": len(tools),
        "tool_names": names,
        "declaration_array_utf8_bytes": len(serialized),
        "declaration_array_sha256": sha256(serialized),
        "o200k_proxy_tokens": len(encoder.encode(serialized.decode("utf-8"))),
        "validation_schema_set_sha256": sha256(canonical(contracts)),
        "per_tool": {
            tool["name"]: {
                "declaration_utf8_bytes": len(canonical(tool)),
                "declaration_o200k_proxy_tokens": len(
                    encoder.encode(canonical(tool).decode("utf-8"))
                ),
                "description_utf8_bytes": len(tool["description"].encode("utf-8")),
                "input_schema_utf8_bytes": len(canonical(tool["input_schema"])),
                "validation_schema_sha256": sha256(canonical(
                    without_descriptions(tool["input_schema"])
                )),
            }
            for tool in tools
        },
    }


def check():
    try:
        import tiktoken
    except ImportError as exc:
        raise RuntimeError("run with uv --with tiktoken==0.12.0") from exc
    claude = shutil.which("claude")
    require(claude is not None, "installed Claude CLI required")
    encoder = tiktoken.get_encoding("o200k_base")
    with tempfile.TemporaryDirectory(prefix="cairn-claude-compact-all-") as temp:
        root = Path(temp)
        cairn = root / "cairn"
        subprocess.run(["go", "build", "-o", str(cairn), "./cmd/cairn"],
                       cwd=ROOT, check=True, timeout=180)
        binary_hash = sha256(cairn.read_bytes())
        version = subprocess.run([claude, "--version"], check=True,
                                 capture_output=True, text=True,
                                 timeout=10).stdout.strip()
        requests = {
            profile or "full": capture(claude, cairn, root, profile)
            for profile in PROFILES
        }
    mapped = {profile: tool_map(request) for profile, request in requests.items()}
    summaries = {profile: profile_summary(request, encoder)
                 for profile, request in requests.items()}
    full = mapped["full"]
    for profile in ("compact/1", "compact/2"):
        require(set(mapped[profile]) == set(full),
                f"{profile}: tool availability changed")
        for name in full:
            require(without_descriptions(full[name]["input_schema"])
                    == without_descriptions(mapped[profile][name]["input_schema"]),
                    f"{profile}/{name}: validation schema changed")
    for name in full:
        if name in RETRIEVAL_TOOLS:
            require(len(mapped["compact/1"][name]["description"])
                    < len(full[name]["description"]),
                    f"compact/1/{name}: retrieval description did not shrink")
        else:
            require(full[name] == mapped["compact/1"][name],
                    f"compact/1/{name}: non-retrieval declaration changed")
        require(len(mapped["compact/2"][name]["description"])
                < len(full[name]["description"]),
                f"compact/2/{name}: tool description did not shrink")
    require(summaries["compact/1"]["declaration_array_utf8_bytes"]
            < summaries["full"]["declaration_array_utf8_bytes"],
            "compact/1 declaration array did not shrink")
    require(summaries["compact/2"]["declaration_array_utf8_bytes"]
            < summaries["full"]["declaration_array_utf8_bytes"],
            "compact/2 declaration array did not shrink")
    source_material = b"\0".join(
        path.encode("utf-8") + b"\0" + (ROOT / path).read_bytes()
        for path in SOURCE_FILES
    )
    base_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True, timeout=10).stdout.strip()
    worktree_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, check=True,
        capture_output=True, text=True, timeout=10).stdout
    print(json.dumps({
        "schema": "cairn.claude-compact-all-tool-declarations/1",
        "source_base_revision": base_revision,
        "candidate_worktree_dirty": bool(worktree_status),
        "candidate_source_identity": "sha256 of listed candidate source files",
        "candidate_binary_sha256": binary_hash,
        "candidate_source_files": list(SOURCE_FILES),
        "candidate_source_files_sha256": sha256(source_material),
        "claude_version": version,
        "transport": "private Claude homes with fake key and loopback Messages stub",
        "request_count_per_profile": 1,
        "profiles": summaries,
        "same_tool_names": True,
        "same_validation_schema_set": True,
        "compact1_changes_retrieval_tools_only": True,
        "compact2_shortens_every_declared_tool_description": True,
        "provider_called": False,
        "subscription_request_observed": False,
        "model_inference": False,
        "tool_execution": False,
        "cairn_api_or_store_access": False,
        "raw_request_retained": False,
        "limits": [
            "Declaration-array bytes and o200k proxy tokens omit the rest of the request.",
            "The proxy is not a verified Claude tokenizer or provider usage measure.",
            "One scripted response per profile does not establish tool-selection quality or task acceptance.",
        ],
    }, sort_keys=True))


if __name__ == "__main__":
    check()
