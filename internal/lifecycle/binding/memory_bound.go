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
	// Revalidate the effective generation without resolving activation again.
	// Cached true decisions still require final pins under a disabled manifest.
	checked, err := checkInvocation(ctx, in, func(_, _, _ *value.Value, _ bool) (bool, error) {
		return enabled, nil
	})
	if err != nil {
		return nil, err
	}
	return &MemoryInvocation{checked: checked, directory: directory, environment: in.Environment}, nil
}

func (i *MemoryInvocation) Inputs() (*CheckResult, string, []string, error) {
	if i == nil || i.checked == nil {
		return nil, "", nil, errors.New("resolved memory invocation required")
	}
	return i.checked, i.directory, i.environment, nil
}
