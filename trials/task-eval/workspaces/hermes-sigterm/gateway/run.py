"""Hermes gateway entry point (excerpt)."""
import logging
import signal
import sys

log = logging.getLogger("gateway")
restart_requested = False


def on_sigterm(signum, frame):
    if restart_requested:
        sys.exit(0)
    log.warning("Exiting with code 1 (signal-initiated shutdown without restart request)")
    sys.exit(1)


signal.signal(signal.SIGTERM, on_sigterm)
