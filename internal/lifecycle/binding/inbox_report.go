package binding

import (
	"context"
	"errors"
	"path/filepath"

	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// ResolveInboxReport checks the selected code/configuration generation after
// Coordination releases its lock. Reporting does not need an event, admission,
// activation decision or any mutable session state.
func ResolveInboxReport(ctx context.Context, in Invocation) (*InboxInputs, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if in.EntryPoint != "coordination" || !filepath.IsAbs(in.Directory) || !validDigest(in.ConfigSHA256) || !validDigest(in.BindingSHA256) {
		return nil, errors.New("checked inbox report selection required")
	}
	r, err := readCompatibilityReceipt(in.ReceiptPath, in.ReceiptSHA256)
	if err != nil {
		return nil, err
	}
	if !selectedConfig(r, "coordination", in.ConfigPath) {
		return nil, errors.New("configuration differs from selected receipt")
	}
	if _, err = r.checkedClosure(); err != nil {
		return nil, err
	}
	if err = ValidateBinary(r.Binary.Path, r.Binary.SHA256); err != nil {
		return nil, err
	}
	raw, err := readData(in.ConfigPath, false)
	if err != nil {
		return nil, err
	}
	if digest(raw) != in.ConfigSHA256 {
		return nil, errors.New("parent configuration changed")
	}
	config, err := decodeData(raw)
	if err != nil {
		return nil, err
	}
	if config.Kind != value.Object {
		return nil, errors.New("configuration must be an object")
	}
	path, err := sourceString(config.Get("inbox_recall_binding"))
	if err != nil {
		return nil, err
	}
	manifestRaw, err := readData(path, true)
	if err != nil {
		return nil, err
	}
	if path != r.Binding.Path || digest(manifestRaw) != r.Binding.SHA256 || digest(manifestRaw) != in.BindingSHA256 {
		return nil, errors.New("binding differs from selected receipt")
	}
	manifest, err := decodeData(manifestRaw)
	if err != nil {
		return nil, err
	}
	if !value.SameText(manifest.Get("schema"), binaryBindingSchema) || manifest.Get("enabled") == nil || manifest.Get("enabled").Kind != value.Bool {
		return nil, errors.New("unsupported inbox recall binding")
	}
	binary, err := descriptor(manifest.Get("binary"))
	if err != nil {
		return nil, err
	}
	memoryDescriptor, err := descriptor(manifest.Get("memory_config"))
	if err != nil {
		return nil, err
	}
	coords := manifest.Get("coordination_configs")
	if binary != r.Binary || memoryDescriptor != r.MemoryConfig || coords == nil || coords.Kind != value.Array || len(coords.Items) != len(r.CoordinationConfigs) {
		return nil, errors.New("mixed binding and receipt selection")
	}
	memory, err := checkedConfig(memoryDescriptor, "", nil)
	if err != nil {
		return nil, err
	}
	coordinators := make([]*value.Value, len(coords.Items))
	for i, item := range coords.Items {
		d, err := descriptor(item)
		if err != nil {
			return nil, err
		}
		if d != r.CoordinationConfigs[i] {
			return nil, errors.New("mixed coordination selection")
		}
		coordinators[i], err = checkedConfig(d, in.ConfigPath, raw)
		if err != nil {
			return nil, err
		}
	}
	probe := admission.OSProbe{Directory: in.Directory}
	if err = validateConfigurationProfiles(configurationProfiles{Current: config, Memory: memory, Coordinators: coordinators, ManifestPath: admission.PathPoints(path), Resolve: probe.Resolve}); err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	return &InboxInputs{Config: config, Memory: memory}, nil
}
