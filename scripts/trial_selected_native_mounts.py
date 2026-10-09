"""Reviewed package byte checks and the default fixture's exact sink mount set.

This preparation helper does not import or launch the reader. The reviewed
commit supplies `pins`; a caller declaration is never runtime admission.
No tests, terminal seam, actor hooks or loose top-level module fallback enter
the default sink dependency boundary. Mount-to-hash races against a hostile
host UID remain outside this prototype's explicitly stated protection.
"""
import dataclasses
import hashlib
import os
from pathlib import Path
import stat


PACKAGE_MODULES = ('__init__.py', 'evidence.py', 'frames.py', 'retention.py')
MODULE_LIMIT = 512*1024


class PackageRefusal(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class PackageMounts:
    manifest: dict
    sources: tuple


def reviewed_package(package_root, pins):
    """Check exactly four regular no-follow leaves against reviewed source pins."""
    if type(pins) is not dict or set(pins) != set(PACKAGE_MODULES):
        raise PackageRefusal('package_pin_set')
    if any(type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value)
           for value in pins.values()):
        raise PackageRefusal('package_pin_shape')
    root = Path(package_root)
    manifest = {}
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            for name in PACKAGE_MODULES:
                leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(leaf, 'rb') as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise PackageRefusal('package_module_type')
                    data = source.read(MODULE_LIMIT+1)
                if len(data) > MODULE_LIMIT:
                    raise PackageRefusal('package_module_bounds')
                actual = hashlib.sha256(data).hexdigest()
                if actual != pins[name]:
                    raise PackageRefusal('package_pin_mismatch')
                manifest['selected_native/'+name] = actual
        finally:
            os.close(fd)
    except OSError:
        raise PackageRefusal('package_source_unavailable') from None
    return PackageMounts(manifest, tuple((root/name, 'selected_native/'+name) for name in PACKAGE_MODULES))
