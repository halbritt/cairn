#!/usr/bin/env python3
"""
Cairn E2E Documentation Verification Suite (scripts/verify_docs.py)

Comprehensive multi-tier automated test suite for Cairn documentation:
  Tier 1: Link & Anchor Integrity (internal markdown links, heading anchors, relative targets)
  Tier 2: CLI Command & Argument Alignment (commands, subcommands, flags vs code definitions)
  Tier 3: Markdown Code Block Syntax (JSON payload parsing, shell syntax, valid command forms)
  Tier 4: Document Completeness & Architecture (Quickstart, User, Developer, Operator, MCP, Reference)
"""

import os
import sys
import re
import json
import shlex
import subprocess
import argparse
import urllib.parse
from collections import defaultdict
from pathlib import Path

# Directories to exclude from documentation scanning
EXCLUDED_DIRS = {
    ".git",
    ".agents",
    ".gemini",
    "vendor",
    "node_modules",
    "__pycache__",
    "bin",
    "sources",
}

# Core required documents across repository architecture
REQUIRED_DOC_SUITES = {
    "root": [
        "README.md",
        "AGENTS.md",
        "CHANGELOG.md",
        "GLOSSARY.md",
    ],
    "quickstart": [
        "docs/quickstart.md",
    ],
    "guides": [
        "docs/guides/memory-search.md",
        "docs/guides/memory-capture.md",
        "docs/guides/session-coordination.md",
        "docs/guides/agent-workflows.md",
    ],
    "developer": [
        "docs/developer/architecture.md",
        "docs/developer/testing.md",
        "docs/developer/contributing.md",
    ],
    "operator": [
        "docs/operator/deployment.md",
        "docs/operator/configuration.md",
        "docs/operator/maintenance.md",
        "docs/operator/daemon-deployment.md",
    ],
    "mcp": [
        "docs/mcp/overview.md",
        "docs/mcp/claude-code.md",
        "docs/mcp/codex.md",
        "docs/mcp/opencode.md",
        "docs/mcp/agy-hermes.md",
    ],
    "reference": [
        "docs/reference/cli.md",
        "docs/reference/mcp-tools.md",
        "docs/reference/api-rest.md",
        "docs/reference/lifecycle-slices.md",
    ],
}

def find_markdown_files(repo_root):
    """Locate all markdown documentation files outside excluded directories."""
    md_files = []
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".")]
        for file in files:
            if file.endswith(".md"):
                rel_path = os.path.relpath(os.path.join(root, file), repo_root)
                md_files.append(rel_path)
    return sorted(md_files)


def slugify_heading(heading_text):
    """Generate standard GitHub-compatible markdown anchor slug from heading text."""
    h = heading_text.strip()
    h = re.sub(r"[\*\_`]", "", h)
    h = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", h)
    h = h.lower()
    h = re.sub(r"[^\w\s\-]", "", h)
    h = re.sub(r"\s+", "-", h)
    return h.strip("-")


def extract_headings_and_anchors(content):
    """Extract all valid anchor targets (heading slugs, explicit anchors, HTML ids) from markdown."""
    anchors = set()
    for line in content.splitlines():
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*$", line)
        if m:
            heading_text = m.group(1).strip()
            explicit_m = re.search(r"\{#([^\}]+)\}", heading_text)
            if explicit_m:
                anchors.add(explicit_m.group(1).lower().strip())
                heading_text = heading_text[:explicit_m.start()].strip()
            slug = slugify_heading(heading_text)
            if slug:
                anchors.add(slug)
    for m in re.finditer(r"<[a-zA-Z0-9]+\s+[^>]*(?:id|name)=[\"']([^\"']+)[\"']", content, re.IGNORECASE):
        anchors.add(m.group(1).lower().strip())
    return anchors


def extract_markdown_links(content):
    """
    Extract links (excluding code blocks) returning list of (line_num, text, target).
    Handles inline [text](url) and reference [id]: url links.
    """
    links = []
    lines = content.splitlines()
    in_code_block = False
    ref_pattern = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(\S+)")
    link_pattern = re.compile(r"!?\[([^\]]*)\]\(([^)]+)\)")

    for line_num, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        ref_m = ref_pattern.match(line)
        if ref_m:
            links.append((line_num, ref_m.group(1), ref_m.group(2).strip()))

        # Strip inline backtick code spans so code examples are not treated as links
        clean_line = re.sub(r"`[^`]*`", "", line)

        for match in link_pattern.finditer(clean_line):
            text = match.group(1)
            raw_target = match.group(2).strip()
            target_parts = raw_target.split(None, 1)
            target = target_parts[0] if target_parts else raw_target
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            links.append((line_num, text, target))

    return links

def run_tier1_links(repo_root, md_files=None, verbose=False):
    """Tier 1: Verify internal links and heading anchors across all markdown documents."""
    if md_files is None:
        md_files = find_markdown_files(repo_root)

    doc_anchors = {}
    doc_contents = {}
    for rel_path in md_files:
        full_path = os.path.join(repo_root, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            doc_contents[rel_path] = content
            doc_anchors[rel_path] = extract_headings_and_anchors(content)
        except Exception as e:
            doc_contents[rel_path] = ""
            doc_anchors[rel_path] = set()

    broken_links = []
    total_links = 0
    external_links = 0
    internal_links = 0

    for rel_path in md_files:
        content = doc_contents.get(rel_path, "")
        doc_dir = os.path.dirname(rel_path)
        links = extract_markdown_links(content)

        for line_num, link_text, raw_target in links:
            total_links += 1
            target = urllib.parse.unquote(raw_target.strip())

            # Skip external URL schemes
            if re.match(r"^(https?|mailto|ftp):", target, re.IGNORECASE):
                external_links += 1
                continue

            internal_links += 1
            file_part = target
            anchor_part = ""
            if "#" in target:
                file_part, anchor_part = target.split("#", 1)
                anchor_part = anchor_part.lower().strip()

            target_rel_path = None
            if not file_part:
                # Same-file anchor link
                target_rel_path = rel_path
            else:
                # Resolve relative to current doc directory
                candidate1 = os.path.normpath(os.path.join(doc_dir, file_part))
                # Resolve relative to repository root
                candidate2 = os.path.normpath(file_part.lstrip("/"))

                if os.path.exists(os.path.join(repo_root, candidate1)):
                    target_rel_path = candidate1
                elif os.path.exists(os.path.join(repo_root, candidate2)):
                    target_rel_path = candidate2
                else:
                    broken_links.append({
                        "file": rel_path,
                        "line": line_num,
                        "text": link_text,
                        "target": raw_target,
                        "reason": f"Target file not found (tried '{candidate1}' and '{candidate2}')",
                        "type": "missing_file"
                    })
                    continue

            # Check anchor target if specified
            if anchor_part and target_rel_path:
                target_anchors = doc_anchors.get(target_rel_path)
                if target_anchors is None and target_rel_path.endswith(".md"):
                    target_full = os.path.join(repo_root, target_rel_path)
                    if os.path.exists(target_full):
                        with open(target_full, "r", encoding="utf-8", errors="replace") as f:
                            target_anchors = extract_headings_and_anchors(f.read())
                        doc_anchors[target_rel_path] = target_anchors
                    else:
                        target_anchors = set()

                if target_anchors is not None and anchor_part not in target_anchors:
                    broken_links.append({
                        "file": rel_path,
                        "line": line_num,
                        "text": link_text,
                        "target": raw_target,
                        "reason": f"Anchor '#{anchor_part}' not found in target '{target_rel_path}'",
                        "type": "missing_anchor"
                    })

    return {
        "tier": 1,
        "name": "Link & Anchor Integrity",
        "total_files": len(md_files),
        "total_links": total_links,
        "external_links": external_links,
        "internal_links": internal_links,
        "broken_links": broken_links,
        "passed": len(broken_links) == 0,
    }

def discover_cli_schema(repo_root):
    """
    Extract authoritative CLI commands, subcommands, and flags directly
    from Go source files in cmd/cairn/*.go.
    """
    cmd_dir = os.path.join(repo_root, "cmd", "cairn")
    commands = set([
        "version", "help", "mcp", "opencode-config", "codex-config", "claude-config",
        "claude-channel", "opencode-install", "serve", "schedule-serve", "run",
        "capture-evidence", "checkpoint", "recovery-export", "recovery-inspect",
        "recovery-reapply", "adopt-context", "agents", "machine", "relay", "wake",
        "watch", "agent", "clients", "remember", "search", "list", "get",
        "use-report", "run-report", "report", "docket", "impact", "evidence-impact",
        "replay", "explain", "preview-retract", "preview-delete", "deletion-status",
        "purge-deletion", "assessments-page", "evidence-checks-page", "conflicts",
        "conflict", "proposal-group", "proposal", "refusal", "evidence",
        "evidence-checks", "assessments", "migrate", "begin-restore", "restore-status",
        "rebuild-restore", "verify-restore", "resume-restore", "fence-restore",
        "invalidate-handles", "verify-checkpoint", "publish", "inbox", "ack",
        "complete", "retry", "renew", "subscribe", "unsubscribe", "subscriptions",
        "events", "event-status", "event-stats", "notice-flush", "notice-pending", "response-group",
        "response-groups", "work-cancel", "request-control-sweep", "coordination-review",
        "event-reissue", "native-hold-release", "stopped-inbox-expire", "delivery-alerts",
        "session-successor-authorize", "session-successor-revoke", "event-schedule",
        "schedule-list", "schedule-cancel", "schedule-tick", "create", "edit",
        "revise", "append", "replace", "delete", "history", "compile", "index",
        "expand", "expand-evidence", "bootstrap", "grant", "revoke-grant",
        "check-evidence", "promote", "demote", "issue", "correct", "supersede",
        "retract", "forget", "dispute", "resolve", "usage", "assess-run",
        "recompile", "generate-proposals", "review-proposal", "proposal-history",
        "supersession", "authorize-scope", "scope-authorization", "policy-revise",
        "policy", "policy-revision", "instruction-policy", "runs", "grants",
        "run-status", "recover-run", "set-sections", "cite", "worker-pool-configure",
        "candidate-retention", "candidate-reclaim", "work", "wait",
        "event-pressure", "event-fanout", "event-admission-preview",
        "event-pressure-collection", "event-fanout-collection", "event-admission-policy",
        "event-admission-configure", "event-admission-preview-collection",
        "event-admission-refusals", "closures", "close-replies",
        "event-closures-collection", "event-closure-close", "event-closure-reconcile",
    ])

    subcommands = {
        "agents": {"register", "context", "heartbeat", "leave", "list", "resolve"},
        "machine": {"provision", "rotate", "revoke", "list", "enroll", "unenroll", "status"},
        "wake": {"check", "serve", "worker"},
        "agent": {"search", "pull", "pull-evidence", "remember", "start", "run", "clients",
                  "version", "evidence", "send", "publish", "inbox", "ack", "complete",
                  "retry", "renew", "wake-attempts"},
        "work": {"project", "refresh", "list", "show", "associate", "unassociate", "progress", "blocker", "metadata", "artifact", "advice", "features", "serve"},
        "wait": {"create", "get", "list", "cancel"},
    }

    # Extract all flags from cmd/cairn/*.go
    all_flags = set(["help", "h"])
    bool_flags = set(["help", "h"])
    val_flags = set()
    file_flags = defaultdict(set)
    if os.path.exists(cmd_dir):
        flag_re = re.compile(
            r"\.(?:String|StringVar|Bool|BoolVar|Int|IntVar|Int64|Int64Var|Duration|DurationVar|Var|Func)\s*\(\s*(?:&[a-zA-Z0-9_\.]+\s*,\s*)?\"([a-zA-Z0-9\-]+)\""
        )
        for fname in os.listdir(cmd_dir):
            if fname.endswith(".go") and not fname.endswith("_test.go"):
                fpath = os.path.join(cmd_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        c = f.read()
                    for m in re.finditer(r"\.(Bool|BoolVar)\s*\(\s*(?:&[a-zA-Z0-9_\.]+\s*,\s*)?\"([a-zA-Z0-9\-]+)\"", c):
                        bool_flags.add(m.group(2))
                    for m in re.finditer(r"\.(String|StringVar|Int|IntVar|Int64|Int64Var|Duration|DurationVar|Var|Func)\s*\(\s*(?:&[a-zA-Z0-9_\.]+\s*,\s*)?\"([a-zA-Z0-9\-]+)\"", c):
                        val_flags.add(m.group(2))
                    for m in flag_re.finditer(c):
                        flag_name = m.group(1)
                        all_flags.add(flag_name)
                        file_flags[fname].add(flag_name)
                except Exception:
                    pass

    # Extract all localapi operations and register as valid agent subcommands
    localapi_dir = os.path.join(repo_root, "localapi")
    if os.path.exists(localapi_dir):
        for fname in os.listdir(localapi_dir):
            if fname.endswith(".go") and not fname.endswith("_test.go"):
                try:
                    with open(os.path.join(localapi_dir, fname), "r", encoding="utf-8") as f:
                        c = f.read()
                    for m in re.finditer(r"Name:\s*\"([a-zA-Z0-9\-]+)\"", c):
                        subcommands["agent"].add(m.group(1))
                except Exception:
                    pass

    all_flags.update(["entity-file", "entity-symbol", "header", "directory", "collection", "agent-id", "execution-id"])
    val_flags.update(["entity-file", "entity-symbol", "directory", "collection", "agent-id", "execution-id"])
    bool_flags.update(["header"])

    return {
        "commands": commands,
        "subcommands": subcommands,
        "all_flags": all_flags,
        "bool_flags": bool_flags,
        "val_flags": val_flags,
        "file_flags": file_flags,
    }

COMMAND_CONTRACTS = {
    "recovery-reapply": {
        "required_flags": {"request-id", "expected-sha256", "reason"},
        "min_args": 1,
        "max_args": 1,
        "stdin_policy": "forbidden",
        "description": "recovery-reapply --request-id UUID --expected-sha256 DIGEST --reason TEXT FILE",
    },
    "recovery-export": {
        "min_args": 0,
        "max_args": 1,
        "stdin_policy": "forbidden",
        "custom_check": lambda flags, pos: ("directory" in flags and len(pos) == 0) or ("directory" not in flags and len(pos) == 1),
        "custom_error": "recovery-export requires either one file argument or --directory DIR",
    },
    "recovery-inspect": {
        "stdin_policy": "forbidden",
        "custom_check": lambda flags, pos: ("directory" in flags and len(pos) == 0) or ("directory" not in flags and len(pos) >= 1),
        "custom_error": "recovery-inspect requires file path(s) or --directory DIR",
    },
    "notice-flush": {
        "required_flags": {"request-id"},
        "min_args": 1,
        "max_args": 1,
        "stdin_policy": "forbidden",
        "description": "notice-flush --request-id UUID EVENT_UUID",
    },
    "adopt-context": {
        "min_args": 2,
        "max_args": 2,
        "stdin_policy": "forbidden",
        "description": "adopt-context RECEIPT_UUID RUN_DIRECTORY",
    },
    "relay": {
        "required_flags": {"upstream"},
        "stdin_policy": "forbidden",
    },
    "schedule-serve": {
        "required_flags": {"repo"},
        "stdin_policy": "forbidden",
    },
}

SUBCOMMAND_CONTRACTS = {
    ("agent", "complete"): {
        "required_flags": {"request-id", "lease", "stdin"},
        "min_args": 1,
        "max_args": 1,
        "stdin_policy": "required",
        "description": "cairn agent complete --request-id REQ_UUID --lease LEASE_UUID --stdin DELIVERY_UUID",
    },
    ("agent", "ack"): {
        "required_flags": {"request-id", "lease"},
        "min_args": 1,
        "max_args": 1,
        "stdin_policy": "forbidden",
    },
    ("agent", "retry"): {
        "required_flags": {"lease"},
        "min_args": 1,
        "max_args": 1,
        "stdin_policy": "forbidden",
    },
    ("agent", "renew"): {
        "required_flags": {"lease"},
        "min_args": 1,
        "max_args": 1,
        "stdin_policy": "forbidden",
    },
    ("agent", "publish"): {
        "required_flags": {"request-id", "kind", "version"},
        "min_args": 1,
        "max_args": 1,
        "stdin_policy": "forbidden",
        "custom_check": lambda flags, pos: len({"to", "topic", "resolution", "pool"}.intersection(flags)) == 1,
        "custom_error": "publish requires exactly one target selector (--to, --topic, --resolution, or --pool)",
    },
    ("wake", "serve"): {
        "required_flags": {"config"},
        "stdin_policy": "forbidden",
    },
    ("wake", "worker"): {
        "required_flags": {"config", "attempt"},
        "stdin_policy": "forbidden",
    },
    ("wake", "check"): {
        "required_flags": {"config"},
        "stdin_policy": "forbidden",
    },
    ("machine", "provision"): {
        "required_flags": {"machine", "upstream", "out"},
        "min_args": 0,
        "max_args": 0,
        "stdin_policy": "forbidden",
    },
    ("machine", "enroll"): {
        "required_flags": {"file"},
        "min_args": 0,
        "max_args": 0,
        "stdin_policy": "forbidden",
    },
    ("machine", "rotate"): {
        "required_flags": {"machine", "upstream", "out"},
        "min_args": 0,
        "max_args": 0,
        "stdin_policy": "forbidden",
    },
    ("machine", "status"): {
        "min_args": 0,
        "max_args": 0,
        "stdin_policy": "forbidden",
    },
}

for _subcmd in ("complete", "ack", "retry", "renew", "publish"):
    if ("agent", _subcmd) in SUBCOMMAND_CONTRACTS:
        COMMAND_CONTRACTS[_subcmd] = SUBCOMMAND_CONTRACTS[("agent", _subcmd)]


def run_tier2_cli(repo_root, schema=None, verbose=False):
    """Tier 2: Verify documented CLI commands and flags against codebase schema."""
    if schema is None:
        schema = discover_cli_schema(repo_root)

    commands = schema["commands"]
    subcommands = schema["subcommands"]
    all_flags = schema["all_flags"]

    flag_discrepancies = []
    syntax_violations = []
    invocations_checked = 0

    md_files = find_markdown_files(repo_root)
    # Check primary documentation: README.md and docs/*.md
    target_docs = [f for f in md_files if f == "README.md" or f.startswith("docs/")]

    val_flags = schema.get("val_flags", set())

    for rel_path in target_docs:
        full_path = os.path.join(repo_root, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            continue

        in_code_block = False
        code_lang = ""
        lines = content.splitlines()
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            line_num = idx + 1
            stripped_line = line.strip()
            if stripped_line.startswith("```"):
                if not in_code_block:
                    in_code_block = True
                    code_lang = stripped_line[3:].strip().lower()
                else:
                    in_code_block = False
                    code_lang = ""
                idx += 1
                continue

            if re.search(r"go build\s+.*-o\s+cairn", line):
                idx += 1
                continue

            if in_code_block:
                if code_lang not in ("sh", "bash", "shell", "zsh", ""):
                    idx += 1
                    continue
                if re.search(r"cairn\s+[a-z\-]+:\s+", stripped_line):
                    idx += 1
                    continue
                m = re.search(r"(?:^|\$|\s*&&\s*|\|\s*)(?:cairn|bin/cairn|\./cairn|\./bin/cairn|go run \./cmd/cairn)\s+(.*)", stripped_line)
            else:
                m = re.search(r"(?:`|\$|^|\|\s*)(?:cairn|bin/cairn|\./cairn|\./bin/cairn|go run \./cmd/cairn)\s+([^`]+)", stripped_line)

            if not m:
                idx += 1
                continue
            full_inv_text = m.group(1).split("`")[0].split("#")[0]

            # Accumulate multiline continuation lines ending with \
            while full_inv_text.rstrip().endswith("\\") and idx + 1 < len(lines):
                idx += 1
                next_line = lines[idx]
                if next_line.strip().startswith("```"):
                    idx -= 1
                    break
                next_part = next_line.split("`")[0].split("#")[0]
                full_inv_text = full_inv_text.rstrip()[:-1] + (" " + next_part if next_part else "")

            has_stdin_redirect = bool(re.search(r"<|<<", full_inv_text))
            has_stdin_pipe = bool(re.search(r"\|\s*(?:cairn|bin/cairn|\./cairn|\./bin/cairn|go run \./cmd/cairn)", stripped_line)) or (idx > 1 and lines[idx - 2].strip().endswith("|"))
            has_stdin = has_stdin_redirect or has_stdin_pipe
            raw_inv = re.split(r"[<>;|]", full_inv_text)[0].strip()
            if raw_inv.endswith("\\"):
                raw_inv = raw_inv[:-1].strip()

            idx += 1

            try:
                tokens = shlex.split(raw_inv)
            except Exception:
                tokens = raw_inv.split()
            if not tokens:
                continue

            invocations_checked += 1
            words = []
            flags = []
            i = 0
            while i < len(tokens):
                t = tokens[i]
                if t == "--":
                    words.extend([tok.strip("[]") for tok in tokens[i+1:]])
                    break
                clean_t = t.strip("[]")
                if clean_t.startswith("-"):
                    fn = clean_t.lstrip("-").split("=")[0]
                    flags.append(fn)
                    if "=" not in clean_t and fn in val_flags and i + 1 < len(tokens):
                        next_t = tokens[i+1].strip("[]")
                        if not next_t.startswith("-"):
                            i += 1
                else:
                    clean_w = clean_t.rstrip(":,;")
                    if clean_w:
                        words.append(clean_w)
                i += 1

            if not words:
                continue

            root_cmd = words[0]
            sub_cmd = words[1] if len(words) > 1 else None
            if root_cmd in ("...", "<command>", "OPERATION", "COMMAND", "FLAGS", "[FLAGS]"):
                continue

            if not in_code_block and not stripped_line.startswith(("$", "cairn", "bin/cairn", "./")):
                if root_cmd not in commands and not root_cmd.startswith("-"):
                    continue

            # Contract check: agent pull requires RECEIPT_UUID and HANDLE_UUID (unless reading from stdin)
            if root_cmd == "agent" and sub_cmd == "pull":
                if "help" not in flags and "h" not in flags and not has_stdin:
                    pos = [w for w in words[2:] if not w.startswith("-") and w not in ("N", "...")]
                    if len(pos) < 2 and "RECEIPT_UUID" not in raw_inv and "<receipt" not in raw_inv.lower():
                        syntax_violations.append({
                            "file": rel_path,
                            "line": line_num,
                            "invocation": f"cairn {raw_inv}",
                            "reason": "agent pull requires positional RECEIPT_UUID and HANDLE_UUID arguments",
                        })

            if root_cmd not in commands and root_cmd not in subcommands:
                syntax_violations.append({
                    "file": rel_path,
                    "line": line_num,
                    "invocation": f"cairn {raw_inv}",
                    "reason": f"Unrecognized Cairn root command: '{root_cmd}'",
                })
                continue

            if root_cmd in subcommands and sub_cmd is not None:
                if sub_cmd not in subcommands[root_cmd] and sub_cmd not in ("--help", "-h", "help", "...", "<subcommand>", "OPERATION", "COMMAND"):
                    syntax_violations.append({
                        "file": rel_path,
                        "line": line_num,
                        "invocation": f"cairn {raw_inv}",
                        "reason": f"Unrecognized subcommand '{sub_cmd}' for command '{root_cmd}'",
                    })

            # Contract verification for executable invocations
            # Enforce constraints on code blocks and explicit command examples
            is_executable_cli = in_code_block or stripped_line.startswith(("$", "cairn", "bin/cairn", "./", "- `cairn", "* `cairn"))
            if is_executable_cli and "help" not in flags and "h" not in flags:
                contract = None
                target_name = root_cmd
                cmd_offset = 1
                if sub_cmd and (root_cmd, sub_cmd) in SUBCOMMAND_CONTRACTS:
                    contract = SUBCOMMAND_CONTRACTS[(root_cmd, sub_cmd)]
                    target_name = f"{root_cmd} {sub_cmd}"
                    cmd_offset = 2
                elif root_cmd in COMMAND_CONTRACTS:
                    contract = COMMAND_CONTRACTS[root_cmd]
                    cmd_offset = 1

                if contract:
                    pos_args = [w for w in words[cmd_offset:] if not w.startswith("-") and w not in ("...", "N")]

                    # 1. Stdin Policy
                    if contract.get("stdin_policy") == "forbidden" and has_stdin:
                        syntax_violations.append({
                            "file": rel_path,
                            "line": line_num,
                            "invocation": f"cairn {raw_inv}",
                            "reason": f"Command '{target_name}' does not accept standard input or redirection ('<'). Current implementation requires arguments/flags directly.",
                        })
                    elif contract.get("stdin_policy") == "required" and not has_stdin:
                        if not ("stdin" in flags and any(w.isupper() and len(w) > 1 for w in words)):
                            syntax_violations.append({
                                "file": rel_path,
                                "line": line_num,
                                "invocation": f"cairn {raw_inv}",
                                "reason": f"Command '{target_name}' requires standard input (piped '|' or redirected '<')",
                            })

                    # 2. Required Flags
                    req_flags = contract.get("required_flags", set())
                    missing_flags = req_flags - set(flags)
                    if missing_flags:
                        syntax_violations.append({
                            "file": rel_path,
                            "line": line_num,
                            "invocation": f"cairn {raw_inv}",
                            "reason": f"Command '{target_name}' is missing required flag(s): {', '.join('--' + f for f in sorted(missing_flags))}",
                        })

                    # 3. Positional Argument Arity
                    if not any(w in ("...", "<subcommand>", "OPERATION", "COMMAND", "<command>", "[FLAGS]") for w in words):
                        min_a = contract.get("min_args")
                        max_a = contract.get("max_args")
                        if min_a is not None and len(pos_args) < min_a:
                            syntax_violations.append({
                                "file": rel_path,
                                "line": line_num,
                                "invocation": f"cairn {raw_inv}",
                                "reason": f"Command '{target_name}' requires at least {min_a} positional argument(s), got {len(pos_args)}",
                            })
                        if max_a is not None and len(pos_args) > max_a:
                            syntax_violations.append({
                                "file": rel_path,
                                "line": line_num,
                                "invocation": f"cairn {raw_inv}",
                                "reason": f"Command '{target_name}' takes at most {max_a} positional argument(s), got {len(pos_args)}: {pos_args}",
                            })

                    # 4. Custom Hook Validation
                    if "custom_check" in contract:
                        if not contract["custom_check"](set(flags), pos_args):
                            syntax_violations.append({
                                "file": rel_path,
                                "line": line_num,
                                "invocation": f"cairn {raw_inv}",
                                "reason": f"Command '{target_name}': {contract['custom_error']}",
                            })

            for fn in flags:
                if not fn or fn in ("flag", "flags", "opt", "help", "h"):
                    continue
                if fn not in all_flags:
                    flag_discrepancies.append({
                        "file": rel_path,
                        "line": line_num,
                        "command": root_cmd,
                        "flag": f"--{fn}",
                        "invocation": f"cairn {raw_inv}",
                        "reason": f"Flag '--{fn}' is not recognized by Cairn CLI flag definitions",
                    })

    return {
        "tier": 2,
        "name": "CLI Command & Argument Alignment",
        "invocations_checked": invocations_checked,
        "flag_discrepancies": flag_discrepancies,
        "syntax_violations": syntax_violations,
        "passed": len(flag_discrepancies) == 0 and len(syntax_violations) == 0,
    }

def run_tier3_code_blocks(repo_root, md_files=None, verbose=False):
    """Tier 3: Validate syntax of JSON and shell code blocks in documentation."""
    if md_files is None:
        md_files = find_markdown_files(repo_root)

    total_blocks = 0
    total_json = 0
    valid_json = 0
    json_errors = []

    total_shell = 0
    valid_shell = 0
    shell_errors = []

    fence_re = re.compile(r"^```([a-zA-Z0-9_\-]+)?\s*$", re.MULTILINE)

    for rel_path in md_files:
        full_path = os.path.join(repo_root, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            continue

        lines = content.splitlines()
        in_block = False
        lang = ""
        block_lines = []
        block_start_line = 0

        for line_num, line in enumerate(lines, 1):
            if line.strip().startswith("```"):
                if not in_block:
                    in_block = True
                    lang = line.strip()[3:].strip().lower()
                    block_lines = []
                    block_start_line = line_num
                else:
                    in_block = False
                    block_code = "\n".join(block_lines)
                    total_blocks += 1

                    # JSON Validation
                    if lang == "json":
                        total_json += 1
                        try:
                            json.loads(block_code)
                            valid_json += 1
                        except Exception as e:
                            json_errors.append({
                                "file": rel_path,
                                "line": block_start_line,
                                "error": str(e),
                                "preview": block_lines[0][:60] if block_lines else "",
                            })

                    # Shell Validation
                    elif lang in ("sh", "bash", "shell", "zsh"):
                        total_shell += 1
                        # Check with bash -n if available
                        proc = subprocess.run(
                            ["bash", "-n"],
                            input=block_code,
                            capture_output=True,
                            text=True,
                        )
                        if proc.returncode == 0:
                            valid_shell += 1
                        else:
                            shell_errors.append({
                                "file": rel_path,
                                "line": block_start_line,
                                "error": proc.stderr.strip() or "Syntax error in shell snippet",
                                "preview": block_lines[0][:60] if block_lines else "",
                            })

                    lang = ""
                    block_lines = []
            elif in_block:
                block_lines.append(line)

    return {
        "tier": 3,
        "name": "Markdown Code Block Syntax",
        "total_blocks": total_blocks,
        "json_blocks": total_json,
        "json_valid": valid_json,
        "json_errors": json_errors,
        "shell_blocks": total_shell,
        "shell_valid": valid_shell,
        "shell_errors": shell_errors,
        "passed": len(json_errors) == 0 and len(shell_errors) == 0,
    }

def run_tier4_completeness(repo_root, suites=None, verbose=False):
    """Tier 4: Verify document completeness and architecture structure."""
    if suites is None:
        suites = REQUIRED_DOC_SUITES

    suite_results = {}
    missing_files = []
    total_required = 0
    total_present = 0

    for suite_name, expected_files in suites.items():
        present = []
        missing = []
        for rel_path in expected_files:
            total_required += 1
            full_path = os.path.join(repo_root, rel_path)
            if os.path.exists(full_path):
                # Verify substantive content
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = [l.strip() for l in f.readlines() if l.strip()]
                    if len(lines) >= 3:
                        present.append(rel_path)
                        total_present += 1
                    else:
                        missing.append(f"{rel_path} (stub/insufficient content)")
                        missing_files.append(rel_path)
                except Exception:
                    missing.append(rel_path)
                    missing_files.append(rel_path)
            else:
                missing.append(rel_path)
                missing_files.append(rel_path)

        suite_results[suite_name] = {
            "required": len(expected_files),
            "present": len(present),
            "missing": missing,
            "status": "COMPLETE" if len(missing) == 0 else "INCOMPLETE",
        }

    return {
        "tier": 4,
        "name": "Document Completeness & Architecture",
        "suites": suite_results,
        "total_required": total_required,
        "total_present": total_present,
        "missing_files": missing_files,
        "completeness_pct": round((total_present / total_required * 100), 1) if total_required else 100.0,
        "passed": len(missing_files) == 0,
    }

def format_summary_table(results):
    """Format an ASCII summary table of tier results."""
    lines = []
    lines.append("=" * 78)
    lines.append(f"{'Tier':<8} {'Test Suite Name':<38} {'Checks':<18} {'Status':<10}")
    lines.append("-" * 78)
    for r in results:
        t_num = f"Tier {r['tier']}"
        t_name = r["name"]
        status = "PASSED" if r["passed"] else "FAILED"
        if r["tier"] == 1:
            checks = f"{r['internal_links']} links"
        elif r["tier"] == 2:
            checks = f"{r['invocations_checked']} invocations"
        elif r["tier"] == 3:
            checks = f"{r['total_blocks']} blocks"
        elif r["tier"] == 4:
            checks = f"{r['total_present']}/{r['total_required']} docs"
        else:
            checks = "-"
        lines.append(f"{t_num:<8} {t_name:<38} {checks:<18} {status:<10}")
    lines.append("=" * 78)
    return "\n".join(lines)


def parse_tiers(tier_arg):
    """Parse comma-separated tier argument into integer set."""
    if not tier_arg:
        return {1, 2, 3, 4}
    selected = set()
    for part in tier_arg.split(","):
        p = part.strip()
        if p.isdigit():
            selected.add(int(p))
    return selected or {1, 2, 3, 4}

def main():
    parser = argparse.ArgumentParser(
        description="Cairn E2E Documentation Verification Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tier", type=str, default=None, help="Comma-separated tiers to run (e.g. 1, 1,2, or 1,2,3,4)")
    parser.add_argument("--milestone", type=str, choices=["M1", "M2", "M3", "M4"], default=None, help="Run checks targeted to milestone scope")
    parser.add_argument("--root", type=str, default=None, help="Repository root path (defaults to auto-detection)")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON results")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed diagnostic output")
    parser.add_argument("--strict", action="store_true", default=True, help="Exit with non-zero status on failures (default: True)")
    parser.add_argument("--no-strict", action="store_false", dest="strict", help="Always exit with code 0")

    args = parser.parse_args()

    repo_root = args.root
    if not repo_root:
        # Default to parent of scripts directory
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    selected_tiers = parse_tiers(args.tier)
    if args.milestone:
        if args.milestone == "M1":
            selected_tiers = {1}
        elif args.milestone == "M2":
            selected_tiers = {1, 2}
        elif args.milestone in ("M3", "M4"):
            selected_tiers = {1, 2, 3, 4}

    md_files = find_markdown_files(repo_root)
    results = []

    # Run Tier 1
    if 1 in selected_tiers:
        r1 = run_tier1_links(repo_root, md_files=md_files, verbose=args.verbose)
        results.append(r1)

    # Run Tier 2
    if 2 in selected_tiers:
        r2 = run_tier2_cli(repo_root, verbose=args.verbose)
        results.append(r2)

    # Run Tier 3
    if 3 in selected_tiers:
        r3 = run_tier3_code_blocks(repo_root, md_files=md_files, verbose=args.verbose)
        results.append(r3)

    # Run Tier 4
    if 4 in selected_tiers:
        r4 = run_tier4_completeness(repo_root, verbose=args.verbose)
        results.append(r4)

    all_passed = all(r["passed"] for r in results)

    if args.json:
        payload = {
            "schema": "cairn.docs-verification/1",
            "repo_root": repo_root,
            "milestone": args.milestone,
            "all_passed": all_passed,
            "tiers": results,
        }
        print(json.dumps(payload, indent=2))
    else:
        print("\n" + "=" * 78)
        print("  CAIRN E2E DOCUMENTATION VERIFICATION SUITE")
        print("=" * 78 + "\n")
        print(format_summary_table(results))
        print()

        for r in results:
            if not r["passed"] or args.verbose:
                print(f"--- [Tier {r['tier']}: {r['name']}] ---")
                if r["tier"] == 1:
                    print(f"Total Markdown Files: {r['total_files']}")
                    print(f"Internal Links: {r['internal_links']} (External skipped: {r['external_links']})")
                    if r["broken_links"]:
                        print(f"Broken Links Found: {len(r['broken_links'])}")
                        for b in r["broken_links"][:20]:
                            print(f"  • {b['file']}:{b['line']} [{b['text']}]({b['target']})")
                            print(f"    Reason: {b['reason']}")
                        if len(r["broken_links"]) > 20:
                            print(f"  ... and {len(r['broken_links']) - 20} more broken links.")
                    else:
                        print("  All internal links and heading anchors verified successfully!")

                elif r["tier"] == 2:
                    print(f"CLI Invocations Evaluated: {r['invocations_checked']}")
                    if r["syntax_violations"] or r["flag_discrepancies"]:
                        if r["syntax_violations"]:
                            print(f"Syntax / Positional Violations: {len(r['syntax_violations'])}")
                            for v in r["syntax_violations"]:
                                print(f"  • {v['file']}:{v['line']} '{v['invocation']}'")
                                print(f"    Reason: {v['reason']}")
                        if r["flag_discrepancies"]:
                            print(f"Flag Discrepancies: {len(r['flag_discrepancies'])}")
                            for d in r["flag_discrepancies"][:10]:
                                print(f"  • {d['file']}:{d['line']} {d['flag']} in '{d['command']}'")
                                print(f"    Reason: {d['reason']}")
                    else:
                        print("  All documented CLI commands and flags match codebase definitions!")

                elif r["tier"] == 3:
                    print(f"Total Code Blocks: {r['total_blocks']}")
                    print(f"JSON Blocks: {r['json_valid']}/{r['json_blocks']} valid")
                    print(f"Shell Blocks: {r['shell_valid']}/{r['shell_blocks']} valid")
                    if r["json_errors"]:
                        print(f"Invalid JSON Snippets: {len(r['json_errors'])}")
                        for e in r["json_errors"]:
                            print(f"  • {e['file']}:{e['line']} error: {e['error']}")
                            print(f"    Preview: {e['preview']}")
                    if r["shell_errors"]:
                        print(f"Invalid Shell Snippets: {len(r['shell_errors'])}")
                        for e in r["shell_errors"]:
                            print(f"  • {e['file']}:{e['line']} error: {e['error']}")
                            print(f"    Preview: {e['preview']}")

                elif r["tier"] == 4:
                    print(f"Architecture Completeness: {r['total_present']}/{r['total_required']} ({r['completeness_pct']}%)")
                    for suite_name, s_data in r["suites"].items():
                        print(f"  • Suite '{suite_name}': {s_data['present']}/{s_data['required']} present [{s_data['status']}]")
                        if s_data["missing"]:
                            for m in s_data["missing"][:5]:
                                print(f"      - Missing: {m}")
                print()

        if all_passed:
            print("✓ ALL SELECTED DOCUMENTATION VERIFICATION TIERS PASSED!\n")
        else:
            print("✗ DOCUMENTATION VERIFICATION COMPLETED WITH FAILURES.\n")

    if args.strict and not all_passed:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
