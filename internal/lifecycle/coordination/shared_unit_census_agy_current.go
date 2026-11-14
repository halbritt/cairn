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
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Version 2 locates current observations. No field grants authority, supplies
// authenticated drain, or asserts that an idle live client has refreshed.
type WatchCensusAgyCurrentReadback struct {
	Schema, Harness, EvidenceKind, NativeSession, Workspace string
	Client, Generation                                      WatchCensusPin
	ClientArgv                                              []string
	A, B                                                    WatchCensusAgyCurrentSelection
	Before, After                                           WatchCensusAgyCurrentPhase
}

type WatchCensusAgyCurrentSelection struct {
	Generation, Config, Settings, Launcher WatchCensusPin
}

type WatchCensusAgyCurrentPhase struct {
	Trace      WatchCensusPin
	Start, End int
	Callbacks  []WatchCensusAgyCurrentCallback
}

type WatchCensusAgyCurrentCallback struct {
	Event             string
	Exec, Join, Frame int
}

type agyGenerationCapture struct {
	Process    WatchCensusProcess
	Client     WatchCensusPin
	ClientArgv []string
}

type agyGenerationRecord struct {
	Schema        string
	Before, After agyGenerationCapture
}

func agyCurrentDecode(raw []byte, dst any) error {
	if err := hookDecode(raw, dst); err != nil {
		return err
	}
	var tree any
	if err := json.Unmarshal(raw, &tree); err != nil {
		return err
	}
	var visit func(any) error
	visit = func(node any) error {
		switch fields := node.(type) {
		case map[string]any:
			seen := map[string]bool{}
			for name, child := range fields {
				key := strings.ToLower(name)
				if seen[key] {
					return errors.New("ambiguous current Agy field aliases")
				}
				seen[key] = true
				if err := visit(child); err != nil {
					return err
				}
			}
		case []any:
			for _, child := range fields {
				if err := visit(child); err != nil {
					return err
				}
			}
		}
		return nil
	}
	return visit(tree)
}

func agyCurrentDescriptor(raw []byte) (WatchCensusAgyCurrentReadback, error) {
	var current WatchCensusAgyCurrentReadback
	if err := agyCurrentDecode(raw, &current); err != nil {
		return current, err
	}
	if current.Schema != "cairn.watch-agy-readback/2" || current.Harness != "agy" || current.EvidenceKind != "current-observation" {
		return current, errors.New("unsupported Agy current observation version/kind")
	}
	return current, nil
}

func (r *watchCensusRead) agyCurrentGeneration(capture agyGenerationCapture, state *value.Value) error {
	p := capture.Process
	if p.PID <= 1 || p.Start == "" || p.Boot == "" || len(capture.ClientArgv) == 0 || capture.ClientArgv[0] != capture.Client.Path {
		return errors.New("missing Agy collector process generation/image/argv")
	}
	if _, err := strconv.ParseUint(p.Start, 10, 64); err != nil || p.Start == "0" {
		return errors.New("invalid Agy collector start identity")
	}
	current, err := nativeProcessReference(r.cli.ProcRoot, value.IntegerText(strconv.Itoa(p.PID)))
	if err != nil {
		return fmt.Errorf("Agy current generation unavailable: %w", err)
	}
	for _, recorded := range []*value.Value{censusNativeProcess(p), state.Get("process")} {
		same, err := nativeSameProcess(current, recorded)
		if err != nil || !same {
			return errors.New("Agy collector/owner/live generation mismatch or reused PID")
		}
	}
	if slices.Contains([]string{"Z", "X"}, nativeText(current.Get("state"))) {
		return errors.New("Agy current generation is not live")
	}
	root := filepath.Join(procRoot(r.cli.ProcRoot), strconv.Itoa(p.PID))
	executable, err := os.Readlink(filepath.Join(root, "exe"))
	if err != nil || executable != capture.Client.Path {
		return errors.New("Agy live executable differs from collector image")
	}
	if _, err := r.pin(capture.Client, true); err != nil {
		return err
	}
	cmdline, err := os.ReadFile(filepath.Join(root, "cmdline"))
	if err != nil || !utf8.Valid(cmdline) || !bytes.HasSuffix(cmdline, []byte{0}) {
		return errors.New("Agy live literal argv unavailable")
	}
	var argv []string
	for _, arg := range bytes.Split(cmdline[:len(cmdline)-1], []byte{0}) {
		argv = append(argv, string(arg))
	}
	if !slices.Equal(argv, capture.ClientArgv) {
		return errors.New("Agy live literal argv differs from collector capture")
	}
	encoded, err := json.Marshal(struct {
		PID         int
		Start, Boot string
		Client      WatchCensusPin
		Argv        []string
	}{p.PID, p.Start, p.Boot, capture.Client, argv})
	if err != nil {
		return err
	}
	key, fingerprint := "agy-current-process:"+strconv.Itoa(p.PID), agyTraceSHA(encoded)
	if old := r.report.Facts[key]; old != "" && old != fingerprint {
		return errors.New("Agy process generation changed at final reread")
	}
	r.report.Facts[key] = fingerprint
	return nil
}

func (r *watchCensusRead) agyCurrentPriorGeneration(before, after WatchCensusProcess) error {
	if before.PID <= 1 {
		return errors.New("missing Agy before generation")
	}
	if before.PID == after.PID {
		return nil
	}
	_, err := os.Lstat(filepath.Join(procRoot(r.cli.ProcRoot), strconv.Itoa(before.PID)))
	if err == nil {
		return errors.New("old Agy generation is still present or its PID was reused")
	}
	if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("old Agy generation disposition unavailable: %w", err)
	}
	return nil
}

func agyOneManagedSettings(raw []byte) error {
	var fields map[string]json.RawMessage
	if err := hookDecode(raw, &fields); err != nil {
		return err
	}
	if len(fields) != 1 || fields["cairn-coordination"] == nil {
		return errors.New("unknown active Agy settings group")
	}
	return nil
}

func agyCurrentLayer(selected WatchCensusAgyHooks, current WatchCensusAgyCurrentReadback, commands []agyCensusCommand) (WatchCensusAgyLayer, error) {
	var active []WatchCensusAgyLayer
	for _, layer := range selected.Layers {
		if layer.Installed.SHA256 != "" {
			active = append(active, layer)
		}
	}
	if len(active) != 1 || len(commands) != 2 || current.B.Generation != active[0].Generation || current.B.Settings != active[0].Settings || current.B.Config.SHA256 != selected.Config.SHA256 || current.A.Launcher == current.B.Launcher {
		return WatchCensusAgyLayer{}, errors.New("Agy current positive requires exactly one active layer and distinct A/B selections")
	}
	return active[0], nil
}

func (r *watchCensusRead) agyCurrentA(ctx context.Context, selected WatchCensusAgyHooks, a WatchCensusAgyCurrentSelection) ([]agyCensusCommand, []byte, error) {
	members, err := r.agyGeneration(ctx, a.Generation)
	if err != nil {
		return nil, nil, err
	}
	producer, err := r.jsonFile(members["hook-selection.json"].Path)
	if err != nil {
		return nil, nil, err
	}
	life := producer.Get("lifecycle_selection")
	files, err := r.agyGeneration(ctx, WatchCensusPin{nativeText(life.Get("path")), nativeText(life.Get("sha256"))})
	if err != nil {
		return nil, nil, err
	}
	association, profiles, err := r.unboundReceipt(files["receipt.json"])
	if err != nil {
		return nil, nil, err
	}
	if !slices.Contains(profiles, a.Config) {
		return nil, nil, errors.New("Agy A config is not its actual unbound profile")
	}
	config, err := r.jsonFile(a.Config.Path)
	if err != nil {
		return nil, nil, err
	}
	owner, err := r.configOwner(config)
	if err != nil || owner != selected.Config.Path {
		return nil, nil, errors.New("Agy A belongs to another selected user")
	}
	receipt, err := r.jsonFile(files["receipt.json"].Path)
	if err != nil {
		return nil, nil, err
	}
	_, memory, err := r.unboundDescriptor(receipt.Get("memory_config"))
	if err != nil {
		return nil, nil, err
	}
	prior := selected
	prior.Config = a.Config
	node := &WatchCensusMember{Config: a.Config, Binding: association.binding, Memory: memory}
	return r.agyProducedLayerFor(ctx, prior, WatchCensusAgyLayer{Generation: a.Generation, Settings: a.Settings}, node, false)
}

func (r *watchCensusRead) inspectAgyCurrent(ctx context.Context, selected WatchCensusAgyHooks, raw []byte, bCommands []agyCensusCommand, observations []WatchCensusHookObservation) ([]WatchCensusHookObservation, error) {
	readback, err := agyCurrentDescriptor(raw)
	if err != nil {
		return nil, err
	}
	if readback.Schema != "cairn.watch-agy-readback/2" || readback.Harness != "agy" || readback.EvidenceKind != "current-observation" || readback.Workspace != selected.Workspace || readback.NativeSession == "" {
		return nil, errors.New("unsupported Agy current observation version/identity")
	}
	active, err := agyCurrentLayer(selected, readback, bCommands)
	if err != nil {
		return nil, err
	}
	if !slices.Contains(r.catalog.Sessions, selected.Session) || !censusWithin(r.catalog.Root, selected.Session.Path) {
		return nil, errors.New("missing current Agy owner state")
	}
	if _, err := r.pin(selected.Session, false); err != nil {
		return nil, err
	}
	state, err := r.jsonFile(selected.Session.Path)
	if err != nil {
		return nil, err
	}
	native, err := watchStateNativeSession(state)
	owner := r.sessionConfig(selected.Session.Path, state)
	if err != nil || native != readback.NativeSession || !value.SameText(state.Get("schema"), NativeSessionSchema) || owner == nil || r.configPaths[owner] != selected.Config.Path || nativeText(state.Get("agent").Get("metadata").Get("workspace")) != selected.Workspace {
		return nil, errors.New("Agy current owner session/workspace/config disagrees")
	}
	if !censusWithin(r.catalog.Root, readback.Generation.Path) || !censusWithin(r.catalog.Root, readback.Client.Path) {
		return nil, errors.New("external Agy generation record or client")
	}
	generationRaw, err := r.pin(readback.Generation, false)
	if err != nil {
		return nil, err
	}
	var generation agyGenerationRecord
	if err := agyCurrentDecode(generationRaw, &generation); err != nil {
		return nil, err
	}
	if generation.Schema != "cairn.agy-collector-generation/1" || generation.Before.Client != readback.Client || generation.After.Client != readback.Client || !slices.Equal(generation.After.ClientArgv, readback.ClientArgv) {
		return nil, errors.New("Agy collector generation/client correspondence missing")
	}
	if err := r.agyCurrentGeneration(generation.After, state); err != nil {
		return nil, err
	}
	if err := r.agyCurrentPriorGeneration(generation.Before.Process, generation.After.Process); err != nil {
		return nil, err
	}
	aCommands, aSettings, err := r.agyCurrentA(ctx, selected, readback.A)
	if err != nil {
		return nil, err
	}
	bSettings, err := r.pin(readback.B.Settings, false)
	if err != nil {
		return nil, err
	}
	for _, settings := range [][]byte{aSettings, bSettings} {
		if err := agyOneManagedSettings(settings); err != nil {
			return nil, err
		}
	}
	for _, selection := range []WatchCensusAgyCurrentSelection{readback.A, readback.B} {
		if !censusWithin(r.catalog.Root, selection.Config.Path) || !censusWithin(r.catalog.Root, selection.Launcher.Path) {
			return nil, errors.New("external Agy current profile/launcher")
		}
		if _, err := r.pin(selection.Config, false); err != nil {
			return nil, err
		}
		if _, err := r.pin(selection.Launcher, true); err != nil {
			return nil, err
		}
	}
	before, err := r.agyTrace(readback.Before.Trace)
	if err != nil {
		return nil, err
	}
	after, err := r.agyTrace(readback.After.Trace)
	if err != nil {
		return nil, err
	}
	if err := agyCurrentPair(before, after, readback, generation, aSettings, bSettings, aCommands, bCommands, selected.Account, active.Installed.Path); err != nil {
		return nil, err
	}
	// These are per-point non-atomic facts, not a global future refresh promise.
	// Authenticated D2-D4, ordinary drain closure and final D6 remain separate.
	key := "agy-current-observation:" + selected.Readback.Path
	r.report.Facts[key] = readback.After.Trace.SHA256
	for i := range observations {
		observations[i].Observation = "Agy current generation executed exact B after A; per-point observed interval only; external coverage unknown"
	}
	return observations, nil
}

func agyCurrentPair(before, after []byte, observed WatchCensusAgyCurrentReadback, generation agyGenerationRecord, aSettings, bSettings []byte, aCommands, bCommands []agyCensusCommand, account, installedPath string) error {
	a, b := generation.Before.Process, generation.After.Process
	for _, process := range []WatchCensusProcess{a, b} {
		start, err := strconv.ParseUint(process.Start, 10, 64)
		if err != nil || start == 0 || strconv.FormatUint(start, 10) != process.Start {
			return errors.New("missing or invalid Agy captured generation start")
		}
	}
	if a.PID <= 1 || b.PID <= 1 || a.Start == "" || a.Boot == "" || a.Boot != b.Boot ||
		a.PID == b.PID && a.Start != b.Start {
		return errors.New("Agy before/after generation mismatch or reused PID")
	}
	if len(generation.Before.ClientArgv) == 0 || generation.Before.ClientArgv[0] != observed.Client.Path {
		return errors.New("missing Agy before literal client argv")
	}
	if a.PID == b.PID && !slices.Equal(generation.Before.ClientArgv, generation.After.ClientArgv) {
		return errors.New("Agy same generation changed client argv")
	}
	beforeFiles, err := agyCurrentBefore(before, observed.Before, generation.Before, observed, aSettings, aCommands, account)
	if err != nil {
		return err
	}
	if a.PID != b.PID {
		calls, err := agyTraceCalls(before)
		if err != nil {
			return err
		}
		exit, joined := -1, false
		for _, call := range calls {
			if call.pid == a.PID && call.name == "exit" && call.body == "+++ exited with 0 +++" {
				exit = call.start
			}
			if exit >= 0 && call.pid == a.Parent && call.name == "wait4" && call.start > exit &&
				strings.HasPrefix(call.body, "wait4("+strconv.Itoa(a.PID)+",") &&
				strings.Contains(call.body, "WIFEXITED(s) && WEXITSTATUS(s) == 0") && strings.HasSuffix(call.body, " = "+strconv.Itoa(a.PID)) {
				joined = true
			}
		}
		if !joined {
			return errors.New("Agy new generation lacks orderly old-generation exit and owner join")
		}
	}
	installed := map[string][]byte{filepath.Join(account, "hooks.json"): nil, filepath.Join(observed.Workspace, ".agents", "hooks.json"): nil}
	if _, known := installed[installedPath]; !known {
		return errors.New("unknown current Agy installed layer")
	}
	installed[installedPath] = bSettings
	return agyCurrentPhase(after, observed.After, generation.After, observed, observed.B, observed.A,
		bCommands, aCommands, installed, beforeFiles)
}

func agyCurrentBefore(raw []byte, phase WatchCensusAgyCurrentPhase, capture agyGenerationCapture, observed WatchCensusAgyCurrentReadback, settings []byte, commands []agyCensusCommand, account string) (map[string][]byte, error) {
	paths := []string{filepath.Join(account, "hooks.json"), filepath.Join(observed.Workspace, ".agents", "hooks.json")}
	var result map[string][]byte
	for _, path := range paths {
		installed := map[string][]byte{paths[0]: nil, paths[1]: nil}
		installed[path] = settings
		if err := agyCurrentPhase(raw, phase, capture, observed, observed.A, WatchCensusAgyCurrentSelection{}, commands, nil, installed, nil); err == nil {
			if result != nil {
				return nil, errors.New("ambiguous Agy before layer")
			}
			result = installed
		}
	}
	if result == nil {
		return nil, errors.New("Agy before raw session/layer/callback/idle correspondence refused")
	}
	return result, nil
}

func agyCurrentPhase(raw []byte, phase WatchCensusAgyCurrentPhase, capture agyGenerationCapture, observed WatchCensusAgyCurrentReadback, selected, prior WatchCensusAgyCurrentSelection, commands, priorCommands []agyCensusCommand, installed, priorFiles map[string][]byte) error {
	if phase.Start < 0 || phase.End != len(raw) || phase.End <= phase.Start || raw[phase.End-1] != '\n' || phase.Start > 0 && raw[phase.Start-1] != '\n' || len(phase.Callbacks) != 2 || len(commands) != 2 {
		return errors.New("incomplete Agy current interval or callback references")
	}
	calls, err := agyTraceCalls(raw)
	if err != nil {
		return err
	}
	for _, line := range bytes.Split(bytes.TrimSuffix(raw, []byte{'\n'}), []byte{'\n'}) {
		parts := agyTraceLine.FindStringSubmatch(string(line))
		if parts == nil {
			return errors.New("unsupported current Agy trace prefix")
		}
		body := parts[2]
		if agyTraceName.MatchString(body) || agyTraceResume.MatchString(body) || strings.HasPrefix(body, "+++ exited with ") || strings.HasPrefix(body, "+++ killed by ") || strings.HasPrefix(body, "--- SIG") {
			continue
		}
		return errors.New("unknown current Agy trace record")
	}
	byOffset := map[int]agyTraceCall{}
	threads, descendants := map[int]bool{capture.Process.PID: true}, map[int]bool{capture.Process.PID: true}
	nativeWrites := map[int][]byte{}
	var executions []agyTraceCall
	clientSeen, boundary := false, -1
	for _, call := range calls {
		byOffset[call.start] = call
		if descendants[call.pid] && slices.Contains([]string{"clone", "clone3", "fork", "vfork"}, call.name) {
			end := strings.LastIndex(call.body, " = ")
			if end < 0 {
				return errors.New("missing current Agy descendant result")
			}
			child, parseErr := strconv.Atoi(call.body[end+3:])
			if parseErr != nil {
				if strings.HasPrefix(call.body[end+3:], "-1 ") {
					continue
				}
				return errors.New("unknown current Agy descendant")
			}
			if child <= 1 || descendants[child] {
				return errors.New("reused or unsafe current Agy descendant PID")
			}
			descendants[child] = true
			if threads[call.pid] && strings.Contains(call.body, "CLONE_THREAD") {
				threads[child] = true
			}
		}
		if call.name == "write" && threads[call.pid] {
			body, _, e := agyTraceIO(call)
			if e != nil {
				return e
			}
			if bytes.Contains(body, []byte("conversationId")) && bytes.Contains(body, []byte("hookEventName")) {
				nativeWrites[call.start] = body
			}
		}
		if call.name != "execve" {
			continue
		}
		path, argv, e := agyExec(call)
		if call.pid == capture.Process.PID {
			if e != nil || path != capture.Client.Path || !slices.Equal(argv, capture.ClientArgv) {
				return errors.New("foreign Agy client exec/argv")
			}
			clientSeen = true
		}
		if !descendants[call.pid] {
			if call.start >= phase.Start && e == nil && (path == selected.Launcher.Path || path == prior.Launcher.Path || len(argv) > 1 && (argv[1] == "hook" || argv[1] == "coordination")) {
				return errors.New("foreign Agy callback generation")
			}
			continue
		}
		if e != nil {
			return e
		}
		if path == capture.Client.Path && call.pid != capture.Process.PID {
			return errors.New("foreign nested Agy client generation")
		}
		if path == selected.Launcher.Path {
			if boundary < 0 {
				boundary = call.start
			}
			executions = append(executions, call)
		} else if prior.Launcher.Path != "" && path == prior.Launcher.Path {
			if boundary >= 0 {
				return errors.New("Agy A launcher executed after first B boundary")
			}
			executions = append(executions, call)
		} else if len(argv) > 1 && (argv[1] == "hook" || argv[1] == "coordination") || filepath.Base(path) == "inbox" {
			known := false
			for _, command := range append(slices.Clone(commands), priorCommands...) {
				known = known || path == command.program.Path && slices.Equal(argv, command.argv)
			}
			if !known {
				return errors.New("extra or foreign Agy callback/source")
			}
		}
	}
	if !clientSeen || boundary < phase.Start {
		return errors.New("Agy refresh unobserved or interval hides its first managed exec")
	}
	if err := agyTraceFilesTransition(calls, threads, installed, priorFiles, boundary); err != nil {
		return err
	}
	refs := map[int]WatchCensusAgyCurrentCallback{}
	for index, ref := range phase.Callbacks {
		if ref.Event != []string{"PreInvocation", "Stop"}[index] || refs[ref.Exec].Event != "" || ref.Exec < phase.Start || ref.Join >= phase.End || ref.Frame < phase.Start {
			return errors.New("invalid current Agy callback/idle locators")
		}
		refs[ref.Exec] = ref
	}
	usedFrames, matched := map[int]bool{}, map[int]bool{}
	previousJoin := -1
	for _, execution := range executions {
		if execution.start < phase.Start {
			continue
		}
		path, argv, e := agyExec(execution)
		if e != nil {
			return e
		}
		selectedRows := commands
		if path == prior.Launcher.Path {
			selectedRows = priorCommands
		}
		var command *agyCensusCommand
		for i := range selectedRows {
			row := &selectedRows[i]
			outer := []string{path, "hook", "--config", selected.Config.Path, "--event", row.event}
			if path == prior.Launcher.Path {
				outer[3] = prior.Config.Path
			}
			if row.launcher.Path == path && slices.Equal(argv, outer) {
				command = row
			}
		}
		if command == nil {
			return errors.New("literal Agy launcher/config/event disagrees")
		}
		join, inner := -1, -1
		for _, call := range calls {
			if call.pid != execution.pid || call.start <= execution.start {
				continue
			}
			if call.name == "execve" {
				program, args, e := agyExec(call)
				if e != nil || inner >= 0 || program != command.program.Path || !slices.Equal(args, command.argv) {
					return errors.New("Agy launcher did not exec its exact pinned callback")
				}
				inner = call.start
			}
			if call.name == "exit" {
				if call.body != "+++ exited with 0 +++" {
					return errors.New("Agy callback did not join to zero exit")
				}
				join = call.start
				break
			}
		}
		if inner < 0 || join <= inner {
			return errors.New("incomplete Agy launcher callback join")
		}
		input, e := agyCurrentInput(calls, execution.pid, inner, join)
		if e != nil {
			return e
		}
		frame := -1
		for offset, body := range nativeWrites {
			if offset >= phase.Start && offset > previousJoin && offset < join && bytes.Equal(body, input) {
				if frame >= 0 {
					return errors.New("ambiguous Agy callback input frame")
				}
				frame = offset
			}
		}
		if frame < 0 || usedFrames[frame] {
			return errors.New("missing or reused Agy native input frame")
		}
		if err := agyCurrentFrame(input, command.event, observed.NativeSession, observed.Workspace); err != nil {
			return err
		}
		usedFrames[frame] = true
		previousJoin = join
		if path == selected.Launcher.Path {
			ref, ok := refs[execution.start]
			if !ok || ref.Event != command.event || ref.Join != join || ref.Frame != frame || byOffset[ref.Frame].name != "write" {
				return errors.New("Agy callback locators disagree with raw exec/input/join")
			}
			matched[execution.start] = true
		}
	}
	if len(matched) != 2 {
		return errors.New("missing exact Agy managed callbacks")
	}
	for offset := range nativeWrites {
		if offset >= phase.Start && !usedFrames[offset] {
			return errors.New("extra Agy native event frame")
		}
	}
	return nil
}

func agyCurrentFrame(raw []byte, event, session, workspace string) error {
	var fields map[string]json.RawMessage
	if err := agyCurrentDecode(raw, &fields); err != nil {
		return err
	}
	var native, name string
	var paths []string
	if json.Unmarshal(fields["conversationId"], &native) != nil || json.Unmarshal(fields["hookEventName"], &name) != nil || json.Unmarshal(fields["workspacePaths"], &paths) != nil || native != session || name != event || !slices.Equal(paths, []string{workspace}) {
		return errors.New("current Agy frame session/event/workspace disagrees")
	}
	if event == "Stop" && !bytes.Equal(bytes.TrimSpace(fields["fullyIdle"]), []byte("true")) {
		return errors.New("Agy Stop is not actual fullyIdle=true")
	}
	return nil
}

func agyCurrentInput(calls []agyTraceCall, pid, start, end int) ([]byte, error) {
	threads := map[int]bool{pid: true}
	var input []byte
	eof := false
	for _, call := range calls {
		if call.start <= start || call.start >= end || !threads[call.pid] {
			continue
		}
		if (call.name == "clone" || call.name == "clone3") && strings.Contains(call.body, "CLONE_THREAD") {
			index := strings.LastIndex(call.body, " = ")
			if index < 0 {
				return nil, errors.New("missing callback thread")
			}
			child, err := strconv.Atoi(call.body[index+3:])
			if err != nil || child <= 1 {
				return nil, errors.New("invalid callback thread")
			}
			threads[child] = true
		}
		if call.name == "read" && strings.HasPrefix(call.body, "read(0,") {
			part, _, err := agyTraceIO(call)
			if err != nil {
				return nil, err
			}
			if eof {
				return nil, errors.New("callback stdin continued after EOF")
			}
			input = append(input, part...)
			eof = len(part) == 0
		}
	}
	if !eof || len(input) == 0 {
		return nil, errors.New("incomplete Agy callback stdin bytes/EOF")
	}
	return input, nil
}
