"""Closed disposable fixtures. No command, task prompt, hook or provider entry."""
import json
import os
import sys
import time

mode, run_id = sys.argv[1:]
if mode == 'never_read':
    time.sleep(60)
if mode == 'early_close':
    os.close(0)
else:
    sys.stdin.buffer.read()
if mode == 'descendant':
    if os.fork() == 0:
        os.setsid()
        os.close(1)
        os.close(2)
        time.sleep(60)
        os._exit(0)
if mode == 'memory':
    allocations = []
    while True:
        allocations.append(bytearray(8*1024*1024))
if mode == 'pids':
    # Kernel refusal, with real processes. Namespace init later kills survivors.
    try:
        while True:
            if os.fork() == 0:
                time.sleep(60)
                os._exit(0)
    except OSError:
        sys.exit(19)
if mode == 'privacy':
    assert not os.path.exists('/retention')
    assert not os.path.exists('/sources/retention.py')
    assert not os.path.exists('/sources/selected_native')
    assert not os.path.exists('/oracle')
    assert not os.path.exists('/home/halbritt/git/cairn')
    assert not os.path.exists('/sys/fs/cgroup')
    assert not os.path.exists('/proc/1/root/retention')
    assert not os.environ.get('ANTHROPIC_API_KEY')
    assert not os.environ.get('OPENAI_API_KEY')
    # No inherited sink/controller descriptors; child close_fds is required.
    assert set(os.listdir('/proc/self/fd')) <= {'0', '1', '2', '3'}
    open('/workspace/public-fixture.txt', 'x').write('fixture work product\n')
tools = ['mcp__cairn__cairn_search', 'mcp__cairn__cairn_pull']
frames = [dict(type='system', subtype='init', session_id=run_id,
    model='fixture-model', claude_code_version='fixture-cli', permissionMode='dontAsk',
    tools=tools, mcp_servers=[dict(name='cairn', status='connected', source='dynamic')]),
    dict(type='assistant', session_id=run_id, message=dict(content='PRIVATE_TRANSCRIPT_SENTINEL')),
    dict(type='result', subtype='success', is_error=False, session_id=run_id,
    result='{"fixture":true}', modelUsage={'fixture-model':dict(provider='firstParty',
    inputTokens=0, outputTokens=0, cacheReadInputTokens=0, cacheCreationInputTokens=0)},
    total_cost_usd=0, num_turns=0)]
for frame in frames:
    print(json.dumps(frame), flush=True)
sys.exit(17 if mode == 'nonzero' else 0)
