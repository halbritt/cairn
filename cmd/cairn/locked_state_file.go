package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"syscall"
)

// LockedStateFile manages an owner-only JSON state file protected by an
// adjacent .lock file under syscall.Flock(LOCK_EX). Updates are written to a
// sibling temporary file, chmodded 0600, synced, renamed over the target path,
// and followed by a parent-directory sync.
type LockedStateFile[T any] struct {
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
}

func (s LockedStateFile[T]) defaultState() T {
	if s.Default != nil {
		return s.Default()
	}
	var zero T
	return zero
}

func (s LockedStateFile[T]) maxBytes() int64 {
	if s.MaxBytes > 0 {
		return s.MaxBytes
	}
	return 512 * 1024
}

func (s LockedStateFile[T]) absPathMessage() string {
	if s.AbsPathError != "" {
		return s.AbsPathError
	}
	return "state_file must be absolute"
}

func (s LockedStateFile[T]) sizeLimitMessage() string {
	if s.SizeLimitError != "" {
		return s.SizeLimitError
	}
	return "state exceeds size limit"
}

func (s LockedStateFile[T]) parseErrorPrefix() string {
	if s.ParseErrorPrefix != "" {
		return s.ParseErrorPrefix
	}
	return "invalid state"
}

func (s LockedStateFile[T]) Update(run func(state T, save func(T) error) (T, bool, error)) error {
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

func (s LockedStateFile[T]) acquireLock() (*os.File, error) {
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

func (s LockedStateFile[T]) loadLocked() (T, error) {
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

func (s LockedStateFile[T]) writeAtomic(value T) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	dir := filepath.Dir(s.Path)
	pattern := s.TempPattern
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
	if _, err := tmp.Write(raw); err == nil {
		err = tmp.Sync()
	}
	if closeErr := tmp.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	if err := os.Rename(tmp.Name(), s.Path); err != nil {
		return err
	}
	parent, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer parent.Close()
	return parent.Sync()
}
