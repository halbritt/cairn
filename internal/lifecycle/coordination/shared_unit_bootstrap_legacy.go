package coordination

import (
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"os"
	"slices"
	"strings"
	"syscall"

	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/sys/unix"
)

type bootstrapLegacyObservation struct {
	Raw         []byte
	SHA256      string
	Fingerprint string
}

// The original /2 executable was installed in an owner-controlled PATH
// directory. Its reader does not change the private-parent contract used by
// compatibility receipts or by any /3 image.
func bootstrapLegacyExecutable(path string) (bootstrapLegacyObservation, error) {
	return bootstrapLegacyRead(path, -1, true)
}

func bootstrapLegacyProfile(path string) (bootstrapLegacyObservation, error) {
	return bootstrapLegacyRead(path, 1048576, false)
}

func bootstrapLegacyProfileHomes(coordinators []*value.Value) error {
	if len(coordinators) == 0 || len(coordinators) > 8 {
		return errors.New("bootstrap legacy coordinator coverage is unknown")
	}
	seen := map[string]bool{}
	for _, config := range coordinators {
		if config == nil || config.Kind != value.Object {
			return errors.New("bootstrap legacy coordinator is not an object")
		}
		home := config.Get("config_home")
		if nativeNone(home) {
			if len(coordinators) > 1 {
				return errors.New("shared legacy memory requires distinct explicit configuration homes")
			}
			continue
		}
		path := nativeText(home)
		if home.Kind != value.String || !compatibilityCanonical(path) || seen[path] {
			return errors.New("bootstrap legacy configuration home is noncanonical or conflicting")
		}
		seen[path] = true
	}
	return nil
}

func (b *bootstrapRead) legacyCredentialExclusion(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	for _, credential := range b.credentials {
		if os.SameFile(info, credential) {
			return errors.New("bootstrap legacy input aliases a credential selector")
		}
	}
	return nil
}

func (b *bootstrapRead) legacyProfile(pin WatchCensusPin) (*value.Value, error) {
	if !censusDigest(pin.SHA256) {
		return nil, errors.New("bootstrap legacy profile requires an exact pin")
	}
	if err := b.legacyCredentialExclusion(pin.Path); err != nil {
		return nil, err
	}
	observed, err := bootstrapLegacyProfile(pin.Path)
	if err != nil || observed.SHA256 != pin.SHA256 {
		return nil, errors.Join(err, errors.New("bootstrap original legacy profile pin changed"))
	}
	profile, err := b.drain.cli.profile().Decode(SiteSessionRead, observed.Raw)
	if err != nil || profile == nil || profile.Kind != value.Object {
		return nil, errors.Join(err, errors.New("bootstrap original legacy profile must be an object"))
	}
	b.drain.facts["file:"+pin.Path] = observed.SHA256
	b.drain.facts["legacy-original-profile:"+pin.Path] = observed.Fingerprint
	return profile, nil
}

func (b *bootstrapRead) legacyExecutable(path string) error {
	if err := b.legacyCredentialExclusion(path); err != nil {
		return err
	}
	observed, err := bootstrapLegacyExecutable(path)
	if err != nil {
		return err
	}
	b.drain.facts["executable:"+path] = observed.SHA256
	b.drain.facts["legacy-original-executable:"+path] = observed.Fingerprint
	return nil
}

func (b *bootstrapRead) accountIdentity(path string) error {
	fd, err := bootstrapLegacyOpenObserved(path, func(name string, st syscall.Stat_t) error {
		key := "account-ancestor:" + name
		fingerprint := fmt.Sprintf("%d:%d:%d:%d:%d", st.Dev, st.Ino, st.Mode, st.Uid, st.Gid)
		if previous := b.drain.facts[key]; previous != "" && previous != fingerprint {
			return errors.New("bootstrap account ancestry changed during observation")
		}
		b.drain.facts[key] = fingerprint
		return nil
	})
	if err != nil {
		return err
	}
	defer syscall.Close(fd)
	var st syscall.Stat_t
	if err := syscall.Fstat(fd, &st); err != nil {
		return err
	}
	if st.Mode&syscall.S_IFMT != syscall.S_IFDIR || st.Uid != uint32(os.Getuid()) || st.Mode&0022 != 0 || st.Mode&07000 != 0 {
		return errors.New("bootstrap account identity directory is unsafe")
	}
	fingerprint := fmt.Sprintf("%d:%d:%d:%d:%d", st.Dev, st.Ino, st.Mode, st.Uid, st.Gid)
	key := "account-directory:" + path
	if prior := b.drain.facts[key]; prior != "" && prior != fingerprint {
		return errors.New("bootstrap account directory changed during observation")
	}
	b.drain.facts[key] = fingerprint
	if b.accounts == nil {
		b.accounts = map[string]bool{}
	}
	b.accounts[path] = true
	return nil
}

func bootstrapLegacyRead(path string, limit int64, executable bool) (bootstrapLegacyObservation, error) {
	var result bootstrapLegacyObservation
	ancestors := map[string]string{}
	record := func(name string, st syscall.Stat_t) error {
		ancestors[name] = fmt.Sprintf("%d:%d:%d:%d:%d", st.Dev, st.Ino, st.Mode, st.Uid, st.Gid)
		return nil
	}
	fd, err := bootstrapLegacyOpenObserved(path, record)
	if err != nil {
		return result, err
	}
	defer syscall.Close(fd)
	var before syscall.Stat_t
	if err := syscall.Fstat(fd, &before); err != nil {
		return result, err
	}
	if before.Mode&syscall.S_IFMT != syscall.S_IFREG || before.Uid != uint32(os.Getuid()) ||
		before.Mode&0022 != 0 || before.Mode&07000 != 0 || before.Nlink != 1 {
		return result, errors.New("bootstrap legacy file ownership, mode, type or alias is unknown")
	}
	if executable {
		if before.Mode&0100 == 0 {
			return result, errors.New("bootstrap legacy executable lacks owner execute permission")
		}
	} else if before.Mode&07777 != 0600 && before.Mode&07777 != 0400 {
		return result, errors.New("bootstrap legacy profile must be private0600 or readonly0400")
	}
	// readOpenedFD owns its descriptor. Retain this open description so its
	// full identity can also bracket the metadata validation above.
	copyFD, err := unix.FcntlInt(uintptr(fd), unix.F_DUPFD_CLOEXEC, 0)
	if err != nil {
		return result, err
	}
	raw, digest, err := readOpenedFD(copyFD, path, limit, executable, uint32(os.Getuid()))
	if err != nil {
		return result, err
	}
	var after syscall.Stat_t
	if err := syscall.Fstat(fd, &after); err != nil {
		return result, err
	}
	if !sameStatIdentity(before, after) {
		return result, errors.New("bootstrap legacy file changed during observation")
	}
	initial := maps.Clone(ancestors)
	fresh, err := bootstrapLegacyOpenObserved(path, record)
	if err != nil {
		return result, err
	}
	var named syscall.Stat_t
	statErr := syscall.Fstat(fresh, &named)
	closeErr := syscall.Close(fresh)
	if statErr != nil || closeErr != nil || !sameStatIdentity(after, named) || !maps.Equal(initial, ancestors) {
		return result, errors.Join(statErr, closeErr, errors.New("bootstrap legacy name or ancestor changed during read"))
	}
	names := make([]string, 0, len(ancestors))
	for name := range ancestors {
		names = append(names, name)
	}
	slices.Sort(names)
	chain := make([]string, 0, len(names))
	for _, name := range names {
		chain = append(chain, name+":"+ancestors[name])
	}
	encoded, _ := json.Marshal(chain)
	result.Raw, result.SHA256 = raw, digest
	result.Fingerprint = fmt.Sprintf("%d:%d:%d:%d:%d:%d:%d:%d:%d:%d:%d:%s",
		before.Dev, before.Ino, before.Mode, before.Uid, before.Gid, before.Nlink,
		before.Size, before.Mtim.Sec, before.Mtim.Nsec, before.Ctim.Sec, before.Ctim.Nsec, digest) + ":" + string(encoded)
	return result, nil
}

func bootstrapLegacyOpen(path string) (int, error) {
	return bootstrapLegacyOpenObserved(path, nil)
}

func bootstrapLegacyOpenObserved(path string, observe func(string, syscall.Stat_t) error) (int, error) {
	if !compatibilityCanonical(path) || path == "/" {
		return -1, errors.New("bootstrap legacy reference requires a canonical absolute file path")
	}
	parent, err := syscall.Open("/", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_CLOEXEC, 0)
	if err != nil {
		return -1, err
	}
	defer func() { _ = syscall.Close(parent) }()
	parts := strings.Split(path[1:], "/")
	ancestor := ""
	for _, name := range parts[:len(parts)-1] {
		ancestor += "/" + name
		next, err := syscall.Openat(parent, name, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
		if err != nil {
			return -1, err
		}
		_ = syscall.Close(parent)
		parent = next
		var st syscall.Stat_t
		if err := syscall.Fstat(parent, &st); err != nil {
			return -1, err
		}
		if observe != nil {
			if err := observe(ancestor, st); err != nil {
				return -1, err
			}
		}
		if (st.Uid != 0 && st.Uid != uint32(os.Getuid())) ||
			(st.Mode&0022 != 0 && !(st.Uid == 0 && st.Mode&syscall.S_ISVTX != 0)) {
			return -1, fmt.Errorf("bootstrap legacy reference has an unsafe ancestor or parent: %s", ancestor)
		}
	}
	return syscall.Openat(parent, parts[len(parts)-1], syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
}
