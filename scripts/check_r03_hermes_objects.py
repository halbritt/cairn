#!/usr/bin/env python3
"""Exercise installed Cairn through actual Hermes provider/registration objects.

Only accepts the disposable marker emitted by check_r03_hermes_installed.py.
No agent conversation, model, messaging transport or production service starts.
"""
import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-root', type=Path, required=True)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--refusal', action='store_true')
    args = parser.parse_args()
    marker = json.loads((args.home / 'r03-fixture.json').read_bytes())
    assert marker['schema'] == 'cairn.r03-hermes-fixture/1'
    fixture = Path(marker['root']).resolve()
    assert args.home.resolve().is_relative_to(fixture)
    config = json.loads((args.home / 'cairn-lifecycle.json').read_bytes())
    assert Path(config['engine_config']).resolve().is_relative_to(fixture)
    args.output.mkdir(mode=0o700)
    sys.path.insert(0, str(args.hermes_root.resolve()))
    # These are required imports. Missing actual Hermes/dependencies is a
    # failing labelled prerequisite, never a mock or unittest skip.
    from agent.memory_provider import MemoryProvider
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from plugins.memory import load_memory_provider
    from hermes_cli.plugins import PluginRegistration, get_plugin_manager
    from hermes_cli.lifecycle import invoke_hook
    from hermes_cli.middleware import apply_llm_request_middleware

    scope = set_hermes_home_override(args.home)
    warnings, calls = [], []
    provider = None
    try:
        # Native delivery lazily discovers the controls plugin on first hook.
        # Complete that real host startup before taking an unrelated-callback
        # baseline; otherwise startup is misreported as a provider leak.
        manager = get_plugin_manager()
        manager.discover_and_load()
        provider = load_memory_provider('cairn', register_skills=False)
        assert isinstance(provider, MemoryProvider), 'actual installed provider failed to load'
        runtime = sys.modules[type(provider).__module__]
        original = runtime.run_engine

        def registrations():
            return [{key: tuple(values) for key, values in registry.items() if values}
                    for registry in (manager._hooks, manager._middleware)]

        baseline_registrations = registrations()

        def observed(command, payload, timeout):
            row = dict(argv=command, payload=payload, timeout=timeout, started=time.monotonic())
            calls.append(row)
            try:
                result = original(command, payload, timeout)
                row.update(exit=result.returncode, stdout=result.stdout, stderr=result.stderr)
                return result
            except BaseException as error:
                row['error_class'] = type(error).__name__
                raise
            finally:
                row['seconds'] = time.monotonic() - row['started']

        runtime.run_engine = observed
        session = marker['session']
        messages = [dict(role='user', content='PRIVATE_FIXTURE: retain thirty days.'),
                    dict(role='assistant', content='Retain the policy and unfinished step.')]
        provider.initialize(session, hermes_home=str(args.home), platform='slack',
                            gateway_session_key='r03-fixture-thread', warning_callback=warnings.append)
        handles = list(provider.registrations)
        assert len(handles) == 5 and all(isinstance(h, PluginRegistration) and h.active for h in handles)
        owner = dict(session_id=session, turn_id='r03-turn-1', user_message='Continue retention policy.',
                     conversation_history=messages)
        assert invoke_hook('pre_llm_call', **owner) == []
        assert invoke_hook('pre_llm_call', **owner) == []
        assert len(calls) == 1, 'same owner turn was recalled more than once'
        original_request = dict(messages=[dict(role='user', content='Original owner prompt')])
        result = apply_llm_request_middleware(original_request, session_id=session)
        assert original_request == dict(messages=[dict(role='user', content='Original owner prompt')])
        if args.refusal:
            assert not result.changed and not provider.context
        else:
            assert result.changed and len(result.payload['messages']) == 2, 'effectful recall was not injected'
            assert len(provider.context.encode()) <= 12000
        before = len(calls)
        invoke_hook('pre_llm_call', **dict(owner, session_id='other-session', turn_id='other-turn'))
        assert len(calls) == before
        assert not apply_llm_request_middleware(original_request, session_id='other-session').changed
        other_scope = set_hermes_home_override(fixture / 'unselected-profile')
        try:
            assert provider.request(original_request, session_id=session) is None
        finally:
            reset_hermes_home_override(other_scope)
        assert invoke_hook('post_llm_call', session_id=session, conversation_history=messages) == []
        record = runtime.read_control(args.home, provider.control_key)
        if args.refusal:
            assert provider.capture_pending and record['last_capture']['outcome'] == 'failed'
            assert record['last_recall']['outcome'] == 'failed'
            assert len(warnings) == 2
        else:
            assert not provider.capture_pending and record['last_capture']['outcome'] == 'saved'
            assert provider.on_pre_compress(messages) == ''
            assert provider.on_session_end(messages) is None
            # Both real hook objects reach the selected Go process. At the
            # assigned base its checked entry explicitly refuses these rows;
            # retain that gap rather than claiming all-operation acceptance.
            invoke_hook('post_tool_call', session_id=session, tool_name='read_file',
                        args={'path': str(fixture / 'policy.txt')}, status='success')
            invoke_hook('post_tool_call', session_id=session, tool_name='terminal', status='error', error_type='fixture')
            tool_calls = calls[-2:]
            assert [c['payload']['hook_event_name'] for c in tool_calls] == ['PostToolUse', 'PostToolUseFailure']
            assert all(c['exit'] == 1 and 'CompatibilityPreflightError' in c['stderr'] for c in tool_calls)
            assert len(warnings) == 2
        assert all('PRIVATE_FIXTURE' not in value for value in warnings)
        before = len(calls)
        marker_path = Path(marker['project']) / '.cairn-no-memory'
        marker_path.touch()
        invoke_hook('pre_llm_call', **dict(owner, turn_id='disabled-turn'))
        assert len(calls) == before and not provider.last_dialogue and not provider.capture_pending
        marker_path.unlink()
        provider.shutdown()
        assert all(not handle.active for handle in handles)
        assert registrations() == baseline_registrations, 'shutdown changed unrelated callbacks or retained this provider'
        replacement = load_memory_provider('cairn', register_skills=False)
        replacement.initialize(session, hermes_home=str(args.home), platform='slack', gateway_session_key='r03-fixture-thread')
        assert len(replacement.registrations) == 5
        replacement.shutdown()
        assert registrations() == baseline_registrations
        report = dict(schema='cairn.r03-hermes-objects/1', refusal=args.refusal,
                      python=sys.version, executable=sys.executable, prefix=sys.prefix, base_prefix=sys.base_prefix,
                      dependencies={item.metadata['Name']: item.version for item in importlib.metadata.distributions()},
                      module_paths={key: value.__file__ for key, value in sys.modules.items()
                                    if getattr(value, '__file__', None)},
                      provider_module=runtime.__file__, calls=len(calls), warnings=warnings,
                      checked_tool_rows='refused at assigned base; Foundation seam remains open',
                      cancellation='no public Store cancellation attestation',
                      refresh='provider disposal/recreation only; no CLI/gateway process qualification')
        (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(dict(passed=True, calls=len(calls), refusal=args.refusal)))
    finally:
        if provider is not None:
            provider.shutdown()
        reset_hermes_home_override(scope)
        (args.output / 'calls.json').write_text(json.dumps(calls, indent=2) + '\n')


if __name__ == '__main__':
    try:
        main()
    except ImportError as error:
        sys.stderr.write('R03_HERMES_PREREQUISITE: ' + str(error) + '\n')
        raise SystemExit(1)
