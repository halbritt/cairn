"""Only launched inside the fixture namespace; performs no operational effect."""
import json
import os
import sys
import time

mode = sys.argv[1]
request = json.loads(sys.stdin.buffer.readline(4097))
if mode == 'delay-before-reply': time.sleep(.12)
outcome = {'schema': 'cairn.remote-effect-outcome/1',
           **{key: request[key] for key in ('run_id', 'effect_id', 'action', 'binding_sha256', 'request_sha256')},
           'outcome': 'APPLIED', 'executor_exit': 0}
print(json.dumps(outcome), flush=True)
if mode == 'eof-before-reap':
    os.close(1); os.close(2); time.sleep(.12)
elif mode == 'delay-after-reply': time.sleep(.12)
elif mode not in ('positive', 'delay-before-reply'): raise SystemExit(3)
