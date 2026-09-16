"""Exercise native hook/process association through the real disposable Unix API."""
import json
import importlib.util
import shlex
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
import uuid


OWNER = r'''
import json, subprocess, sys
script, config = sys.argv[1:]
for line in sys.stdin:
    result = subprocess.run([sys.executable, script, 'hook', '--config', config],
        input=line, text=True, capture_output=True, timeout=15)
    print(json.dumps(dict(code=result.returncode, stdout=result.stdout, stderr=result.stderr)), flush=True)
'''


def check(binary, root, repo, api_call):
    assert os.environ.get("CAIRN_TEST_DATABASE_URL")
    script = Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py"
    configs = root / "native-bindings"
    configs.mkdir(mode=0o700)
    config = configs / "codex-probe.json"
    state = root / "native-state"
    config.write_text(json.dumps(dict(cairn=str(Path(binary).resolve()), socket=str(root / "api.sock"),
        token_file=str(root / "bob.token"), repo=repo, harness="codex", binding="codex-probe",
        model="configured", process_names=["python3"], state_dir=str(state))))
    config.chmod(0o600)
    processes = []

    def owner():
        p = subprocess.Popen([sys.executable, "-u", "-c", OWNER, str(script), str(config)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        processes.append(p)
        return p

    def hook(p, event, code=0):
        p.stdin.write(json.dumps(event) + "\n")
        p.stdin.flush()
        ready, _, _ = select.select([p.stdout], [], [], 20)
        assert ready, "native hook owner did not respond"
        result = json.loads(p.stdout.readline())
        assert result["code"] == code, result
        return json.loads(result["stdout"]) if code == 0 else result

    def watch():
        subprocess.run([sys.executable, str(script), "watch", "--config-dir", str(configs), "--once"],
                       capture_output=True, text=True, timeout=15, check=True)

    def entry(ident, offline=False):
        args = ["list", "--agent-id", ident]
        if offline:
            args.append("--include-offline")
        return api_call("bob", "agents", *args)["agents"]

    event = dict(session_id="native-probe", cwd=str(root), hook_event_name="UserPromptSubmit",
                 model="observed-one", prompt="PRIVATE PROMPT MUST NOT BE RETAINED")
    try:
        first = owner()
        response = hook(first, event)
        agents = api_call("bob", "agents", "list", "--harness", "codex")["agents"]
        agent = next(a for a in agents if a["native_session_id"] == "native-probe")
        assert agent["agent_id"] in response["hookSpecificOutput"]["additionalContext"]
        assert agent["metadata"]["observed_model"] == "observed-one"
        metadata = dict(agent["metadata"], task_summary="Selected chart editor task")
        api_call("bob", "agent-context", raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()),
            session={k: agent[k] for k in ("agent_id", "execution_id")},
            expected_revision=agent["context_revision"], metadata=metadata)))
        # A concurrent second process must not take the same native conversation.
        second = owner()
        refused = hook(second, event, code=1)
        assert "SESSION_BUSY" in refused["stderr"]
        first.terminate()
        first.communicate(timeout=5)
        watch()
        assert not entry(agent["agent_id"])
        assert entry(agent["agent_id"], True)[0]["stopped"]
        # Resume retains the UUID and task metadata even though its old host died.
        event["model"] = "observed-two"
        hook(second, event)
        resumed = entry(agent["agent_id"])[0]
        assert resumed["execution_id"] != agent["execution_id"]
        assert resumed["metadata"]["task_summary"] == "Selected chart editor task"
        assert resumed["metadata"]["observed_model"] == "observed-two"
        revision = resumed["context_revision"]
        watch()
        assert entry(agent["agent_id"])[0]["context_revision"] == revision
        # A separate conversation in the same process gets its own UUID.
        hook(second, dict(event, session_id="native-fork"))
        entries = api_call("bob", "agents", "list", "--harness", "codex")["agents"]
        fork = next(a for a in entries if a["native_session_id"] == "native-fork")
        assert fork["agent_id"] != resumed["agent_id"]
        replacement = api_call("bob", "agent-register", raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()),
            repo=repo, binding="codex-probe", native_session_id="native-fork", metadata=fork['metadata'])))
        watch()
        assert entry(fork['agent_id'])[0]['execution_id'] == replacement['execution_id'], 'watcher replaced a newer execution'
        refused = hook(second, dict(event, session_id="native-fork"), code=1)
        assert 'STALE_SESSION' in refused['stderr']
        hook(second, dict(event, session_id="native-fork", hook_event_name="SessionEnd"))
        assert entry(fork['agent_id'])[0]['execution_id'] == replacement['execution_id'], 'old process stopped a newer execution'
        api_call('bob', 'agent-leave', raw=True, body=json.dumps({k: replacement[k] for k in ('agent_id', 'execution_id')}))
        for path in state.glob("*.json"):
            assert "PRIVATE PROMPT" not in path.read_text()
            assert path.stat().st_mode & 0o777 == 0o600
        hook(second, dict(event, hook_event_name="SessionEnd"))
        assert not entry(agent["agent_id"])
        if os.environ.get("CAIRN_HERMES_PYTHON"):
            subprocess.run([os.environ["CAIRN_HERMES_PYTHON"], str(Path(__file__).with_name("check_hermes_coordination.py")),
                str(root / "native-hermes"), os.environ["CAIRN_HERMES_ROOT"], str(config)], check=True, timeout=90)
        if os.environ.get("CAIRN_OPENCODE_BINARY"):
            from check_opencode_coordination import check as check_opencode
            check_opencode(os.environ['CAIRN_OPENCODE_BINARY'], root / 'native-opencode', json.loads(config.read_text()))
        if os.environ.get('CAIRN_AGY_BINARY'):
            # Explicit opt-in: Agy has no configured local fixture provider, so
            # this bounded smoke test uses its existing account and one model turn.
            installer_spec = importlib.util.spec_from_file_location('agy_coordination_installer', Path(__file__).with_name('install-agent-coordination.py'))
            installer = importlib.util.module_from_spec(installer_spec)
            installer_spec.loader.exec_module(installer)
            work = root / 'native-agy'
            work.mkdir()
            subprocess.run(['git', 'init', '--quiet', str(work)], check=True, timeout=5)
            native_config = dict(json.loads(config.read_text()), harness='agy', binding='agy-native', process_names=['agy'])
            installed = installer.install(root / 'native-agy-engine', work / '.agents/hooks.json', native_config)
            model = os.environ['CAIRN_AGY_NATIVE_MODEL']
            env = dict(os.environ)
            for key in ('CAIRN_LIFECYCLE_DISABLED', 'CAIRN_LIFECYCLE_CHILD', 'CAIRN_WAKE_CONTEXT'):
                env.pop(key, None)
            result = subprocess.run([os.environ['CAIRN_AGY_BINARY'], '--new-project', '--model', model,
                '--print-timeout', '30s', '--output-format', 'json', '--print',
                'Native session identity smoke test. Reply only with your Cairn agent UUID from the injected session context. Do not use tools or access files.'],
                cwd=work, env=env, capture_output=True, text=True, timeout=40)
            (work / 'stdout.json').write_text(result.stdout)
            (work / 'stderr.log').write_text(result.stderr)
            assert result.returncode == 0, result.stderr[-1000:]
            output = json.loads(result.stdout)
            records = api_call('bob', 'agents', 'list', '--harness', 'agy', '--include-offline')['agents']
            native = next(a for a in records if a['native_session_id'] == output['conversation_id'])
            assert native['agent_id'] in output['response'], 'Agy did not observe its registered identity'
            assert native['metadata']['observed_model'] == model and native['metadata']['state'] == 'idle'
            installer.engine.watch_once(installer.engine.load_config(installed))
            assert not entry(native['agent_id']), 'Exited Agy remained live'
            print('Installed Agy PreInvocation/Stop supplied native identity/model and ended presence after exit')
        if os.environ.get("CAIRN_CLAUDE_BINARY"):
            installer_spec = importlib.util.spec_from_file_location("claude_coordination_installer", Path(__file__).with_name("install-agent-coordination.py"))
            installer = importlib.util.module_from_spec(installer_spec)
            installer_spec.loader.exec_module(installer)
            native_home = root / "native-claude-home"
            native_home.mkdir()
            settings = native_home / "settings.json"
            native_config = dict(json.loads(config.read_text()), harness="claude", binding="claude-native", process_names=["claude"], config_home=str(native_home))
            installer.install(root / "native-claude-engine", settings, native_config)
            ident = str(uuid.uuid4())
            env = dict(os.environ, HOME=str(native_home), CLAUDE_CONFIG_DIR=str(native_home), ANTHROPIC_API_KEY="fixture-only",
                       ANTHROPIC_BASE_URL="http://127.0.0.1:1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
            for key in ("CLAUDECODE", "CAIRN_LIFECYCLE_DISABLED", "CAIRN_LIFECYCLE_CHILD", "CAIRN_WAKE_CONTEXT"):
                env.pop(key, None)
            with (root / "native-claude.stdout").open("w") as out, (root / "native-claude.stderr").open("w") as err:
                native = subprocess.Popen([os.environ["CAIRN_CLAUDE_BINARY"], "--print", "--verbose", "--output-format", "stream-json",
                    "--input-format", "stream-json", "--setting-sources", "", "--settings", str(settings), "--strict-mcp-config",
                    "--mcp-config", '{"mcpServers":{}}', "--tools", "", "--permission-mode", "dontAsk", "--session-id", ident, "--model", "sonnet"],
                    cwd=root, env=env, stdin=subprocess.PIPE, stdout=out, stderr=err, text=True, start_new_session=True)
                try:
                    native.stdin.write(json.dumps(dict(type="user", message=dict(role="user", content="Reply OK"))) + "\n")
                    native.stdin.close()
                    deadline = time.monotonic() + 15
                    while True:
                        records = api_call("bob", "agents", "list", "--harness", "claude", "--include-offline")["agents"]
                        if any(a["native_session_id"] == ident for a in records):
                            break
                        assert time.monotonic() < deadline and native.poll() is None, "Actual Claude hooks did not register the native conversation"
                        time.sleep(.1)
                finally:
                    try:
                        os.killpg(native.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        native.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(native.pid, signal.SIGKILL)
                        native.wait(timeout=5)
            subprocess.run([sys.executable, str(root / "native-claude-engine/coordination.py"), "watch", "--config-dir", str(root / "native-claude-engine/bindings"), "--once"], check=True, capture_output=True, timeout=15)
            assert not api_call("bob", "agents", "list", "--harness", "claude")["agents"]
            print("Installed Claude hooks registered the actual native conversation with a fixture-only provider")
        if os.environ.get("CAIRN_NODE_BINARY"):
            opencode_config = configs / "opencode-probe.json"
            opencode_config.write_text(json.dumps(dict(json.loads(config.read_text()), harness="opencode", binding="opencode-probe", state_dir=str(root / "opencode-state"))))
            subprocess.run([os.environ["CAIRN_NODE_BINARY"], str(Path(__file__).with_name("check_opencode_coordination.mjs")), str(root / "opencode-plugin"), sys.executable, str(script), str(opencode_config)], check=True, timeout=30)
            records = api_call("bob", "agents", "list", "--harness", "opencode", "--include-offline")["agents"]
            records = [a for a in records if a['native_session_id'] == 'ses_coordination']
            assert len(records) == 1 and records[0]["stopped"] and records[0]["metadata"]["observed_model"] == "fixture/probe"
        if os.environ.get("CAIRN_CODEX_BINARY"):
            installer_spec = importlib.util.spec_from_file_location("coordination_installer", Path(__file__).with_name("install-agent-coordination.py"))
            installer = importlib.util.module_from_spec(installer_spec)
            installer_spec.loader.exec_module(installer)
            native_home = root / "native-codex-home"
            native_home.mkdir()
            (native_home / "config.toml").write_text('model_provider = "fixture"\n[model_providers.fixture]\nname = "Fixture"\nbase_url = "http://127.0.0.1:1/v1"\nwire_api = "responses"\nrequires_openai_auth = false\nrequest_max_retries = 0\nstream_max_retries = 0\n')
            native_config = dict(json.loads(config.read_text()), binding="codex-native", process_names=["codex"], config_home=str(native_home))
            installed = installer.install(root / "native-codex-engine", native_home / "hooks.json", native_config)
            native_command = shlex.join([sys.executable, str(root / "native-codex-engine/coordination.py"), "hook", "--config", str(installed)])
            native_ids = []
            def native_start(rpc):
                started = rpc("thread/start", dict(cwd=str(root), model="gpt-6-astra", approvalPolicy="never", sandbox="read-only", ephemeral=True))
                ident = started["thread"]["id"]
                native_ids.append(ident)
                rpc("turn/start", dict(threadId=ident, input=[dict(type="text", text="Reply OK", text_elements=[])]))
                deadline = time.monotonic() + 8
                while True:
                    entries = api_call("bob", "agents", "list", "--harness", "codex")["agents"]
                    if any(a["native_session_id"] == ident for a in entries):
                        break
                    assert time.monotonic() < deadline, "Actual Codex first-turn hooks did not register its native thread"
                    time.sleep(.1)
            installer.trust_codex_hooks(native_home, native_home / "hooks.json", native_command, verify=native_start, codex_binary=os.environ["CAIRN_CODEX_BINARY"])
            subprocess.run([sys.executable, str(root / "native-codex-engine/coordination.py"), "watch", "--config-dir", str(root / "native-codex-engine/bindings"), "--once"], check=True, capture_output=True, timeout=15)
            remaining = api_call("bob", "agents", "list", "--harness", "codex")["agents"]
            assert not any(a["native_session_id"] in native_ids for a in remaining), "Exited native Codex stayed live"
        print("Native coordination: process ownership, death, resume, fork, model/task context and transcript exclusion passed")
        if os.environ.get("CAIRN_CODEX_BINARY"):
            print("Installed Codex first-turn hooks registered the actual native thread with a fixture-only provider")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)
        watch()
