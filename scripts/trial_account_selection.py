"""Explicit subscription account selection for the legacy harness, by reference only.

The weekly runner selects a Claude config home (a directory that holds that
account's ``.credentials.json``) and passes its path to the harness in
``CAIRN_TASK_EVAL_CLAUDE_CONFIG_DIR``. ``trial_task_eval.sandbox_command``
then binds that file in place of the host's ambient credentials. Only path
metadata is checked; credential content is never opened, read or copied.
Which account the file belongs to, and whether it is dedicated, remain
declarations made by whoever selected it.
"""
import os
from pathlib import Path
import stat

import trial_paired_request as paired

ENVIRONMENT = 'CAIRN_TASK_EVAL_CLAUDE_CONFIG_DIR'
CREDENTIALS = '.credentials.json'


def ambient_config_home():
    return Path.home() / '.claude'


def selected_credentials(value):
    """Return the selected credentials path; refuse invalid, missing or ambient selections."""
    paired.need(isinstance(value, str) and value and '\0' not in value and Path(value).is_absolute(),
                'account_selection_invalid')
    try:
        home = Path(value).resolve(strict=True)
    except (OSError, RuntimeError):
        raise paired.Refusal('account_selection_invalid') from None
    paired.need(home.is_dir(), 'account_selection_invalid')
    try:
        ambient = ambient_config_home().resolve(strict=False)
    except (OSError, RuntimeError):
        raise paired.Refusal('account_selection_invalid') from None
    paired.need(home != ambient, 'ambient_credentials_refused')
    credentials = home / CREDENTIALS
    try:
        mode = os.stat(credentials, follow_symlinks=False).st_mode
    except OSError:
        raise paired.Refusal('account_credentials_missing') from None
    paired.need(stat.S_ISREG(mode), 'account_credentials_missing')
    return credentials


def credentials_source(environment=None):
    """Bind source for the sandbox: the selected credentials, or None for the original ambient binding."""
    environment = os.environ if environment is None else environment
    if ENVIRONMENT not in environment:
        return None
    return selected_credentials(environment[ENVIRONMENT])
