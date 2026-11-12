package binding

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"syscall"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// OrdinarySelection is a receipt selection from the checked launch frame. It
// grants no authority by itself; the ordinary process also verifies H/V/L/D.
type OrdinarySelection struct {
	ReceiptPath, ReceiptSHA256   string
	Role, Entrypoint, ConfigPath string
	ImagePath                    string
}

// OrdinaryDataError retains the Source phase for controlled data failures.
type OrdinaryDataError struct {
	Phase string
	Err   error
}

func (e *OrdinaryDataError) Error() string { return e.Err.Error() }
func (e *OrdinaryDataError) Unwrap() error { return e.Err }

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
	snapshot, err := VerifyUnboundSelection(ctx, in)
	if err != nil {
		return nil, err
	}
	defer func() {
		if err != nil {
			_ = snapshot.Close()
		}
	}()
	memory := snapshot.Config
	harness, mode, fallback := memory.Get("harness"), memory.Get("recall_mode"), memory.Get("semantic_fallback")
	agentTools := (value.SameText(harness, "opencode") || value.SameText(harness, "claude") || value.SameText(harness, "hermes")) && value.SameText(mode, "agent_tools")
	ambient := value.SameText(harness, "opencode") && (mode == nil || value.SameText(mode, "ambient")) && (fallback == nil || fallback.Kind == value.Bool && !fallback.Boolean)
	if !agentTools && !ambient {
		return nil, errors.New("ordinary checked cell remains unavailable")
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	return snapshot, nil
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

func ordinaryDataFailure(phase string, err error) error {
	var path *os.PathError
	var number syscall.Errno
	var source *value.Error
	if !errors.As(err, &path) && !errors.As(err, &number) && !errors.As(err, &source) {
		err = &value.Error{Class: "ValueError", Message: "invalid controlled data"}
	}
	return &OrdinaryDataError{Phase: phase, Err: err}
}
