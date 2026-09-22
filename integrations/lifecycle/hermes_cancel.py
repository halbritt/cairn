"""Bounded native control transport. Native ownership is checked by the host."""
import json
import time
import uuid

import hermes_queue


MAX_RESPONSE_BYTES = 65536


class ControlUnavailable(Exception):
    """No native request was sent."""


class ControlUncertain(Exception):
    """A native request may have acted; never replay it from this exception."""


class ControlRefused(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class BridgeClient:
    def __init__(self, endpoint, process, timeout=4):
        self.endpoint = endpoint
        self.process = process
        self.timeout = timeout

    def rpc(self, method, params):
        deadline = time.monotonic() + self.timeout
        try:
            connection = hermes_queue._open(self.endpoint, self.process, timeout=self.timeout)
        except hermes_queue.QueueUnavailable as exc:
            raise ControlUnavailable(str(exc)) from exc
        try:
            request_id = str(uuid.uuid4())
            request = dict(id=request_id, method=method, params=params)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ControlUnavailable('native control connection exceeded its deadline')
            connection.settimeout(remaining)
            connection.sendall((json.dumps(request) + '\n').encode())
            response = bytearray()
            while b'\n' not in response:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ControlUncertain('native control response exceeded its deadline')
                connection.settimeout(remaining)
                chunk = connection.recv(min(4096, MAX_RESPONSE_BYTES + 1 - len(response)))
                if not chunk:
                    raise ControlUncertain('native control closed before its response')
                response.extend(chunk)
                if len(response) > MAX_RESPONSE_BYTES:
                    raise ControlUncertain('native control response exceeds limit')
            line, trailing = response.split(b'\n', 1)
            if trailing.strip():
                raise ControlUncertain('native control returned multiple responses')
            message = json.loads(line)
            if (not isinstance(message, dict) or message.get('id') != request_id
                    or ('error' in message) == ('result' in message)):
                raise ControlUncertain('native control response identity or envelope invalid')
            if 'error' in message:
                error = message['error']
                if (not isinstance(error, dict) or type(error.get('code')) is not int
                        or not isinstance(error.get('message'), str)):
                    raise ControlUncertain('native control refusal is malformed')
                raise ControlRefused(error['code'], error['message'])
            if not isinstance(message['result'], dict):
                raise ControlUncertain('native control result is not an object')
            return message['result']
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise ControlUncertain('native control outcome is unknown') from exc
        finally:
            connection.close()
