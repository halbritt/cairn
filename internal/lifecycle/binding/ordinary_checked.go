package binding

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// OrdinarySelection is a receipt selection from the checked launch frame. It
// grants no authority by itself; the ordinary process also verifies H/V/L/D.
type OrdinarySelection struct {
	ReceiptPath, ReceiptSHA256   string
	Role, Entrypoint, ConfigPath string
	ImagePath                    string
}

// OrdinarySnapshot owns the exact selected config and retained executing image.
// The caller must close it after the complete ordinary invocation.
type OrdinarySnapshot struct {
	Config    *value.Value
	RawConfig []byte
	image     *os.File
}

func (s *OrdinarySnapshot) Close() error { return s.image.Close() }

// VerifyOrdinary checks the complete immutable selection before config decode
// or any recall/state effect. It does not resolve activation, strip a binding,
// or reuse the bound consumer's disabled-staging exception.
func VerifyOrdinary(ctx context.Context, in OrdinarySelection) (_ *OrdinarySnapshot, err error) {
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
		return nil, err
	}
	if digest(raw) != r.Binding.SHA256 {
		return nil, errors.New("binding differs from receipt")
	}
	manifest, err := decodeData(raw)
	if err != nil {
		return nil, err
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
			return nil, err
		}
		if digest(configs[i]) != d.SHA256 {
			return nil, errors.New("ordinary profile changed")
		}
	}
	var memory *value.Value
	for i, raw := range configs {
		cfg, e := decodeData(raw)
		if e != nil {
			return nil, e
		}
		if cfg.Kind != value.Object || profileTruthy(cfg.Get("inbox_recall_binding")) {
			return nil, errors.New("all selected profiles must be unbound")
		}
		if i == 0 {
			memory = cfg
		}
	}
	if !value.SameText(memory.Get("harness"), "opencode") || !value.SameText(memory.Get("recall_mode"), "agent_tools") {
		return nil, errors.New("ordinary checked cell remains unavailable")
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	return &OrdinarySnapshot{Config: memory, RawConfig: configs[0], image: image}, nil
}

func ordinaryExecutingImage(d fileDescriptor) (*os.File, error) {
	image, err := openBinary(d.Path)
	if err != nil {
		return nil, err
	}
	keep := false
	defer func() {
		if !keep {
			_ = image.Close()
		}
	}()
	before, err := fileStat(image)
	if err != nil {
		return nil, err
	}
	if err = ValidateBinary(d.Path, d.SHA256); err != nil {
		return nil, err
	}
	running, err := os.Open("/proc/self/exe")
	if err != nil {
		return nil, err
	}
	defer running.Close()
	current, err := fileStat(running)
	if err != nil {
		return nil, err
	}
	if !sameMetadata(before, current) {
		return nil, errors.New("inherited descriptor is not executing image")
	}
	raw := io.NewSectionReader(image, 0, before.Size)
	sum := sha256.New()
	_, err = io.Copy(sum, raw)
	hash := hex.EncodeToString(sum.Sum(nil))
	if err != nil {
		return nil, err
	}
	after, err := fileStat(image)
	if err != nil {
		return nil, err
	}
	if !sameMetadata(before, after) || hash != d.SHA256 {
		return nil, errors.New("inherited executable changed")
	}
	keep = true
	return image, nil
}

// VerifyOrdinaryLauncher checks the actual Go V's independently installed L.
func VerifyOrdinaryLauncher(path string, raw []byte) error {
	f, err := openArtifact(path)
	if err != nil {
		return err
	}
	defer f.Close()
	before, err := fileStat(f)
	if err != nil {
		return err
	}
	if before.Mode&07777 != 0500 || before.Size > 65536 {
		return errors.New("invalid selected launcher")
	}
	actual, err := io.ReadAll(io.LimitReader(f, 65537))
	if err != nil {
		return err
	}
	after, err := fileStat(f)
	if err != nil {
		return err
	}
	if !sameMetadata(before, after) || int64(len(actual)) != before.Size || !bytes.Equal(raw, actual) {
		return errors.New("selected launcher changed")
	}
	return nil
}
