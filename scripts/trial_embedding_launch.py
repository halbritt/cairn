"""Prepare the original weekly embedding endpoint without starting a worker.

Trusted, quiescent host inputs/output parent are required. This verifies selected
source bytes, not concurrent filesystem custody or transitive Python imports.
"""
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import shlex
import stat

from trial_paired_request import Refusal, decode_json, need, pin_shape, plain_path, shape

SCHEMA = 'cairn.weekly-embedding-launch/1'
RENDERER = 'cairn.install-semantic-embedding-launcher/1'
CONFIG_BYTES = 16384
LAUNCHER_BYTES = 16384
PATH_BYTES = 4096
# Reviewed model-dir protocol, not caller declarations of capability. Changing
# this pair requires an explicit source compatibility review/version decision.
SOURCE_PAIR = {
    'worker': ('embedding_worker.py',
               'f8b134f92523eb53ab21f7d4878283f5a6dc401ecb605c7e8d7e2df2aa673ae8', 6493),
    'scorer': ('semantic_rank.py',
               '4f70d19c1442bebe812cf0fab667a214992575d68737b981189783211916de95', 10831),
}


def _path(raw, *, interpreter=False):
    need(type(raw) is str and '\0' not in raw, 'embedding_path')
    try:
        need(0 < len(raw.encode('utf8')) <= PATH_BYTES, 'embedding_path_bound')
    except UnicodeEncodeError:
        raise Refusal('embedding_path') from None
    path = Path(raw)
    need(path.is_absolute() and str(path) == raw and '..' not in path.parts,
         'embedding_path')
    return path if interpreter else plain_path(raw)


def _read(reference, limit, *, executable=False):
    pin_shape(reference)
    path = _path(reference['path'])
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
        mode = os.fstat(source.fileno()).st_mode
        need(stat.S_ISREG(mode), 'embedding_regular_file')
        need(not executable or (mode & 0o111 and os.access(path, os.X_OK)), 'embedding_executable')
        content = source.read(limit + 1)
    need(len(content) <= limit, 'embedding_file_bound')
    need(hashlib.sha256(content).hexdigest() == reference['sha256'], 'embedding_pin_mismatch')
    return content


def _configuration(reference):
    raw = _read(reference, CONFIG_BYTES)
    try:
        config = decode_json(raw.decode('utf8'))
    except UnicodeDecodeError:
        raise Refusal('embedding_config_utf8') from None
    except RecursionError:
        raise Refusal('embedding_config_depth') from None
    shape(config, 'schema python worker scorer model_dir', 'embedding_config_shape')
    need(config['schema'] == SCHEMA, 'embedding_config_schema')
    for role, (name, digest, size) in SOURCE_PAIR.items():
        pin_shape(config[role])
        need(config[role]['sha256'] == digest, 'embedding_source_unsupported')
        path = _path(config[role]['path'])
        need(path.name == name, 'embedding_source_name')
        _read(config[role], size)
    need(Path(config['scorer']['path']).parent == Path(config['worker']['path']).parent,
         'embedding_scorer_location')
    need(_path(config['model_dir']).is_dir(), 'embedding_model_directory')
    return raw, config


def _interpreter(invocation):
    path = _path(invocation, interpreter=True)
    resolved = path.resolve(strict=True)
    with os.fdopen(os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
        mode = os.fstat(source.fileno())
        need(stat.S_ISREG(mode.st_mode) and os.access(path, os.X_OK), 'embedding_interpreter')
    # Record the observation; never replace a lexical venv invocation with its
    # system-python target. This is not an interpreter/package content pin.
    return dict(invocation=invocation, resolved=str(resolved), device=mode.st_dev,
                inode=mode.st_ino, bytes=mode.st_size)


def _render(config):
    return ('#!/bin/sh\nexec ' + shlex.join([config['python'], config['worker']['path'],
                                           '--model-dir', config['model_dir']]) + '\n').encode('utf8')


def _write(path, content, mode, rollback):
    with path.open('xb') as output:
        rollback.callback(path.unlink)
        os.fchmod(output.fileno(), mode)
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def prepare(config_pin, workers, output):
    """Return retained pins/observations for one endpoint shared by both arms.

    workers is exactly {CURRENT: pin, CAND: pin}; output must be a fresh absolute
    directory. All validation precedes writes. I/O errors propagate; failed
    writes remove only this call's created files/directory. Caller owns successful
    retention and later revalidation, execution, identity comparison and cleanup.
    """
    raw, config = _configuration(config_pin)
    python = _interpreter(config['python'])
    rendered = _render(config)
    need(len(rendered) <= LAUNCHER_BYTES, 'embedding_launcher_bound')
    shape(workers, 'CURRENT CAND', 'embedding_workers_shape')
    for reference in workers.values():
        need(_read(reference, LAUNCHER_BYTES, executable=True) == rendered,
             'embedding_launcher_mismatch')
    need(workers['CURRENT'] == workers['CAND'], 'embedding_common_worker')
    output = _path(str(output))
    config_output, launcher = output/'embedding-config.json', output/'worker-embeddings'
    receipt = dict(schema='cairn.weekly-embedding-launch-preparation/1', renderer=RENDERER,
                   selected_config=dict(config_pin), declared_worker=dict(workers['CURRENT']),
                   config=dict(path=str(config_output), sha256=hashlib.sha256(raw).hexdigest()),
                   launcher=dict(path=str(launcher), sha256=hashlib.sha256(rendered).hexdigest()),
                   config_bytes=len(raw), launcher_bytes=len(rendered), python=python,
                   worker=dict(config['worker']), scorer=dict(config['scorer']), model_dir=config['model_dir'])
    with ExitStack() as rollback:
        output.mkdir(mode=0o700)
        rollback.callback(output.rmdir)
        _write(config_output, raw, 0o600, rollback)
        _write(launcher, rendered, 0o700, rollback)
        rollback.pop_all()
    return receipt
