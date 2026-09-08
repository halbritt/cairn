// Package artifacts owns Cairn's registered local context-file slots. Database
// authorization and observed filesystem effects have separate commit points.
package artifacts

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"syscall"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

type directory struct {
	file   *os.File
	root   *os.Root
	device string
	inode  string
}

type ContextRegistrar interface {
	RegisterManagedContext(context.Context, core.ManagedContextRequest) (core.ManagedContext, error)
}

func (d *directory) close() error { return errors.Join(d.root.Close(), d.file.Close()) }

func lockDirectory(ctx context.Context, path string) (*directory, error) {
	canonical, err := filepath.EvalSymlinks(path)
	if err != nil {
		return nil, err
	}
	if canonical != path {
		return nil, &core.Error{Code: "ARTIFACT_CHANGED", Message: "registered directory path now contains a symlink"}
	}
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, err
	}
	closeFailure := func(cause error) (*directory, error) { return nil, errors.Join(cause, file.Close()) }
	stat, err := file.Stat()
	if err != nil {
		return closeFailure(err)
	}
	owner, ok := stat.Sys().(*syscall.Stat_t)
	if !ok || owner.Uid != uint32(os.Geteuid()) || stat.Mode().Perm()&0077 != 0 {
		return closeFailure(&core.Error{Code: "ARTIFACT_UNSAFE", Message: "managed run directory must remain private and owned by this user"})
	}
	for {
		err = syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			break
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) {
			return closeFailure(err)
		}
		select {
		case <-ctx.Done():
			return closeFailure(ctx.Err())
		case <-time.After(10 * time.Millisecond):
		}
	}
	root, err := os.OpenRoot(path)
	if err != nil {
		return closeFailure(err)
	}
	rootInfo, err := root.Stat(".")
	if err != nil {
		return closeFailure(errors.Join(err, root.Close()))
	}
	if rootInfo.Mode().Perm()&0077 != 0 {
		return closeFailure(errors.Join(&core.Error{Code: "ARTIFACT_UNSAFE", Message: "managed directory permissions changed while obtaining its lock"}, root.Close()))
	}
	if !os.SameFile(stat, rootInfo) {
		return closeFailure(errors.Join(&core.Error{Code: "ARTIFACT_CHANGED", Message: "managed directory changed while obtaining its lock"}, root.Close()))
	}
	return &directory{file, root, strconv.FormatUint(uint64(owner.Dev), 10), strconv.FormatUint(owner.Ino, 10)}, nil
}

// WriteContext reserves a new private run directory. No context bytes are
// written until registration commits. The directory remains as the stable lock
// identity even after its context slot is purged.
func WriteContext(ctx context.Context, store ContextRegistrar, pkg core.Package, parent string) (path string, err error) {
	parsed, parseErr := uuid.Parse(pkg.ReceiptID)
	if parseErr != nil || parsed.String() != pkg.ReceiptID {
		return "", &core.Error{Code: "INVALID_REQUEST", Message: "context requires a canonical receipt UUID"}
	}
	if err = os.MkdirAll(parent, 0700); err != nil {
		return "", err
	}
	parent, err = filepath.Abs(parent)
	if err != nil {
		return "", err
	}
	parent, err = filepath.EvalSymlinks(parent)
	if err != nil {
		return "", err
	}
	path = filepath.Join(parent, pkg.ReceiptID)
	if err = os.Mkdir(path, 0700); err != nil {
		return path, err
	}
	locked, err := lockDirectory(ctx, path)
	if err != nil {
		return path, err
	}
	defer func() { err = errors.Join(err, locked.close()) }()
	ownership := uuid.NewString()
	marker, err := locked.root.OpenFile(".cairn-context-owner", os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return path, err
	}
	_, writeMarkerErr := marker.WriteString(ownership)
	if err = errors.Join(writeMarkerErr, marker.Sync(), marker.Close(), locked.file.Sync()); err != nil {
		return path, err
	}
	parentFile, err := os.Open(parent)
	if err != nil {
		return path, err
	}
	if err = errors.Join(parentFile.Sync(), parentFile.Close()); err != nil {
		return path, err
	}
	registered, err := store.RegisterManagedContext(ctx, core.ManagedContextRequest{OwnershipID: ownership, RequestID: pkg.ReceiptID, ReceiptID: pkg.ReceiptID, Directory: path, DirectoryDevice: locked.device, DirectoryInode: locked.inode})
	if err != nil {
		return path, err
	}
	rendered, err := pkg.Render()
	if err != nil {
		return path, err
	}
	sum := sha256.Sum256([]byte(rendered))
	if hex.EncodeToString(sum[:]) != registered.BodySHA256 {
		return path, &core.Error{Code: "INTEGRITY_FAILURE", Message: "context file bytes do not match the registered canonical package"}
	}
	file, err := locked.root.OpenFile("context.txt", os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return path, err
	}
	_, writeErr := file.WriteString(rendered)
	err = errors.Join(writeErr, file.Sync(), file.Close())
	if err != nil {
		return path, err
	}
	return path, locked.file.Sync()
}

func PurgeDeletion(ctx context.Context, store *core.Store, id string) (core.Deletion, error) {
	status, err := store.PurgeDeletion(ctx, id)
	if err != nil {
		return core.Deletion{}, err
	}
	for _, effect := range status.Effects {
		if effect.TargetType != "managed_context" || (effect.Status != "pending" && effect.Status != "failed") {
			continue
		}
		target, err := store.ContextPurgeTarget(ctx, id, effect.TargetID)
		if err != nil {
			return status, err
		}
		if err = purgeContext(ctx, store, id, target); err != nil {
			return status, err
		}
	}
	return store.DeletionStatus(ctx, id)
}

func purgeContext(ctx context.Context, store *core.Store, id string, target core.ContextPurgeTarget) (err error) {
	locked, lockErr := lockDirectory(ctx, target.Directory)
	if lockErr != nil {
		return recordPurge(ctx, store, id, target, lockErr)
	}
	defer func() { err = errors.Join(err, locked.close()) }()
	// Recheck after obtaining the file lock: another worker may have completed
	// while this one waited. A stale scan must not repeat the external effect.
	target, err = store.ContextPurgeTarget(ctx, id, target.ReceiptID)
	if err != nil {
		return err
	}
	if target.Status != "pending" && target.Status != "failed" {
		return nil
	}
	if locked.device != target.DirectoryDevice || locked.inode != target.DirectoryInode {
		return recordPurge(ctx, store, id, target, &core.Error{Code: "ARTIFACT_CHANGED", Message: "registered run directory was replaced; no file removed"})
	}
	marker, markerErr := locked.root.OpenFile(".cairn-context-owner", os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if markerErr != nil {
		return recordPurge(ctx, store, id, target, &core.Error{Code: "ARTIFACT_CHANGED", Message: "registered ownership marker is unavailable", Cause: markerErr})
	}
	markerInfo, statErr := marker.Stat()
	if statErr != nil {
		return recordPurge(ctx, store, id, target, errors.Join(statErr, marker.Close()))
	}
	if !markerInfo.Mode().IsRegular() || markerInfo.Mode().Perm()&0077 != 0 {
		return recordPurge(ctx, store, id, target, errors.Join(&core.Error{Code: "ARTIFACT_UNSAFE", Message: "ownership marker must remain a private regular file"}, marker.Close()))
	}
	markerBytes, readMarkerErr := io.ReadAll(io.LimitReader(marker, 128))
	if markerErr = errors.Join(readMarkerErr, marker.Close()); markerErr != nil {
		return recordPurge(ctx, store, id, target, markerErr)
	}
	if string(markerBytes) != target.OwnershipID {
		return recordPurge(ctx, store, id, target, &core.Error{Code: "ARTIFACT_CHANGED", Message: "registered ownership marker changed; no context removed"})
	}
	info, readErr := locked.root.Lstat("context.txt")
	if readErr == nil {
		if info.IsDir() {
			return recordPurge(ctx, store, id, target, &core.Error{Code: "ARTIFACT_UNSAFE", Message: "reserved context slot is a directory; recursive deletion is refused"})
		}
		// Unlinkat removes only the reserved slot, including a substituted symlink.
		// It never follows that symlink to delete a file elsewhere.
		if err = syscall.Unlinkat(int(locked.file.Fd()), "context.txt"); err != nil {
			return recordPurge(ctx, store, id, target, err)
		}
	} else if !errors.Is(readErr, os.ErrNotExist) {
		return recordPurge(ctx, store, id, target, readErr)
	}
	return recordPurge(ctx, store, id, target, locked.file.Sync())
}
func recordPurge(ctx context.Context, store *core.Store, id string, target core.ContextPurgeTarget, cause error) error {
	code := ""
	if cause != nil {
		code = core.Code(cause)
	}
	_, err := store.RecordContextPurge(ctx, core.ContextPurgeResult{RequestID: uuid.NewString(), DeletionID: id, ReceiptID: target.ReceiptID, ErrorCode: code})
	if err != nil {
		return &core.Error{Code: "PURGE_UNRECORDED", Message: "context purge observation could not be retained; inspect deletion-status and retry", Cause: errors.Join(cause, err)}
	}
	return cause
}
