package install

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"time"

	"golang.org/x/sys/unix"
)

// SettingsSnapshot is an exact file observation, not native admission authority.
// Its retained descriptor identities and bytes cannot be supplied by JSON.
type SettingsSnapshot struct {
	file  agyLayerSnapshot
	attrs map[string][]byte
}

type SettingsMetadata struct {
	Present bool              `json:"present"`
	Device  uint64            `json:"device"`
	Inode   uint64            `json:"inode"`
	UID     uint32            `json:"uid"`
	GID     uint32            `json:"gid"`
	Mode    uint32            `json:"mode"`
	Size    int64             `json:"size"`
	Mtime   unix.Timespec     `json:"mtime"`
	SHA256  string            `json:"sha256"`
	Xattrs  map[string][]byte `json:"xattrs"`
}

// InspectSettings does not create a directory, lock, recovery record or file.
func InspectSettings(ctx context.Context, path string) (*SettingsSnapshot, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if !canonical(path) {
		return nil, errors.New("settings path must be canonical and absolute")
	}
	file, err := inspectAgyLayerBytes(filepath.Dir(path), filepath.Base(path))
	if err != nil {
		return nil, err
	}
	if file.Directories[filepath.Dir(path)].Mode&0022 != 0 {
		return nil, errors.New("settings parent must not be writable by group or others")
	}
	if file.Directories[filepath.Dir(path)].Mode&0022 != 0 {
		return nil, errors.New("settings parent is writable by another principal")
	}
	s := &SettingsSnapshot{file: file, attrs: map[string][]byte{}}
	if file.Present {
		parent, err := openDirectory(filepath.Dir(path), false)
		if err != nil {
			return nil, err
		}
		fd, e := unix.Openat(int(parent.Fd()), filepath.Base(path), unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)
		err = errors.Join(e, parent.Close())
		if err != nil {
			if fd >= 0 {
				err = errors.Join(err, unix.Close(fd))
			}
			return nil, err
		}
		f := os.NewFile(uintptr(fd), path)
		before, e := agyFileIdentity(f)
		attrs, aerr := settingsXattrs(fd)
		after, serr := agyFileIdentity(f)
		err = errors.Join(e, aerr, serr, f.Close())
		if err != nil || before != file.File || after != before {
			return nil, errors.Join(err, errors.New("settings changed while reading attributes"))
		}
		s.attrs = attrs
	}
	current, err := inspectAgyLayerBytes(filepath.Dir(path), filepath.Base(path))
	if err != nil || !reflect.DeepEqual(current, file) {
		return nil, errors.Join(err, errors.New("settings file changed during inspection"))
	}
	return s, ctx.Err()
}

func settingsXattrs(fd int) (map[string][]byte, error) {
	size, err := unix.Flistxattr(fd, nil)
	if err != nil {
		return nil, err
	}
	if size > 1024*1024 {
		return nil, errors.New("settings attributes exceed 1 MiB")
	}
	list := make([]byte, size)
	n, err := unix.Flistxattr(fd, list)
	if err != nil || n != size {
		return nil, errors.Join(err, errors.New("settings attribute names changed"))
	}
	result := make(map[string][]byte)
	if size == 0 {
		return result, nil
	}
	if list[len(list)-1] != 0 {
		return nil, errors.New("settings attribute list is incomplete")
	}
	for _, name := range strings.Split(string(list[:len(list)-1]), "\x00") {
		if name == "" {
			return nil, errors.New("settings attribute name is empty")
		}
		length, err := unix.Fgetxattr(fd, name, nil)
		if err != nil || length > 1024*1024 {
			return nil, errors.Join(err, errors.New("settings attribute unavailable or too large"))
		}
		value := make([]byte, length)
		n, err := unix.Fgetxattr(fd, name, value)
		if err != nil || n != length {
			return nil, errors.Join(err, errors.New("settings attribute changed"))
		}
		result[name] = value
	}
	return result, nil
}

func (s *SettingsSnapshot) Metadata() SettingsMetadata {
	if s == nil {
		return SettingsMetadata{}
	}
	f := s.file.File
	attrs := make(map[string][]byte, len(s.attrs))
	for name, value := range s.attrs {
		attrs[name] = bytes.Clone(value)
	}
	return SettingsMetadata{Present: s.file.Present, Device: f.Dev, Inode: f.Ino,
		UID: f.UID, GID: f.GID, Mode: f.Mode, Size: f.Size, Mtime: f.Modified,
		SHA256: agyDigest(s.file.Raw), Xattrs: attrs}
}

func (s *SettingsSnapshot) Bytes() []byte {
	if s == nil {
		return nil
	}
	return bytes.Clone(s.file.Raw)
}

func (s *SettingsSnapshot) Recheck(ctx context.Context) error {
	if s == nil {
		return errors.New("settings observation required")
	}
	current, err := InspectSettings(ctx, filepath.Join(s.file.Root, s.file.Relative))
	if err != nil {
		return err
	}
	if !reflect.DeepEqual(current.file, s.file) || !reflect.DeepEqual(current.attrs, s.attrs) {
		return errors.New("settings observation changed")
	}
	return nil
}

// CopySettings preserves representable metadata in a new inode. Publication
// and inverse retain the original inode by exchange; a copy is not that inode.
func CopySettings(ctx context.Context, source *SettingsSnapshot, target string) (*SettingsSnapshot, error) {
	if source == nil || !source.file.Present || !canonical(target) {
		return nil, errors.New("present source and canonical new target required")
	}
	return CreateSettings(ctx, source, source.Bytes(), target)
}

// CreateSettings creates an exclusive new object with selected bytes and the
// original's metadata. An absent original selects private0600 metadata. A
// partial new file is retained on failure, never guessed safe to delete.
// This is a filesystem primitive, not native admission authority.
func CreateSettings(ctx context.Context, source *SettingsSnapshot, data []byte, target string) (result *SettingsSnapshot, err error) {
	if source == nil || !canonical(target) || len(data) > agySettingsLimit {
		return nil, errors.New("selected source and bounded canonical new target required")
	}
	if _, _, err := agyObject(data); err != nil {
		return nil, err
	}
	if err := source.Recheck(ctx); err != nil {
		return nil, err
	}
	parent, err := openDirectory(filepath.Dir(target), false)
	if err != nil {
		return nil, err
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	name := filepath.Base(target)
	fd, err := unix.Openat(int(parent.Fd()), name, unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0600)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), target)
	defer func() { err = errors.Join(err, f.Close()) }()
	if n, err := f.Write(data); err != nil || n != len(data) {
		return nil, errors.Join(err, io.ErrShortWrite)
	}
	id := source.file.File
	if !source.file.Present {
		id = agyFileID{Mode: unix.S_IFREG | 0600, UID: uint32(os.Getuid()), GID: uint32(os.Getgid()), Modified: unix.NsecToTimespec(time.Now().UnixNano())}
	}
	if err := f.Chown(int(id.UID), int(id.GID)); err != nil {
		return nil, err
	}
	names := make([]string, 0, len(source.attrs))
	for name := range source.attrs {
		names = append(names, name)
	}
	sort.Slice(names, func(i, j int) bool {
		// Installing the access ACL can itself remove owner write permission.
		// Copy all other attributes before applying that final access policy.
		if names[i] == "system.posix_acl_access" {
			return false
		}
		if names[j] == "system.posix_acl_access" {
			return true
		}
		return names[i] < names[j]
	})
	for _, name := range names {
		if err := unix.Fsetxattr(fd, name, source.attrs[name], 0); err != nil {
			return nil, fmt.Errorf("copy settings attribute %s: %w", name, err)
		}
	}
	// Populate attributes while this exclusive new inode is writable, then
	// install the selected final mode (which can itself be read-only).
	if err := unix.Fchmod(fd, id.Mode&07777); err != nil {
		return nil, err
	}
	if err := unix.UtimesNanoAt(int(parent.Fd()), name, []unix.Timespec{id.Modified, id.Modified}, unix.AT_SYMLINK_NOFOLLOW); err != nil {
		return nil, err
	}
	if err := errors.Join(f.Sync(), parent.Sync(), source.Recheck(ctx)); err != nil {
		return nil, err
	}
	result, err = InspectSettings(ctx, target)
	if err != nil {
		return nil, err
	}
	actual := result.file.File
	if !bytes.Equal(result.file.Raw, data) || actual.Mode != id.Mode || actual.UID != id.UID || actual.GID != id.GID || actual.Modified != id.Modified || !reflect.DeepEqual(result.attrs, source.attrs) {
		return nil, errors.New("settings copy metadata or bytes differ from selected source")
	}
	return result, nil
}

// PatchSettingsHooks leaves every byte outside the hooks value untouched. Its
// caller separately verifies that only explicitly selected hook rows changed.
func PatchSettingsHooks(original, prepared []byte) ([]byte, error) {
	old, close, err := agyObject(original)
	if err != nil {
		return nil, err
	}
	new, _, err := agyObject(prepared)
	if err != nil {
		return nil, err
	}
	hooks, ok := new["hooks"]
	if !ok {
		return nil, errors.New("prepared settings have no hooks")
	}
	if prior, ok := old["hooks"]; ok {
		raw := append(bytes.Clone(original[:prior.start]), hooks.raw...)
		return append(raw, original[prior.end:]...), nil
	}
	raw := bytes.Clone(original[:close])
	if len(old) != 0 {
		raw = append(raw, ',')
	}
	raw = append(raw, []byte(`"hooks":`)...)
	raw = append(raw, hooks.raw...)
	return append(raw, original[close:]...), nil
}
