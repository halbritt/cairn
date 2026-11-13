package coordination

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Agy consumes account hooks.json and project .agents/hooks.json cumulatively.
// A layer list selects finite observations; it is not a native settings API.
type WatchCensusAgyHooks struct {
	Schema, Harness           string
	Account, Workspace        string
	Config, Session, Readback WatchCensusPin
	Layers                    []WatchCensusAgyLayer
	LayerFiles                []WatchCensusPin
}

type WatchCensusAgyLayer struct {
	Layer                           string
	Generation, Settings, Installed WatchCensusPin
}

type WatchCensusAgyReadback struct {
	Schema, Harness, EvidenceKind, NativeSession string
	NativePID                                    int
	Client, Trace                                WatchCensusPin
	ClientArgv                                   []string
	Start, End                                   int
}

const agyNativeUnknown = "Agy native session/file-read/exec provenance is unobserved; code preparation is not native qualification"

type agyCensusCommand struct {
	event             string
	launcher, program WatchCensusPin
	argv              []string
}

func (r *watchCensusRead) inspectAgyHooks(ctx context.Context, selected WatchCensusAgyHooks) ([]WatchCensusHookObservation, error) {
	node := r.nodes[selected.Config.Path]
	if node == nil || node.Config != selected.Config || node.Account != selected.Account {
		return nil, errors.New("Agy selection names an unknown or mixed unit/config")
	}
	config, err := r.jsonFile(selected.Config.Path)
	if err != nil {
		return nil, err
	}
	if !value.SameText(config.Get("harness"), "agy") {
		return nil, errors.New("Agy selection disagrees with profile harness")
	}
	if err := r.agyLayerInventory(ctx, selected); err != nil {
		return nil, err
	}
	if len(selected.Layers) != 2 || selected.Layers[0].Layer != "account" || selected.Layers[1].Layer != "project" {
		return nil, errors.New("Agy requires exhaustive ordered account/project layer selections")
	}
	installed := map[string][]byte{}
	var commands []agyCensusCommand
	var observations []WatchCensusHookObservation
	for _, layer := range selected.Layers {
		path := filepath.Join(selected.Account, "hooks.json")
		if layer.Layer == "project" {
			path = filepath.Join(selected.Workspace, ".agents", "hooks.json")
		}
		if layer.Installed.Path != path {
			return nil, errors.New("Agy hook path is in another layer")
		}
		if layer.Installed.SHA256 == "" {
			if layer.Generation != (WatchCensusPin{}) || layer.Settings != (WatchCensusPin{}) {
				return nil, errors.New("absent Agy layer carries mixed generation/settings")
			}
			if _, err := os.Lstat(path); !os.IsNotExist(err) {
				return nil, errors.New("declared absent Agy layer is present or unreadable")
			}
			installed[path] = nil
			continue
		}
		rows, raw, err := r.agyProducedLayer(ctx, selected, layer)
		if err != nil {
			return nil, err
		}
		commands = append(commands, rows...) // Identical commands in two layers remain two commands.
		installed[path] = raw
		observations = append(observations, WatchCensusHookObservation{Config: selected.Config, Session: selected.Session,
			Generation: layer.Generation, Settings: layer.Settings, Layer: layer.Layer,
			Observation: "Agy cumulative layer: pinned producer and installed bytes; native provenance required; no per-PID layer attribution"})
	}
	if len(commands) == 0 {
		return nil, errors.New("Agy selected hook coverage is empty")
	}
	if !censusWithin(r.catalog.Root, selected.Readback.Path) {
		return nil, errors.New("external Agy readback")
	}
	raw, err := r.pin(selected.Readback, false)
	if err != nil {
		return nil, err
	}
	var readback WatchCensusAgyReadback
	if err := hookDecode(raw, &readback); err != nil {
		return nil, err
	}
	if readback.Schema != "cairn.watch-agy-readback/1" || readback.Harness != "agy" {
		return nil, errors.New("unsupported Agy readback tag")
	}
	if readback.EvidenceKind == "code-preparation" {
		if selected.Session != (WatchCensusPin{}) || readback.NativeSession != "" || readback.NativePID != 0 ||
			readback.Client != (WatchCensusPin{}) || readback.Trace != (WatchCensusPin{}) ||
			len(readback.ClientArgv) != 0 || readback.Start != 0 || readback.End != 0 {
			return nil, errors.New("Agy preparation input contains invented native evidence")
		}
		return nil, errors.New(agyNativeUnknown)
	}
	if readback.EvidenceKind != "retained-native-strace" || readback.NativePID <= 1 ||
		!slices.Contains(r.catalog.Sessions, selected.Session) || !censusWithin(r.catalog.Root, selected.Session.Path) {
		return nil, errors.New("missing or unsupported Agy native provenance/session")
	}
	if _, err := r.pin(selected.Session, false); err != nil {
		return nil, err
	}
	state, err := r.jsonFile(selected.Session.Path)
	if err != nil {
		return nil, err
	}
	owner := r.sessionConfig(selected.Session.Path, state)
	native, identityErr := watchStateNativeSession(state)
	if identityErr != nil || native != readback.NativeSession || !value.SameText(state.Get("schema"), NativeSessionSchema) ||
		owner == nil || r.configPaths[owner] != selected.Config.Path || nativeText(state.Get("agent").Get("metadata").Get("workspace")) != selected.Workspace {
		return nil, errors.New("Agy native session/workspace/config identity disagrees")
	}
	if !censusWithin(r.catalog.Root, readback.Client.Path) {
		return nil, errors.New("external Agy native image")
	}
	if _, err := r.pin(readback.Client, true); err != nil {
		return nil, err
	}
	trace, err := r.agyTrace(readback.Trace)
	if err != nil {
		return nil, err
	}
	if err := agyTraceReadback(trace, readback, selected.Workspace, installed, commands); err != nil {
		return nil, err
	}
	// A retained interval does not observe current activation, refresh or D1-D7.
	// The public inspector still executes its normal closure/final reread.
	r.block("retained Agy native provenance does not qualify the current image/session or native refresh")
	return observations, nil
}

func (r *watchCensusRead) agyLayerInventory(ctx context.Context, selected WatchCensusAgyHooks) error {
	if selected.Account == selected.Workspace || censusOverlap(selected.Account, selected.Workspace) {
		return errors.New("overlapping Agy account/project inventories")
	}
	wanted, err := censusPinMap(selected.LayerFiles)
	if err != nil {
		return err
	}
	seen := map[string]bool{}
	var walk func(string) error
	walk = func(path string) error {
		names, err := r.directory(path)
		if err != nil {
			return err
		}
		for _, name := range names {
			if err := ctx.Err(); err != nil {
				return err
			}
			child := filepath.Join(path, name)
			info, err := os.Lstat(child)
			if err != nil {
				return err
			}
			if info.IsDir() {
				if err := walk(child); err != nil {
					return err
				}
				continue
			}
			if seen[child] || wanted[child] == "" {
				return errors.New("unknown or omitted Agy account/project layer file")
			}
			if _, err := r.pin(WatchCensusPin{child, wanted[child]}, false); err != nil {
				return err
			}
			seen[child] = true
		}
		return nil
	}
	for parent := filepath.Dir(selected.Workspace); censusWithin(r.catalog.Root, parent) || parent == r.catalog.Root; parent = filepath.Dir(parent) {
		if _, err := os.Lstat(filepath.Join(parent, ".agents")); !os.IsNotExist(err) {
			return errors.New("unknown ancestor Agy project layer")
		}
		if parent == r.catalog.Root {
			break
		}
	}
	for _, directory := range []string{selected.Account, selected.Workspace} {
		if err := walk(directory); err != nil {
			return err
		}
	}
	if len(seen) != len(wanted) {
		return errors.New("extra Agy pin outside account/project inventory")
	}
	return nil
}

func (r *watchCensusRead) agyGeneration(ctx context.Context, pin WatchCensusPin) (map[string]WatchCensusPin, error) {
	if !censusWithin(r.catalog.Root, pin.Path) {
		return nil, errors.New("external Agy generation")
	}
	if _, err := r.pin(pin, false); err != nil {
		return nil, err
	}
	files, err := install.ReadGeneration(ctx, install.Generation{Path: pin.Path, SHA256: pin.SHA256})
	if err != nil {
		return nil, err
	}
	members := map[string]WatchCensusPin{}
	for _, file := range files {
		if !censusWithin(r.catalog.Root, file.Path) {
			return nil, errors.New("external Agy generation member")
		}
		member := WatchCensusPin{file.Path, file.SHA256}
		if _, err := r.pin(member, file.Executable); err != nil {
			return nil, err
		}
		members[file.Name] = member
	}
	return members, nil
}

func (r *watchCensusRead) agyProducedLayer(ctx context.Context, selected WatchCensusAgyHooks, layer WatchCensusAgyLayer) ([]agyCensusCommand, []byte, error) {
	members, err := r.agyGeneration(ctx, layer.Generation)
	if err != nil {
		return nil, nil, err
	}
	if len(members) != 3 || members["hook-settings.json"] != layer.Settings || members["previous-settings.json"].Path == "" || members["hook-selection.json"].Path == "" {
		return nil, nil, errors.New("Agy hook generation membership disagrees")
	}
	producer, err := r.jsonFile(members["hook-selection.json"].Path)
	if err != nil {
		return nil, nil, err
	}
	if !value.SameText(producer.Get("schema"), "cairn.lifecycle-hook-settings/1") || !value.SameText(producer.Get("harness"), "agy") ||
		nativeText(producer.Get("account_home")) != selected.Account || !exactReceiptObject(producer.Get("layout"), "hook-settings.json") ||
		!value.SameText(producer.Get("layout").Get("hook-settings.json"), "hooks.json") ||
		!hookValuesEqual(producer.Get("unsupported_operations"), &value.Value{Kind: value.Array, Items: []*value.Value{value.Text("ordinary memory hook")}}) {
		return nil, nil, errors.New("unsupported Agy producer harness/account/layout/operations")
	}
	for _, field := range []string{"native_trust", "refresh", "activation"} {
		if !value.SameText(producer.Get(field), "not-performed") {
			return nil, nil, errors.New("Agy producer cannot assert native activation/refresh/trust")
		}
	}
	life := producer.Get("lifecycle_selection")
	lifeMembers, err := r.agyGeneration(ctx, WatchCensusPin{nativeText(life.Get("path")), nativeText(life.Get("sha256"))})
	if err != nil {
		return nil, nil, err
	}
	node := r.nodes[selected.Config.Path]
	if lifeMembers["binding.json"] != node.Binding || lifeMembers["receipt.json"].Path == "" {
		return nil, nil, errors.New("Agy producer selects another binding/receipt")
	}
	receiptPin := lifeMembers["receipt.json"]
	if !slices.Contains(r.catalog.UnboundReceipts, receiptPin) {
		return nil, nil, errors.New("Agy producer receipt is not the selected unbound association")
	}
	receipt, err := r.jsonFile(receiptPin.Path)
	if err != nil {
		return nil, nil, err
	}
	metadata, err := r.jsonFile(lifeMembers["selection.json"].Path)
	if err != nil {
		return nil, nil, err
	}
	anchors := metadata.Get("anchors")
	if anchors == nil || anchors.Kind != value.Object {
		return nil, nil, errors.New("Agy emitted launcher anchors are missing")
	}
	for _, role := range []string{"memory", "coordination"} {
		_, pin, err := r.descriptor(producer.Get("role_configurations").Get(role))
		if err != nil {
			return nil, nil, err
		}
		expected := node.Config
		if role == "memory" {
			expected = node.Memory
		}
		if pin.SHA256 != expected.SHA256 || role == "memory" && pin != expected {
			return nil, nil, errors.New("Agy mixed role profile")
		}
		if role == "coordination" && producerProfile(receipt, pin.Path, pin.SHA256) == nil {
			return nil, nil, errors.New("Agy producer role profile is outside its receipt")
		}
	}
	settings, err := r.pin(layer.Settings, false)
	if err != nil {
		return nil, nil, err
	}
	actual, err := r.pin(layer.Installed, false)
	if err != nil {
		return nil, nil, err
	}
	if !bytes.Equal(settings, actual) {
		return nil, nil, errors.New("Agy installed layer differs from selected settings")
	}
	previous, err := r.pin(members["previous-settings.json"], false)
	if err != nil {
		return nil, nil, err
	}
	source := producer.Get("source_settings")
	if nativeText(source.Get("sha256")) != members["previous-settings.json"].SHA256 {
		return nil, nil, errors.New("Agy previous/source settings pin disagrees")
	}
	if _, _, err := r.descriptor(source); err != nil {
		return nil, nil, err
	}
	var rendered, old map[string]json.RawMessage
	if err := hookDecode(settings, &rendered); err != nil {
		return nil, nil, err
	}
	if err := hookDecode(previous, &old); err != nil {
		return nil, nil, err
	}
	for key, raw := range old {
		if key != "cairn-coordination" && !hookJSONEqual(raw, rendered[key]) {
			return nil, nil, errors.New("Agy producer changed unrelated settings")
		}
	}
	for key := range rendered {
		if key != "cairn-coordination" && old[key] == nil {
			return nil, nil, errors.New("Agy producer added unrelated settings")
		}
	}
	rows := producer.Get("rows")
	if rows == nil || rows.Kind != value.Array || len(rows.Items) != 2 {
		return nil, nil, errors.New("unsupported Agy coordination event coverage")
	}
	var groups map[string][]json.RawMessage
	if err := hookDecode(rendered["cairn-coordination"], &groups); err != nil {
		return nil, nil, err
	}
	if len(groups) != 2 {
		return nil, nil, errors.New("extra or missing Agy native hook event")
	}
	var commands []agyCensusCommand
	for i, event := range []string{"PreInvocation", "Stop"} {
		row := rows.Items[i]
		if !value.SameText(row.Get("role"), "coordination") || !value.SameText(row.Get("event"), event) || !value.SameText(row.Get("implementation"), "go") ||
			!exactReceiptObject(row.Get("hook"), "type", "command", "timeout") || !value.SameText(row.Get("hook").Get("type"), "command") ||
			row.Get("hook").Get("timeout") == nil || row.Get("hook").Get("timeout").Kind != value.Int || row.Get("hook").Get("timeout").Integer != "15" ||
			len(groups[event]) != 1 {
			return nil, nil, errors.New("unsupported or duplicate Agy native hook row")
		}
		parsed, err := value.DecodeTextWithObjectPolicy(groups[event][0], "utf-8", 0, value.DuplicateReject)
		if err != nil || !hookValuesEqual(parsed, row.Get("hook")) {
			return nil, nil, errors.New("Agy rendered hook differs from producer row")
		}
		command, err := r.agyCommand(selected, row, receipt, receiptPin, anchors)
		if err != nil {
			return nil, nil, err
		}
		commands = append(commands, command)
	}
	return commands, settings, nil
}

func (r *watchCensusRead) agyCommand(selected WatchCensusAgyHooks, row, receipt *value.Value, receiptPin WatchCensusPin, anchors *value.Value) (agyCensusCommand, error) {
	var result agyCensusCommand
	argv, err := hookStringVector(row.Get("argv"))
	if err != nil {
		return result, err
	}
	event := nativeText(row.Get("event"))
	checked, err := hookStringVector(row.Get("checked_launcher"))
	if err != nil || len(checked) != 1 || len(argv) != 6 || argv[0] != checked[0] || argv[1] != "hook" || argv[2] != "--config" || argv[4] != "--event" || argv[5] != event {
		return result, errors.New("Agy requires literal six-element coordination event argv")
	}
	profile := producerProfile(receipt, argv[3], selected.Config.SHA256)
	if profile == nil {
		return result, errors.New("Agy hook selects another immutable coordination profile")
	}
	if _, _, err := r.descriptor(profile); err != nil {
		return result, err
	}
	joined, err := shlexJoinStrings(argv)
	if err != nil || joined != nativeText(row.Get("hook").Get("command")) {
		return result, errors.New("Agy command and literal argv disagree")
	}
	if !censusWithin(r.catalog.Root, argv[0]) {
		return result, errors.New("external Agy launcher")
	}
	launcher, sha, err := compatibilityOpened(argv[0], 65536, uint32(os.Getuid()))
	if err != nil {
		return result, err
	}
	launcherPin := WatchCensusPin{argv[0], sha}
	if _, err := r.pin(launcherPin, true); err != nil {
		return result, err
	}
	const prefix = "#!/bin/sh\nexec "
	if !bytes.HasPrefix(launcher, []byte(prefix)) {
		return result, errors.New("unsupported Agy launcher shell")
	}
	vector, ok := shlexSplitPosix(strings.TrimSuffix(string(launcher[len(prefix):]), "\n"))
	if !ok || len(vector) != 20 || vector[18] != "--" || vector[19] != "$@" {
		return result, errors.New("unsupported Agy launcher frame")
	}
	vector = vector[:18]
	canonical, err := shlexJoinStrings(vector)
	if err != nil || string(launcher) != prefix+canonical+" -- \"$@\"\n" || filepath.Base(filepath.Dir(argv[0])) != sha || filepath.Base(argv[0]) != "inbox" {
		return result, errors.New("Agy launcher is not the emitted content-addressed frame")
	}
	flags := []string{"--receipt", "--receipt-sha256", "--role", "--entrypoint", "--config", "--hook-timeout", "--launch-root", "--shell"}
	for i, flag := range flags {
		if vector[2+2*i] != flag {
			return result, errors.New("Agy launcher flag membership changed")
		}
	}
	program := WatchCensusPin{nativeText(receipt.Get("binary").Get("path")), nativeText(receipt.Get("binary").Get("sha256"))}
	if vector[0] != program.Path || vector[1] != "coordination" || vector[3] != receiptPin.Path || vector[5] != receiptPin.SHA256 ||
		vector[7] != "coordination" || (vector[9] != "coordination.py" && nativeText(receipt.Get("aliases").Get(vector[9])) != "coordination.py") || vector[11] != argv[3] || vector[13] != "15" || vector[17] != "/bin/sh" {
		return result, errors.New("Agy launcher program/receipt/role/config/timeout disagrees")
	}
	if _, err := r.pin(program, true); err != nil {
		return result, err
	}
	matched := 0
	for _, anchor := range anchors.Members {
		av, err := hookStringVector(anchor.Value.Get("vector"))
		if err != nil {
			return result, err
		}
		if len(av) != 21 || !slices.Equal(av[5:], vector[2:]) {
			continue
		}
		retainedRaw, err := LauncherBytes(av, "/bin/sh")
		if err != nil {
			return result, err
		}
		retained, err := hookStringVector(row.Get("retained_launcher"))
		path := LauncherPath(vector[15], retainedRaw)
		if err != nil || len(retained) != 1 || retained[0] != path || !censusWithin(r.catalog.Root, path) {
			return result, errors.New("Agy retained launcher differs from emitted anchor")
		}
		actual, err := r.pin(WatchCensusPin{path, fmt.Sprintf("%x", sha256.Sum256(retainedRaw))}, false)
		if err != nil {
			return result, err
		}
		if !bytes.Equal(actual, retainedRaw) {
			return result, errors.New("Agy retained launcher changed")
		}
		matched++
	}
	if matched != 1 {
		return result, errors.New("Agy launcher lacks one exact emitted anchor")
	}
	return agyCensusCommand{event: event, launcher: launcherPin, program: program, argv: append(append(slices.Clone(vector), "--"), argv[1:]...)}, nil
}

func producerProfile(receipt *value.Value, path, digest string) *value.Value {
	profiles := receipt.Get("coordination_configs")
	if profiles == nil || profiles.Kind != value.Array {
		return nil
	}
	var found *value.Value
	for _, profile := range profiles.Items {
		if nativeText(profile.Get("path")) == path && nativeText(profile.Get("sha256")) == digest {
			if found != nil {
				return nil
			}
			found = profile
		}
	}
	return found
}

// Raw traces can exceed the ordinary 1 MiB JSON limit. They use the same path,
// inode and credential exclusion checks, with a separate bounded fact reread
// by the second inspectHookSelections call after the final census readback.
func (r *watchCensusRead) agyTrace(pin WatchCensusPin) ([]byte, error) {
	if !censusWithin(r.catalog.Root, pin.Path) || !censusDigest(pin.SHA256) || r.local.tokenPaths[pin.Path] {
		return nil, errors.New("unsafe Agy trace pin")
	}
	if err := r.safePath(pin.Path, false); err != nil {
		return nil, err
	}
	raw, digest, err := compatibilityOpened(pin.Path, 256<<20, uint32(os.Getuid()))
	if err != nil {
		return nil, err
	}
	if digest != pin.SHA256 {
		return nil, errors.New("Agy trace pin changed")
	}
	key := "agy-trace:" + pin.Path
	if old := r.report.Facts[key]; old != "" && old != digest {
		return nil, errors.New("Agy trace changed during final readback")
	}
	r.report.Facts[key] = digest
	return raw, nil
}

type agyTraceCall struct {
	pid, start int
	name, body string
}

var agyTraceLine = regexp.MustCompile(`^(\d+)\s+(.*)$`)
var agyTraceResume = regexp.MustCompile(`^<\.\.\. (\w+) resumed>(.*)$`)
var agyTraceName = regexp.MustCompile(`^(\w+)\(`)
var agyTraceString = regexp.MustCompile(`"(?:\\.|[^"\\])*"`)

// Only the documented strace -f -xx dialect is accepted. Unit codec inputs
// are synthetic bytes; successful decoding never makes them native evidence.
func agyTraceCalls(raw []byte) ([]agyTraceCall, error) {
	pending := map[int]agyTraceCall{}
	var out []agyTraceCall
	position := 0
	for _, line := range bytes.SplitAfter(raw, []byte{'\n'}) {
		start := position
		position += len(line)
		if len(line) == 0 {
			continue
		}
		if line[len(line)-1] != '\n' {
			return nil, errors.New("partial Agy trace line")
		}
		parts := agyTraceLine.FindStringSubmatch(strings.TrimSuffix(string(line), "\n"))
		if parts == nil {
			return nil, errors.New("unsupported Agy trace prefix")
		}
		pid, err := strconv.Atoi(parts[1])
		if err != nil || pid <= 1 {
			return nil, errors.New("invalid Agy trace PID")
		}
		body := parts[2]
		if resumed := agyTraceResume.FindStringSubmatch(body); resumed != nil {
			old, ok := pending[pid]
			if !ok || old.name != resumed[1] {
				return nil, errors.New("Agy resumed syscall lacks its original prefix")
			}
			delete(pending, pid)
			body = old.body + resumed[2]
			start = old.start
		}
		name := agyTraceName.FindStringSubmatch(body)
		if name == nil {
			if strings.HasPrefix(body, "+++ exited with ") || strings.HasPrefix(body, "+++ killed by ") {
				out = append(out, agyTraceCall{pid, start, "exit", body})
			}
			continue
		}
		call := agyTraceCall{pid, start, name[1], body}
		if strings.HasSuffix(body, "<unfinished ...>") {
			if _, ok := pending[pid]; ok {
				return nil, errors.New("overlapping unfinished Agy syscall")
			}
			call.body = strings.TrimSuffix(body, "<unfinished ...>")
			pending[pid] = call
			continue
		}
		out = append(out, call)
	}
	if len(pending) != 0 {
		return nil, errors.New("incomplete Agy trace syscall coverage")
	}
	return out, nil
}

func agyQuoted(raw string) (string, error) {
	if !strings.HasPrefix(raw, `"`) || !strings.HasSuffix(raw, `"`) {
		return "", errors.New("missing Agy strace string")
	}
	return strconv.Unquote(raw)
}

func agyExec(call agyTraceCall) (string, []string, error) {
	end := strings.LastIndex(call.body, "]")
	if end < 0 || !strings.HasSuffix(call.body, " = 0") || strings.Contains(call.body[:end], "...") {
		return "", nil, errors.New("truncated or failed Agy execve")
	}
	stringsIn := agyTraceString.FindAllString(call.body[:end], -1)
	if len(stringsIn) < 2 {
		return "", nil, errors.New("missing Agy execve literal argv")
	}
	var parts []string
	for _, part := range stringsIn {
		decoded, err := agyQuoted(part)
		if err != nil {
			return "", nil, err
		}
		parts = append(parts, decoded)
	}
	return parts[0], parts[1:], nil
}

func agyTraceReadback(raw []byte, observed WatchCensusAgyReadback, workspace string, installed map[string][]byte, commands []agyCensusCommand) error {
	if observed.Start < 0 || observed.End <= observed.Start || observed.End > len(raw) ||
		observed.Start > 0 && raw[observed.Start-1] != '\n' || raw[observed.End-1] != '\n' {
		return errors.New("invalid Agy trace interval")
	}
	calls, err := agyTraceCalls(raw[:observed.End])
	if err != nil {
		return err
	}
	threads := map[int]bool{observed.NativePID: true}
	for changed := true; changed; {
		changed = false
		for _, call := range calls {
			if (call.name == "clone" || call.name == "clone3") && threads[call.pid] && strings.Contains(call.body, "CLONE_THREAD") {
				end := strings.LastIndex(call.body, " = ")
				if end < 0 {
					return errors.New("unknown Agy native thread result")
				}
				child, err := strconv.Atoi(call.body[end+3:])
				if err != nil || child <= 1 {
					return errors.New("unknown Agy native thread")
				}
				if !threads[child] {
					threads[child] = true
					changed = true
				}
			}
		}
	}
	clientSeen := false
	exits := map[int]int{}
	for _, call := range calls {
		if call.name == "execve" && call.pid == observed.NativePID {
			path, argv, err := agyExec(call)
			if err == nil && path == observed.Client.Path && slices.Equal(argv, observed.ClientArgv) {
				clientSeen = true
			}
		}
		if call.name == "exit" && call.body == "+++ exited with 0 +++" {
			exits[call.pid] = call.start
		}
	}
	if !clientSeen || len(observed.ClientArgv) == 0 || observed.ClientArgv[0] != observed.Client.Path {
		return errors.New("Agy native client exec identity is missing")
	}
	if err := agyTraceFiles(calls, threads, installed); err != nil {
		return err
	}
	writes := map[string]int{}
	for _, call := range calls {
		if call.start < observed.Start || !threads[call.pid] || call.name != "write" {
			continue
		}
		body, _, err := agyTraceIO(call)
		if err != nil {
			return err
		}
		if !bytes.Contains(body, []byte("conversationId")) || !bytes.Contains(body, []byte("hookEventName")) {
			continue
		}
		var payload map[string]json.RawMessage
		if err := hookDecode(body, &payload); err != nil {
			return err
		}
		var native, event string
		var paths []string
		if json.Unmarshal(payload["conversationId"], &native) != nil || json.Unmarshal(payload["hookEventName"], &event) != nil || json.Unmarshal(payload["workspacePaths"], &paths) != nil ||
			native != observed.NativeSession || !slices.Equal(paths, []string{workspace}) || event != "PreInvocation" && event != "Stop" {
			return errors.New("Agy native event/session/workspace provenance disagrees")
		}
		writes[event]++
	}
	wanted := map[string]int{}
	for _, command := range commands {
		wanted[command.event]++
	}
	if len(writes) != len(wanted) {
		return errors.New("missing or extra Agy native event frames")
	}
	for event, count := range wanted {
		if writes[event] != count {
			return errors.New("Agy cumulative native event multiplicity disagrees")
		}
	}
	matched := make([]bool, len(commands))
	pids := map[int]bool{}
	for _, call := range calls {
		if call.name != "execve" || call.start < observed.Start {
			continue
		}
		path, argv, err := agyExec(call)
		if err != nil {
			path, _ := agyQuoted(agyTraceString.FindString(call.body))
			for _, command := range commands {
				if path == command.program.Path {
					return err
				}
			}
			continue
		}
		selectedProgram := false
		for _, command := range commands {
			selectedProgram = selectedProgram || path == command.program.Path
		}
		if !selectedProgram && (len(argv) < 2 || argv[1] != "coordination") {
			continue
		}
		index := -1
		for i, command := range commands {
			if !matched[i] && path == command.program.Path && slices.Equal(argv, command.argv) {
				index = i
				break
			}
		}
		if index < 0 || pids[call.pid] || exits[call.pid] <= call.start {
			return errors.New("extra, stale, failed or unmatched Agy literal callback execution")
		}
		matched[index] = true
		pids[call.pid] = true
	}
	for _, ok := range matched {
		if !ok {
			return errors.New("missing Agy literal callback execution")
		}
	}
	return nil
}

func agyTraceIO(call agyTraceCall) ([]byte, int, error) {
	quoted := agyTraceString.FindString(call.body)
	text, err := agyQuoted(quoted)
	end := strings.LastIndex(call.body, " = ")
	if err != nil || end < 0 {
		return nil, 0, errors.New("unknown Agy trace I/O")
	}
	count, err := strconv.Atoi(call.body[end+3:])
	if err != nil || count < 0 || count != len(text) || strings.Contains(call.body[:end], `"...`) {
		return nil, 0, errors.New("partial or truncated Agy trace I/O")
	}
	fdEnd := strings.Index(call.body, ",")
	if fdEnd < 0 {
		return nil, 0, errors.New("missing Agy trace fd")
	}
	fd, err := strconv.Atoi(call.body[len(call.name)+1 : fdEnd])
	if err != nil || fd < 0 {
		return nil, 0, errors.New("invalid Agy trace fd")
	}
	return []byte(text), fd, nil
}

func agyTraceFiles(calls []agyTraceCall, threads map[int]bool, installed map[string][]byte) error {
	type read struct {
		path string
		raw  []byte
		eof  bool
	}
	opened := map[string]*read{}
	seen := map[string]int{}
	for _, call := range calls {
		if !threads[call.pid] {
			continue
		}
		if call.name == "openat" {
			quoted := agyTraceString.FindString(call.body)
			path, err := agyQuoted(quoted)
			if err != nil {
				return err
			}
			if filepath.Base(path) != "hooks.json" {
				continue
			}
			expected, ok := installed[path]
			if !ok {
				return errors.New("unknown or wrong-layer Agy native hooks.json read")
			}
			end := strings.LastIndex(call.body, " = ")
			if end < 0 {
				return errors.New("missing Agy open result")
			}
			if expected == nil && strings.HasPrefix(call.body[end+3:], "-1 ENOENT ") {
				continue
			}
			if expected == nil {
				return errors.New("native Agy read a declared absent layer")
			}
			fd, err := strconv.Atoi(call.body[end+3:])
			if err != nil || fd < 0 {
				return errors.New("failed Agy selected hook file open")
			}
			key := strconv.Itoa(fd) // CLONE_FILES shares native thread descriptors.
			if opened[key] != nil {
				return errors.New("overlapping Agy hook file descriptor")
			}
			opened[key] = &read{path: path}
		}
		if call.name == "read" {
			fdEnd := strings.Index(call.body, ",")
			if fdEnd < 0 {
				continue
			}
			key := call.body[5:fdEnd]
			selected := opened[key]
			if selected == nil {
				continue
			}
			raw, _, err := agyTraceIO(call)
			if err != nil {
				return err
			}
			if selected.eof {
				return errors.New("Agy hook read continued after EOF")
			}
			selected.raw = append(selected.raw, raw...)
			selected.eof = len(raw) == 0
		}
		if call.name == "close" {
			end := strings.Index(call.body, ")")
			if end < 0 {
				continue
			}
			key := call.body[6:end]
			selected := opened[key]
			if selected == nil {
				continue
			}
			if !strings.HasSuffix(call.body, " = 0") || !selected.eof || !bytes.Equal(selected.raw, installed[selected.path]) {
				return errors.New("Agy native hook bytes/EOF/close disagree")
			}
			seen[selected.path]++
			delete(opened, key)
		}
	}
	if len(opened) != 0 {
		return errors.New("incomplete Agy observed hook file reads")
	}
	for path, raw := range installed {
		if raw != nil && seen[path] == 0 {
			return errors.New("missing Agy observed hook file read")
		}
	}
	return nil
}

// Used by codec controls to pin the exact supplied raw bytes, never normalize
// or relabel retained observations as a fresh native qualification.
func agyTraceSHA(raw []byte) string { return fmt.Sprintf("%x", sha256.Sum256(raw)) }
