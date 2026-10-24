"""Model-free native Claude Code search/pull preflight for CAIRN-223.

Run inside scripts/trial-task-eval.sh with a built Cairn binary. Claude's
Messages endpoint is a bounded loopback script; it cannot reach a provider.
"""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import uuid

from check_claude_tools import response_events, value
from check_source_fit_panel import PANEL, validate
from run_source_fit import mcp_file, native_command, parse_stream
from trial_task_eval import TrialStore, TRIAL_REPO


def check(binary, claude):
    panel = json.loads(PANEL.read_text())
    manifest = validate(panel)
    marker = 'toolrecovery' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='source-fit-tool-preflight-') as temporary:
        root = Path(temporary)
        store = TrialStore(root / 'store', binary, 'source_fit_tool_preflight')
        try:
            store.seed([dict(id='whole', kind='lesson', body=marker + ': eligible whole source', shareable=True),
                        dict(id='private', kind='lesson', body=marker + ': inaccessible local source', shareable=False)],
                       workers=1)
            store.start()
            config = mcp_file(root, dict(id='tool_recovery'), 'current', store)
            server_config = json.loads(config.read_text())['mcpServers']['cairn']
            assert server_config['type'] == 'stdio'
            assert Path(server_config['command']).resolve() == store.binary
            assert server_config['args'][-6:] == [
                '--repo', TRIAL_REPO, '--task', 'source-fit/tool_recovery', '--run', 'tool_recovery/current']
            command = native_command(config, panel['provider_plan'])
            command[0] = str(Path(claude).resolve())
            # The same print/MCP/permission flags as the frozen runner are
            # exercised below. Only the provider transport is substituted.
            assert command[command.index('--allowedTools') + 1] == (
                'mcp__cairn__cairn_search,mcp__cairn__cairn_pull')
            for flag, expected in (('--permission-mode', 'dontAsk'), ('--permission-prompts', 'none'),
                                   ('--tools', ''), ('--strict-mcp-config', '--mcp-config')):
                assert command[command.index(flag) + 1] == expected
            requests, results, failures = [], {}, []

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_POST(self):
                    try:
                        size = int(self.headers.get('Content-Length', '0'))
                        if self.path.split('?', 1)[0] != '/v1/messages' or size < 1 or size > 1024 * 1024 or len(requests) >= 4:
                            raise ValueError('unexpected local request or bound')
                        request = json.loads(self.rfile.read(size))
                        requests.append(request)
                        for message in request['messages']:
                            for part in message.get('content', []) if isinstance(message.get('content'), list) else []:
                                if isinstance(part, dict) and part.get('type') == 'tool_result':
                                    results[part['tool_use_id']] = part
                        if 'search' not in results:
                            block = dict(type='tool_use', id='search', name='mcp__cairn__cairn_search',
                                         input=dict(query=marker))
                        elif 'pull' not in results:
                            source = value(results, 'search')['index']
                            entry = next(item for item in source if item['record_id'] == store.ids['whole'])
                            block = dict(type='tool_use', id='pull', name='mcp__cairn__cairn_pull',
                                         input=entry['pull_arguments'])
                        else:
                            block = dict(type='text', text='Scripted local tool recovery complete.')
                        encoded = response_events('msg_fixture_' + str(len(requests)), request['model'], block)
                    except (KeyError, TypeError, ValueError, StopIteration, AssertionError) as error:
                        failures.append(type(error).__name__)
                        self.send_error(500)
                        return
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Content-Length', str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)

            local = HTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=local.serve_forever, daemon=True)
            work = root / 'workspace'
            work.mkdir()
            home = root / 'home'
            home.mkdir(mode=0o700)
            claude_config = root / 'claude-config'
            claude_config.mkdir(mode=0o700)
            env = {key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}
            env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(claude_config),
                       ANTHROPIC_BASE_URL='http://127.0.0.1:' + str(local.server_port),
                       ANTHROPIC_API_KEY='fixture-only', CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1',
                       HTTP_PROXY='http://127.0.0.1:1', HTTPS_PROXY='http://127.0.0.1:1',
                       ALL_PROXY='http://127.0.0.1:1', NO_PROXY='127.0.0.1,localhost')
            thread.start()
            try:
                result = subprocess.run(command, input='Use the disposable Cairn tools to inspect the exact source.',
                                        cwd=work, env=env, text=True, capture_output=True, timeout=45)
            finally:
                local.shutdown()
                local.server_close()
                thread.join(timeout=5)
            assert result.returncode == 0, ('native_local_nonzero_exit', result.returncode,
                                            failures, len(requests))
            assert not failures and len(requests) == 3, (failures, len(requests))
            offered = {tool['name'] for tool in requests[0]['tools']}
            assert {'mcp__cairn__cairn_search', 'mcp__cairn__cairn_pull'} <= offered, offered
            assert not results['search'].get('is_error') and not results['pull'].get('is_error'), results
            search = value(results, 'search')
            assert [entry['record_id'] for entry in search['index']] == [store.ids['whole']], search['index']
            pulled = value(results, 'pull')['selection']['record']
            assert pulled['record_id'] == store.ids['whole'] and pulled['body'] == marker + ': eligible whole source'
            assert store.ids['private'] not in json.dumps(results)
            called = parse_stream(result.stdout)['tool_calls']
            assert called == ['mcp__cairn__cairn_search', 'mcp__cairn__cairn_pull'], called
            return dict(schema='cairn.source-fit-tool-preflight/1',
                        panel_sha256=manifest['panel_sha256'],
                        claude_version=subprocess.check_output([claude, '--version'], text=True, timeout=10).strip(),
                        request_count=len(requests), offered=sorted(offered & {
                            'mcp__cairn__cairn_search', 'mcp__cairn__cairn_pull'}),
                        called=called, whole_source=True, local_only_excluded=True,
                        model_inference=False, provider_endpoint='loopback_script',
                        qualification='local scripted tool execution; subscription permission and task outcome unverified')
        finally:
            store.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    parser.add_argument('--claude', default=shutil.which('claude'))
    args = parser.parse_args()
    if not args.claude:
        raise SystemExit('Claude Code CLI is required for native tool preflight')
    print(json.dumps(check(args.binary, args.claude), sort_keys=True))


if __name__ == '__main__':
    main()
