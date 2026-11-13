package coordination

import (
	"context"
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Association is observed selection data, not a grant or native refresh fact.
type watchCensusAssociation struct {
	manifest, memory *value.Value
	binding          WatchCensusPin
	resources        []string
}

func (r *watchCensusRead) unboundPin(pin WatchCensusPin, image bool) ([]byte, error) {
	if !censusWithin(r.catalog.Root, pin.Path) {
		return nil, errors.New("unbound receipt reference outside census universe")
	}
	return r.pin(pin, image)
}

func (r *watchCensusRead) unboundJSON(pin WatchCensusPin) (*value.Value, error) {
	raw, err := r.unboundPin(pin, false)
	if err != nil {
		return nil, err
	}
	if err := jsontext.CheckUnicode(raw); err != nil {
		return nil, err
	}
	decoded, err := value.DecodeTextWithObjectPolicy(raw, "utf-8", 0, value.DuplicateReject)
	if err != nil || decoded == nil || decoded.Kind != value.Object {
		return nil, errors.Join(err, errors.New("unbound census reference is not a strict JSON object"))
	}
	return decoded, nil
}

func (r *watchCensusRead) unboundDescriptor(desc *value.Value) (*value.Value, WatchCensusPin, error) {
	if !validReceiptDescriptor(desc, "path", "sha256") {
		return nil, WatchCensusPin{}, errors.New("invalid unbound profile descriptor")
	}
	pin := WatchCensusPin{nativeText(desc.Get("path")), nativeText(desc.Get("sha256"))}
	profile, err := r.unboundJSON(pin)
	return profile, pin, err
}

func (r *watchCensusRead) unboundReceipt(pin WatchCensusPin) (*watchCensusAssociation, []WatchCensusPin, error) {
	receipt, err := r.unboundJSON(pin)
	if err != nil {
		return nil, nil, err
	}
	coords := receipt.Get("coordination_configs")
	if coords == nil || coords.Kind != value.Array || len(coords.Items) < 1 || len(coords.Items) > 8 ||
		!validReceiptDescriptor(receipt.Get("binding"), "path", "sha256", "schema") ||
		!validReceiptDescriptor(receipt.Get("binary"), "path", "sha256") {
		return nil, nil, errors.New("unsupported unbound receipt descriptors")
	}
	memory, _, err := r.unboundDescriptor(receipt.Get("memory_config"))
	if err != nil {
		return nil, nil, err
	}
	profiles := []*value.Value{memory}
	var selected []WatchCensusPin
	for _, desc := range coords.Items {
		profile, member, err := r.unboundDescriptor(desc)
		if err != nil {
			return nil, nil, err
		}
		if slices.Contains(selected, member) {
			return nil, nil, errors.New("duplicate unbound coordination descriptor")
		}
		profiles, selected = append(profiles, profile), append(selected, member)
	}
	homes := map[string]bool{}
	for i, profile := range profiles {
		if value.Truth(profile.Get("inbox_recall_binding")) {
			return nil, nil, errors.New("selected unbound profile has a binding")
		}
		for _, field := range []string{"harness", "cairn", "socket", "token_file", "repo"} {
			if nativeText(profile.Get(field)) == "" || !hookValuesEqual(profile.Get(field), memory.Get(field)) {
				return nil, nil, errors.New("mixed unbound profile selectors")
			}
		}
		if !r.local.tokenPaths[nativeText(profile.Get("token_file"))] || !censusWithin(r.catalog.Root, nativeText(profile.Get("state_dir"))) ||
			!censusWithin(r.catalog.Root, nativeText(profile.Get("socket"))) ||
			!censusWithin(r.catalog.Root, nativeText(profile.Get("config_home"))) {
			return nil, nil, errors.New("unbound profile state/account/credential coverage is unknown")
		}
		if i > 0 {
			home := nativeText(profile.Get("config_home"))
			if homes[home] || nativeText(profile.Get("state_dir")) == nativeText(memory.Get("state_dir")) {
				return nil, nil, errors.New("mixed unbound coordinator homes or memory state")
			}
			homes[home] = true
		}
	}
	if !homes[nativeText(memory.Get("config_home"))] {
		return nil, nil, errors.New("unbound memory account is not a selected coordinator account")
	}
	binding := WatchCensusPin{nativeText(receipt.Get("binding").Get("path")), nativeText(receipt.Get("binding").Get("sha256"))}
	manifest, err := r.unboundJSON(binding)
	if err != nil {
		return nil, nil, err
	}
	info, err := os.Lstat(binding.Path)
	if err != nil || info.Mode().Perm()&0077 != 0 || !value.SameText(manifest.Get("schema"), "cairn.inbox-recall-binding/3") ||
		manifest.Get("enabled") == nil || manifest.Get("enabled").Kind != value.Bool || manifest.Get("enabled").Boolean {
		return nil, nil, errors.New("unbound receipt requires its private disabled /3 manifest")
	}
	for _, field := range []string{"binary", "memory_config", "coordination_configs"} {
		if !hookValuesEqual(manifest.Get(field), receipt.Get(field)) {
			return nil, nil, errors.New("unbound manifest differs from receipt selection")
		}
	}
	if r.bindingPins[binding.Path] != binding.SHA256 {
		return nil, nil, errors.New("unbound manifest is not a selected binding pin")
	}
	binary := receipt.Get("binary")
	if _, err := r.unboundPin(WatchCensusPin{nativeText(binary.Get("path")), nativeText(binary.Get("sha256"))}, true); err != nil {
		return nil, nil, err
	}
	root := nativeText(receipt.Get("snapshot_root"))
	members := receipt.Get("members")
	if !censusWithin(r.catalog.Root, root) || members == nil || members.Kind != value.Object {
		return nil, nil, errors.New("unbound source closure is outside the census universe")
	}
	for _, member := range members.Members {
		name := string(member.Key)
		if filepath.Base(name) != name || name == "." || name == ".." || strings.ContainsAny(name, "\\\x00") {
			return nil, nil, errors.New("unsafe unbound source member")
		}
		if _, err := r.unboundPin(WatchCensusPin{filepath.Join(root, name), nativeText(member.Value.Get("sha256"))}, false); err != nil {
			return nil, nil, err
		}
	}
	// Seed only the strictly parsed, pinned receipt, so validation cannot discover
	// a replacement receipt's paths. All validator disk reads above are already
	// in ordinary census facts and will undergo the final readback.
	compat := &CompatibilityContext{Role: "coordination", Entrypoint: "coordination.py", ConfigPath: selected[0].Path,
		HookTimeout: 15, LaunchRoot: filepath.Dir(pin.Path), Shell: "/bin/sh", Receipt: receipt, Profile: r.cli.profile()}
	if err := compat.VerifyReceipt(r.cli.profile()); err != nil {
		return nil, nil, fmt.Errorf("unbound receipt: %w", err)
	}
	// The manifest and role-profile predicates of UnboundProfiles were checked
	// on the tracked bytes above, avoiding its separate controlledData reads.
	return &watchCensusAssociation{manifest: manifest, memory: memory, binding: binding,
		resources: []string{root, filepath.Dir(pin.Path), filepath.Dir(binding.Path)}}, selected, nil
}

func (r *watchCensusRead) unboundAssociations(ctx context.Context) error {
	r.associations = map[string]*watchCensusAssociation{}
	if _, err := censusPinMap(r.catalog.UnboundReceipts); err != nil {
		return err
	}
	for _, pin := range r.catalog.UnboundReceipts {
		if err := ctx.Err(); err != nil {
			return err
		}
		association, coordinators, err := r.unboundReceipt(pin)
		if err != nil {
			return err
		}
		matched := 0
		for _, path := range slices.Sorted(maps.Keys(r.nodes)) {
			count := 0
			for _, coordinator := range coordinators {
				if coordinator.SHA256 == r.nodes[path].Config.SHA256 {
					count++
				}
			}
			if count > 1 || count == 1 && r.associations[path] != nil {
				return errors.New("ambiguous unbound receipt/profile association")
			}
			if count == 1 {
				r.associations[path] = association
				matched++
			}
		}
		if matched == 0 {
			return errors.New("unbound receipt has no selected coordinator")
		}
		for _, coordinator := range coordinators {
			if !r.knownConfig(coordinator) {
				return errors.New("unbound receipt names an omitted or mixed coordinator")
			}
		}
	}
	return nil
}
