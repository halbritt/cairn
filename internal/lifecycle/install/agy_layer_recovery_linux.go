package install

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"

	"golang.org/x/sys/unix"
)

// AgyLayerRecovery selects one retained correspondence, never a directory scan.
type AgyLayerRecovery struct {
	AccountHome string
	Workspace   string
	Layer       string
	Record      AgyLayerSettings
}

type agyRecoveryLayer struct {
	Root, Relative string
	Directories    map[string]agyDirectoryID
	Present        bool
	File           agyFileID
	SHA256         string
}

type agyRecoveryRecord struct {
	Schema          string
	Layer           string
	Selected, Other agyRecoveryLayer
	Temporary       string
	NewFile         agyFileID
	NewSHA256       string
}

func agyRecoverySnapshot(s agyLayerSnapshot) agyRecoveryLayer {
	return agyRecoveryLayer{s.Root, s.Relative, s.Directories, s.Present, s.File, agyDigest(s.Raw)}
}

// Rename changes ctime on both exchanged inodes. Every other observed identity
// field and the content digest must still match the before-rename pin.
func agyRecoveryFileEqual(a, b agyFileID) bool {
	a.Change, b.Change = unix.Timespec{}, unix.Timespec{}
	return a == b
}

func (r agyRecoveryLayer) matches(s agyLayerSnapshot, renamed bool) bool {
	if r.Root != s.Root || r.Relative != s.Relative || !reflect.DeepEqual(r.Directories, s.Directories) || r.Present != s.Present || r.SHA256 != agyDigest(s.Raw) {
		return false
	}
	if renamed {
		return agyRecoveryFileEqual(r.File, s.File)
	}
	return r.File == s.File
}

func agyRecoveryNames(selected agyLayerSnapshot, requested, nonce string) (string, string, error) {
	parent := filepath.Join(selected.Root, filepath.Dir(selected.Relative))
	name := ".cairn-recovery-agy-" + nonce + ".json"
	if requested != "" {
		if !canonical(requested) || filepath.Dir(requested) != parent {
			return "", "", errors.New("Agy recovery record must be in the exact selected directory")
		}
		name = filepath.Base(requested)
	}
	token := strings.TrimSuffix(strings.TrimPrefix(name, ".cairn-recovery-agy-"), ".json")
	decoded, err := hex.DecodeString(token)
	if err != nil || len(decoded) != 16 || name != ".cairn-recovery-agy-"+hex.EncodeToString(decoded)+".json" {
		return "", "", errors.New("Agy recovery record requires a canonical 32-hex correspondence name")
	}
	return name, ".cairn-agy-" + token, nil
}

func agyRetainRecovery(parent *os.File, name string, selected, other agyLayerSnapshot, layer, temporary string, staged agyFileID, digest string) (AgyLayerSettings, error) {
	r := agyRecoveryRecord{"cairn.agy-layer-recovery/1", layer, agyRecoverySnapshot(selected), agyRecoverySnapshot(other), temporary, staged, digest}
	raw, err := json.Marshal(r)
	if err != nil {
		return AgyLayerSettings{}, err
	}
	if err := agyWriteTemporary(parent, name, raw, 0400); err != nil {
		return AgyLayerSettings{}, err
	}
	// The record and staged inode must both have durable names before selection.
	if err := parent.Sync(); err != nil {
		return AgyLayerSettings{}, err
	}
	return AgyLayerSettings{Path: filepath.Join(selected.Root, filepath.Dir(selected.Relative), name), SHA256: agyDigest(raw)}, nil
}

func agyLoadRecovery(plan AgyLayerRecovery) (agyRecoveryRecord, agyLayerSnapshot, error) {
	var record agyRecoveryRecord
	if plan.Layer != "account" && plan.Layer != "project" || !canonical(plan.AccountHome) || !canonical(plan.Workspace) || !canonical(plan.Record.Path) || len(plan.Record.SHA256) != 64 {
		return record, agyLayerSnapshot{}, errors.New("Agy recovery requires explicit roots, layer and pinned record")
	}
	root, relative := plan.AccountHome, ".gemini/config/hooks.json"
	if plan.Layer == "project" {
		root, relative = plan.Workspace, ".agents/hooks.json"
	}
	selected := agyLayerSnapshot{Root: root, Relative: relative}
	name, temporary, err := agyRecoveryNames(selected, plan.Record.Path, "")
	if err != nil {
		return record, agyLayerSnapshot{}, err
	}
	snapshot, err := inspectAgyLayer(root, filepath.Join(filepath.Dir(relative), name))
	if err != nil || !snapshot.Present || snapshot.File.Mode&07777 != 0400 || agyDigest(snapshot.Raw) != plan.Record.SHA256 {
		return record, snapshot, errors.Join(err, errors.New("Agy recovery record ownership, mode or pin differs"))
	}
	decoder := json.NewDecoder(bytes.NewReader(snapshot.Raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&record); err != nil {
		return record, snapshot, err
	}
	canonicalRecord, err := json.Marshal(record)
	if err != nil || !bytes.Equal(canonicalRecord, snapshot.Raw) || record.Schema != "cairn.agy-layer-recovery/1" || record.Layer != plan.Layer || record.Temporary != temporary || record.Selected.Root != root || record.Selected.Relative != relative {
		return record, snapshot, errors.New("Agy recovery correspondence is unknown or forged")
	}
	otherRoot, otherRelative := plan.Workspace, ".agents/hooks.json"
	if plan.Layer == "project" {
		otherRoot, otherRelative = plan.AccountHome, ".gemini/config/hooks.json"
	}
	if record.Other.Root != otherRoot || record.Other.Relative != otherRelative || len(record.NewSHA256) != 64 || record.NewFile.Ino == 0 || record.NewFile.UID != uint32(os.Geteuid()) {
		return record, snapshot, errors.New("Agy recovery roots or staged identity differ")
	}
	return record, snapshot, nil
}

func agyRecoveryCurrent(record agyRecoveryRecord) (agyLayerSnapshot, agyLayerSnapshot, agyLayerSnapshot, error) {
	selected, err := inspectAgyLayer(record.Selected.Root, record.Selected.Relative)
	if err != nil {
		return selected, agyLayerSnapshot{}, agyLayerSnapshot{}, err
	}
	other, err := inspectAgyLayer(record.Other.Root, record.Other.Relative)
	if err != nil || !record.Other.matches(other, false) {
		return selected, other, agyLayerSnapshot{}, errors.Join(err, errors.New("Agy recovery other layer changed"))
	}
	if other.Present {
		conflict, e := agyDispatch(other.Raw)
		if e != nil || conflict {
			return selected, other, agyLayerSnapshot{}, errors.Join(e, errors.New("Agy recovery other layer has conflicting dispatch"))
		}
	}
	if record.Selected.Root != record.Other.Root && selected.Directories[record.Selected.Root] == other.Directories[record.Other.Root] {
		return selected, other, agyLayerSnapshot{}, errors.New("Agy recovery roots alias")
	}
	if !reflect.DeepEqual(selected.Directories, record.Selected.Directories) {
		return selected, other, agyLayerSnapshot{}, errors.New("Agy recovery selected ancestors changed")
	}
	backup, err := inspectAgyLayer(record.Selected.Root, filepath.Join(filepath.Dir(record.Selected.Relative), record.Temporary))
	return selected, other, backup, err
}

func agyRecoveryNew(record agyRecoveryRecord, current agyLayerSnapshot) bool {
	return current.Present && agyRecoveryFileEqual(current.File, record.NewFile) && agyDigest(current.Raw) == record.NewSHA256
}

func agyRecoveryCommitted(record agyRecoveryRecord, selected, backup agyLayerSnapshot) (bool, error) {
	if record.Selected.Root != selected.Root || record.Selected.Relative != selected.Relative || !reflect.DeepEqual(record.Selected.Directories, selected.Directories) {
		return false, errors.New("Agy recovery selected ancestors changed")
	}
	old := record.Selected.matches(selected, true) && agyRecoveryNew(record, backup)
	committed := agyRecoveryNew(record, selected) && (record.Selected.Present && backup.Present && agyRecoveryFileEqual(record.Selected.File, backup.File) && record.Selected.SHA256 == agyDigest(backup.Raw) ||
		!record.Selected.Present && !backup.Present)
	if old == committed {
		return false, errors.New("Agy recovery selected inode, bytes or backup changed or ambiguous")
	}
	return committed, nil
}

// An intent is durable before this paired read. A later competing publisher
// therefore sees our intent even if it creates its record after this scan.
func agyOtherPublication(plan AgyLayerPlan, other agyLayerSnapshot) (err error) {
	if err := other.recheck(); err != nil {
		return err
	}
	if other.Present {
		conflict, err := agyDispatch(other.Raw)
		if err != nil || conflict {
			return errors.Join(err, errors.New("Agy nonselected layer conflict: Cairn dispatch or unknown hooks"))
		}
	}
	path := filepath.Join(other.Root, filepath.Dir(other.Relative))
	parent, err := openDirectory(path, false)
	if errors.Is(err, unix.ENOENT) {
		return other.recheck()
	}
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	id, err := agyDirectoryIdentity(parent)
	if err != nil || id != other.Directories[path] {
		return errors.Join(err, errors.New("Agy other recovery directory changed"))
	}
	entries, err := parent.ReadDir(-1)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), ".cairn-recovery-agy-") {
			if err := agyOtherRecovery(plan, other, entry.Name()); err != nil {
				return err
			}
		}
	}
	return other.recheck()
}

func agyOtherRecovery(plan AgyLayerPlan, other agyLayerSnapshot, name string) error {
	layer := "account"
	if plan.Layer == "account" {
		layer = "project"
	}
	recordPath := filepath.Join(other.Root, filepath.Dir(other.Relative), name)
	if _, _, err := agyRecoveryNames(other, recordPath, ""); err != nil {
		return err
	}
	observed, err := inspectAgyLayer(other.Root, filepath.Join(filepath.Dir(other.Relative), name))
	if err != nil || !observed.Present {
		return errors.Join(err, errors.New("Agy other recovery record visibility is unknown"))
	}
	record, journal, err := agyLoadRecovery(AgyLayerRecovery{plan.AccountHome, plan.Workspace, layer, AgyLayerSettings{recordPath, agyDigest(observed.Raw)}})
	if err != nil {
		return err
	}
	backup, err := inspectAgyLayer(other.Root, filepath.Join(filepath.Dir(other.Relative), record.Temporary))
	if err != nil {
		return err
	}
	committed, err := agyRecoveryCommitted(record, other, backup)
	if err != nil {
		return err
	}
	if err := errors.Join(journal.recheck(), backup.recheck(), other.recheck()); err != nil {
		return err
	}
	if !committed {
		return fmt.Errorf("Agy other layer has an in-progress recovery record: %s", recordPath)
	}
	return nil
}

// RecoverAgyLayer validates the exact pinned correspondence and finishes a
// committed publication forward. The record and backup remain as evidence.
// Before commit it preserves the old selection and staged new inode.
func RecoverAgyLayer(ctx context.Context, plan AgyLayerRecovery) (selection AgyLayerSelection, err error) {
	record, journal, err := agyLoadRecovery(plan)
	if err != nil {
		return AgyLayerSelection{}, err
	}
	selected, other, backup, err := agyRecoveryCurrent(record)
	if err != nil {
		return AgyLayerSelection{}, err
	}
	committed, err := agyRecoveryCommitted(record, selected, backup)
	if err != nil {
		return AgyLayerSelection{}, err
	}
	check := func() error {
		return errors.Join(ctx.Err(), selected.recheck(), other.recheck(), backup.recheck(), journal.recheck())
	}
	if err := check(); err != nil {
		return AgyLayerSelection{}, err
	}
	selection = AgyLayerSelection{Path: filepath.Join(selected.Root, selected.Relative), Layer: plan.Layer, Recovery: &plan.Record}
	if committed {
		selection.SHA256 = record.NewSHA256
	} else if record.Selected.Present {
		selection.SHA256 = record.Selected.SHA256
	}
	if !committed {
		return selection, nil
	}
	parent, err := openDirectory(filepath.Dir(selection.Path), false)
	if err != nil {
		return AgyLayerSelection{}, err
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	if err := check(); err != nil {
		return AgyLayerSelection{}, err
	}
	if err := parent.Sync(); err != nil {
		return AgyLayerSelection{}, err
	}
	if err := check(); err != nil {
		return AgyLayerSelection{}, err
	}
	return selection, nil
}

func agyRecoveryBackupUnchanged(parent *os.File, temporary string, old agyLayerSnapshot) error {
	raw, id, err := agyReadFile(parent, temporary)
	if !old.Present {
		if errors.Is(err, unix.ENOENT) {
			return nil
		}
		return errors.Join(err, errors.New("Agy rollback absence recovery member changed"))
	}
	if err != nil || !agyRecoveryFileEqual(id, old.File) || !bytes.Equal(raw, old.Raw) {
		return errors.Join(err, errors.New("Agy rollback backup ownership changed; retained correspondence"))
	}
	return nil
}
