package binding

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
)

// ValidateBinary checks the /3 pin against this running image. It neither
// launches code nor authenticates a /2 Python descriptor or compatibility receipt.
func ValidateBinary(path, expected string) error {
	if len(expected) != 64 || strings.Trim(expected, "0123456789abcdef") != "" {
		return errors.New("binary SHA-256 must be lowercase hexadecimal")
	}
	pin, err := openBinary(path)
	if err != nil {
		return err
	}
	defer pin.Close()
	running, err := os.Open("/proc/self/exe")
	if err != nil {
		return fmt.Errorf("running executable unavailable: %w", err)
	}
	defer running.Close()
	pinInfo, err := fileStat(pin)
	if err != nil {
		return err
	}
	runInfo, err := fileStat(running)
	if err != nil {
		return err
	}
	if err := runningPresent(runInfo); err != nil {
		return err
	}
	if pinInfo.Dev != runInfo.Dev || pinInfo.Ino != runInfo.Ino {
		return errors.New("pinned and running binary must be the same device/inode")
	}
	for _, file := range []*os.File{pin, running} {
		digest, err := stableDigest(file)
		if err != nil {
			return err
		}
		if digest != expected {
			return errors.New("binary digest changed")
		}
	}
	// Detect a rename/replacement while hashing as well as one before startup.
	current, err := openBinary(path)
	if err != nil {
		return err
	}
	defer current.Close()
	currentInfo, err := fileStat(current)
	if err != nil {
		return err
	}
	finalPin, err := fileStat(pin)
	if err != nil {
		return err
	}
	finalRun, err := fileStat(running)
	if err != nil {
		return err
	}
	if !sameMetadata(pinInfo, currentInfo) || !sameMetadata(pinInfo, finalPin) || !sameMetadata(runInfo, finalRun) {
		return errors.New("binary metadata changed while validating")
	}
	return runningPresent(finalRun)
}

func fileStat(file *os.File) (syscall.Stat_t, error) {
	var info syscall.Stat_t
	err := syscall.Fstat(int(file.Fd()), &info)
	return info, err
}

func sameMetadata(a, b syscall.Stat_t) bool {
	// Reads may change atime. All identity and write-relevant fields stay stable.
	return a.Dev == b.Dev && a.Ino == b.Ino && a.Mode == b.Mode && a.Nlink == b.Nlink &&
		a.Uid == b.Uid && a.Gid == b.Gid && a.Size == b.Size && a.Mtim == b.Mtim && a.Ctim == b.Ctim
}

func stableDigest(file *os.File) (string, error) {
	before, err := fileStat(file)
	if err != nil {
		return "", err
	}
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", err
	}
	after, err := fileStat(file)
	if err != nil {
		return "", err
	}
	if !sameMetadata(before, after) {
		return "", errors.New("binary metadata changed while hashing")
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

func runningPresent(info syscall.Stat_t) error {
	if info.Nlink == 0 {
		return errors.New("running executable is deleted")
	}
	path, err := os.Readlink("/proc/self/exe")
	if err != nil {
		return err
	}
	if strings.HasSuffix(path, " (deleted)") {
		// A literal filename may itself end with that text. Only accept it if
		// that literal pathname still identifies the running inode.
		var named syscall.Stat_t
		if syscall.Stat(path, &named) != nil || named.Dev != info.Dev || named.Ino != info.Ino {
			return errors.New("running executable is deleted")
		}
	}
	return nil
}

func openBinary(path string) (*os.File, error) {
	if !filepath.IsAbs(path) {
		return nil, errors.New("binary path must be absolute")
	}
	parts := strings.Split(path, "/")
	fd, err := syscall.Open("/", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	defer func() { _ = syscall.Close(fd) }()
	walked := "/"
	var parent syscall.Stat_t
	for _, part := range parts[1 : len(parts)-1] {
		if part == "" {
			continue
		}
		next, err := syscall.Openat(fd, part, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
		if err != nil {
			return nil, fmt.Errorf("binary ancestor unavailable or symlinked: %w", err)
		}
		_ = syscall.Close(fd)
		fd = next
		walked = filepath.Join(walked, part)
		if err := syscall.Fstat(fd, &parent); err != nil {
			return nil, err
		}
		if parent.Uid != 0 && parent.Uid != uint32(os.Getuid()) {
			return nil, errors.New("binary ancestor must be root-owned or owned by real UID")
		}
		if parent.Mode&0022 != 0 {
			systemTemp := (walked == "/tmp" || walked == "/var/tmp") && parent.Uid == 0 && parent.Mode&syscall.S_ISVTX != 0
			if !systemTemp {
				return nil, fmt.Errorf("binary ancestor %q must not be writable by group or others", walked)
			}
		}
	}
	if err := syscall.Fstat(fd, &parent); err != nil {
		return nil, err
	}
	fileFD, err := syscall.Openat(fd, parts[len(parts)-1], syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, fmt.Errorf("binary unavailable or symlinked: %w", err)
	}
	file := os.NewFile(uintptr(fileFD), path)
	info, err := fileStat(file)
	if err == nil {
		switch {
		case info.Mode&syscall.S_IFMT != syscall.S_IFREG:
			err = errors.New("binary must be a regular file")
		case info.Uid != uint32(os.Getuid()):
			err = errors.New("binary must be owned by invoking real UID")
		case info.Mode&0100 == 0 || info.Mode&0022 != 0 || info.Mode&07000 != 0:
			err = errors.New("binary requires owner execute, no group/other write and no set-ID/sticky bits")
		case parent.Uid != uint32(os.Getuid()) || parent.Mode&07777 != 0700:
			err = errors.New("binary snapshot directory must be real-UID-owned mode 0700")
		}
	}
	if err != nil {
		_ = file.Close()
		return nil, err
	}
	return file, nil
}
