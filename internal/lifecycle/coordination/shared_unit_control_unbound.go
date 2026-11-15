package coordination

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"maps"
	"os"
	"path/filepath"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Only a complete census can construct this process-local comparison. Proposed
// files must be actual tracked receipt descriptors, not arbitrary copies.
// Their identity therefore remains covered by the existing drain D6 reread.
type unitControlUnboundPreflight struct {
	source  *unitCensusDrain
	plan    []byte
	facts   map[string]string
	members map[string]WatchCensusMember
}

func (c *CLI) unitControlUnboundPreflight(ctx context.Context, p *UnitControlPlan, proof **unitControlUnboundPreflight, passOwner **watchCensusRead) error {
	current := WatchBindings{Directory: p.Current.Directory, Pins: p.Current.Pins}
	names, err := watchBindingNames(current.Pins)
	if err != nil {
		return err
	}
	hasUnbound := false
	credentialPaths := map[string]bool{}
	for _, name := range names {
		raw, _, err := compatibilityOpened(filepath.Join(current.Directory, name), 1048576, uint32(os.Getuid()))
		if err != nil {
			return err
		}
		if c.profile() == nil {
			return &value.ProfileUnavailable{}
		}
		cfg, err := c.profile().Decode(SiteSessionRead, raw)
		if err != nil {
			return err
		}
		if nativeNone(cfg.Get("inbox_recall_binding")) {
			hasUnbound = true
		}
		credentialPaths[nativeText(cfg.Get("token_file"))] = true
	}
	if !hasUnbound {
		if p.Census != nil && p.Census.TargetReceipt != nil {
			return errors.New("target receipt requires a selected unbound member")
		}
		return nil
	}
	if p.Census == nil || len(p.Census.Proposal.Bindings) != 1 || p.Census.Proposal.Bindings[0].Directory != current.Directory || !maps.Equal(p.Census.Proposal.Bindings[0].Pins, current.Pins) {
		return errors.New("unbound preflight requires the exact pinned current census selection")
	}
	// Exclude known selectors and inode aliases before even opening the catalog
	// or generic next-file reader. These are metadata observations, never tokens.
	credentials := []os.FileInfo{}
	for path := range credentialPaths {
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		credentials = append(credentials, info)
	}
	paths := []string{p.Census.Catalog.Path}
	if p.Census.TargetReceipt != nil {
		paths = append(paths, p.Census.TargetReceipt.Path)
	}
	for _, file := range p.Next {
		paths = append(paths, file.Path)
	}
	for _, path := range paths {
		if credentialPaths[path] {
			return errors.New("unbound preflight refuses credential-content reads")
		}
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		for _, credential := range credentials {
			if os.SameFile(info, credential) {
				return errors.New("unbound preflight refuses credential-content reads through aliases")
			}
		}
	}
	plan, err := json.Marshal(p)
	if err != nil {
		return err
	}
	fresh := *proof == nil
	if fresh {
		input := cloneUnitControlCensus(p.Census)
		scope := &unitCensusScope{current: current, retained: input.Retained, target: input.TargetReceipt}
		var report WatchUnitCensus
		var pass *watchCensusRead
		var err error
		if passOwner != nil {
			pass, err = c.openWatchUnitCensus(ctx, input.Catalog, input.Proposal, scope)
			*passOwner = pass
			report = pass.report
		} else {
			report, err = c.inspectWatchUnitCensus(ctx, input.Catalog, input.Proposal, scope)
		}
		if err != nil && !censusOnlyNeedsD2(err) {
			return err
		}
		report.Facts = maps.Clone(report.Facts)
		*proof = &unitControlUnboundPreflight{source: &unitCensusDrain{cli: c, input: input, current: cloneCensusSelection(current), initial: report, pass: pass}, plan: plan}
	} else if !bytes.Equal(plan, (*proof).plan) {
		return errors.New("unbound preflight plan/census selection changed")
	}
	v := *proof
	if fresh {
		// No status call or selected-file work intervened after the full
		// inspector. Derive the private association from those actual facts.
		members, facts, err := v.source.associationFactsFromReport(v.source.initial, nil)
		if err != nil {
			return err
		}
		v.members, v.facts = members, maps.Clone(facts)
		v.source.associationFacts = maps.Clone(facts)
	} else {
		if err := v.recheck(ctx); err != nil {
			return err
		}
	}
	read := &watchDrainRead{cli: c, facts: map[string]string{}, tokenPaths: map[string]bool{}, configPaths: map[*value.Value]string{}, censusMembers: v.members}
	if v.source.pass != nil {
		read.verified = v.source.pass.local.verified
	}
	for key := range v.facts {
		if strings.HasPrefix(key, "credential-reference:") {
			read.tokenPaths[strings.TrimPrefix(key, "credential-reference:")] = true
		}
	}
	seen := map[string]bool{}
	for _, file := range p.Next {
		if _, ok := current.Pins[file.Name]; !ok || file.Executable {
			return errors.New("watch exchange must retain every binding name as data")
		}
		if seen[file.Name] {
			return errors.New("duplicate next watch binding")
		}
		seen[file.Name] = true
		if read.tokenPaths[file.Path] {
			return errors.New("unbound preflight refuses credential-content reads")
		}
		member, ok := v.members[filepath.Join(current.Directory, file.Name)]
		if !ok {
			continue
		} // Bound pairs retain their original validator below.
		// The selected receipt must actually pin this proposed path, not only a
		// current profile with a matching selector or an asserted memory directory.
		if v.facts["file:"+file.Path] != file.SHA256 {
			return errors.New("unbound next profile lacks a tracked receipt descriptor")
		}
		bindingPin, memoryPin := member.Binding, member.Memory
		if target := v.source.initial.target; target != nil {
			if profile, ok := target.profiles[file.Name]; ok {
				if file.SHA256 != profile.SHA256 || (file.Path != profile.Path && !v.retainedFile(file.Name, file.Path, file.SHA256)) {
					return errors.New("target proposal omits or changes an associated coordinator")
				}
				bindingPin, memoryPin = target.association.binding, target.memory
			}
		}
		manifest, err := read.file(bindingPin.Path)
		if err != nil {
			return err
		}
		enabled := manifest.Get("enabled")
		if read.facts["file:"+bindingPin.Path] != bindingPin.SHA256 || !value.SameText(manifest.Get("schema"), "cairn.inbox-recall-binding/3") || enabled == nil || enabled.Kind != value.Bool || enabled.Boolean {
			return errors.New("unbound preflight requires the selected disabled /3 manifest")
		}
		matches := 0
		if coords := manifest.Get("coordination_configs"); coords != nil && coords.Kind == value.Array {
			for _, desc := range coords.Items {
				if (value.SameText(desc.Get("path"), file.Path) || v.retainedFile(file.Name, file.Path, file.SHA256)) && value.SameText(desc.Get("sha256"), file.SHA256) {
					matches++
				}
			}
		}
		if matches != 1 {
			return errors.New("unbound next profile lacks exact receipt membership")
		}
		next, err := read.descriptor(nativeMap("path", file.Path, "sha256", file.SHA256))
		if err != nil {
			return err
		}
		if _, err := ValidateConfig(next); err != nil {
			return err
		}
		if !nativeNone(next.Get("inbox_recall_binding")) {
			return errors.New("unbound preflight cannot mix binding shapes")
		}
		config, err := read.descriptor(nativeMap("path", member.Config.Path, "sha256", member.Config.SHA256))
		if err != nil {
			return err
		}
		read.configPaths[config] = member.Config.Path
		currentMemory, err := read.unboundMemory(config)
		if err != nil {
			return err
		}
		memoryDescriptor := manifest.Get("memory_config")
		if !value.SameText(memoryDescriptor.Get("path"), memoryPin.Path) || !value.SameText(memoryDescriptor.Get("sha256"), memoryPin.SHA256) {
			return errors.New("unbound preflight memory association changed")
		}
		memory, err := read.descriptor(memoryDescriptor)
		if err != nil {
			return err
		}
		for _, key := range []string{"harness", "cairn", "socket", "token_file", "repo"} {
			if !nativeEqual(next.Get(key), memory.Get(key)) {
				return errors.New("unbound proposed profile and memory selectors differ")
			}
		}
		if err := unitControlPairIdentity([]*value.Value{config, next}, []string{nativeText(currentMemory.Get("state_dir")), nativeText(memory.Get("state_dir"))}); err != nil {
			return err
		}
	}
	return v.recheck(ctx)
}

func (v *unitControlUnboundPreflight) recheck(ctx context.Context) error {
	members, facts, err := v.source.observeDrainAssociations(ctx, nil)
	if err != nil {
		return err
	}
	if v.facts != nil && !maps.Equal(facts, v.facts) {
		return errors.New("unbound preflight association/identity changed")
	}
	if v.facts == nil {
		v.facts = maps.Clone(facts)
	}
	v.members = members
	return nil
}

func (v *unitControlUnboundPreflight) retainedFile(name, path, pin string) bool {
	for _, copy := range v.source.input.Retained {
		if filepath.Join(copy.Directory, name) == path && copy.Pins[name] == pin && v.facts["file:"+path] == pin {
			return true
		}
	}
	return false
}
