"""Active local turn deadline with cancel request and bounded local process stop."""
import threading


class ActiveTurnDeadline:
    def __init__(self, seconds, cancel_turn, stop_server):
        if type(seconds) not in (int, float) or seconds <= 0:
            raise ValueError("turn deadline must be positive")
        self.cancel_turn = cancel_turn
        self.stop_server = stop_server
        self.expired = threading.Event()
        self.cleaned = threading.Event()
        self.cancel_error = None
        self.stop_error = None
        self.timer = threading.Timer(seconds, self._expire)
        self.timer.daemon = True

    def start(self):
        self.timer.start()
        return self

    def _expire(self):
        self.expired.set()
        try:
            self.cancel_turn()
        except Exception as exc:
            self.cancel_error = type(exc).__name__
        try:
            self.stop_server()
        except Exception as exc:
            self.stop_error = type(exc).__name__
        finally:
            self.cleaned.set()

    def disarm(self):
        if not self.expired.is_set():
            self.timer.cancel()

    def wait_cleanup(self, timeout):
        return self.cleaned.wait(timeout)
