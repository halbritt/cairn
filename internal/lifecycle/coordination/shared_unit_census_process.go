package coordination

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"syscall"

	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// WatchCensusProcess binds a known process to its boot/start identity and group.
// A joined PID alone never proves all of its children or a native connection
// refreshed. Every known child needs its own catalog reference.
type WatchCensusProcess struct {
	PID, PGID, Parent int
	Start, Boot       string
}

type watchCensusCallbackRecord struct {
	Schema        string
	Role          string
	Bindings      []string
	Config        WatchCensusPin
	Binding       WatchCensusPin
	Process       WatchCensusProcess
	Executable    WatchCensusPin
	EntryPoint    WatchCensusPin
	Join          *WatchCensusPin
	selectedOwner string
}

type watchCensusJoin struct {
	Schema   string
	Process  WatchCensusProcess
	Reaper   WatchCensusProcess
	ExitCode int
}

func censusNativeProcess(p WatchCensusProcess) *value.Value {
	return nativeMap("pid", p.PID, "start", value.IntegerText(p.Start), "boot", p.Boot, "parent", p.Parent)
}

func censusProcessIdentity(p WatchCensusProcess) error {
	if p.PID <= 1 || p.PGID <= 1 || p.Parent <= 1 || p.Start == "" || p.Boot == "" {
		return errors.New("census process PID/group/parent must exceed 1 with boot/start identity")
	}
	n, err := strconv.ParseUint(p.Start, 10, 64)
	if err != nil || n == 0 {
		return errors.New("invalid process start identity")
	}
	return nil
}

func (r *watchCensusRead) observeProcess(record watchCensusCallbackRecord) (string, bool, error) {
	p := record.Process
	if err := censusProcessIdentity(p); err != nil {
		return "", false, err
	}
	current, err := nativeProcessReference("/proc", value.IntegerText(strconv.Itoa(p.PID)))
	if err == nil {
		same, e := nativeSameProcess(current, censusNativeProcess(p))
		if e != nil {
			return "", false, e
		}
		if !same {
			return "", false, errors.New("callback PID was reused; original disposition is unknown")
		}
		group, e := syscall.Getpgid(p.PID)
		if e != nil {
			return "", false, e
		}
		if group <= 1 || group != p.PGID || current.Get("parent").Integer != strconv.Itoa(p.Parent) {
			return "", false, errors.New("callback process/group identity changed")
		}
		executable, e := os.Readlink(filepath.Join("/proc", strconv.Itoa(p.PID), "exe"))
		if e != nil {
			return "", false, e
		}
		if executable != record.Executable.Path {
			return "", false, errors.New("actual callback executable differs from its pin")
		}
		cmdline, e := os.ReadFile(filepath.Join("/proc", strconv.Itoa(p.PID), "cmdline"))
		if e != nil {
			return "", false, e
		}
		args := bytes.Split(cmdline, []byte{0})
		contains := func(path string) bool {
			for _, arg := range args {
				if string(arg) == path {
					return true
				}
			}
			return false
		}
		if !contains(record.EntryPoint.Path) || !contains(record.Config.Path) {
			return "", false, errors.New("callback argv does not retain its selected entrypoint/config")
		}
		// A zombie is still unjoined for this preflight. The owner supplies an
		// exact wait receipt and reaps it; the inspector never does either.
		return "running:" + p.Boot + ":" + p.Start + ":" + strconv.Itoa(p.PGID), false, nil
	}
	if !errors.Is(err, os.ErrNotExist) && !errors.Is(err, syscall.ESRCH) {
		return "", false, err
	}
	if record.Join == nil {
		return "unknown:missing-owner-join", false, nil
	}
	if !censusWithin(r.catalog.Root, record.Join.Path) {
		return "", false, errors.New("join evidence is outside the observation universe")
	}
	raw, err := r.pin(*record.Join, false)
	if err != nil {
		return "", false, err
	}
	var join watchCensusJoin
	if err := censusDecode(raw, &join); err != nil {
		return "", false, err
	}
	if join.Schema != "cairn.fixture-callback-join/1" || join.Process != p || join.Reaper.PID != p.Parent {
		return "", false, errors.New("callback join does not match its exact process/owner")
	}
	if err := censusProcessIdentity(join.Reaper); err != nil {
		return "", false, err
	}
	owner, err := nativeProcessReference("/proc", value.IntegerText(strconv.Itoa(join.Reaper.PID)))
	if err != nil {
		return "", false, err
	}
	same, err := nativeSameProcess(owner, censusNativeProcess(join.Reaper))
	if err != nil || !same {
		return "", false, errors.New("callback join owner identity is unavailable")
	}
	group, err := syscall.Getpgid(join.Reaper.PID)
	if err != nil || group <= 1 || group != join.Reaper.PGID {
		return "", false, errors.New("callback reaper group identity changed")
	}
	return "joined:" + record.Join.SHA256, true, nil
}

func (r *watchCensusRead) callbacks(ctx context.Context) error {
	names, err := r.directory(r.catalog.CallbackRoot)
	if err != nil {
		return err
	}
	var expected []string
	pins := map[string]WatchCensusPin{}
	for _, pin := range r.catalog.Callbacks {
		if filepath.Dir(pin.Path) != r.catalog.CallbackRoot || pins[pin.Path].Path != "" {
			return errors.New("callback census has an unknown or duplicate reference")
		}
		expected = append(expected, filepath.Base(pin.Path))
		pins[pin.Path] = pin
	}
	if !censusSameStrings(names, expected) {
		return errors.New("extra or missing actual callback reference; coverage is unknown")
	}
	for _, path := range slices.Sorted(maps.Keys(pins)) {
		if err := ctx.Err(); err != nil {
			return err
		}
		raw, err := r.pin(pins[path], false)
		if err != nil {
			return err
		}
		var record watchCensusCallbackRecord
		if err := censusDecode(raw, &record); err != nil {
			return err
		}
		if record.Schema != "cairn.fixture-callback-reference/1" || !slices.Contains([]string{"callback", "watcher", "wake", "issued"}, record.Role) || len(record.Bindings) == 0 {
			return errors.New("unknown callback reference/role coverage")
		}
		seen := map[string]bool{}
		for _, binding := range record.Bindings {
			if r.nodes[binding] == nil || seen[binding] {
				return errors.New("callback has an unknown/duplicate selected user")
			}
			seen[binding] = true
		}
		for _, pin := range []WatchCensusPin{record.Config, record.EntryPoint} {
			if !censusWithin(r.catalog.Root, pin.Path) {
				return errors.New("callback selected reference is outside the fixture universe")
			}
			if _, err := r.pin(pin, false); err != nil {
				return err
			}
		}
		resources, users, owner, err := r.callbackSelection(record)
		if err != nil {
			return err
		}
		record.selectedOwner = owner
		for _, user := range users {
			if !slices.Contains(record.Bindings, user) {
				record.Bindings = append(record.Bindings, user)
			}
		}
		slices.Sort(record.Bindings)
		for _, user := range record.Bindings {
			r.nodes[user].Resources = append(r.nodes[user].Resources, resources...)
			slices.Sort(r.nodes[user].Resources)
			r.nodes[user].Resources = slices.Compact(r.nodes[user].Resources)
		}
		// The actual interpreter is an immutable observed image, not a claim
		// about any native MCP/channel connection or supported refresh.
		if !censusWithin(r.catalog.Root, record.Executable.Path) {
			return errors.New("callback image is outside the fixture universe")
		}
		if _, err := r.pin(record.Executable, true); err != nil {
			return err
		}
		fact, joined, err := r.observeProcess(record)
		if err != nil {
			return err
		}
		r.report.Facts["process:"+path] = fact
		r.callbackRecords[path] = record
		for _, binding := range record.Bindings {
			if !joined {
				r.nodeBlockers[binding] = append(r.nodeBlockers[binding], fmt.Sprintf("%s callback is running or lacks its owner's exact join", record.Role))
			}
			if record.Role == "issued" {
				r.nodeBlockers[binding] = append(r.nodeBlockers[binding], "retained issued reference needs owning-session disposition")
			}
			if binding == record.selectedOwner && record.Config.SHA256 != r.nodes[binding].Config.SHA256 {
				r.nodeBlockers[binding] = append(r.nodeBlockers[binding], "retained callback still selects old cached bytes; refresh is unobserved")
			}
		}
		r.report.Callbacks = append(r.report.Callbacks, WatchCensusCallback{Reference: path, Role: record.Role, Bindings: slices.Clone(record.Bindings), Config: record.Config, Binding: record.Binding, Process: record.Process, Joined: joined})
	}
	return nil
}

func (r *watchCensusRead) configOwner(config *value.Value) (string, error) {
	owner := ""
	for current, path := range r.configPaths {
		if r.nodes[path] == nil {
			continue
		}
		same := true
		for _, key := range []string{"harness", "binding", "repo", "config_home", "token_file"} {
			same = same && nativeEqual(current.Get(key), config.Get(key))
		}
		if same {
			if owner != "" && owner != path {
				return "", errors.New("retained config has ambiguous selected users")
			}
			owner = path
		}
	}
	if owner == "" {
		return "", errors.New("retained config has an unknown selected user")
	}
	return owner, nil
}

func (r *watchCensusRead) callbackSelection(record watchCensusCallbackRecord) ([]string, []string, string, error) {
	config, err := r.jsonFile(record.Config.Path)
	if err != nil {
		return nil, nil, "", err
	}
	if _, err := ValidateConfig(config); err != nil {
		return nil, nil, "", err
	}
	if !r.local.tokenPaths[nativeText(config.Get("token_file"))] {
		return nil, nil, "", errors.New("retained credential selector coverage is unknown")
	}
	owner, err := r.configOwner(config)
	if err != nil {
		return nil, nil, "", err
	}
	if record.Binding.Path != nativeText(config.Get("inbox_recall_binding")) {
		return nil, nil, "", errors.New("callback omitted its retained binding identity")
	}
	if _, err := r.pin(record.Binding, false); err != nil {
		return nil, nil, "", err
	}
	manifest, err := r.jsonFile(record.Binding.Path)
	if err != nil {
		return nil, nil, "", err
	}
	mode := nativeText(manifest.Get("schema"))
	if mode != "cairn.inbox-recall-binding/2" && mode != "cairn.inbox-recall-binding/3" {
		return nil, nil, "", errors.New("unknown retained binding schema")
	}
	memory, _, err := r.descriptor(manifest.Get("memory_config"))
	if err != nil {
		return nil, nil, "", err
	}
	list := manifest.Get("coordination_configs")
	if list == nil || list.Kind != value.Array {
		return nil, nil, "", errors.New("retained profile membership is unknown")
	}
	var configs []*value.Value
	var users []string
	for _, desc := range list.Items {
		coord, _, err := r.descriptor(desc)
		if err != nil {
			return nil, nil, "", err
		}
		user, err := r.configOwner(coord)
		if err != nil {
			return nil, nil, "", err
		}
		configs = append(configs, coord)
		users = append(users, user)
		r.local.configs = append(r.local.configs, coord)
		r.configPaths[coord] = user
	}
	if err := binding.ValidateConfigurationProfiles(binding.ConfigurationProfiles{Current: config, Memory: memory, Coordinators: configs, ManifestPath: []rune(record.Binding.Path), Resolve: r.cli.profile().Routes.Filesystem().Resolve}); err != nil {
		return nil, nil, "", err
	}
	resources := []string{nativeText(config.Get("state_dir")), nativeText(memory.Get("state_dir")), filepath.Dir(record.Executable.Path)}
	for _, path := range resources {
		if _, err := r.directory(path); err != nil {
			return nil, nil, "", err
		}
	}
	client := nativeText(config.Get("cairn"))
	if !censusWithin(r.catalog.Root, client) {
		return nil, nil, "", errors.New("retained client image is outside the observation universe")
	}
	if _, err := r.pin(WatchCensusPin{client, r.clientPins[client]}, true); err != nil {
		return nil, nil, "", err
	}
	retained := &WatchCensusMember{Mode: mode}
	if err := r.runtime(retained, manifest); err != nil {
		return nil, nil, "", err
	}
	resources = append(resources, retained.Resources...)
	return resources, users, owner, nil
}
