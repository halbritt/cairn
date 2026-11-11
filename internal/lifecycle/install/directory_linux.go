package install

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"unicode/utf8"

	"golang.org/x/sys/unix"
)

// Directory names an independently selected immutable file set. Identity is
// assigned by its consumer (a closure-map digest or launcher-byte digest), not
// manufactured here as an activation decision or a compatibility receipt.
type Directory struct {
	Path     string `json:"path"`
	Identity string `json:"identity"`
}

func fileName(name string) bool {
	return utf8.ValidString(name) && name != "" && name != "." && name != ".." && !strings.ContainsAny(name, "/\\\x00")
}

func selectedFiles(files []File, allowManifest bool) ([]File, error) {
	if len(files) == 0 {
		return nil, errors.New("immutable directory requires selected files")
	}
	ordered := append([]File(nil), files...)
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].Name < ordered[j].Name })
	for i, f := range ordered {
		pin, err := hex.DecodeString(f.SHA256)
		if !fileName(f.Name) || !allowManifest && f.Name == "manifest.json" || !canonical(f.Path) ||
			err != nil || len(pin) != sha256.Size || hex.EncodeToString(pin) != f.SHA256 || i > 0 && ordered[i-1].Name == f.Name {
			return nil, errors.New("files require unique basenames, canonical source paths and lowercase SHA-256 pins")
		}
	}
	return ordered, nil
}

func directoryIdentity(identity string) bool {
	raw, err := hex.DecodeString(identity)
	return err == nil && len(raw) == sha256.Size && hex.EncodeToString(raw) == identity
}

// StageDirectory publishes only the selected files at root/identity. Unlike a
// configuration generation it adds no manifest member, so every accepted alias
// can be retained, including manifest.json. Existing identities never change.
func StageDirectory(ctx context.Context, root, identity string, files []File) (Directory, error) {
	return stageDirectory(ctx, root, identity, files, nil)
}

func stageDirectory(ctx context.Context, root, identity string, files []File, observe func(string) error) (selected Directory, err error) {
	if !directoryIdentity(identity) || !utf8.ValidString(root) || os.Getuid() != os.Geteuid() {
		return selected, errors.New("directory requires a lowercase SHA-256 identity, UTF-8 root and unchanged UID")
	}
	ordered, err := selectedFiles(files, true)
	if err != nil {
		return selected, err
	}
	locked, err := openDirectory(root, true)
	if err != nil {
		return selected, err
	}
	defer func() { err = errors.Join(err, locked.Close()) }()
	if err := unix.Flock(int(locked.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		return selected, fmt.Errorf("install root lock: %w", err)
	}
	anchored, err := os.OpenRoot(root)
	if err != nil {
		return selected, err
	}
	defer func() { err = errors.Join(err, anchored.Close()) }()
	anchor, anchorErr := anchored.Stat(".")
	lock, lockErr := locked.Stat()
	if anchorErr != nil || lockErr != nil || !os.SameFile(anchor, lock) {
		return selected, errors.Join(anchorErr, lockErr, errors.New("install root changed"))
	}
	step := func(name string, action func() error) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if observe != nil {
			if err := observe("before:" + name); err != nil {
				return err
			}
		}
		if err := action(); err != nil {
			return err
		}
		if observe != nil {
			return observe("after:" + name)
		}
		return nil
	}
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return selected, err
	}
	temporary := ".stage-" + hex.EncodeToString(nonce[:])
	if err := unix.Mkdirat(int(locked.Fd()), temporary, 0700); err != nil {
		return selected, err
	}
	defer func() {
		if temporary != "" {
			err = errors.Join(err, anchored.RemoveAll(temporary))
		}
	}()
	expected := make([]generationMember, 0, len(ordered))
	for _, f := range ordered {
		member, e := copyGenerationFile(ctx, anchored, temporary, f, step)
		if e != nil {
			return selected, fmt.Errorf("stage member %s: %w", f.Name, e)
		}
		expected = append(expected, member)
	}
	if err := step("directory-sync", func() error {
		dir, e := anchored.Open(temporary)
		if e != nil {
			return e
		}
		return errors.Join(dir.Sync(), dir.Close())
	}); err != nil {
		return selected, err
	}
	published := false
	if err := step("publication", func() error {
		e := unix.Renameat2(int(locked.Fd()), temporary, int(locked.Fd()), identity, unix.RENAME_NOREPLACE)
		if errors.Is(e, unix.EEXIST) {
			return nil
		}
		if e == nil {
			temporary, published = "", true
		}
		return e
	}); err != nil {
		if published {
			selected = Directory{Path: filepath.Join(root, identity), Identity: identity}
		}
		return selected, fmt.Errorf("directory publication; reconcile %s: %w", filepath.Join(root, identity), err)
	}
	selected = Directory{Path: filepath.Join(root, identity), Identity: identity}
	dir, err := openDirectory(selected.Path, true)
	if err != nil {
		if !published {
			selected = Directory{}
		}
		return selected, err
	}
	err = errors.Join(verifyInstalledFiles(ctx, dir, expected), dir.Close())
	if err != nil {
		if !published {
			selected = Directory{}
		}
		return selected, fmt.Errorf("directory readback: %w", err)
	}
	if temporary != "" {
		if err := anchored.RemoveAll(temporary); err != nil {
			return selected, err
		}
		temporary = ""
	}
	if err := step("root-sync", locked.Sync); err != nil {
		return selected, fmt.Errorf("published directory requires reconciliation: %w", err)
	}
	if err := verifyRootPath(root, locked); err != nil {
		return selected, err
	}
	return selected, nil
}

// ReadDirectory verifies an externally selected file set without publishing or
// repairing it. Pins come from the retained configuration-generation selection.
func ReadDirectory(ctx context.Context, selected Directory, files []File) ([]File, error) {
	if !canonical(selected.Path) || !directoryIdentity(selected.Identity) || filepath.Base(selected.Path) != selected.Identity {
		return nil, errors.New("invalid immutable directory selection")
	}
	ordered, err := selectedFiles(files, true)
	if err != nil {
		return nil, err
	}
	expected := make([]generationMember, 0, len(ordered))
	for _, f := range ordered {
		if f.Path != filepath.Join(selected.Path, f.Name) {
			return nil, errors.New("member path differs from selected directory")
		}
		mode := uint32(0400)
		if f.Executable {
			mode = 0500
		}
		expected = append(expected, generationMember{Name: f.Name, SHA256: f.SHA256, Bytes: -1, Mode: mode})
	}
	dir, err := openDirectory(selected.Path, true)
	if err != nil {
		return nil, err
	}
	err = errors.Join(verifyInstalledFiles(ctx, dir, expected), verifyRootPath(selected.Path, dir), dir.Close())
	if err != nil {
		return nil, err
	}
	return ordered, nil
}
