"""Bounded custody of one local transport child. Operational launcher is absent.

Only a fixed-action request enters the launcher port. There is no argv, shell,
host, path or operation selector. Tests supply a fixed disposable dummy launcher.
Positive local transport reap says nothing about remote descendants or service join.
"""
import os
import selectors
from effects import Action, ExchangeResult, ExchangeUnknown, MAX_REPLY, MAX_STDERR, canonical
from domains import integer

MAX_REQUEST = 4096


class UnboundLauncher:
    def launch(self, request):
        raise ExchangeUnknown('operational_launcher_UNBOUND')


class ChildTransport:
    def __init__(self, monotonic_ns, launcher=None):
        self.monotonic_ns = monotonic_ns
        self.launcher = launcher if launcher is not None else UnboundLauncher()
        self.used = set()
        self.children = {}  # Retain custody, including timeout/lost outcomes. No retry/reaper policy.

    def exchange(self, request, budget_ns):
        if request.get('action') not in {a.value for a in Action}:
            raise ExchangeUnknown('fixed_action_required')
        key = (request['run_id'], request['effect_id'])
        if key in self.used: raise ExchangeUnknown('session_already_consumed')
        self.used.add(key)
        body = canonical(request) + b'\n'
        try: integer(budget_ns, 'budget_ns')
        except Exception: raise ExchangeUnknown('transport_budget_unknown') from None
        if len(body) > MAX_REQUEST or budget_ns <= 0:
            raise ExchangeUnknown('request_or_wait_unqualified')
        try: started = integer(self.monotonic_ns(), 'monotonic_ns')
        except Exception: raise ExchangeUnknown('transport_clock_unknown') from None
        deadline = started + budget_ns
        child = self.launcher.launch(request)
        self.children[key] = child
        identity = 'local-transport-child:%d' % child.pid  # Local fixture/transport identifier only.
        output = {'stdout': bytearray(), 'stderr': bytearray()}
        selector = selectors.DefaultSelector()
        try:
            for name in ('stdin', 'stdout', 'stderr'):
                stream = getattr(child, name)
                if stream is None: raise ExchangeUnknown('missing_child_pipe', identity)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_WRITE if name == 'stdin' else selectors.EVENT_READ, name)
            sent = 0
            while True:
                remaining = deadline - integer(self.monotonic_ns(), 'monotonic_ns')
                if remaining < 0: raise ExchangeUnknown('outcome_wait_expired', identity)
                code = child.poll()  # A positive local reap still is not remote child reap.
                if not selector.get_map() and code is not None:
                    return ExchangeResult(bytes(output['stdout']), bytes(output['stderr']), code, identity, True)
                if remaining == 0: raise ExchangeUnknown('outcome_wait_expired', identity)
                for key_ready, _ in selector.select(min(remaining / 1_000_000_000, .01)):
                    name = key_ready.data; stream = key_ready.fileobj
                    if name == 'stdin':
                        sent += os.write(stream.fileno(), body[sent:])
                        if sent == len(body):
                            selector.unregister(stream); stream.close()
                    else:
                        data = os.read(stream.fileno(), 4096)
                        if not data:
                            selector.unregister(stream); stream.close()
                        else:
                            output[name].extend(data)
                            cap = MAX_REPLY if name == 'stdout' else MAX_STDERR
                            if len(output[name]) > cap:
                                raise ExchangeUnknown('output_limit', identity)
        except ExchangeUnknown:
            raise
        except Exception:
            raise ExchangeUnknown('transport_io_unknown', identity) from None
        finally:
            # Do not kill, terminate, wait without a bound, relaunch or infer cancellation.
            # Any open pipes/child remain retained in children; future O2 is not selected.
            selector.close()
