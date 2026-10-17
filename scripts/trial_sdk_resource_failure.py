"""Retain resource recovery uncertainty without replacing the primary failure."""
from trial_bootstrap_snapshot import record_cleanup


def freeze_resource(resources):
    try:
        resources.freeze()
    except BaseException:
        return ('resource_freeze_unknown',)
    return ()


def poison_resource(binding, primary):
    if binding is None:
        return ()
    # Local refusal does not depend on successful ledger recovery. Never
    # describe a failed freeze as a known frozen bank or refund its tickets.
    binding._unknown = True
    try:
        faults = binding.poison(primary)
    except BaseException:
        faults = ('resource_poison_unknown',)
    if faults is None:
        faults = ()
    previous = getattr(binding, '_resource_cleanup_faults', ())
    binding._resource_cleanup_faults = tuple(sorted(set(previous + faults)))
    return binding._resource_cleanup_faults


def annotate_resource_failure(primary, faults):
    if not faults:
        return
    try:
        previous = getattr(primary, 'cleanup_faults', ())
        if (type(previous) is not tuple or len(previous) > 16
                or any(type(code) is not str or len(code) > 80 for code in previous)):
            previous = ('cleanup_annotation_unknown',)
    except BaseException:
        previous = ('cleanup_annotation_unknown',)
    record_cleanup(primary, tuple(sorted(set(previous + faults))))
