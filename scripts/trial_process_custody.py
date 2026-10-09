"""Closed process-report reads and selected unknowns for the prospective caller."""
import json
import os
import stat


ERRORS = frozenset({'leader_pidfd_unknown', 'leader_identity_unknown', 'leader_exit_observation_unknown',
    'parent_identity_unknown', 'descendant_tracking_limit', 'descendant_identity_changed',
    'descendant_ancestry_unbound', 'descendant_pidfd_unknown', 'pidfd_close_unknown',
    'process_scan_limit', 'process_scan_unknown', 'member_identity_unbound',
    'member_signal_unknown', 'leader_signal_unknown', 'leader_wait_unknown', 'observed_members_remain',
    'stream_close_unknown'})
CLASSIFICATIONS = frozenset({'completed', 'timeout', 'output_limit', 'timing_limit', 'observer_interrupted_or_io_error'})
LIMITS = ['unsampled_or_reparented_escape_unproven', 'procfs_io_not_hard_supervised',
          'zombie_reaping_by_other_parents_unproven']


def unknown_custody(reason):
    return dict(schema='cairn.native-process-custody/1', classification='unknown',
                legacy_exit_code=None, observed_returncode=None, observed_wait_status=None,
                returncode_basis='unavailable', leader_reaped=None,
                cleanup=dict(status='unknown', errors=[reason], members_before_cleanup=[],
                             members_after_cleanup=[], observed_escaped_count=None,
                             complete_descendant_custody=False, scope='unavailable', limits=LIMITS),
                input_bytes=None, input_bytes_written=None, input_delivery_complete=None, stdout_bytes=None, stderr_bytes=None,
                stdout_eof=None, stderr_eof=None, raw_stream_retained=False,
                hard_io_deadline_attested=False)


def validate_custody(value):
    expected = unknown_custody('unused')
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError('custody_shape')
    if value['schema'] != expected['schema'] or value['classification'] not in CLASSIFICATIONS:
        raise ValueError('custody_shape')
    integers = ['input_bytes', 'input_bytes_written', 'stdout_bytes', 'stderr_bytes']
    if any(type(value[k]) is not int or not 0 <= value[k] <= 2**63-1 for k in integers):
        raise ValueError('custody_shape')
    if value['input_bytes_written'] > value['input_bytes']:
        raise ValueError('custody_shape')
    if type(value['input_delivery_complete']) is not bool or value['input_delivery_complete'] != (value['input_bytes_written'] == value['input_bytes']):
        raise ValueError('custody_shape')
    if type(value['legacy_exit_code']) is not int or not -128 <= value['legacy_exit_code'] <= 255:
        raise ValueError('custody_shape')
    raw, code = value['observed_wait_status'], value['observed_returncode']
    if raw is not None and (type(raw) is not int or not 0 <= raw <= 65535):
        raise ValueError('custody_shape')
    if (raw is None and code is not None) or (raw is not None and (type(code) is not int or code != os.waitstatus_to_exitcode(raw))):
        raise ValueError('custody_shape')
    if type(value['leader_reaped']) is not bool or value['leader_reaped'] != (raw is not None):
        raise ValueError('custody_shape')
    if value['returncode_basis'] != 'waitpid_status_to_exitcode':
        raise ValueError('custody_shape')
    if any(type(value[k]) is not bool for k in ['stdout_eof', 'stderr_eof', 'raw_stream_retained', 'hard_io_deadline_attested']):
        raise ValueError('custody_shape')
    if value['raw_stream_retained'] or value['hard_io_deadline_attested']:
        raise ValueError('custody_shape')
    cleanup = value['cleanup']
    if type(cleanup) is not dict or set(cleanup) != set(expected['cleanup']):
        raise ValueError('custody_shape')
    errors = cleanup['errors']
    if type(errors) is not list or len(errors) > len(ERRORS) or any(type(e) is not str or e not in ERRORS for e in errors):
        raise ValueError('custody_shape')
    if cleanup['status'] != ('unknown' if errors else 'observed_clear') or cleanup['limits'] != LIMITS:
        raise ValueError('custody_shape')
    if cleanup['complete_descendant_custody'] is not False or cleanup['scope'] != 'anchored_new_session_and_pidfd_bound_descendants':
        raise ValueError('custody_shape')
    if type(cleanup['observed_escaped_count']) is not int or not 0 <= cleanup['observed_escaped_count'] <= 256:
        raise ValueError('custody_shape')
    for key in ['members_before_cleanup', 'members_after_cleanup']:
        members = cleanup[key]
        if type(members) is not list or len(members) > 256:
            raise ValueError('custody_shape')
        for member in members:
            if type(member) is not dict or set(member) != {'pid', 'state', 'ppid', 'pgid', 'sid', 'start'}:
                raise ValueError('custody_shape')
            if any(type(member[k]) is not int or not 0 <= member[k] <= 2**63-1 for k in ['pid', 'ppid', 'pgid', 'sid', 'start']):
                raise ValueError('custody_shape')
            if type(member['state']) is not str or member['state'] not in {'R','S','D','Z','T','t','W','X','x','K','P','I'}:
                raise ValueError('custody_shape')
    return value


def read_custody(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError('custody_shape')
            encoded = source.read(128*1024+1)
            if len(encoded) > 128*1024:
                raise ValueError('custody_shape')
        return validate_custody(json.loads(encoded))
    except FileNotFoundError:
        return unknown_custody('custody_metadata_missing')
    except OSError:
        return unknown_custody('custody_metadata_io')
    except (ValueError, TypeError, KeyError, RecursionError):
        return unknown_custody('custody_metadata_malformed')
