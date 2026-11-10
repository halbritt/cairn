// Package install stages immutable lifecycle artifacts without selecting a runtime.
package install

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"

	"golang.org/x/sys/unix"
)

// Binary is a staged executable descriptor, not admission or an active selection.
type Binary struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}

// StageBinary publishes root/sha256/cairn with modes 0700/0500. Root must already
// be a private directory owned by this UID. Existing artifacts are verified and
// reused, never replaced. A failure after publication may leave the named artifact;
// the returned error identifies that outcome. This function never executes it.
func StageBinary(ctx context.Context, root, source, digest string) (result Binary, err error) {
	if os.Getuid() != os.Geteuid() {
		return result, errors.New("staging refuses a changed effective UID")
	}
	if !utf8.ValidString(root) {
		return result, errors.New("staging root must be valid UTF-8 for its JSON descriptor")
	}
	pin, decodeErr := hex.DecodeString(digest)
	if decodeErr != nil || len(pin) != sha256.Size || hex.EncodeToString(pin) != digest {
		return result, errors.New("binary SHA-256 must be 64 lowercase hexadecimal characters")
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	stageDir, err := openDirectory(root, true)
	if err != nil {
		return result, fmt.Errorf("staging root: %w", err)
	}
	defer func() { err = errors.Join(err, stageDir.Close()) }()
	if err := unix.Flock(int(stageDir.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		return result, fmt.Errorf("staging root lock: %w", err)
	}
	parent, err := openDirectory(filepath.Dir(source), false)
	if err != nil || !canonical(source) {
		if parent != nil {
			err = errors.Join(err, parent.Close())
		}
		return result, fmt.Errorf("binary source directory: %w", errors.Join(err, errors.New("canonical absolute source required")))
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	fd, err := unix.Openat(int(parent.Fd()), filepath.Base(source), unix.O_RDONLY|unix.O_NONBLOCK|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return result, fmt.Errorf("open binary source: %w", err)
	}
	input := os.NewFile(uintptr(fd), source)
	defer func() { err = errors.Join(err, input.Close()) }()
	before, err := regularExecutable(input, false)
	if err != nil {
		return result, fmt.Errorf("binary source: %w", err)
	}

	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return result, fmt.Errorf("staging name: %w", err)
	}
	temporary := ".stage-" + hex.EncodeToString(nonce[:])
	anchored, err := os.OpenRoot(root)
	if err != nil {
		return result, fmt.Errorf("open staging namespace: %w", err)
	}
	defer func() { err = errors.Join(err, anchored.Close()) }()
	anchoredInfo, err := anchored.Stat(".")
	lockedInfo, statErr := stageDir.Stat()
	if err != nil || statErr != nil || !os.SameFile(anchoredInfo, lockedInfo) {
		return result, errors.Join(err, statErr, errors.New("staging root changed"))
	}
	if err := unix.Mkdirat(int(stageDir.Fd()), temporary, 0700); err != nil {
		return result, fmt.Errorf("create private stage: %w", err)
	}
	defer func() {
		if temporary != "" {
			err = errors.Join(err, anchored.RemoveAll(temporary))
		}
	}()
	output, err := anchored.OpenFile(temporary+"/cairn", os.O_WRONLY|os.O_CREATE|os.O_EXCL|unix.O_NOFOLLOW, 0600)
	if err != nil {
		return result, fmt.Errorf("create staged executable: %w", err)
	}
	hash := sha256.New()
	_, copyErr := io.Copy(io.MultiWriter(output, hash), contextReader{ctx, input})
	after, sourceErr := regularExecutable(input, false)
	if copyErr == nil && sourceErr == nil && *before != *after {
		sourceErr = errors.New("binary source changed during copy")
	}
	if copyErr == nil && sourceErr == nil && hex.EncodeToString(hash.Sum(nil)) != digest {
		sourceErr = errors.New("binary source SHA-256 mismatch")
	}
	if err := errors.Join(copyErr, sourceErr); err != nil {
		return result, errors.Join(err, output.Close())
	}
	if err := output.Chmod(0500); err != nil {
		return result, errors.Join(fmt.Errorf("seal executable mode: %w", err), output.Close())
	}
	if err := errors.Join(output.Sync(), output.Close()); err != nil {
		return result, fmt.Errorf("sync staged executable: %w", err)
	}
	private, err := anchored.Open(temporary)
	if err != nil {
		return result, err
	}
	if err := errors.Join(private.Sync(), private.Close()); err != nil {
		return result, fmt.Errorf("sync private stage: %w", err)
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	if err := unix.Renameat2(int(stageDir.Fd()), temporary, int(stageDir.Fd()), digest, unix.RENAME_NOREPLACE); err != nil {
		if !errors.Is(err, unix.EEXIST) {
			return result, fmt.Errorf("publish immutable executable: %w", err)
		}
	} else {
		temporary = ""
	}
	result = Binary{Path: filepath.Join(root, digest, "cairn"), SHA256: digest}
	if err := verifyExisting(ctx, anchored, digest); err != nil {
		return result, fmt.Errorf("artifact exists at %s but readback failed: %w", result.Path, err)
	}
	if temporary != "" {
		if err := anchored.RemoveAll(temporary); err != nil {
			return result, fmt.Errorf("remove unpublished duplicate: %w", err)
		}
		temporary = ""
	}
	if err := stageDir.Sync(); err != nil {
		return result, fmt.Errorf("executable exists at %s but staging-root sync failed: %w", result.Path, err)
	}
	if err := verifyRootPath(root, stageDir); err != nil {
		return result, fmt.Errorf("executable published but staging-root pathname changed: %w", err)
	}
	return result, nil
}

func verifyRootPath(path string, locked *os.File) (err error) {
	current, err := openDirectory(path, true)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, current.Close()) }()
	actual, err := current.Stat()
	want, wantErr := locked.Stat()
	if err != nil || wantErr != nil || !os.SameFile(actual, want) {
		return errors.Join(err, wantErr, errors.New("staging-root identity differs"))
	}
	return nil
}

func canonical(path string) bool {
	return filepath.IsAbs(path) && filepath.Clean(path) == path
}

// Walk with descriptor-relative, non-following opens. A trusted owner's sticky
// ancestor (such as a private namespace's /tmp) protects owned child names.
func openDirectory(path string, private bool) (*os.File, error) {
	if !canonical(path) {
		return nil, errors.New("canonical absolute directory required")
	}
	fd, err := unix.Open("/", unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	checked := "/"
	for _, component := range strings.Split(strings.TrimPrefix(path, "/"), "/") {
		if component == "" {
			continue
		}
		next, openErr := unix.Openat(fd, component, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		closeErr := unix.Close(fd)
		if err := errors.Join(openErr, closeErr); err != nil {
			if next >= 0 {
				err = errors.Join(err, unix.Close(next))
			}
			return nil, err
		}
		fd = next
		checked = filepath.Join(checked, component)
		var st unix.Stat_t
		if err := unix.Fstat(fd, &st); err != nil {
			return nil, errors.Join(err, unix.Close(fd))
		}
		if (st.Uid != uint32(os.Geteuid()) && st.Uid != 0) || (st.Mode&0022 != 0 && st.Mode&unix.S_ISVTX == 0) {
			return nil, errors.Join(fmt.Errorf("unsafe directory %s: uid %d, mode %#o", checked, st.Uid, st.Mode&07777), unix.Close(fd))
		}
	}
	file := os.NewFile(uintptr(fd), path)
	if private {
		var st unix.Stat_t
		if err := unix.Fstat(fd, &st); err != nil {
			return nil, errors.Join(err, file.Close())
		}
		if st.Uid != uint32(os.Geteuid()) || st.Mode&07777 != 0700 {
			return nil, errors.Join(errors.New("staging directory must be owned by this UID with mode 0700"), file.Close())
		}
	}
	return file, nil
}

func regularExecutable(file *os.File, staged bool) (*unix.Stat_t, error) {
	var st unix.Stat_t
	if err := unix.Fstat(int(file.Fd()), &st); err != nil {
		return nil, err
	}
	if st.Mode&unix.S_IFMT != unix.S_IFREG || st.Nlink != 1 || st.Uid != uint32(os.Geteuid()) || st.Mode&0022 != 0 || st.Mode&0100 == 0 {
		return nil, errors.New("executable must be a singly linked regular file owned by this UID, executable and not writable by others")
	}
	if staged && st.Mode&07777 != 0500 {
		return nil, errors.New("immutable executable must have mode 0500")
	}
	// Reading changes atime; it is not a content or identity change.
	st.Atim = unix.Timespec{}
	return &st, nil
}

func verifyExisting(ctx context.Context, root *os.Root, digest string) (err error) {
	directory, err := root.OpenRoot(digest)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, directory.Close()) }()
	info, err := root.Lstat(digest)
	if err != nil || !info.IsDir() || info.Mode().Perm() != 0700 {
		return errors.Join(err, errors.New("invalid immutable directory"))
	}
	opened, err := directory.Stat(".")
	if err != nil || !os.SameFile(info, opened) {
		return errors.Join(err, errors.New("immutable directory changed"))
	}
	listing, err := directory.Open(".")
	if err != nil {
		return err
	}
	var st unix.Stat_t
	statErr := unix.Fstat(int(listing.Fd()), &st)
	names, listErr := listing.Readdirnames(2)
	if listErr != nil && !errors.Is(listErr, io.EOF) {
		return errors.Join(listErr, listing.Close())
	}
	if err := errors.Join(statErr, listing.Close()); err != nil {
		return err
	}
	if st.Uid != uint32(os.Geteuid()) || st.Mode&07777 != 0700 || len(names) != 1 || names[0] != "cairn" {
		return errors.New("immutable directory must be private, owned and contain only cairn")
	}
	file, err := directory.OpenFile("cairn", os.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, file.Close()) }()
	before, err := regularExecutable(file, true)
	if err != nil {
		return err
	}
	hash := sha256.New()
	if _, err := io.Copy(hash, contextReader{ctx, file}); err != nil {
		return err
	}
	after, err := regularExecutable(file, true)
	if err != nil || *before != *after || hex.EncodeToString(hash.Sum(nil)) != digest {
		return errors.Join(err, errors.New("immutable executable differs from its pin"))
	}
	return nil
}

type contextReader struct {
	ctx context.Context
	in  io.Reader
}

func (r contextReader) Read(p []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.in.Read(p)
}
