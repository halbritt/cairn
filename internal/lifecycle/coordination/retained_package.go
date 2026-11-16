package coordination

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const retainedPackageSchema = "cairn.lifecycle-retained-package/1"

type retainedPackageRead func(WatchCensusPin) ([]byte, error)

type retainedDataPackage struct {
	Pin             WatchCensusPin
	Object, Binding *value.Value
	Files           map[string]WatchCensusPin
}

func retainedPackageError() error {
	return errors.New("retained package association/evidence disagrees or is unsupported")
}

func retainedPackagePin(v *value.Value) (WatchCensusPin, error) {
	p := WatchCensusPin{Path: nativeText(v.Get("path")), SHA256: nativeText(v.Get("sha256"))}
	if !exactReceiptObject(v, "path", "sha256") || !compatibilityCanonical(p.Path) || !censusDigest(p.SHA256) {
		return p, retainedPackageError()
	}
	return p, nil
}

func retainedPackageJSON(raw []byte) (*value.Value, error) {
	if err := jsontext.CheckUnicode(raw); err != nil {
		return nil, err
	}
	return value.DecodeBytesWithObjectPolicy(raw, 0, value.DuplicateReject)
}

func retainedPackageValue(read retainedPackageRead, pin WatchCensusPin) (*value.Value, error) {
	raw, err := read(pin)
	if err != nil {
		return nil, err
	}
	return retainedPackageJSON(raw)
}

func retainedPackageKeys(v *value.Value) []string {
	if v == nil || v.Kind != value.Object {
		return nil
	}
	names := make([]string, 0, len(v.Members))
	for _, member := range v.Members {
		names = append(names, string(member.Key))
	}
	slices.Sort(names)
	return names
}

func retainedPackageMembers() []string {
	var names []string
	for _, name := range CompatibilityClosureMembers {
		if name != "lifecycle_compatibility.py" {
			names = append(names, name)
		}
	}
	slices.Sort(names)
	return names
}

func retainedPackageDataReader(ctx context.Context, read retainedPackageRead) retainedPackageRead {
	return func(pin WatchCensusPin) ([]byte, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		before, err := unitVerifiedPathStat(pin.Path)
		if err != nil {
			return nil, err
		}
		if err := unitVerifiedPolicy(before, 1048576, false); err != nil || before.Nlink != 1 || before.Mode&07000 != 0 {
			return nil, errors.Join(err, retainedPackageError())
		}
		raw, err := read(pin)
		if err != nil {
			return nil, err
		}
		after, err := unitVerifiedPathStat(pin.Path)
		if err != nil || !unitVerifiedSame(before, after) || ctx.Err() != nil {
			return nil, errors.Join(err, ctx.Err(), retainedPackageError())
		}
		return raw, nil
	}
}

func readRetainedPackage(ctx context.Context, pin WatchCensusPin, read, readProfile retainedPackageRead) (*retainedDataPackage, error) {
	read = retainedPackageDataReader(ctx, read)
	if _, err := read(pin); err != nil {
		return nil, err
	}
	files, err := install.ReadGeneration(ctx, install.Generation{Path: pin.Path, SHA256: pin.SHA256})
	if err != nil {
		return nil, err
	}
	filePins := map[string]WatchCensusPin{}
	for _, file := range files {
		if file.Executable {
			return nil, retainedPackageError()
		}
		filePins[file.Name] = WatchCensusPin{Path: file.Path, SHA256: file.SHA256}
	}
	packagePin := filePins["retained-package.json"]
	if packagePin.Path == "" {
		return nil, retainedPackageError()
	}
	pkg, err := retainedPackageValue(read, packagePin)
	if err != nil {
		return nil, err
	}
	if !exactReceiptObject(pkg, "schema", "host", "harness", "multiplicity", "primary", "binding", "profiles", "closures", "members", "evidence", "files") || !value.SameText(pkg.Get("schema"), retainedPackageSchema) {
		return nil, retainedPackageError()
	}
	names := retainedPackageKeys(pkg.Get("members"))
	n, multiplicity := len(names), pkg.Get("multiplicity")
	if multiplicity == nil || multiplicity.Kind != value.Int || multiplicity.Integer != fmt.Sprint(n) || (n != 1 && n != 3) || n == 3 && !value.SameText(pkg.Get("harness"), "codex") || !slices.Contains(names, nativeText(pkg.Get("primary"))) || !regexp.MustCompile(`^[a-z0-9-]+$`).MatchString(nativeText(pkg.Get("host"))) || !slices.Contains([]string{"codex", "claude", "opencode", "agy", "hermes"}, nativeText(pkg.Get("harness"))) {
		return nil, retainedPackageError()
	}
	for _, name := range []string{"profiles", "closures", "members", "evidence", "files"} {
		v := pkg.Get(name)
		if v == nil || v.Kind != value.Object || len(v.Members) == 0 || len(v.Members) > 128 {
			return nil, retainedPackageError()
		}
	}
	r := &retainedDataPackage{Pin: pin, Object: pkg, Files: filePins}
	if err := r.material(read); err != nil {
		return nil, err
	}
	bindingPin, err := retainedPackagePin(pkg.Get("binding"))
	if err != nil || bindingPin.SHA256 != r.Files["binding.json"].SHA256 {
		return nil, retainedPackageError()
	}
	r.Binding, err = retainedPackageValue(read, bindingPin)
	if err != nil {
		return nil, err
	}
	pins := map[string]string{bindingPin.Path: bindingPin.SHA256}
	if err := r.profiles(readProfile, pins); err != nil {
		return nil, err
	}
	for _, check := range []func(retainedPackageRead, map[string]string) error{r.closures, r.runtime, r.evidence} {
		if err := check(read, pins); err != nil {
			return nil, err
		}
	}
	return r, nil
}

func (r *retainedDataPackage) material(read retainedPackageRead) error {
	root := filepath.Dir(r.Pin.Path)
	if filepath.Base(r.Pin.Path) != "manifest.json" {
		return retainedPackageError()
	}
	expected := append(retainedPackageMembers(), "binding.json", "lifecycle-bundle.json", "retained-argv.json")
	expected = append(expected, retainedPackageKeys(r.Object.Get("profiles"))...)
	expected = append(expected, retainedPackageKeys(r.Object.Get("evidence"))...)
	slices.Sort(expected)
	if !slices.Equal(expected, retainedPackageKeys(r.Object.Get("files"))) {
		return retainedPackageError()
	}
	actual := []string{}
	for name := range r.Files {
		if name != "retained-package.json" {
			actual = append(actual, name)
		}
	}
	slices.Sort(actual)
	if !slices.Equal(actual, expected) {
		return errors.New("retained package has undeclared or missing material")
	}
	for _, name := range expected {
		pin := WatchCensusPin{Path: filepath.Join(root, name), SHA256: nativeText(r.Object.Get("files").Get(name))}
		if name == "." || name == ".." || filepath.Base(name) != name || strings.Contains(name, `\`) || !censusDigest(pin.SHA256) {
			return retainedPackageError()
		}
		if _, err := read(pin); err != nil {
			return err
		}
		if r.Files[name] != pin {
			return retainedPackageError()
		}
	}
	return nil
}

func (r *retainedDataPackage) profiles(read retainedPackageRead, pins map[string]string) error {
	b := r.Binding
	if !exactReceiptObject(b, "schema", "enabled", "engine", "bridge", "memory_config", "coordination_configs") || !value.SameText(b.Get("schema"), "cairn.inbox-recall-binding/2") || b.Get("enabled").Kind != value.Bool || b.Get("coordination_configs").Kind != value.Array || len(b.Get("coordination_configs").Items) < 1 || len(b.Get("coordination_configs").Items) > 8 {
		return retainedPackageError()
	}
	profiles := append([]*value.Value{b.Get("memory_config")}, b.Get("coordination_configs").Items...)
	count := 0
	for _, desc := range profiles {
		if memoryAbsent(desc) {
			if value.Truth(b.Get("enabled")) {
				return retainedPackageError()
			}
			continue
		}
		pin, err := retainedPackagePin(desc)
		if err != nil {
			return err
		}
		matched := 0
		for _, member := range r.Object.Get("profiles").Members {
			if nativeEqual(member.Value, desc) && r.Files[string(member.Key)].SHA256 == pin.SHA256 {
				matched++
			}
		}
		raw, err := read(pin)
		if err != nil {
			return err
		}
		if err := jsontext.CheckUnicode(raw); err != nil {
			return err
		}
		// Preserve the existing original-profile duplicate-last reader policy.
		config, err := value.DecodeBytesWithObjectPolicy(raw, 0, value.DuplicateLast)
		if err != nil {
			return err
		}
		bound := config.Get("inbox_recall_binding")
		if matched != 1 || !nativeEqual(config.Get("harness"), r.Object.Get("harness")) || !nativeNone(bound) && !value.SameText(bound, "") && !nativeEqual(bound, r.Object.Get("binding").Get("path")) {
			return retainedPackageError()
		}
		pins[pin.Path] = pin.SHA256
		count++
	}
	if count != len(retainedPackageKeys(r.Object.Get("profiles"))) {
		return retainedPackageError()
	}
	return nil
}

func (r *retainedDataPackage) closures(read retainedPackageRead, pins map[string]string) error {
	for _, selected := range r.Object.Get("closures").Members {
		root, bundle := string(selected.Key), selected.Value
		if !exactReceiptObject(bundle, "schema", "sha256", "files", "manifest") {
			return retainedPackageError()
		}
		digest, err := canonicalSortedJSONSHA256(bundle.Get("files"))
		if !compatibilityCanonical(root) || !value.SameText(bundle.Get("schema"), "cairn.lifecycle-bundle/1") || !slices.Equal(retainedPackageKeys(bundle.Get("files")), retainedPackageMembers()) || err != nil || digest != nativeText(bundle.Get("sha256")) {
			return retainedPackageError()
		}
		manifest, err := retainedPackagePin(bundle.Get("manifest"))
		if err != nil || manifest.Path != filepath.Join(root, "lifecycle-bundle.json") {
			return retainedPackageError()
		}
		sidecar, err := retainedPackageValue(read, manifest)
		if err != nil || !nativeEqual(sidecar, retainedClosureBundle(bundle)) {
			return errors.Join(err, retainedPackageError())
		}
		pins[manifest.Path] = manifest.SHA256
		for _, file := range bundle.Get("files").Members {
			pin := WatchCensusPin{Path: filepath.Join(root, string(file.Key)), SHA256: nativeText(file.Value)}
			if !censusDigest(pin.SHA256) {
				return retainedPackageError()
			}
			if _, err := read(pin); err != nil {
				return err
			}
			pins[pin.Path] = pin.SHA256
		}
	}
	for role, entry := range map[string]string{"engine": "memory.py", "bridge": "inbox_recall.py"} {
		desc := r.Binding.Get(role)
		bundle := r.Object.Get("closures").Get(filepath.Dir(nativeText(desc.Get("path"))))
		if !exactReceiptObject(desc, "path", "sha256", "bundle") || bundle == nil || !nativeEqual(desc.Get("bundle"), retainedClosureBundle(bundle)) || filepath.Base(nativeText(desc.Get("path"))) != entry || nativeText(desc.Get("sha256")) != nativeText(bundle.Get("files").Get(entry)) {
			return retainedPackageError()
		}
	}
	bundle, err := retainedPackageValue(read, r.Files["lifecycle-bundle.json"])
	if err != nil || !nativeEqual(bundle, r.Binding.Get("engine").Get("bundle")) {
		return retainedPackageError()
	}
	for _, file := range bundle.Get("files").Members {
		if r.Files[string(file.Key)].SHA256 != nativeText(file.Value) {
			return retainedPackageError()
		}
	}
	return nil
}

func retainedClosureBundle(closure *value.Value) *value.Value {
	bundle := &value.Value{Kind: value.Object}
	for _, name := range []string{"schema", "sha256", "files"} {
		bundle.Set(name, closure.Get(name))
	}
	return bundle
}

func retainedFacade(delegate string) ([]byte, error) {
	if strings.ContainsAny(delegate, "'\\\r\n") {
		return nil, retainedPackageError()
	}
	return []byte("#!/usr/bin/python3\n\"\"\"Select the verified Cairn coordination bundle for native hooks.\"\"\"\nfrom runpy import run_path\n\nglobals().update(run_path(\n    '" + delegate + "',\n    run_name=__name__,\n))\n"), nil
}

func (r *retainedDataPackage) runtimeRole(read retainedPackageRead, role string, runtime *value.Value, pins map[string]string) error {
	if !exactReceiptObject(runtime, "entry", "delegate", "closure") {
		return retainedPackageError()
	}
	entry, err := retainedPackagePin(runtime.Get("entry"))
	if err != nil {
		return err
	}
	root := nativeText(runtime.Get("closure"))
	bundle := r.Object.Get("closures").Get(root)
	if bundle == nil {
		return retainedPackageError()
	}
	raw, err := read(entry)
	if err != nil {
		return err
	}
	expected := nativeText(bundle.Get("files").Get(role + ".py"))
	if nativeNone(runtime.Get("delegate")) {
		if filepath.Dir(entry.Path) != root || entry.SHA256 != expected {
			return retainedPackageError()
		}
	} else {
		delegate, err := retainedPackagePin(runtime.Get("delegate"))
		facade, facadeErr := retainedFacade(delegate.Path)
		if err != nil || facadeErr != nil || role != "coordination" || delegate != (WatchCensusPin{Path: filepath.Join(root, "coordination.py"), SHA256: expected}) || !bytes.Equal(raw, facade) {
			return retainedPackageError()
		}
		if _, err := read(delegate); err != nil {
			return err
		}
	}
	pins[entry.Path] = entry.SHA256
	return nil
}

func (r *retainedDataPackage) runtime(read retainedPackageRead, pins map[string]string) error {
	roles := []string{"coordination", "memory"}
	if memoryAbsent(r.Binding.Get("memory_config")) {
		roles = []string{"coordination"}
	}
	seen := map[string]bool{}
	for _, declared := range r.Object.Get("members").Members {
		member := declared.Value
		if !regexp.MustCompile(`^[a-z][a-z0-9-]{0,63}$`).MatchString(string(declared.Key)) || !exactReceiptObject(member, "config", "argv", "runtime") || !slices.Equal(retainedPackageKeys(member.Get("argv")), roles) || !slices.Equal(retainedPackageKeys(member.Get("runtime")), roles) {
			return retainedPackageError()
		}
		pin, err := retainedPackagePin(member.Get("config"))
		if err != nil || seen[pin.Path] {
			return retainedPackageError()
		}
		matched := false
		for _, config := range r.Binding.Get("coordination_configs").Items {
			matched = matched || nativeEqual(config, member.Get("config"))
		}
		if !matched {
			return retainedPackageError()
		}
		seen[pin.Path] = true
		for _, role := range roles {
			if err := r.runtimeRole(read, role, member.Get("runtime").Get(role), pins); err != nil {
				return err
			}
			argv, err := hookStringVector(member.Get("argv").Get(role))
			if err != nil {
				return err
			}
			script, tail, err := retainedPythonFrame(argv)
			expected := []string{"--config", nativeText(r.Binding.Get("memory_config").Get("path"))}
			if role == "coordination" {
				expected = []string{"hook", "--config", pin.Path}
			}
			if err != nil || !compatibilityCanonical(argv[0]) || script != nativeText(member.Get("runtime").Get(role).Get("entry").Get("path")) || !slices.Equal(tail, expected) {
				return retainedPackageError()
			}
		}
	}
	if len(seen) != len(r.Binding.Get("coordination_configs").Items) {
		return retainedPackageError()
	}
	primary := r.Object.Get("members").Get(nativeText(r.Object.Get("primary")))
	argv, err := retainedPackageValue(read, r.Files["retained-argv.json"])
	if err != nil || !nativeEqual(argv, primary.Get("argv")) {
		return retainedPackageError()
	}
	return nil
}

func (r *retainedDataPackage) evidence(read retainedPackageRead, pins map[string]string) error {
	names := retainedPackageKeys(r.Object.Get("members"))
	expected := []string{"native-representation.json", "provenance.json"}
	for _, name := range names {
		expected = append(expected, "argv-"+name+".json")
	}
	slices.Sort(expected)
	if !slices.Equal(expected, retainedPackageKeys(r.Object.Get("evidence"))) {
		return retainedPackageError()
	}
	records := map[string]*value.Value{}
	for _, name := range expected {
		declaration := r.Object.Get("evidence").Get(name)
		members := names
		if strings.HasPrefix(name, "argv-") {
			members = []string{strings.TrimSuffix(strings.TrimPrefix(name, "argv-"), ".json")}
		}
		actual, err := hookStringVector(declaration.Get("members"))
		if !exactReceiptObject(declaration, "sha256", "members") || err != nil || !slices.Equal(actual, members) || nativeText(declaration.Get("sha256")) != r.Files[name].SHA256 {
			return retainedPackageError()
		}
		records[name], err = retainedPackageValue(read, r.Files[name])
		if err != nil {
			return err
		}
	}
	for _, name := range names {
		if !nativeEqual(records["argv-"+name+".json"], r.Object.Get("members").Get(name).Get("argv")) {
			return retainedPackageError()
		}
	}
	return r.nativeEvidence(records, names, pins)
}

func (r *retainedDataPackage) nativeEvidence(records map[string]*value.Value, names []string, pins map[string]string) error {
	native, provenance := records["native-representation.json"], records["provenance.json"]
	if native == nil || native.Kind != value.Object || provenance == nil || provenance.Kind != value.Object || native.Get("roles") == nil || native.Get("roles").Kind != value.Object {
		return retainedPackageError()
	}
	nativeNames, err := hookStringVector(native.Get("members"))
	if err != nil {
		return err
	}
	slices.Sort(nativeNames)
	wanted := []string{}
	for _, name := range names {
		wanted = append(wanted, nativeText(r.Object.Get("host"))+"/"+name)
	}
	provenanceNames, err := hookStringVector(provenance.Get("members"))
	if err != nil {
		return err
	}
	slices.Sort(provenanceNames)
	if !slices.Equal(nativeNames, wanted) || !slices.Equal(provenanceNames, names) || !nativeEqual(provenance.Get("native_representation"), native) {
		return retainedPackageError()
	}
	for _, flag := range []string{"actual_native_command_text_rewritten", "original_profile_bytes_rewritten", "source17_bytes_rewritten", "source17_canonicalization_into_native_argv"} {
		v := provenance.Get(flag)
		if v == nil || v.Kind != value.Bool || v.Boolean {
			return retainedPackageError()
		}
	}
	correspondence := provenance.Get("profile_correspondence")
	if correspondence == nil || correspondence.Kind != value.Array || len(correspondence.Items) != len(r.Object.Get("profiles").Members) {
		return retainedPackageError()
	}
	seen := map[string]bool{}
	for _, row := range correspondence.Items {
		name := nativeText(row.Get("generation_name"))
		pin := r.Object.Get("profiles").Get(name)
		if seen[name] || pin == nil || nativeText(row.Get("original_path")) != nativeText(pin.Get("path")) || nativeText(row.Get("sha256")) != nativeText(pin.Get("sha256")) {
			return retainedPackageError()
		}
		seen[name] = true
	}
	var vectors [][]string
	for _, member := range r.Object.Get("members").Members {
		for _, role := range member.Value.Get("argv").Members {
			vector, err := hookStringVector(role.Value)
			if err != nil {
				return err
			}
			vectors = append(vectors, vector)
		}
	}
	if err := retainedNativeClaims(native, pins, vectors, nativeText(r.Object.Get("harness"))); err != nil {
		return err
	}
	for _, role := range native.Get("roles").Members {
		if role.Value == nil || role.Value.Kind != value.Object {
			return retainedPackageError()
		}
		var roleVectors [][]string
		for _, member := range r.Object.Get("members").Members {
			if argv := member.Value.Get("argv").Get(string(role.Key)); argv != nil {
				vector, err := hookStringVector(argv)
				if err != nil {
					return err
				}
				roleVectors = append(roleVectors, vector)
			}
		}
		if err := retainedNativeClaims(role.Value, pins, roleVectors, nativeText(r.Object.Get("harness"))); err != nil {
			return err
		}
		variants := role.Value.Get("member_variants")
		if variants == nil {
			continue
		}
		if !slices.Equal(retainedPackageKeys(variants), names) {
			return retainedPackageError()
		}
		for _, variant := range variants.Members {
			if variant.Value == nil || variant.Value.Kind != value.Object {
				return retainedPackageError()
			}
			for _, field := range []string{"declared_profile", "actual_binding"} {
				if claimed := variant.Value.Get(field); claimed != nil {
					config := r.Object.Get("members").Get(string(variant.Key)).Get("config")
					if !nativeEqual(claimed.Get("path"), config.Get("path")) || !nativeEqual(claimed.Get("sha256"), config.Get("sha256")) {
						return retainedPackageError()
					}
				}
			}
			argv, err := hookStringVector(r.Object.Get("members").Get(string(variant.Key)).Get("argv").Get(string(role.Key)))
			if err != nil {
				return err
			}
			if err := retainedNativeClaims(variant.Value, pins, [][]string{argv}, nativeText(r.Object.Get("harness"))); err != nil {
				return err
			}
		}
	}
	return nil
}

func retainedNativeClaims(node *value.Value, pins map[string]string, vectors [][]string, harness string) error {
	if node == nil {
		return nil
	}
	if node.Kind == value.Array {
		for _, item := range node.Items {
			if err := retainedNativeClaims(item, pins, vectors, harness); err != nil {
				return err
			}
		}
	}
	if node.Kind != value.Object {
		return nil
	}
	path := nativeText(node.Get("path"))
	if selected := pins[path]; node.Get("sha256") != nil && selected != "" && !value.SameText(node.Get("sha256"), selected) {
		return retainedPackageError()
	}
	if source := node.Get("source_path"); source != nil && pins[nativeText(source)] == "" {
		return retainedPackageError()
	}
	for _, field := range node.Members {
		name := string(field.Key)
		if slices.Contains([]string{"literal_command_text", "one_shared_literal_command", "derived_argv", "actual_internal_engine_derived_argv"}, name) {
			var argv []string
			var ok bool
			if name == "literal_command_text" || name == "one_shared_literal_command" {
				argv, ok = shlexSplitPosix(nativeText(field.Value))
			} else {
				var err error
				argv, err = hookStringVector(field.Value)
				ok = err == nil
			}
			if !ok {
				return retainedPackageError()
			}
			matched := false
			for _, selected := range vectors {
				matched = matched || slices.Equal(argv, selected)
				if harness == "agy" && (name == "literal_command_text" || name == "one_shared_literal_command") && len(argv) >= 2 && argv[len(argv)-2] == "--event" && slices.Contains([]string{"PreInvocation", "Stop"}, argv[len(argv)-1]) {
					matched = matched || slices.Equal(argv[:len(argv)-2], selected)
				}
			}
			if !matched {
				return retainedPackageError()
			}
		}
		if err := retainedNativeClaims(field.Value, pins, vectors, harness); err != nil {
			return err
		}
	}
	return nil
}

func (r *retainedDataPackage) member(config string) (string, *value.Value, error) {
	for _, selected := range r.Object.Get("members").Members {
		if nativeText(selected.Value.Get("config").Get("path")) == config {
			return string(selected.Key), selected.Value, nil
		}
	}
	return "", nil, fmt.Errorf("retained package has no member for %s", config)
}

func (b *bootstrapRead) retainedBytes(pin WatchCensusPin) ([]byte, error) {
	if !censusDigest(pin.SHA256) {
		return nil, retainedPackageError()
	}
	// The authenticated package explicitly selects original installed inputs.
	// They may be outside the plan root, with all ordinary content guards kept.
	if err := b.ownedReference(pin.Path); err != nil {
		return nil, err
	}
	var raw []byte
	var digest string
	var err error
	if b.files != nil {
		raw, digest, err = b.files.Read(pin.Path, 1048576, false)
	} else {
		raw, digest, err = compatibilityOpened(pin.Path, 1048576, uint32(os.Getuid()))
	}
	if err != nil || digest != pin.SHA256 {
		return nil, errors.Join(err, retainedPackageError())
	}
	b.drain.facts["retained-source:"+pin.Path] = digest
	return raw, nil
}

func (b *bootstrapRead) retainedPackage(selected BootstrapRetainedAssociation, config string) error {
	// Only original data/source inputs may be external; publication stays scoped.
	if err := b.reference(selected.Package.Path); err != nil {
		return err
	}
	ctx := context.Background()
	if b.files != nil {
		ctx = b.files.ctx
	}
	pkg, err := readRetainedPackage(ctx, *selected.Package, b.retainedBytes, func(pin WatchCensusPin) ([]byte, error) {
		if err := b.legacyCredentialExclusion(pin.Path); err != nil {
			return nil, err
		}
		observed, err := b.legacyRead(pin.Path, false)
		if err != nil || observed.SHA256 != pin.SHA256 {
			return nil, errors.Join(err, retainedPackageError())
		}
		b.drain.facts["retained-source:"+pin.Path] = observed.SHA256
		return observed.Raw, nil
	})
	if err != nil {
		return err
	}
	binding, err := retainedPackagePin(pkg.Object.Get("binding"))
	if err != nil || binding != selected.Binding {
		return retainedPackageError()
	}
	name, member, err := pkg.member(config)
	if err != nil {
		return err
	}
	if selected.Argv != pkg.Files["argv-"+name+".json"] {
		return retainedPackageError()
	}
	for _, role := range member.Get("argv").Members {
		argv, err := hookStringVector(role.Value)
		if err != nil {
			return err
		}
		if b.binaryDirs != nil {
			b.binaryDirs[filepath.Dir(argv[0])] = true
		}
	}
	return nil
}

func (r *watchCensusRead) retainedPackage(node *WatchCensusMember, members map[string]WatchCensusPin, producer *value.Value) error {
	selected, err := retainedPackagePin(producer.Get("lifecycle_selection"))
	if err != nil || !censusWithin(r.catalog.Root, selected.Path) {
		return errors.Join(err, retainedPackageError())
	}
	ctx := context.Background()
	if r.local.verified != nil {
		ctx = r.local.verified.ctx
	}
	pkg, err := readRetainedPackage(ctx, selected, func(pin WatchCensusPin) ([]byte, error) { return r.pin(pin, false) }, r.retainedProfileBytes)
	if err != nil {
		return err
	}
	binding, err := retainedPackagePin(pkg.Object.Get("binding"))
	if err != nil || binding != node.Binding {
		return retainedPackageError()
	}
	if len(pkg.Files) != len(members) {
		return retainedPackageError()
	}
	for name, pin := range pkg.Files {
		if members[name] != pin {
			return retainedPackageError()
		}
	}
	name := nativeText(producer.Get("retained_python").Get("member"))
	member := pkg.Object.Get("members").Get(name)
	config, configErr := retainedPackagePin(member.Get("config"))
	if configErr != nil || config.SHA256 != node.Config.SHA256 {
		return errors.Join(configErr, retainedPackageError())
	}
	if config.Path != node.Config.Path {
		if !r.selectedRetainedCopy(node.Config) {
			return retainedPackageError()
		}
		if _, err := r.pin(node.Config, false); err != nil {
			return err
		}
	}
	proof := producer.Get("retained_python")
	if !exactReceiptObject(proof, "binding", "binding_schema", "engine", "bridge", "bundle_manifest", "retained_argv", "package", "member") || !value.SameText(proof.Get("binding_schema"), "cairn.inbox-recall-binding/2") || nativeText(proof.Get("member")) != name {
		return retainedPackageError()
	}
	for key, pin := range map[string]WatchCensusPin{"binding": binding, "package": selected, "bundle_manifest": members["lifecycle-bundle.json"], "retained_argv": members["retained-argv.json"]} {
		actual, err := retainedPackagePin(proof.Get(key))
		if err != nil || actual != pin {
			return retainedPackageError()
		}
	}
	if !nativeEqual(proof.Get("engine"), pkg.Binding.Get("engine")) || !nativeEqual(proof.Get("bridge"), pkg.Binding.Get("bridge")) {
		return retainedPackageError()
	}
	if r.retainedPackages == nil {
		r.retainedPackages = map[string]*retainedDataPackage{}
		r.retainedProfiles = map[string]WatchCensusPin{}
	}
	r.retainedPackages[node.Config.Path] = pkg
	for _, profile := range pkg.Object.Get("profiles").Members {
		pin, err := retainedPackagePin(profile.Value)
		if err != nil {
			return err
		}
		r.retainedProfiles[pin.Path] = pin
	}
	r.retainedProfiles[node.Config.Path] = node.Config
	return nil
}

func (r *watchCensusRead) retainedProfileBytes(pin WatchCensusPin) ([]byte, error) {
	if !censusDigest(pin.SHA256) || r.local.tokenPaths[pin.Path] {
		return nil, retainedPackageError()
	}
	if err := r.safePath(pin.Path, false); err != nil {
		return nil, err
	}
	observed, err := bootstrapLegacyProfile(pin.Path)
	if err != nil || observed.SHA256 != pin.SHA256 {
		return nil, errors.Join(err, retainedPackageError())
	}
	if old := r.files[pin.Path]; old != "" && old != observed.SHA256 {
		return nil, retainedPackageError()
	}
	key := "retained-original-profile:" + pin.Path
	if old := r.report.Facts[key]; old != "" && old != observed.Fingerprint {
		return nil, errors.New("retained original profile identity/ancestry changed")
	}
	r.report.Facts[key] = observed.Fingerprint
	r.files[pin.Path] = observed.SHA256
	r.report.Facts["file:"+pin.Path] = observed.SHA256
	return observed.Raw, nil
}

func (r *watchCensusRead) selectedRetainedCopy(pin WatchCensusPin) bool {
	for _, selection := range r.catalog.Selections {
		if filepath.Dir(pin.Path) == selection.Directory && selection.Pins[filepath.Base(pin.Path)] == pin.SHA256 {
			return true
		}
	}
	return false
}

// Resolve explicit package associations before members interprets any profile.
// This is still the same finite disposable Claude boundary, not a live observer.
func (r *watchCensusRead) loadRetainedPackages(ctx context.Context) error {
	for _, hook := range r.hookSelections {
		if hook.Claude == nil {
			continue
		}
		selected := *hook.Claude
		producer, _, err := r.claudeHookProducer(ctx, selected)
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			// No authenticated package means no alternate profile policy. The
			// ordinary full reader below still checks this generation, after its
			// existing config/session prerequisites, and returns the actual error.
			continue
		}
		if producer.Get("retained_python").Get("package") == nil {
			continue
		}
		if !r.selectedRetainedCopy(selected.Config) {
			return retainedPackageError()
		}
		members, err := r.retainedGeneration(ctx, producer)
		if err != nil {
			return err
		}
		if err := r.retainedPackageUniverse(members); err != nil {
			return err
		}
		binding, err := retainedPackagePin(producer.Get("retained_python").Get("binding"))
		if err != nil || r.bindingPins[binding.Path] != binding.SHA256 {
			return errors.Join(err, retainedPackageError())
		}
		node := &WatchCensusMember{Config: selected.Config, Binding: binding, Mode: "cairn.inbox-recall-binding/2"}
		if err := r.retainedPackage(node, members, producer); err != nil {
			return err
		}
		config, err := r.jsonFile(selected.Config.Path)
		if err != nil || !value.SameText(config.Get("harness"), "claude") || nativeText(config.Get("config_home")) != selected.Account {
			return errors.Join(err, retainedPackageError())
		}
	}
	return nil
}

// Package helper readers also serve explicitly selected external original
// inputs. The public native census keeps its narrower finite-universe contract:
// check every content-bearing original reference before granting profile reads.
func (r *watchCensusRead) retainedPackageUniverse(members map[string]WatchCensusPin) error {
	metadata, err := retainedPackageValue(func(pin WatchCensusPin) ([]byte, error) { return r.pin(pin, false) }, members["retained-package.json"])
	if err != nil {
		return err
	}
	within := func(v *value.Value) bool { return censusWithin(r.catalog.Root, nativeText(v.Get("path"))) }
	for _, field := range []string{"profiles", "closures", "members"} {
		if v := metadata.Get(field); v == nil || v.Kind != value.Object {
			return retainedPackageError()
		}
	}
	if !within(metadata.Get("binding")) {
		return retainedPackageError()
	}
	for _, profile := range metadata.Get("profiles").Members {
		if !within(profile.Value) {
			return retainedPackageError()
		}
	}
	for _, closure := range metadata.Get("closures").Members {
		if !censusWithin(r.catalog.Root, string(closure.Key)) || !within(closure.Value.Get("manifest")) {
			return retainedPackageError()
		}
	}
	for _, member := range metadata.Get("members").Members {
		if !within(member.Value.Get("config")) {
			return retainedPackageError()
		}
		runtime := member.Value.Get("runtime")
		if runtime == nil || runtime.Kind != value.Object {
			return retainedPackageError()
		}
		for _, role := range runtime.Members {
			if !within(role.Value.Get("entry")) || !nativeNone(role.Value.Get("delegate")) && !within(role.Value.Get("delegate")) {
				return retainedPackageError()
			}
		}
	}
	return nil
}

func (r *watchCensusRead) retainedGeneration(ctx context.Context, producer *value.Value) (map[string]WatchCensusPin, error) {
	selected, err := retainedPackagePin(producer.Get("lifecycle_selection"))
	proof, proofErr := retainedPackagePin(producer.Get("retained_python").Get("package"))
	if err != nil || proofErr != nil || selected != proof || !censusWithin(r.catalog.Root, selected.Path) {
		return nil, errors.Join(err, proofErr, retainedPackageError())
	}
	if _, err := r.pin(selected, false); err != nil {
		return nil, err
	}
	files, err := install.ReadGeneration(ctx, install.Generation{Path: selected.Path, SHA256: selected.SHA256})
	if err != nil {
		return nil, err
	}
	members := map[string]WatchCensusPin{}
	for _, file := range files {
		if !censusWithin(r.catalog.Root, file.Path) || file.Executable {
			return nil, retainedPackageError()
		}
		members[file.Name] = WatchCensusPin{Path: file.Path, SHA256: file.SHA256}
	}
	if members["retained-package.json"].Path == "" {
		return nil, retainedPackageError()
	}
	return members, nil
}
