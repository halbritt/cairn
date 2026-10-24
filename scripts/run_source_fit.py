"""Bounded CAIRN-223 native subscription trial on a disposable Cairn store.

Run only after the frozen panel and preflight are reviewed. Retains selected
terminal answers/usage and sanitized tool-result metadata outside the repository;
never saves raw native streams or tool-result content.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from check_source_fit_panel import source_body
from preflight_source_fit import run_preflight
from trial_task_eval import TRIAL_REPO


ROOT = Path(__file__).resolve().parents[1]
REMOVE_ENV = {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
              'OPENROUTER_API_KEY', 'CLAUDE_CODE_USE_BEDROCK',
              'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY'}
SYSTEM = ("Synthetic Cairn source-fit evaluation. Answer only from delivered eligible "
          "source text and the disposable Cairn tools, when available. Collection scope, "
          "similarity and a partial span do not prove project fit or complete source. "
          "Use current corrections and every required conflicting source. If needed "
          "evidence is unavailable or conflicting, say that plainly. Keep the answer "
          "under 1200 output tokens. Do not use sources outside this trial.")
MIN_NEXT_RUN_RESERVE = 32000


def next_run_reserve(rows):
    """Reserve native overhead before admission; unknown usage stops elsewhere.

    32k exceeds the largest observed first-run charge in the frozen pilot. A
    larger observed run raises the reserve by 2k. This is an admission guard,
    not a provider-enforced token maximum for an individual run.
    """
    observed = [row['tokens'] for row in rows if type(row.get('tokens')) is int]
    return max(MIN_NEXT_RUN_RESERVE, max(observed, default=0) + 2000)


def can_admit_next(total_tokens, rows, plan):
    return (len(rows) < plan['maximum_runs']
            and total_tokens + next_run_reserve(rows)
            <= plan['maximum_total_reported_tokens'])


def provider_env():
    return {key: value for key, value in os.environ.items() if key not in REMOVE_ENV
            and not key.startswith('AWS_') and not key.startswith('GOOGLE_APPLICATION_')}


def subscription_status(env):
    result = subprocess.run(['claude', 'auth', 'status', '--json'], env=env,
                            capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError('subscription_auth_status_failed')
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError('subscription_auth_status_malformed') from error
    if (status.get('loggedIn') is not True or status.get('authMethod') != 'claude.ai'
            or not status.get('subscriptionType')):
        raise RuntimeError('subscription_route_unavailable')
    return dict(auth_method=status['authMethod'], subscription_type=status['subscriptionType'])


def direct_context(panel, case):
    chunks = []
    for key in case['source_order']:
        record = panel['records'][key]
        if record['eligibility'] == 'local_only_inaccessible_to_hosted':
            continue
        body = source_body(record)
        chunks.append(json.dumps(dict(source=key, project=record['project'],
                                      eligibility=record['eligibility'], source_extent='whole',
                                      sha256=hashlib.sha256(body.encode()).hexdigest())) + '\n' + body)
    return '\n---\n'.join(chunks)


def parse_stream(stdout):
    terminal = None
    models = set()
    tool_calls = []
    tool_names = {}
    tool_results = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get('type') == 'assistant':
            message = event.get('message')
            model = message.get('model') if isinstance(message, dict) else None
            if isinstance(model, str) and model:
                models.add(model)
            content = message.get('content') if isinstance(message, dict) else None
            for part in content if isinstance(content, list) else []:
                if isinstance(part, dict) and part.get('type') == 'tool_use':
                    name = part.get('name')
                    tool_calls.append(name)
                    ident = part.get('id')
                    if isinstance(ident, str) and isinstance(name, str):
                        tool_names[ident] = name
        if event.get('type') == 'user':
            message = event.get('message')
            content = message.get('content') if isinstance(message, dict) else None
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict) or part.get('type') != 'tool_result':
                    continue
                ident = part.get('tool_use_id')
                name = tool_names.get(ident) if isinstance(ident, str) else None
                if not isinstance(name, str) or not name.startswith('mcp__cairn__'):
                    continue
                # Keep enough to distinguish a missing, failed, or successful
                # result and identify its exact bytes, without retaining note
                # text or the native model stream in the trial ledger.
                raw_content = part.get('content')
                if isinstance(raw_content, str):
                    encoded_content = raw_content.encode('utf-8')
                else:
                    encoded_content = json.dumps(
                        raw_content, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':')).encode('utf-8')
                tool_results.append(dict(
                    tool=name,
                    tool_use_id=ident,
                    is_error=part.get('is_error') if type(part.get('is_error')) is bool else None,
                    content_bytes=len(encoded_content),
                    content_sha256=hashlib.sha256(encoded_content).hexdigest()))
        if event.get('type') == 'result':
            terminal = event
    if terminal is None:
        raise RuntimeError('native_terminal_missing')
    if len(models) != 1:
        raise RuntimeError('resolved_model_missing_or_multiple')
    usage = terminal.get('usage')
    required = ('input_tokens', 'cache_read_input_tokens',
                'cache_creation_input_tokens', 'output_tokens')
    if not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0
                                          for key in required):
        raise RuntimeError('native_usage_missing')
    if terminal.get('is_error'):
        raise RuntimeError('native_result_error')
    answer = terminal.get('result')
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError('native_answer_missing')
    return dict(model=next(iter(models)), usage={key: usage[key] for key in required},
                tokens=sum(usage[key] for key in required), answer=answer,
                tool_calls=tool_calls, tool_results=tool_results,
                turns=terminal.get('num_turns'), duration_ms=terminal.get('duration_ms'),
                estimated_api_cost_usd=terminal.get('total_cost_usd'))


def native_command(mcp_config, plan):
    return ['claude', '-p', '--model', plan['model_argument'], '--effort', plan['effort_argument'],
               '--output-format', 'stream-json', '--verbose', '--restricted',
               '--setting-sources', '', '--strict-mcp-config', '--mcp-config', str(mcp_config),
               '--no-session-persistence', '--disable-slash-commands', '--tools', '',
               '--allowedTools', 'mcp__cairn__cairn_search,mcp__cairn__cairn_pull',
               '--permission-mode', 'dontAsk', '--permission-prompts', 'none',
               '--max-turns', '6', '--append-system-prompt', SYSTEM]


def native_call(prompt, mcp_config, plan, workdir, env):
    command = native_command(mcp_config, plan)
    start = time.monotonic()
    process = subprocess.Popen(command, cwd=workdir, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    try:
        stdout, stderr = process.communicate(prompt.encode(), timeout=plan['maximum_wall_seconds_per_run'])
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise RuntimeError('native_timeout') from None
    elapsed = time.monotonic() - start
    if process.returncode:
        # stderr may contain private transport detail; retain only a class.
        raise RuntimeError('native_nonzero_exit')
    parsed = parse_stream(stdout.decode(errors='replace'))
    parsed['elapsed_seconds'] = round(elapsed, 3)
    parsed['stdout_bytes'] = len(stdout)
    parsed['stderr_bytes'] = len(stderr)
    return parsed


def mcp_file(output, case, arm, store):
    servers = {}
    if arm != 'direct':
        servers['cairn'] = dict(type='stdio', command=str(store.binary), args=[
            'mcp', '--socket', str(store.socket), '--token-file', str(store.token_file),
            '--repo', TRIAL_REPO, '--task', 'source-fit/' + case['id'],
            '--run', case['id'] + '/' + arm])
    path = output / ('mcp-' + case['id'] + '-' + arm + '.json')
    path.write_text(json.dumps(dict(mcpServers=servers)))
    path.chmod(0o600)
    return path


def trial(panel, manifest, contexts, stores, preflight, output):
    plan = panel['provider_plan']
    env = provider_env()
    try:
        route = subscription_status(env)
    except RuntimeError as error:
        save(output, manifest, None, None, [], 0, str(error))
        return
    started = time.monotonic()
    rows = []
    resolved_model = None
    total_tokens = 0
    for case in panel['cases']:
        for arm in case['arms']:
            if not can_admit_next(total_tokens, rows, plan):
                save(output, manifest, route, resolved_model, rows, total_tokens,
                     'next_run_reserve_exhausted')
                return
            if time.monotonic() - started >= plan['maximum_campaign_wall_seconds']:
                raise RuntimeError('campaign_wall_ceiling_exhausted')
            if total_tokens >= plan['maximum_total_reported_tokens']:
                raise RuntimeError('campaign_token_ceiling_exhausted')
            try:
                subscription_status(env)
            except RuntimeError as error:
                save(output, manifest, route, resolved_model, rows, total_tokens, str(error))
                return
            store = stores['conflict' if case['id'] == 'required_conflict' else
                           'author_split' if arm == 'author_split' else 'current']
            context = (direct_context(panel, case) if arm == 'direct'
                       else contexts[(case['id'], arm)])
            prompt = ('Task: ' + case['task'] +
                      ('\n' + case['hint_suffix'] if arm == 'hints' else '') +
                      '\n\nDelivered source/context:\n' + (context or '[none]') +
                      '\n\nGive a concise answer. State when the source is incomplete or unavailable.')
            config = mcp_file(output, case, arm, store)
            row = dict(case=case['id'], arm=arm, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                       context_bytes=len(context.encode()), status='unscored')
            try:
                result = native_call(prompt, config, plan, output, env)
                row.update(result)
                total_tokens += result['tokens']
                if resolved_model is None:
                    resolved_model = result['model']
                if result['model'] != resolved_model:
                    raise RuntimeError('resolved_model_changed')
                if any(not isinstance(name, str) or not name.startswith('mcp__cairn__')
                       for name in result['tool_calls']) or (arm == 'direct' and result['tool_calls']):
                    raise RuntimeError('unexpected_tool_call')
                if result['usage']['output_tokens'] > plan['maximum_output_tokens_per_run']:
                    raise RuntimeError('output_token_ceiling_exceeded')
                if total_tokens > plan['maximum_total_reported_tokens']:
                    raise RuntimeError('campaign_token_ceiling_exceeded')
                row.update(status='completed',
                           required_literal_screen={phrase: phrase.casefold() in result['answer'].casefold()
                                                    for phrase in case['required_answer']},
                           human_task_review='pending')
            except RuntimeError as error:
                row.update(status='failed', failure=str(error), human_task_review='unscored')
                rows.append(row)
                save(output, manifest, route, resolved_model, rows, total_tokens, 'stopped')
                return
            finally:
                config.unlink(missing_ok=True)
            rows.append(row)
            save(output, manifest, route, resolved_model, rows, total_tokens, 'running')
    save(output, manifest, route, resolved_model, rows, total_tokens, 'completed')


def save(output, manifest, route, model, rows, tokens, status):
    data = dict(schema='cairn.source-fit-trial/1', panel_sha256=manifest['panel_sha256'],
                route=route, resolved_model=model, reported_tokens=tokens, status=status,
                qualification='one replicate; synthetic task answers need independent review', runs=rows)
    staged = output / 'result.staging.json'
    staged.write_text(json.dumps(data, indent=2, sort_keys=True))
    staged.chmod(0o600)
    staged.replace(output / 'result.json')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    parser.add_argument('--expected-panel-sha256', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output == ROOT or ROOT in output.parents:
        raise SystemExit('trial output must be outside the repository')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)

    def admitted(panel, manifest, contexts, stores, preflight):
        if manifest['panel_sha256'] != args.expected_panel_sha256:
            raise RuntimeError('frozen_panel_hash_changed')
        trial(panel, manifest, contexts, stores, preflight, output)

    run_preflight(args.binary, admitted)


if __name__ == '__main__':
    main()
