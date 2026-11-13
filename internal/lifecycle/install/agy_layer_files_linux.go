package install

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"

	"golang.org/x/sys/unix"
)

const agySettingsLimit = 1024 * 1024

type agyDirectoryID struct {
	Dev, Ino  uint64
	Mode, UID uint32
}

type agyFileID struct {
	Dev, Ino, Links  uint64
	Mode, UID, GID   uint32
	Size             int64
	Modified, Change unix.Timespec
}

type agyLayerSnapshot struct {
	Root, Relative string
	Directories    map[string]agyDirectoryID
	Present        bool
	File           agyFileID
	Raw            []byte
}

func agyDirectoryIdentity(file *os.File) (agyDirectoryID, error) {
	var stat unix.Stat_t
	if err := unix.Fstat(int(file.Fd()), &stat); err != nil {
		return agyDirectoryID{}, err
	}
	if stat.Mode&unix.S_IFMT != unix.S_IFDIR || stat.Mode&0500 != 0500 {
		return agyDirectoryID{}, errors.New("Agy directory is unreadable or not a directory")
	}
	return agyDirectoryID{Dev: uint64(stat.Dev), Ino: stat.Ino, Mode: stat.Mode, UID: stat.Uid}, nil
}

func agyFileIdentity(file *os.File) (agyFileID, error) {
	var stat unix.Stat_t
	if err := unix.Fstat(int(file.Fd()), &stat); err != nil {
		return agyFileID{}, err
	}
	if stat.Mode&unix.S_IFMT != unix.S_IFREG || stat.Uid != uint32(os.Geteuid()) || stat.Nlink != 1 ||
		stat.Mode&0022 != 0 || stat.Mode&0400 == 0 || stat.Mode&07000 != 0 || stat.Size > agySettingsLimit {
		return agyFileID{}, errors.New("Agy settings must be readable owner-controlled regular bytes without aliases")
	}
	return agyFileID{Dev: uint64(stat.Dev), Ino: stat.Ino, Links: uint64(stat.Nlink), Mode: stat.Mode, UID: stat.Uid, GID: stat.Gid,
		Size: stat.Size, Modified: stat.Mtim, Change: stat.Ctim}, nil
}

func agyReadFile(parent *os.File, name string) ([]byte, agyFileID, error) {
	fd, err := unix.Openat(int(parent.Fd()), name, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, agyFileID{}, err
	}
	f := os.NewFile(uintptr(fd), name)
	before, err := agyFileIdentity(f)
	if err != nil {
		return nil, before, errors.Join(err, f.Close())
	}
	raw, readErr := io.ReadAll(io.LimitReader(f, agySettingsLimit+1))
	after, statErr := agyFileIdentity(f)
	err = errors.Join(readErr, statErr, f.Close())
	if err != nil || before != after || int64(len(raw)) != before.Size {
		return nil, before, errors.Join(err, errors.New("Agy settings changed during read"))
	}
	return raw, before, nil
}

func inspectAgyLayer(root, relative string) (snapshot agyLayerSnapshot, err error) {
	snapshot = agyLayerSnapshot{Root: root, Relative: relative, Directories: map[string]agyDirectoryID{}}
	if !canonical(root) || os.Getuid() != os.Geteuid() {
		return snapshot, errors.New("Agy roots require canonical absolute paths and unchanged UID")
	}
	// Retain every named ancestor identity, not only a final symlink-free FD.
	for path := root; ; path = filepath.Dir(path) {
		file, e := openDirectory(path, false)
		if e != nil {
			return snapshot, e
		}
		id, e := agyDirectoryIdentity(file)
		err = errors.Join(e, file.Close())
		if err != nil {
			return snapshot, err
		}
		if path == root && id.UID != uint32(os.Geteuid()) {
			return snapshot, errors.New("Agy root must belong to the current UID")
		}
		snapshot.Directories[path] = id
		if path == "/" {
			break
		}
	}
	parent, err := openDirectory(root, false)
	if err != nil {
		return snapshot, err
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	components := strings.Split(relative, "/")
	path := root
	for _, component := range components[:len(components)-1] {
		fd, e := unix.Openat(int(parent.Fd()), component, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		if errors.Is(e, unix.ENOENT) {
			return snapshot, nil
		}
		if e != nil {
			return snapshot, e
		}
		next := os.NewFile(uintptr(fd), component)
		id, e := agyDirectoryIdentity(next)
		if e == nil && (id.UID != uint32(os.Geteuid()) || id.Mode&0022 != 0) {
			e = errors.New("Agy layer directory is not owner-controlled")
		}
		if e != nil {
			return snapshot, errors.Join(e, next.Close())
		}
		if e := parent.Close(); e != nil {
			return snapshot, errors.Join(e, next.Close())
		}
		parent = next
		path = filepath.Join(path, component)
		snapshot.Directories[path] = id
	}
	raw, id, err := agyReadFile(parent, components[len(components)-1])
	if errors.Is(err, unix.ENOENT) {
		return snapshot, nil
	}
	if err != nil {
		return snapshot, err
	}
	if err := agyStrictJSON(raw); err != nil {
		return snapshot, err
	}
	if _, _, err := agyObject(raw); err != nil {
		return snapshot, err
	}
	snapshot.Present, snapshot.Raw, snapshot.File = true, raw, id
	return snapshot, nil
}

func (s agyLayerSnapshot) recheck() error {
	now, err := inspectAgyLayer(s.Root, s.Relative)
	if err != nil || !reflect.DeepEqual(now, s) {
		return errors.Join(err, fmt.Errorf("Agy layer observation changed: %s", filepath.Join(s.Root, s.Relative)))
	}
	return nil
}

func agySelectedParent(root, relative string) (*os.File, error) {
	current, err := openDirectory(root, false)
	if err != nil {
		return nil, err
	}
	components := strings.Split(relative, "/")
	for _, component := range components[:len(components)-1] {
		if err := unix.Mkdirat(int(current.Fd()), component, 0700); err != nil && !errors.Is(err, unix.EEXIST) {
			return nil, errors.Join(err, current.Close())
		}
		fd, err := unix.Openat(int(current.Fd()), component, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		if err != nil {
			return nil, errors.Join(err, current.Close())
		}
		next := os.NewFile(uintptr(fd), component)
		id, statErr := agyDirectoryIdentity(next)
		if statErr != nil || id.UID != uint32(os.Geteuid()) || id.Mode&0022 != 0 || id.Mode&0200 == 0 {
			return nil, errors.Join(statErr, current.Close(), next.Close(), errors.New("Agy selected directory is unsafe or unwritable"))
		}
		if err := current.Sync(); err != nil {
			return nil, errors.Join(err, current.Close(), next.Close())
		}
		if err := current.Close(); err != nil {
			return nil, errors.Join(err, next.Close())
		}
		current = next
	}
	return current, nil
}

func agyWriteTemporary(parent *os.File, name string, raw []byte, mode uint32) error {
	fd, err := unix.Openat(int(parent.Fd()), name, unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0600)
	if err != nil {
		return err
	}
	f := os.NewFile(uintptr(fd), name)
	n, writeErr := f.Write(raw)
	if n != len(raw) {
		writeErr = errors.Join(writeErr, io.ErrShortWrite)
	}
	return errors.Join(writeErr, f.Chmod(os.FileMode(mode&0777)), f.Sync(), f.Close())
}
