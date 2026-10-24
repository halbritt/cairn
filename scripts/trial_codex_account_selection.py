"""Explicit Codex subscription-account path selection and ChatGPT auth verification."""
import hashlib
import json
import os
import select
import stat
import subprocess
import time
from pathlib import Path

import trial_paired_request as paired

FIELDS='account_id auth_file auth_sha256'


def selected_auth(account):
    paired.shape(account,FIELDS,'codex_account_selection_invalid')
    paired.need(paired.capacity_name(account['account_id']),'codex_account_selection_invalid')
    value=account['auth_file']
    paired.need(type(value) is str and value and '\0' not in value and Path(value).is_absolute(),
                'codex_account_selection_invalid')
    digest=account['auth_sha256']
    paired.need(type(digest) is str and len(digest)==64 and all(c in '0123456789abcdef' for c in digest),
                'codex_account_selection_invalid')
    return verify_auth_file(value,digest)


def verify_auth_file(value, digest, *, expected_account_id=None):
    """Verify pinned ChatGPT subscription mode without returning or logging token data."""
    paired.need(type(value) is str and value and '\0' not in value and Path(value).is_absolute(),
                'codex_account_selection_invalid')
    paired.need(type(digest) is str and len(digest)==64 and all(c in '0123456789abcdef' for c in digest),
                'codex_account_selection_invalid')
    try:
        path=Path(value).resolve(strict=True)
        mode=os.stat(path,follow_symlinks=False).st_mode
    except (OSError,RuntimeError):
        raise paired.Refusal('codex_account_auth_missing') from None
    paired.need(stat.S_ISREG(mode),'codex_account_auth_missing')
    try:
        raw=path.read_bytes()
        document=json.loads(raw)
    except (OSError,ValueError,UnicodeDecodeError):
        raise paired.Refusal('codex_account_auth_invalid') from None
    paired.need(hashlib.sha256(raw).hexdigest()==digest,'codex_account_auth_changed')
    paired.need(isinstance(document,dict) and document.get('auth_mode')=='chatgpt'
                and not document.get('OPENAI_API_KEY'), 'codex_account_subscription_required')
    if expected_account_id is not None:
        tokens = document.get('tokens')
        paired.need(isinstance(tokens, dict) and tokens.get('account_id') == expected_account_id,
                    'codex_account_identity_mismatch')
    return path


def subscription_preflight(account, provider, model, *, timeout=30):
    """Read only rate-limit and model-catalog status through this account's pinned Codex CLI."""
    auth = verify_auth_file(account['auth_file'], account['auth_sha256'],
                            expected_account_id=account['account_id'])
    paired.shape(provider, 'path sha256', 'codex_provider_pin_invalid')
    binary = Path(provider['path']).resolve(strict=True)
    paired.need(hashlib.sha256(binary.read_bytes()).hexdigest() == provider['sha256'],
                'codex_provider_pin_changed')
    paired.need(Path(account['config_home']).resolve(strict=True) == auth.parent,
                'codex_account_home_mismatch')
    env = dict(os.environ, CODEX_HOME=str(auth.parent))
    for name in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENROUTER_API_KEY', 'OPENROUTER_BASE_URL'):
        env.pop(name, None)
    process = None
    try:
        process = subprocess.Popen([str(binary), 'app-server', '--listen', 'stdio://'], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        _write_rpc(process, dict(id=1, method='initialize', params={
            'clientInfo': dict(name='cairn-weekly-capacity-preflight', version='1.0.0')}))
        _rpc_response(process, 1, timeout)
        process.stdin.write(b'{"method":"initialized","params":{}}\n')
        process.stdin.flush()
        _write_rpc(process, dict(id=2, method='account/rateLimits/read', params={}))
        rate = _rpc_response(process, 2, timeout)
        _write_rpc(process, dict(id=3, method='model/list', params={}))
        catalog = _rpc_response(process, 3, timeout)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, TimeoutError):
        raise paired.Refusal('codex_subscription_status_unavailable') from None
    finally:
        if process is not None:
            try:
                process.stdin.close()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            except OSError:
                if process.poll() is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait()
            finally:
                process.stdout.close()
    paired.need(isinstance(rate, dict) and isinstance(catalog, dict),
                'codex_subscription_status_unavailable')
    allowed = rate.get('ordinaryUsageAllowed')
    paired.need(allowed is True, 'codex_subscription_not_allowed')
    limits = rate.get('rateLimits')
    paired.need(isinstance(limits, dict) and limits.get('limitId') == 'codex',
                'codex_subscription_status_unavailable')
    primary = limits.get('primary')
    paired.need(isinstance(primary, dict)
                and type(primary.get('usedPercent')) is int and 0 <= primary['usedPercent'] <= 100
                and type(primary.get('windowDurationMins')) is int and primary['windowDurationMins'] > 0,
                'codex_subscription_status_unavailable')
    paired.need(_catalog_contains(catalog, model), 'codex_model_unavailable')
    verify_auth_file(account['auth_file'], account['auth_sha256'], expected_account_id=account['account_id'])
    paired.need(hashlib.sha256(binary.read_bytes()).hexdigest() == provider['sha256'],
                'codex_provider_pin_changed')
    return dict(account_id=account['account_id'], model=model, ordinary_usage_allowed=True,
                used_percent=primary['usedPercent'], window_duration_mins=primary['windowDurationMins'],
                resets_at=primary.get('resetsAt'), secondary_present=limits.get('secondary') is not None,
                catalog_model_present=True, absolute_allowance_observed=False)


def _write_rpc(process, request):
    process.stdin.write(json.dumps(request, separators=(',', ':')).encode() + b'\n')
    process.stdin.flush()


def _rpc_response(process, request_id, timeout):
    deadline = time.monotonic() + timeout
    buffered = bytearray()
    while time.monotonic() < deadline:
        readable, _, _ = select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))
        if not readable:
            break
        chunk = os.read(process.stdout.fileno(), 65536)
        if not chunk:
            break
        buffered.extend(chunk)
        paired.need(len(buffered) <= 4 * 1024 * 1024, 'codex_subscription_status_unavailable')
        while b'\n' in buffered:
            line, _, rest = buffered.partition(b'\n')
            buffered = bytearray(rest)
            response = json.loads(line)
            if isinstance(response, dict) and response.get('id') == request_id:
                paired.need('error' not in response and isinstance(response.get('result'), dict),
                            'codex_subscription_status_unavailable')
                return response['result']
    raise TimeoutError


def _catalog_contains(value, model):
    if isinstance(value, dict):
        return (any(value.get(key) == model for key in ('id', 'model', 'modelId', 'model_id'))
                or any(_catalog_contains(child, model) for child in value.values()))
    if isinstance(value, list):
        return any(_catalog_contains(child, model) for child in value)
    return False

