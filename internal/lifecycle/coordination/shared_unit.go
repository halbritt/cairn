package coordination

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/install"
	"golang.org/x/sys/unix"
)

// WatchBindings selects complete copies of immutable coordination configs.
// Pins describe file bytes, not permission to activate callbacks or settle work.
type WatchBindings struct {
	Directory string
	Pins      map[string]string
}

// WatchBindingsExchange retains both selections, including an uncertain result
// after publication. Published means the atomic exchange happened; a later sync
// or readback error must be reconciled, never retried as an unchanged operation.
type WatchBindingsExchange struct {
	Active, Retained WatchBindings
	Preparation      string
	Published        bool
}

func watchBindingNames(pins map[string]string) ([]string, error) {
	if len(pins) == 0 {
		return nil, errors.New("watch selection requires bindings")
	}
	names := make([]string, 0, len(pins))
	for name, digest := range pins {
		raw, err := hex.DecodeString(digest)
		if filepath.Base(name) != name || !strings.HasSuffix(name, ".json") || strings.ContainsAny(name, "\\\x00") ||
			err != nil || len(raw) != sha256.Size || hex.EncodeToString(raw) != digest {
			return nil, errors.New("watch bindings require JSON basenames and lowercase SHA-256 pins")
		}
		names = append(names, name)
	}
	sort.Strings(names)
	return names, nil
}

func readWatchBindings(selected WatchBindings) error {
	names, err := watchBindingNames(selected.Pins)
	if err != nil {
		return err
	}
	entries, err := os.ReadDir(selected.Directory)
	if err != nil {
		return err
	}
	seen := 0
	for _, entry := range entries {
		if entry.Name() == ".watch.lock" {
			continue
		}
		if _, ok := selected.Pins[entry.Name()]; !ok || !entry.Type().IsRegular() {
			return errors.New("watch config-dir membership changed")
		}
		seen++
	}
	if seen != len(names) {
		return errors.New("watch config-dir membership changed")
	}
	for _, name := range names {
		_, digest, err := compatibilityOpened(filepath.Join(selected.Directory, name), 1048576, uint32(os.Getuid()))
		if err != nil {
			return err
		}
		if digest != selected.Pins[name] {
			return errors.New("watch config pin changed")
		}
	}
	return nil
}

type watchBindingsSwap struct {
	ctx                      context.Context
	current, selected        WatchBindings
	retainedDir, parentPath  string
	activeName, retainedName string
	parent                   *os.Root
	parentInfo, activeInfo   os.FileInfo
	lease                    *LockLease
	files                    []install.File
	bodies                   map[string][]byte
	names                    []string
	observe                  func(string) error
	verifyDrain              func(context.Context) error
	result                   WatchBindingsExchange
}

func nextWatchBindingFiles(current WatchBindings, files []install.File) (WatchBindings, map[string][]byte, error) {
	selected := WatchBindings{Directory: current.Directory, Pins: make(map[string]string, len(files))}
	if len(files) != len(current.Pins) {
		return selected, nil, errors.New("watch exchange must retain every binding name")
	}
	bodies := make(map[string][]byte, len(files))
	for _, file := range files {
		if _, ok := current.Pins[file.Name]; !ok || file.Executable {
			return selected, nil, errors.New("watch exchange must retain every binding name as data")
		}
		if _, duplicate := selected.Pins[file.Name]; duplicate {
			return selected, nil, errors.New("duplicate next watch binding")
		}
		selected.Pins[file.Name] = file.SHA256
		raw, digest, err := compatibilityOpened(file.Path, 1048576, uint32(os.Getuid()))
		if err != nil {
			return selected, nil, err
		}
		if digest != file.SHA256 {
			return selected, nil, errors.New("next watch config pin mismatch")
		}
		bodies[file.Name] = raw
	}
	return selected, bodies, nil
}

func openWatchBindingsSwap(ctx context.Context, current WatchBindings, retainedDir string, files []install.File) (swap *watchBindingsSwap, err error) {
	parentPath := filepath.Dir(current.Directory)
	if !compatibilityCanonical(current.Directory) || !compatibilityCanonical(retainedDir) || current.Directory == retainedDir || filepath.Dir(retainedDir) != parentPath || current.Directory == "/" {
		return nil, errors.New("watch exchange requires distinct canonical sibling directories")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if err := readWatchBindings(current); err != nil {
		return nil, err
	}
	if _, e := os.Lstat(retainedDir); !errors.Is(e, os.ErrNotExist) {
		return nil, errors.Join(e, errors.New("retained watch selection already exists or is unavailable"))
	}
	selected, bodies, err := nextWatchBindingFiles(current, files)
	if err != nil {
		return nil, err
	}
	names, err := watchBindingNames(selected.Pins)
	if err != nil {
		return nil, err
	}
	parent, err := os.OpenRoot(parentPath)
	if err != nil {
		return nil, err
	}
	defer func() {
		if err != nil {
			err = errors.Join(err, parent.Close())
		}
	}()
	parentInfo, err := parent.Stat(".")
	if err != nil {
		return nil, err
	}
	activeName := filepath.Base(current.Directory)
	activeInfo, err := parent.Lstat(activeName)
	if err != nil || !activeInfo.IsDir() || activeInfo.Mode().Perm() != 0700 {
		return nil, errors.Join(err, errors.New("watch bindings require a private directory"))
	}
	lease, err := AcquireWatcherLock(current.Directory)
	if err != nil {
		return nil, err
	}
	return &watchBindingsSwap{ctx: ctx, current: current, selected: selected, retainedDir: retainedDir,
		parentPath: parentPath, activeName: activeName, retainedName: filepath.Base(retainedDir),
		parent: parent, parentInfo: parentInfo, activeInfo: activeInfo, lease: lease,
		files: files, bodies: bodies, names: names}, nil
}

func (s *watchBindingsSwap) step(name string, action func() error) error {
	if err := s.ctx.Err(); err != nil {
		return err
	}
	if s.observe != nil {
		if err := s.observe("before:" + name); err != nil {
			return err
		}
	}
	if err := action(); err != nil {
		return err
	}
	if s.observe != nil {
		return s.observe("after:" + name)
	}
	return nil
}

func (s *watchBindingsSwap) syncDir(name string) error {
	dir, err := s.parent.Open(name)
	if err != nil {
		return err
	}
	return errors.Join(dir.Sync(), dir.Close())
}

func (s *watchBindingsSwap) writeConfig(name string) error {
	file, err := s.parent.OpenFile(filepath.Join(s.retainedName, name), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0400)
	if err != nil {
		return err
	}
	err = s.step("write:"+name, func() error { _, err := file.Write(s.bodies[name]); return err })
	if err == nil {
		err = s.step("file-sync:"+name, file.Sync)
	}
	return errors.Join(err, file.Close())
}

func (s *watchBindingsSwap) prepare() error {
	if err := readWatchBindings(s.current); err != nil {
		return err
	}
	if err := s.parent.Mkdir(s.retainedName, 0700); err != nil {
		return err
	}
	// Failed preparations remain for inspection; Published remains false.
	s.result.Preparation = s.retainedDir
	if err := s.parent.Link(filepath.Join(s.activeName, ".watch.lock"), filepath.Join(s.retainedName, ".watch.lock")); err != nil {
		return err
	}
	if err := s.step("watch-lock-sync", s.lease.file.Sync); err != nil {
		return err
	}
	for _, name := range s.names {
		if err := s.writeConfig(name); err != nil {
			return err
		}
	}
	for _, sync := range []struct{ name, path string }{
		{"prepared-sync", s.retainedName}, {"active-sync", s.activeName}, {"parent-preparation-sync", "."},
	} {
		if err := s.step(sync.name, func() error { return s.syncDir(sync.path) }); err != nil {
			return err
		}
	}
	return nil
}

func (s *watchBindingsSwap) checkParent() error {
	info, err := os.Lstat(s.parentPath)
	if err != nil || !os.SameFile(s.parentInfo, info) || !info.IsDir() {
		return errors.Join(err, errors.New("watch directory parent changed"))
	}
	return nil
}

func (s *watchBindingsSwap) verifyPrepared() error {
	if err := s.checkParent(); err != nil {
		return err
	}
	info, err := s.parent.Lstat(s.activeName)
	if err != nil || !os.SameFile(s.activeInfo, info) {
		return errors.Join(err, errors.New("active watch directory changed"))
	}
	if err := readWatchBindings(s.current); err != nil {
		return err
	}
	prepared := s.selected
	prepared.Directory = s.retainedDir
	if err := readWatchBindings(prepared); err != nil {
		return err
	}
	for _, file := range s.files {
		_, digest, err := compatibilityOpened(file.Path, 1048576, uint32(os.Getuid()))
		if err != nil {
			return err
		}
		if digest != file.SHA256 {
			return errors.New("next watch config pin changed before exchange")
		}
	}
	for _, dir := range []string{s.activeName, s.retainedName} {
		info, err := s.parent.Stat(filepath.Join(dir, ".watch.lock"))
		lockInfo, statErr := s.lease.file.Stat()
		if err != nil || statErr != nil || !os.SameFile(info, lockInfo) {
			return errors.Join(err, statErr, errors.New("watch lock identity changed"))
		}
	}
	return nil
}

func (s *watchBindingsSwap) exchange() error {
	if err := s.verifyPrepared(); err != nil {
		return err
	}
	fd, err := s.parent.Open(".")
	if err != nil {
		return err
	}
	if s.verifyDrain != nil {
		if err := s.verifyDrain(s.ctx); err != nil {
			return errors.Join(err, fd.Close())
		}
	}
	if err := unix.Renameat2(int(fd.Fd()), s.activeName, int(fd.Fd()), s.retainedName, unix.RENAME_EXCHANGE); err != nil {
		return errors.Join(err, fd.Close())
	}
	s.result.Active, s.result.Published = s.selected, true
	s.result.Retained = WatchBindings{Directory: s.retainedDir, Pins: s.current.Pins}
	return fd.Close()
}

func (s *watchBindingsSwap) finish() error {
	if err := s.step("parent-publication-sync", func() error { return s.syncDir(".") }); err != nil {
		return fmt.Errorf("published watch selection requires reconciliation: %w", err)
	}
	if err := readWatchBindings(s.result.Active); err != nil {
		return err
	}
	if err := readWatchBindings(s.result.Retained); err != nil {
		return err
	}
	return s.checkParent()
}

func exchangeWatchBindings(ctx context.Context, current WatchBindings, retainedDir string, next []install.File, observe func(string) error) (result WatchBindingsExchange, err error) {
	swap, err := openWatchBindingsSwap(ctx, current, retainedDir, next)
	if err != nil {
		return result, err
	}
	swap.observe = observe
	return runWatchBindingsSwap(swap)
}

func runWatchBindingsSwap(swap *watchBindingsSwap) (result WatchBindingsExchange, err error) {
	defer func() { err = errors.Join(err, swap.lease.Release(), swap.parent.Close()) }()
	if err := swap.prepare(); err != nil {
		return swap.result, err
	}
	if err := swap.step("exchange", swap.exchange); err != nil {
		return swap.result, fmt.Errorf("watch selection exchange (published=%t): %w", swap.result.Published, err)
	}
	return swap.result, swap.finish()
}
