# Selected attempt retention

`scripts/trial_attempt_retention.py` is a separate selected failure-channel
writer for a fixed composition worker. It accepts a newly consumed `Retention`
factory object and duplicates its held directory descriptor. The fixed worker
must create it before admitting actor bytes. It does not reopen the run by pathname, delete partial files,
launch processes or admit providers.

`prepare(composer, hook_rows)` accepts the exact `CodingStream` type for the
same run, and refuses a previously prepared success-retention object. It closes
the composer and writes its selected observations, intent digest and source
manifest digest to the single exclusive `ATTEMPT-EVIDENCE.json` leaf. There is
no raw dictionary or file-content ingestion API. This path writes no success
SELECTED or PREPARED record and makes no task-success or cell-validity claim.

The writer checks its held directory identity, owner and 0700 permissions,
creates the leaf with exclusive/no-follow access, flushes and fsyncs it and the
directory, then reads it back. Readback requires the written inode, regular-file
type, current owner, 0600 permissions, one link, expected size and exact bytes.
The existing 64 MiB selected-storage limit applies. Encoding happens before the
size check; the storage cap does not independently prove a hard peak-memory or
I/O deadline. The composer supplies the separately bounded selected input.

Preparation is consumed before processing begins. Refusals, exceptions,
interrupts and later write/readback/fsync faults leave that writer unavailable;
any partial file stays consumed. Closing drops the owned duplicate once and
reports uncertainty on identity or close faults. The original retention writer
has its own descriptor and close outcome. The fixed worker must close both
writers before it can emit a CLOSED declaration.

The immutable preparation receipt contains the selected file hash and byte
count; `return_receipt` remains false. Neither that receipt nor the retained file
establishes runtime closure. The actual worker must validate its bounded end
packet and stream/source binding before preparation. The external controller
must separately observe actual worker return, containment release, complete
control-channel draining and the exact CLOSED declaration. This module alone
cannot establish any of those facts or authenticate process/account identity.
Same-UID changes, host loss and blocking filesystem calls remain within the
external ownership and deadline contract.

Tests use disposable local directories and constructed failed native streams.
They exercise selected-only output, descriptor ownership after original close
or directory rename, run/type refusal, existing symlinks, lost privacy, fsync
failure, hard links, replaced inodes, changed bytes, constructor interrupts and
close uncertainty. They run no provider, campaign, production database or grade.
