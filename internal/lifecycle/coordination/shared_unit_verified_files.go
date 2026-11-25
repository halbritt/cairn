package coordination

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"slices"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/sys/unix"
)

// unitVerifiedFiles owns one observation's descriptors and build reads. Its
// observations confer no authority and must not cross a freshness boundary.
type unitVerifiedFiles struct {
	ctx       context.Context
	hashReads int
	bytesRead int64
	files     map[string]*unitVerifiedFile
	inodes    map[unitVerifiedInode]string
	closed    bool
}

type unitVerifiedInode struct{ dev, ino uint64 }

type unitVerifiedFile struct {
	file   *os.File
	stat   unix.Stat_t
	raw    []byte
	digest string
	// modes is the strictest parent-mode set this path was read under;
	// Recheck applies it rather than a looser or stricter default.
	modes []uint32
}

func newUnitVerifiedFiles(ctx context.Context) *unitVerifiedFiles {
	return &unitVerifiedFiles{ctx: ctx, files: map[string]*unitVerifiedFile{}, inodes: map[unitVerifiedInode]string{}}
}

func (p *unitVerifiedFiles) Read(path string, limit int64, binary bool) ([]byte, string, error) {
	return p.ReadOwnedModes(path, limit, binary, 0700)
}

func (p *unitVerifiedFiles) ReadOwnedModes(path string, limit int64, binary bool, modes ...uint32) ([]byte, string, error) {
	entry, err := p.readModes(path, limit, binary, modes...)
	if err != nil {
		return nil, "", errors.Join(err, p.Close())
	}
	if binary {
		return nil, entry.digest, nil
	}
	return bytes.Clone(entry.raw), entry.digest, nil
}

func (p *unitVerifiedFiles) read(path string, limit int64, binary bool) (*unitVerifiedFile, error) {
	return p.readModes(path, limit, binary, 0700)
}

func (p *unitVerifiedFiles) readModes(path string, limit int64, binary bool, modes ...uint32) (*unitVerifiedFile, error) {
	if p.closed {
		return nil, errors.New("verified file observation is closed")
	}
	if err := p.ctx.Err(); err != nil {
		return nil, err
	}
	if entry := p.files[path]; entry != nil {
		current, err := unitVerifiedPathStatModes(path, modes...)
		var held unix.Stat_t
		heldErr := unix.Fstat(int(entry.file.Fd()), &held)
		if err != nil || heldErr != nil || !unitVerifiedSame(entry.stat, current) || !unitVerifiedSame(entry.stat, held) {
			return nil, errors.Join(err, heldErr, fmt.Errorf("verified file identity changed: %s", path))
		}
		if err := unitVerifiedPolicy(held, limit, binary); err != nil {
			return nil, err
		}
		entry.modes = slices.DeleteFunc(entry.modes, func(m uint32) bool { return !slices.Contains(modes, m) })
		if !binary && entry.raw == nil {
			// A streamed executable later requested as data needs actual bytes;
			// its earlier digest alone cannot satisfy the new bounded reader.
			raw, digest, err := p.hash(entry.file, held, limit, true)
			if err != nil || digest != entry.digest {
				return nil, errors.Join(err, fmt.Errorf("verified file content changed: %s", path))
			}
			entry.raw = raw
		}
		return entry, nil
	}
	parent, name, err := unitVerifiedParentModes(path, modes...)
	if err != nil {
		return nil, err
	}
	fd, openErr := unix.Openat(parent, name, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)
	closeErr := unix.Close(parent)
	if openErr != nil {
		return nil, errors.Join(openErr, closeErr)
	}
	file := os.NewFile(uintptr(fd), path)
	if closeErr != nil {
		return nil, errors.Join(closeErr, file.Close())
	}
	var before unix.Stat_t
	if err := unix.Fstat(fd, &before); err != nil {
		return nil, errors.Join(err, file.Close())
	}
	if err := unitVerifiedPolicy(before, limit, binary); err != nil {
		return nil, errors.Join(err, file.Close())
	}
	identity := unitVerifiedInode{uint64(before.Dev), before.Ino}
	if old := p.inodes[identity]; old != "" && old != path {
		return nil, errors.Join(fmt.Errorf("verified file hardlink alias: %s and %s", old, path), file.Close())
	}
	raw, digest, err := p.hash(file, before, limit, !binary || before.Size <= 2<<20)
	if err != nil {
		return nil, errors.Join(err, file.Close())
	}
	current, err := unitVerifiedPathStatModes(path, modes...)
	if err != nil || !unitVerifiedSame(before, current) {
		return nil, errors.Join(err, fmt.Errorf("verified file path changed: %s", path), file.Close())
	}
	entry := &unitVerifiedFile{file: file, stat: before, raw: raw, digest: digest, modes: slices.Clone(modes)}
	p.files[path] = entry
	p.inodes[identity] = path
	return entry, nil
}

func (p *unitVerifiedFiles) hash(file *os.File, before unix.Stat_t, limit int64, keep bool) ([]byte, string, error) {
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return nil, "", err
	}
	if err := p.ctx.Err(); err != nil {
		return nil, "", err
	}
	p.hashReads++
	var reader io.Reader = unitVerifiedReader{ctx: p.ctx, file: file}
	if limit >= 0 {
		reader = io.LimitReader(reader, limit+1)
	}
	hash := sha256.New()
	var raw []byte
	var count int64
	var err error
	if keep {
		raw, err = io.ReadAll(reader)
		count = int64(len(raw))
		_, _ = hash.Write(raw)
	} else {
		count, err = io.Copy(hash, reader)
	}
	p.bytesRead += count
	var after unix.Stat_t
	statErr := unix.Fstat(int(file.Fd()), &after)
	if err != nil || statErr != nil || count != before.Size || !unitVerifiedSame(before, after) || limit >= 0 && count > limit || p.ctx.Err() != nil {
		return nil, "", errors.Join(err, statErr, p.ctx.Err(), fmt.Errorf("verified file changed while reading: %s", file.Name()))
	}
	return raw, hex.EncodeToString(hash.Sum(nil)), nil
}

// Recheck reads every held file's bytes again, including read-only images and
// large traces. Mode and stat identity cannot rule out an existing writable
// shared mapping or an in-place write on a coarse-timestamp filesystem.
func (p *unitVerifiedFiles) Recheck() error {
	if p.closed {
		return errors.New("verified file observation is closed")
	}
	for _, path := range slices.Sorted(maps.Keys(p.files)) {
		if err := p.ctx.Err(); err != nil {
			return errors.Join(err, p.Close())
		}
		entry := p.files[path]
		current, err := unitVerifiedPathStatModes(path, entry.modes...)
		var held unix.Stat_t
		heldErr := unix.Fstat(int(entry.file.Fd()), &held)
		if err != nil || heldErr != nil || !unitVerifiedSame(entry.stat, current) || !unitVerifiedSame(entry.stat, held) {
			return errors.Join(err, heldErr, fmt.Errorf("verified file identity changed: %s", path), p.Close())
		}
		_, digest, err := p.hash(entry.file, entry.stat, -1, false)
		if err != nil || digest != entry.digest {
			return errors.Join(err, fmt.Errorf("verified file content changed: %s", path), p.Close())
		}
		current, err = unitVerifiedPathStatModes(path, entry.modes...)
		if err != nil || !unitVerifiedSame(entry.stat, current) {
			return errors.Join(err, fmt.Errorf("verified file path changed: %s", path), p.Close())
		}
	}
	if err := p.ctx.Err(); err != nil {
		return errors.Join(err, p.Close())
	}
	return nil
}

func (p *unitVerifiedFiles) Close() error {
	if p.closed {
		return nil
	}
	p.closed = true
	var err error
	for _, entry := range p.files {
		err = errors.Join(err, entry.file.Close())
	}
	p.files = nil
	p.inodes = nil
	return err
}

type unitVerifiedReader struct {
	ctx  context.Context
	file *os.File
}

func (r unitVerifiedReader) Read(raw []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.file.Read(raw)
}

func unitVerifiedPolicy(s unix.Stat_t, limit int64, binary bool) error {
	if s.Mode&unix.S_IFMT != unix.S_IFREG || s.Uid != uint32(os.Getuid()) || s.Mode&0022 != 0 || binary && (s.Mode&0100 == 0 || s.Mode&07000 != 0) {
		return &value.Error{Class: "ValueError", Message: "file"}
	}
	if limit >= 0 && s.Size > limit {
		return &value.Error{Class: "ValueError", Message: "size"}
	}
	return nil
}

func unitVerifiedSame(a, b unix.Stat_t) bool {
	return a.Dev == b.Dev && a.Ino == b.Ino && a.Mode == b.Mode && a.Uid == b.Uid && a.Gid == b.Gid &&
		a.Size == b.Size && a.Mtim == b.Mtim && a.Ctim == b.Ctim && a.Nlink == b.Nlink
}

func unitVerifiedParent(path string) (int, string, error) {
	return unitVerifiedParentModes(path, 0700)
}

func unitVerifiedParentModes(path string, modes ...uint32) (int, string, error) {
	if !compatibilityCanonical(path) {
		return -1, "", &value.Error{Class: "ValueError", Message: "path"}
	}
	parts := strings.Split(path[1:], "/")
	parent, err := unix.Open("/", unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return -1, "", err
	}
	for _, name := range parts[:len(parts)-1] {
		child, err := unix.Openat(parent, name, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		closeErr := unix.Close(parent)
		if err != nil {
			return -1, "", errors.Join(err, closeErr)
		}
		parent = child
		var s unix.Stat_t
		err = unix.Fstat(parent, &s)
		if err != nil || closeErr != nil || s.Uid != 0 && s.Uid != uint32(os.Getuid()) || s.Mode&0022 != 0 && !(s.Uid == 0 && s.Mode&unix.S_ISVTX != 0) {
			return -1, "", errors.Join(err, closeErr, &value.Error{Class: "ValueError", Message: "ancestor"}, unix.Close(parent))
		}
	}
	var s unix.Stat_t
	if err := unix.Fstat(parent, &s); err != nil || s.Uid != uint32(os.Getuid()) || !slices.Contains(modes, s.Mode&07777) {
		return -1, "", errors.Join(err, &value.Error{Class: "ValueError", Message: "private directory"}, unix.Close(parent))
	}
	return parent, parts[len(parts)-1], nil
}

func unitVerifiedPathStat(path string) (unix.Stat_t, error) {
	return unitVerifiedPathStatModes(path, 0700)
}

func unitVerifiedPathStatModes(path string, modes ...uint32) (unix.Stat_t, error) {
	parent, name, err := unitVerifiedParentModes(path, modes...)
	if err != nil {
		return unix.Stat_t{}, err
	}
	var s unix.Stat_t
	err = unix.Fstatat(parent, name, &s, unix.AT_SYMLINK_NOFOLLOW)
	return s, errors.Join(err, unix.Close(parent))
}
