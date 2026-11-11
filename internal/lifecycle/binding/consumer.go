package binding

import (
	"context"
	"errors"
	"io"
	"os"
	"os/user"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const inputLimit = 1024 * 1024

var ErrLegacyBinding = errors.New("legacy /2 binding requires its retained pinned Python executor; Go requires explicit /3 repin")

// Invocation is host-selected input, not authentication or an activation grant.
// Event bytes and the captured process environment/cwd are never reconstructed.
type Invocation = admission.HookInvocation
type CheckResult struct {
	// Config and Event are the checked snapshots for the reached memory consumer.
	// They never enter the public read-only binding-check response.
	Config        *value.Value   `json:"-"`
	Event         *value.Value   `json:"-"`
	Coordinators  []*value.Value `json:"-"`
	Disposition   string         `json:"disposition"`
	Verdict       string         `json:"verdict"`
	Reason        string         `json:"reason,omitempty"`
	ConfigSHA256  string         `json:"config_sha256,omitempty"`
	BindingSHA256 string         `json:"binding_sha256,omitempty"`
}

func sourceString(v *value.Value) (string, error) {
	if v == nil || v.Kind != value.String {
		return "", errors.New("binding field must be a string")
	}
	return admission.PathBytes(v.String)
}
func descriptor(v *value.Value) (fileDescriptor, error) {
	if v == nil || v.Kind != value.Object {
		return fileDescriptor{}, errors.New("binding descriptor must be an object")
	}
	p, err := sourceString(v.Get("path"))
	if err != nil {
		return fileDescriptor{}, err
	}
	h, err := sourceString(v.Get("sha256"))
	if err != nil {
		return fileDescriptor{}, err
	}
	d := fileDescriptor{p, h}
	if !validDescriptor(d) {
		return d, errors.New("invalid binding descriptor")
	}
	return d, nil
}

// Config/manifest files retain the source's absolute-path, UID and mode policy,
// including symlink following. New binary/code artifacts use the separate /3 walk.
func readData(path string, private bool) ([]byte, error) {
	if !filepath.IsAbs(path) {
		return nil, errors.New("binding data path must be absolute")
	}
	f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	before, err := fileStat(f)
	if err != nil {
		f.Close()
		return nil, err
	}
	mode := uint32(0022)
	if private {
		mode = 0077
	}
	if before.Uid != uint32(os.Getuid()) || before.Mode&mode != 0 || before.Mode&syscall.S_IFMT != syscall.S_IFREG {
		f.Close()
		return nil, errors.New("binding data is not owner-controlled")
	}
	if before.Size < 0 || before.Size > inputLimit {
		f.Close()
		return nil, errors.New("binding data exceeds limit")
	}
	raw, readErr := io.ReadAll(io.LimitReader(f, inputLimit+1))
	after, statErr := fileStat(f)
	closeErr := f.Close()
	if readErr != nil {
		return nil, readErr
	}
	if statErr != nil {
		return nil, statErr
	}
	if closeErr != nil {
		return nil, closeErr
	}
	if !sameMetadata(before, after) || int64(len(raw)) != before.Size {
		return nil, errors.New("binding data changed during read")
	}
	return raw, nil
}
func decodeData(raw []byte) (*value.Value, error) {
	// Go's reader uses an explicit unlimited integer setting. Interpreter limits
	// are Class C; no originating interpreter profile is inferred or calibrated.
	return value.DecodeText(raw, "utf-8", 0)
}
func checkedConfig(d fileDescriptor, currentPath string, current []byte) (*value.Value, error) {
	raw := current
	if d.Path != currentPath {
		var err error
		raw, err = readData(d.Path, false)
		if err != nil {
			return nil, err
		}
	}
	if digest(raw) != d.SHA256 {
		return nil, errors.New("inbox recall binding changed")
	}
	return decodeData(raw)
}
func selectedConfig(r *compatibilityReceipt, kind, path string) bool {
	if kind == "memory" {
		return path == r.MemoryConfig.Path
	}
	if kind == "coordination" {
		for _, d := range r.CoordinationConfigs {
			if path == d.Path {
				return true
			}
		}
	}
	return false
}
func capturedEnvironment(raw []string) map[string][]rune {
	out := make(map[string][]rune, len(raw))
	for _, s := range raw {
		if k, v, ok := strings.Cut(s, "="); ok {
			out[k] = admission.PathPoints(v)
		}
	}
	return out
}
func sourceHome(env map[string][]rune) ([]rune, error) {
	if home, ok := env["HOME"]; ok {
		return home, nil
	}
	current, err := user.Current()
	if err != nil {
		return nil, err
	}
	return admission.PathPoints(current.HomeDir), nil
}

// Check is the read-only F-01 consumer: code preflight, original admission,
// one checked binding/config snapshot, then actual configuration predicates.
// It performs no token/API/state access, activation, fallback, or Python launch.
func Check(ctx context.Context, in Invocation) (*CheckResult, error) {
	return checkInvocation(ctx, in, nil)
}

func checkInvocation(ctx context.Context, in Invocation, activate func(*value.Value, *value.Value, *value.Value, bool) (bool, error)) (*CheckResult, error) {
	codeFailure := func(err error) error {
		if activate != nil {
			return &ActivationError{Reason: "compatibility_receipt", Class: "CompatibilityPreflightError", Err: err}
		}
		return err
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if in.EntryPoint != "memory" && in.EntryPoint != "coordination" {
		return nil, errors.New("unsupported lifecycle entrypoint")
	}
	if !filepath.IsAbs(in.Directory) {
		return nil, errors.New("originating process cwd required")
	}
	receipt, err := readCompatibilityReceipt(in.ReceiptPath, in.ReceiptSHA256)
	if err != nil {
		return nil, codeFailure(err)
	}
	if !selectedConfig(receipt, in.EntryPoint, in.ConfigPath) {
		return nil, codeFailure(errors.New("configuration differs from selected receipt"))
	}
	if _, err = receipt.checkedClosure(); err != nil {
		return nil, codeFailure(err)
	}
	if err = ValidateBinary(receipt.Binary.Path, receipt.Binary.SHA256); err != nil {
		return nil, codeFailure(err)
	}
	raw, err := readData(in.ConfigPath, false)
	if err != nil {
		return nil, err
	}
	if activate != nil && digest(raw) != in.ConfigSHA256 {
		return nil, errors.New("parent configuration changed")
	}
	config, err := decodeData(raw)
	if err != nil {
		return nil, err
	}
	if config.Kind != value.Object {
		return nil, errors.New("configuration must be an object")
	}
	result := &CheckResult{Disposition: "unbound", Verdict: "skip", ConfigSHA256: digest(raw), Config: config}
	opt := config.Get("inbox_recall_binding")
	if !profileTruthy(opt) {
		return result, nil
	}
	if len(in.Event) > inputLimit {
		return nil, errors.New("hook event exceeds limit")
	}
	event, err := value.DecodeBytes(in.Event, 0)
	if err != nil {
		return nil, err
	}
	result.Event = event
	env := capturedEnvironment(in.Environment)
	probe := admission.OSProbe{Directory: in.Directory}
	decision, err := admission.Decide(admission.Invocation{Kind: "inbox_recall", Config: config, Event: event, Environment: env, Home: func() ([]rune, error) { return sourceHome(env) }, Filesystem: probe})
	if err != nil {
		return nil, err
	}
	result.Verdict, result.Reason = decision.Verdict, decision.Reason
	if !decision.Run() {
		result.Disposition = "excluded"
		return result, nil
	}
	path, err := sourceString(opt)
	if err != nil {
		return nil, err
	}
	manifestRaw, err := readData(path, true)
	if err != nil {
		return nil, err
	}
	manifest, err := decodeData(manifestRaw)
	if err != nil {
		return nil, err
	}
	if manifest.Kind != value.Object {
		return nil, errors.New("binding must be an object")
	}
	schema, err := sourceString(manifest.Get("schema"))
	if err != nil {
		return nil, err
	}
	if schema == "cairn.inbox-recall-binding/2" {
		return nil, ErrLegacyBinding
	}
	enabled := manifest.Get("enabled")
	if schema != binaryBindingSchema || enabled == nil || enabled.Kind != value.Bool {
		return nil, errors.New("unsupported inbox recall binding")
	}
	if path != receipt.Binding.Path || digest(manifestRaw) != receipt.Binding.SHA256 {
		return nil, errors.New("binding differs from selected receipt")
	}
	if activate != nil && digest(manifestRaw) != in.BindingSHA256 {
		return nil, errors.New("parent binding changed")
	}
	binary, err := descriptor(manifest.Get("binary"))
	if err != nil {
		return nil, err
	}
	memory, err := descriptor(manifest.Get("memory_config"))
	if err != nil {
		return nil, err
	}
	coords := manifest.Get("coordination_configs")
	if coords == nil || coords.Kind != value.Array || len(coords.Items) < 1 || len(coords.Items) > 8 {
		return nil, errors.New("one to eight coordination configurations required")
	}
	if binary != receipt.Binary || memory != receipt.MemoryConfig || len(coords.Items) != len(receipt.CoordinationConfigs) {
		return nil, errors.New("mixed binding and receipt selection")
	}
	descriptors := make([]fileDescriptor, len(coords.Items))
	for i, v := range coords.Items {
		d, err := descriptor(v)
		if err != nil {
			return nil, err
		}
		if d != receipt.CoordinationConfigs[i] {
			return nil, errors.New("mixed coordination selection")
		}
		descriptors[i] = d
	}
	if err = ValidateBinary(binary.Path, binary.SHA256); err != nil {
		return nil, codeFailure(err)
	}
	result.BindingSHA256 = digest(manifestRaw)
	effective := enabled.Boolean
	if activate != nil {
		effective, err = activate(config, manifest, event, effective)
		if err != nil {
			return nil, err
		}
	}
	if !effective {
		result.Disposition = "disabled"
		return result, nil
	}
	memoryConfig, err := checkedConfig(memory, in.ConfigPath, raw)
	if err != nil {
		return nil, err
	}
	coordinatorConfigs := make([]*value.Value, len(descriptors))
	for i, d := range descriptors {
		coordinatorConfigs[i], err = checkedConfig(d, in.ConfigPath, raw)
		if err != nil {
			return nil, err
		}
	}
	if err = validateConfigurationProfiles(configurationProfiles{Current: config, Memory: memoryConfig, Coordinators: coordinatorConfigs, ManifestPath: admission.PathPoints(path), Resolve: probe.Resolve}); err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	result.Disposition = "checked"
	result.Coordinators = coordinatorConfigs
	return result, nil
}
