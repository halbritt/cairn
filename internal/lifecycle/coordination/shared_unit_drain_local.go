package coordination

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/sys/unix"
)

func (r *watchDrainRead) file(path string) (*value.Value, error) {
	if r.tokenPaths[path] {
		return nil, errors.New("drain scope overlaps a credential selector; visibility is unknown")
	}
	raw, digest, err := compatibilityOpened(path, 1048576, uint32(os.Getuid()))
	if err != nil {
		return nil, err
	}
	r.facts["file:"+path] = digest
	decoded, err := r.cli.profile().Decode(SiteSessionRead, raw)
	if err != nil || decoded == nil || decoded.Kind != value.Object {
		return nil, errors.Join(err, fmt.Errorf("unknown drain JSON object: %s", path))
	}
	return decoded, nil
}

func (r *watchDrainRead) descriptor(desc *value.Value) (*value.Value, error) {
	if desc == nil || desc.Kind != value.Object {
		return nil, errors.New("unknown selected drain descriptor")
	}
	path := nativeText(desc.Get("path"))
	decoded, err := r.file(path)
	if err != nil {
		return nil, err
	}
	if !value.SameText(desc.Get("sha256"), r.facts["file:"+path]) {
		return nil, errors.New("selected drain descriptor changed")
	}
	return decoded, nil
}

func (r *watchDrainRead) local(selected WatchBindings) error {
	if err := readWatchBindings(selected); err != nil {
		return err
	}
	names, err := watchBindingNames(selected.Pins)
	if err != nil {
		return err
	}
	states, memories := map[string]bool{}, map[string]bool{}
	for _, name := range names {
		config, err := r.file(filepath.Join(selected.Directory, name))
		if err != nil {
			return err
		}
		if _, err := ValidateConfig(config); err != nil {
			return err
		}
		r.configs = append(r.configs, config)
		r.tokenPaths[nativeText(config.Get("token_file"))] = true
		binary, err := compatibilityOpenedBinary(nativeText(config.Get("cairn")), uint32(os.Getuid()))
		if err != nil {
			return err
		}
		r.facts["executable:"+nativeText(config.Get("cairn"))] = binary
		states[nativeText(config.Get("state_dir"))] = true
	}
	for _, config := range r.configs {
		memory, err := r.disabledBinding(config)
		if err != nil {
			return err
		}
		memories[nativeText(memory.Get("state_dir"))] = true
	}
	for directory := range states {
		files, err := r.tree(directory)
		if err != nil {
			return err
		}
		if err := r.sessionFacts(directory, files); err != nil {
			return err
		}
	}
	for directory := range memories {
		files, err := r.tree(directory)
		if err != nil {
			return err
		}
		for _, path := range files {
			ledger, err := r.file(path)
			if err != nil {
				return err
			}
			if err := inactiveGrantFacts(ledger); err != nil {
				return err
			}
		}
	}
	return nil
}

func (r *watchDrainRead) disabledBinding(config *value.Value) (*value.Value, error) {
	manifest, err := r.file(nativeText(config.Get("inbox_recall_binding")))
	if err != nil {
		return nil, fmt.Errorf("D1 binding visibility is unknown: %w", err)
	}
	schema := nativeText(manifest.Get("schema"))
	if schema != "cairn.inbox-recall-binding/2" && schema != "cairn.inbox-recall-binding/3" {
		return nil, errors.New("D1 unknown binding schema")
	}
	enabled := manifest.Get("enabled")
	if enabled == nil || enabled.Kind != value.Bool || enabled.Boolean {
		return nil, errors.New("D1 selected binding is enabled or unknown")
	}
	memory, err := r.descriptor(manifest.Get("memory_config"))
	if err != nil {
		return nil, err
	}
	descriptors := manifest.Get("coordination_configs")
	if descriptors == nil || descriptors.Kind != value.Array {
		return nil, errors.New("D1 profile membership is unknown")
	}
	var coordinators []*value.Value
	for _, desc := range descriptors.Items {
		coord, err := r.descriptor(desc)
		if err != nil {
			return nil, err
		}
		coordinators = append(coordinators, coord)
	}
	if err := binding.ValidateConfigurationProfiles(binding.ConfigurationProfiles{
		Current: config, Memory: memory, Coordinators: coordinators,
		ManifestPath: config.Get("inbox_recall_binding").String, Resolve: r.cli.profile().Routes.Filesystem().Resolve,
	}); err != nil {
		return nil, err
	}
	return memory, nil
}

func inactiveGrantFacts(ledger *value.Value) error {
	if decisions := ledger.Get("inbox_activation"); decisions != nil {
		if decisions.Kind != value.Object {
			return errors.New("D1 activation ledger is unknown")
		}
		for _, decision := range decisions.Members {
			if decision.Value == nil || decision.Value.Kind != value.Bool || decision.Value.Boolean {
				return errors.New("D1 retained positive or unknown activation grant needs owning-session disposition")
			}
		}
	}
	return nil
}

// tree records directory membership and data bytes. It never reads lock or
// credential contents; existing lock inodes are observed through /proc/locks.
func (r *watchDrainRead) tree(directory string) ([]string, error) {
	if !compatibilityCanonical(directory) {
		return nil, errors.New("unknown drain state directory")
	}
	entries, err := os.ReadDir(directory)
	if errors.Is(err, os.ErrNotExist) {
		r.facts["directory:"+directory] = "absent"
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var files, membership []string
	for _, entry := range entries {
		path := filepath.Join(directory, entry.Name())
		membership = append(membership, entry.Name()+":"+entry.Type().String())
		switch {
		case entry.IsDir():
			children, err := r.tree(path)
			if err != nil {
				return nil, err
			}
			files = append(files, children...)
		case strings.HasSuffix(path, ".lock"), strings.HasSuffix(path, ".cuelock"):
			if err := r.lockFile(path); err != nil {
				return nil, err
			}
		case strings.HasSuffix(path, ".json"), strings.HasSuffix(path, ".initialized"):
			if _, err := r.file(path); err != nil {
				return nil, err
			}
			files = append(files, path)
		default:
			return nil, fmt.Errorf("unknown state member: %s", path)
		}
	}
	r.remember("directory:"+directory, []byte(strings.Join(membership, "\x00")))
	return files, nil
}

func (r *watchDrainRead) lockFile(path string) error {
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() {
		return errors.New("unknown state lock inode")
	}
	key := fmt.Sprintf("%x:%x:%d", unix.Major(uint64(stat.Dev)), unix.Minor(uint64(stat.Dev)), stat.Ino)
	r.locks[key] = path
	r.facts["lock-inode:"+path] = key
	return nil
}

func (r *watchDrainRead) sessionFacts(directory string, files []string) error {
	var indexed []string
	for _, path := range files {
		if filepath.Dir(path) != directory || !strings.HasSuffix(path, ".json") {
			continue
		}
		state, err := r.file(path)
		if err != nil {
			return err
		}
		if !value.SameText(state.Get("schema"), NativeSessionSchema) {
			return errors.New("D2 native session schema is unknown")
		}
		config := r.sessionConfig(path, state)
		if config == nil {
			return errors.New("D2 session binding or unit coverage is unknown")
		}
		for _, key := range []string{"batch_advance_intent", "organic_claim_intent", "organic_advance_intent", "active_prompt", "idle_wake", "cancel_submission", "cancel_owner_join"} {
			if value.Truth(state.Get(key)) {
				return fmt.Errorf("D2 %s needs authoritative terminal/close correspondence; unknown or unsettled", key)
			}
		}
		ref, err := r.cli.sessionRef(state.Get("agent"))
		if err != nil {
			return err
		}
		r.sessions = append(r.sessions, watchDrainSession{config: config, ref: ref, state: state})
		if ids := state.Get("inbox_journals"); ids != nil {
			if ids.Kind != value.Array {
				return errors.New("D3 journal index is unknown")
			}
			for _, id := range ids.Items {
				if id == nil || id.Kind != value.String {
					return errors.New("D3 journal reference is unknown")
				}
				indexed = append(indexed, nativeText(id))
			}
		}
	}
	inspection, err := InspectWatchJournals(directory, indexed)
	if err != nil {
		return err
	}
	for _, row := range inspection.Journals {
		if err := drainedJournalFacts(row.Status); err != nil {
			return fmt.Errorf("D3 %s: %w", row.AttemptID, err)
		}
	}
	return nil
}

func (r *watchDrainRead) sessionConfig(path string, state *value.Value) *value.Value {
	native := state.Get("native_session_id")
	if native == nil || native.Kind != value.String {
		return nil
	}
	for _, config := range r.configs {
		expected := SessionStatePath(nativeText(config.Get("state_dir")), nativeText(config.Get("binding")), nativeText(native))
		if expected == path {
			return config
		}
	}
	return nil
}

func drainedJournalFacts(status journal.JournalStatus) error {
	if !status.Exists || status.ReadErr != nil || status.ParseErr != nil || status.NonObject || status.SessionNonObject || status.Schema != journal.Schema {
		return errors.New("journal/reference visibility is unknown")
	}
	if status.PendingReplay || status.ReplyPossible {
		return errors.New("pending command or unresolved reply_possible; owning session must settle")
	}
	if !status.CompletionPresent || status.CompletionNonObject || status.CompletionStatus != "committed" && status.CompletionStatus != "refused" {
		return errors.New("completion correspondence is unknown or unsettled")
	}
	if status.CompletionKind != "complete" && status.CompletionKind != "ack" || status.CompletionRequestID == "" {
		return errors.New("completion kind/retry reference is unknown")
	}
	if status.CompletionKind == "complete" && status.CompletionStatus == "committed" && !status.HasUsableFinishReplyRef {
		return errors.New("exact completion reference is unavailable")
	}
	if status.ResponsePresent && (status.ResponseNonObject || status.ResponseStatus != "committed" && status.ResponseStatus != "refused") {
		return errors.New("response correspondence is unknown or unsettled")
	}
	return nil
}
