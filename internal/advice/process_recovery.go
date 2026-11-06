//go:build linux

package advice

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"time"

	"golang.org/x/sys/unix"
)

// Linux 6.9 include/uapi/linux/pidfd.h. x/sys v0.41.0 exposes the syscall
// but not this flag. The kernel signals the pidfd's pinned struct pid/PGID:
// https://github.com/torvalds/linux/blob/v6.9/kernel/signal.c#L3733-L3736
const recoverySignalProcessGroup = 1 << 2

// RecoverProcessGroup cleans up a previously recorded isolated group on Linux
// 6.9+. Nil means the group was proved absent; every other outcome is a
// CleanupUnconfirmedError preserving identity. It neither waits for children
// nor falls back to signaling a numeric PID/PGID. Callers must retain their
// durable cleanup fence on error, including cancellation and unsupported kernels.
//
// Boot/start/group checks are observations, not an atomic /proc transaction.
// Signaling and subsequent probes use one pidfd so PID reuse after validation
// cannot redirect the signal. A missing leader with a surviving group is not
// enough evidence to signal. Processes that escaped the original group cannot
// be identified or cleaned up from ProcessIdentity alone.
func RecoverProcessGroup(ctx context.Context, identity ProcessIdentity) error {
	return recoverProcessGroup(ctx, identity, unix.PidfdSendSignal)
}

type recoverySignal func(int, unix.Signal, *unix.Siginfo, int) error

func recoverProcessGroup(ctx context.Context, identity ProcessIdentity, signal recoverySignal) error {
	unconfirmed := func(cause error) error {
		return &CleanupUnconfirmedError{Identity: identity, Cause: cause}
	}
	if err := ctx.Err(); err != nil {
		return unconfirmed(err)
	}
	if identity.PID <= 1 || identity.PGID != identity.PID || identity.StartTicks == 0 || identity.BootID == "" {
		return unconfirmed(errors.New("incomplete isolated process-group leader identity"))
	}
	boot, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
	if err != nil {
		return unconfirmed(fmt.Errorf("read current boot identity: %w", err))
	}
	if strings.TrimSpace(string(boot)) != identity.BootID {
		return unconfirmed(errors.New("recorded process belongs to a different boot"))
	}
	fd, err := unix.PidfdOpen(identity.PID, 0)
	if errors.Is(err, unix.ESRCH) {
		// A zero signal cannot affect a replacement group. Only ESRCH proves
		// absence; an existing group with no verifiable leader stays fenced.
		if probe := unix.Kill(-identity.PGID, 0); errors.Is(probe, unix.ESRCH) {
			return nil
		}
		return unconfirmed(errors.New("leader absent but process-group absence unproved"))
	}
	if err != nil {
		return unconfirmed(fmt.Errorf("pidfd recovery unavailable: %w", err))
	}
	defer unix.Close(fd)
	pid, group, ticks, state, err := recoveryProcessStat(identity.PID)
	if err != nil {
		return unconfirmed(fmt.Errorf("read pinned leader identity: %w", err))
	}
	if pid != identity.PID || group != identity.PGID || ticks != identity.StartTicks {
		return unconfirmed(errors.New("recorded PID/start/group identity no longer matches"))
	}
	if state == "Z" || state == "X" {
		return unconfirmed(errors.New("leader already exited; group ownership cannot be newly established"))
	}
	if err := ctx.Err(); err != nil {
		return unconfirmed(err)
	}
	probe := signal(fd, 0, nil, recoverySignalProcessGroup)
	if errors.Is(probe, unix.ESRCH) {
		return nil
	}
	if probe != nil {
		return unconfirmed(fmt.Errorf("pidfd group recovery unsupported or denied: %w", probe))
	}
	if err := signal(fd, unix.SIGKILL, nil, recoverySignalProcessGroup); err != nil {
		if errors.Is(err, unix.ESRCH) {
			return nil
		}
		return unconfirmed(fmt.Errorf("signal verified process group: %w", err))
	}
	// Zombies remain group members until their own parent reaps them. This
	// helper cannot reap unrelated children or turn a live/prohibited probe
	// into proof of death, so every wait is bounded and may remain unconfirmed.
	waitCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		probe := signal(fd, 0, nil, recoverySignalProcessGroup)
		if errors.Is(probe, unix.ESRCH) {
			return nil
		}
		if probe != nil {
			return unconfirmed(fmt.Errorf("verify process-group absence: %w", probe))
		}
		select {
		case <-waitCtx.Done():
			return unconfirmed(fmt.Errorf("process group still present after cleanup: %w", waitCtx.Err()))
		case <-ticker.C:
		}
	}
}

func recoveryProcessStat(pid int) (int, int, uint64, string, error) {
	file, err := os.Open(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return 0, 0, 0, "", err
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, 4097))
	if err != nil {
		return 0, 0, 0, "", err
	}
	text := string(data)
	open, close := strings.IndexByte(text, '('), strings.LastIndexByte(text, ')')
	if len(data) > 4096 || open < 1 || close <= open {
		return 0, 0, 0, "", errors.New("invalid bounded process stat")
	}
	fields := strings.Fields(text[close+1:])
	if len(fields) < 20 {
		return 0, 0, 0, "", errors.New("incomplete process stat")
	}
	actualPID, pidErr := strconv.Atoi(strings.TrimSpace(text[:open]))
	group, groupErr := strconv.Atoi(fields[2])
	ticks, ticksErr := strconv.ParseUint(fields[19], 10, 64)
	if err := errors.Join(pidErr, groupErr, ticksErr); err != nil {
		return 0, 0, 0, "", err
	}
	return actualPID, group, ticks, fields[0], nil
}
