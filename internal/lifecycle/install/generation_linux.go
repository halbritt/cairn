package install

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"unicode/utf8"

	"github.com/google/uuid"
	"golang.org/x/sys/unix"
)

// File selects exact input bytes. Executable selects mode 0500; other members
// use 0400. Credential files are not installation members: configs retain their
// existing token-file selectors rather than copying the token itself.
type File struct {
	Name       string `json:"name"`
	Path       string `json:"path"`
	SHA256     string `json:"sha256"`
	Executable bool   `json:"executable,omitempty"`
}

// Generation pins a complete, immutable file publication. It is not Foundation
// validation, an activation decision, a hook refresh, or evidence of drain.
type Generation struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}

type generationMember struct {
	Name   string `json:"name"`
	SHA256 string `json:"sha256"`
	Bytes  int64  `json:"bytes"`
	Mode   uint32 `json:"mode"`
}

type generationManifest struct {
	Schema string             `json:"schema"`
	ID     string             `json:"id"`
	Files  []generationMember `json:"files"`
}

const generationManifestLimit = 1024 * 1024

// StageGeneration publishes an already-rendered versioned configuration/code
// set. Callers choose and retain id before rendering paths into config bytes;
// reusing id with different bytes refuses. Old generations are never replaced.
// The root-directory flock serializes installers, not legacy hooks or watchers.
func StageGeneration(ctx context.Context, root, id string, files []File) (Generation, error) {
	return stageGeneration(ctx, root, id, files, nil)
}

// A per-call boundary observer supports the installer's interruption fixtures.
// It does not change the public API or install a process-wide I/O override.
func stageGeneration(ctx context.Context, root, id string, files []File, observe func(string) error) (result Generation, err error) {
	parsed, parseErr := uuid.Parse(id)
	if parseErr != nil || parsed == uuid.Nil || parsed.String() != id {
		return result, errors.New("generation requires a canonical nonzero UUID selected before staging")
	}
	if os.Getuid() != os.Geteuid() || !utf8.ValidString(root) {
		return result, errors.New("generation requires an unchanged UID and a UTF-8 root")
	}
	if len(files) == 0 {
		return result, errors.New("generation requires selected files")
	}
	ordered := append([]File(nil), files...)
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].Name < ordered[j].Name })
	for i, f := range ordered {
		pin, e := hex.DecodeString(f.SHA256)
		if !memberName(f.Name) || !canonical(f.Path) || e != nil || len(pin) != sha256.Size || hex.EncodeToString(pin) != f.SHA256 || (i > 0 && ordered[i-1].Name == f.Name) {
			return result, errors.New("generation files require unique basenames, canonical source paths and lowercase SHA-256 pins")
		}
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	locked, err := openDirectory(root, true)
	if err != nil {
		return result, err
	}
	defer func() { err = errors.Join(err, locked.Close()) }()
	if err := unix.Flock(int(locked.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		return result, fmt.Errorf("install root lock: %w", err)
	}
	anchored, err := os.OpenRoot(root)
	if err != nil {
		return result, err
	}
	defer func() { err = errors.Join(err, anchored.Close()) }()
	anchorInfo, anchorErr := anchored.Stat(".")
	lockInfo, lockErr := locked.Stat()
	if anchorErr != nil || lockErr != nil || !os.SameFile(anchorInfo, lockInfo) {
		return result, errors.Join(anchorErr, lockErr, errors.New("install root changed"))
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
		return result, err
	}
	temporary := ".stage-" + hex.EncodeToString(nonce[:])
	if err := unix.Mkdirat(int(locked.Fd()), temporary, 0700); err != nil {
		return result, err
	}
	defer func() {
		if temporary != "" {
			err = errors.Join(err, anchored.RemoveAll(temporary))
		}
	}()
	manifest := generationManifest{Schema: "cairn.lifecycle-install-generation/1", ID: id}
	for _, f := range ordered {
		member, e := copyGenerationFile(ctx, anchored, temporary, f, step)
		if e != nil {
			return result, fmt.Errorf("stage member %s: %w", f.Name, e)
		}
		manifest.Files = append(manifest.Files, member)
	}
	raw, err := json.Marshal(manifest)
	if err != nil {
		return result, err
	}
	if len(raw) > generationManifestLimit {
		return result, errors.New("install generation manifest exceeds 1 MiB")
	}
	if err := step("manifest-sync", func() error { return writeSealed(anchored, temporary+"/manifest.json", raw) }); err != nil {
		return result, err
	}
	if err := step("generation-sync", func() error {
		dir, err := anchored.Open(temporary)
		if err != nil {
			return err
		}
		return errors.Join(dir.Sync(), dir.Close())
	}); err != nil {
		return result, err
	}
	name := "selection-" + id
	published := false
	if err := step("publication", func() error {
		err := unix.Renameat2(int(locked.Fd()), temporary, int(locked.Fd()), name, unix.RENAME_NOREPLACE)
		if errors.Is(err, unix.EEXIST) {
			return nil
		}
		if err == nil {
			temporary, published = "", true
		}
		return err
	}); err != nil {
		if published {
			result = generationPin(root, name, raw)
			return result, fmt.Errorf("generation published at %s; reconcile before selecting: %w", result.Path, err)
		}
		return result, err
	}
	result = generationPin(root, name, raw)
	if err := verifyGeneration(ctx, root, name, manifest, raw); err != nil {
		return result, fmt.Errorf("generation exists at %s but verification failed: %w", result.Path, err)
	}
	if temporary != "" {
		if err := anchored.RemoveAll(temporary); err != nil {
			return result, err
		}
		temporary = ""
	}
	if err := step("root-sync", locked.Sync); err != nil {
		return result, fmt.Errorf("generation exists at %s but root sync failed: %w", result.Path, err)
	}
	if err := verifyRootPath(root, locked); err != nil {
		return result, fmt.Errorf("generation published but root pathname changed: %w", err)
	}
	return result, nil
}

func memberName(name string) bool {
	return utf8.ValidString(name) && name != "" && name != "." && name != ".." && name != "manifest.json" && !strings.ContainsAny(name, "/\\\x00")
}

// ReadGeneration verifies an independent manifest pin and every installed member
// before returning exact immutable file selections. A caller can retain this pin
// for reverse selection; reading it never rewrites an earlier /2 configuration.
func ReadGeneration(ctx context.Context, selected Generation) (files []File, err error) {
	if !canonical(selected.Path) || !utf8.ValidString(selected.Path) || filepath.Base(selected.Path) != "manifest.json" {
		return nil, errors.New("selected generation manifest path must be canonical, absolute and UTF-8")
	}
	dirPath := filepath.Dir(selected.Path)
	dir, err := openDirectory(dirPath, true)
	if err != nil {
		return nil, err
	}
	defer func() { err = errors.Join(err, dir.Close()) }()
	fd, err := unix.Openat(int(dir.Fd()), "manifest.json", unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), selected.Path)
	before, statErr := generationStat(f, 0400)
	if statErr != nil || before.Size > generationManifestLimit {
		return nil, errors.Join(statErr, errors.New("invalid generation manifest"), f.Close())
	}
	raw, readErr := io.ReadAll(io.LimitReader(contextReader{ctx, f}, generationManifestLimit+1))
	after, afterErr := generationStat(f, 0400)
	closeErr := f.Close()
	if readErr != nil || afterErr != nil || closeErr != nil || *before != *after || int64(len(raw)) != before.Size || generationPin(filepath.Dir(dirPath), filepath.Base(dirPath), raw) != selected {
		return nil, errors.Join(readErr, afterErr, closeErr, errors.New("selected generation manifest changed"))
	}
	var manifest generationManifest
	if err := json.Unmarshal(raw, &manifest); err != nil {
		return nil, err
	}
	canonicalRaw, err := json.Marshal(manifest)
	if err != nil || !bytes.Equal(canonicalRaw, raw) {
		return nil, errors.Join(err, errors.New("generation manifest is not the exact installer format"))
	}
	id, err := uuid.Parse(manifest.ID)
	if err != nil || id == uuid.Nil || id.String() != manifest.ID || filepath.Base(dirPath) != "selection-"+manifest.ID || manifest.Schema != "cairn.lifecycle-install-generation/1" || len(manifest.Files) == 0 {
		return nil, errors.New("unsupported generation selection")
	}
	previous := ""
	for _, member := range manifest.Files {
		pin, e := hex.DecodeString(member.SHA256)
		if !memberName(member.Name) || member.Name <= previous || member.Bytes < 0 || e != nil || len(pin) != sha256.Size || hex.EncodeToString(pin) != member.SHA256 || member.Mode != 0400 && member.Mode != 0500 {
			return nil, errors.New("invalid generation member selection")
		}
		previous = member.Name
		files = append(files, File{Name: member.Name, Path: filepath.Join(dirPath, member.Name), SHA256: member.SHA256, Executable: member.Mode == 0500})
	}
	if err := verifyGeneration(ctx, filepath.Dir(dirPath), filepath.Base(dirPath), manifest, raw); err != nil {
		return nil, err
	}
	if err := verifyRootPath(dirPath, dir); err != nil {
		return nil, err
	}
	return files, nil
}

func generationPin(root, name string, raw []byte) Generation {
	h := sha256.Sum256(raw)
	return Generation{Path: filepath.Join(root, name, "manifest.json"), SHA256: hex.EncodeToString(h[:])}
}

func writeSealed(root *os.Root, path string, raw []byte) error {
	f, err := root.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL|unix.O_NOFOLLOW, 0600)
	if err != nil {
		return err
	}
	if n, err := f.Write(raw); err != nil || n != len(raw) {
		return errors.Join(err, io.ErrShortWrite, f.Close())
	}
	if err := f.Chmod(0400); err != nil {
		return errors.Join(err, f.Close())
	}
	return errors.Join(f.Sync(), f.Close())
}

func copyGenerationFile(ctx context.Context, root *os.Root, temporary string, in File, step func(string, func() error) error) (member generationMember, err error) {
	parent, err := openDirectory(filepath.Dir(in.Path), false)
	if err != nil {
		return member, err
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	fd, err := unix.Openat(int(parent.Fd()), filepath.Base(in.Path), unix.O_RDONLY|unix.O_NONBLOCK|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return member, err
	}
	input := os.NewFile(uintptr(fd), in.Path)
	defer func() { err = errors.Join(err, input.Close()) }()
	before, err := generationStat(input, 0)
	if err != nil {
		return member, err
	}
	mode := uint32(0400)
	if in.Executable {
		mode = 0500
	}
	output, err := root.OpenFile(temporary+"/"+in.Name, os.O_WRONLY|os.O_CREATE|os.O_EXCL|unix.O_NOFOLLOW, 0600)
	if err != nil {
		return member, err
	}
	h := sha256.New()
	n, copyErr := io.Copy(io.MultiWriter(output, h), contextReader{ctx, input})
	after, statErr := generationStat(input, 0)
	if copyErr != nil || statErr != nil || *before != *after || hex.EncodeToString(h.Sum(nil)) != in.SHA256 {
		return member, errors.Join(copyErr, statErr, errors.New("source bytes or metadata differ from selection"), output.Close())
	}
	if err := output.Chmod(os.FileMode(mode)); err != nil {
		return member, errors.Join(err, output.Close())
	}
	if err := step("member-sync/"+in.Name, output.Sync); err != nil {
		return member, errors.Join(err, output.Close())
	}
	if err := output.Close(); err != nil {
		return member, err
	}
	return generationMember{Name: in.Name, SHA256: in.SHA256, Bytes: n, Mode: mode}, nil
}

func generationStat(f *os.File, mode uint32) (*unix.Stat_t, error) {
	var s unix.Stat_t
	if err := unix.Fstat(int(f.Fd()), &s); err != nil {
		return nil, err
	}
	if s.Mode&unix.S_IFMT != unix.S_IFREG || s.Uid != uint32(os.Getuid()) || s.Nlink != 1 || s.Mode&07022 != 0 || mode != 0 && s.Mode&07777 != mode {
		return nil, errors.New("generation member must be a controlled singly linked regular file with the selected mode")
	}
	s.Atim = unix.Timespec{}
	return &s, nil
}

func verifyGeneration(ctx context.Context, root, name string, manifest generationManifest, raw []byte) (err error) {
	dir, err := openDirectory(filepath.Join(root, name), true)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, dir.Close()) }()
	names, err := dir.Readdirnames(-1)
	if err != nil || len(names) != len(manifest.Files)+1 {
		return errors.Join(err, errors.New("generation membership differs"))
	}
	expected := append([]generationMember{{Name: "manifest.json", SHA256: generationPin(root, name, raw).SHA256, Bytes: int64(len(raw)), Mode: 0400}}, manifest.Files...)
	for _, member := range expected {
		fd, err := unix.Openat(int(dir.Fd()), member.Name, unix.O_RDONLY|unix.O_NONBLOCK|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		if err != nil {
			return err
		}
		f := os.NewFile(uintptr(fd), member.Name)
		before, statErr := generationStat(f, member.Mode)
		if statErr != nil {
			return errors.Join(statErr, f.Close())
		}
		h := sha256.New()
		n, readErr := io.Copy(h, contextReader{ctx, f})
		after, afterErr := generationStat(f, member.Mode)
		closeErr := f.Close()
		if readErr != nil || afterErr != nil || closeErr != nil || *before != *after || n != member.Bytes || hex.EncodeToString(h.Sum(nil)) != member.SHA256 {
			return errors.Join(readErr, afterErr, closeErr, errors.New("generation member changed"))
		}
	}
	return nil
}
