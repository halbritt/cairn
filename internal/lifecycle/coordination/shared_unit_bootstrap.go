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
	"time"

	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/sys/unix"
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
	Archives            []BootstrapMember                       `json:"archives"`
	WatchLock           BootstrapMember                         `json:"watch_lock"`
	CredentialSelectors []string                                `json:"credential_selectors"`
	Callbacks           json.RawMessage                         `json:"callbacks"`
	Associations        map[string]BootstrapRetainedAssociation `json:"associations,omitempty"`
	ExternalStates      []BootstrapExternalState                `json:"external_states,omitempty"`
	Transition          *BootstrapTransition                    `json:"transition,omitempty"`
}

type BootstrapExternalState struct {
	Member    string          `json:"member"`
	Role      string          `json:"role"`
	Directory string          `json:"directory"`
	Identity  BootstrapMember `json:"identity"`
}

// Retained associations select the original /2 manifest and literal commands.
// They are checked as data; they neither replace a drain nor mint a /3 receipt.
type BootstrapRetainedAssociation struct {
	Binding WatchCensusPin  `json:"binding"`
	Argv    WatchCensusPin  `json:"argv"`
	Receipt *WatchCensusPin `json:"receipt,omitempty"`
	Package *WatchCensusPin `json:"package,omitempty"`
}

// Both directories already contain their complete prepared selections. The
// exchange moves the original directory itself, retaining every opaque inode.
type BootstrapTransition struct {
	Target           WatchCensusPin         `json:"target"`
	OriginalIdentity BootstrapMember        `json:"original_identity"`
	TargetIdentity   BootstrapMember        `json:"target_identity"`
	Fence            SelectedUnitFenceScope `json:"fence"`
}

type UnitBootstrapResult struct {
	Schema           string            `json:"schema"`
	Operation        string            `json:"operation"`
	Inspected        bool              `json:"inspected"`
	Rechecked        bool              `json:"rechecked"`
	Published        bool              `json:"published"`
	OwnerFence       string            `json:"owner_fence"`
	Reverse          string            `json:"reverse"`
	Associations     map[string]string `json:"associations,omitempty"`
	Facts            map[string]string `json:"facts,omitempty"`
	Error            string            `json:"error,omitempty"`
	Active           string            `json:"active,omitempty"`
	Retained         string            `json:"retained,omitempty"`
	Reconciliation   string            `json:"reconciliation,omitempty"`
	DatabaseCoverage string            `json:"database_coverage,omitempty"`
}

// bootstrapDrainReceipt cannot be supplied by a caller or recovered from JSON.
// Its final reread is read-only evidence; no mutation consumes this receipt.
type bootstrapDrainReceipt struct {
	cli                  *CLI
	planPath, planSHA256 string
	facts, associations  map[string]string
}

func (c *CLI) RunUnitBootstrap(ctx context.Context, args []string, out io.Writer) int {
	return c.runUnitBootstrap(ctx, args, out, nil)
}

// The public entry point is the sole production caller and passes nil. Package
// tests supply a capability from the existing private fixture producer; no
// serialized value, argument or environment variable can supply one.
func (c *CLI) runUnitBootstrap(ctx context.Context, args []string, out io.Writer, fence *SelectedUnitFenceReceipt) int {
	r := UnitBootstrapResult{Schema: "cairn.unit-bootstrap-result/1", OwnerFence: "unknown", Reverse: "unsupported"}
	code := 1
	err := func() error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if len(args) != 5 || args[1] != "--plan" || args[3] != "--sha256" {
			code = 2
			return errors.New("usage: cairn-lifecycle unit-bootstrap inspect --plan ABSOLUTE_JSON --sha256 SHA256")
		}
		r.Operation = args[0]
		if args[0] == "switch" || args[0] == "reverse" || args[0] == "rollback" {
			err := c.bootstrapMutation(ctx, args[2], args[4], args[0], &r, fence, nil)
			if err == nil {
				code = 0
			}
			return err
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
	if err := jsontext.CheckUnicode(raw); err != nil {
		return nil, nil, err
	}
	tree, err := value.DecodeBytes(raw, 0)
	if err != nil {
		return nil, nil, err
	}
	if err := unitControlUniqueKeys(tree, 0); err != nil {
		return nil, nil, err
	}
	if err := bootstrapExtensionKeys(tree); err != nil {
		return nil, nil, err
	}
	fields := []string{"schema", "root", "current", "archives", "watch_lock", "credential_selectors", "callbacks"}
	for _, optional := range []string{"associations", "external_states", "transition"} {
		if tree.Get(optional) != nil {
			fields = append(fields, optional)
		}
	}
	if !exactReceiptObject(tree, fields...) ||
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
	if (p.Schema != "cairn.unit-bootstrap/1" && p.Schema != "cairn.unit-bootstrap/2") ||
		(p.Schema == "cairn.unit-bootstrap/1" && (p.Transition != nil || len(p.Associations) != 0)) ||
		len(p.Current.Pins) < 1 || len(p.Current.Pins) > 32 ||
		len(p.CredentialSelectors) < 1 || len(p.CredentialSelectors) > 32 {
		return nil, nil, errors.New("bootstrap schema or bounded selection is unknown")
	}
	if _, err := watchBindingNames(p.Current.Pins); err != nil {
		return nil, nil, err
	}
	if p.Schema == "cairn.unit-bootstrap/2" {
		if len(p.Associations) != len(p.Current.Pins) {
			return nil, nil, errors.New("bootstrap /2 requires every exact association")
		}
		for name := range p.Associations {
			if p.Current.Pins[name] == "" {
				return nil, nil, errors.New("bootstrap association selects unknown member")
			}
		}
	}
	if _, err := bootstrapStateDeclarations(&p); err != nil {
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

func bootstrapExtensionKeys(tree *value.Value) error {
	if value.SameText(tree.Get("schema"), "cairn.unit-bootstrap/1") && (tree.Get("associations") != nil || tree.Get("transition") != nil || tree.Get("external_states") != nil) {
		return errors.New("bootstrap /1 does not accept transition metadata")
	}
	if external := tree.Get("external_states"); external != nil {
		if external.Kind != value.Array || len(external.Items) > 64 {
			return errors.New("bootstrap external state declarations must be bounded and literal")
		}
		for _, state := range external.Items {
			if !exactReceiptObject(state, "member", "role", "directory", "identity") ||
				!exactReceiptObject(state.Get("identity"), "name", "type", "uid", "gid", "mode", "device", "inode") {
				return errors.New("bootstrap external state requires an exact profile and directory identity")
			}
		}
	}
	if associations := tree.Get("associations"); associations != nil {
		if associations.Kind != value.Object || len(associations.Members) > 32 {
			return errors.New("bootstrap exact association object required")
		}
		for _, member := range associations.Members {
			a := member.Value
			if !(exactReceiptObject(a, "binding", "argv") || exactReceiptObject(a, "binding", "argv", "receipt") || exactReceiptObject(a, "binding", "argv", "package")) || !exactReceiptObject(a.Get("binding"), "Path", "SHA256") || !exactReceiptObject(a.Get("argv"), "Path", "SHA256") {
				return errors.New("bootstrap association has unknown or nonliteral fields")
			}
			if receipt := a.Get("receipt"); receipt != nil && !exactReceiptObject(receipt, "Path", "SHA256") {
				return errors.New("bootstrap receipt selector fields unknown")
			}
			if pkg := a.Get("package"); pkg != nil && !exactReceiptObject(pkg, "Path", "SHA256") {
				return retainedPackageError()
			}
		}
	}
	if transition := tree.Get("transition"); transition != nil {
		if !exactReceiptObject(transition, "target", "original_identity", "target_identity", "fence") || !exactReceiptObject(transition.Get("target"), "Path", "SHA256") {
			return errors.New("bootstrap transition fields unknown")
		}
		for _, key := range []string{"original_identity", "target_identity"} {
			if !exactReceiptObject(transition.Get(key), "name", "type", "uid", "gid", "mode", "device", "inode") {
				return errors.New("bootstrap directory identity fields unknown")
			}
		}
		fence := transition.Get("fence")
		if !exactReceiptObject(fence, "UID", "Units", "Executables", "CommandSHA256", "Database", "DatabaseSystemID", "Window") {
			return errors.New("bootstrap exact lifecycle fence selection required")
		}
		pins := fence.Get("Executables")
		if pins == nil || pins.Kind != value.Array {
			return errors.New("bootstrap fence executables unknown")
		}
		for _, pin := range pins.Items {
			if !exactReceiptObject(pin, "Path", "SHA256") {
				return errors.New("bootstrap fence executable fields unknown")
			}
		}
	}
	return nil
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
	files           *unitVerifiedFiles
	receipts        map[string]*value.Value
	legacyPreflight bool
	plan            *UnitBootstrapPlan
	drain           *watchDrainRead
	credentials     map[string]os.FileInfo
	paths           *watchCensusRead
	accounts        map[string]bool
	legacy          map[string]bootstrapLegacyObservation
	externalStates  map[string]BootstrapExternalState
	binaryDirs      map[string]bool
}

func (b *bootstrapRead) reference(path string) error {
	if !compatibilityCanonical(path) || !censusWithin(b.plan.Root, path) {
		return errors.New("bootstrap reference is outside the observed private root")
	}
	return b.ownedReference(path)
}

func (b *bootstrapRead) ownedReference(path string) error {
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
	if err := b.paths.safePath(p.Root, true); err != nil {
		return err
	}
	if err := b.paths.safePath(p.Current.Directory, p.Schema == "cairn.unit-bootstrap/1"); err != nil {
		return err
	}
	if !censusWithin(p.Root, p.Current.Directory) {
		return errors.New("bootstrap original selection is outside root")
	}
	root, err := os.Lstat(p.Current.Directory)
	if err != nil {
		return err
	}
	s := root.Sys().(*syscall.Stat_t)
	if !root.IsDir() || s.Uid != uint32(os.Getuid()) || s.Mode&07000 != 0 || (root.Mode().Perm() != 0700 && root.Mode().Perm() != 0755) {
		return errors.New("bootstrap original directory ownership/mode is unknown")
	}
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
	members := slices.Clone(p.Archives)
	for _, archive := range members {
		if archive.Name == ".watch.lock" {
			return errors.New("bootstrap watcher lock cannot be hidden in opaque archives")
		}
	}
	if p.WatchLock.Type != "absent" {
		members = append(members, p.WatchLock)
	}
	for _, member := range members {
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
	if p.WatchLock.Type == "absent" {
		if p.Schema != "cairn.unit-bootstrap/2" || p.WatchLock != (BootstrapMember{Name: ".watch.lock", Type: "absent"}) {
			return errors.New("bootstrap absent lock requires an exact /2 observation")
		}
	} else if p.WatchLock.Name != ".watch.lock" || p.WatchLock.Mode != 0600 {
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
	if p.WatchLock.Type == "absent" {
		return nil
	}
	return r.lockFile(filepath.Join(p.Current.Directory, ".watch.lock"))
}

func (b *bootstrapRead) local() (map[string]string, error) {
	r := b.drain
	associations := map[string]string{}
	names, _ := watchBindingNames(b.plan.Current.Pins)
	states, memories := map[string]bool{}, map[string]bool{}
	identities := map[string]bool{}
	var affected []bootstrapAffectedState
	b.binaryDirs = map[string]bool{}
	if b.plan.Transition != nil {
		for _, pin := range b.plan.Transition.Fence.Executables {
			b.binaryDirs[filepath.Dir(pin.Path)] = true
		}
	}
	for _, name := range names {
		path := filepath.Join(b.plan.Current.Directory, name)
		config, err := b.config(path)
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
		manifest, memory, err := b.association(name, config)
		if err != nil {
			return nil, err
		}
		originalLegacy := b.plan.Schema == "cairn.unit-bootstrap/2" && value.SameText(manifest.Get("schema"), "cairn.inbox-recall-binding/2")
		for _, profile := range []*value.Value{config, memory} {
			if profile == nil {
				continue
			}
			home := profile.Get("config_home")
			if nativeNone(home) {
				continue
			}
			if home.Kind != value.String || !compatibilityCanonical(nativeText(home)) {
				return nil, errors.New("bootstrap account identity requires a canonical absolute directory")
			}
			if err := b.accountIdentity(nativeText(home)); err != nil {
				return nil, err
			}
		}
		affected = append(affected, bootstrapAffectedState{name, "coordination", nativeText(config.Get("state_dir"))})
		if memory != nil {
			affected = append(affected, bootstrapAffectedState{name, "memory", nativeText(memory.Get("state_dir"))})
		}
		binary := nativeText(config.Get("cairn"))
		b.binaryDirs[filepath.Dir(binary)] = true
		if selected := nativeText(manifest.Get("binary").Get("path")); selected != "" {
			b.binaryDirs[filepath.Dir(selected)] = true
		}
		if originalLegacy {
			if err := b.legacyExecutable(binary); err != nil {
				return nil, err
			}
		} else {
			if err := b.reference(binary); err != nil {
				return nil, err
			}
			var digest string
			var err error
			if b.files != nil {
				_, digest, err = b.files.Read(binary, -1, true)
			} else {
				digest, err = compatibilityOpenedBinary(binary, uint32(os.Getuid()))
			}
			if err != nil {
				return nil, err
			}
			r.facts["executable:"+binary] = digest
		}
		r.configs = append(r.configs, config)
		r.configPaths[config] = path
		r.memories[config] = memory
		states[nativeText(config.Get("state_dir"))] = true
		if memory != nil {
			memories[nativeText(memory.Get("state_dir"))] = true
		}
		mode := ":disabled-bound"
		if nativeNone(config.Get("inbox_recall_binding")) {
			mode = ":disabled-unbound"
		}
		if value.Truth(manifest.Get("enabled")) {
			mode = strings.Replace(mode, ":disabled-", ":enabled-", 1)
		}
		if memoryAbsent(manifest.Get("memory_config")) {
			mode += ":coordination-only"
		}
		associations[name] = nativeText(manifest.Get("schema")) + mode
	}
	if err := b.selectStateRoots(affected); err != nil {
		return nil, err
	}
	for directory := range states {
		if err := b.guardState(directory); err != nil {
			return nil, err
		}
	}
	for directory := range memories {
		if !states[directory] {
			if err := b.guardState(directory); err != nil {
				return nil, err
			}
		}
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

// A selected legacy directory may be 0755 inside the required private root.
// Keep that original mode intact. Open each component without following links,
// require the enclosing owned0700 root on the actual descriptor walk, and reuse
// the ordinary stable-FD content reader and configured JSON decoder.
func (b *bootstrapRead) config(path string) (*value.Value, error) {
	if b.plan.Schema != "cairn.unit-bootstrap/2" {
		return b.drain.file(path)
	}
	if filepath.Dir(path) != b.plan.Current.Directory || b.plan.Current.Pins[filepath.Base(path)] == "" || !censusWithin(b.plan.Root, path) {
		return nil, errors.New("bootstrap config is outside selected directory")
	}
	if err := b.reference(path); err != nil {
		return nil, err
	}
	before, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	parent, err := syscall.Open("/", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	defer func() { _ = syscall.Close(parent) }()
	parts := strings.Split(path[1:], "/")
	current := "/"
	private := false
	for _, part := range parts[:len(parts)-1] {
		fd, err := syscall.Openat(parent, part, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
		if err != nil {
			return nil, err
		}
		_ = syscall.Close(parent)
		parent = fd
		current = filepath.Join(current, part)
		var st syscall.Stat_t
		if err := syscall.Fstat(parent, &st); err != nil {
			return nil, err
		}
		if (st.Uid != 0 && st.Uid != uint32(os.Getuid())) || st.Mode&0022 != 0 && !(st.Uid == 0 && st.Mode&syscall.S_ISVTX != 0) {
			return nil, errors.New("bootstrap actual config ancestor unsafe")
		}
		if current == b.plan.Root {
			if st.Uid != uint32(os.Getuid()) || st.Mode&07777 != 0700 {
				return nil, errors.New("bootstrap actual root is not private")
			}
			private = true
		}
	}
	if !private {
		return nil, errors.New("bootstrap config has no selected private root")
	}
	fd, err := syscall.Openat(parent, parts[len(parts)-1], syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	var actual syscall.Stat_t
	if err := syscall.Fstat(fd, &actual); err != nil {
		_ = syscall.Close(fd)
		return nil, err
	}
	if !sameStatIdentity(*before.Sys().(*syscall.Stat_t), actual) || actual.Nlink != 1 {
		_ = syscall.Close(fd)
		return nil, errors.New("bootstrap config changed or aliases data before content read")
	}
	raw, digest, err := readOpenedFD(fd, path, 1048576, false, uint32(os.Getuid()))
	if err != nil {
		return nil, err
	}
	b.drain.facts["file:"+path] = digest
	decoded, err := b.drain.cli.profile().Decode(SiteSessionRead, raw)
	if err != nil || decoded == nil || decoded.Kind != value.Object {
		return nil, errors.Join(err, errors.New("bootstrap config must be an object"))
	}
	return decoded, nil
}

func (b *bootstrapRead) pinned(pin WatchCensusPin) (*value.Value, error) {
	if !censusDigest(pin.SHA256) {
		return nil, errors.New("bootstrap descriptor pin required")
	}
	if err := b.reference(pin.Path); err != nil {
		return nil, err
	}
	var v *value.Value
	var err error
	if b.files == nil {
		v, err = b.drain.file(pin.Path)
	} else {
		var raw []byte
		var digest string
		raw, digest, err = b.files.Read(pin.Path, 1048576, false)
		if err == nil {
			b.drain.facts["file:"+pin.Path] = digest
			v, err = b.drain.cli.profile().Decode(SiteSessionRead, raw)
		}
	}
	if err != nil || b.drain.facts["file:"+pin.Path] != pin.SHA256 || v == nil || v.Kind != value.Object {
		return nil, errors.Join(err, errors.New("bootstrap association pin changed"))
	}
	return v, nil
}

func (b *bootstrapRead) association(name string, config *value.Value) (*value.Value, *value.Value, error) {
	r := b.drain
	selected, explicit := b.plan.Associations[name]
	bound := nativeText(config.Get("inbox_recall_binding"))
	if bound == "" && !explicit {
		return nil, nil, errors.New("bootstrap unbound retained /2 association unsupported without exact binding and literal argv selection")
	}
	if bound != "" && !explicit {
		if b.plan.Schema != "cairn.unit-bootstrap/1" {
			return nil, nil, errors.New("bootstrap /2 requires explicit association selections")
		}
		if err := b.reference(bound); err != nil {
			return nil, nil, err
		}
		manifest, err := r.file(bound)
		if err != nil {
			return nil, nil, err
		}
		coords := manifest.Get("coordination_configs")
		if coords == nil || coords.Kind != value.Array {
			return nil, nil, errors.New("bootstrap bound association membership is unknown")
		}
		for _, desc := range append(slices.Clone(coords.Items), manifest.Get("memory_config")) {
			if err := b.reference(nativeText(desc.Get("path"))); err != nil {
				return nil, nil, err
			}
		}
		memory, err := r.disabledBinding(config)
		return manifest, memory, err
	}
	if bound != "" && selected.Binding.Path != bound {
		return nil, nil, errors.New("bootstrap binding selector mismatch")
	}
	manifest, err := b.pinned(selected.Binding)
	if err != nil {
		return nil, nil, err
	}
	if value.SameText(manifest.Get("schema"), "cairn.inbox-recall-binding/3") {
		if selected.Package != nil {
			return nil, nil, retainedPackageError()
		}
		memory, err := b.current3(config, manifest, selected)
		return manifest, memory, err
	}
	if !value.SameText(manifest.Get("schema"), "cairn.inbox-recall-binding/2") ||
		manifest.Get("enabled") == nil || manifest.Get("enabled").Kind != value.Bool || (manifest.Get("enabled").Boolean && (!b.legacyPreflight || bound == "")) {
		return nil, nil, errors.New("bootstrap retained association must be an actual disabled /2 manifest")
	}
	coords := manifest.Get("coordination_configs")
	if coords == nil || coords.Kind != value.Array || len(coords.Items) == 0 || len(coords.Items) > 8 {
		return nil, nil, errors.New("bootstrap retained coordinator membership unknown")
	}
	var memory *value.Value
	matched := 0
	if memoryAbsent(manifest.Get("memory_config")) && (bound != "" || value.Truth(manifest.Get("enabled"))) {
		return nil, nil, errors.New("coordination-only retained selection must be disabled and unbound")
	}
	selectedProfilePath := ""
	var coordinators []*value.Value
	for i, desc := range append([]*value.Value{manifest.Get("memory_config")}, coords.Items...) {
		if i == 0 && memoryAbsent(desc) {
			continue
		}
		if !exactReceiptObject(desc, "path", "sha256") {
			return nil, nil, errors.New("bootstrap retained profile descriptor unknown")
		}
		profile, err := b.legacyProfile(WatchCensusPin{Path: nativeText(desc.Get("path")), SHA256: nativeText(desc.Get("sha256"))})
		if err != nil {
			return nil, nil, err
		}
		if i == 0 {
			memory = profile
		}
		for _, key := range []string{"harness", "cairn", "socket", "token_file", "repo"} {
			if !nativeEqual(profile.Get(key), config.Get(key)) {
				return nil, nil, errors.New("bootstrap retained profile selectors differ")
			}
		}
		if bound == "" && !nativeNone(profile.Get("inbox_recall_binding")) || bound != "" && nativeText(profile.Get("inbox_recall_binding")) != bound {
			return nil, nil, errors.New("bootstrap retained bound/unbound profiles differ")
		}
		if i > 0 {
			coordinators = append(coordinators, profile)
			if nativeText(profile.Get("state_dir")) == nativeText(memory.Get("state_dir")) {
				return nil, nil, errors.New("bootstrap retained state/account identity ambiguous")
			}
			if nativeEqual(profile, config) {
				matched++
				selectedProfilePath = nativeText(desc.Get("path"))
			}
		}
	}
	if err := bootstrapLegacyProfileHomes(coordinators); err != nil {
		return nil, nil, err
	}
	if matched != 1 {
		return nil, nil, errors.New("bootstrap retained profile does not select this exact current config")
	}
	if bound != "" {
		if err := binding.ValidateConfigurationProfiles(binding.ConfigurationProfiles{Current: config, Memory: memory, Coordinators: coordinators, ManifestPath: config.Get("inbox_recall_binding").String, Resolve: b.drain.cli.profile().Routes.Filesystem().Resolve}); err != nil {
			return nil, nil, err
		}
	}
	if selected.Package != nil {
		if err := b.retainedPackage(selected, selectedProfilePath); err != nil {
			return nil, nil, err
		}
	} else if err := b.retainedRuntime(manifest, selected.Argv, selectedProfilePath); err != nil {
		return nil, nil, err
	}
	return manifest, memory, nil
}

func (b *bootstrapRead) current3(config, manifest *value.Value, selected BootstrapRetainedAssociation) (*value.Value, error) {
	if selected.Receipt == nil || selected.Argv != (WatchCensusPin{}) {
		return nil, errors.New("bootstrap current3 requires its actual compatibility selection")
	}
	receipt, err := b.pinned(*selected.Receipt)
	if err != nil {
		return nil, err
	}
	binding := receipt.Get("binding")
	if !exactReceiptObject(binding, "path", "sha256", "schema") || nativeText(binding.Get("path")) != selected.Binding.Path || nativeText(binding.Get("sha256")) != selected.Binding.SHA256 || !value.SameText(binding.Get("schema"), "cairn.inbox-recall-binding/3") {
		return nil, errors.New("bootstrap current3 receipt selects another binding")
	}
	for _, key := range []string{"binary", "memory_config", "coordination_configs"} {
		if !nativeEqual(manifest.Get(key), receipt.Get(key)) {
			return nil, errors.New("bootstrap current3 manifest/receipt differs")
		}
	}
	coords := manifest.Get("coordination_configs")
	if coords == nil || coords.Kind != value.Array || len(coords.Items) == 0 || len(coords.Items) > 8 {
		return nil, errors.New("bootstrap current3 profile coverage unknown")
	}
	configPath := ""
	var memory *value.Value
	if memoryAbsent(manifest.Get("memory_config")) && (!nativeNone(config.Get("inbox_recall_binding")) || value.Truth(manifest.Get("enabled"))) {
		return nil, errors.New("coordination-only current3 selection must be disabled and unbound")
	}
	for i, desc := range append([]*value.Value{manifest.Get("memory_config")}, coords.Items...) {
		if i == 0 && memoryAbsent(desc) {
			continue
		}
		if !exactReceiptObject(desc, "path", "sha256") {
			return nil, errors.New("bootstrap current3 profile descriptor unknown")
		}
		profile, err := b.pinned(WatchCensusPin{Path: nativeText(desc.Get("path")), SHA256: nativeText(desc.Get("sha256"))})
		if err != nil {
			return nil, err
		}
		if i == 0 {
			memory = profile
		}
		for _, key := range []string{"harness", "cairn", "socket", "token_file", "repo"} {
			if !nativeEqual(profile.Get(key), config.Get(key)) {
				return nil, errors.New("bootstrap current3 shared role selectors differ")
			}
		}
		if i > 0 && nativeEqual(profile, config) {
			if configPath != "" {
				return nil, errors.New("bootstrap current3 profile ambiguous")
			}
			configPath = nativeText(desc.Get("path"))
		}
	}
	if configPath == "" {
		return nil, errors.New("bootstrap current3 does not select exact active config")
	}
	root := nativeText(receipt.Get("snapshot_root"))
	members := receipt.Get("members")
	if !censusWithin(b.plan.Root, root) || members == nil || members.Kind != value.Object {
		return nil, errors.New("bootstrap current3 source closure outside private root")
	}
	for _, member := range members.Members {
		name := string(member.Key)
		if filepath.Base(name) != name || name == "." || name == ".." || strings.ContainsAny(name, "\\\x00") {
			return nil, errors.New("bootstrap current3 source member unsafe")
		}
		if err := b.reference(filepath.Join(root, name)); err != nil {
			return nil, err
		}
	}
	binary := receipt.Get("binary")
	if err := b.reference(nativeText(binary.Get("path"))); err != nil {
		return nil, err
	}
	// Each actual receipt is verified once in this observation. Every selected
	// source/profile/image is held and freshly hashed after the API bracket.
	if cached, ok := b.receipts[selected.Receipt.Path]; ok {
		return cached, nil
	}
	for _, member := range members.Members {
		name := string(member.Key)
		if b.files != nil {
			if _, _, err := b.files.Read(filepath.Join(root, name), 1048576, false); err != nil {
				return nil, err
			}
		}
	}
	if b.files != nil {
		if _, _, err := b.files.Read(nativeText(binary.Get("path")), -1, true); err != nil {
			return nil, err
		}
	}
	compat := &CompatibilityContext{Role: "coordination", Entrypoint: "coordination.py", ConfigPath: configPath, HookTimeout: 15, LaunchRoot: filepath.Dir(selected.Receipt.Path), Shell: "/bin/sh", Receipt: receipt, Profile: b.drain.cli.profile()}
	if err := compat.VerifyReceipt(b.drain.cli.profile()); err != nil {
		return nil, err
	}
	if nativeNone(config.Get("inbox_recall_binding")) {
		if err := compat.UnboundProfiles(b.drain.cli.profile()); err != nil {
			return nil, err
		}
		if b.receipts != nil {
			b.receipts[selected.Receipt.Path] = memory
		}
		return memory, nil
	}
	if nativeText(config.Get("inbox_recall_binding")) != selected.Binding.Path {
		return nil, errors.New("bootstrap current3 bound profile selects another manifest")
	}
	memory, err = b.drain.disabledBinding(config)
	if err == nil && b.receipts != nil {
		b.receipts[selected.Receipt.Path] = memory
	}
	return memory, err
}

func (b *bootstrapRead) retainedRuntime(manifest *value.Value, argvPin WatchCensusPin, configPath string) error {
	var bundle *value.Value
	for _, role := range []string{"engine", "bridge"} {
		desc := manifest.Get(role)
		if !exactReceiptObject(desc, "path", "sha256", "bundle") {
			return errors.New("bootstrap retained engine/bridge descriptor unknown")
		}
		selected := desc.Get("bundle")
		files := selected.Get("files")
		if !exactReceiptObject(selected, "schema", "sha256", "files") || !value.SameText(selected.Get("schema"), "cairn.lifecycle-bundle/1") || files == nil || files.Kind != value.Object || len(files.Members) != len(CompatibilityClosureMembers)-1 {
			return errors.New("bootstrap retained 17-member bundle coverage unknown")
		}
		digest, err := canonicalSortedJSONSHA256(files)
		if err != nil || digest != nativeText(selected.Get("sha256")) || bundle != nil && !nativeEqual(bundle, selected) {
			return errors.New("bootstrap retained engine/bridge bundle differs")
		}
		bundle = selected
		for _, name := range CompatibilityClosureMembers {
			if name == "lifecycle_compatibility.py" {
				continue
			}
			pin := nativeText(files.Get(name))
			path := filepath.Join(filepath.Dir(nativeText(desc.Get("path"))), name)
			if !censusDigest(pin) {
				return errors.New("bootstrap retained bundle member missing")
			}
			if err := b.reference(path); err != nil {
				return err
			}
			var actual string
			var err error
			if b.files != nil {
				_, actual, err = b.files.Read(path, 1048576, false)
			} else {
				_, actual, err = compatibilityOpened(path, 1048576, uint32(os.Getuid()))
			}
			if err != nil || actual != pin {
				return errors.Join(err, errors.New("bootstrap retained bundle member changed"))
			}
			b.drain.facts["retained-source:"+path] = actual
		}
		entry := "memory.py"
		if role == "bridge" {
			entry = "inbox_recall.py"
		}
		path := nativeText(desc.Get("path"))
		if filepath.Base(path) != entry || nativeText(desc.Get("sha256")) != nativeText(files.Get(entry)) {
			return errors.New("bootstrap retained entrypoint differs from source bundle")
		}
	}
	argv, err := b.pinned(argvPin)
	if err != nil {
		return err
	}
	roles := []string{"memory", "coordination"}
	if memoryAbsent(manifest.Get("memory_config")) {
		roles = []string{"coordination"}
	}
	if !exactReceiptObject(argv, roles...) {
		return errors.New("bootstrap retained literal role argv unknown")
	}
	for _, role := range roles {
		literal, err := hookStringVector(argv.Get(role))
		if err != nil || len(literal) == 0 {
			return errors.New("bootstrap retained argv invalid")
		}
		script, tail, err := retainedPythonFrame(literal)
		if err != nil || !compatibilityCanonical(literal[0]) || script != filepath.Join(filepath.Dir(nativeText(manifest.Get("engine").Get("path"))), role+".py") {
			return errors.New("bootstrap retained argv selects another source generation")
		}
		if role == "coordination" {
			if len(tail) == 0 || tail[0] != "hook" {
				return errors.New("bootstrap retained coordination entrypoint unknown")
			}
			tail = tail[1:]
		}
		if len(tail) != 2 || tail[0] != "--config" {
			return errors.New("bootstrap retained argv requires exact source-owned config selector")
		}
		expected := nativeText(manifest.Get("memory_config").Get("path"))
		if role == "coordination" {
			expected = configPath
		}
		if tail[1] != expected {
			return errors.New("bootstrap retained argv selects foreign profile")
		}
		if b.binaryDirs != nil {
			b.binaryDirs[filepath.Dir(literal[0])] = true
		}
	}
	return nil
}

type bootstrapAffectedState struct{ member, role, directory string }

func bootstrapStateDeclarations(p *UnitBootstrapPlan) (map[string]BootstrapExternalState, error) {
	declared := map[string]BootstrapExternalState{}
	paths := map[string]bool{}
	for _, root := range p.ExternalStates {
		key := root.Member + ":" + root.Role
		if p.Schema != "cairn.unit-bootstrap/2" || p.Current.Pins[root.Member] == "" ||
			(root.Role != "coordination" && root.Role != "memory") || !compatibilityCanonical(root.Directory) ||
			paths[root.Directory] || declared[key].Directory != "" || censusOverlap(root.Directory, p.Root) ||
			root.Identity.Name != filepath.Base(root.Directory) || root.Identity.Type != "directory" ||
			root.Identity.UID != uint32(os.Getuid()) || root.Identity.Mode != 0700 {
			return nil, errors.New("bootstrap external state declaration is duplicated, overlapping or not profile-bound private state")
		}
		declared[key], paths[root.Directory] = root, true
	}
	return declared, nil
}

func bootstrapExternalStateAt(root BootstrapExternalState, observe func(string, syscall.Stat_t) error) error {
	fd, err := bootstrapLegacyOpenObserved(root.Directory, observe)
	if err != nil {
		return err
	}
	defer syscall.Close(fd)
	var st syscall.Stat_t
	if err := syscall.Fstat(fd, &st); err != nil {
		return err
	}
	actual := BootstrapMember{Name: filepath.Base(root.Directory), Type: "directory", UID: st.Uid, GID: st.Gid,
		Mode: st.Mode & 07777, Device: uint64(st.Dev), Inode: st.Ino}
	if st.Mode&syscall.S_IFMT != syscall.S_IFDIR || actual != root.Identity {
		return errors.New("bootstrap external state directory identity or private mode changed")
	}
	return nil
}

func (b *bootstrapRead) externalStateIdentity(root BootstrapExternalState) error {
	err := bootstrapExternalStateAt(root, func(path string, st syscall.Stat_t) error {
		key := "external-state-ancestor:" + root.Directory + ":" + path
		fingerprint := fmt.Sprintf("%d:%d:%d:%d:%d", st.Dev, st.Ino, st.Mode, st.Uid, st.Gid)
		if previous := b.drain.facts[key]; previous != "" && previous != fingerprint {
			return errors.New("bootstrap external state ancestry changed during observation")
		}
		b.drain.facts[key] = fingerprint
		return nil
	})
	if err == nil {
		b.drain.facts["external-state:"+root.Directory] = fmt.Sprintf("%v", root.Identity)
	}
	return err
}

func (b *bootstrapRead) selectStateRoots(affected []bootstrapAffectedState) error {
	declared, err := bootstrapStateDeclarations(b.plan)
	if err != nil {
		return err
	}
	b.externalStates = map[string]BootstrapExternalState{}
	used := map[string]bool{}
	for _, state := range affected {
		if !compatibilityCanonical(state.directory) {
			return errors.New("bootstrap affected state directory must be canonical")
		}
		if censusWithin(b.plan.Root, state.directory) {
			continue
		}
		key := state.member + ":" + state.role
		root, ok := declared[key]
		if !ok || root.Directory != state.directory || used[key] {
			return errors.New("bootstrap external state is not declared for this exact profile")
		}
		for _, other := range affected {
			if (other.member != state.member || other.role != state.role) && censusOverlap(root.Directory, other.directory) {
				return errors.New("bootstrap external state overlaps another affected state profile")
			}
		}
		for home := range b.accounts {
			if censusOverlap(root.Directory, home) {
				return errors.New("bootstrap external state overlaps an account directory")
			}
		}
		for directory := range b.binaryDirs {
			if censusOverlap(root.Directory, directory) {
				return errors.New("bootstrap external state overlaps a pinned binary directory")
			}
		}
		if err := b.externalStateIdentity(root); err != nil {
			return err
		}
		used[key], b.externalStates[root.Directory] = true, root
	}
	if len(used) != len(declared) {
		return errors.New("bootstrap external state declaration does not select an affected profile")
	}
	return nil
}

func (b *bootstrapRead) guardState(directory string) error {
	// Validate ancestors before WalkDir: walking only the leaf can otherwise
	// traverse an intermediate symlink or writable parent before checking it.
	parent := filepath.Dir(directory)
	_, external := b.externalStates[directory]
	if err := b.paths.safePath(parent, !external); err != nil {
		return err
	}
	if _, err := os.Lstat(directory); err == nil {
		if err := b.paths.safePath(directory, true); err != nil {
			return err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
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
		if external {
			return b.ownedReference(path)
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
	return c.readBootstrapSelection(ctx, p, callbacks, nil)
}

func (c *CLI) readBootstrapSelection(ctx context.Context, p *UnitBootstrapPlan, callbacks []*value.Value, owned *LockLease) (map[string]string, map[string]string, error) {
	return c.readBootstrapSelectionPhase(ctx, p, callbacks, owned, false)
}

// Enabled original manifests are observable only by the stopped transaction's
// private preflight. This never mints an inspect/drain receipt for an enabled hook.
func (c *CLI) readBootstrapSelectionPhase(ctx context.Context, p *UnitBootstrapPlan, callbacks []*value.Value, owned *LockLease, legacyPreflight bool) (facts map[string]string, associations map[string]string, resultErr error) {
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	if c.Host == nil || c.profile() == nil {
		return nil, nil, &value.ProfileUnavailable{}
	}
	r := &watchDrainRead{cli: c, facts: map[string]string{}, locks: map[string]string{}, tokenPaths: map[string]bool{},
		memories: map[*value.Value]*value.Value{}, settled: map[*value.Value]*value.Value{}, configPaths: map[*value.Value]string{}}
	files := newUnitVerifiedFiles(ctx)
	defer func() { resultErr = errors.Join(resultErr, files.Close()) }()
	b := &bootstrapRead{plan: p, drain: r, credentials: map[string]os.FileInfo{}, paths: &watchCensusRead{inodes: map[string]string{}}, files: files, receipts: map[string]*value.Value{}, legacyPreflight: legacyPreflight}
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
	for _, read := range []func() error{func() error { return r.store(ctx) }, r.grantFacts, func() error { return bootstrapLockOwners(r, owned) }} {
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
	if err := b.legacyRecheck(); err != nil {
		return nil, nil, err
	}
	if err := files.Recheck(); err != nil {
		return nil, nil, err
	}
	initial := maps.Clone(r.facts)
	for account := range b.accounts {
		if err := b.accountIdentity(account); err != nil {
			return nil, nil, err
		}
	}
	for _, root := range b.externalStates {
		if err := b.externalStateIdentity(root); err != nil {
			return nil, nil, err
		}
	}
	if err := b.layout(); err != nil {
		return nil, nil, err
	}
	if !maps.Equal(initial, r.facts) {
		return nil, nil, errors.New("bootstrap layout changed during authenticated drain read")
	}
	return r.facts, associations, nil
}

// Only the exact exclusive flock visible on this open file description may be
// excluded from D3. Other owners, waiters and same-inode ambiguities refuse.
func bootstrapLockOwners(r *watchDrainRead, owned *LockLease) error {
	return bootstrapLockOwnersFrom(r, owned, r.openProcLocks)
}

func bootstrapLockOwnersFrom(r *watchDrainRead, owned *LockLease, open procLocksOpen) error {
	if owned == nil {
		return r.lockOwnersFrom(open)
	}
	info, err := owned.file.Stat()
	if err != nil {
		return err
	}
	st := info.Sys().(*syscall.Stat_t)
	fdinfo, err := os.ReadFile(fmt.Sprintf("/proc/self/fdinfo/%d", owned.file.Fd()))
	if err != nil {
		return err
	}
	var own []string
	for _, line := range strings.Split(string(fdinfo), "\n") {
		if strings.HasPrefix(line, "lock:") {
			fields := strings.Fields(strings.TrimPrefix(line, "lock:"))
			if len(fields) == 8 && fields[1] == "FLOCK" && fields[3] == "WRITE" && fields[4] == strconv.Itoa(os.Getpid()) {
				own = fields[1:]
			}
		}
	}
	if len(own) == 0 {
		return errors.New("bootstrap exact owned watcher flock is unknown")
	}
	root := r.cli.ProcRoot
	if root == "" {
		root = "/proc"
	}
	if root != "/proc" {
		return errors.New("bootstrap mutation requires actual kernel lock observations")
	}
	inodes, err := r.lockInodes()
	if err != nil {
		return err
	}
	inodes[st.Ino] = true
	rows, err := stableProcLocks(open, inodes)
	if err != nil {
		return fmt.Errorf("bootstrap kernel lock visibility is unknown: %w", err)
	}
	seen := 0
	var other []procLockRow
	for _, row := range rows {
		if row.inode == st.Ino {
			if row.fields != strings.Join(own, " ") {
				return errors.New("bootstrap watcher lock has another owner/waiter or ambiguous identity")
			}
			seen++
		} else {
			other = append(other, row)
		}
	}
	if seen != 1 {
		return errors.New("bootstrap owned watcher flock is not uniquely observed")
	}
	return r.refuseLockOwners(other)
}

func bootstrapDirectory(path string) (BootstrapMember, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return BootstrapMember{}, err
	}
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.IsDir() || info.Mode()&(os.ModeSymlink|os.ModeSetuid|os.ModeSetgid|os.ModeSticky) != 0 {
		return BootstrapMember{}, errors.New("bootstrap directory identity unknown")
	}
	return BootstrapMember{Name: filepath.Base(path), Type: "directory", UID: st.Uid, GID: st.Gid, Mode: uint32(info.Mode().Perm()), Device: uint64(st.Dev), Inode: st.Ino}, nil
}

func bootstrapIdentityAt(path string, expected BootstrapMember) error {
	actual, err := bootstrapDirectory(path)
	actual.Name = expected.Name
	if err != nil || actual != expected {
		return errors.Join(err, errors.New("bootstrap selected directory identity changed"))
	}
	return nil
}

// bootstrapMutation is also exercised by package tests using a privately
// captured fixture receipt. The exported CLI always captures its real host
// receipt here; no command-line field can supply or deserialize one.
func (c *CLI) bootstrapMutation(ctx context.Context, path, pin, operation string, result *UnitBootstrapResult, fence *SelectedUnitFenceReceipt, observe func(string) error) (err error) {
	p, callbacks, err := readUnitBootstrapPlan(path, pin)
	if err != nil {
		return err
	}
	if p.Schema != "cairn.unit-bootstrap/2" || p.Transition == nil {
		return errors.New("bootstrap mutation requires a /2 transition selection and fresh lifecycle fence")
	}
	originalPlan := p
	t := p.Transition
	if t.Fence.Database != "" || t.Fence.DatabaseSystemID != "" || t.Fence.Window <= 0 || t.Fence.Window > 30*time.Second {
		return errors.New("bootstrap selects lifecycle-only fence; database coverage must be not-selected")
	}
	target, targetCallbacks, err := readUnitBootstrapPlan(t.Target.Path, t.Target.SHA256)
	if err != nil {
		return err
	}
	if target.Transition != nil || target.Schema != "cairn.unit-bootstrap/2" || target.Root != p.Root ||
		!censusWithin(p.Root, path) || !censusWithin(p.Root, t.Target.Path) ||
		filepath.Dir(p.Current.Directory) != filepath.Dir(target.Current.Directory) || p.Current.Directory == target.Current.Directory ||
		t.OriginalIdentity.Name != filepath.Base(p.Current.Directory) || t.TargetIdentity.Name != filepath.Base(target.Current.Directory) ||
		t.OriginalIdentity.Type != "directory" || t.TargetIdentity.Type != "directory" || t.TargetIdentity.Mode != 0700 {
		return errors.New("bootstrap requires exact distinct prepared sibling selections in one private root")
	}
	for _, planPath := range []string{path, t.Target.Path} {
		if censusOverlap(planPath, p.Current.Directory) || censusOverlap(planPath, target.Current.Directory) {
			return errors.New("bootstrap selection plans must remain outside exchanged directories")
		}
	}
	if len(p.Current.Pins) != len(target.Current.Pins) {
		return errors.New("bootstrap transition must retain every selected binding name")
	}
	originalStates, err := bootstrapStateDeclarations(p)
	if err != nil {
		return err
	}
	targetStates, err := bootstrapStateDeclarations(target)
	if err != nil || !maps.Equal(originalStates, targetStates) {
		return errors.Join(err, errors.New("bootstrap transition changed an external state profile or directory identity"))
	}
	if len(target.Archives) != 0 || target.WatchLock.Type != "regular" {
		return errors.New("bootstrap forward target must contain only immutable configs and its actual watcher lock")
	}
	for name := range target.Current.Pins {
		if target.Associations[name].Receipt == nil || p.Associations[name].Receipt != nil {
			return errors.New("bootstrap transition requires selected retained2 original and current3 target")
		}
	}
	for name := range p.Current.Pins {
		if target.Current.Pins[name] == "" {
			return errors.New("bootstrap transition changed selected binding names")
		}
	}
	if !slices.Equal(p.CredentialSelectors, target.CredentialSelectors) || string(p.Callbacks) != string(target.Callbacks) {
		return errors.New("bootstrap transition credential/callback coverage differs")
	}
	if fence == nil {
		fence, err = CaptureSelectedUnitFence(ctx, t.Fence, nil)
		if err != nil {
			return err
		}
	}
	if err = fence.Recheck(ctx, t.Fence); err != nil {
		return err
	}
	result.OwnerFence = "selected-units-only"
	result.DatabaseCoverage = "not-selected"
	for key, val := range fence.Facts() {
		if result.Facts == nil {
			result.Facts = map[string]string{}
		}
		result.Facts["fence:"+key] = val
	}
	currentIdentity, targetIdentity := t.OriginalIdentity, t.TargetIdentity
	reverse := operation == "reverse" || operation == "rollback"
	if operation == "rollback" && bootstrapIdentityAt(p.Current.Directory, t.OriginalIdentity) == nil && bootstrapIdentityAt(target.Current.Directory, t.TargetIdentity) == nil {
		reverse = false
	}
	if reverse {
		currentIdentity, targetIdentity = targetIdentity, currentIdentity
		pCopy, targetCopy := *p, *target
		pCopy.Current.Directory, targetCopy.Current.Directory = target.Current.Directory, p.Current.Directory
		p, target = &targetCopy, &pCopy
		callbacks, targetCallbacks = targetCallbacks, callbacks
	}
	active, retained := p.Current.Directory, target.Current.Directory
	go3 := target
	if reverse {
		go3 = p
	}
	for name := range go3.Current.Pins {
		info, err := os.Lstat(filepath.Join(go3.Current.Directory, name))
		if err != nil || info.Mode() != 0400 {
			return errors.Join(err, errors.New("bootstrap forward target config must be immutable0400"))
		}
	}
	checkIdentity := func() error {
		if err := bootstrapIdentityAt(active, currentIdentity); err != nil {
			return err
		}
		return bootstrapIdentityAt(retained, targetIdentity)
	}
	if err = checkIdentity(); err != nil {
		return err
	}
	checkPair := func() error {
		// Reopen both selected plan pins before every composite observation.
		if _, _, err := readUnitBootstrapPlan(path, pin); err != nil {
			return err
		}
		if _, _, err := readUnitBootstrapPlan(t.Target.Path, t.Target.SHA256); err != nil {
			return err
		}
		return checkIdentity()
	}
	if err = checkPair(); err != nil {
		return err
	}
	lockPath := filepath.Join(active, ".watch.lock")
	if p.WatchLock.Type == "absent" {
		lockPath = filepath.Join(retained, ".watch.lock")
	}
	if p.WatchLock.Type != "absent" && target.WatchLock.Type != "absent" && p.WatchLock != target.WatchLock {
		return errors.New("bootstrap prepared selection split the existing watcher lock")
	}
	// The pre-lock pass observes metadata only. Full file/API observations are
	// made while holding the exact existing lock, then freshly at D6 and D7.
	for _, plan := range []*UnitBootstrapPlan{p, target} {
		r := &watchDrainRead{cli: c, facts: map[string]string{}, locks: map[string]string{}}
		b := &bootstrapRead{plan: plan, drain: r, credentials: map[string]os.FileInfo{}, paths: &watchCensusRead{inodes: map[string]string{}}}
		for _, selector := range plan.CredentialSelectors {
			info, e := os.Lstat(selector)
			if e != nil {
				return e
			}
			b.credentials[selector] = info
		}
		if err := b.layout(); err != nil {
			return err
		}
	}
	tx, err := newBootstrapLegacyTransaction(originalPlan, path, operation)
	if err != nil {
		return err
	}
	if tx.prepared && len(tx.journal.Entries) > 0 {
		result.Reconciliation = "retained legacy recovery data; preserve current manifests and re-establish stopped boundary before fresh rollback"
	}
	if operation == "switch" && tx.prepared {
		for _, entry := range tx.journal.Entries {
			applied, _, e := tx.observeEntry(entry)
			if e != nil {
				return e
			}
			if applied {
				return errors.New("bootstrap legacy disable is retained; use fresh rollback, never replay forward")
			}
		}
	}
	readSelection := func(plan *UnitBootstrapPlan, callbacks []*value.Value, owned *LockLease, preflight bool) (map[string]string, map[string]string, error) {
		if len(plan.Associations) > 0 {
			for _, association := range plan.Associations {
				if association.Receipt == nil {
					effective, e := tx.effectivePlan(plan)
					if e != nil {
						return nil, nil, e
					}
					return c.readBootstrapSelectionPhase(ctx, effective, callbacks, owned, preflight)
				}
				break
			}
		}
		return c.readBootstrapSelection(ctx, plan, callbacks, owned)
	}
	file, err := os.OpenFile(lockPath, os.O_RDWR|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return err
	}
	lease := &LockLease{file: file}
	if err = syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return errors.Join(err, file.Close())
	}
	defer func() { err = errors.Join(err, lease.Release()) }()
	for _, selected := range []*UnitBootstrapPlan{p, target} {
		if selected.WatchLock.Type == "absent" {
			continue
		}
		actual, e := os.Lstat(filepath.Join(selected.Current.Directory, ".watch.lock"))
		held, se := file.Stat()
		if e != nil || se != nil || !os.SameFile(actual, held) {
			return errors.Join(e, se, errors.New("bootstrap opened watcher lock changed"))
		}
	}
	facts, associations, err := readSelection(p, callbacks, lease, !reverse)
	if err != nil {
		return err
	}
	other, otherAssociations, err := readSelection(target, targetCallbacks, lease, !reverse)
	if err != nil {
		return err
	}
	for key, val := range facts {
		result.Facts[key] = val
	}
	result.Associations = associations
	result.Inspected = true
	pairP, pairTarget := p, target
	if !reverse {
		pairP, err = tx.effectivePlan(p)
	} else {
		pairTarget, err = tx.effectivePlan(target)
	}
	if err != nil {
		return err
	}
	if err = bootstrapProfilePairsPhase(c, pairP, pairTarget, !reverse); err != nil {
		return err
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
	legacyObserve := func(phase string) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if strings.HasPrefix(phase, "before:") {
			if err := fence.Recheck(ctx, t.Fence); err != nil {
				return err
			}
			if err := checkPair(); err != nil {
				return err
			}
		}
		if observe != nil {
			return observe(phase)
		}
		return nil
	}
	err = step("exchange", func() error {
		if err := checkPair(); err != nil {
			return err
		}
		fresh, assoc, err := readSelection(p, callbacks, lease, !reverse)
		if err != nil || !maps.Equal(facts, fresh) || !maps.Equal(associations, assoc) {
			return errors.Join(err, errors.New("D6 bootstrap drain invalidated before exchange"))
		}
		freshOther, freshOtherAssociations, err := readSelection(target, targetCallbacks, lease, !reverse)
		if err != nil || !maps.Equal(other, freshOther) || !maps.Equal(otherAssociations, freshOtherAssociations) {
			return errors.Join(err, errors.New("D6 bootstrap target invalidated before exchange"))
		}
		if err := fence.Recheck(ctx, t.Fence); err != nil {
			return err
		}
		if err := checkPair(); err != nil {
			return err
		}
		if operation == "rollback" && !reverse {
			if err := tx.change(true, legacyObserve); err != nil {
				return err
			}
			restored, restoredAssociations, e := readSelection(p, callbacks, lease, true)
			if e != nil || !tx.sameDrain(fresh, restored, assoc, restoredAssociations, p) {
				return errors.Join(e, errors.New("bootstrap raw original rollback changed protected facts"))
			}
			result.Rechecked = true
			result.Active = active
			result.Reverse = "original unchanged; raw original manifests restored"
			result.Reconciliation = "unpublished transition; exact original selection retained; recovery data preserved"
			return nil
		}
		if !reverse && len(tx.journal.Entries) > 0 {
			if err := step("legacy-prepare", tx.prepare); err != nil {
				return err
			}
			result.Reverse = "available; raw original manifests retained; use fresh rollback"
			result.Reconciliation = "legacy disable may be partial; preserve recovery data and use fresh rollback"
			if err := tx.change(false, legacyObserve); err != nil {
				return err
			}
			// This is a new observation after actual disabled publication. The only
			// allowed difference is the exactly derived manifest exchange, whose two
			// raw names and inode identities are verified again by the transaction.
			disabled, disabledAssociations, e := readSelection(p, callbacks, lease, false)
			if e != nil || !tx.sameDrain(fresh, disabled, assoc, disabledAssociations, p) {
				return errors.Join(e, errors.New("D6 bootstrap protected facts changed during legacy disable"))
			}
			disabledOther, disabledOtherAssociations, e := readSelection(target, targetCallbacks, lease, false)
			if e != nil || !maps.Equal(freshOther, disabledOther) || !maps.Equal(freshOtherAssociations, disabledOtherAssociations) {
				return errors.Join(e, errors.New("D6 bootstrap target changed during legacy disable"))
			}
			if err := fence.Recheck(ctx, t.Fence); err != nil {
				return err
			}
			if err := checkPair(); err != nil {
				return err
			}
		}
		parent, e := os.OpenRoot(filepath.Dir(active))
		if e != nil {
			return e
		}
		defer parent.Close()
		fd, e := parent.Open(".")
		if e != nil {
			return e
		}
		defer fd.Close()
		if e := checkPair(); e != nil {
			return e
		}
		if observe != nil {
			if err := observe("before:rename"); err != nil {
				return err
			}
		}
		// Bind the descriptors used by rename, not only path-based rereads.
		// A parent reparented after OpenRoot must not redirect this action to
		// unrelated entries left in the old directory.
		opened, e := fd.Stat()
		if e != nil {
			return e
		}
		named, e := os.Lstat(filepath.Dir(active))
		if e != nil || !named.IsDir() || !os.SameFile(opened, named) {
			return errors.Join(e, errors.New("bootstrap opened parent identity changed"))
		}
		for name, expected := range map[string]BootstrapMember{filepath.Base(active): currentIdentity, filepath.Base(retained): targetIdentity} {
			info, e := parent.Lstat(name)
			if e != nil {
				return e
			}
			st := info.Sys().(*syscall.Stat_t)
			actual := BootstrapMember{Name: expected.Name, Type: "directory", UID: st.Uid, GID: st.Gid, Mode: uint32(info.Mode().Perm()), Device: uint64(st.Dev), Inode: st.Ino}
			if !info.IsDir() || info.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky) != 0 || actual != expected {
				return errors.New("bootstrap opened directory entry identity changed")
			}
		}
		renameFD, activeName, retainedName := int(fd.Fd()), filepath.Base(active), filepath.Base(retained)
		if observe != nil {
			if err := observe("before:commit"); err != nil {
				return err
			}
		}
		for _, root := range originalPlan.ExternalStates {
			if err := bootstrapExternalStateAt(root, nil); err != nil {
				return err
			}
		}
		if err := fence.Recheck(ctx, t.Fence); err != nil {
			return err
		}
		if e := unix.Renameat2(renameFD, activeName, renameFD, retainedName, unix.RENAME_EXCHANGE); e != nil {
			result.Rechecked = true
			return e
		}
		result.Rechecked = true
		result.Published = true
		result.Active = active
		result.Retained = retained
		result.Reverse = "available; exact original directory retained"
		result.Reconciliation = "published; retain both directories/recovery data and use fresh rollback after any later error"
		return step("parent-sync", fd.Sync)
	})
	if err != nil {
		return err
	}
	if !result.Published {
		return nil
	}
	if e := bootstrapIdentityAt(active, targetIdentity); e != nil {
		return e
	}
	if e := bootstrapIdentityAt(retained, currentIdentity); e != nil {
		return e
	}
	// D7 verifies the complete resulting selections, not only their directory
	// names. Changed pins/state after publication remain a reconciliation error.
	selectedAfter, retainedAfter := *target, *p
	selectedAfter.Current.Directory, retainedAfter.Current.Directory = active, retained
	selectedFacts, selectedAssociations, e := readSelection(&selectedAfter, targetCallbacks, lease, false)
	if e != nil {
		return e
	}
	if _, _, e := readSelection(&retainedAfter, callbacks, lease, false); e != nil {
		return e
	}
	if reverse {
		// Keep original hooks disabled until both exchanged selections have passed
		// D7 under the same live fence. Raw restore then reuses the retained inode.
		if err := fence.Recheck(ctx, t.Fence); err != nil {
			return err
		}
		restoreObserve := func(phase string) error {
			if err := ctx.Err(); err != nil {
				return err
			}
			if strings.HasPrefix(phase, "before:") {
				if err := fence.Recheck(ctx, t.Fence); err != nil {
					return err
				}
				if err := bootstrapIdentityAt(active, targetIdentity); err != nil {
					return err
				}
				if err := bootstrapIdentityAt(retained, currentIdentity); err != nil {
					return err
				}
			}
			if observe != nil {
				return observe(phase)
			}
			return nil
		}
		if err := tx.change(true, restoreObserve); err != nil {
			return err
		}
		restored, restoredAssociations, e := readSelection(&selectedAfter, targetCallbacks, lease, true)
		if e != nil || !tx.sameDrain(selectedFacts, restored, selectedAssociations, restoredAssociations, &selectedAfter) {
			return errors.Join(e, errors.New("bootstrap raw original reverse changed protected facts"))
		}
		result.Reverse = "original restored; forward selection retained"
		if len(tx.journal.Entries) > 0 {
			result.Reverse = "original restored; raw original manifests restored; forward selection retained"
		}
	}
	result.Reconciliation = "complete; recovery data preserved; final read/action interval remains outside atomic coverage"
	return nil
}

func bootstrapProfilePairs(c *CLI, current, target *UnitBootstrapPlan) error {
	return bootstrapProfilePairsPhase(c, current, target, false)
}

func bootstrapProfilePairsPhase(c *CLI, current, target *UnitBootstrapPlan, preflight bool) error {
	for name := range current.Current.Pins {
		configs := make([]*value.Value, 0, 2)
		memories := []string{}
		for _, p := range []*UnitBootstrapPlan{current, target} {
			r := &watchDrainRead{cli: c, facts: map[string]string{}, tokenPaths: map[string]bool{}}
			b := &bootstrapRead{plan: p, drain: r, credentials: map[string]os.FileInfo{}, paths: &watchCensusRead{inodes: map[string]string{}}, legacyPreflight: preflight}
			for _, selector := range p.CredentialSelectors {
				info, err := os.Lstat(selector)
				if err != nil {
					return err
				}
				b.credentials[selector] = info
				r.tokenPaths[selector] = true
			}
			configPath := filepath.Join(p.Current.Directory, name)
			if err := b.reference(configPath); err != nil {
				return err
			}
			config, err := b.config(configPath)
			if err != nil {
				return err
			}
			_, memory, err := b.association(name, config)
			if err != nil {
				return err
			}
			configs = append(configs, config)
			memories = append(memories, nativeText(memory.Get("state_dir")))
		}
		if err := unitControlPairIdentity(configs, memories); err != nil {
			return err
		}
	}
	return nil
}
