"""Native session profile of the existing original-owner receipt I/O port."""
from functools import wraps
from trial_sdk_producer_resources import ProducerResources
from trial_sdk_resource_failure import poison_resource, annotate_resource_failure


class SDKSessionResources(ProducerResources):
    # Named stream slabs, bounded writer copy, decoder byte copy/concatenation
    # and UTF-8 carry only. The original
    # preparation standing allocation covers controller/callback state; a full
    # simultaneous quote remains the fixed builder's obligation.
    MINIMUM_RAW = 5 * 512 + 6


def resource_boundary(method):
    """Quarantine original accounting even if final local cleanup interrupts."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except BaseException as primary:
            resources = self._resources
            faults = poison_resource(resources, primary)
            annotate_resource_failure(primary, faults)
            raise
    return wrapped
