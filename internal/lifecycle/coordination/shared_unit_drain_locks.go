package coordination

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
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
	root := r.cli.ProcRoot
	if root == "" {
		root = "/proc"
	}
	raw, err := os.ReadFile(filepath.Join(root, "locks"))
	if err != nil {
		return fmt.Errorf("D3 lock-owner visibility is unknown: %w", err)
	}
	for _, line := range strings.Split(string(raw), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		typeIndex := 1
		if len(fields) > 1 && fields[1] == "->" {
			typeIndex++
		}
		if len(fields) <= typeIndex {
			return errors.New("D3 lock observation is unknown")
		}
		if fields[typeIndex] != "FLOCK" {
			continue
		}
		if len(fields) <= typeIndex+4 {
			return errors.New("D3 BSD lock observation is unknown")
		}
		parts := strings.Split(fields[typeIndex+4], ":")
		if len(parts) != 3 {
			return errors.New("D3 BSD lock inode is unknown")
		}
		major, majorErr := strconv.ParseUint(parts[0], 16, 32)
		minor, minorErr := strconv.ParseUint(parts[1], 16, 32)
		inode, inodeErr := strconv.ParseUint(parts[2], 10, 64)
		if err := errors.Join(majorErr, minorErr, inodeErr); err != nil {
			return err
		}
		key := fmt.Sprintf("%x:%x:%d", major, minor, inode)
		if path := r.locks[key]; path != "" {
			return fmt.Errorf("D3 unit state lock has an owner or waiter: %s", path)
		}
		for selected, path := range r.locks {
			if strings.HasSuffix(selected, fmt.Sprintf(":%d", inode)) {
				return fmt.Errorf("D3 unit state lock owner/waiter identity is ambiguous: %s", path)
			}
		}
	}
	return nil
}
