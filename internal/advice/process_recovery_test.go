//go:build linux

package advice

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"testing"
	"time"

	"golang.org/x/sys/unix"
)

type recoveryFixtureState struct {
	Identity      ProcessIdentity
	DescendantPID int
}

// The supervisor owns and reaps the isolated group. The recovering test process
// is not its parent and cannot use Wait to establish successful cleanup.
func TestRecoveryProcessFixture(t *testing.T) {
	mode := os.Getenv("CAIRN_ADVICE_RECOVERY_FIXTURE")
	if mode == "" {
		return
	}
	if mode == "hold" {
		_, _ = io.Copy(io.Discard, os.Stdin)
		os.Exit(0)
	}
	if mode == "leader" {
		child := exec.Command(os.Args[0], "-test.run=^TestRecoveryProcessFixture$")
		child.Env = []string{"CAIRN_ADVICE_RECOVERY_FIXTURE=hold"}
		child.Stdin = os.Stdin
		if err := child.Start(); err != nil {
			os.Exit(90)
		}
		_ = json.NewEncoder(os.Stdout).Encode(child.Process.Pid)
		gate := os.NewFile(3, "exit-gate")
		var exit [1]byte
		_, _ = gate.Read(exit[:])
		os.Exit(0)
	}
	if mode != "supervisor" && mode != "orphan-supervisor" && mode != "family-supervisor" && mode != "zombie-supervisor" {
		os.Exit(91)
	}
	leafInput, leafWriter, err := os.Pipe()
	if err != nil {
		os.Exit(92)
	}
	leaf := exec.Command(os.Args[0], "-test.run=^TestRecoveryProcessFixture$")
	leaf.Env = []string{"CAIRN_ADVICE_RECOVERY_FIXTURE=hold"}
	leaf.Stdin = leafInput
	leaf.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	var gateReader, gateWriter *os.File
	var childOutput io.ReadCloser
	if mode == "orphan-supervisor" || mode == "family-supervisor" {
		if err := unix.Prctl(unix.PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0); err != nil {
			os.Exit(95)
		}
		gateReader, gateWriter, err = os.Pipe()
		if err != nil {
			os.Exit(96)
		}
		leaf.Env = []string{"CAIRN_ADVICE_RECOVERY_FIXTURE=leader"}
		leaf.ExtraFiles = []*os.File{gateReader}
		childOutput, err = leaf.StdoutPipe()
		if err != nil {
			os.Exit(97)
		}
	}
	if err := leaf.Start(); err != nil {
		os.Exit(93)
	}
	_ = leafInput.Close()
	identity := ProcessIdentity{PID: leaf.Process.Pid, PGID: leaf.Process.Pid,
		StartTicks: readStartTicks(leaf.Process.Pid), BootID: readBootID()}
	state := recoveryFixtureState{Identity: identity}
	descendantFD := -1
	if mode == "orphan-supervisor" || mode == "family-supervisor" {
		_ = gateReader.Close()
		if err := json.NewDecoder(childOutput).Decode(&state.DescendantPID); err != nil {
			_ = leaf.Process.Kill()
			_ = leaf.Wait()
			os.Exit(98)
		}
		descendantFD, err = unix.PidfdOpen(state.DescendantPID, 0)
		if err != nil {
			os.Exit(103)
		}
		if mode == "orphan-supervisor" {
			_ = gateWriter.Close()
			if err := leaf.Wait(); err != nil {
				os.Exit(99)
			}
		}
	}
	if mode == "zombie-supervisor" {
		if err := leaf.Process.Kill(); err != nil {
			os.Exit(100)
		}
		var info unix.Siginfo
		if err := unix.Waitid(unix.P_PID, leaf.Process.Pid, &info, unix.WEXITED|unix.WNOWAIT, nil); err != nil {
			os.Exit(101)
		}
	}
	reaped := make(chan struct{})
	if mode == "supervisor" || mode == "family-supervisor" {
		go func() {
			_ = leaf.Wait()
			if mode == "family-supervisor" {
				if _, err := unix.Wait4(state.DescendantPID, nil, 0, nil); err != nil {
					os.Exit(104)
				}
			}
			close(reaped)
		}()
	}
	if err := json.NewEncoder(os.Stdout).Encode(state); err != nil {
		_ = leaf.Process.Kill()
		_ = leafWriter.Close()
		os.Exit(94)
	}
	var command [1]byte
	_, _ = os.Stdin.Read(command[:])
	_ = leafWriter.Close()
	if descendantFD >= 0 {
		// The same fixture child may already have exited and been reaped.
		// A held pidfd avoids signaling a later reuse of its numeric PID.
		_ = unix.PidfdSendSignal(descendantFD, unix.SIGKILL, nil, 0)
		_ = unix.Close(descendantFD)
	}
	if mode == "orphan-supervisor" {
		if _, err := unix.Wait4(state.DescendantPID, nil, 0, nil); err != nil {
			os.Exit(102)
		}
	} else {
		_ = leaf.Process.Kill() // Only the fixture's direct child.
		if mode == "supervisor" || mode == "family-supervisor" {
			<-reaped
		} else {
			_ = leaf.Wait()
		}
	}
	os.Exit(0)
}

func recoveryFixture(t *testing.T, mode string) recoveryFixtureState {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestRecoveryProcessFixture$")
	cmd.Env = []string{"CAIRN_ADVICE_RECOVERY_FIXTURE=" + mode}
	input, err := cmd.StdinPipe()
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	output, err := cmd.StdoutPipe()
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		cancel()
		t.Fatal(err)
	}
	var state recoveryFixtureState
	t.Cleanup(func() {
		_ = input.Close()
		err := cmd.Wait()
		cancel()
		if err != nil {
			t.Errorf("owned supervisor cleanup: %v", err)
		}
		for _, pid := range []int{state.Identity.PID, state.DescendantPID} {
			if pid > 1 {
				if err := syscall.Kill(pid, 0); !errors.Is(err, syscall.ESRCH) {
					t.Errorf("owned fixture process %d remains after supervisor cleanup: %v", pid, err)
				}
			}
		}
	})
	if err := json.NewDecoder(output).Decode(&state); err != nil {
		t.Fatal(err)
	}
	identity := state.Identity
	if identity.PID <= 1 || identity.PID != identity.PGID || identity.StartTicks == 0 || identity.BootID == "" {
		t.Fatalf("fixture did not register a real isolated process: %+v", identity)
	}
	t.Logf("owned supervisor=%d leader=%d descendant=%d", cmd.Process.Pid, identity.PID, state.DescendantPID)
	return state
}

func TestRecoverProcessGroupMatchingNonChildProvesGone(t *testing.T) {
	for _, mode := range []string{"supervisor", "family-supervisor"} {
		t.Run(mode, func(t *testing.T) {
			state := recoveryFixture(t, mode)
			identity := state.Identity
			if state.DescendantPID != 0 {
				requireRecoveryProcessAlive(t, state.DescendantPID)
				group, err := syscall.Getpgid(state.DescendantPID)
				if err != nil || group != identity.PGID {
					t.Fatalf("fixture descendant is not in the selected group: %d %v", group, err)
				}
			}
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			if err := RecoverProcessGroup(ctx, identity); err != nil {
				t.Fatal(err)
			}
			if err := syscall.Kill(-identity.PGID, 0); !errors.Is(err, syscall.ESRCH) {
				t.Fatalf("cleanup claimed group absence without ESRCH: %v", err)
			}
			if err := RecoverProcessGroup(ctx, identity); err != nil {
				t.Fatalf("already absent group should reconcile without signaling: %v", err)
			}
		})
	}
}

func requireRecoveryUnconfirmed(t *testing.T, err error, identity ProcessIdentity) {
	t.Helper()
	var unconfirmed *CleanupUnconfirmedError
	if !errors.As(err, &unconfirmed) || unconfirmed.Identity != identity || unconfirmed.Cause == nil {
		t.Fatalf("want preserved-identity cleanup refusal, got %v", err)
	}
}

func requireRecoveryProcessAlive(t *testing.T, pid int) {
	t.Helper()
	status, err := os.ReadFile(fmt.Sprintf("/proc/%d/status", pid))
	if err != nil || strings.Contains(string(status), "State:\tZ") || strings.Contains(string(status), "State:\tX") {
		t.Fatalf("owned process %d did not survive refusal: %v", pid, err)
	}
}

func TestRecoverProcessGroupIdentityMismatchDoesNotSignal(t *testing.T) {
	for _, field := range []string{"boot", "start", "group"} {
		t.Run(field, func(t *testing.T) {
			original := recoveryFixture(t, "supervisor").Identity
			changed := original
			switch field {
			case "boot":
				changed.BootID = "different-boot"
			case "start":
				changed.StartTicks++
			case "group":
				changed.PGID++
			}
			err := RecoverProcessGroup(context.Background(), changed)
			requireRecoveryUnconfirmed(t, err, changed)
			requireRecoveryProcessAlive(t, original.PID)
		})
	}
}

func TestRecoverProcessGroupMissingLeaderDoesNotSignalDescendant(t *testing.T) {
	state := recoveryFixture(t, "orphan-supervisor")
	if _, err := os.Stat(fmt.Sprintf("/proc/%d", state.Identity.PID)); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("fixture leader was not reaped: %v", err)
	}
	requireRecoveryProcessAlive(t, state.DescendantPID)
	err := RecoverProcessGroup(context.Background(), state.Identity)
	requireRecoveryUnconfirmed(t, err, state.Identity)
	requireRecoveryProcessAlive(t, state.DescendantPID)
}

func TestRecoverProcessGroupZombieRemainsUnconfirmed(t *testing.T) {
	identity := recoveryFixture(t, "zombie-supervisor").Identity
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	requireRecoveryUnconfirmed(t, RecoverProcessGroup(ctx, identity), identity)
	if err := syscall.Kill(-identity.PGID, 0); err != nil {
		t.Fatalf("fixture did not retain the unreaped group: %v", err)
	}
}

func TestRecoverProcessGroupUnsupportedOrDeniedDoesNotSignal(t *testing.T) {
	for _, failure := range []error{unix.ENOSYS, unix.EINVAL, unix.EPERM} {
		t.Run(failure.Error(), func(t *testing.T) {
			identity := recoveryFixture(t, "supervisor").Identity
			// Only the kernel syscall boundary is substituted. A real isolated
			// process must survive every refusal; no model/provider is involved.
			err := recoverProcessGroup(context.Background(), identity, func(_ int, sig unix.Signal, _ *unix.Siginfo, _ int) error {
				if sig != 0 {
					t.Fatalf("unsupported/denied group probe permitted a destructive signal")
				}
				return failure
			})
			requireRecoveryUnconfirmed(t, err, identity)
			if !errors.Is(err, failure) {
				t.Fatalf("refusal lost kernel cause: %v", err)
			}
			requireRecoveryProcessAlive(t, identity.PID)
		})
	}
}

func TestRecoverProcessGroupFinalLiveOrDeniedProbeIsNotGone(t *testing.T) {
	for _, denied := range []bool{false, true} {
		t.Run(fmt.Sprintf("denied=%t", denied), func(t *testing.T) {
			identity := recoveryFixture(t, "supervisor").Identity
			ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
			defer cancel()
			killed := false
			err := recoverProcessGroup(ctx, identity, func(fd int, sig unix.Signal, info *unix.Siginfo, flags int) error {
				if killed && sig == 0 {
					if denied {
						return unix.EPERM
					}
					return nil // Kernel still reports group existence.
				}
				if sig == unix.SIGKILL {
					killed = true
				}
				return unix.PidfdSendSignal(fd, sig, info, flags)
			})
			if !killed {
				t.Fatal("fixture did not reach actual verified-group signaling")
			}
			requireRecoveryUnconfirmed(t, err, identity)
			if denied && !errors.Is(err, unix.EPERM) || !denied && !errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("cleanup lost the live/denied outcome: %v", err)
			}
		})
	}
}
