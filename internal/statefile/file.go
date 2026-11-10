// Package statefile provides the existing configurable Go JSON file mechanism.
package statefile

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"syscall"

	"github.com/halbritt/cairn/core"
)

// File manages a JSON state file with caller-selected policy, protected by an
// adjacent .lock file under syscall.Flock(LOCK_EX). Updates are written to a
// sibling temporary file, chmodded 0600, synced, renamed over the target path,
// and followed by a parent-directory sync.
// Temporary I/O errors stop publication. A directory-sync error is returned
// after the new state has already been published.
type File[T any] struct {
	Path               string
	TempPattern        string
	MaxBytes           int64
	CreateDir          bool
	RequirePrivateDir  bool
	RequirePrivateFile bool
	StrictJSON         bool
	NonBlocking        bool
	Default            func() T
	Validate           func(T) error
	AbsPathError       string
	SizeLimitError     string
	ParseErrorPrefix   string
	calls              IO
}

// IO selects the four filesystem calls already present in the retained CLI's
// serial fault fixtures. Nil members use the operating-system implementation.
// Selection is per File value; there is no package-global filesystem switch.
type IO struct {
	Write  func(*os.File, []byte) (int, error)
	Sync   func(*os.File) error
	Close  func(*os.File) error
	Rename func(string, string) error
}

// WithIO retains those existing I/O boundaries for a concrete caller.
func (s File[T]) WithIO(calls IO) File[T] {
	s.calls = calls
	return s
}

func (s File[T]) ioCalls() IO {
	calls := s.calls
	if calls.Write == nil {
		calls.Write = (*os.File).Write
	}
	if calls.Sync == nil {
		calls.Sync = (*os.File).Sync
	}
	if calls.Close == nil {
		calls.Close = (*os.File).Close
	}
	if calls.Rename == nil {
		calls.Rename = os.Rename
	}
	return calls
}

func invalid(message string) error { return &core.Error{Code: "INVALID_REQUEST", Message: message} }

func (s File[T]) defaultState() T {
	if s.Default != nil {
		return s.Default()
	}
	var zero T
	return zero
}

func (s File[T]) maxBytes() int64 {
	if s.MaxBytes > 0 {
		return s.MaxBytes
	}
	return 512 * 1024
}

func (s File[T]) absPathMessage() string {
	if s.AbsPathError != "" {
		return s.AbsPathError
	}
	return "state_file must be absolute"
}

func (s File[T]) sizeLimitMessage() string {
	if s.SizeLimitError != "" {
		return s.SizeLimitError
	}
	return "state exceeds size limit"
}

func (s File[T]) parseErrorPrefix() string {
	if s.ParseErrorPrefix != "" {
		return s.ParseErrorPrefix
	}
	return "invalid state"
}

func (s File[T]) Update(run func(state T, save func(T) error) (T, bool, error)) error {
	if s.Path == "" {
		_, _, err := run(s.defaultState(), func(T) error { return nil })
		return err
	}
	if !filepath.IsAbs(s.Path) {
		return invalid(s.absPathMessage())
	}
	lock, err := s.acquireLock()
	if err != nil {
		return err
	}
	defer lock.Close()
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	state, err := s.loadLocked()
	if err != nil {
		return err
	}
	next, shouldWrite, runErr := run(state, s.writeAtomic)
	if !shouldWrite {
		return runErr
	}
	if writeErr := s.writeAtomic(next); writeErr != nil {
		return errors.Join(runErr, writeErr)
	}
	return runErr
}

func (s File[T]) acquireLock() (*os.File, error) {
	dir := filepath.Dir(s.Path)
	if s.CreateDir {
		if err := os.MkdirAll(dir, 0700); err != nil {
			return nil, err
		}
	}
	if s.RequirePrivateDir {
		info, err := os.Stat(dir)
		if err != nil {
			return nil, err
		}
		if !info.IsDir() || info.Mode().Perm()&0022 != 0 {
			return nil, invalid("send journal directory must not be writable by others")
		}
	}
	flags := os.O_CREATE | os.O_RDWR | syscall.O_NOFOLLOW
	if s.NonBlocking {
		flags |= syscall.O_NONBLOCK
	}
	lock, err := os.OpenFile(s.Path+".lock", flags, 0600)
	if err != nil {
		return nil, err
	}
	if s.RequirePrivateFile {
		lockInfo, err := lock.Stat()
		if err != nil || !lockInfo.Mode().IsRegular() || lockInfo.Mode().Perm()&0077 != 0 {
			lock.Close()
			return nil, invalid("send journal lock must be owner-only regular file")
		}
	}
	how := syscall.LOCK_EX
	if s.NonBlocking {
		how |= syscall.LOCK_NB
	}
	if err := syscall.Flock(int(lock.Fd()), how); err != nil {
		lock.Close()
		if s.NonBlocking {
			return nil, invalid("send journal is already in use or cannot be locked")
		}
		return nil, err
	}
	return lock, nil
}

func (s File[T]) loadLocked() (T, error) {
	state := s.defaultState()
	flags := os.O_RDONLY | syscall.O_NOFOLLOW
	if s.NonBlocking {
		flags |= syscall.O_NONBLOCK
	}
	file, err := os.OpenFile(s.Path, flags, 0)
	if errors.Is(err, os.ErrNotExist) {
		return state, nil
	}
	if err != nil {
		return state, err
	}
	maxBytes := s.maxBytes()
	if s.RequirePrivateFile {
		info, statErr := file.Stat()
		if statErr != nil {
			file.Close()
			return state, statErr
		}
		if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > maxBytes {
			file.Close()
			return state, invalid(s.sizeLimitMessage())
		}
	}
	raw, readErr := io.ReadAll(io.LimitReader(file, maxBytes+1))
	file.Close()
	if readErr != nil {
		return state, readErr
	}
	if int64(len(raw)) > maxBytes {
		return state, invalid(s.sizeLimitMessage())
	}
	if s.StrictJSON {
		decoder := json.NewDecoder(bytes.NewReader(raw))
		if err := decoder.Decode(&state); err != nil {
			return state, invalid(s.parseErrorPrefix() + ": " + err.Error())
		}
		var extra any
		if err := decoder.Decode(&extra); err != io.EOF {
			return state, invalid("send journal contains trailing data")
		}
	} else if err := json.Unmarshal(raw, &state); err != nil {
		return state, fmt.Errorf("%s: %w", s.parseErrorPrefix(), err)
	}
	if s.Validate != nil {
		if err := s.Validate(state); err != nil {
			return state, err
		}
	}
	return state, nil
}

func (s File[T]) writeAtomic(value T) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	return replaceBytes(s.Path, raw, s.TempPattern, s.ioCalls())
}

// Replace persists already encoded caller-owned bytes with the same durable
// boundary as File.Update. The caller must hold its operation's existing lock.
func Replace(path string, raw []byte) error {
	return replaceBytes(path, raw, "", (File[any]{}).ioCalls())
}

func replaceBytes(path string, raw []byte, pattern string, calls IO) error {
	dir := filepath.Dir(path)
	if pattern == "" {
		pattern = ".cairn-state-*"
	}
	tmp, err := os.CreateTemp(dir, pattern)
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err := tmp.Chmod(0600); err != nil {
		tmp.Close()
		return err
	}
	n, err := calls.Write(tmp, raw)
	if err == nil && n != len(raw) {
		err = io.ErrShortWrite
	}
	if err == nil {
		err = calls.Sync(tmp)
	}
	if closeErr := calls.Close(tmp); err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	if err := calls.Rename(tmp.Name(), path); err != nil {
		return err
	}
	parent, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer parent.Close()
	return calls.Sync(parent)
}
