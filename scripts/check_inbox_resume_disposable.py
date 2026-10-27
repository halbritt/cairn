#!/usr/bin/env python3
"""Cross-layer compact/resume hook probe against one disposable Cairn Store.

All lifecycle callbacks are synthetic host events. No Codex compaction, model
retention, provider call, or actor task is observed.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import select
import stat
import subprocess
import sys
import time
import uuid


OWNER = r'''
import json, subprocess, sys
script, config = sys.argv[1:3]
for line in sys.stdin:
    result = subprocess.run([sys.executable, script, 'hook', '--config', config],
        input=line, text=True, capture_output=True, timeout=15)
    print(json.dumps(dict(code=result.returncode, stdout=result.stdout,
                          stderr=result.stderr)), flush=True)
'''

SELECTOR = r'''#!/usr/bin/env python3
import json, sys
sys.stdin.read()
print(json.dumps(dict(type='result', is_error=False,
    structured_output=dict(checkpoint=None, workstream=None, memories=[]))))
'''


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def stop(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)


def check(binary):
    root = Path(os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    require(root.parent == Path("/tmp") and root.name.startswith("cairn-postgres.")
            and root.stat().st_uid == os.getuid()
            and stat.S_IMODE(root.stat().st_mode) == 0o700
            and (root / "data" / "PG_VERSION").is_file(),
            "fresh private PostgreSQL cluster required")
    require(binary == root / "cairn" and binary.is_file() and not binary.is_symlink(),
            "disposable Cairn binary required")
    dsn = f"host={root / 'socket'} dbname=cairn_inbox_resume_disposable sslmode=disable"
    require(os.environ.get("CAIRN_TEST_DATABASE_URL") == dsn
            and os.environ.get("CAIRN_DATABASE_URL") == dsn,
            "dedicated disposable database required")

    def sql(statement):
        result = subprocess.run(["psql", "-X", "-A", "-t", "-d", dsn, "-c", statement],
                                capture_output=True, text=True, timeout=10)
        require(result.returncode == 0, result.stderr)
        return result.stdout.strip()

    require(sql("SELECT count(*) FROM cairn.agent_event") == "0"
            and sql("SELECT count(*) FROM cairn.agent_session") == "0"
            and sql("SELECT count(*) FROM cairn.memory_record") == "0",
            "resume database is not fresh")
    repo = "fixture:cairn-224-resume"
    home = root / "inbox-resume-home"
    home.mkdir(mode=0o700)
    work = home / "work"
    (work / ".git").mkdir(parents=True)
    identities = []
    for role in ("sender", "worker"):
        token = secrets.token_urlsafe(32)
        path = home / f"{role}.token"
        path.write_text(token, encoding="utf-8")
        path.chmod(0o600)
        identities.append(dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                               principal=f"agent/resume-{role}-{uuid.uuid4()}", repo=repo,
                               role="agent", destination="hosted"))
    identities_path = home / "identities.json"
    identities_path.write_text(json.dumps(identities), encoding="utf-8")
    identities_path.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(home), CAIRN_INBOX_ROOT=str(home))
    server = subprocess.Popen([str(binary), "serve"], env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    owner = None
    try:
        socket = home / "api.sock"
        deadline = time.monotonic() + 10
        while not socket.exists():
            require(server.poll() is None and time.monotonic() < deadline,
                    "disposable API did not start")
            time.sleep(0.02)
        require(stat.S_ISSOCK(socket.stat().st_mode), "disposable API socket missing")

        def profile(role, command, body, session=None):
            options = ["--socket", str(socket), "--token-file", str(home / f"{role}.token")]
            if session:
                options += ["--agent-id", session["agent_id"],
                            "--execution-id", session["execution_id"]]
            result = subprocess.run([str(binary), "agent", *options, command],
                                    input=json.dumps(body), capture_output=True, text=True,
                                    env=env, timeout=15)
            envelope = json.loads(result.stdout)
            require(result.returncode == 0 and envelope["status"] == "OK",
                    f"{command}: {envelope['status']} {result.stderr}")
            return envelope["data"]

        def operator(command, payload=None, arg=None):
            result = subprocess.run([str(binary), command, *([arg] if arg else [])],
                                    input=json.dumps(payload) if payload is not None else None,
                                    capture_output=True, text=True, env=env, timeout=15)
            envelope = json.loads(result.stdout)
            require(result.returncode == 0 and envelope["status"] == "OK",
                    f"operator {command}: {envelope['status']} {result.stderr}")
            return envelope["data"]

        for role in ("sender", "worker"):
            profile(role, "version", {})
        session_id = str(uuid.uuid4())
        turn_id = str(uuid.uuid4())
        binding_path = home / "inbox-recall.json"
        coord_state = home / "coordination"
        memory_state = home / "memory"
        coord_state.mkdir(mode=0o700)
        memory_state.mkdir(mode=0o700)
        selector = home / "local-selector"
        selector.write_text(SELECTOR, encoding="utf-8")
        selector.chmod(0o700)
        common = dict(cairn=str(binary), socket=str(socket),
                      token_file=str(home / "worker.token"), repo=repo,
                      harness="codex", inbox_recall_binding=str(binding_path))
        coordination_config = home / "coordination.json"
        memory_config = home / "memory.json"
        coord = dict(common, state_dir=str(coord_state), binding="resume-probe",
                     native_delivery=True, model="synthetic/probe",
                     process_names=["python3"])
        mem = dict(common, state_dir=str(memory_state), recall_mode="agent_tools",
                   context_lifecycle="compact/1", context_bytes=9500,
                   claude=str(selector))
        for path, value in ((coordination_config, coord), (memory_config, mem)):
            path.write_text(json.dumps(value), encoding="utf-8")
            path.chmod(0o600)
        source_lifecycle = Path(__file__).resolve().parents[1] / "integrations/lifecycle"
        lifecycle = home / "lifecycle"
        spec = importlib.util.spec_from_file_location("fixture_lifecycle_bundle",
                                                    source_lifecycle / "lifecycle_bundle.py")
        bundle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bundle)
        bundle.stage_runtime(source_lifecycle, lifecycle)
        descriptor = bundle.bundle_descriptor(lifecycle)

        def pinned(path):
            return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())

        manifest = dict(schema="cairn.inbox-recall-binding/2", enabled=True,
            bridge=dict(pinned(lifecycle / "inbox_recall.py"), bundle=descriptor),
            engine=dict(pinned(lifecycle / "memory.py"), bundle=descriptor),
            memory_config=pinned(memory_config), coordination_configs=[pinned(coordination_config)])
        binding_path.write_text(json.dumps(manifest), encoding="utf-8")
        binding_path.chmod(0o600)
        bridge = bundle.Bundle(lifecycle).load_pinned(manifest["bridge"], "inbox_recall")
        bridge.validate_binding(mem, require_enabled=True)

        base_event = dict(session_id=session_id, turn_id=turn_id, cwd=str(work),
                          workstream="Fixture repair", model="synthetic/probe")
        owner = subprocess.Popen([sys.executable, "-u", "-c", OWNER,
                                  str(lifecycle / "coordination.py"), str(coordination_config)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, env=env)

        def coord_hook(name):
            event = dict(base_event, hook_event_name=name,
                         prompt="Synthetic native request boundary")
            owner.stdin.write(json.dumps(event) + "\n")
            owner.stdin.flush()
            require(select.select([owner.stdout], [], [], 20)[0],
                    "coordination hook timed out")
            response = json.loads(owner.stdout.readline())
            require(response["code"] == 0, f"coordination {name}: {response['stderr']}")
            return json.loads(response["stdout"])

        def memory_hook(name, source="startup"):
            event = dict(base_event, hook_event_name=name, source=source, prompt="")
            result = subprocess.run([sys.executable, str(lifecycle / "memory.py"),
                                     "--config", str(memory_config)], input=json.dumps(event),
                                    capture_output=True, text=True, env=env, timeout=20)
            require(result.returncode == 0,
                    f"memory {name}/{source}: {result.stdout} {result.stderr}")
            return json.loads(result.stdout)

        root_grant = operator("bootstrap", dict(request_id=str(uuid.uuid4()),
                         reason="Authorize disposable compact/resume instruction"))
        policy_key = "resume-probe-current-rule"
        scope = dict(repo=repo, task_id="*", run_id="*")
        old_rule = "Old required fixture rule before resume."
        new_rule = "Current required fixture rule after resume."

        def issue(body):
            return operator("issue", dict(request_id=str(uuid.uuid4()),
                grant_id=root_grant["grant_id"], mandatory=True,
                policy_key=policy_key, category="workflow",
                reason="Verify current required context through synthetic lifecycle callbacks",
                draft=dict(kind="instruction", body=body, claim_type="self",
                           sensitivity="shareable", scope=scope)))

        original_rule = issue(old_rule)
        title = "Handoff: work / Fixture repair"
        old_checkpoint = title + "\nNext: verify the original fixture identity."
        new_checkpoint = title + "\nNext: verify the corrected retry identity."
        checkpoint = profile("worker", "create", dict(request_id=str(uuid.uuid4()),
            draft=dict(kind="note", body=old_checkpoint, scope=scope,
                       claim_type="self", sensitivity="shareable")))
        memory_hook("SessionStart")
        coord_hook("UserPromptSubmit")
        result = subprocess.run([str(binary), "agents", "list", "--socket", str(socket),
            "--token-file", str(home / "worker.token"), "--harness", "codex"],
            capture_output=True, text=True, env=env, timeout=15)
        require(result.returncode == 0, "worker registration failed")
        agents = json.loads(result.stdout)["data"]["agents"]
        matched = [agent for agent in agents if agent["native_session_id"] == session_id]
        require(len(matched) == 1, "native hook did not register one worker")
        worker = matched[0]
        agent_id = str(uuid.UUID(worker["agent_id"]))
        require(sql(f"SELECT owner FROM cairn.agent_session WHERE agent_id='{agent_id}'")
                == identities[1]["principal"], "Store worker owner mismatch")
        request_body = "Resume the fixture request with current context."
        source = profile("sender", "create", dict(request_id=str(uuid.uuid4()),
            draft=dict(kind="note", body=request_body, scope=scope,
                       claim_type="self", sensitivity="shareable")))
        ref = dict(record_id=source["record_id"], version=source["version"])
        event = profile("sender", "event-publish", dict(request_id=str(uuid.uuid4()),
            kind="request", ref=ref,
            destination=dict(type="agent", name=worker["inbox"])))
        require(coord_hook("Stop").get("decision") == "block",
                "synthetic turn did not claim the request")
        contexts = [json.loads(path.read_text()) for path in (coord_state / "inbox").glob("*.json")]
        contexts = [context for context in contexts if context.get("event_id") == event["event_id"]]
        require(len(contexts) == 1, "native context missing")
        context = contexts[0]
        attempt_id = context["attempt_id"]
        delivery_id = context["delivery_id"]
        leased = profile("sender", "event-inspect", dict(event_id=event["event_id"]))["deliveries"]
        require(len(leased) == 1 and leased[0]["state"] == "leased"
                and leased[0]["attempts"] == 1
                and leased[0]["delivery_id"] == delivery_id,
                "request did not retain one native claim")
        transcript = work / "empty-rollout.jsonl"
        transcript.write_text("", encoding="utf-8")
        pre = dict(base_event, hook_event_name="PreCompact", transcript_path=str(transcript))
        pre_result = subprocess.run([sys.executable, str(lifecycle / "memory.py"),
            "--config", str(memory_config)], input=json.dumps(pre), text=True,
            capture_output=True, env=env, timeout=20)
        require(pre_result.returncode == 0, f"synthetic PreCompact failed: {pre_result.stderr}")
        compact = memory_hook("SessionStart", "compact")
        compact_text = compact["hookSpecificOutput"]["additionalContext"]
        require(old_rule in compact_text,
                f"compact callback omitted current required/checkpoint content: {compact_text[:2000]!r}")
        compact_selected, _ = json.JSONDecoder().raw_decode(
            compact_text[compact_text.index('{"selected":'):])
        require(len(compact_selected["selected"]) == 1
                and compact_selected["selected"][0]["mandatory"] is True
                and compact_selected["selected"][0]["record"]["record_id"]
                == original_rule["record_id"],
                "compact required rule identity differs from the Store issuance")
        checkpoint_start = compact_text.index('{"schema":"cairn.checkpoint-refresh/1"')
        compact_view = json.loads(compact_text[checkpoint_start:])
        require(compact_view["record_id"] == checkpoint["record_id"]
                and compact_view["version"] == 1 and compact_view["body"] == old_checkpoint
                and compact_view["source_extent"] == "whole",
                "compact checkpoint was not the exact whole Store source")

        # Commit the result, then lose the reply transport before publication.
        # The partial journal must survive the compact/resume callbacks below.
        result_body = "Synthetic resolved fixture result."
        completion = subprocess.run(context["completion"], input=result_body, text=True,
                                    capture_output=True, env=env, timeout=30)
        require(completion.returncode == 0,
                f"completion before compaction failed: {completion.stdout} {completion.stderr}")
        completion_data = json.loads(completion.stdout)["data"]
        result_ref = completion_data["result"]
        require(completion_data["state"] == "handled" and result_ref is not None,
                "completion did not commit before compact/resume")
        lost_reply_argv = [*context["commands"]["respond"], "--version",
                           str(result_ref["version"]), result_ref["record_id"]]
        hidden_socket = home / "api.sock.unavailable"
        os.rename(socket, hidden_socket)
        try:
            failed_reply = subprocess.run([*context["finish_reply"]], input=result_body,
                text=True, capture_output=True, env=env, timeout=30)
            failure = json.loads(failed_reply.stdout)
            require(failed_reply.returncode != 0
                    and failure["status"] == "API_CONNECTION_FAILED",
                    "reply transport loss did not retain a pending reply")
        finally:
            os.rename(hidden_socket, socket)
        journal_path = coord_state / "intents" / f"{attempt_id}.json"
        pending = json.loads(journal_path.read_text())
        require(pending["completion"]["status"] == "committed"
                and pending["completion"]["result"] == result_ref
                and pending["response"]["status"] == "pending"
                and pending["response"]["argv"] == lost_reply_argv
                and pending["response"]["request_id"] == context["request_ids"]["response"],
                "compact-boundary partial completion/reply intent changed")

        preview = operator("preview-retract", arg=original_rule["record_id"])
        operator("retract", dict(request_id=str(uuid.uuid4()),
            record_id=original_rule["record_id"], expected_version=original_rule["version"],
            grant_id=root_grant["grant_id"], preview_id=preview["preview_id"],
            reason="Replace the earlier fixture instruction before resume"))
        current_rule = issue(new_rule)
        require(current_rule["record_id"] != original_rule["record_id"],
                "reissued required rule reused retracted identity")
        revised = profile("worker", "revise", dict(request_id=str(uuid.uuid4()),
            record_id=checkpoint["record_id"], expected_version=1, repo=repo,
            body=new_checkpoint))
        require(revised["version"] == 2, "checkpoint revision was not stored")
        resume = memory_hook("SessionStart", "resume")
        resume_text = resume["hookSpecificOutput"]["additionalContext"]
        require(new_rule in resume_text and old_rule not in resume_text
                and "original fixture identity" not in resume_text,
                "resume did not replace stale required/checkpoint content")
        resume_selected, _ = json.JSONDecoder().raw_decode(
            resume_text[resume_text.index('{"selected":'):])
        require(len(resume_selected["selected"]) == 1
                and resume_selected["selected"][0]["mandatory"] is True
                and resume_selected["selected"][0]["record"]["record_id"]
                == current_rule["record_id"],
                "resume required rule identity differs from current Store issuance")
        resume_start = resume_text.index('{"schema":"cairn.checkpoint-refresh/1"')
        resume_view = json.loads(resume_text[resume_start:])
        require(resume_view["record_id"] == checkpoint["record_id"]
                and resume_view["version"] == 2 and resume_view["body"] == new_checkpoint
                and resume_view["source_extent"] == "whole",
                "resume checkpoint was not current whole version")
        state = json.loads((memory_state / f"{session_id}.json").read_text())
        ledger = json.loads((memory_state / f"{session_id}.memory-budget.json").read_text())
        require(state["last_recall"]["checkpoint_refresh"] == "restored"
                and state["workstream"] == title,
                "resume state did not retain its current workstream")
        grant = ledger["turns"].get(turn_id, ledger["pending"])
        require(grant["required_refresh_bytes"] > 0
                and grant["checkpoint_refresh_bytes"] > 0,
                "refresh bytes were not separately accounted")
        retained = profile("sender", "event-inspect", dict(event_id=event["event_id"]))["deliveries"]
        require(len(retained) == 1 and retained[0]["state"] == "handled"
                and retained[0]["delivery_id"] == delivery_id
                and retained[0]["attempts"] == 1
                and sql("SELECT count(*) FROM cairn.agent_session_attempt") == "1",
                "synthetic lifecycle callbacks replaced the partially settled native attempt")
        pending_after_resume = json.loads(journal_path.read_text())
        require(pending_after_resume["completion"] == pending["completion"]
                and pending_after_resume["response"] == pending["response"],
                "resume changed the committed result or pending reply intent")
        first = subprocess.run(context["finish_reply"], input=result_body, text=True,
                               capture_output=True, env=env, timeout=30)
        require(first.returncode == 0, f"finish_reply failed: {first.stdout} {first.stderr}")
        finished = json.loads(first.stdout)["data"]
        second = subprocess.run(context["finish_reply"], input=result_body, text=True,
                                capture_output=True, env=env, timeout=30)
        require(second.returncode == 0 and json.loads(second.stdout)["data"] == finished,
                "same finish/reply intent did not replay")
        require(finished["completion_committed"] and finished["reply_committed"]
                and finished["result"] is not None,
                "finish/reply journal did not confirm settlement")
        journal = json.loads(journal_path.read_text())
        require(journal["completion"]["request_id"] == context["request_ids"]["completion"]
                and journal["response"]["request_id"] == context["request_ids"]["response"]
                and journal["completion"]["status"] == "committed"
                and journal["response"]["status"] == "committed",
                "finish/reply identities changed across synthetic resume")
        result_ref = finished["result"]
        result_history = profile("worker", "history", result_ref, session=worker)
        require(len(result_history["versions"]) == 1
                and result_history["versions"][0]["body"] == result_body
                and result_history["versions"][0]["observed_writer"] == worker["inbox"],
                "settled result is not the exact Store-owned synthetic body")
        response_id = sql("SELECT event_id FROM cairn.agent_event WHERE kind='response'")
        response = profile("worker", "event-inspect", dict(event_id=response_id), session=worker)
        require(response["event"]["ref"] == result_ref
                and response["event"]["causation_id"] == event["event_id"]
                and len(response["deliveries"]) == 1,
                "reply did not retain exact result and request causation")
        require(sql("SELECT count(*) FROM cairn.agent_session_attempt") == "1"
                and sql("SELECT count(*) FROM cairn.memory_record") == "5"
                and sql("SELECT count(*) FROM cairn.agent_event") == "2"
                and sql("SELECT count(*) FROM cairn.agent_delivery") == "2",
                "settlement replay duplicated claim or reply")
        print(json.dumps(dict(schema="cairn.disposable-resume-probe/1",
                              initial_required_id=original_rule["record_id"],
                              current_required_id=current_rule["record_id"],
                              checkpoint_version=resume_view["version"],
                              attempt_id=attempt_id,
                              callbacks="synthetic_precompact_compact_resume",
                              reply_pending_survived_resume=True,
                              actor_task="unrun", provider_called=False,
                              model_retention="unobserved", task_outcome="unassessed"),
                         sort_keys=True))
    finally:
        stop(owner)
        stop(server)


if __name__ == "__main__":
    require(len(sys.argv) == 2, "usage: check_inbox_resume_disposable.py BINARY")
    check(Path(sys.argv[1]).resolve())
