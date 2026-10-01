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

// ContextAdopter is the store side of adopting a historical context file.
type ContextAdopter interface {
	CheckContextAdoption(context.Context, core.AdoptContextRequest) (*core.AdoptedContext, error)
	AdoptManagedContext(context.Context, core.AdoptContextRequest) (core.AdoptedContext, error)
}

const markerName = ".cairn-context-owner"

// Filesystem magic numbers (statfs f_type) of network or remote-backed
// filesystems. Their locking and unlink semantics are not the local ones that
// managed custody relies on, so adoption refuses them.
var remoteFilesystems = map[int64]bool{
	0x6969:     true, // NFS
	0x517B:     true, // SMB
	0xFF534D42: true, // CIFS
	0xFE534D42: true, // SMB2
	0xC36400:   true, // Ceph
	0x5346414F: true, // AFS
	0x73757245: true, // Coda
	0x01021997: true, // 9P
}

func remoteFilesystem(kind int64) bool { return remoteFilesystems[kind] }

// statfs is replaceable so a test can present a network filesystem.
var statfs = func(fd int, stat *syscall.Statfs_t) error { return syscall.Fstatfs(fd, stat) }

type observedFile struct {
	data     []byte
	device   string
	inode    string
	modified time.Time
}

func refuse(code, message string) error { return &core.Error{Code: code, Message: message} }

// readPrivateFile reads one regular, private, single-link file of the locked
// directory without following a final symlink, and proves it did not change
// while it was read.
func readPrivateFile(locked *directory, name string, limit int64) (observedFile, error) {
	before, err := locked.root.Lstat(name)
	if err != nil {
		return observedFile{}, err
	}
	if !before.Mode().IsRegular() {
		return observedFile{}, refuse("ARTIFACT_UNSAFE", name+" must be a regular file, not a link or directory")
	}
	file, err := locked.root.OpenFile(name, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return observedFile{}, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return observedFile{}, err
	}
	owner, ok := info.Sys().(*syscall.Stat_t)
	switch {
	case !info.Mode().IsRegular() || !ok:
		return observedFile{}, refuse("ARTIFACT_UNSAFE", name+" must be a regular file")
	case !os.SameFile(before, info):
		return observedFile{}, refuse("ARTIFACT_CHANGED", name+" changed while it was opened")
	case owner.Uid != uint32(os.Geteuid()) || info.Mode().Perm()&0077 != 0:
		return observedFile{}, refuse("ARTIFACT_UNSAFE", name+" must be private and owned by this user")
	case owner.Nlink != 1:
		return observedFile{}, refuse("ARTIFACT_UNSAFE", name+" has another hard link; removing this name would not remove its bytes")
	case info.Size() > limit:
		return observedFile{}, refuse("ARTIFACT_UNSAFE", name+" is larger than this operation reads")
	}
	data, err := io.ReadAll(io.LimitReader(file, info.Size()+1))
	if err != nil {
		return observedFile{}, err
	}
	after, err := file.Stat()
	if err != nil {
		return observedFile{}, err
	}
	again, lstatErr := locked.root.Lstat(name)
	if int64(len(data)) != info.Size() || after.Size() != info.Size() || !after.ModTime().Equal(info.ModTime()) || lstatErr != nil || !os.SameFile(info, again) {
		return observedFile{}, refuse("ARTIFACT_CHANGED", name+" changed while it was read")
	}
	return observedFile{data: data, device: strconv.FormatUint(uint64(owner.Dev), 10), inode: strconv.FormatUint(owner.Ino, 10), modified: info.ModTime().UTC()}, nil
}

// AdoptContext brings the context.txt of one explicitly named historical run
// directory into managed custody. It does not search for files, and it never
// changes context.txt, outcome.json or any other file in the directory; the
// only filesystem change is a private ownership marker, created last and only
// after the store reports that the file could be adopted.
//
// The directory must be this user's private local run directory named by the
// receipt UUID, reached through no symlink. The file must be the retained
// canonical package of a completed run this operator owns. A retry after a
// marker was created but the registration was not recorded reuses that marker;
// an identical completed adoption returns unchanged.
func AdoptContext(ctx context.Context, store ContextAdopter, receiptID, path string) (core.AdoptedContext, error) {
	if parsed, err := uuid.Parse(receiptID); err != nil || parsed == uuid.Nil || parsed.String() != receiptID {
		return core.AdoptedContext{}, refuse("INVALID_REQUEST", "adoption requires a canonical receipt UUID")
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return core.AdoptedContext{}, err
	}
	if filepath.Base(absolute) != receiptID {
		return core.AdoptedContext{}, refuse("INVALID_REQUEST", "the run directory must be named by the receipt UUID")
	}
	canonical, err := filepath.EvalSymlinks(absolute)
	if errors.Is(err, os.ErrNotExist) {
		return core.AdoptedContext{}, refuse("NOT_FOUND", "the run directory does not exist")
	}
	if err != nil {
		return core.AdoptedContext{}, err
	}
	if canonical != absolute {
		return core.AdoptedContext{}, refuse("ARTIFACT_CHANGED", "the run directory path must be canonical; it passes through a symlink")
	}
	locked, err := lockDirectory(ctx, absolute)
	if err != nil {
		return core.AdoptedContext{}, err
	}
	defer func() { _ = locked.close() }()
	var stat syscall.Statfs_t
	if err = statfs(int(locked.file.Fd()), &stat); err != nil {
		return core.AdoptedContext{}, err
	}
	if remoteFilesystem(int64(stat.Type)) {
		return core.AdoptedContext{}, refuse("ARTIFACT_UNSAFE", "the run directory is on a network filesystem; adoption accepts only local custody")
	}
	file, err := readPrivateFile(locked, "context.txt", core.MaxAdoptedContextBytes)
	if errors.Is(err, os.ErrNotExist) {
		return core.AdoptedContext{}, refuse("NOT_FOUND", "the run directory has no context.txt to adopt")
	}
	if err != nil {
		return core.AdoptedContext{}, err
	}
	digest := sha256.Sum256(file.data)
	request := core.AdoptContextRequest{
		RequestID: uuid.NewString(), ReceiptID: receiptID, Directory: absolute, DirectoryDevice: locked.device, DirectoryInode: locked.inode,
		ObservedBytes: int64(len(file.data)), ObservedSHA256: hex.EncodeToString(digest[:]),
		FileDevice: file.device, FileInode: file.inode, FileModifiedAt: file.modified,
	}
	// An existing marker is read and judged before any store call. A marker is
	// never rewritten or removed here.
	marker, err := readPrivateFile(locked, markerName, 128)
	switch {
	case err == nil:
		parsed, parseErr := uuid.Parse(string(marker.data))
		if parseErr != nil || parsed == uuid.Nil || parsed.String() != string(marker.data) {
			return core.AdoptedContext{}, refuse("ARTIFACT_UNSAFE", "the existing ownership marker is not a Cairn marker; nothing was adopted")
		}
		request.OwnershipID = string(marker.data)
	case !errors.Is(err, os.ErrNotExist):
		return core.AdoptedContext{}, err
	}
	// The store's refusals, such as a different package or deleted payload, are
	// decided before the directory is touched.
	existing, err := store.CheckContextAdoption(ctx, request)
	if err != nil {
		return core.AdoptedContext{}, err
	}
	if existing != nil {
		if request.OwnershipID != existing.OwnershipID {
			return core.AdoptedContext{}, refuse("ARTIFACT_CHANGED", "this adoption is recorded but its ownership marker is missing; no file was changed")
		}
		existing.AlreadyAdopted = true
		return *existing, nil
	}
	if request.OwnershipID == "" {
		request.OwnershipID = uuid.NewString()
		created, createErr := locked.root.OpenFile(markerName, os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, 0600)
		if createErr != nil {
			return core.AdoptedContext{}, createErr
		}
		_, writeErr := created.WriteString(request.OwnershipID)
		if err = errors.Join(writeErr, created.Sync(), created.Close(), locked.file.Sync()); err != nil {
			return core.AdoptedContext{}, err
		}
	}
	return store.AdoptManagedContext(ctx, request)
}
