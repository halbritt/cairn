package coordination

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/halbritt/cairn/internal/jsontext"
	"io"
	"maps"
	"os"
	"path/filepath"
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
	observed, err := b.legacyRead(pin.Path, false)
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
	observed, err := b.legacyRead(path, true)
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

func (b *bootstrapRead) legacyRead(path string, executable bool) (bootstrapLegacyObservation, error) {
	key := fmt.Sprintf("%t:%s", executable, path)
	if observed, ok := b.legacy[key]; ok {
		return observed, nil
	}
	var observed bootstrapLegacyObservation
	var err error
	if executable {
		observed, err = bootstrapLegacyExecutable(path)
	} else {
		observed, err = bootstrapLegacyProfile(path)
	}
	if err == nil {
		if b.legacy == nil {
			b.legacy = map[string]bootstrapLegacyObservation{}
		}
		b.legacy[key] = observed
	}
	return observed, err
}

func (b *bootstrapRead) legacyRecheck() error {
	for _, key := range slices.Sorted(maps.Keys(b.legacy)) {
		executable := strings.HasPrefix(key, "true:")
		_, path, ok := strings.Cut(key, ":")
		if !ok {
			return errors.New("bootstrap legacy observation key is invalid")
		}
		var observed bootstrapLegacyObservation
		var err error
		if executable {
			observed, err = bootstrapLegacyExecutable(path)
		} else {
			observed, err = bootstrapLegacyProfile(path)
		}
		if err != nil || observed.Fingerprint != b.legacy[key].Fingerprint {
			return errors.Join(err, errors.New("bootstrap original legacy input changed across API bracket"))
		}
	}
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

// The journal below is recovery DATA, never a drain/fence receipt. Every use
// reopens both namespace entries, verifies the original plan's SHA, derives the
// disabled bytes again, and still performs the complete fresh D1-D6/fence read.
// RENAME_EXCHANGE retains the original manifest inode and raw bytes, including
// an enabled:true manifest. No rewritten copy is labelled the raw original.
type bootstrapLegacyFileIdentity struct {
	Device    uint64 `json:"device"`
	Inode     uint64 `json:"inode"`
	UID       uint32 `json:"uid"`
	GID       uint32 `json:"gid"`
	Mode      uint32 `json:"mode"`
	Size      int64  `json:"size"`
	MtimeSec  int64  `json:"mtime_sec"`
	MtimeNsec int64  `json:"mtime_nsec"`
	SHA256    string `json:"sha256"`
}

type bootstrapLegacyJournalEntry struct {
	Path     string                      `json:"path"`
	Backup   string                      `json:"backup"`
	Original bootstrapLegacyFileIdentity `json:"original"`
	Disabled bootstrapLegacyFileIdentity `json:"disabled"`
}

type bootstrapLegacyJournal struct {
	Schema    string                        `json:"schema"`
	Selection string                        `json:"selection"`
	Entries   []bootstrapLegacyJournalEntry `json:"entries"`
}

type bootstrapLegacyTransaction struct {
	directory     string
	journal       bootstrapLegacyJournal
	prepared      bool
	incomplete    bool
	disabledBytes map[string][]byte
	selected      map[string]string
}

func bootstrapLegacyMutationFile(path string) ([]byte, bootstrapLegacyFileIdentity, error) {
	var result bootstrapLegacyFileIdentity
	parent, err := bootstrapPrivateDirectory(filepath.Dir(path))
	if err != nil {
		return nil, result, err
	}
	if err := parent.Close(); err != nil {
		return nil, result, err
	}
	before, err := os.Lstat(path)
	if err != nil {
		return nil, result, err
	}
	observed, err := bootstrapLegacyProfile(path)
	if err != nil {
		return nil, result, err
	}
	after, err := os.Lstat(path)
	if err != nil {
		return nil, result, err
	}
	a, b := *before.Sys().(*syscall.Stat_t), *after.Sys().(*syscall.Stat_t)
	if !sameStatIdentity(a, b) {
		return nil, result, errors.New("bootstrap legacy mutation input changed")
	}
	result = bootstrapLegacyFileIdentity{uint64(b.Dev), b.Ino, b.Uid, b.Gid, b.Mode, b.Size, b.Mtim.Sec, b.Mtim.Nsec, observed.SHA256}
	return observed.Raw, result, nil
}

func bootstrapPrivateDirectory(path string) (*os.File, error) {
	fd, err := bootstrapLegacyOpen(path)
	if err != nil {
		return nil, err
	}
	file := os.NewFile(uintptr(fd), path)
	var st syscall.Stat_t
	if err := syscall.Fstat(fd, &st); err != nil {
		return nil, errors.Join(err, file.Close())
	}
	if st.Mode&syscall.S_IFMT != syscall.S_IFDIR || st.Uid != uint32(os.Getuid()) || st.Mode&07777 != 0700 {
		return nil, errors.Join(errors.New("bootstrap mutation parent must remain owned0700"), file.Close())
	}
	return file, nil
}

func bootstrapDisabledManifest(raw []byte) ([]byte, error) {
	if err := jsontext.CheckUnicode(raw); err != nil {
		return nil, err
	}
	manifest, err := value.DecodeBytes(raw, 0)
	if err != nil {
		return nil, err
	}
	if err := unitControlUniqueKeys(manifest, 0); err != nil {
		return nil, err
	}
	if !value.SameText(manifest.Get("schema"), "cairn.inbox-recall-binding/2") || manifest.Get("enabled") == nil || manifest.Get("enabled").Kind != value.Bool || !manifest.Get("enabled").Boolean {
		return nil, errors.New("bootstrap raw reverse requires actual enabled /2 original")
	}
	manifest.Set("enabled", value.Boolean(false))
	return canonicalSortedJSONBytes(manifest)
}

func newBootstrapLegacyTransaction(p *UnitBootstrapPlan, planPath, operation string) (*bootstrapLegacyTransaction, error) {
	// The fence is freshly captured each invocation. It is deliberately not a
	// recovery identifier; selection paths, pins and directory identities are.
	selection := *p
	transition := *p.Transition
	transition.Fence = SelectedUnitFenceScope{}
	selection.Transition = &transition
	raw, err := json.Marshal(struct {
		Path string
		Plan *UnitBootstrapPlan
	}{planPath, &selection})
	if err != nil {
		return nil, err
	}
	digest := sha256.Sum256(raw)
	id := hex.EncodeToString(digest[:])
	tx := &bootstrapLegacyTransaction{directory: filepath.Join(p.Root, ".unit-bootstrap-legacy-"+id), journal: bootstrapLegacyJournal{Schema: "cairn.unit-bootstrap-legacy-recovery/1", Selection: id}, disabledBytes: map[string][]byte{}, selected: map[string]string{}}
	for _, association := range p.Associations {
		pin := association.Binding
		if association.Receipt != nil || !compatibilityCanonical(pin.Path) || !censusDigest(pin.SHA256) || !censusWithin(p.Root, pin.Path) || censusOverlap(pin.Path, p.Current.Directory) || censusOverlap(pin.Path, filepath.Join(filepath.Dir(p.Current.Directory), p.Transition.TargetIdentity.Name)) {
			return nil, errors.New("bootstrap original manifest selection is unsafe")
		}
		if previous := tx.selected[pin.Path]; previous != "" && previous != pin.SHA256 {
			return nil, errors.New("bootstrap original manifest has conflicting pins")
		}
		tx.selected[pin.Path] = pin.SHA256
		info, err := os.Lstat(pin.Path)
		if err != nil {
			return nil, err
		}
		for _, credential := range p.CredentialSelectors {
			token, err := os.Lstat(credential)
			if err != nil {
				return nil, err
			}
			if os.SameFile(info, token) {
				return nil, errors.New("bootstrap raw manifest aliases a credential selector")
			}
		}
	}
	for _, credential := range p.CredentialSelectors {
		if censusOverlap(tx.directory, credential) {
			return nil, errors.New("bootstrap recovery namespace overlaps credential selector")
		}
	}
	journalPath := filepath.Join(tx.directory, "journal.json")
	if _, err := os.Lstat(tx.directory); err == nil {
		directory, e := bootstrapPrivateDirectory(tx.directory)
		if e != nil {
			return nil, e
		}
		if e = directory.Close(); e != nil {
			return nil, e
		}
		raw, _, e := bootstrapLegacyMutationFile(journalPath)
		if e == nil {
			tree, e := value.DecodeBytes(raw, 0)
			if e != nil {
				return nil, e
			}
			if e = unitControlUniqueKeys(tree, 0); e != nil {
				return nil, e
			}
			decoder := json.NewDecoder(bytes.NewReader(raw))
			decoder.DisallowUnknownFields()
			if e = decoder.Decode(&tx.journal); e != nil {
				return nil, e
			}
			if e = decoder.Decode(new(any)); e != io.EOF {
				return nil, errors.New("bootstrap recovery journal has trailing data")
			}
			if tx.journal.Schema != "cairn.unit-bootstrap-legacy-recovery/1" || tx.journal.Selection != id || len(tx.journal.Entries) > len(tx.selected) {
				return nil, errors.New("bootstrap recovery journal does not select this exact plan")
			}
			tx.prepared = true
		} else if errors.Is(e, os.ErrNotExist) && operation == "rollback" {
			// No exchange is possible before the complete journal is fsynced. Verify
			// every live original below and retain this incomplete preparation as data.
			tx.incomplete = true
		} else {
			return nil, errors.Join(e, errors.New("bootstrap incomplete recovery preparation; use fresh rollback, preserve evidence"))
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	if tx.prepared {
		seen := map[string]bool{}
		for i, entry := range tx.journal.Entries {
			if tx.selected[entry.Path] != entry.Original.SHA256 || seen[entry.Path] || entry.Backup != fmt.Sprintf("manifest-%02d.json", i) {
				return nil, errors.New("bootstrap recovery manifest coverage differs")
			}
			seen[entry.Path] = true
			if _, _, err := tx.observeEntry(entry); err != nil {
				return nil, err
			}
		}
		// A journal must cover every enabled original; a caller cannot omit one
		// and turn the journal into permission to accept enabled selection data.
		for path, pin := range tx.selected {
			if seen[path] {
				continue
			}
			raw, observed, err := bootstrapLegacyMutationFile(path)
			if err != nil || observed.SHA256 != pin {
				return nil, errors.Join(err, errors.New("bootstrap uncovered original manifest changed"))
			}
			manifest, err := value.DecodeBytes(raw, 0)
			if err != nil || manifest.Get("enabled") == nil || manifest.Get("enabled").Kind != value.Bool || manifest.Get("enabled").Boolean {
				return nil, errors.Join(err, errors.New("bootstrap journal omits enabled original"))
			}
		}
		return tx, nil
	}
	for _, path := range slices.Sorted(maps.Keys(tx.selected)) {
		raw, original, err := bootstrapLegacyMutationFile(path)
		if err != nil || original.SHA256 != tx.selected[path] {
			return nil, errors.Join(err, errors.New("bootstrap actual original raw pin changed"))
		}
		manifest, err := value.DecodeBytes(raw, 0)
		if err != nil || !value.SameText(manifest.Get("schema"), "cairn.inbox-recall-binding/2") || manifest.Get("enabled") == nil || manifest.Get("enabled").Kind != value.Bool {
			return nil, errors.Join(err, errors.New("bootstrap original /2 activation visibility is unknown"))
		}
		if !manifest.Get("enabled").Boolean {
			continue
		}
		disabled, err := bootstrapDisabledManifest(raw)
		if err != nil {
			return nil, err
		}
		entry := bootstrapLegacyJournalEntry{Path: path, Backup: fmt.Sprintf("manifest-%02d.json", len(tx.journal.Entries)), Original: original}
		tx.journal.Entries = append(tx.journal.Entries, entry)
		tx.disabledBytes[path] = disabled
	}
	return tx, nil
}

// observeEntry freshly hashes BOTH names. The original pin comes from the
// selected plan, and disabled bytes are derived from that actual raw original,
// not from a journal boolean or a caller-selected "already disabled" flag.
func (tx *bootstrapLegacyTransaction) observeEntry(entry bootstrapLegacyJournalEntry) (bool, bootstrapLegacyFileIdentity, error) {
	live, liveIdentity, err := bootstrapLegacyMutationFile(entry.Path)
	if err != nil {
		return false, liveIdentity, err
	}
	saved, savedIdentity, err := bootstrapLegacyMutationFile(filepath.Join(tx.directory, entry.Backup))
	if err != nil {
		return false, liveIdentity, err
	}
	original, disabled := live, saved
	originalIdentity, disabledIdentity := liveIdentity, savedIdentity
	applied := false
	if liveIdentity.SHA256 != entry.Original.SHA256 {
		applied = true
		original, disabled = saved, live
		originalIdentity, disabledIdentity = savedIdentity, liveIdentity
	}
	if originalIdentity != entry.Original || disabledIdentity != entry.Disabled {
		return false, liveIdentity, errors.New("bootstrap raw reverse manifest inode/bytes/metadata changed")
	}
	expected, err := bootstrapDisabledManifest(original)
	if err != nil || !bytes.Equal(expected, disabled) {
		return false, liveIdentity, errors.Join(err, errors.New("bootstrap disabled manifest is not derived from the selected raw original"))
	}
	return applied, liveIdentity, nil
}

func (tx *bootstrapLegacyTransaction) effectivePlan(p *UnitBootstrapPlan) (*UnitBootstrapPlan, error) {
	result := *p
	result.Associations = maps.Clone(p.Associations)
	for _, entry := range tx.journal.Entries {
		if !tx.prepared {
			continue
		}
		_, live, err := tx.observeEntry(entry)
		if err != nil {
			return nil, err
		}
		for name, association := range result.Associations {
			if association.Receipt == nil && association.Binding.Path == entry.Path {
				if association.Binding.SHA256 != entry.Original.SHA256 {
					return nil, errors.New("bootstrap recovery selection changed")
				}
				association.Binding.SHA256 = live.SHA256
				result.Associations[name] = association
			}
		}
	}
	return &result, nil
}

func bootstrapCreateRecoveryFile(path string, raw []byte, mode os.FileMode) error {
	parent, err := bootstrapPrivateDirectory(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer parent.Close()
	fd, err := syscall.Openat(int(parent.Fd()), filepath.Base(path), syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, uint32(mode))
	if err != nil {
		return err
	}
	file := os.NewFile(uintptr(fd), path)
	_, writeErr := file.Write(raw)
	syncErr := file.Sync()
	closeErr := file.Close()
	return errors.Join(writeErr, syncErr, closeErr, parent.Sync())
}

func (tx *bootstrapLegacyTransaction) prepare() error {
	if tx.prepared || len(tx.journal.Entries) == 0 {
		return nil
	}
	if tx.incomplete {
		return errors.New("bootstrap incomplete preparation is retained; no forward replay")
	}
	parent, err := bootstrapPrivateDirectory(filepath.Dir(tx.directory))
	if err != nil {
		return err
	}
	defer parent.Close()
	if err := syscall.Mkdirat(int(parent.Fd()), filepath.Base(tx.directory), 0700); err != nil {
		return err
	}
	if err := parent.Sync(); err != nil {
		return err
	}
	for i := range tx.journal.Entries {
		entry := &tx.journal.Entries[i]
		_, fresh, err := bootstrapLegacyMutationFile(entry.Path)
		if err != nil || fresh != entry.Original {
			return errors.Join(err, errors.New("bootstrap raw original changed before preparation"))
		}
		mode := os.FileMode(entry.Original.Mode & 07777)
		path := filepath.Join(tx.directory, entry.Backup)
		if err := bootstrapCreateRecoveryFile(path, tx.disabledBytes[entry.Path], mode); err != nil {
			return err
		}
		_, entry.Disabled, err = bootstrapLegacyMutationFile(path)
		if err != nil {
			return err
		}
	}
	raw, err := json.Marshal(tx.journal)
	if err != nil {
		return err
	}
	if err := bootstrapCreateRecoveryFile(filepath.Join(tx.directory, "journal.json"), raw, 0400); err != nil {
		return err
	}
	tx.prepared = true
	return nil
}

func (tx *bootstrapLegacyTransaction) change(restore bool, observe func(string) error) error {
	if !tx.prepared {
		return nil
	}
	entries := slices.Clone(tx.journal.Entries)
	if restore {
		slices.Reverse(entries)
	}
	for i, entry := range entries {
		applied, _, err := tx.observeEntry(entry)
		if err != nil {
			return err
		}
		if applied == !restore {
			continue
		}
		name := "legacy-disable"
		if restore {
			name = "legacy-restore"
		}
		phase := fmt.Sprintf("%s-%d", name, i)
		if observe != nil {
			if err := observe("before:" + phase); err != nil {
				return err
			}
		}
		if err := tx.exchange(entry); err != nil {
			return err
		}
		if observe != nil {
			if err := observe("after:" + phase); err != nil {
				return err
			}
		}
	}
	return nil
}

func (tx *bootstrapLegacyTransaction) exchange(entry bootstrapLegacyJournalEntry) error {
	_, live, err := tx.observeEntry(entry)
	if err != nil {
		return err
	}
	_, saved, err := bootstrapLegacyMutationFile(filepath.Join(tx.directory, entry.Backup))
	if err != nil {
		return err
	}
	a, err := bootstrapPrivateDirectory(filepath.Dir(entry.Path))
	if err != nil {
		return err
	}
	defer a.Close()
	b, err := bootstrapPrivateDirectory(tx.directory)
	if err != nil {
		return err
	}
	defer b.Close()
	for _, check := range []struct {
		parent   *os.File
		path     string
		identity bootstrapLegacyFileIdentity
	}{{a, entry.Path, live}, {b, filepath.Join(tx.directory, entry.Backup), saved}} {
		opened, err := check.parent.Stat()
		if err != nil {
			return err
		}
		named, err := os.Lstat(filepath.Dir(check.path))
		if err != nil || !os.SameFile(opened, named) {
			return errors.Join(err, errors.New("bootstrap raw reverse parent changed"))
		}
		var current unix.Stat_t
		if err := unix.Fstatat(int(check.parent.Fd()), filepath.Base(check.path), &current, unix.AT_SYMLINK_NOFOLLOW); err != nil {
			return err
		}
		x := check.identity
		if current.Dev != x.Device || current.Ino != x.Inode || current.Mode != x.Mode || current.Uid != x.UID || current.Gid != x.GID || current.Size != x.Size || current.Mtim.Sec != x.MtimeSec || current.Mtim.Nsec != x.MtimeNsec || current.Nlink != 1 {
			return errors.New("bootstrap raw reverse opened entry changed")
		}
	}
	if err := unix.Renameat2(int(a.Fd()), filepath.Base(entry.Path), int(b.Fd()), entry.Backup, unix.RENAME_EXCHANGE); err != nil {
		return err
	}
	if err := errors.Join(a.Sync(), b.Sync()); err != nil {
		return err
	}
	_, _, err = tx.observeEntry(entry)
	return err
}

func (tx *bootstrapLegacyTransaction) sameDrain(before, after, associationsBefore, associationsAfter map[string]string, p *UnitBootstrapPlan) bool {
	if !tx.prepared {
		return maps.Equal(before, after) && maps.Equal(associationsBefore, associationsAfter)
	}
	fresh := maps.Clone(after)
	associations := maps.Clone(associationsAfter)
	for _, entry := range tx.journal.Entries {
		if tx.prepared {
			if _, _, err := tx.observeEntry(entry); err != nil {
				return false
			}
		}
		for _, key := range []string{"file:" + entry.Path, "bootstrap-reference:" + entry.Path} {
			if before[key] == "" || fresh[key] == "" {
				return false
			}
			fresh[key] = before[key]
		}
		for name, association := range p.Associations {
			if association.Binding.Path == entry.Path {
				if associationsBefore[name] == "" || associations[name] == "" {
					return false
				}
				associations[name] = associationsBefore[name]
			}
		}
	}
	return maps.Equal(before, fresh) && maps.Equal(associationsBefore, associations)
}
