#!/usr/bin/env python3
"""Initialize an actual disposable HermesCLI owner without a model request."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-root', type=Path, required=True)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--replacement-config', type=Path)
    args = parser.parse_args()
    marker = json.loads((args.home / 'r03-fixture.json').read_bytes())
    root = Path(marker['root']).resolve()
    assert marker['schema'] == 'cairn.r03-hermes-fixture/1' and args.home.resolve().is_relative_to(root)
    assert os.environ['HERMES_HOME'] == str(args.home)
    active = args.home / 'cairn-lifecycle.json'
    original = active.read_bytes()
    assert hashlib.sha256(original).hexdigest() == args.expected_sha256
    args.output.mkdir(mode=0o700)
    sys.path.insert(0, str(args.hermes_root))
    from cli import HermesCLI, _finalize_single_query
    from agent.memory_provider import MemoryProvider
    from hermes_cli.lifecycle import invoke_hook
    from tools.mcp_tool import shutdown_mcp_servers

    cli = None
    try:
        cli = HermesCLI(model='r03-initialization-only', provider='custom', api_key='fixture-only',
                        base_url='http://127.0.0.1:9/v1', toolsets=['mcp-cairn'], compact=True)
        assert cli._fallback_model == [], 'fixture provider fallback must be absent'
        assert cli._init_agent(), 'actual CLI owner failed initialization'
        provider = cli.agent._memory_manager.get_provider('cairn')
        assert isinstance(provider, MemoryProvider) and provider.config == json.loads(original)
        selected, timeout = provider.engine_command('UserPromptSubmit')
        if args.replacement_config:
            pending = active.with_suffix('.refresh')
            pending.write_bytes(args.replacement_config.read_bytes())
            pending.chmod(0o600)
            pending.replace(active)
            assert provider.config == json.loads(original), 'cached CLI changed selection without refresh'
            assert provider.engine_command('UserPromptSubmit') == (selected, timeout)
        runtime = sys.modules[type(provider).__module__]
        original_run = runtime.run_engine
        calls = []

        def observed(argv, payload, timeout):
            result = original_run(argv, payload, timeout)
            calls.append(dict(argv=argv, payload=payload, timeout=timeout, exit=result.returncode,
                              stdout=result.stdout, stderr=result.stderr))
            return result

        runtime.run_engine = observed
        invoke_hook('pre_llm_call', session_id=cli.session_id, turn_id='r03-cli-init',
                    user_message='Continue retention policy.', conversation_history=[])
        assert len(calls) == 1 and calls[0]['exit'] == 0
        assert calls[0]['argv'] == selected and calls[0]['payload']['session_id'] == cli.session_id
        handles = list(provider.registrations)
        cli.agent.shutdown_memory_provider([])
        assert all(not handle.active for handle in handles)
        report = dict(passed=True, kind='actual HermesCLI initialization and memory hook; conversation API not invoked',
                      session=cli.session_id, selection_sha256=args.expected_sha256, calls=calls,
                      cached_selection_preserved=bool(args.replacement_config), python=sys.version)
        (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(dict(passed=True, session=cli.session_id, conversation_api_invoked=False)))
    finally:
        if cli is not None:
            _finalize_single_query(cli)
        shutdown_mcp_servers()


if __name__ == '__main__':
    try:
        main()
    except ImportError as error:
        sys.stderr.write('R03_HERMES_CLI_PREREQUISITE: ' + str(error) + '\n')
        raise SystemExit(1)
