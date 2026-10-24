"""Explicit Codex subscription-account path selection and ChatGPT auth verification."""
import hashlib
import json
import os
import stat
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


def verify_auth_file(value, digest):
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
    return path

