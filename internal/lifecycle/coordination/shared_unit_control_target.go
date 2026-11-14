package coordination

import (
	"errors"
	"maps"
	"path/filepath"
	"slices"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// A target selects observed data, never authentication or drain authority. Its
// profiles are tied bijectively to actual current unbound unit identities.
type unitCensusTarget struct {
	receipt     WatchCensusPin
	association *watchCensusAssociation
	memory      WatchCensusPin
	profiles    map[string]WatchCensusPin
}

func cloneUnitControlCensus(input *UnitControlCensus) UnitControlCensus {
	copy := *input
	copy.Proposal.Bindings = make([]WatchBindings, len(input.Proposal.Bindings))
	for i, s := range input.Proposal.Bindings {
		copy.Proposal.Bindings[i] = cloneCensusSelection(s)
	}
	copy.Proposal.Sessions = slices.Clone(input.Proposal.Sessions)
	copy.Proposal.Callbacks = slices.Clone(input.Proposal.Callbacks)
	copy.Retained = make([]WatchBindings, len(input.Retained))
	for i, s := range input.Retained {
		copy.Retained[i] = cloneCensusSelection(s)
	}
	if input.TargetReceipt != nil {
		pin := *input.TargetReceipt
		copy.TargetReceipt = &pin
	}
	return copy
}

func sameUnitProfile(a, b *value.Value) bool {
	for _, key := range []string{"binding", "state_dir", "harness", "repo", "socket", "token_file", "config_home"} {
		if !nativeEqual(a.Get(key), b.Get(key)) {
			return false
		}
	}
	return true
}

func (r *watchCensusRead) loadUnitTarget() error {
	if r.control == nil || r.control.target == nil {
		return nil
	}
	pin := *r.control.target
	if !compatibilityCanonical(pin.Path) || !censusDigest(pin.SHA256) {
		return errors.New("unknown target receipt pin")
	}
	receipt, err := r.unboundJSON(pin)
	if err != nil {
		return err
	}
	desc := receipt.Get("binding")
	if !validReceiptDescriptor(desc, "path", "sha256", "schema") || !value.SameText(desc.Get("schema"), "cairn.inbox-recall-binding/3") {
		return errors.New("target requires a Go /3 receipt; retained /2 binding/argv/bundle observations are separate")
	}
	bindingPin := WatchCensusPin{nativeText(desc.Get("path")), nativeText(desc.Get("sha256"))}
	if old := r.bindingPins[bindingPin.Path]; old != "" && old != bindingPin.SHA256 {
		return errors.New("target binding conflicts with selected pin")
	}
	// The explicit pinned receipt supplies this data descriptor. Do not change
	// catalog JSON or treat it as a current selected receipt/user.
	targetRead := *r
	targetRead.bindingPins = maps.Clone(r.bindingPins)
	targetRead.bindingPins[bindingPin.Path] = bindingPin.SHA256
	association, profiles, err := targetRead.unboundReceipt(pin)
	if err != nil {
		return err
	}
	target := &unitCensusTarget{receipt: pin, association: association, memory: WatchCensusPin{nativeText(association.manifest.Get("memory_config").Get("path")), nativeText(association.manifest.Get("memory_config").Get("sha256"))}, profiles: map[string]WatchCensusPin{}}
	names, err := watchBindingNames(r.control.current.Pins)
	if err != nil {
		return err
	}
	for _, profile := range profiles {
		config, err := r.unboundJSON(profile)
		if err != nil {
			return err
		}
		if _, err := ValidateConfig(config); err != nil {
			return err
		}
		if !nativeNone(config.Get("inbox_recall_binding")) {
			return errors.New("target profile is not null/unbound")
		}
		matched := ""
		for _, name := range names {
			current, err := r.jsonFile(filepath.Join(r.control.current.Directory, name))
			if err != nil {
				return err
			}
			if !nativeNone(current.Get("inbox_recall_binding")) || !sameUnitProfile(current, config) {
				continue
			}
			if matched != "" || target.profiles[name].Path != "" {
				return errors.New("ambiguous target/current unit identity")
			}
			matched = name
		}
		if matched == "" {
			return errors.New("target coordinator is foreign to the actual current unit")
		}
		target.profiles[matched] = profile
		r.report.Facts["target-member:"+filepath.Join(r.control.current.Directory, matched)] = profile.Path + ":" + profile.SHA256
		client := nativeText(config.Get("cairn"))
		if _, err := r.pin(WatchCensusPin{client, r.clientPins[client]}, true); err != nil {
			return err
		}
		for _, directory := range []string{nativeText(config.Get("state_dir")), nativeText(config.Get("config_home")), nativeText(association.memory.Get("state_dir"))} {
			if _, err := r.directory(directory); err != nil {
				return err
			}
			target.association.resources = append(target.association.resources, directory)
		}
		node := &WatchCensusMember{Config: profile, Binding: association.binding, Mode: "cairn.inbox-recall-binding/3"}
		if err := r.runtime(node, association.manifest); err != nil {
			return err
		}
		target.association.resources = append(target.association.resources, node.Resources...)
	}
	for _, directory := range target.association.resources {
		if _, err := r.directory(directory); err != nil {
			return err
		}
	}
	r.bindingPins[bindingPin.Path] = bindingPin.SHA256
	r.report.Facts["target-receipt:"+pin.Path] = pin.SHA256
	r.target = target
	r.report.target = target
	return nil
}

func (r *watchCensusRead) verifyUnitTarget() error {
	if r.target == nil {
		return nil
	}
	for name, pin := range r.target.profiles {
		path := filepath.Join(r.control.current.Directory, name)
		node, association := r.nodes[path], r.associations[path]
		if node == nil || association == nil {
			return errors.New("target lost its current selected association")
		}
		current, err := r.jsonFile(path)
		if err != nil {
			return err
		}
		target, err := r.unboundJSON(pin)
		if err != nil {
			return err
		}
		if err := unitControlPairIdentity([]*value.Value{current, target}, []string{nativeText(association.memory.Get("state_dir")), nativeText(r.target.association.memory.Get("state_dir"))}); err != nil {
			return err
		}
		node.Resources = append(node.Resources, r.target.association.resources...)
		slices.Sort(node.Resources)
		node.Resources = slices.Compact(node.Resources)
	}
	return nil
}
