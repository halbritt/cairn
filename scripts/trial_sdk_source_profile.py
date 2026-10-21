"""Source-owned preparation reservation for the integrated SDK composition.

These named Python backings do not attest native RSS, kernel queues, a selected
executable's compatibility or campaign capacity. All remain separate runtime
obligations. The legacy caller-quoted preparation route is unchanged.
"""
from trial_sdk_control import CAPTURE_LIMIT
from trial_sdk_incoming import MAX_CHUNK_BYTES, MAX_DOCUMENT_BYTES
from trial_task_permissions import settings_permissions
from trial_sdk_incoming import canonical_size_sha256
from trial_sdk_service_shutdown import ServiceOutput

# Retained controller wire <=8500; fixed permissions are retained independently.
# Four fixed canonical slabs cover caller's prior block, yielded block, encoder
# piece and generator slab. Text readback (including a UnicodeDecodeError copy)
# uses <=2*8192 plus carry; compiler/control full encodes use <=3*8500.
PERMISSION_BYTES = canonical_size_sha256(settings_permissions(profile='implementation/1'))[0]
PREPARATION_RAW = CAPTURE_LIMIT + PERMISSION_BYTES + 4 * MAX_CHUNK_BYTES + 12
# Parsed line + frozen controller graph + terminal graph, fragments, retained
# native offers/results and preparation/source metadata. Graph nodes are bounded
# by the 128KiB document grammar; 512 bytes/node includes Python container/table
# and allocator overhead. This is a source storage bound, not process RSS.
PREPARATION_WORKING = 512 * MAX_DOCUMENT_BYTES
SERVICE_RAW = ServiceOutput.MINIMUM_RAW


def prepare_native_review(document, output, resources):
    from trial_native_bootstrap import prepare_sdk_review_with_resources, PreparationRefusal
    if document.get('hook_interface') != {'profile': 'sdk-hosted-memory/1'}:
        raise PreparationRefusal('sdk_native_profile')
    return prepare_sdk_review_with_resources(document, output, resources,
        raw=PREPARATION_RAW, working=PREPARATION_WORKING)


def require_preparation(allocation):
    from trial_native_bootstrap import PreparationRefusal
    allocation.check()
    row = allocation.resources._lookup(allocation.resources._memory, allocation.ticket)
    if row[2] < PREPARATION_RAW or row[3] < PREPARATION_WORKING:
        raise PreparationRefusal('sdk_native_preparation_underquoted')
