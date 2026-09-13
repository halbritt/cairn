"""Small per-conversation control records; no dialogue or credentials."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile


def conversation_key(platform, gateway_key=''):
    if platform == 'cli':
        return 'cli/' + str(os.getpid())
    if not gateway_key:
        raise ValueError('Cairn conversation identity is unavailable')
    return 'gateway/' + gateway_key


def control_path(home, key):
    return Path(home) / 'cairn/conversations' / (hashlib.sha256(key.encode()).hexdigest() + '.json')


def read_control(home, key):
    path = control_path(home, key)
    if not path.exists():
        return {}
    record = json.loads(path.read_text())
    if not isinstance(record, dict):
        raise ValueError('Invalid Cairn conversation control record')
    return record


@contextmanager
def change_control(home, key):
    path = control_path(home, key)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = read_control(home, key)
        yield record
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, encoding='utf-8', delete=False) as out:
            temporary = Path(out.name)
            try:
                json.dump(record, out, ensure_ascii=False)
                out.write('\n')
                out.close()
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)


def describe_context(record):
    binding = record.get('binding')
    if not binding:
        return 'Cairn context: automatic project from the working directory.'
    return 'Cairn project: ' + binding['project_path'] + '\nWorkstream: ' + (binding.get('workstream') or 'automatic')


def set_context(home, key, arguments):
    if not arguments:
        return describe_context(read_control(home, key))
    binding = None
    if arguments != ['clear']:
        project = Path(arguments[0]).expanduser().resolve()
        if not project.is_dir():
            return 'Cairn project must be an existing directory.'
        topic = ' '.join(arguments[1:]).strip()
        if len(topic.encode()) > 90 or any(c in topic for c in '\r\n"'):
            return 'Cairn workstream must be one line under 90 UTF-8 bytes, without double quotes.'
        binding = dict(project_path=str(project), workstream=topic)
    with change_control(home, key) as record:
        if record.get('pending'):
            return 'Cairn has an unconfirmed capture. Retry it before changing context.'
        if record.get('binding') != binding:
            record.update(binding=binding, turn_capture=True)
    return describe_context(record)
