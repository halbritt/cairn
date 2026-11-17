package coordination

import (
	"fmt"
	"strings"
)

// Read the kernel's existing BSD flock observations rather than acquiring an
// inspector lock that could make an owning callback refuse. Only matching unit
// inodes are retained; unrelated host lock/process details are discarded.
// Btrfs can expose a subvolume device through stat and its superblock device in
// /proc/locks. An inode match with a different device is therefore UNKNOWN,
// never proof that the selected lock is free. A collision on another filesystem
// may conservatively refuse; this reader does not attribute that lock to a PID.
func (r *watchDrainRead) lockOwners() error {
	return r.lockOwnersFrom(r.openProcLocks)
}

func (r *watchDrainRead) lockOwnersFrom(open procLocksOpen) error {
	inodes, err := r.lockInodes()
	if err != nil {
		return err
	}
	rows, err := stableProcLocks(open, inodes)
	if err != nil {
		return fmt.Errorf("D3 lock-owner visibility is unknown: %w", err)
	}
	return r.refuseLockOwners(rows)
}

func (r *watchDrainRead) refuseLockOwners(rows []procLockRow) error {
	for _, row := range rows {
		if row.kind != "FLOCK" {
			continue
		}
		if path := r.locks[row.key]; path != "" {
			return fmt.Errorf("D3 unit state lock has an owner or waiter: %s", path)
		}
		for selected, path := range r.locks {
			if strings.HasSuffix(selected, fmt.Sprintf(":%d", row.inode)) {
				return fmt.Errorf("D3 unit state lock owner/waiter identity is ambiguous: %s", path)
			}
		}
	}
	return nil
}
