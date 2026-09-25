"""Check generated Cairn configuration with the installed Claude CLI; no inference."""
import json
import os
from pathlib import Path
import re
import subprocess


def check(claude, binary, root):
    work = root / 'claude-config-check'
    work.mkdir(mode=0o700)
    env = {key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}
    env.update(HOME=str(work / 'home'), CLAUDE_CONFIG_DIR=str(work / 'config'),
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
    Path(env['HOME']).mkdir(mode=0o700)
    Path(env['CLAUDE_CONFIG_DIR']).mkdir(mode=0o700)

    def run(args):
        return subprocess.run(args, cwd=work, env=env, capture_output=True,
                              text=True, check=True, timeout=30).stdout

    rendered = run([binary, 'claude-config', '--socket', str(root / 'api.sock'),
                    '--token-file', str(root / 'hosted-agent.token'),
                    '--repo', 'fixture:socket', '--task', 'claude-configuration',
                    '--run', 'connection-check'])
    config = json.loads(rendered)
    (work / 'generated.json').write_text(rendered)
    server = config['mcpServers']['cairn']
    added = run([claude, 'mcp', 'add-json', '--scope', 'local', 'cairn', json.dumps(server)])
    assert 'Added' in added, added
    inspected = run([claude, 'mcp', 'get', 'cairn'])
    assert re.search(r'^\s*Status:\s*(?:✔\s*)?Connected\s*$', inspected, re.MULTILINE), inspected
    assert server['command'] in inspected, inspected
    report = dict(claude_version=run([claude, '--version']).strip(),
                  registered=True, connected=True, scope='explicit task/run',
                  model_inference=False, operational_settings_changed=False)
    (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Claude Code accepts generated stdio configuration and connects in an isolated home: ' + report['claude_version'])
