#!/usr/bin/env python3
"""Freeze and check a provider-free single-workflow CAIRN-227 dry run.

This narrow companion does not replace ``token_efficiency_gate.py``. It freezes
one baseline ``single_notice`` workflow, records fresh Codex subscription
utilization through the selected local Codex app-server account, and can later
check the exact event/attempt/native-turn join. It never starts a model turn.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import stat
import subprocess
import sys
import time
import tomllib

import token_efficiency_gate as gate


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "trials/token-efficiency/FIXTURE.json"
ACCOUNT = "codex-home:.codex"
MODEL = "gpt-6-luna"
EFFORT = "low"
WORKFLOW_SCHEMA = "cairn.token-efficiency-single-workflow-dry-run/1"
OBSERVATION_SCHEMA = "cairn.token-efficiency-single-workflow-observation/1"
CEILING_USED_PERCENT = 20
RESERVE_PERCENTAGE_POINTS = 1
STAGE_SUFFIX = "recipient"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_owner_json(path, *, label):
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and info.st_mode & 0o077 == 0 and info.st_size <= 5_000_000,
                f"{label} must be an owner-only regular JSON file under 5 MB")
        raw = os.read(descriptor, 5_000_001)
        require(len(raw) <= 5_000_000, f"{label} exceeds 5 MB")
    finally:
        os.close(descriptor)
    try:
        return json.loads(raw, object_pairs_hook=gate.unique_pairs,
                          parse_constant=gate.invalid_constant)
    except (ValueError, json.JSONDecodeError):
        raise ValueError(f"{label} is invalid JSON") from None


def select_workflow(fixture_path, controller_path, *, case, arm):
    """Resolve one supported fixture case to its prospective baseline workflow."""
    require(case not in ("concurrent_receivers",),
            "concurrency case unsupported by the sequential one-workflow runner")
    fixture_path, controller_path = Path(fixture_path), Path(controller_path)
    fixture = gate.read_json(fixture_path)
    controller = read_owner_json(controller_path, label="controller manifest")
    require(arm == "baseline", "one-workflow dry run supports baseline arm only")
    require(fixture.get("schema") == "cairn.token-efficiency-fixture/1", "fixture schema")
    cases = fixture.get("cases") or []
    require([row.get("id") for row in cases] == list(gate.CASES), "fixture case order")
    fixture_case = next((row for row in cases if row.get("id") == case), None)
    require(fixture_case is not None, "case is not in the fixture")
    if "capture" in (fixture_case.get("setup") or {}):
        raise ValueError("capture case unsupported: no selected Claude selector is frozen")
    require(case == "single_notice", "unsupported case: one-workflow runner only stages single_notice")
    fixture_hash = sha_file(fixture_path)
    require(controller.get("schema") == "cairn.token-efficiency-panel-manifest/1",
            "controller manifest schema")
    require(controller.get("fixture_sha256") == fixture_hash, "controller fixture hash mismatch")
    require(controller.get("provider_called") is False, "controller source already records provider calls")
    prospective = controller.get("prospective_runs") or []
    require(len(prospective) == len(gate.CASES) * len(gate.ARMS),
            "controller must contain all 88 prospective fixture/arm identities")
    selected = [row for row in prospective if row.get("case") == case and row.get("arm") == arm]
    require(len(selected) == 1, "selected workflow identity must be unique")
    planned = selected[0]
    workflow_id = f"{arm}.{case}"
    require(planned.get("workflow_id") == workflow_id, "selected workflow ID mismatch")
    require("capture" not in (fixture_case.get("setup") or {}), "capture case unsupported")
    require(case != "concurrent_receivers", "concurrency case unsupported")
    require(not (fixture_case.get("setup") or {}).get("transitions"),
            "case requires unsupported transition orchestration")
    actor = {"schema": "cairn.token-efficiency-actor-task/1", "case": case,
             "task": fixture_case.get("actor_task")}
    actor_hash = gate.sha(actor)
    setup_hash = gate.sha(fixture_case.get("setup"))
    check_hash = gate.sha(fixture_case.get("acceptance_check"))
    require(planned.get("actor_sha256") == actor_hash, "controller actor hash mismatch")
    require(planned.get("setup_sha256") == setup_hash, "controller setup hash mismatch")
    require(planned.get("acceptance_check_sha256") == check_hash,
            "controller acceptance-check hash mismatch")
    runs = [row for row in controller.get("prospective_runs", [])
            if row.get("workflow_id") == workflow_id]
    require(len(runs) == 1, "workflow appears more than once in controller")
    return {
        "case": case,
        "arm": arm,
        "workflow_id": workflow_id,
        "fixture_sha256": fixture_hash,
        "controller_sha256": sha_file(controller_path),
        "actor_sha256": actor_hash,
        "setup_sha256": setup_hash,
        "acceptance_check_sha256": check_hash,
        "intervention_sha256": planned.get("intervention_sha256"),
    }


def fixture_manifest(fixture_path, controller_path, *, case, arm):
    """Return a minimal gate-shaped one-run manifest for local join checks."""
    selected = select_workflow(fixture_path, controller_path, case=case, arm=arm)
    stage_id = f"{selected['workflow_id']}.{STAGE_SUFFIX}"
    return {
        "schema": WORKFLOW_SCHEMA,
        **selected,
        "stage_id": stage_id,
        "stage_role": "recipient",
        "model": {"harness": "codex", "account": ACCOUNT, "id": MODEL,
                  "effort": EFFORT, "route": "subscription"},
    }


def validate_stage_capacity(evidence, policy, *, workflow_id, stage_id, now=None):
    now = now or datetime.now(timezone.utc)
    gate.validate_utilization_evidence(
        evidence, harness="codex", account=ACCOUNT, row=policy,
        workflow_id=workflow_id, stage_id=stage_id, checked_at=now,
    )


def validate_native_limits(provider, started, ended, limits):
    """Fail closed when whole-turn timing or usage exceeds its frozen stop limits."""
    require(isinstance(limits, dict) and set(limits) == {
        "elapsed_seconds", "input_tokens", "output_tokens"},
        "frozen native elapsed and token ceilings required")
    require(all(type(limits[key]) is int and limits[key] > 0 for key in limits),
            "frozen native elapsed and token ceilings must be positive integers")
    elapsed = (ended - started).total_seconds()
    require(elapsed <= limits["elapsed_seconds"], "native elapsed ceiling exceeded")

    usage = provider.get("usage")
    require(isinstance(usage, dict), "native whole-turn token usage unavailable")
    response_count = usage.get("observed_responses")
    require(type(response_count) is int and response_count > 0,
            "native response usage count unavailable")
    require(type(usage.get("usage_records")) is int
            and usage["usage_records"] == response_count,
            "native whole-turn usage records incomplete")
    token_fields = usage.get("tokens")
    require(isinstance(token_fields, dict), "native whole-turn token usage unavailable")
    totals = {}
    for field in ("input", "output"):
        record = token_fields.get(field)
        require(isinstance(record, dict) and type(record.get("total")) is int
                and record["total"] >= 0,
                f"native {field} token usage unavailable")
        totals[field] = record["total"]
    # Input includes cached tokens; output includes its reasoning-token subset.
    require(totals["input"] <= limits["input_tokens"], "native input token ceiling exceeded")
    require(totals["output"] <= limits["output_tokens"], "native output token ceiling exceeded")
    return {"elapsed_seconds": elapsed, "input_tokens": totals["input"],
            "output_tokens": totals["output"]}


def verify_native_join(manifest, observation, native_report, *, pins, now=None,
                       capacity_policy=None):
    """Verify one exact recipient stage against the payload-free workflow report."""
    now = now or datetime.now(timezone.utc)
    require(manifest.get("schema") == WORKFLOW_SCHEMA, "single-workflow manifest schema")
    require(observation.get("schema") == OBSERVATION_SCHEMA, "single-workflow observation schema")
    require(native_report.get("schema") == "cairn.workflow-model-report/1", "native report schema")
    workflow_id = manifest["workflow_id"]
    stage_id = manifest["stage_id"]
    require(observation.get("workflow_id") == workflow_id
            and observation.get("case") == manifest["case"]
            and observation.get("arm") == manifest["arm"], "observation workflow identity mismatch")
    require(observation.get("state") == "observed", "observed native turn required")
    event = observation.get("event")
    require(isinstance(event, str) and gate.OPAQUE.fullmatch(event), "opaque native event required")
    workflows = native_report.get("workflows") or []
    matches = [row for row in workflows if row.get("event") == event]
    require(len(matches) == 1, "native workflow event missing or ambiguous")
    native = matches[0]
    require(native.get("workload") == workflow_id, "native workload mismatch")
    require(native.get("publication") == "committed", "native publication not committed")
    stages = observation.get("stages") or []
    require(len(stages) == 1 and stages[0].get("stage_id") == stage_id,
            "exactly the frozen recipient stage is required")
    stage = stages[0]
    require(stage.get("state") == "observed", "recipient stage not observed")
    require(stage.get("route") == "subscription" and stage.get("model") == MODEL
            and stage.get("effort") == EFFORT, "recipient route/model/effort drift")
    require(stage.get("pins") == pins, "recipient pin drift")
    attempt = stage.get("attempt")
    require(isinstance(attempt, str) and gate.OPAQUE.fullmatch(attempt), "opaque native attempt required")
    deliveries = [item for item in native.get("deliveries", []) if item.get("attempt") == attempt]
    require(len(deliveries) == 1, "exact native delivery attempt missing or ambiguous")
    delivery = deliveries[0]
    require((delivery.get("settlement") or {}).get("status") == "committed",
            "native recipient settlement not committed")
    require(isinstance(delivery.get("turn_join"), str) and delivery["turn_join"],
            "exact native turn join missing")
    provider = delivery.get("provider")
    expected_pin = pins.get("inbox")
    require(gate.provider_ok(provider, {"harness": "codex", "account": ACCOUNT,
                                        "id": MODEL, "effort": EFFORT,
                                        "route": "subscription"}, expected_pin),
            "native provider model/account/effort/pin mismatch")
    require(provider.get("native_completed") is True, "native provider turn did not complete")
    started = gate._utc_timestamp(provider.get("started_at"), "native started_at")
    ended = gate._utc_timestamp(provider.get("ended_at"), "native ended_at")
    require(ended >= started, "native turn end precedes start")
    observed_limits = validate_native_limits(provider, started, ended, manifest.get("limits"))
    refs = provider.get("response_refs")
    require(isinstance(refs, list) and refs and all(gate.OPAQUE.fullmatch(x) for x in refs)
            and len(refs) == len(set(refs)), "native response references missing or duplicated")
    usage = provider.get("usage") or {}
    require(type(usage.get("observed_responses")) is int
            and usage["observed_responses"] == len(refs),
            "native response references do not match observed response count")
    capacity_ref = stage.get("capacity_check")
    require(isinstance(capacity_ref, dict), "recipient stage capacity check required")
    capacity = gate.load_capacity_evidence(capacity_ref, purpose="task")
    policy = capacity_policy or {"purpose": "task",
                                 "ceiling_used_percent": CEILING_USED_PERCENT,
                                 "reserve_percentage_points": RESERVE_PERCENTAGE_POINTS}
    initial_ref = manifest["capacity_admission"]["accounts"][0]
    require((capacity_ref["evidence_path"], capacity_ref["evidence_sha256"])
            != (initial_ref["evidence_path"], initial_ref["evidence_sha256"]),
            "stage capacity sample cannot reuse initial admission sample")
    gate.validate_utilization_evidence(capacity, harness="codex", account=ACCOUNT, row=policy,
                                       started_at=started, workflow_id=workflow_id,
                                       stage_id=stage_id)
    return {
        "schema": "cairn.token-efficiency-single-workflow-join/1",
        "workflow_id": workflow_id,
        "event": event,
        "attempt": attempt,
        "turn_join": delivery["turn_join"],
        "response_refs": refs,
        "observed_limits": observed_limits,
        "limits_enforced_post_turn": True,
        "provider_hard_cap": False,
        "exact_native_join": True,
        "independent_task_acceptance": "unassessed",
        "provider_call_observed": True,
    }


def _read_response(proc, selector, request_id, timeout, buffered):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if b"\n" in buffered:
            line, _, remainder = buffered.partition(b"\n")
            buffered[:] = remainder
            if not line:
                continue
            value = json.loads(line)
            if value.get("id") == request_id:
                return value
            continue
        if len(buffered) > 2_000_000:
            raise ValueError("Codex status response exceeds 2 MB")
        if not selector.select(max(0.0, deadline - time.monotonic())):
            continue
        chunk = os.read(proc.stdout.fileno(), 65536)
        if not chunk:
            break
        buffered.extend(chunk)
    raise ValueError("Codex app-server status response timed out or closed")


def codex_capacity_snapshot(codex_home, *, workflow_id=None, stage_id=None, timeout=20):
    """Read account utilization using Codex app-server; this does not start inference."""
    home = Path(codex_home).expanduser().resolve()
    require(home == (Path.home() / ".codex").resolve(), "selected account must be ~/.codex")
    config_path = home / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    require(config.get("model_provider") is None and not config.get("model_providers"),
            "custom/API model providers are forbidden for subscription status")
    executable = shutil.which("codex")
    require(executable is not None, "Codex CLI unavailable")
    env = dict(os.environ)
    env["CODEX_HOME"] = str(home)
    env.pop("OPENAI_API_KEY", None)
    env.pop("OPENROUTER_API_KEY", None)
    proc = subprocess.Popen([executable, "app-server", "--listen", "stdio://"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env, bufsize=0)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    buffered = bytearray()
    try:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"clientInfo": {"name": "cairn_capacity_read", "title": "Cairn status read",
                                            "version": "1"}, "capabilities": {}}}
        proc.stdin.write((json.dumps(init, separators=(",", ":")) + "\n").encode())
        response = _read_response(proc, selector, 1, timeout, buffered)
        require(response.get("id") == 1 and "result" in response,
                "Codex app-server initialization failed")
        proc.stdin.write(b'{"jsonrpc":"2.0","method":"initialized","params":{}}\n')
        proc.stdin.write(b'{"jsonrpc":"2.0","id":2,"method":"account/rateLimits/read","params":{}}\n')
        response = _read_response(proc, selector, 2, timeout, buffered)
        require(response.get("id") == 2 and "result" in response,
                "Codex subscription utilization status unavailable")
        result = response["result"]
        require(result.get("ordinaryUsageAllowed") is True,
                "Codex subscription ordinary usage is not allowed")
        limits = result.get("rateLimits") or {}
        require(isinstance(limits, dict) and limits.get("limitId") == "codex",
                "Codex weekly utilization window unavailable")
        primary = limits.get("primary") or {}
        used = primary.get("usedPercent")
        require(type(used) is int and 0 <= used <= 100, "Codex utilization percentage unavailable")
        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(seconds=60)
        reset = primary.get("resetsAt")
        if type(reset) is int:
            reset_at = datetime.fromtimestamp(reset, timezone.utc)
            if now < reset_at < valid_until:
                valid_until = reset_at
        evidence = {
            "schema": "cairn.provider-capacity-evidence/1",
            "provider": "openai", "route": "subscription", "harness": "codex",
            "account": ACCOUNT, "source_type": "caller_recorded",
            "measurement": "utilization_status", "ordinary_usage_allowed": True,
            "used_percent": used, "observed_at": now.isoformat().replace("+00:00", "Z"),
            "valid_until": valid_until.isoformat().replace("+00:00", "Z"),
        }
        if workflow_id is not None or stage_id is not None:
            require(workflow_id is not None and stage_id is not None,
                    "capacity stage identity must be complete")
            evidence.update(workflow_id=workflow_id, stage_id=stage_id)
        return evidence
    finally:
        selector.close()
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _command(argv, *, cwd=ROOT, env=None):
    completed = subprocess.run(argv, cwd=cwd, env=env, check=False,
                                capture_output=True, text=True, timeout=30)
    if completed.returncode:
        raise ValueError(f"read-only pin command failed: {Path(argv[0]).name}")
    return (completed.stdout or completed.stderr).strip()


def runtime_pin_closure(codex_home):
    """Collect a sanitized selected-account and installed deployment snapshot."""
    home = Path(codex_home).resolve()
    require(home == (Path.home() / ".codex").resolve(), "selected account must be ~/.codex")
    status_env = dict(os.environ)
    status_env["CODEX_HOME"] = str(home)
    status_env.pop("OPENAI_API_KEY", None)
    status_env.pop("OPENROUTER_API_KEY", None)
    config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    require(config.get("model_provider") is None and not config.get("model_providers"),
            "custom/API model providers are forbidden for subscription canary")
    codex_path = Path(shutil.which("codex") or "").resolve()
    require(str(codex_path) != "." and codex_path.is_file(), "Codex CLI unavailable")
    account_status = _command([str(codex_path), "login", "status"], env=status_env)
    require("Logged in using ChatGPT" in account_status,
            "selected .codex account is not signed in through ChatGPT")
    inventory_text = _command([sys.executable, "-B", "scripts/check_effective_recall_deployment.py",
                               "--compact"])
    inventory = json.loads(inventory_text)
    require(inventory.get("schema") == "cairn.effective-recall-deployment-check/1"
            and inventory.get("ok") is True and inventory.get("installed_verified") is True
            and inventory.get("read_only_verified") is True,
            "effective installed Cairn pins are not verified")
    ordinary = [row for row in inventory.get("ordinary_hooks", [])
                if row.get("account_home") == str(home)]
    require(len(ordinary) == 1, "selected Codex ordinary hook pin is ambiguous")
    ordinary = ordinary[0]
    require(not ordinary.get("issues"), "selected Codex ordinary hook has deployment issues")
    binding_path = ordinary.get("memory_hook", {}).get("inbox_recall_binding")
    binding = [row for row in inventory.get("inbox_bindings", [])
               if row.get("binding_path") == binding_path]
    require(len(binding) == 1 and binding[0].get("enabled") is True
            and binding[0].get("pins_valid") is True and binding[0].get("contract_valid") is True,
            "selected Codex inbox binding is not enabled and valid")
    binding = binding[0]
    api_cli = json.loads(_command(["cairn", "version"]))
    token_file = Path.home() / ".local/share/cairn/hosted-agent.token"
    require(token_file.is_file(), "hosted Cairn profile unavailable for installed API pin")
    api_server = json.loads(_command(["cairn", "agent", "--token-file", str(token_file), "version"]))
    api_revision = api_server.get("data", {}).get("server", {}).get("vcs_revision")
    if not api_revision:
        api_revision = api_server.get("data", {}).get("vcs_revision")
    require(isinstance(api_revision, str) and len(api_revision) >= 7,
            "installed Cairn API revision unavailable")
    module_revision = api_cli.get("data", {}).get("vcs_revision")
    require(isinstance(module_revision, str) and len(module_revision) >= 7,
            "Cairn CLI revision unavailable")
    memory = ordinary["memory_hook"]
    coord = ordinary["coordination_hook"]
    bridge = binding["bridge"]
    stage_revision = _command(["git", "rev-parse", "HEAD"])
    dirty = bool(_command(["git", "status", "--porcelain"]))
    return {
        "account": {"handle": ACCOUNT, "home": str(home),
                    "config_sha256": sha_file(home / "config.toml"),
                    "hooks_sha256": sha_file(home / "hooks.json"),
                    "identity_exported": False},
        "model": {"harness": "codex", "id": MODEL, "effort": EFFORT,
                  "route": "subscription", "provider_api_keys_removed": True,
                  "custom_provider_config": False},
        "source": {"repository": str(ROOT), "revision": stage_revision, "dirty": dirty,
                   "runner_sha256": sha_file(Path(__file__)),
                   "gate_sha256": sha_file(ROOT / "scripts/token_efficiency_gate.py")},
        "installed": {
            "cairn_cli_revision": module_revision,
            "cairn_api_revision": api_revision,
            "codex_version": _command([str(codex_path), "--version"], env=status_env),
            "codex_binary_sha256": sha_file(codex_path),
            "ordinary_hook_settings_sha256": sha_file(home / "hooks.json"),
            "ordinary_engine_sha256": memory["engine_sha256"],
            "ordinary_config_sha256": memory["config_sha256"],
            "coordination_engine_sha256": sha_file(coord["script"]),
            "coordination_config_sha256": coord["config_sha256"],
            "inbox_binding_sha256": binding["manifest_sha256"],
            "inbox_bridge_sha256": bridge["actual_sha256"],
            "effective_recall_live_observed": inventory.get("live_observed") is True,
        },
    }


def gate_pins_from_runtime(runtime):
    installed = runtime["installed"]
    return {
        "api": installed["cairn_api_revision"],
        "ordinary": gate.sha({key: installed[key] for key in
                              ("ordinary_engine_sha256", "ordinary_config_sha256")}),
        "inbox": gate.sha({key: installed[key] for key in
                           ("coordination_engine_sha256", "coordination_config_sha256",
                            "inbox_binding_sha256", "inbox_bridge_sha256")}),
        "client": gate.sha({key: installed[key] for key in
                            ("codex_version", "codex_binary_sha256",
                             "ordinary_hook_settings_sha256")}),
    }


def create_owner_only_dir(path):
    requested = Path(path).expanduser().absolute()
    parent = requested.parent.resolve(strict=True)
    repository = ROOT.resolve(strict=True)
    require(parent != repository and repository not in parent.parents,
            "provider evidence must be written outside the repository")
    parent_info = parent.stat()
    require(stat.S_ISDIR(parent_info.st_mode) and parent_info.st_uid == os.getuid()
            and stat.S_IMODE(parent_info.st_mode) == 0o700,
            "output parent must be an owner-only directory")
    path = parent / requested.name
    require(not path.exists() and not path.is_symlink(), "output must be a new directory")
    path.mkdir(mode=0o700)
    require(stat.S_IMODE(path.stat().st_mode) == 0o700, "output directory must be mode 0700")
    return path


def write_private_json(path, value):
    path = Path(path)
    parent_info = path.parent.stat()
    require(not path.parent.is_symlink() and stat.S_ISDIR(parent_info.st_mode)
            and parent_info.st_uid == os.getuid()
            and stat.S_IMODE(parent_info.st_mode) == 0o700,
            "private JSON parent must be owner-only and owner-owned")
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def freeze_dry_run(*, fixture_path, controller_path, codex_home, output_dir,
                   case="single_notice", arm="baseline", elapsed_seconds=None,
                   input_tokens=None, output_tokens=None):
    selection = select_workflow(fixture_path, controller_path, case=case, arm=arm)
    output_candidate = Path(output_dir).expanduser().absolute()
    require(not output_candidate.exists() and not output_candidate.is_symlink(),
            "output must be a new directory")
    require(type(elapsed_seconds) is int and elapsed_seconds > 0, "positive elapsed limit required")
    require(type(input_tokens) is int and input_tokens > 0, "positive input-token limit required")
    require(type(output_tokens) is int and output_tokens > 0, "positive output-token limit required")
    stage_id = f"{selection['workflow_id']}.{STAGE_SUFFIX}"
    require(gate.ID.fullmatch(stage_id), "workflow stage ID is invalid")
    pins = runtime_pin_closure(codex_home)
    require(pins["source"]["dirty"] is False, "source checkout must be clean before freeze")
    # These are status reads only. No task prompt, inference turn, or provider model call is made.
    initial = codex_capacity_snapshot(codex_home)
    stage_capacity = codex_capacity_snapshot(codex_home, workflow_id=selection["workflow_id"],
                                             stage_id=stage_id)
    policy = {"purpose": "task", "ceiling_used_percent": CEILING_USED_PERCENT,
              "reserve_percentage_points": RESERVE_PERCENTAGE_POINTS}
    now = datetime.now(timezone.utc)
    gate.validate_utilization_evidence(initial, harness="codex", account=ACCOUNT,
                                       row=policy, checked_at=now)
    validate_stage_capacity(stage_capacity, policy, workflow_id=selection["workflow_id"],
                            stage_id=stage_id, now=now)
    gate_pins = gate_pins_from_runtime(pins)
    # The actual fixture acceptance criterion is pinned, but no independent grade is asserted.
    output = create_owner_only_dir(output_dir)
    initial_path = output / "capacity-initial.json"
    stage_path = output / "capacity-baseline-single-notice-recipient.json"
    write_private_json(initial_path, initial)
    write_private_json(stage_path, stage_capacity)
    initial_ref = {"evidence_path": str(initial_path), "evidence_sha256": sha_file(initial_path)}
    stage_ref = {"evidence_path": str(stage_path), "evidence_sha256": sha_file(stage_path)}
    manifest = {
        "schema": WORKFLOW_SCHEMA,
        "gate_schema": "cairn.token-efficiency-plan/1 (single-workflow dry-run subset)",
        "frozen_at": now.isoformat().replace("+00:00", "Z"),
        "fixture_path": str(Path(fixture_path).resolve()),
        "controller_path": str(Path(controller_path).resolve()),
        **selection,
        "stage_id": stage_id,
        "stage_role": "recipient",
        "model": {"harness": "codex", "account": ACCOUNT, "id": MODEL,
                  "effort": EFFORT, "route": "subscription"},
        "limits": {"elapsed_seconds": elapsed_seconds, "input_tokens": input_tokens,
                   "output_tokens": output_tokens},
        "pins": gate_pins,
        "runtime_pin_closure": pins,
        "capacity_policy": policy,
        "capacity_interpretation": "weekly utilization percentage only; absolute allowance unknown",
        "capacity_admission": {"max_concurrent_workflows": 1, "accounts": [{
            "harness": "codex", "account": ACCOUNT, "purpose": "task",
            "ceiling_used_percent": CEILING_USED_PERCENT,
            "reserve_percentage_points": RESERVE_PERCENTAGE_POINTS, **initial_ref,
        }]},
        "dry_run_stage_capacity_check": stage_ref,
        "execution": {"provider_request_made": False, "provider_task_started": False,
                      "live_admission_authorized": False},
        "full_panel_status": "incomplete",
        "release_decision": "defer",
        "independent_task_acceptance": "unassessed",
    }
    validate_stage_capacity(stage_capacity, policy, workflow_id=selection["workflow_id"],
                            stage_id=stage_id, now=now)
    manifest["manifest_sha256"] = gate.sha(manifest)
    write_private_json(output / "manifest.json", manifest)
    receipt = {
        "schema": "cairn.token-efficiency-single-workflow-dry-run-receipt/1",
        "manifest_sha256": manifest["manifest_sha256"],
        "state": "preflight_passed",
        "full_panel_status": "incomplete",
        "provider_request_made": False,
        "provider_task_started": False,
        "exact_native_join": "pending_live_turn",
        "independent_task_acceptance": "unassessed",
        "live_admission_authorized": False,
        "absolute_allowance": "unknown",
        "reason": "dry-run validates pins and caller-recorded capacity only; the paired gate, independent grade, live join, and rollback remain required",
    }
    write_private_json(output / "dry-run.json", receipt)
    return receipt


def verify_frozen_manifest(manifest):
    require(manifest.get("schema") == WORKFLOW_SCHEMA, "single-workflow manifest schema")
    stored = manifest.get("manifest_sha256")
    body = dict(manifest)
    body.pop("manifest_sha256", None)
    require(stored == gate.sha(body), "single-workflow manifest hash mismatch")
    require(manifest.get("execution") == {"provider_request_made": False,
                                           "provider_task_started": False,
                                           "live_admission_authorized": False},
            "dry-run manifest cannot authorize provider execution")
    require(manifest.get("full_panel_status") == "incomplete"
            and manifest.get("release_decision") == "defer"
            and manifest.get("independent_task_acceptance") == "unassessed",
            "one-workflow evidence cannot pass the paired panel or task grade")
    require(manifest.get("capacity_interpretation")
            == "weekly utilization percentage only; absolute allowance unknown",
            "capacity percentage cannot stand in for an absolute allowance")
    require(manifest.get("case") == "single_notice" and manifest.get("arm") == "baseline",
            "unsupported one-workflow selection")
    require(manifest.get("model") == {"harness": "codex", "account": ACCOUNT, "id": MODEL,
                                       "effort": EFFORT, "route": "subscription"},
            "frozen Codex Luna subscription account/model drift")
    selection = select_workflow(manifest["fixture_path"], manifest["controller_path"],
                                case=manifest["case"], arm=manifest["arm"])
    for key, value in selection.items():
        require(manifest.get(key) == value, f"frozen fixture/controller {key} mismatch")
    require(manifest.get("stage_role") == "recipient"
            and manifest.get("stage_id") == f"{manifest['workflow_id']}.{STAGE_SUFFIX}",
            "frozen recipient stage identity mismatch")
    require(set(manifest.get("pins") or {}) == {"api", "ordinary", "inbox", "client"}
            and all(isinstance(pin, str) and gate.ID.fullmatch(pin)
                    for pin in manifest["pins"].values()), "frozen deployment pins missing")
    require(manifest["pins"] == gate_pins_from_runtime(manifest["runtime_pin_closure"]),
            "gate pins do not match the separate source/client/API closure")
    limits = manifest.get("limits") or {}
    require(isinstance(limits, dict)
            and set(limits) == {"elapsed_seconds", "input_tokens", "output_tokens"}
            and all(type(limits[key]) is int and limits[key] > 0 for key in limits),
            "frozen elapsed and token ceilings required")
    policy_rows = (manifest.get("capacity_admission") or {}).get("accounts") or []
    require(len(policy_rows) == 1 and policy_rows[0].get("purpose") == "task"
            and policy_rows[0].get("harness") == "codex"
            and policy_rows[0].get("account") == ACCOUNT,
            "single-workflow Codex capacity policy missing")
    frozen_at = gate._utc_timestamp(manifest.get("frozen_at"), "manifest frozen_at")
    initial = gate.load_capacity_evidence(
        {key: policy_rows[0][key] for key in ("evidence_path", "evidence_sha256")},
        purpose="task")
    gate.validate_utilization_evidence(initial, harness="codex", account=ACCOUNT,
                                       row=policy_rows[0], checked_at=frozen_at)
    stage_ref = manifest.get("dry_run_stage_capacity_check")
    stage_evidence = gate.load_capacity_evidence(stage_ref, purpose="task")
    validate_stage_capacity(stage_evidence, policy_rows[0], workflow_id=manifest["workflow_id"],
                            stage_id=manifest["stage_id"], now=frozen_at)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze", help="record a provider-free one-workflow preflight")
    freeze.add_argument("--fixture", type=Path, default=FIXTURE)
    freeze.add_argument("--controller", required=True, type=Path)
    freeze.add_argument("--case", default="single_notice")
    freeze.add_argument("--arm", default="baseline")
    freeze.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    freeze.add_argument("--output-dir", required=True, type=Path)
    freeze.add_argument("--elapsed-seconds", type=int, required=True)
    freeze.add_argument("--input-tokens", type=int, required=True)
    freeze.add_argument("--output-tokens", type=int, required=True)
    join = sub.add_parser("join", help="check one exact post-run native workflow join")
    join.add_argument("--manifest", required=True, type=Path)
    join.add_argument("--observation", required=True, type=Path)
    join.add_argument("--workflow-report", required=True, type=Path)
    join.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            receipt = freeze_dry_run(fixture_path=args.fixture, controller_path=args.controller,
                                     codex_home=args.codex_home, output_dir=args.output_dir,
                                     case=args.case, arm=args.arm,
                                     elapsed_seconds=args.elapsed_seconds,
                                     input_tokens=args.input_tokens, output_tokens=args.output_tokens)
            print(json.dumps(receipt, sort_keys=True))
            return 0
        manifest = verify_frozen_manifest(read_owner_json(args.manifest, label="frozen manifest"))
        current_pins = runtime_pin_closure(Path.home() / ".codex")
        require(current_pins == manifest["runtime_pin_closure"], "selected source/account/deployment pin drift")
        observation = read_owner_json(args.observation, label="observation")
        native = read_owner_json(args.workflow_report, label="workflow report")
        join_result = verify_native_join(
            manifest, observation, native, pins=manifest["pins"],
            capacity_policy=manifest["capacity_policy"])
        write_private_json(args.output, join_result)
        print(json.dumps({"state": "exact_native_join_verified",
                          "provider_call_observed": True,
                          "independent_task_acceptance": "unassessed"}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"token_efficiency_single_workflow: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
