package main

import (
	"os"

	"github.com/halbritt/cairn/internal/statefile"
)

// LockedStateFile retains the CLI configuration and callback surface while the
// shared package owns the existing lock/load/checkpoint/replacement mechanism.
type LockedStateFile[T any] statefile.File[T]

// These I/O call sites are replaced only by serial tests and stay unexported.
var (
	lockedStateWrite  = (*os.File).Write
	lockedStateSync   = (*os.File).Sync
	lockedStateClose  = (*os.File).Close
	lockedStateRename = os.Rename
)

func (s LockedStateFile[T]) Update(run func(T, func(T) error) (T, bool, error)) error {
	// Forward at each call, retaining the original serial fixture selection time.
	file := statefile.File[T](s).WithIO(statefile.IO{
		Write:  func(file *os.File, raw []byte) (int, error) { return lockedStateWrite(file, raw) },
		Sync:   func(file *os.File) error { return lockedStateSync(file) },
		Close:  func(file *os.File) error { return lockedStateClose(file) },
		Rename: func(old, next string) error { return lockedStateRename(old, next) },
	})
	return file.Update(run)
}
