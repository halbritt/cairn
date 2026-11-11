package binding

import (
	"context"
	"errors"
	"os"

	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// MemoryEntry retains the checked code selection and the current configuration
// snapshot. Binding data is read by Source admission, rather than code preflight.
type MemoryEntry struct {
	selection OrdinarySelection
	receipt   *compatibilityReceipt
	config    *value.Value
	raw       []byte
	image     *os.File
	ordinary  *OrdinarySnapshot
}

// VerifyMemoryEntry accepts the actual checked host selection. Unbound profiles
// retain VerifyOrdinary's complete disabled-generation contract.
func VerifyMemoryEntry(ctx context.Context, in OrdinarySelection) (_ *MemoryEntry, err error) {
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	r, err := readCompatibilityReceipt(in.ReceiptPath, in.ReceiptSHA256)
	if err != nil {
		return nil, err
	}
	if in.Role != "memory" || !selectedConfig(r, "memory", in.ConfigPath) || (in.Entrypoint != "memory.py" && r.Aliases[in.Entrypoint] != "memory.py") {
		return nil, errors.New("memory selection differs from receipt")
	}
	if _, err = r.checkedClosure(); err != nil {
		return nil, err
	}
	if in.ImagePath != r.Binary.Path {
		return nil, errors.New("actual argv image differs from receipt")
	}
	image, err := ordinaryExecutingImage(r.Binary)
	if err != nil {
		return nil, err
	}
	defer func() {
		if err != nil {
			_ = image.Close()
		}
	}()
	raw, err := readData(in.ConfigPath, false)
	if err != nil {
		return nil, ordinaryDataFailure("configuration", err)
	}
	config, err := decodeData(raw)
	if err != nil {
		return nil, ordinaryDataFailure("configuration", err)
	}
	entry := &MemoryEntry{selection: in, receipt: r, config: config, raw: raw, image: image}
	if config.Kind != value.Object || !profileTruthy(config.Get("inbox_recall_binding")) {
		entry.ordinary, err = VerifyOrdinary(ctx, in)
		if err != nil {
			return nil, err
		}
		entry.config, entry.raw = entry.ordinary.Config, entry.ordinary.RawConfig
	}
	return entry, nil
}

func (e *MemoryEntry) Close() error {
	if e.ordinary != nil {
		defer e.ordinary.Close()
	}
	return e.image.Close()
}

func (e *MemoryEntry) Config() *value.Value { return e.config }
func (e *MemoryEntry) Bound() bool          { return e.ordinary == nil }

// MemoryInvocation can only acquire checked inputs through Resolve. A caller's
// config, activation boolean or native identity is not a substitute for it.
type MemoryInvocation struct {
	entry       *MemoryEntry
	checked     *CheckResult
	directory   string
	environment []string
}

func (e *MemoryEntry) Resolve(ctx context.Context, raw []byte, directory string, environment []string) (*MemoryInvocation, error) {
	if !e.Bound() {
		return nil, errors.New("bound memory selection required")
	}
	in := admission.HookInvocation{
		EntryPoint: "memory", ConfigPath: e.selection.ConfigPath,
		ReceiptPath: e.selection.ReceiptPath, ReceiptSHA256: e.selection.ReceiptSHA256,
		ConfigSHA256: digest(e.raw), BindingSHA256: e.receipt.Binding.SHA256,
		Event: raw, Directory: directory, Environment: append([]string(nil), environment...),
	}
	// This is the same sole writer used by the retained frontend. Its admission
	// also excludes capture before binding-data access, without a second writer.
	enabled, err := ResolveActivation(ctx, in)
	if err != nil {
		return nil, err
	}
	event, err := value.DecodeBytes(raw, 0)
	if err != nil {
		return nil, err
	}
	checked := &CheckResult{Config: e.config, Event: event, Disposition: "disabled"}
	if enabled {
		// The retained caller's final checked_binding uses the cached parent
		// config. Do not reopen it, rerun admission or decide activation again.
		checked.Coordinators, err = e.effectiveProfiles(directory)
		if err != nil {
			return nil, err
		}
		checked.Disposition = "checked"
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	return &MemoryInvocation{entry: e, checked: checked, directory: directory, environment: in.Environment}, nil
}

// Recheck retains the Source frontend's post-dispatch check. A failure cannot
// refund a completed grant or turn the body's committed work into output.
func (i *MemoryInvocation) Recheck() error {
	if i == nil || i.entry == nil || i.checked == nil {
		return errors.New("resolved memory invocation required")
	}
	if i.checked.Disposition != "checked" {
		return nil
	}
	_, err := i.entry.effectiveProfiles(i.directory)
	return err
}

func (e *MemoryEntry) effectiveProfiles(directory string) ([]*value.Value, error) {
	path := e.receipt.Binding.Path
	raw, err := readData(path, true)
	if err != nil {
		return nil, err
	}
	if digest(raw) != e.receipt.Binding.SHA256 {
		return nil, errors.New("inbox recall binding changed")
	}
	manifest, err := decodeData(raw)
	if err != nil {
		return nil, err
	}
	if manifest.Kind != value.Object || !value.SameText(manifest.Get("schema"), binaryBindingSchema) || manifest.Get("enabled") == nil || manifest.Get("enabled").Kind != value.Bool {
		return nil, errors.New("unsupported inbox recall binding")
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
	if binary != e.receipt.Binary || memory != e.receipt.MemoryConfig || coords == nil || coords.Kind != value.Array || len(coords.Items) != len(e.receipt.CoordinationConfigs) {
		return nil, errors.New("mixed binding and receipt selection")
	}
	// A cached true activation still needs every final profile pin, even when
	// the selected manifest now says false. Reuse the original parent bytes.
	memoryConfig, err := checkedConfig(memory, e.selection.ConfigPath, e.raw)
	if err != nil {
		return nil, err
	}
	coordinators := make([]*value.Value, len(coords.Items))
	for i, item := range coords.Items {
		d, err := descriptor(item)
		if err != nil {
			return nil, err
		}
		if d != e.receipt.CoordinationConfigs[i] {
			return nil, errors.New("mixed coordination selection")
		}
		coordinators[i], err = checkedConfig(d, e.selection.ConfigPath, e.raw)
		if err != nil {
			return nil, err
		}
	}
	probe := admission.OSProbe{Directory: directory}
	if err = validateConfigurationProfiles(configurationProfiles{Current: e.config, Memory: memoryConfig, Coordinators: coordinators, ManifestPath: admission.PathPoints(path), Resolve: probe.Resolve}); err != nil {
		return nil, err
	}
	return coordinators, nil
}

func (i *MemoryInvocation) Inputs() (*CheckResult, string, []string, error) {
	if i == nil || i.checked == nil {
		return nil, "", nil, errors.New("resolved memory invocation required")
	}
	return i.checked, i.directory, i.environment, nil
}
