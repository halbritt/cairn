// Package admission implements source guard and observation mechanics over
// decoded values. It does not decode input, choose a production runtime/profile,
// inspect the process environment, or supply a real filesystem implementation.
package admission

import (
	"fmt"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Probe represents the source filesystem boundary. Resolve receives the actual
// source argument: config_home can be a non-string host value whose Path
// conversion fails inside the probe. Successful resolutions return the source
// Path's code points. No Go string conversion may replace isolated surrogates.
type Probe interface {
	Resolve(*value.Value) ([]rune, error)
	IsDir([]rune) (bool, error)
	Exists([]rune) (bool, error)
}

// Invocation contains explicit inputs, not defaults or a production wire.
// Environment is the captured string environment (an empty map means empty).
// Home supplies Path.home() from the originating process, which is distinct
// from Environment; its callback preserves the source's eager fallback lookup.
// Filesystem implements the source probe, including its conversion/errors.
// WakeAccepted supplies the caller's already-established wake decision; nil
// means false. Admission never authenticates that decision or grants authority.
type Invocation struct {
	Kind                     string
	Config, Event, EventName *value.Value
	Environment              map[string][]rune
	Home                     func() ([]rune, error)
	Filesystem               Probe
	WakeAccepted             func() (bool, error)
}

// Decision retains source optional fields and opaque host values. A nil Value
// means Python None; non-nil String values can contain isolated surrogates.
// Reason/Message/Phase use empty strings for the source's absent values.
type Decision struct {
	Kind, Verdict, Reason, Message, Phase              string
	Event, NativeID, Workspace, Project, ObservedModel *value.Value
	NativeTurnID, OwnerObservation                     *value.Value
	Exclusions                                         [][]rune
}

func (d *Decision) Run() bool { return d.Verdict == "run" }

// Observation returns the source coordination observation, without implying
// store authentication. The owner envelope is bounded transport data only.
func (d *Decision) Observation() *value.Value {
	result := object(
		member("native_id", d.NativeID), member("workspace", d.Workspace),
		member("project", d.Project), member("observed_model", d.ObservedModel),
		member("phase", optionalText(d.Phase)), member("event", d.Event),
		member("native_turn_id", d.NativeTurnID))
	if d.OwnerObservation != nil {
		result.Members = append(result.Members, d.OwnerObservation.Members...)
	}
	return result
}

func sourceError(class, message string) error { return &value.Error{Class: class, Message: message} }
func text(raw string) *value.Value            { return &value.Value{Kind: value.String, String: []rune(raw)} }
func pathText(raw []rune) *value.Value        { return &value.Value{Kind: value.String, String: raw} }
func optionalText(raw string) *value.Value {
	if raw == "" {
		return nil
	}
	return text(raw)
}
func member(key string, v *value.Value) value.Member { return value.Member{Key: []rune(key), Value: v} }
func object(members ...value.Member) *value.Value {
	return &value.Value{Kind: value.Object, Members: members}
}
func isNone(v *value.Value) bool { return v == nil || v.Kind == value.Null }
func stringIs(v *value.Value, s string) bool {
	return v != nil && v.Kind == value.String && equal(v.String, []rune(s))
}
func equal(a, b []rune) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
func truth(v *value.Value) bool {
	if isNone(v) {
		return false
	}
	switch v.Kind {
	case value.Bool:
		return v.Boolean
	case value.Int:
		return v.Integer != "0"
	case value.Float:
		return v.Number != 0
	case value.String:
		return len(v.String) != 0
	case value.Array:
		return len(v.Items) != 0
	case value.Object:
		return len(v.Members) != 0
	}
	return false
}
func typeName(v *value.Value) string {
	if isNone(v) {
		return "NoneType"
	}
	return [...]string{"NoneType", "bool", "int", "float", "str", "list", "dict"}[v.Kind]
}
func requireObject(v *value.Value) error {
	if v == nil || v.Kind != value.Object {
		return sourceError("AttributeError", fmt.Sprintf("'%s' object has no attribute 'get'", typeName(v)))
	}
	return nil
}
func hashable(v *value.Value) error {
	if v != nil && (v.Kind == value.Array || v.Kind == value.Object) {
		return sourceError("TypeError", "unhashable type: '"+typeName(v)+"'")
	}
	return nil
}
func getDefault(v *value.Value, key string, fallback *value.Value) *value.Value {
	if item := v.Get(key); item != nil {
		return item
	}
	return fallback
}
func eventName(explicit, event *value.Value) (*value.Value, error) {
	if truth(explicit) {
		return explicit, nil
	}
	if !truth(event) {
		return nil, nil
	}
	if err := requireObject(event); err != nil {
		return nil, err
	}
	return event.Get("hook_event_name"), nil
}
func decision(kind, verdict, reason, message string, event *value.Value) *Decision {
	return &Decision{Kind: kind, Verdict: verdict, Reason: reason, Message: message, Event: event}
}
func invalid(kind, message string, event *value.Value) *Decision {
	return decision(kind, "invalid", "invalid_host", message, event)
}

// Decide follows hook_admission.decide's guard/body/privacy order. Host shape
// errors remain distinct from invalid verdicts, and callback errors propagate
// from their original point. Only the explicit probe/callbacks can read a host.
func Decide(in Invocation) (*Decision, error) {
	if in.Kind != "memory" && in.Kind != "coordination" && in.Kind != "inbox_recall" {
		return nil, sourceError("KeyError", in.Kind)
	}
	name, err := eventName(in.EventName, in.Event)
	if err != nil {
		return nil, err
	}
	result, err := guard(in, name)
	if err != nil || !result.Run() || result.Phase == "cue" || isNone(in.Event) {
		return result, err
	}
	if in.Kind == "coordination" {
		result, err = observe(in, name)
		if err != nil || !result.Run() {
			return result, err
		}
		result.Exclusions, err = exclusions(in, [][]rune{result.Workspace.String}, true)
		if err != nil {
			return nil, err
		}
		if len(result.Exclusions) != 0 {
			result.Verdict, result.Reason = "skip", "privacy_marker"
		}
		return result, nil
	}
	if in.Kind == "inbox_recall" {
		if !oneOf(name, "SessionStart", "UserPromptSubmit") {
			return decision(in.Kind, "skip", "unsupported_event", "", name), nil
		}
		roots, err := privacyRoots(in, true)
		if err != nil {
			return nil, err
		}
		found, err := exclusions(in, roots, true)
		if err != nil {
			return nil, err
		}
		result = decision(in.Kind, "run", "", "", name)
		if len(found) != 0 {
			result.Verdict, result.Reason, result.Exclusions = "skip", "privacy_marker", found
			return result, nil
		}
		if err := requireObject(in.Event); err != nil {
			return nil, err
		}
		result.NativeID = in.Event.Get("session_id")
		return result, nil
	}
	return memory(in, name)
}

func (in Invocation) resolve(path *value.Value) ([]rune, error) {
	if in.Filesystem == nil {
		return nil, sourceError("ExplicitInputError", "filesystem probe required at source resolve")
	}
	return in.Filesystem.Resolve(path)
}
func (in Invocation) isDir(path []rune) (bool, error) {
	if in.Filesystem == nil {
		return false, sourceError("ExplicitInputError", "filesystem probe required at source is_dir")
	}
	return in.Filesystem.IsDir(path)
}
func (in Invocation) exists(path []rune) (bool, error) {
	if in.Filesystem == nil {
		return false, sourceError("ExplicitInputError", "filesystem probe required at source exists")
	}
	return in.Filesystem.Exists(path)
}
func guard(in Invocation, name *value.Value) (*Decision, error) {
	if in.Kind == "coordination" && equal(in.Environment["CAIRN_COORDINATION_DISABLED"], []rune("1")) {
		return decision(in.Kind, "skip", "coordination_disabled", "", name), nil
	}
	if err := requireObject(in.Config); err != nil {
		return nil, err
	}
	harness, configuredHome := in.Config.Get("harness"), in.Config.Get("config_home")
	if truth(configuredHome) {
		if err := hashable(harness); err != nil {
			return nil, err
		}
		if oneOf(harness, "codex", "claude") {
			if in.Home == nil {
				return nil, sourceError("ExplicitInputError", "originating process home lookup required")
			}
			home, err := in.Home() // the Python dict.get fallback argument is eager
			if err != nil {
				return nil, err
			}
			variable, suffix := "CODEX_HOME", ".codex"
			if stringIs(harness, "claude") {
				variable, suffix = "CLAUDE_CONFIG_DIR", ".claude"
			}
			active := joinPath(home, []rune(suffix))
			if selected, present := in.Environment[variable]; present {
				active = makePath(selected)
			}
			left, err := in.resolve(pathText(active))
			if err != nil {
				return nil, err
			}
			right, err := in.resolve(configuredHome)
			if err != nil {
				return nil, err
			}
			if !equal(left, right) {
				return decision(in.Kind, "skip", "other_account_home", "", name), nil
			}
		}
	}
	if in.Kind == "coordination" && stringIs(name, "PostToolUse") {
		if !oneOf(harness, "claude", "codex", "opencode") {
			return decision(in.Kind, "skip", "unsupported_event", "", name), nil
		}
		reason, err := disabled(in, false)
		if err != nil {
			return nil, err
		}
		if reason != "" {
			return decision(in.Kind, "skip", reason, "", name), nil
		}
		if err := requireObject(in.Event); err != nil {
			return nil, err
		}
		result := decision(in.Kind, "run", "", "", name)
		result.Phase, result.NativeID = "cue", in.Event.Get("session_id")
		return result, nil
	}
	reason, err := disabled(in, true)
	if err != nil {
		return nil, err
	}
	if reason != "" {
		return decision(in.Kind, "skip", reason, "", name), nil
	}
	return decision(in.Kind, "run", "", "", name), nil
}

func disabled(in Invocation, consultWake bool) (string, error) {
	if in.Kind == "coordination" && equal(in.Environment["CAIRN_COORDINATION_DISABLED"], []rune("1")) {
		return "coordination_disabled", nil
	}
	guarded := equal(in.Environment["CAIRN_LIFECYCLE_DISABLED"], []rune("1")) || equal(in.Environment["CAIRN_LIFECYCLE_CHILD"], []rune("1"))
	if in.Kind == "inbox_recall" {
		guarded = guarded || equal(in.Environment["CAIRN_COORDINATION_DISABLED"], []rune("1"))
	}
	waking := in.Kind != "memory" && len(in.Environment["CAIRN_WAKE_CONTEXT"]) != 0
	if in.Kind == "coordination" && (guarded || waking) && consultWake && in.WakeAccepted != nil {
		accepted, err := in.WakeAccepted()
		if err != nil {
			return "", err
		}
		if accepted {
			return "", nil
		}
	}
	if guarded {
		return "disabled_env", nil
	}
	if waking {
		return "wake_context", nil
	}
	return "", nil
}

func memory(in Invocation, name *value.Value) (*Decision, error) {
	if err := requireObject(in.Event); err != nil {
		return nil, err
	}
	harness, native := in.Config.Get("harness"), in.Event.Get("session_id")
	if message := shapedIdentityError(harness, native); message != "" {
		return invalid("memory", message, name), nil
	}
	if stringIs(name, "Stop") && (!stringIs(harness, "codex") || truth(in.Event.Get("stop_hook_active"))) {
		result := decision("memory", "skip", "stop_not_boundary", "", name)
		result.NativeID = native
		return result, nil
	}
	if !oneOf(name, "SessionStart", "UserPromptSubmit", "PreCompact", "SessionEnd", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop") {
		result := decision("memory", "skip", "unsupported_event", "", name)
		result.NativeID = native
		return result, nil
	}
	cwd := in.Event.Get("cwd")
	if cwd == nil || cwd.Kind != value.String {
		return invalid("memory", "host working directory is unavailable", name), nil
	}
	directory, err := in.isDir(cwd.String)
	if err != nil {
		return nil, err
	}
	if !directory {
		return invalid("memory", "host working directory is unavailable", name), nil
	}
	project := in.Event.Get("project_path")
	if truth(project) {
		if project.Kind != value.String || !absolute(project.String) {
			return invalid("memory", "explicit project directory is unavailable", name), nil
		}
		directory, err = in.isDir(project.String)
		if err != nil {
			return nil, err
		}
		if !directory {
			return invalid("memory", "explicit project directory is unavailable", name), nil
		}
	}
	roots, err := privacyRoots(in, true)
	if err != nil {
		return nil, err
	}
	found, err := exclusions(in, roots, false)
	if err != nil {
		return nil, err
	}
	result := decision("memory", "run", "", "", name)
	result.NativeID = native
	if len(found) != 0 {
		result.Verdict, result.Reason, result.Exclusions = "skip", "privacy_marker", found
		return result, nil
	}
	workspace, err := in.resolve(cwd)
	if err != nil {
		return nil, err
	}
	result.Workspace = pathText(workspace)
	return result, nil
}

func observe(in Invocation, name *value.Value) (*Decision, error) {
	harness := in.Config.Get("harness")
	if harness == nil {
		return nil, sourceError("KeyError", "harness")
	}
	if err := requireObject(in.Event); err != nil {
		return nil, err
	}
	var native, cwd, model *value.Value
	if stringIs(harness, "agy") {
		native, model = in.Event.Get("conversationId"), getDefault(in.Event, "modelName", text(""))
		roots := in.Event.Get("workspacePaths")
		if roots != nil && roots.Kind == value.Array && len(roots.Items) != 0 {
			cwd = roots.Items[0]
		}
	} else {
		native, cwd, model = in.Event.Get("session_id"), in.Event.Get("cwd"), getDefault(in.Event, "model", text(""))
	}
	if !validNativeID(native) {
		return invalid("coordination", "native conversation ID required", name), nil
	}
	if cwd == nil || cwd.Kind != value.String || !absolute(cwd.String) {
		return invalid("coordination", "native workspace must be an existing absolute directory", name), nil
	}
	directory, err := in.isDir(cwd.String)
	if err != nil {
		return nil, err
	}
	if !directory {
		return invalid("coordination", "native workspace must be an existing absolute directory", name), nil
	}
	if model.Kind != value.String || len(model.String) > 256 {
		return invalid("coordination", "invalid observed model", name), nil
	}
	if err := hashable(name); err != nil {
		return nil, err
	}
	phase := ""
	switch {
	case stringIs(name, "SessionStart"):
		phase = "start"
	case oneOf(name, "UserPromptSubmit", "PreInvocation", "TurnStart", "ToolStart", "ToolEnd"):
		phase = "busy"
	case oneOf(name, "Stop", "TurnEnd"):
		phase = "idle"
	case stringIs(name, "SessionEnd"):
		phase = "leave"
	case stringIs(name, "Interrupt"):
		phase = "interrupted"
	case stringIs(name, "ProviderObservation"):
		phase = "provider"
	default:
		return invalid("coordination", "unsupported coordination hook event", name), nil
	}
	if oneOf(name, "ToolStart", "ToolEnd") && !stringIs(harness, "opencode") {
		return invalid("coordination", "tool capture hooks are OpenCode-only", name), nil
	}
	if stringIs(name, "Interrupt") && !stringIs(harness, "codex") {
		return invalid("coordination", "interrupt observation is a Codex-only hook", name), nil
	}
	fullyIdle := in.Event.Get("fullyIdle")
	if stringIs(harness, "agy") && stringIs(name, "Stop") && (fullyIdle == nil || fullyIdle.Kind != value.Bool || !fullyIdle.Boolean) {
		phase = "busy"
	}
	workspace, err := in.resolve(cwd)
	if err != nil {
		return nil, err
	}
	root := workspace
	for _, parent := range ancestry(workspace) {
		exists, err := in.exists(joinPath(parent, []rune(".git")))
		if err != nil {
			return nil, err
		}
		if exists {
			root = parent
			break
		}
	}
	if err := hashable(harness); err != nil {
		return nil, err
	}
	owner := text("")
	if oneOf(harness, "claude", "codex", "hermes", "opencode") {
		field := "turn_id"
		if stringIs(harness, "claude") {
			field = "prompt_id"
		}
		owner = getDefault(in.Event, field, text(""))
		if owner.Kind != value.String {
			return invalid("coordination", "invalid native ownership key", name), nil
		}
		length, err := strictUTF8Length(owner.String)
		if err != nil {
			return nil, err
		}
		if length > 256 || control(owner.String) {
			return invalid("coordination", "invalid native ownership key", name), nil
		}
		if len(owner.String) != 0 && stringIs(harness, "claude") {
			owner = pathText(append([]rune("claude-channel:"), owner.String...))
		}
	}
	ownerObservation := object()
	if stringIs(harness, "codex") && stringIs(name, "UserPromptSubmit") && !isNone(in.Event.Get("owner_input_attestation")) {
		envelope, origin := in.Event.Get("owner_input_attestation"), in.Event.Get("owner_input_origin")
		if len(owner.String) == 0 || !exactKeys(envelope, "schema", "payload", "signature") ||
			!stringIs(envelope.Get("schema"), "cairn.owner-input-attestation/1") ||
			!base64url(envelope.Get("payload"), 1, 2732) || !base64url(envelope.Get("signature"), 86, 86) {
			return invalid("coordination", "invalid owner input attestation envelope", name), nil
		}
		if !exactKeys(origin, "version", "source", "admission", "input_id") || origin.Get("version").Kind != value.Int || origin.Get("version").Integer != "1" ||
			!oneOf(origin.Get("source"), "owner_composer", "unknown") || !oneOf(origin.Get("admission"), "fresh_start", "joined", "recovery", "unknown") {
			return invalid("coordination", "invalid owner input origin", name), nil
		}
		identity := origin.Get("input_id")
		if !isNone(identity) && !canonicalNonzeroUUID(identity) {
			return invalid("coordination", "invalid owner input identity", name), nil
		}
		ownerObservation = object(member("owner_input_origin", shallowObject(origin)), member("owner_input_attestation", shallowObject(envelope)))
	}
	project := baseName(root)
	if len(project) == 0 {
		project = root
	}
	result := decision("coordination", "run", "", "", name)
	result.Phase, result.NativeID, result.Workspace, result.Project = phase, native, pathText(workspace), pathText(project)
	result.ObservedModel, result.NativeTurnID, result.OwnerObservation = model, owner, ownerObservation
	return result, nil
}

func oneOf(v *value.Value, choices ...string) bool {
	for _, choice := range choices {
		if stringIs(v, choice) {
			return true
		}
	}
	return false
}
func exactKeys(v *value.Value, keys ...string) bool {
	if v == nil || v.Kind != value.Object || len(v.Members) != len(keys) {
		return false
	}
	for _, key := range keys {
		if v.Get(key) == nil {
			return false
		}
	}
	return true
}
func shallowObject(v *value.Value) *value.Value {
	return object(append([]value.Member{}, v.Members...)...)
}
func base64url(v *value.Value, minimum, maximum int) bool {
	if v == nil || v.Kind != value.String || len(v.String) < minimum || len(v.String) > maximum {
		return false
	}
	for _, r := range v.String {
		if !(r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '_' || r == '-') {
			return false
		}
	}
	return true
}
func control(raw []rune) bool {
	for _, r := range raw {
		if r < 32 {
			return true
		}
	}
	return false
}
func strictUTF8Length(raw []rune) (int, error) {
	length := 0
	for _, r := range raw {
		if r >= 0xd800 && r <= 0xdfff {
			return 0, sourceError("UnicodeEncodeError", "UTF-8 surrogate encoding failure")
		}
		length += utf8.RuneLen(r)
	}
	return length, nil
}
func pythonSpace(r rune) bool {
	return r >= 9 && r <= 13 || r >= 0x1c && r <= 0x20 || r == 0x85 || r == 0xa0 || r == 0x1680 || r >= 0x2000 && r <= 0x200a || r == 0x2028 || r == 0x2029 || r == 0x202f || r == 0x205f || r == 0x3000
}
func validNativeID(v *value.Value) bool {
	if v == nil || v.Kind != value.String {
		return false
	}
	nonspace, length := false, 0
	for _, r := range v.String {
		if r < 32 || r >= 0xd800 && r <= 0xdfff {
			return false
		}
		nonspace = nonspace || !pythonSpace(r)
		length += utf8.RuneLen(r)
	}
	return nonspace && length <= 256
}

func privacyRoots(in Invocation, explicitProject bool) ([][]rune, error) {
	var roots [][]rune
	names := []string{"cwd"}
	if explicitProject {
		names = append(names, "project_path")
	}
	for _, name := range names {
		if !truth(in.Event) {
			continue
		}
		if err := requireObject(in.Event); err != nil {
			return nil, err
		}
		item := in.Event.Get(name)
		if truth(item) && item.Kind == value.String {
			path, err := in.resolve(item)
			if err != nil {
				return nil, err
			}
			roots = append(roots, path)
		}
	}
	return roots, nil
}
func exclusions(in Invocation, roots [][]rune, coordination bool) ([][]rune, error) {
	markers := []string{".cairn-no-memory"}
	if coordination {
		markers = append(markers, ".cairn-no-coordination")
	}
	var found [][]rune
	for _, root := range roots {
		for _, directory := range ancestry(root) {
			for _, marker := range markers {
				candidate := joinPath(directory, []rune(marker))
				exists, err := in.exists(candidate)
				if err != nil {
					return nil, err
				}
				if !exists {
					continue
				}
				duplicate := false
				for _, prior := range found {
					duplicate = duplicate || equal(candidate, prior)
				}
				if !duplicate {
					found = append(found, candidate)
				}
			}
		}
	}
	return found, nil
}

// These are PosixPath's lexical operations, not filesystem normalization.
// Keep '..' and the special '//' root; the injected Resolve owns resolution.
func absolute(path []rune) bool { return len(path) != 0 && path[0] == '/' }
func pathParts(path []rune) ([]rune, [][]rune) {
	root, start := []rune{}, 0
	for start < len(path) && path[start] == '/' {
		start++
	}
	if start != 0 {
		root = []rune{'/'}
		if start == 2 {
			root = []rune{'/', '/'}
		}
	}
	var parts [][]rune
	for i := start; i <= len(path); i++ {
		if i != len(path) && path[i] != '/' {
			continue
		}
		if i > start && !equal(path[start:i], []rune{'.'}) {
			parts = append(parts, path[start:i])
		}
		start = i + 1
	}
	return root, parts
}
func fromParts(root []rune, parts [][]rune) []rune {
	result := append([]rune{}, root...)
	for i, part := range parts {
		if i != 0 {
			result = append(result, '/')
		}
		result = append(result, part...)
	}
	if len(result) == 0 {
		return []rune{'.'}
	}
	return result
}
func makePath(path []rune) []rune {
	root, parts := pathParts(path)
	return fromParts(root, parts)
}
func joinPath(parent, child []rune) []rune {
	if absolute(child) {
		return makePath(child)
	}
	joined := makePath(parent)
	if joined[len(joined)-1] != '/' {
		joined = append(joined, '/')
	}
	return makePath(append(joined, child...))
}
func ancestry(path []rune) [][]rune {
	root, parts := pathParts(path)
	result := [][]rune{fromParts(root, parts)}
	for len(parts) != 0 {
		parts = parts[:len(parts)-1]
		result = append(result, fromParts(root, parts))
	}
	return result
}
func baseName(path []rune) []rune {
	_, parts := pathParts(path)
	if len(parts) == 0 {
		return nil
	}
	return parts[len(parts)-1]
}

func shapedIdentityError(harness, native *value.Value) string {
	if native == nil || native.Kind != value.String || len(native.String) == 0 || len(native.String) > 128 {
		return "missing or invalid host session identity"
	}
	if stringIs(harness, "opencode") {
		if len(native.String) <= 4 || !equal(native.String[:4], []rune("ses_")) {
			return "invalid OpenCode session identity"
		}
		for _, r := range native.String[4:] {
			if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9') {
				return "invalid OpenCode session identity"
			}
		}
		return ""
	}
	if stringIs(harness, "hermes") {
		if control(native.String) {
			return "invalid Hermes session identity"
		}
		return ""
	}
	if !uuidShape(native.String) {
		return "host session identity must be a UUID"
	}
	return ""
}

func canonicalNonzeroUUID(v *value.Value) bool {
	if v == nil || v.Kind != value.String || len(v.String) != 36 {
		return false
	}
	nonzero := false
	for i, r := range v.String {
		if i == 8 || i == 13 || i == 18 || i == 23 {
			if r != '-' {
				return false
			}
			continue
		}
		if !(r >= '0' && r <= '9' || r >= 'a' && r <= 'f') {
			return false
		}
		nonzero = nonzero || r != '0'
	}
	return nonzero
}

// UUID shape follows the current reference Python constructor, including its
// Unicode 16 decimal conversion capability. This is mechanical support, not a
// production runtime/profile selection. Qualification must bind the actual
// originating runtime before this package acquires its production consumer.
var uuidDecimalZero = [...]rune{
	48, 1632, 1776, 1984, 2406, 2534, 2662, 2790, 2918, 3046, 3174, 3302, 3430, 3558, 3664, 3792,
	3872, 4160, 4240, 6112, 6160, 6470, 6608, 6784, 6800, 6992, 7088, 7232, 7248, 42528, 43216,
	43264, 43472, 43504, 43600, 44016, 65296, 66720, 68912, 68928, 69734, 69872, 69942, 70096,
	70384, 70736, 70864, 71248, 71360, 71376, 71386, 71472, 71904, 72016, 72688, 72784, 73040,
	73120, 73552, 90416, 92768, 92864, 93008, 93552, 118000, 120782, 120792, 120802, 120812,
	120822, 123200, 123632, 124144, 124401, 125264, 130032,
}

func decimal(r rune) (rune, bool) {
	for _, zero := range uuidDecimalZero {
		if r >= zero && r < zero+10 {
			return '0' + r - zero, true
		}
	}
	return 0, false
}
func replace(raw, old []rune) []rune {
	var result []rune
	for i := 0; i < len(raw); {
		if i+len(old) <= len(raw) && equal(raw[i:i+len(old)], old) {
			i += len(old)
		} else {
			result = append(result, raw[i])
			i++
		}
	}
	return result
}
func uuidShape(raw []rune) bool {
	raw = replace(replace(raw, []rune("urn:")), []rune("uuid:"))
	for len(raw) != 0 && (raw[0] == '{' || raw[0] == '}') {
		raw = raw[1:]
	}
	for len(raw) != 0 && (raw[len(raw)-1] == '{' || raw[len(raw)-1] == '}') {
		raw = raw[:len(raw)-1]
	}
	raw = replace(raw, []rune{'-'})
	if len(raw) != 32 {
		return false
	}
	// int(str,16) transforms non-ASCII decimal digits/space before its ASCII
	// scanner. ASCII separators 0x1c..0x1f are not integer whitespace.
	converted := append([]rune{}, raw...)
	for i, r := range converted {
		if digit, ok := decimal(r); ok {
			converted[i] = digit
		} else if r >= 128 && pythonSpace(r) {
			converted[i] = ' '
		}
	}
	space := func(r rune) bool { return r == ' ' || r >= '\t' && r <= '\r' }
	for len(converted) != 0 && space(converted[0]) {
		converted = converted[1:]
	}
	for len(converted) != 0 && space(converted[len(converted)-1]) {
		converted = converted[:len(converted)-1]
	}
	if len(converted) != 0 && converted[0] == '+' {
		converted = converted[1:]
	}
	previousDigit, prefixUnderscore := false, false
	if len(converted) >= 2 && converted[0] == '0' && (converted[1] == 'x' || converted[1] == 'X') {
		converted = converted[2:]
		prefixUnderscore = true
	}
	digits := 0
	for _, r := range converted {
		if r == '_' && (previousDigit || prefixUnderscore) {
			previousDigit, prefixUnderscore = false, false
			continue
		}
		if !(r >= '0' && r <= '9' || r >= 'a' && r <= 'f' || r >= 'A' && r <= 'F') {
			return false
		}
		digits++
		previousDigit, prefixUnderscore = true, false
	}
	return digits != 0 && previousDigit // at most 32 hex digits after preprocessing
}
