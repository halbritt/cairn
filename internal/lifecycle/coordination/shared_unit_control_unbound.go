package coordination

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"maps"
	"os"
	"path/filepath"
	"slices"
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

func (c *CLI) unitControlUnboundPreflight(ctx context.Context, p *UnitControlPlan, proof **unitControlUnboundPreflight) error {
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
	if *proof == nil {
		input := *p.Census
		input.Proposal.Bindings = []WatchBindings{cloneCensusSelection(current)}
		input.Proposal.Sessions = slices.Clone(input.Proposal.Sessions)
		input.Proposal.Callbacks = slices.Clone(input.Proposal.Callbacks)
		input.Retained = make([]WatchBindings, len(p.Census.Retained))
		for i, s := range p.Census.Retained {
			input.Retained[i] = cloneCensusSelection(s)
		}
		report, err := c.inspectWatchUnitCensus(ctx, input.Catalog, input.Proposal, &unitCensusScope{current: current, retained: input.Retained})
		if err != nil && !censusOnlyNeedsD2(err) {
			return err
		}
		report.Facts = maps.Clone(report.Facts)
		*proof = &unitControlUnboundPreflight{source: &unitCensusDrain{cli: c, input: input, current: cloneCensusSelection(current), initial: report}, plan: plan}
	} else if !bytes.Equal(plan, (*proof).plan) {
		return errors.New("unbound preflight plan/census selection changed")
	}
	v := *proof
	if err := v.recheck(ctx); err != nil {
		return err
	}
	read := &watchDrainRead{cli: c, facts: map[string]string{}, tokenPaths: map[string]bool{}, configPaths: map[*value.Value]string{}, censusMembers: v.members}
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
		manifest, err := read.file(member.Binding.Path)
		if err != nil {
			return err
		}
		enabled := manifest.Get("enabled")
		if read.facts["file:"+member.Binding.Path] != member.Binding.SHA256 || !value.SameText(manifest.Get("schema"), "cairn.inbox-recall-binding/3") || enabled == nil || enabled.Kind != value.Bool || enabled.Boolean {
			return errors.New("unbound preflight requires the selected disabled /3 manifest")
		}
		matches := 0
		if coords := manifest.Get("coordination_configs"); coords != nil && coords.Kind == value.Array {
			for _, desc := range coords.Items {
				if value.SameText(desc.Get("path"), file.Path) && value.SameText(desc.Get("sha256"), file.SHA256) {
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
		if !value.SameText(memoryDescriptor.Get("path"), member.Memory.Path) || !value.SameText(memoryDescriptor.Get("sha256"), member.Memory.SHA256) {
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
