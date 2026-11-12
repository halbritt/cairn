package coordination

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"syscall"

	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func censusDecode(raw []byte, dst any) error {
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(dst); err != nil {
		return err
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return errors.New("census descriptor has trailing JSON")
	}
	return nil
}

func (r *watchCensusRead) safePath(path string, directory bool) error {
	if !compatibilityCanonical(path) {
		return errors.New("census requires canonical absolute paths")
	}
	current := "/"
	parts := strings.Split(path[1:], "/")
	for i, part := range parts {
		current = filepath.Join(current, part)
		info, err := os.Lstat(current)
		if err != nil {
			return err
		}
		stat, ok := info.Sys().(*syscall.Stat_t)
		if !ok || info.Mode()&os.ModeSymlink != 0 || (stat.Uid != 0 && stat.Uid != uint32(os.Getuid())) ||
			(info.Mode().Perm()&0022 != 0 && !(stat.Uid == 0 && info.Mode()&os.ModeSticky != 0)) {
			return fmt.Errorf("census unsafe path/alias: %s", current)
		}
		if i < len(parts)-1 && !info.IsDir() {
			return errors.New("census path ancestor is not a directory")
		}
		if i == len(parts)-1 {
			if directory && (!info.IsDir() || stat.Uid != uint32(os.Getuid()) || info.Mode().Perm() != 0700) {
				return fmt.Errorf("census requires a private owned directory: %s", path)
			}
			key := fmt.Sprintf("%d:%d", stat.Dev, stat.Ino)
			if old := r.inodes[key]; old != "" && old != path {
				return fmt.Errorf("census unsafe hardlink alias: %s and %s", old, path)
			}
			r.inodes[key] = path
		}
	}
	return nil
}

func censusDigest(digest string) bool {
	b, err := hex.DecodeString(digest)
	return err == nil && len(b) == 32 && hex.EncodeToString(b) == digest
}

func censusPinMap(pins []WatchCensusPin) (map[string]string, error) {
	out := map[string]string{}
	for _, pin := range pins {
		if !compatibilityCanonical(pin.Path) || !censusDigest(pin.SHA256) || out[pin.Path] != "" {
			return nil, errors.New("missing, duplicate or invalid census descriptor pin")
		}
		out[pin.Path] = pin.SHA256
	}
	return out, nil
}

func (r *watchCensusRead) pin(pin WatchCensusPin, image bool) ([]byte, error) {
	if !censusDigest(pin.SHA256) {
		return nil, errors.New("census pin requires lowercase SHA-256")
	}
	if r.local.tokenPaths[pin.Path] {
		return nil, errors.New("census refuses credential-content reads")
	}
	if err := r.safePath(pin.Path, false); err != nil {
		return nil, err
	}
	var raw []byte
	var digest string
	var err error
	if image {
		digest, err = compatibilityOpenedBinary(pin.Path, uint32(os.Getuid()))
		r.images[pin.Path] = true
	} else {
		raw, digest, err = compatibilityOpened(pin.Path, 1048576, uint32(os.Getuid()))
	}
	if err != nil {
		return nil, err
	}
	if digest != pin.SHA256 {
		return nil, fmt.Errorf("census pin changed: %s", pin.Path)
	}
	if old := r.files[pin.Path]; old != "" && old != digest {
		return nil, errors.New("census file changed between observations")
	}
	r.files[pin.Path] = digest
	r.report.Facts["file:"+pin.Path] = digest
	return raw, nil
}

func (r *watchCensusRead) jsonFile(path string) (*value.Value, error) {
	if !censusWithin(r.catalog.Root, path) {
		return nil, fmt.Errorf("census reference outside observation universe: %s", path)
	}
	if err := r.safePath(path, false); err != nil {
		return nil, err
	}
	old := r.files[path]
	v, err := r.local.file(path)
	if err == nil {
		digest := r.report.Facts["file:"+path]
		if old != "" && old != digest {
			return nil, errors.New("census JSON changed between observations")
		}
		r.files[path] = digest
	}
	return v, err
}

func (r *watchCensusRead) descriptor(v *value.Value) (*value.Value, WatchCensusPin, error) {
	p := WatchCensusPin{Path: nativeText(v.Get("path")), SHA256: nativeText(v.Get("sha256"))}
	if !censusDigest(p.SHA256) {
		return nil, p, errors.New("census selected descriptor is missing")
	}
	b, err := r.jsonFile(p.Path)
	if err != nil {
		return nil, p, err
	}
	if r.files[p.Path] != p.SHA256 {
		return nil, p, errors.New("census selected descriptor changed")
	}
	return b, p, nil
}

func (r *watchCensusRead) loadCatalog(ctx context.Context, pin WatchCensusPin) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	raw, err := r.pin(pin, false)
	if err != nil {
		return err
	}
	if err := censusDecode(raw, &r.catalog); err != nil {
		return err
	}
	if r.catalog.Schema != "cairn.watch-unit-catalog/1" || (r.catalog.Scope != "disposable-fixture" && r.catalog.Scope != "external-unknown") {
		return errors.New("unknown census schema or observation coverage")
	}
	if err := r.safePath(r.catalog.Root, true); err != nil {
		return err
	}
	r.bindingPins, err = censusPinMap(r.catalog.Bindings)
	if err != nil {
		return err
	}
	r.clientPins, err = censusPinMap(r.catalog.Clients)
	if err != nil {
		return err
	}
	for _, path := range r.catalog.CredentialPaths {
		if !censusWithin(r.catalog.Root, path) {
			return errors.New("census credential selector outside fixture universe")
		}
		if err := r.safePath(path, false); err != nil {
			return err
		}
		r.local.tokenPaths[path] = true
		r.report.Facts["credential-reference:"+path] = "metadata-only"
	}
	names, err := r.directory(r.catalog.ConfigRoot)
	if err != nil {
		return err
	}
	var expected []string
	for _, selection := range r.catalog.Selections {
		if filepath.Dir(selection.Directory) != r.catalog.ConfigRoot {
			return errors.New("census selection root is not exhaustive")
		}
		expected = append(expected, filepath.Base(selection.Directory))
	}
	if !censusSameStrings(names, expected) {
		return errors.New("unknown or omitted selected config directory")
	}
	return nil
}

func (r *watchCensusRead) members(ctx context.Context) error {
	// Register all credential selectors before walking any associated state.
	for _, selection := range r.catalog.Selections {
		if err := readWatchBindings(selection); err != nil {
			return err
		}
		if _, err := r.directory(selection.Directory); err != nil {
			return err
		}
		for _, name := range slices.Sorted(maps.Keys(selection.Pins)) {
			if err := ctx.Err(); err != nil {
				return err
			}
			path := filepath.Join(selection.Directory, name)
			config, err := r.jsonFile(path)
			if err != nil {
				return err
			}
			if _, err := ValidateConfig(config); err != nil {
				return err
			}
			if !r.local.tokenPaths[nativeText(config.Get("token_file"))] {
				return errors.New("census credential selector coverage is unknown")
			}
			r.local.configs = append(r.local.configs, config)
			r.configPaths[config] = path
			r.nodes[path] = &WatchCensusMember{Config: WatchCensusPin{path, selection.Pins[name]}, Account: nativeText(config.Get("config_home")), Repository: nativeText(config.Get("repo")), Resources: []string{selection.Directory}}
		}
	}
	for _, path := range slices.Sorted(maps.Keys(r.nodes)) {
		config, err := r.jsonFile(path)
		if err != nil {
			return err
		}
		node := r.nodes[path]
		manifest, err := r.jsonFile(nativeText(config.Get("inbox_recall_binding")))
		if err != nil {
			return err
		}
		node.Binding = WatchCensusPin{nativeText(config.Get("inbox_recall_binding")), r.files[nativeText(config.Get("inbox_recall_binding"))]}
		if r.bindingPins[node.Binding.Path] != node.Binding.SHA256 {
			return errors.New("selected binding pin is missing or changed")
		}
		// Reuse the accepted profile agreement, membership and descriptor checks.
		memory, err := r.profiles(config, manifest)
		if err != nil {
			return err
		}
		if manifest.Get("enabled").Boolean {
			r.nodeBlockers[path] = append(r.nodeBlockers[path], "selected binding is enabled; disabled refresh is unobserved")
		}
		_, node.Memory, err = r.descriptor(manifest.Get("memory_config"))
		if err != nil {
			return err
		}
		for _, desc := range manifest.Get("coordination_configs").Items {
			_, selected, err := r.descriptor(desc)
			if err != nil {
				return err
			}
			if !r.knownConfig(selected) {
				return errors.New("binding descriptor names an omitted or mixed selected user")
			}
		}
		node.Mode = nativeText(manifest.Get("schema"))
		for _, directory := range []string{nativeText(config.Get("state_dir")), nativeText(memory.Get("state_dir"))} {
			if _, err := r.directory(directory); err != nil {
				return err
			}
			node.Resources = append(node.Resources, directory)
		}
		if node.Account == "" {
			return errors.New("selected account coverage is unknown")
		}
		if err := r.emptyHookLayer(node.Account, "account"); err != nil {
			return err
		}
		node.Resources = append(node.Resources, node.Account)
		client := nativeText(config.Get("cairn"))
		if !censusWithin(r.catalog.Root, client) {
			return errors.New("census image outside fixture universe")
		}
		if _, err := r.pin(WatchCensusPin{client, r.clientPins[client]}, true); err != nil {
			return err
		}
		if err := r.runtime(node, manifest); err != nil {
			return err
		}
		slices.Sort(node.Resources)
		node.Resources = slices.Compact(node.Resources)
	}
	return nil
}

func (r *watchCensusRead) profiles(config, manifest *value.Value) (*value.Value, error) {
	mode := nativeText(manifest.Get("schema"))
	enabled := manifest.Get("enabled")
	if (mode != "cairn.inbox-recall-binding/2" && mode != "cairn.inbox-recall-binding/3") || enabled == nil || enabled.Kind != value.Bool {
		return nil, errors.New("unknown selected binding schema/enablement")
	}
	memory, _, err := r.descriptor(manifest.Get("memory_config"))
	if err != nil {
		return nil, err
	}
	list := manifest.Get("coordination_configs")
	if list == nil || list.Kind != value.Array {
		return nil, errors.New("unknown coordination profile coverage")
	}
	var coordinators []*value.Value
	for _, desc := range list.Items {
		coord, _, err := r.descriptor(desc)
		if err != nil {
			return nil, err
		}
		coordinators = append(coordinators, coord)
	}
	if err := binding.ValidateConfigurationProfiles(binding.ConfigurationProfiles{Current: config, Memory: memory, Coordinators: coordinators, ManifestPath: config.Get("inbox_recall_binding").String, Resolve: r.cli.profile().Routes.Filesystem().Resolve}); err != nil {
		return nil, err
	}
	return memory, nil
}

func (r *watchCensusRead) knownConfig(pin WatchCensusPin) bool {
	for path, node := range r.nodes {
		if node.Config.SHA256 == pin.SHA256 {
			if pin.Path == path {
				return true
			}
			// Selected copies name immutable descriptors. Equality is of actual
			// bytes; the binding path is also retained and checked separately.
			if r.files[pin.Path] == node.Config.SHA256 {
				return true
			}
		}
	}
	return false
}

func (r *watchCensusRead) runtime(node *WatchCensusMember, manifest *value.Value) error {
	if node.Mode == "cairn.inbox-recall-binding/3" {
		binary := manifest.Get("binary")
		pin := WatchCensusPin{nativeText(binary.Get("path")), nativeText(binary.Get("sha256"))}
		if !censusWithin(r.catalog.Root, pin.Path) {
			return errors.New("unknown runtime root outside observation universe")
		}
		if _, err := r.pin(pin, true); err != nil {
			return err
		}
		if _, err := r.directory(filepath.Dir(pin.Path)); err != nil {
			return err
		}
		node.Image = pin.SHA256
		node.Resources = append(node.Resources, filepath.Dir(pin.Path))
		return nil
	}
	// Legacy bundle paths are retained, never executed or repinned by census.
	var images []string
	for _, role := range []string{"engine", "bridge"} {
		desc := manifest.Get(role)
		pin := WatchCensusPin{nativeText(desc.Get("path")), nativeText(desc.Get("sha256"))}
		if !censusWithin(r.catalog.Root, pin.Path) {
			return errors.New("missing legacy runtime descriptor")
		}
		if _, err := r.pin(pin, false); err != nil {
			return err
		}
		if _, err := r.directory(filepath.Dir(pin.Path)); err != nil {
			return err
		}
		bundle := desc.Get("bundle")
		files := bundle.Get("files")
		if !value.SameText(bundle.Get("schema"), "cairn.lifecycle-bundle/1") || files == nil || files.Kind != value.Object || len(files.Members) != len(CompatibilityClosureMembers)-1 {
			return errors.New("legacy runtime bundle coverage is unknown")
		}
		for _, name := range CompatibilityClosureMembers {
			if name != "lifecycle_compatibility.py" && files.Get(name) == nil {
				return errors.New("legacy runtime bundle omits a source member")
			}
		}
		digest, err := canonicalSortedJSONSHA256(files)
		if err != nil || digest != nativeText(bundle.Get("sha256")) {
			return errors.New("legacy bundle digest does not match its actual member pins")
		}
		for _, member := range files.Members {
			name := string(member.Key)
			if filepath.Base(name) != name || strings.ContainsAny(name, "\\\x00") {
				return errors.New("unsafe legacy runtime member")
			}
			if _, err := r.pin(WatchCensusPin{filepath.Join(filepath.Dir(pin.Path), name), nativeText(member.Value)}, false); err != nil {
				return err
			}
		}
		images = append(images, nativeText(bundle.Get("sha256")))
		node.Resources = append(node.Resources, filepath.Dir(pin.Path))
	}
	if images[0] != images[1] || !censusDigest(images[0]) {
		return errors.New("mixed legacy runtime selection")
	}
	node.Image = images[0]
	return nil
}

func (r *watchCensusRead) tree(ctx context.Context, directory string) ([]string, error) {
	names, err := r.directory(directory)
	if err != nil {
		return nil, err
	}
	var files []string
	for _, name := range names {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		path := filepath.Join(directory, name)
		info, err := os.Lstat(path)
		if err != nil {
			return nil, err
		}
		if info.IsDir() {
			children, err := r.tree(ctx, path)
			if err != nil {
				return nil, err
			}
			files = append(files, children...)
			continue
		}
		if err := r.safePath(path, false); err != nil {
			return nil, err
		}
		switch {
		case strings.HasSuffix(name, ".lock"), strings.HasSuffix(name, ".cuelock"):
			if !info.Mode().IsRegular() {
				return nil, errors.New("unknown census lock identity")
			}
			stat := info.Sys().(*syscall.Stat_t)
			r.report.Facts["lock-reference:"+path] = fmt.Sprintf("%d:%d", stat.Dev, stat.Ino)
		case strings.HasSuffix(name, ".json"), strings.HasSuffix(name, ".initialized"):
			if _, err := r.jsonFile(path); err != nil {
				return nil, err
			}
			files = append(files, path)
		default:
			return nil, fmt.Errorf("unknown census state member: %s", path)
		}
	}
	return files, nil
}

func (r *watchCensusRead) sessions(ctx context.Context) error {
	states, memories := map[string]bool{}, map[string]bool{}
	for _, config := range r.local.configs {
		states[nativeText(config.Get("state_dir"))] = true
		manifest, err := r.jsonFile(nativeText(config.Get("inbox_recall_binding")))
		if err != nil {
			return err
		}
		memory, _, err := r.descriptor(manifest.Get("memory_config"))
		if err != nil {
			return err
		}
		memories[nativeText(memory.Get("state_dir"))] = true
	}
	wanted := map[string]string{}
	for _, pin := range r.catalog.Sessions {
		if wanted[pin.Path] != "" {
			return errors.New("duplicate session census reference")
		}
		wanted[pin.Path] = pin.SHA256
	}
	seen := map[string]bool{}
	for _, directory := range slices.Sorted(maps.Keys(states)) {
		files, err := r.tree(ctx, directory)
		if err != nil {
			return err
		}
		for _, path := range files {
			if filepath.Dir(path) != directory {
				continue
			}
			state, err := r.jsonFile(path)
			if err != nil {
				return err
			}
			if !value.SameText(state.Get("schema"), NativeSessionSchema) {
				return errors.New("unknown native session census member")
			}
			if wanted[path] == "" || wanted[path] != r.files[path] {
				return errors.New("extra, missing or changed selected session")
			}
			config := r.local.sessionConfig(path, state)
			if config == nil {
				return errors.New("native session has an unknown binding user")
			}
			ref, err := r.cli.sessionRef(state.Get("agent"))
			if err != nil {
				return err
			}
			workspace := nativeText(state.Get("agent").Get("metadata").Get("workspace"))
			if workspace == "" {
				return errors.New("native session workspace coverage is unknown")
			}
			if err := r.emptyHookLayer(workspace, "repository"); err != nil {
				return err
			}
			owner := r.configPaths[config]
			if owner == "" {
				return errors.New("session config ownership is unknown")
			}
			r.nodes[owner].Sessions = append(r.nodes[owner].Sessions, path)
			r.nodes[owner].Resources = append(r.nodes[owner].Resources, workspace)
			slices.Sort(r.nodes[owner].Resources)
			r.nodes[owner].Resources = slices.Compact(r.nodes[owner].Resources)
			seen[path] = true
			r.sessionRefs[path] = WatchCensusSession{Path: path, Binding: owner, NativeSession: nativeText(state.Get("native_session_id")), AgentID: nativeText(ref.Get("agent_id")), ExecutionID: nativeText(ref.Get("execution_id")), SHA256: r.files[path], Workspace: workspace}
			for _, key := range AttemptKeys {
				if !nativeNone(state.Get(key)) {
					r.nodeBlockers[owner] = append(r.nodeBlockers[owner], "retained native "+key+" needs its authenticated drain correspondence")
				}
			}
		}
	}
	if len(seen) != len(wanted) {
		return errors.New("missing pinned session census member")
	}
	for _, directory := range slices.Sorted(maps.Keys(memories)) {
		files, err := r.tree(ctx, directory)
		if err != nil {
			return err
		}
		for _, path := range files {
			ledger, err := r.jsonFile(path)
			if err != nil {
				return err
			}
			activation := ledger.Get("inbox_activation")
			if activation == nil {
				continue
			}
			for configPath, node := range r.nodes {
				if !slices.Contains(node.Resources, directory) {
					continue
				}
				if activation.Kind != value.Object {
					r.nodeBlockers[configPath] = append(r.nodeBlockers[configPath], "activation grant coverage is unknown")
					continue
				}
				for _, decision := range activation.Members {
					if decision.Value == nil || decision.Value.Kind != value.Bool {
						r.nodeBlockers[configPath] = append(r.nodeBlockers[configPath], "activation grant disposition is unknown")
					} else if decision.Value.Boolean {
						r.nodeGrants[configPath] = append(r.nodeGrants[configPath], path+"#"+string(decision.Key))
						r.nodeBlockers[configPath] = append(r.nodeBlockers[configPath], "retained positive activation grant requires its owner")
					}
				}
			}
		}
	}
	return nil
}

func (r *watchCensusRead) emptyHookLayer(path, role string) error {
	names, err := r.directory(path)
	if err != nil {
		return err
	}
	if len(names) != 0 {
		return fmt.Errorf("%s hook/config coverage is unknown: selected native settings/readback seam is unavailable", role)
	}
	return nil
}

func (r *watchCensusRead) readback(ctx context.Context) error {
	for _, path := range slices.Sorted(maps.Keys(r.directories)) {
		before := slices.Clone(r.directories[path])
		after, err := r.directory(path)
		if err != nil {
			return err
		}
		if !slices.Equal(before, after) {
			return errors.New("census directory membership changed during readback")
		}
	}
	for _, path := range slices.Sorted(maps.Keys(r.files)) {
		if err := ctx.Err(); err != nil {
			return err
		}
		if _, err := r.pin(WatchCensusPin{path, r.files[path]}, r.images[path]); err != nil {
			return err
		}
	}
	for path, record := range r.callbackRecords {
		fact, _, err := r.observeProcess(record)
		if err != nil {
			return err
		}
		if r.report.Facts["process:"+path] != fact {
			return errors.New("census callback identity changed during readback")
		}
	}
	for _, selection := range r.catalog.Selections {
		if err := readWatchBindings(selection); err != nil {
			return err
		}
	}
	return nil
}
