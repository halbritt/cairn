"""Synthetic stand-ins for the retired Python embedding source pair (test only).

trial_embedding_launch pins the reviewed embedding_worker.py/semantic_rank.py
digests. Those sources left the repository with the Python worker, so tests
write inert placeholder bytes and patch the pin to them. Nothing here runs.
"""
import hashlib
from pathlib import Path
from unittest import mock

import trial_embedding_launch

CONTENT = {
    'worker': ('embedding_worker.py', b'# inert test placeholder for the retired embedding worker\n'),
    'scorer': ('semantic_rank.py', b'# inert test placeholder for the retired scorer\n'),
}


def write_sources(directory):
    """Write both placeholders into directory; return a patcher for SOURCE_PAIR."""
    pair = {}
    for role, (name, data) in CONTENT.items():
        Path(directory, name).write_bytes(data)
        pair[role] = (name, hashlib.sha256(data).hexdigest(), len(data))
    return mock.patch.dict(trial_embedding_launch.SOURCE_PAIR, pair)
