package coordination

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// BootstrapMember describes an opaque original member without opening its body.
// It is observed selection data, never a qualified active profile or authority.
type BootstrapMember struct {
	Name   string `json:"name"`
	Type   string `json:"type"`
	UID    uint32 `json:"uid"`
	GID    uint32 `json:"gid"`
	Mode   uint32 `json:"mode"`
	Device uint64 `json:"device"`
	Inode  uint64 `json:"inode"`
}

type UnitBootstrapPlan struct {
	Schema  string `json:"schema"`
	Root    string `json:"root"`
	Current struct {
		Directory string            `json:"directory"`
		Pins      map[string]string `json:"pins"`
	} `json:"current"`
	Archives            []BootstrapMember `json:"archives"`
	WatchLock           BootstrapMember   `json:"watch_lock"`
	CredentialSelectors []string          `json:"credential_selectors"`
	Callbacks           json.RawMessage   `json:"callbacks"`
}

type UnitBootstrapResult struct {
	Schema       string            `json:"schema"`
	Operation    string            `json:"operation"`
	Inspected    bool              `json:"inspected"`
	Rechecked    bool              `json:"rechecked"`
	Published    bool              `json:"published"`
	OwnerFence   string            `json:"owner_fence"`
	Reverse      string            `json:"reverse"`
	Associations map[string]string `json:"associations,omitempty"`
	Facts        map[string]string `json:"facts,omitempty"`
	Error        string            `json:"error,omitempty"`
}

// bootstrapDrainReceipt cannot be supplied by a caller or recovered from JSON.
// Its final reread is read-only evidence; no mutation consumes this receipt.
type bootstrapDrainReceipt struct {
	cli                  *CLI
	planPath, planSHA256 string
	facts, associations  map[string]string
}

func (c *CLI) RunUnitBootstrap(ctx context.Context, args []string, out io.Writer) int {
	r := UnitBootstrapResult{Schema: "cairn.unit-bootstrap-result/1", OwnerFence: "unknown", Reverse: "unsupported"}
	code := 1
	err := func() error {
		if len(args) != 5 || args[1] != "--plan" || args[3] != "--sha256" {
			code = 2
			return errors.New("usage: cairn-lifecycle unit-bootstrap inspect --plan ABSOLUTE_JSON --sha256 SHA256")
		}
		r.Operation = args[0]
		if args[0] == "switch" || args[0] == "reverse" {
			return errors.New("unit bootstrap mutation unavailable: complete stopped/joined owner and restart-fence contract is not implemented")
		}
		if args[0] != "inspect" {
			code = 2
			return errors.New("unit bootstrap supports read-only inspect only")
		}
		receipt, err := c.captureBootstrapDrain(ctx, args[2], args[4])
		if err != nil {
			return err
		}
		r.Inspected = true
		r.Facts, r.Associations = maps.Clone(receipt.facts), maps.Clone(receipt.associations)
		if err := receipt.recheck(ctx); err != nil {
			return err
		}
		r.Rechecked, code = true, 0
		return nil
	}()
	if err != nil {
		r.Error = err.Error()
	}
	if err := json.NewEncoder(out).Encode(r); err != nil {
		return 1
	}
	return code
}

func readUnitBootstrapPlan(path, pin string) (*UnitBootstrapPlan, []*value.Value, error) {
	if !censusDigest(pin) || os.Getuid() != os.Geteuid() {
		return nil, nil, errors.New("bootstrap requires lowercase SHA256 and unchanged UID")
	}
	before, err := os.Lstat(path)
	if err != nil || !before.Mode().IsRegular() || before.Mode() != 0600 || before.Sys().(*syscall.Stat_t).Uid != uint32(os.Getuid()) || before.Sys().(*syscall.Stat_t).Nlink != 1 {
		return nil, nil, errors.Join(err, errors.New("bootstrap plan metadata/alias is unknown before content read"))
	}
	raw, actual, err := compatibilityOpened(path, 65536, uint32(os.Getuid()))
	if err != nil {
		return nil, nil, err
	}
	info, err := os.Lstat(path)
	if err != nil || info.Mode() != 0600 || actual != pin || !os.SameFile(before, info) {
		return nil, nil, errors.Join(err, errors.New("bootstrap plan pin or private mode changed"))
	}
	tree, err := value.DecodeBytes(raw, 0)
	if err != nil {
		return nil, nil, err
	}
	if err := unitControlUniqueKeys(tree, 0); err != nil {
		return nil, nil, err
	}
	if !exactReceiptObject(tree, "schema", "root", "current", "archives", "watch_lock", "credential_selectors", "callbacks") ||
		!exactReceiptObject(tree.Get("current"), "directory", "pins") {
		return nil, nil, errors.New("bootstrap unknown or nonliteral plan field; serialized receipts are not accepted")
	}
	archives := tree.Get("archives")
	if archives == nil || archives.Kind != value.Array || len(archives.Items) > 64 {
		return nil, nil, errors.New("bootstrap requires explicit bounded opaque archive inventory")
	}
	for _, item := range append(slices.Clone(archives.Items), tree.Get("watch_lock")) {
		if !exactReceiptObject(item, "name", "type", "uid", "gid", "mode", "device", "inode") {
			return nil, nil, errors.New("bootstrap member requires exact literal metadata fields")
		}
	}
	var p UnitBootstrapPlan
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&p); err != nil {
		return nil, nil, err
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return nil, nil, errors.New("bootstrap requires exactly one JSON plan")
	}
	if p.Schema != "cairn.unit-bootstrap/1" || len(p.Current.Pins) < 1 || len(p.Current.Pins) > 32 ||
		len(p.CredentialSelectors) < 1 || len(p.CredentialSelectors) > 32 {
		return nil, nil, errors.New("bootstrap schema or bounded selection is unknown")
	}
	if _, err := watchBindingNames(p.Current.Pins); err != nil {
		return nil, nil, err
	}
	callbacks := tree.Get("callbacks")
	if callbacks == nil || callbacks.Kind != value.Array || len(callbacks.Items) > 256 {
		return nil, nil, errors.New("D5 bootstrap callback census is unknown")
	}
	for _, ref := range callbacks.Items {
		if !exactReceiptObject(ref, "pid", "start", "boot") || ref.Get("pid").Kind != value.Int || ref.Get("start").Kind != value.Int || ref.Get("boot").Kind != value.String || nativeText(ref.Get("boot")) == "" {
			return nil, nil, errors.New("D5 bootstrap callback identity is unknown")
		}
		pid, e := strconv.ParseUint(ref.Get("pid").Integer, 10, 32)
		start, s := strconv.ParseUint(ref.Get("start").Integer, 10, 64)
		if e != nil || s != nil || pid <= 1 || start == 0 {
			return nil, nil, errors.New("D5 bootstrap callback PID/start is invalid")
		}
	}
	return &p, callbacks.Items, nil
}

func (c *CLI) captureBootstrapDrain(ctx context.Context, path, pin string) (*bootstrapDrainReceipt, error) {
	facts, associations, err := c.readBootstrapDrain(ctx, path, pin)
	if err != nil {
		return nil, err
	}
	return &bootstrapDrainReceipt{cli: c, planPath: path, planSHA256: pin, facts: facts, associations: associations}, nil
}

func (r *bootstrapDrainReceipt) recheck(ctx context.Context) error {
	if r == nil || r.cli == nil || r.facts == nil {
		return errors.New("D6 fresh private bootstrap drain receipt required")
	}
	facts, associations, err := r.cli.readBootstrapDrain(ctx, r.planPath, r.planSHA256)
	if err != nil {
		return fmt.Errorf("D6 bootstrap drain invalidated: %w", err)
	}
	if !maps.Equal(facts, r.facts) || !maps.Equal(associations, r.associations) {
		return errors.New("D6 bootstrap drain invalidated: layout/identity/D1-D5 facts changed")
	}
	return nil
}

type bootstrapRead struct {
	plan        *UnitBootstrapPlan
	drain       *watchDrainRead
	credentials map[string]os.FileInfo
	paths       *watchCensusRead
}

func (b *bootstrapRead) reference(path string) error {
	if !compatibilityCanonical(path) || !censusWithin(b.plan.Root, path) {
		return errors.New("bootstrap reference is outside the observed private root")
	}
	if err := b.paths.safePath(path, false); err != nil {
		return err
	}
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	s, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() || s.Uid != uint32(os.Getuid()) || s.Nlink != 1 {
		return errors.New("bootstrap reference type/owner/alias is unknown")
	}
	for _, credential := range b.credentials {
		if os.SameFile(info, credential) {
			return errors.New("bootstrap refuses credential or inode-alias content reads")
		}
	}
	b.drain.facts["bootstrap-reference:"+path] = fmt.Sprintf("%d:%d:%d:%d:%d:%d:%d:%d", s.Dev, s.Ino, s.Uid, s.Gid, s.Mode, s.Size, s.Mtim.Nano(), s.Ctim.Nano())
	return nil
}

func bootstrapStat(path string) (BootstrapMember, *syscall.Stat_t, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return BootstrapMember{}, nil, err
	}
	s, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() || s.Uid != uint32(os.Getuid()) || info.Mode()&0077 != 0 || s.Mode&07000 != 0 {
		return BootstrapMember{}, nil, errors.New("bootstrap opaque/lock member must be owned private regular data")
	}
	return BootstrapMember{filepath.Base(path), "regular", s.Uid, s.Gid, uint32(info.Mode().Perm()), uint64(s.Dev), s.Ino}, s, nil
}

func (b *bootstrapRead) layout() error {
	p, r := b.plan, b.drain
	for _, path := range []string{p.Root, p.Current.Directory} {
		if err := b.paths.safePath(path, true); err != nil {
			return err
		}
	}
	if !censusWithin(p.Root, p.Current.Directory) {
		return errors.New("bootstrap original selection is outside root")
	}
	root, err := os.Lstat(p.Current.Directory)
	if err != nil {
		return err
	}
	s := root.Sys().(*syscall.Stat_t)
	r.facts["bootstrap-directory"] = fmt.Sprintf("%d:%d:%d:%d:%d:%d", s.Dev, s.Ino, s.Uid, s.Gid, s.Mtim.Nano(), s.Ctim.Nano())
	expected := map[string]bool{}
	for name := range p.Current.Pins {
		expected[name] = true
		path := filepath.Join(p.Current.Directory, name)
		if err := b.reference(path); err != nil {
			return err
		}
		member, st, err := bootstrapStat(path)
		if err != nil || (member.Mode != 0600 && member.Mode != 0400) {
			return errors.Join(err, errors.New("bootstrap active JSON mode must be original0600 or readonly0400"))
		}
		r.facts["bootstrap-active:"+name] = fmt.Sprintf("%v:%d:%d:%d:%d", member, st.Nlink, st.Size, st.Mtim.Nano(), st.Ctim.Nano())
	}
	for _, member := range append(slices.Clone(p.Archives), p.WatchLock) {
		if filepath.Base(member.Name) != member.Name || strings.ContainsAny(member.Name, "\\\x00") || expected[member.Name] ||
			(member.Name != ".watch.lock" && (!strings.Contains(member.Name, ".before-") || strings.HasSuffix(member.Name, ".json"))) {
			return errors.New("bootstrap opaque archive name is unsafe, duplicate or active")
		}
		expected[member.Name] = true
		path := filepath.Join(p.Current.Directory, member.Name)
		if err := b.paths.safePath(path, false); err != nil {
			return err
		}
		actual, st, err := bootstrapStat(path)
		if err != nil || actual != member || (member.Name != ".watch.lock" && st.Nlink != 1) {
			return errors.Join(err, errors.New("bootstrap opaque/lock metadata changed"))
		}
		for _, credential := range b.credentials {
			info, err := os.Lstat(path)
			if err != nil || os.SameFile(info, credential) {
				return errors.Join(err, errors.New("bootstrap opaque/lock aliases a credential"))
			}
		}
		r.facts["bootstrap-metadata:"+member.Name] = fmt.Sprintf("%v:%d:%d:%d:%d", actual, st.Nlink, st.Size, st.Mtim.Nano(), st.Ctim.Nano())
	}
	if p.WatchLock.Name != ".watch.lock" || p.WatchLock.Mode != 0600 {
		return errors.New("bootstrap requires the actual existing private .watch.lock inode; initialization is unsupported")
	}
	entries, err := os.ReadDir(p.Current.Directory)
	if err != nil || len(entries) != len(expected) {
		return errors.Join(err, errors.New("bootstrap original layout membership changed"))
	}
	for _, entry := range entries {
		if !expected[entry.Name()] {
			return errors.New("bootstrap original layout has an unknown member")
		}
	}
	return r.lockFile(filepath.Join(p.Current.Directory, ".watch.lock"))
}

func (b *bootstrapRead) local() (map[string]string, error) {
	r := b.drain
	associations := map[string]string{}
	names, _ := watchBindingNames(b.plan.Current.Pins)
	states, memories := map[string]bool{}, map[string]bool{}
	identities := map[string]bool{}
	for _, name := range names {
		path := filepath.Join(b.plan.Current.Directory, name)
		config, err := r.file(path)
		if err != nil || r.facts["file:"+path] != b.plan.Current.Pins[name] {
			return nil, errors.Join(err, errors.New("bootstrap active JSON pin changed"))
		}
		if _, err := ValidateConfig(config); err != nil {
			return nil, err
		}
		identity, err := canonicalSortedJSONSHA256(nativeMap("binding", config.Get("binding"), "state_dir", config.Get("state_dir"), "harness", config.Get("harness"), "config_home", config.Get("config_home")))
		if err != nil || identities[identity] {
			return nil, errors.Join(err, errors.New("bootstrap active unit identity is duplicated or unknown"))
		}
		identities[identity] = true
		if b.credentials[nativeText(config.Get("token_file"))] == nil {
			return nil, errors.New("bootstrap actual credential selector is not covered")
		}
		if !censusWithin(b.plan.Root, nativeText(config.Get("socket"))) {
			return nil, errors.New("bootstrap API socket is outside observed private root")
		}
		if home := nativeText(config.Get("config_home")); home != "" {
			if !censusWithin(b.plan.Root, home) {
				return nil, errors.New("bootstrap configuration home is outside observed private root")
			}
			if err := b.paths.safePath(home, true); err != nil {
				return nil, err
			}
		}
		if nativeNone(config.Get("inbox_recall_binding")) {
			return nil, errors.New("bootstrap unbound retained /2 association unsupported; binding/literal-argv inventory is not a Go /3 receipt")
		}
		for _, ref := range []string{nativeText(config.Get("cairn")), nativeText(config.Get("inbox_recall_binding"))} {
			if err := b.reference(ref); err != nil {
				return nil, err
			}
		}
		manifest, err := r.file(nativeText(config.Get("inbox_recall_binding")))
		if err != nil {
			return nil, err
		}
		coords := manifest.Get("coordination_configs")
		if coords == nil || coords.Kind != value.Array {
			return nil, errors.New("bootstrap bound association membership is unknown")
		}
		for _, desc := range append(slices.Clone(coords.Items), manifest.Get("memory_config")) {
			if err := b.reference(nativeText(desc.Get("path"))); err != nil {
				return nil, err
			}
		}
		memory, err := r.disabledBinding(config)
		if err != nil {
			return nil, err
		}
		for _, directory := range []string{nativeText(config.Get("state_dir")), nativeText(memory.Get("state_dir"))} {
			if !compatibilityCanonical(directory) || !censusWithin(b.plan.Root, directory) {
				return nil, errors.New("bootstrap affected state directory is outside observed root")
			}
			if err := b.guardState(directory); err != nil {
				return nil, err
			}
		}
		binary := nativeText(config.Get("cairn"))
		digest, err := compatibilityOpenedBinary(binary, uint32(os.Getuid()))
		if err != nil {
			return nil, err
		}
		r.facts["executable:"+binary] = digest
		r.configs = append(r.configs, config)
		r.configPaths[config] = path
		r.memories[config] = memory
		states[nativeText(config.Get("state_dir"))], memories[nativeText(memory.Get("state_dir"))] = true, true
		associations[name] = nativeText(manifest.Get("schema")) + ":disabled-bound"
	}
	for directory := range states {
		files, err := r.tree(directory)
		if err != nil {
			return nil, err
		}
		if err := r.sessionFacts(directory, files); err != nil {
			return nil, err
		}
	}
	for directory := range memories {
		files, err := r.tree(directory)
		if err != nil {
			return nil, err
		}
		for _, path := range files {
			ledger, err := r.file(path)
			if err != nil {
				return nil, err
			}
			if err := r.collectGrantFacts(directory, path, ledger); err != nil {
				return nil, err
			}
		}
	}
	return associations, nil
}

func (b *bootstrapRead) guardState(directory string) error {
	return filepath.WalkDir(directory, func(path string, d os.DirEntry, err error) error {
		if errors.Is(err, os.ErrNotExist) && path == directory {
			return nil
		}
		if err != nil {
			return err
		}
		if d.IsDir() {
			return unitControlDirectory(path)
		}
		if strings.HasSuffix(path, ".lock") || strings.HasSuffix(path, ".cuelock") {
			// The retained ledger owns its lock modes. Like lockFile, observe
			// metadata/kernel ownership only; never open these bodies or repair
			// their permissions to make a content-reader predicate pass.
			info, err := os.Lstat(path)
			if err != nil {
				return err
			}
			st := info.Sys().(*syscall.Stat_t)
			if !info.Mode().IsRegular() || st.Uid != uint32(os.Getuid()) || st.Nlink != 1 {
				return errors.New("bootstrap state-lock owner/type/alias is unknown")
			}
			for _, credential := range b.credentials {
				if os.SameFile(info, credential) {
					return errors.New("bootstrap state-lock aliases a credential")
				}
			}
			b.drain.facts["bootstrap-state-lock:"+path] = fmt.Sprintf("%d:%d:%d:%d:%d:%d", st.Dev, st.Ino, st.Uid, st.Gid, st.Mode, st.Ctim.Nano())
			return nil
		}
		return b.reference(path)
	})
}

func (c *CLI) readBootstrapDrain(ctx context.Context, path, pin string) (map[string]string, map[string]string, error) {
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	if c.Host == nil || c.profile() == nil {
		return nil, nil, &value.ProfileUnavailable{}
	}
	p, callbacks, err := readUnitBootstrapPlan(path, pin)
	if err != nil {
		return nil, nil, err
	}
	r := &watchDrainRead{cli: c, facts: map[string]string{}, locks: map[string]string{}, tokenPaths: map[string]bool{},
		memories: map[*value.Value]*value.Value{}, settled: map[*value.Value]*value.Value{}, configPaths: map[*value.Value]string{}}
	b := &bootstrapRead{plan: p, drain: r, credentials: map[string]os.FileInfo{}, paths: &watchCensusRead{inodes: map[string]string{}}}
	for _, selector := range p.CredentialSelectors {
		if !compatibilityCanonical(selector) || !censusWithin(p.Root, selector) || b.credentials[selector] != nil {
			return nil, nil, errors.New("bootstrap credential selector is outside root or duplicated")
		}
		if err := b.paths.safePath(selector, false); err != nil {
			return nil, nil, err
		}
		info, err := os.Lstat(selector)
		if err != nil || !info.Mode().IsRegular() || info.Mode() != 0600 || info.Sys().(*syscall.Stat_t).Uid != uint32(os.Getuid()) {
			return nil, nil, errors.Join(err, errors.New("bootstrap credential selector metadata is unknown"))
		}
		b.credentials[selector], r.tokenPaths[selector] = info, true
		st := info.Sys().(*syscall.Stat_t)
		r.facts["credential-reference:"+selector] = fmt.Sprintf("%d:%d:%d:%d:%d", st.Dev, st.Ino, st.Uid, st.Gid, st.Mode)
	}
	if err := b.layout(); err != nil {
		return nil, nil, err
	}
	associations, err := b.local()
	if err != nil {
		return nil, nil, err
	}
	for _, read := range []func() error{func() error { return r.store(ctx) }, r.grantFacts, r.lockOwners} {
		if err := read(); err != nil {
			return nil, nil, err
		}
	}
	for i, ref := range callbacks {
		alive, err := c.processAlive(ref)
		if err != nil || alive {
			return nil, nil, errors.Join(err, errors.New("D5 bootstrap callback process has not been joined"))
		}
		if err := r.record(fmt.Sprintf("callback:%d", i), ref); err != nil {
			return nil, nil, err
		}
	}
	initial := maps.Clone(r.facts)
	if err := b.layout(); err != nil {
		return nil, nil, err
	}
	if !maps.Equal(initial, r.facts) {
		return nil, nil, errors.New("bootstrap layout changed during authenticated drain read")
	}
	return r.facts, associations, nil
}
