package install

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"reflect"

	"golang.org/x/sys/unix"
)

type SettingsRecovery struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}

type settingsExchangeRecord struct {
	Schema         string           `json:"schema"`
	Target         string           `json:"target"`
	Candidate      string           `json:"candidate"`
	Original       SettingsMetadata `json:"original"`
	Prepared       SettingsMetadata `json:"prepared"`
	CtimeException string           `json:"ctime_exception"`
}

type SettingsExchangeResult struct {
	State          string           `json:"state"`
	Committed      bool             `json:"committed"`
	Recovery       SettingsRecovery `json:"recovery"`
	Target         SettingsMetadata `json:"target"`
	CtimeException string           `json:"ctime_exception"`
}

// SettingsExchange is a filesystem transaction, not native admission. The
// public native controller owns the actual guard and must retain it throughout.
// Recovery records can be inspected, but never reconstruct this live object.
type SettingsExchange struct {
	origin    *SettingsExchange
	original  *SettingsSnapshot
	candidate *SettingsSnapshot
	record    *SettingsSnapshot
	selection SettingsRecovery
	data      settingsExchangeRecord
	reversing bool
}

func (*SettingsExchange) MarshalJSON() ([]byte, error) {
	return nil, errors.New("settings exchange is process-local; retain recovery observations")
}
func (*SettingsExchange) UnmarshalJSON([]byte) error {
	return errors.New("serialized recovery cannot mint an exchange")
}

// PrepareSettingsExchange creates only new candidate/recovery objects, keeping
// the original named inode untouched. Call under the native guard: creating
// recovery state is already a mutation. Partial new objects are retained.
func PrepareSettingsExchange(ctx context.Context, original *SettingsSnapshot, raw []byte) (_ *SettingsExchange, err error) {
	if original == nil {
		return nil, errors.New("measured settings preimage required")
	}
	if err := original.Recheck(ctx); err != nil {
		return nil, err
	}
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return nil, err
	}
	name := ".cairn-native-" + hex.EncodeToString(nonce[:])
	parentPath := original.file.Root
	target := filepath.Join(parentPath, original.file.Relative)
	candidatePath := filepath.Join(parentPath, name+".candidate")
	recordPath := filepath.Join(parentPath, name+".json")
	t := &SettingsExchange{original: original}
	t.origin = t
	t.candidate, err = CreateSettings(ctx, original, raw, candidatePath)
	if err != nil {
		return t, errors.Join(err, errors.New("retain native candidate "+candidatePath))
	}
	t.data = settingsExchangeRecord{Schema: "cairn.native-settings-recovery/1", Target: target, Candidate: candidatePath,
		Original: original.Metadata(), Prepared: t.candidate.Metadata(), CtimeException: "ctime cannot be restored; original inode/bytes/uid/gid/mode/mtime/ACL/xattrs are required"}
	encoded, err := json.Marshal(t.data)
	if err != nil || len(encoded) > agySettingsLimit {
		return t, errors.Join(err, errors.New("native recovery metadata exceeds bound; candidate retained"))
	}
	parent, err := openDirectory(parentPath, false)
	if err != nil {
		return t, err
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	fd, err := unix.Openat(int(parent.Fd()), filepath.Base(recordPath), unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0600)
	if err != nil {
		return t, err
	}
	f := os.NewFile(uintptr(fd), recordPath)
	n, writeErr := f.Write(encoded)
	if writeErr == nil && n != len(encoded) {
		writeErr = io.ErrShortWrite
	}
	err = errors.Join(writeErr, f.Sync(), f.Close(), parent.Sync())
	if err != nil {
		return t, errors.Join(err, errors.New("retain native recovery "+recordPath))
	}
	t.record, err = InspectSettings(ctx, recordPath)
	if err != nil {
		return t, err
	}
	t.selection = SettingsRecovery{recordPath, agyDigest(encoded)}
	return t, nil
}

func matchingSettings(actual *SettingsSnapshot, expected SettingsMetadata) bool {
	return actual != nil && reflect.DeepEqual(actual.Metadata(), expected)
}

func inspectSettingsExchange(ctx context.Context, selected SettingsRecovery) (SettingsExchangeResult, *SettingsSnapshot, *SettingsSnapshot, error) {
	result := SettingsExchangeResult{State: "unknown", Recovery: selected}
	record, err := InspectSettings(ctx, selected.Path)
	if err != nil || !record.Metadata().Present || record.Metadata().SHA256 != selected.SHA256 {
		return result, nil, nil, errors.Join(err, errors.New("native recovery record differs"))
	}
	if err := agyStrictJSON(record.Bytes()); err != nil {
		return result, nil, nil, err
	}
	var data settingsExchangeRecord
	if err := json.Unmarshal(record.Bytes(), &data); err != nil {
		return result, nil, nil, err
	}
	root := filepath.Dir(selected.Path)
	if data.Schema != "cairn.native-settings-recovery/1" || !canonical(data.Target) || !canonical(data.Candidate) ||
		filepath.Dir(data.Target) != root || filepath.Dir(data.Candidate) != root || data.Target == data.Candidate || data.Target == selected.Path || data.Candidate == selected.Path {
		return result, nil, nil, errors.New("native recovery paths/schema differ")
	}
	target, err := InspectSettings(ctx, data.Target)
	if err != nil {
		return result, nil, nil, err
	}
	candidate, err := InspectSettings(ctx, data.Candidate)
	if err != nil {
		return result, nil, nil, err
	}
	result.Target, result.CtimeException = target.Metadata(), data.CtimeException
	switch {
	case matchingSettings(target, data.Original) && matchingSettings(candidate, data.Prepared):
		result.State = "prepared"
	case matchingSettings(target, data.Prepared) && (!data.Original.Present && !candidate.Metadata().Present || matchingSettings(candidate, data.Original)):
		result.State, result.Committed = "published", true
	case matchingSettings(target, data.Original) && !candidate.Metadata().Present:
		result.State = "reversed"
	default:
		return result, target, candidate, errors.New("native settings/recovery drift; retain all objects")
	}
	return result, target, candidate, errors.Join(record.Recheck(ctx), target.Recheck(ctx), candidate.Recheck(ctx))
}

// InspectSettingsExchange is pure: it never locks, creates, replays or repairs.
func InspectSettingsExchange(ctx context.Context, selected SettingsRecovery) (SettingsExchangeResult, error) {
	result, _, _, err := inspectSettingsExchange(ctx, selected)
	return result, err
}

func (t *SettingsExchange) Publish(ctx context.Context, guard func(context.Context) error) (result SettingsExchangeResult, err error) {
	if t == nil || t.origin != t || t.record == nil || guard == nil {
		return result, errors.New("live exchange and actual caller guard required")
	}
	result, target, candidate, err := inspectSettingsExchange(ctx, t.selection)
	if err != nil {
		return result, err
	}
	if result.State == "published" {
		return result, nil
	}
	if result.State != "prepared" {
		return result, errors.New("native exchange is not prepared")
	}
	if err := guard(ctx); err != nil {
		return result, err
	}
	parent, err := openDirectory(filepath.Dir(t.data.Target), false)
	if err != nil {
		return result, err
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	if err := errors.Join(target.Recheck(ctx), candidate.Recheck(ctx), t.record.Recheck(ctx), guard(ctx)); err != nil {
		return result, err
	}
	flags := uint(unix.RENAME_EXCHANGE)
	if !t.data.Original.Present {
		flags = unix.RENAME_NOREPLACE
	}
	err = unix.Renameat2(int(parent.Fd()), filepath.Base(t.data.Candidate), int(parent.Fd()), filepath.Base(t.data.Target), flags)
	if err != nil {
		return result, err
	}
	result.State, result.Committed, result.Target = "published-unverified", true, SettingsMetadata{}
	if err := parent.Sync(); err != nil {
		return result, err
	}
	verified, err := InspectSettingsExchange(ctx, t.selection)
	if err != nil {
		return result, err
	}
	return verified, nil
}

func (t *SettingsExchange) Reverse(ctx context.Context, guard func(context.Context) error) (result SettingsExchangeResult, err error) {
	if t == nil || t.origin != t || t.record == nil || guard == nil {
		return result, errors.New("live exchange and actual caller guard required")
	}
	result, target, candidate, err := inspectSettingsExchange(ctx, t.selection)
	if err != nil {
		return result, err
	}
	if result.State == "reversed" {
		return result, nil
	}
	if result.State != "published" && !(t.reversing && result.State == "prepared") {
		return result, errors.New("native exchange is not published")
	}
	if err := guard(ctx); err != nil {
		return result, err
	}
	parent, err := openDirectory(filepath.Dir(t.data.Target), false)
	if err != nil {
		return result, err
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	if err := errors.Join(target.Recheck(ctx), candidate.Recheck(ctx), t.record.Recheck(ctx), guard(ctx)); err != nil {
		return result, err
	}
	if t.data.Original.Present {
		if !t.reversing {
			if err := unix.Renameat2(int(parent.Fd()), filepath.Base(t.data.Candidate), int(parent.Fd()), filepath.Base(t.data.Target), unix.RENAME_EXCHANGE); err != nil {
				return result, err
			}
			t.reversing = true
			result.State, result.Committed, result.Target = "inverse-unverified", true, SettingsMetadata{}
		}
		restored, err := InspectSettings(ctx, t.data.Target)
		if err != nil || !matchingSettings(restored, t.data.Original) {
			return result, errors.Join(err, errors.New("native inverse committed but preimage readback differs; retain recovery"))
		}
		candidate, err = InspectSettings(ctx, t.data.Candidate)
		if err != nil || !matchingSettings(candidate, t.data.Prepared) {
			return result, errors.Join(err, errors.New("native inverse candidate drift; retain recovery"))
		}
		if err := candidate.Recheck(ctx); err != nil {
			return result, err
		}
		if err := unix.Unlinkat(int(parent.Fd()), filepath.Base(t.data.Candidate), 0); err != nil {
			return result, err
		}
	} else {
		if err := unix.Unlinkat(int(parent.Fd()), filepath.Base(t.data.Target), 0); err != nil {
			return result, err
		}
		result.State, result.Committed, result.Target = "inverse-unverified", true, SettingsMetadata{}
	}
	if err := parent.Sync(); err != nil {
		return result, err
	}
	verified, err := InspectSettingsExchange(ctx, t.selection)
	if err != nil {
		return result, err
	}
	return verified, nil
}

// Snapshot returns the current named target after a checked committed step.
func (t *SettingsExchange) Snapshot(ctx context.Context) (*SettingsSnapshot, error) {
	if t == nil || t.origin != t {
		return nil, errors.New("live exchange required")
	}
	return InspectSettings(ctx, t.data.Target)
}

func (t *SettingsExchange) Recovery() SettingsRecovery {
	if t == nil || t.origin != t {
		return SettingsRecovery{}
	}
	return t.selection
}

func (t *SettingsExchange) Resume(ctx context.Context, guard func(context.Context) error) (SettingsExchangeResult, error) {
	if t == nil || t.origin != t {
		return SettingsExchangeResult{}, errors.New("live exchange required")
	}
	if t.reversing {
		return t.Reverse(ctx, guard)
	}
	return t.Publish(ctx, guard)
}
