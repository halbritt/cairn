"""Pinned, size-bounded, owner-only, write-once JSON documents with typed refusals.

One module owns the rules every trial and evaluation script applies to a document
it pins, reads back or writes once: absolute plain paths without indirection,
regular files only, bounded sizes, strict JSON (unique keys, finite numbers),
canonical digests and owner-only write-once creation. ``trial_paired_request``
re-exports the original names, so existing importers and pinned sources keep
working; new callers import this module directly.

Refusals carry a fixed public code and never a path, payload or exception text.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat

FILE_LIMIT = 8 * 1024 * 1024
PIN_LIMIT = 512 * 1024 * 1024
DIGEST = re.compile('[0-9a-f]{64}')


class Refusal(ValueError):
    """Fixed public code; no path, payload or exception text."""


def need(condition, code):
    if not condition:
        raise Refusal(code)


def shape(document, fields, code):
    need(isinstance(document, dict) and set(document) == set(fields.split()), code)


def utc(text):
    need(isinstance(text, str), 'utc_timestamp')
    try:
        parsed = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        raise Refusal('utc_timestamp') from None
    need(parsed.tzinfo is not None and parsed.utcoffset() == dt.timedelta(0), 'utc_timestamp')
    return parsed


def canonical_sha(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def sha256_hex(content):
    return hashlib.sha256(content).hexdigest()


def digest(content):
    """The ``{bytes, sha256}`` pair receipts retain for exact content."""
    return dict(bytes=len(content), sha256=sha256_hex(content))


def hex_digest(value, code='digest_shape'):
    """Accept only a lowercase 64-hex-digit digest string."""
    need(type(value) is str and DIGEST.fullmatch(value) is not None, code)
    return value


def scan_refusal(_):
    raise Refusal('filesystem_scan_failed')


def plain_path(raw):
    need(isinstance(raw, str) and Path(raw).is_absolute(), 'absolute_path_required')
    path = Path(raw)
    need('..' not in path.parts and all(not p.is_symlink() for p in (path, *path.parents)), 'path_indirection')
    return path


def read_bytes(path, limit=FILE_LIMIT, code='file_limit'):
    """Whole bytes of a regular file at a plain path, refusing past ``limit``."""
    path = plain_path(str(path))
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
        need(stat.S_ISREG(os.fstat(source.fileno()).st_mode), 'regular_file_required')
        content = source.read(limit + 1)
    need(len(content) <= limit, code)
    return content


def read_json(path):
    return decode_json(read_bytes(path))


def decode_json(content):
    try:
        return json.loads(content, object_pairs_hook=unique_object,
                          parse_constant=reject_nonfinite)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Refusal('invalid_json') from None


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def reject_nonfinite(_):
    raise Refusal('nonfinite_json')


def file_sha256(path, *, limit=PIN_LIMIT):
    """Streamed sha256 of a regular file at a plain path, refusing past ``limit``."""
    path = plain_path(str(path))
    digest_, total = hashlib.sha256(), 0
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
        need(stat.S_ISREG(os.fstat(source.fileno()).st_mode), 'regular_file_required')
        while content := source.read(1024*1024):
            total += len(content)
            need(total <= limit, 'pin_byte_limit')
            digest_.update(content)
    return digest_.hexdigest()


def pin_shape(reference):
    need(isinstance(reference, dict) and set(reference) == {'path', 'sha256'}, 'pin_shape')
    need(isinstance(reference['sha256'], str) and DIGEST.fullmatch(reference['sha256']), 'pin_digest')


def pin(reference):
    """Verify a ``{path, sha256}`` reference by streaming the file; large pins never load."""
    pin_shape(reference)
    need(file_sha256(reference['path']) == reference['sha256'], 'pin_mismatch')


def pin_reference(path):
    """A ``{path, sha256}`` reference for a regular file read under ``FILE_LIMIT``."""
    return dict(path=str(path), sha256=sha256_hex(read_bytes(path)))


def read_pin_bytes(reference):
    # Callers parse or retain these exact hashed bytes without a second open.
    pin_shape(reference)
    content = read_bytes(reference['path'])
    need(sha256_hex(content) == reference['sha256'], 'pin_mismatch')
    return content


def read_json_pin(reference):
    return decode_json(read_pin_bytes(reference))


def sized_pin_shape(reference, code, *, exact=True):
    fields = {'path', 'bytes', 'sha256'}
    need(isinstance(reference, dict)
         and (set(reference) == fields if exact else fields <= set(reference)), code)


def read_sized_pin(reference, code='pin_mismatch', *, shape_code=None, exact=True,
                   limit=FILE_LIMIT, limit_code='file_limit', os_error=False):
    """Exact bytes behind a ``{path, bytes, sha256}`` reference; any change refuses with ``code``.

    ``exact`` requires exactly those fields; ``limit``/``limit_code`` bound the read;
    ``os_error`` turns an unreadable path into the same refusal instead of an OSError.
    """
    sized_pin_shape(reference, code if shape_code is None else shape_code, exact=exact)
    try:
        content = read_bytes(reference['path'], limit=limit, code=limit_code)
    except OSError:
        if not os_error:
            raise
        raise Refusal(code) from None
    need(len(content) == reference['bytes'] and sha256_hex(content) == reference['sha256'], code)
    return content


def write_selected(path, document):
    write_selected_bytes(path, selected_bytes(document))


def selected_bytes(document):
    return (json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True)+'\n').encode('utf-8')


def write_selected_bytes(path, content):
    with path.open('xb') as target:
        os.chmod(path, 0o600)
        target.write(content)
        target.flush()
        os.fsync(target.fileno())


def write_once(path, content, code):
    """Create once, owner-only; an identical rerun is a no-op, different content refuses."""
    path = Path(path)
    if path.exists():
        need(read_bytes(path, limit=max(FILE_LIMIT, len(content))) == content, code)
    else:
        write_selected_bytes(path, content)
    return dict(path=str(path), **digest(content))
