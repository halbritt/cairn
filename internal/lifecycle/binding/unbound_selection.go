package binding

import (
	"context"
	"errors"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// VerifyUnboundSelection verifies the complete disabled receipt generation and
// retains its executing image. It grants no harness, event or timeout admission;
// the checked consumer must enforce its supported cells before any body effect.
func VerifyUnboundSelection(ctx context.Context, in OrdinarySelection) (_ *OrdinarySnapshot, err error) {
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	if in.Role != "memory" {
		return nil, errors.New("ordinary memory role required")
	}
	r, err := readCompatibilityReceipt(in.ReceiptPath, in.ReceiptSHA256)
	if err != nil {
		return nil, err
	}
	if in.ConfigPath != r.MemoryConfig.Path || (in.Entrypoint != "memory.py" && r.Aliases[in.Entrypoint] != "memory.py") {
		return nil, errors.New("ordinary selection differs from receipt")
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

	raw, err := readData(r.Binding.Path, true)
	if err != nil {
		return nil, ordinaryDataFailure("activation", err)
	}
	if digest(raw) != r.Binding.SHA256 {
		return nil, errors.New("binding differs from receipt")
	}
	manifest, err := decodeData(raw)
	if err != nil {
		return nil, ordinaryDataFailure("activation", err)
	}
	if manifest.Kind != value.Object || !value.SameText(manifest.Get("schema"), binaryBindingSchema) || manifest.Get("enabled") == nil || manifest.Get("enabled").Kind != value.Bool || manifest.Get("enabled").Boolean {
		return nil, errors.New("explicit disabled ordinary generation required")
	}
	checkDescriptor := func(v *value.Value, expected fileDescriptor) bool {
		if v == nil || v.Kind != value.Object || len(v.Members) != 2 {
			return false
		}
		d, e := descriptor(v)
		return e == nil && d == expected
	}
	if !checkDescriptor(manifest.Get("binary"), r.Binary) || !checkDescriptor(manifest.Get("memory_config"), r.MemoryConfig) {
		return nil, errors.New("mixed ordinary receipt descriptors")
	}
	coords := manifest.Get("coordination_configs")
	if coords == nil || coords.Kind != value.Array || len(coords.Items) != len(r.CoordinationConfigs) {
		return nil, errors.New("mixed coordination descriptors")
	}
	for i, d := range r.CoordinationConfigs {
		if !checkDescriptor(coords.Items[i], d) {
			return nil, errors.New("mixed coordination descriptor")
		}
	}
	// Read and pin every member before trusting any profile's unbound claim.
	descriptors := append([]fileDescriptor{r.MemoryConfig}, r.CoordinationConfigs...)
	configs := make([][]byte, len(descriptors))
	for i, d := range descriptors {
		configs[i], err = readData(d.Path, false)
		if err != nil {
			phase := "activation"
			if i == 0 {
				phase = "configuration"
			}
			return nil, ordinaryDataFailure(phase, err)
		}
		if digest(configs[i]) != d.SHA256 {
			// The pin has already failed. Decode only to retain Source's
			// refused-data diagnostic; no rejected value can reach policy.
			if _, decodeErr := decodeData(configs[i]); decodeErr != nil {
				phase := "activation"
				if i == 0 {
					phase = "configuration"
				}
				return nil, ordinaryDataFailure(phase, decodeErr)
			}
			return nil, errors.New("ordinary profile changed")
		}
	}
	var memory *value.Value
	for i, raw := range configs {
		cfg, e := decodeData(raw)
		if e != nil {
			phase := "activation"
			if i == 0 {
				phase = "configuration"
			}
			return nil, ordinaryDataFailure(phase, e)
		}
		if cfg.Kind != value.Object || profileTruthy(cfg.Get("inbox_recall_binding")) {
			return nil, errors.New("all selected profiles must be unbound")
		}
		if i == 0 {
			memory = cfg
		}
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	return &OrdinarySnapshot{Config: memory, RawConfig: configs[0], image: image}, nil
}
