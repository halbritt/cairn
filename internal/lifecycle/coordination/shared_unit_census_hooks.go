package coordination

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"

	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// WatchCensusClaudeHooks selects finite fixture observations, not authenticated
// native ownership, global layer coverage or an active MCP connection.
type WatchCensusClaudeHooks struct {
	Schema, Harness, Layer string
	Account, Workspace     string
	Config, Session        WatchCensusPin
	Generation             WatchCensusPin
	Settings, Readback     WatchCensusPin
	LayerFiles             []WatchCensusPin
}

type WatchCensusHookExecution struct {
	Role, Event       string
	Launcher, Program WatchCensusPin
	Argv              []string
	PID, Exit         int
}

type WatchCensusClaudeReadback struct {
	Schema, Harness, Layer, NativeSession string
	Settings, Shell                       WatchCensusPin
	Effective                             json.RawMessage
	Listing                               []WatchCensusHookListing
	Executions                            []WatchCensusHookExecution
}

type WatchCensusHookListing struct{ Event, Command, Source string }

type WatchCensusHookObservation struct {
	Config, Session, Generation, Settings WatchCensusPin
	Layer, Observation                    string
}

func (r *watchCensusRead) loadHookSelections() error {
	seen := map[string]bool{}
	for _, pin := range r.catalog.HookSelections {
		if seen[pin.Path] || !censusWithin(r.catalog.Root, pin.Path) {
			return errors.New("duplicate or external native hook selection")
		}
		seen[pin.Path] = true
		raw, err := r.pin(pin, false)
		if err != nil {
			return err
		}
		var selected WatchCensusClaudeHooks
		if err := hookDecode(raw, &selected); err != nil {
			return err
		}
		if selected.Schema != "cairn.watch-claude-hooks/1" || selected.Harness != "claude" || selected.Layer != "flagSettings" {
			return errors.New("unsupported native harness/layer; connection refresh remains unobserved")
		}
		if !censusWithin(r.catalog.Root, selected.Account) || !censusWithin(r.catalog.Root, selected.Workspace) {
			return errors.New("native hook account/workspace is outside the finite universe")
		}
		r.hookSelections = append(r.hookSelections, selected)
	}
	return nil
}

func (r *watchCensusRead) inspectHookSelections(ctx context.Context) error {
	var observations []WatchCensusHookObservation
	seen := map[string]bool{}
	for _, selected := range r.hookSelections {
		if err := ctx.Err(); err != nil {
			return err
		}
		key := selected.Config.Path + "\x00" + selected.Session.Path
		if seen[key] {
			return errors.New("duplicate native hook session selection")
		}
		seen[key] = true
		if err := r.inspectClaudeHooks(ctx, selected); err != nil {
			return err
		}
		observations = append(observations, WatchCensusHookObservation{selected.Config, selected.Session,
			selected.Generation, selected.Settings, selected.Layer, "selected settings, native listing and prompt exec observations agree; connection refresh unobserved"})
	}
	for path, node := range r.nodes {
		if len(r.directories[node.Account]) == 0 {
			continue
		}
		found := false
		for _, selection := range r.hookSelections {
			found = found || selection.Config.Path == path
		}
		if !found {
			return errors.New("nonempty native account lacks selected config readback")
		}
	}
	r.report.HookObservations = observations
	return nil
}

func (r *watchCensusRead) hookLayers(ctx context.Context, selected WatchCensusClaudeHooks) error {
	wanted, err := censusPinMap(selected.LayerFiles)
	if err != nil {
		return err
	}
	seen := map[string]bool{}
	var walk func(string) error
	walk = func(directory string) error {
		names, err := r.directory(directory)
		if err != nil {
			return err
		}
		for _, name := range names {
			if err := ctx.Err(); err != nil {
				return err
			}
			path := filepath.Join(directory, name)
			info, err := os.Lstat(path)
			if err != nil {
				return err
			}
			if info.IsDir() {
				if err := walk(path); err != nil {
					return err
				}
				continue
			}
			if wanted[path] == "" || seen[path] {
				return errors.New("unknown, duplicate or omitted account/project layer file")
			}
			seen[path] = true
			raw, err := r.pin(WatchCensusPin{path, wanted[path]}, false)
			if err != nil {
				return err
			}
			// Other layers are not enabled by the selected --settings invocation.
			// Refuse hooks there rather than guessing which commands are Cairn.
			if name == "settings.json" || name == "settings.local.json" {
				var settings map[string]json.RawMessage
				if err := hookDecode(raw, &settings); err != nil {
					return err
				}
				if hooks := settings["hooks"]; hooks != nil && !hookJSONEqual(hooks, []byte("{}")) && string(hooks) != "null" {
					return errors.New("unsupported account/project hook layer: effective native hook coverage is unknown")
				}
			}
		}
		return nil
	}
	if selected.Account == selected.Workspace || censusOverlap(selected.Account, selected.Workspace) {
		return errors.New("overlapping native account/project layer inventory")
	}
	for _, path := range []string{selected.Account, selected.Workspace} {
		if err := walk(path); err != nil {
			return err
		}
	}
	if len(seen) != len(wanted) {
		return errors.New("extra native layer pin outside account/project inventory")
	}
	return nil
}

func (r *watchCensusRead) inspectClaudeHooks(ctx context.Context, selected WatchCensusClaudeHooks) error {
	for _, pin := range []WatchCensusPin{selected.Config, selected.Session, selected.Generation, selected.Settings, selected.Readback} {
		if !censusWithin(r.catalog.Root, pin.Path) {
			return errors.New("native hook selected file/readback outside universe")
		}
	}
	node := r.nodes[selected.Config.Path]
	if node == nil || node.Config != selected.Config || node.Account != selected.Account {
		return errors.New("native hook selection names an unknown or mixed unit/config")
	}
	config, err := r.jsonFile(selected.Config.Path)
	if err != nil {
		return err
	}
	if !value.SameText(config.Get("harness"), "claude") {
		return errors.New("unsupported native hook config harness")
	}
	if !slices.Contains(r.catalog.Sessions, selected.Session) {
		return errors.New("native hook readback session is not catalogued")
	}
	if _, err := r.pin(selected.Session, false); err != nil {
		return err
	}
	state, err := r.jsonFile(selected.Session.Path)
	if err != nil {
		return err
	}
	owner := r.local.sessionConfig(selected.Session.Path, state)
	if owner == nil || r.configPaths[owner] != selected.Config.Path || nativeText(state.Get("agent").Get("metadata").Get("workspace")) != selected.Workspace || !value.SameText(state.Get("schema"), NativeSessionSchema) {
		return errors.New("native hook session/workspace/config identity disagrees")
	}
	if err := r.hookLayers(ctx, selected); err != nil {
		return err
	}
	if !censusWithin(r.catalog.Root, selected.Generation.Path) {
		return errors.New("external native hook generation")
	}
	files, err := install.ReadGeneration(ctx, install.Generation{Path: selected.Generation.Path, SHA256: selected.Generation.SHA256})
	if err != nil {
		return fmt.Errorf("native hook generation: %w", err)
	}
	if !censusWithin(r.catalog.Root, selected.Generation.Path) {
		return errors.New("external native hook generation")
	}
	if _, err := r.pin(selected.Generation, false); err != nil {
		return err
	}
	members := map[string]WatchCensusPin{}
	for _, file := range files {
		if !censusWithin(r.catalog.Root, file.Path) {
			return errors.New("external native hook generation member")
		}
		pin := WatchCensusPin{file.Path, file.SHA256}
		if _, err := r.pin(pin, file.Executable); err != nil {
			return err
		}
		members[file.Name] = pin
	}
	if len(members) != 3 || members["hook-settings.json"] != selected.Settings || members["previous-settings.json"].Path == "" || members["hook-selection.json"].Path == "" {
		return errors.New("native hook generation membership/settings selection disagrees")
	}
	producer, err := r.jsonFile(members["hook-selection.json"].Path)
	if err != nil {
		return err
	}
	if !value.SameText(producer.Get("schema"), "cairn.lifecycle-hook-settings/1") || !value.SameText(producer.Get("harness"), "claude") || nativeText(producer.Get("account_home")) != selected.Account {
		return errors.New("native hook producer harness/account disagrees")
	}
	lifecycle := producer.Get("lifecycle_selection")
	lifePin := WatchCensusPin{nativeText(lifecycle.Get("path")), nativeText(lifecycle.Get("sha256"))}
	if !censusWithin(r.catalog.Root, lifePin.Path) {
		return errors.New("external native lifecycle selection")
	}
	if _, err := r.pin(lifePin, false); err != nil {
		return err
	}
	lifeFiles, err := install.ReadGeneration(ctx, install.Generation{Path: lifePin.Path, SHA256: lifePin.SHA256})
	if err != nil {
		return err
	}
	bindingMatched := false
	for _, file := range lifeFiles {
		pin := WatchCensusPin{file.Path, file.SHA256}
		if !censusWithin(r.catalog.Root, file.Path) {
			return errors.New("external native lifecycle member")
		}
		if _, err := r.pin(pin, file.Executable); err != nil {
			return err
		}
		bindingMatched = bindingMatched || pin == node.Binding
	}
	if !bindingMatched {
		return errors.New("native hook generation selects another lifecycle binding")
	}
	if !exactReceiptObject(producer.Get("layout"), "hook-settings.json") || !value.SameText(producer.Get("layout").Get("hook-settings.json"), "settings.json") || nativeText(producer.Get("source_settings").Get("path")) != filepath.Join(selected.Account, "settings.json") {
		return errors.New("unsupported native hook source/layout selection")
	}
	if nativeText(producer.Get("source_settings").Get("sha256")) != members["previous-settings.json"].SHA256 {
		return errors.New("native hook previous settings do not match the emitted source pin")
	}
	for _, role := range []string{"memory", "coordination"} {
		_, pin, err := r.descriptor(producer.Get("role_configurations").Get(role))
		if err != nil {
			return err
		}
		expected := node.Config
		if role == "memory" {
			expected = node.Memory
		}
		if pin.SHA256 != expected.SHA256 || role == "memory" && pin.Path != expected.Path {
			return errors.New("native producer selects a mixed role/config")
		}
	}
	settingsRaw, err := r.pin(selected.Settings, false)
	if err != nil {
		return err
	}
	previousRaw, err := r.pin(members["previous-settings.json"], false)
	if err != nil {
		return err
	}
	var settings, previous map[string]json.RawMessage
	if err := hookDecode(settingsRaw, &settings); err != nil {
		return err
	}
	if err := hookDecode(previousRaw, &previous); err != nil {
		return err
	}
	for key, old := range previous {
		if key != "hooks" && !hookJSONEqual(old, settings[key]) {
			return errors.New("native hook producer changed unrelated settings")
		}
	}
	for key := range settings {
		if key != "hooks" && previous[key] == nil {
			return errors.New("native hook producer added unrelated settings")
		}
	}
	return r.claudeHookReadback(selected, state, producer, settings)
}

func hookDecode(raw []byte, dst any) error {
	if err := jsontext.CheckUnicode(raw); err != nil {
		return err
	}
	if _, err := value.DecodeTextWithObjectPolicy(raw, "utf-8", 0, value.DuplicateReject); err != nil {
		return err
	}
	return censusDecode(raw, dst)
}

func hookJSONEqual(a, b []byte) bool {
	left, e1 := value.DecodeTextWithObjectPolicy(a, "utf-8", 0, value.DuplicateReject)
	right, e2 := value.DecodeTextWithObjectPolicy(b, "utf-8", 0, value.DuplicateReject)
	if e1 != nil || e2 != nil {
		return false
	}
	return hookValuesEqual(left, right)
}

func hookValuesEqual(a, b *value.Value) bool {
	left, e1 := canonicalSortedJSONSHA256(a)
	right, e2 := canonicalSortedJSONSHA256(b)
	return e1 == nil && e2 == nil && left == right
}

func (r *watchCensusRead) hookProgram(pin WatchCensusPin) error {
	if censusWithin(r.catalog.Root, pin.Path) {
		_, err := r.pin(pin, true)
		return err
	}
	resolved, err := filepath.EvalSymlinks(pin.Path)
	if err != nil {
		return err
	}
	allowed := false
	for _, system := range []string{"/bin/sh", "/usr/bin/python3"} {
		real, err := filepath.EvalSymlinks(system)
		allowed = allowed || err == nil && real == resolved
	}
	if !allowed || !censusDigest(pin.SHA256) {
		return errors.New("unsupported native hook system program")
	}
	if err := r.safePath(resolved, false); err != nil {
		return err
	}
	var st syscall.Stat_t
	if err := syscall.Stat(resolved, &st); err != nil {
		return err
	}
	fd, err := syscall.Open(resolved, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if err != nil {
		return err
	}
	_, digest, err := readOpenedFD(fd, resolved, -1, true, st.Uid)
	if err != nil || digest != pin.SHA256 {
		return errors.New("native hook program/shell pin changed")
	}
	key := "hook-tool:" + pin.Path
	if old := r.report.Facts[key]; old != "" && old != digest {
		return errors.New("native hook system program changed during readback")
	}
	r.report.Facts[key] = digest
	return nil
}

func (r *watchCensusRead) claudeHookReadback(selected WatchCensusClaudeHooks, state, producer *value.Value, settings map[string]json.RawMessage) error {
	raw, err := r.pin(selected.Readback, false)
	if err != nil {
		return err
	}
	var observed WatchCensusClaudeReadback
	if err := hookDecode(raw, &observed); err != nil {
		return err
	}
	if observed.Schema != "cairn.watch-claude-readback/1" || observed.Harness != "claude" || observed.Layer != "flagSettings" || observed.Settings != selected.Settings || observed.NativeSession != nativeText(state.Get("native_session_id")) {
		return errors.New("missing, stale or mismatched native hook readback")
	}
	if observed.Shell.Path != "/bin/sh" {
		return errors.New("unsupported native hook shell identity")
	}
	if err := r.hookProgram(observed.Shell); err != nil {
		return err
	}
	var effective map[string]json.RawMessage
	if err := hookDecode(observed.Effective, &effective); err != nil {
		return err
	}
	if !hookJSONEqual(effective["hooks"], settings["hooks"]) {
		return errors.New("native effective hooks differ from selected settings; refresh unobserved")
	}
	rows := producer.Get("rows")
	if rows == nil || rows.Kind != value.Array || len(rows.Items) != 11 {
		return errors.New("unsupported Claude hook row coverage")
	}
	var rendered map[string][]map[string]json.RawMessage
	if err := hookDecode(settings["hooks"], &rendered); err != nil {
		return err
	}
	expectedRows := map[string]int{"memory:SessionStart": 13, "memory:UserPromptSubmit": 13, "memory:PreCompact": 150, "memory:SessionEnd": 150, "memory:PostToolUse": 13, "memory:PostToolUseFailure": 13, "coordination:SessionStart": 15, "coordination:UserPromptSubmit": 15, "coordination:Stop": 15, "coordination:SessionEnd": 2, "coordination:PostToolUse": 2}
	wanted := map[string]bool{}
	for _, row := range rows.Items {
		role, event := nativeText(row.Get("role")), nativeText(row.Get("event"))
		key := role + ":" + event
		if wanted[key] || expectedRows[key] == 0 || !exactReceiptObject(row.Get("hook"), "type", "command", "timeout") || !value.SameText(row.Get("hook").Get("type"), "command") || row.Get("hook").Get("timeout").Kind != value.Int || row.Get("hook").Get("timeout").Integer != strconv.Itoa(expectedRows[key]) {
			return errors.New("unsupported or duplicate selected native hook row")
		}
		wanted[key] = true
		command := nativeText(row.Get("hook").Get("command"))
		matched := 0
		for _, listing := range observed.Listing {
			if listing.Event == event && listing.Command == command && listing.Source == "flagSettings" {
				matched++
			}
		}
		if matched != 1 {
			return errors.New("native hook listing is missing, duplicated or in another layer")
		}
		if err := r.claudeHookRow(selected, row, observed.Executions); err != nil {
			return err
		}
	}
	if len(observed.Listing) != len(wanted) {
		return errors.New("extra native hook listing entry")
	}
	// This finite seam supports append-only hook production, not unobserved
	// replacement of arbitrary existing hooks.
	count := 0
	for event, groups := range rendered {
		for _, group := range groups {
			if len(group) != 1 {
				return errors.New("unsupported selected hook group/matcher")
			}
			var hooks []json.RawMessage
			if err := hookDecode(group["hooks"], &hooks); err != nil {
				return err
			}
			for _, hook := range hooks {
				var candidate map[string]json.RawMessage
				if err := hookDecode(hook, &candidate); err != nil {
					return err
				}
				matches := 0
				for _, row := range rows.Items {
					decoded, err := value.DecodeTextWithObjectPolicy(hook, "utf-8", 0, value.DuplicateReject)
					if err != nil {
						return err
					}
					if nativeText(row.Get("event")) == event && hookValuesEqual(decoded, row.Get("hook")) {
						matches++
					}
				}
				if matches != 1 {
					return errors.New("unknown/extra selected native hook")
				}
				count++
			}
		}
	}
	if count != len(rows.Items) {
		return errors.New("selected hook membership differs from emitted rows")
	}
	if len(observed.Executions) != 2 {
		return errors.New("native prompt hook execution coverage is unknown")
	}
	return nil
}

func (r *watchCensusRead) claudeHookRow(selected WatchCensusClaudeHooks, row *value.Value, executions []WatchCensusHookExecution) error {
	role, event := nativeText(row.Get("role")), nativeText(row.Get("event"))
	if role != "memory" && role != "coordination" {
		return errors.New("unsupported native hook role")
	}
	if row.Get("argv") == nil || row.Get("argv").Kind != value.Array {
		return errors.New("missing native hook argv")
	}
	var argv []string
	for _, part := range row.Get("argv").Items {
		if part.Kind != value.String {
			return errors.New("invalid native hook argv")
		}
		argv = append(argv, nativeText(part))
	}
	if len(argv) == 0 || !censusWithin(r.catalog.Root, argv[0]) {
		return errors.New("native hook launcher outside selected universe")
	}
	if role == "memory" && len(argv) != 1 || role == "coordination" && (len(argv) != 4 || argv[1] != "hook" || argv[2] != "--config") {
		return errors.New("unsupported native hook tail argv")
	}
	joined, err := shlexJoinStrings(argv)
	if err != nil || joined != nativeText(row.Get("hook").Get("command")) {
		return errors.New("native hook command and literal argv disagree")
	}
	launcherRaw, digest, err := compatibilityOpened(argv[0], 65536, uint32(os.Getuid()))
	if err != nil {
		return err
	}
	if _, err := r.pin(WatchCensusPin{argv[0], digest}, false); err != nil {
		return err
	}
	const prefix = "#!/bin/sh\nexec "
	if !bytes.HasPrefix(launcherRaw, []byte(prefix)) {
		return errors.New("unsupported native hook launcher shell")
	}
	vector, ok := shlexSplitPosix(strings.TrimSuffix(string(launcherRaw[len(prefix):]), "\n"))
	if !ok || len(vector) < 4 || vector[len(vector)-2] != "--" || vector[len(vector)-1] != "$@" {
		return errors.New("unsupported native hook launcher frame")
	}
	vector = vector[:len(vector)-2]
	canonical, err := shlexJoinStrings(vector)
	if err != nil || string(launcherRaw) != prefix+canonical+" -- \"$@\"\n" {
		return errors.New("native launcher is not the canonical emitted shell frame")
	}
	if filepath.Base(filepath.Dir(argv[0])) != digest || filepath.Base(argv[0]) != "inbox" {
		return errors.New("native launcher content address disagrees")
	}

	flagStart := 2
	if len(vector) > 4 && vector[1] == "-I" && vector[2] == "-S" && vector[3] == "-c" {
		flagStart = 5
	}
	flags := []string{"--receipt", "--receipt-sha256", "--role", "--entrypoint", "--config", "--hook-timeout", "--launch-root", "--shell"}
	if len(vector) != flagStart+16 {
		return errors.New("unsupported native hook literal program argv")
	}
	for i, flag := range flags {
		if vector[flagStart+2*i] != flag {
			return errors.New("native hook selection flag membership changed")
		}
	}
	vals := vector[flagStart:]
	profile := selected.Config
	if role == "memory" {
		profile = r.nodes[selected.Config.Path].Memory
	}
	timeout, err := strconv.Atoi(vals[11])
	hook := row.Get("hook")
	if err != nil || hook.Get("timeout") == nil || hook.Get("timeout").Kind != value.Int || hook.Get("timeout").Integer != strconv.Itoa(timeout) || vals[5] != role || vals[15] != "/bin/sh" {
		return errors.New("native hook role/timeout/shell disagrees")
	}
	if role == "memory" && vals[9] != profile.Path {
		return errors.New("native hook memory config disagrees")
	}
	if !censusWithin(r.catalog.Root, vals[9]) {
		return errors.New("native hook config outside universe")
	}
	if _, err := r.pin(WatchCensusPin{vals[9], profile.SHA256}, false); err != nil {
		return err
	}
	if role == "coordination" && argv[3] != vals[9] {
		return errors.New("native coordination hook tail config disagrees")
	}
	if flagStart == 2 && !value.SameText(row.Get("implementation"), "go") || flagStart == 5 && !value.SameText(row.Get("implementation"), "python") {
		return errors.New("native hook implementation label disagrees with literal program")
	}
	if flagStart == 2 && (vector[0] == "" || vector[1] != role) {
		return errors.New("native hook program operation disagrees")
	}
	compat := &CompatibilityContext{Role: role, Entrypoint: vals[7], ConfigPath: vals[9], HookTimeout: float64(timeout), LaunchRoot: vals[13], Shell: vals[15], ReceiptPath: vals[1], ReceiptSHA256: vals[3], Profile: r.cli.profile()}
	if _, err := r.pin(WatchCensusPin{vals[1], vals[3]}, false); err != nil {
		return err
	}
	if err := compat.VerifyReceipt(r.cli.profile()); err != nil {
		return err
	}
	if nativeText(compat.Receipt.Get("binding").Get("path")) != r.nodes[selected.Config.Path].Binding.Path || nativeText(compat.Receipt.Get("binding").Get("sha256")) != r.nodes[selected.Config.Path].Binding.SHA256 {
		return errors.New("native hook receipt selects another binding")
	}
	for _, desc := range append([]*value.Value{compat.Receipt.Get("memory_config")}, compat.Receipt.Get("coordination_configs").Items...) {
		if _, _, err := r.descriptor(desc); err != nil {
			return err
		}
	}
	if flagStart == 2 && vector[0] != nativeText(compat.Receipt.Get("binary").Get("path")) {
		return errors.New("native hook program differs from receipt image")
	}
	for _, member := range compat.Receipt.Get("members").Members {
		path := filepath.Join(nativeText(compat.Receipt.Get("snapshot_root")), string(member.Key))
		if !censusWithin(r.catalog.Root, path) {
			return errors.New("external native receipt source")
		}
		if _, err := r.pin(WatchCensusPin{path, nativeText(member.Value.Get("sha256"))}, false); err != nil {
			return err
		}
	}

	if event == "UserPromptSubmit" {
		matched := 0
		for _, execution := range executions {
			if execution.Role != role || execution.Event != event {
				continue
			}
			matched++
			expected := append(slices.Clone(vector), argv[1:]...)
			if execution.PID <= 1 || execution.Exit != 0 || execution.Launcher != (WatchCensusPin{argv[0], digest}) || execution.Program.Path != vector[0] || !slices.Equal(execution.Argv, expected) {
				return errors.New("native hook observed program/literal argv/exit disagrees")
			}
			if err := r.hookProgram(execution.Program); err != nil {
				return err
			}
		}
		if matched != 1 {
			return errors.New("native prompt hook execution/readback is missing")
		}
	}
	return nil
}

func (r *watchCensusRead) requireHookSession(config, path, workspace string) error {
	node := r.nodes[config]
	if len(r.directories[node.Account]) == 0 && len(r.directories[workspace]) == 0 {
		return nil
	}
	for _, selected := range r.hookSelections {
		if selected.Config.Path == config && selected.Session.Path == path && selected.Workspace == workspace {
			return nil
		}
	}
	return errors.New("nonempty native layer lacks this unit member/session readback")
}
