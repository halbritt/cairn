package coordination

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
)

const (
	procLocksReadSize = 64 * 1024
	procLocksMaxBytes = 4 * 1024 * 1024
	procLocksAttempts = 4
)

type procLocksOpen func() (io.ReadCloser, error)

type procLockRow struct {
	fields string // Ordinals are positions in a changing global list, not identity.
	kind   string
	key    string
	inode  uint64
}

func (r *watchDrainRead) openProcLocks() (io.ReadCloser, error) {
	root := r.cli.ProcRoot
	if root == "" {
		root = "/proc"
	}
	return os.Open(filepath.Join(root, "locks"))
}

func procLockIdentity(raw string) (string, uint64, error) {
	parts := strings.Split(raw, ":")
	if len(parts) != 3 {
		return "", 0, errors.New("kernel lock inode is unknown")
	}
	major, a := strconv.ParseUint(parts[0], 16, 32)
	minor, b := strconv.ParseUint(parts[1], 16, 32)
	inode, c := strconv.ParseUint(parts[2], 10, 64)
	if err := errors.Join(a, b, c); err != nil {
		return "", 0, err
	}
	return fmt.Sprintf("%x:%x:%d", major, minor, inode), inode, nil
}

func (r *watchDrainRead) lockInodes() (map[uint64]bool, error) {
	inodes := map[uint64]bool{}
	for key := range r.locks {
		_, inode, err := procLockIdentity(key)
		if err != nil {
			return nil, err
		}
		inodes[inode] = true
	}
	return inodes, nil
}

// A short seq_file read is not EOF. Never certify a bounded prefix as a table.
func readCompleteProcLocks(open procLocksOpen) (raw []byte, err error) {
	file, err := open()
	if err != nil {
		return nil, err
	}
	defer func() { err = errors.Join(err, file.Close()) }()
	buffer := make([]byte, procLocksReadSize)
	for {
		n, readErr := file.Read(buffer)
		if n > procLocksMaxBytes-len(raw) {
			return nil, errors.New("kernel lock table exceeds observation bound")
		}
		raw = append(raw, buffer[:n]...)
		if readErr == io.EOF {
			return raw, nil
		}
		if readErr != nil {
			return nil, readErr
		}
		if n == 0 {
			return nil, io.ErrNoProgress
		}
	}
}

func selectedProcLocks(raw []byte, inodes map[uint64]bool) ([]procLockRow, error) {
	if len(raw) != 0 && raw[len(raw)-1] != '\n' {
		return nil, errors.New("kernel lock table has an incomplete row")
	}
	var rows []procLockRow
	for _, line := range strings.Split(string(raw), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		typeIndex := 1
		if len(fields) > 1 && fields[1] == "->" {
			typeIndex++
		}
		if procLockWithoutInode(fields, typeIndex) {
			continue
		}
		if len(fields) != typeIndex+7 || !strings.HasSuffix(fields[0], ":") {
			return nil, errors.New("kernel lock row is malformed")
		}
		numbersErr := procLockNumbers(fields[0], fields[typeIndex+3], fields[typeIndex+5], fields[typeIndex+6])
		key, inode, identityErr := procLockIdentity(fields[typeIndex+4])
		if err := errors.Join(numbersErr, identityErr); err != nil {
			return nil, fmt.Errorf("kernel lock row is malformed: %w", err)
		}
		if inodes[inode] {
			if err := procLockKind(fields[typeIndex : typeIndex+3]); err != nil {
				return nil, err
			}
			rows = append(rows, procLockRow{strings.Join(fields[1:], " "), fields[typeIndex], key, inode})
		}
	}
	slices.SortFunc(rows, func(a, b procLockRow) int { return strings.Compare(a.fields, b.fields) })
	for i := 1; i < len(rows); i++ {
		if rows[i-1] == rows[i] {
			return nil, errors.New("kernel lock table duplicates a selected candidate")
		}
	}
	return rows, nil
}

// Kernel access/lease rows can explicitly have no backing inode. Preserve the
// prior exclusion of those unrelated rows, without accepting malformed flocks.
func procLockWithoutInode(fields []string, i int) bool {
	if len(fields) != i+6 || !strings.HasSuffix(fields[0], ":") ||
		!slices.Contains([]string{"READ", "WRITE", "UNLCK"}, fields[i+2]) {
		return false
	}
	switch fields[i] {
	case "POSIX", "OFDLCK", "ACCESS":
		if fields[i+1] != "*NOINODE*" {
			return false
		}
	case "LEASE", "DELEG":
		if fields[i+1] != "BREAKER" && fields[i+1] != "BREAKING" {
			return false
		}
	default:
		return false
	}
	pid := strings.Split(fields[i+3], ":")
	if len(pid) != 2 || pid[1] != "0" {
		return false
	}
	return procLockNumbers(fields[0], pid[0], fields[i+4], fields[i+5]) == nil
}

func procLockNumbers(ordinal, pid, first, last string) error {
	_, ordinalErr := strconv.ParseUint(strings.TrimSuffix(ordinal, ":"), 10, 64)
	_, pidErr := strconv.ParseInt(pid, 10, 64)
	start, startErr := strconv.ParseUint(first, 10, 64)
	if last == "EOF" {
		return errors.Join(ordinalErr, pidErr, startErr)
	}
	end, endErr := strconv.ParseUint(last, 10, 64)
	if err := errors.Join(ordinalErr, pidErr, startErr, endErr); err != nil {
		return err
	}
	if end < start {
		return errors.New("kernel lock range is malformed")
	}
	return nil
}

func procLockKind(fields []string) error {
	if fields[2] != "READ" && fields[2] != "WRITE" && fields[2] != "UNLCK" {
		return errors.New("kernel lock access mode is malformed")
	}
	switch fields[0] {
	case "FLOCK", "POSIX", "OFDLCK", "ACCESS":
		if fields[1] == "ADVISORY" {
			return nil
		}
	case "LEASE", "DELEG":
		if fields[1] == "ACTIVE" || fields[1] == "BREAKING" || fields[1] == "BREAKER" {
			return nil
		}
	}
	return errors.New("kernel lock candidate kind is malformed or unknown")
}

// Agreement concerns selected inode candidates, including device aliases and
// waiters. It is not a claim that /proc/locks provides an atomic snapshot.
func stableProcLocks(open procLocksOpen, inodes map[uint64]bool) ([]procLockRow, error) {
	var previous []procLockRow
	for attempt := 0; attempt < procLocksAttempts; attempt++ {
		raw, err := readCompleteProcLocks(open)
		if err != nil {
			return nil, err
		}
		rows, err := selectedProcLocks(raw, inodes)
		if err != nil {
			return nil, err
		}
		if attempt > 0 && slices.Equal(rows, previous) {
			return rows, nil
		}
		previous = rows
	}
	return nil, errors.New("kernel lock observations are unstable")
}
