// Package coordination contains the filesystem and process primitives used by
// lifecycle coordination. Callers order session -> ledger or nonblocking intent;
// ledger holders never acquire session, intent, watcher or cue locks. Native
// frontend and journal orchestration own that call graph and its policy.
package coordination

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

type CoordinationError struct {
	Code, Message string
	Cause         error
}

func (e *CoordinationError) Error() string { return fmt.Sprintf("%s: %s", e.Code, e.Message) }
func (e *CoordinationError) Unwrap() error { return e.Cause }
func IsSessionBusy(err error) bool {
	var busy *CoordinationError
	return errors.As(err, &busy) && busy.Code == "SESSION_BUSY"
}

const DefaultLedgerLockWait = 1500 * time.Millisecond

// LockLease owns one open file description, including its BSD flock. Release
// closes it even if explicit unlock fails, and repeated releases return the
// original result. Lock files themselves are never changed or removed.
type LockLease struct {
	file *os.File
	once sync.Once
	err  error
}

func (l *LockLease) Release() error {
	l.once.Do(func() { l.err = errors.Join(syscall.Flock(int(l.file.Fd()), syscall.LOCK_UN), l.file.Close()) })
	return l.err
}

func SessionStatePath(stateDir, binding, nativeSessionID string) string {
	digest := sha256.Sum256([]byte(binding + "\x00" + nativeSessionID))
	return filepath.Join(stateDir, hex.EncodeToString(digest[:])+".json")
}

func stateSibling(path, suffix string) string {
	return strings.TrimSuffix(path, ".json") + suffix
}

func SessionLockPath(statePath string) string { return stateSibling(statePath, ".lock") }
func GrantLedgerLockPath(memoryStateDir, nativeSessionID string) string {
	return filepath.Join(memoryStateDir, nativeSessionID+".lock")
}
func IntentLockPath(stateDir, attemptID string) string {
	return filepath.Join(stateDir, "intents", attemptID+".lock")
}
func WatcherLockPath(configDir string) string { return filepath.Join(configDir, ".watch.lock") }
func CueLockPath(statePath string) string     { return stateSibling(statePath, ".cuelock") }

func acquireLock(ctx context.Context, path string, perm os.FileMode, wait time.Duration, message string) (*LockLease, error) {
	// Legacy Python lock opens follow symlinks and retain existing permissions.
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, perm)
	if err != nil {
		return nil, err
	}
	deadline := time.Now().Add(wait)
	if outer, ok := ctx.Deadline(); ok && outer.Before(deadline) {
		deadline = outer
	}
	busy := func(cause error) (*LockLease, error) {
		contextErr := ctx.Err()
		if outer, ok := ctx.Deadline(); contextErr == nil && ok && !time.Now().Before(outer) {
			contextErr = context.DeadlineExceeded
		}
		return nil, &CoordinationError{Code: "SESSION_BUSY", Message: message, Cause: errors.Join(cause, contextErr, file.Close())}
	}
	// The initial nonblocking attempt precedes the contended deadline check,
	// including zero-wait calls, matching grant_ledger.acquire_session_lock.
	for {
		err = syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			return &LockLease{file: file}, nil
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			return nil, errors.Join(err, file.Close())
		}
		remaining := time.Until(deadline)
		if remaining <= 0 || ctx.Err() != nil {
			return busy(err)
		}
		timer := time.NewTimer(min(10*time.Millisecond, remaining))
		select {
		case <-ctx.Done():
			timer.Stop()
			return busy(err)
		case <-timer.C:
		}
		if ctx.Err() != nil {
			return busy(err)
		}
	}
}

func AcquireSessionLock(statePath string) (*LockLease, error) {
	if err := os.MkdirAll(filepath.Dir(statePath), 0o700); err != nil {
		return nil, err
	}
	return acquireLock(context.Background(), SessionLockPath(statePath), 0o600, 0, "another lifecycle operation is active")
}
func TryAcquireGrantLedgerLock(memoryStateDir, nativeSessionID string) (*LockLease, error) {
	return AcquireGrantLedgerLock(context.Background(), memoryStateDir, nativeSessionID, 0)
}
func AcquireGrantLedgerLock(ctx context.Context, memoryStateDir, nativeSessionID string, wait time.Duration) (*LockLease, error) {
	return acquireLock(ctx, GrantLedgerLockPath(memoryStateDir, nativeSessionID), 0o666, wait, "another lifecycle operation is active")
}
func TryAcquireIntentLock(stateDir, attemptID string) (*LockLease, error) {
	return AcquireIntentLock(context.Background(), stateDir, attemptID, 0)
}
func AcquireIntentLock(ctx context.Context, stateDir, attemptID string, wait time.Duration) (*LockLease, error) {
	path := IntentLockPath(stateDir, attemptID)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	return acquireLock(ctx, path, 0o600, wait, "another inbox command for this attempt is active")
}
func AcquireWatcherLock(configDir string) (*LockLease, error) {
	if err := os.MkdirAll(configDir, 0o700); err != nil {
		return nil, err
	}
	return acquireLock(context.Background(), WatcherLockPath(configDir), 0o600, 0, "another lifecycle operation is active")
}
func TryAcquireCueLock(statePath string) (*LockLease, bool, error) {
	lease, err := acquireLock(context.Background(), CueLockPath(statePath), 0o600, 0, "another lifecycle operation is active")
	if IsSessionBusy(err) {
		return nil, false, nil
	}
	return lease, err == nil, err
}

type CueSignature struct {
	ExecutionID                                  string
	LatestPosition, Requests, Notices, Responses int64
}

func exactInteger(v any) (int64, bool) {
	switch n := v.(type) {
	case int:
		return int64(n), true
	case int8:
		return int64(n), true
	case int16:
		return int64(n), true
	case int32:
		return int64(n), true
	case int64:
		return n, true
	case uint:
		if uint64(n) <= math.MaxInt64 {
			return int64(n), true
		}
	case uint8:
		return int64(n), true
	case uint16:
		return int64(n), true
	case uint32:
		return int64(n), true
	case uint64:
		if n <= math.MaxInt64 {
			return int64(n), true
		}
	case json.Number:
		if strings.ContainsAny(n.String(), ".eE") {
			return 0, false
		}
		value, err := strconv.ParseInt(n.String(), 10, 64)
		return value, err == nil
	}
	return 0, false
}

func cueTotal(a, b, c int64) *big.Int {
	return new(big.Int).Add(new(big.Int).Add(big.NewInt(a), big.NewInt(b)), big.NewInt(c))
}

func CueArrival(previous any, sig CueSignature) bool {
	if sig.Requests == 0 && sig.Notices == 0 && sig.Responses == 0 {
		return false
	}
	prior, ok := previous.([]any)
	if !ok || len(prior) != 5 {
		return true
	}
	execution, ok := prior[0].(string)
	if !ok || execution != sig.ExecutionID {
		return true
	}
	var values [4]int64
	for i := range values {
		value, ok := exactInteger(prior[i+1])
		if !ok {
			return true
		}
		values[i] = value
	}
	return sig.LatestPosition > values[0] || cueTotal(sig.Requests, sig.Notices, sig.Responses).Cmp(cueTotal(values[1], values[2], values[3])) > 0
}

// WriteStateFile commits JSON through a private same-directory temporary file.
// The directory sync makes the rename durable; callers own session locking.
func WriteStateFile(path string, state any) (result error) {
	directory := filepath.Dir(path)
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return err
	}
	file, err := os.CreateTemp(directory, ".coordination-*")
	if err != nil {
		return err
	}
	pending := file.Name()
	defer func() {
		if file != nil {
			result = errors.Join(result, file.Close())
		}
		if err := os.Remove(pending); err != nil && !errors.Is(err, os.ErrNotExist) {
			result = errors.Join(result, err)
		}
	}()
	encoder := json.NewEncoder(file)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(state); err != nil {
		return err
	}
	if err := file.Sync(); err != nil {
		return err
	}
	closeErr := file.Close()
	file = nil
	if closeErr != nil {
		return closeErr
	}
	if err := os.Rename(pending, path); err != nil {
		return err
	}
	parent, err := os.OpenFile(directory, os.O_RDONLY|syscall.O_DIRECTORY, 0)
	if err != nil {
		return err
	}
	return errors.Join(parent.Sync(), parent.Close())
}
