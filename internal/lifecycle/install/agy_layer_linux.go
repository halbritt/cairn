package install

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"unicode/utf8"

	"golang.org/x/sys/unix"
)

// AgyLayerSettings selects an already prepared settings file by exact bytes.
// Only its cairn-coordination group is installed; unrelated existing bytes stay.
type AgyLayerSettings struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}

// AgyLayerPlan names both independent roots and exactly one publishing layer.
// The CLI supplies the roots through explicit flags, never HOME discovery.
type AgyLayerPlan struct {
	AccountHome     string
	Workspace       string
	Layer           string
	Settings        AgyLayerSettings
	ReplaceCommands []string
}

type AgyLayerSelection struct {
	Path    string `json:"path"`
	SHA256  string `json:"sha256"`
	Layer   string `json:"layer"`
	Changed bool   `json:"changed"`
}

// PublishAgyLayer checks both explicit layers and publishes only the selected
// Cairn group. Final read/rename/readback is not a transaction with legacy
// writers: callers must retain that qualification limit, not infer a barrier.
func PublishAgyLayer(ctx context.Context, plan AgyLayerPlan) (AgyLayerSelection, error) {
	return publishAgyLayer(ctx, plan, nil)
}

func publishAgyLayer(ctx context.Context, plan AgyLayerPlan, observe func(string) error) (result AgyLayerSelection, err error) {
	if plan.Layer != "account" && plan.Layer != "project" || !utf8.ValidString(plan.AccountHome) || !utf8.ValidString(plan.Workspace) ||
		!canonical(plan.AccountHome) || !canonical(plan.Workspace) || !canonical(plan.Settings.Path) {
		return result, errors.New("agy-layer requires explicit canonical account/workspace roots, pinned settings and account|project layer")
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	account, err := inspectAgyLayer(plan.AccountHome, ".gemini/config/hooks.json")
	if err != nil {
		return result, fmt.Errorf("Agy account layer: %w", err)
	}
	project, err := inspectAgyLayer(plan.Workspace, ".agents/hooks.json")
	if err != nil {
		return result, fmt.Errorf("Agy project layer: %w", err)
	}
	if plan.AccountHome != plan.Workspace && account.Directories[plan.AccountHome] == project.Directories[plan.Workspace] {
		return result, errors.New("Agy root anchors alias")
	}
	selected, other := account, project
	if plan.Layer == "project" {
		selected, other = project, account
	}
	if other.Present {
		conflict, err := agyDispatch(other.Raw)
		if err != nil || conflict {
			return result, errors.Join(err, errors.New("Agy nonselected layer conflict: Cairn dispatch or unknown hooks"))
		}
	}
	prepared, err := inspectAgyLayer(filepath.Dir(plan.Settings.Path), filepath.Base(plan.Settings.Path))
	if err != nil || !prepared.Present {
		return result, errors.Join(err, errors.New("Agy prepared settings visibility is unknown"))
	}
	sum := sha256.Sum256(prepared.Raw)
	if hex.EncodeToString(sum[:]) != plan.Settings.SHA256 {
		return result, errors.New("Agy prepared settings SHA-256 differs")
	}
	raw, err := agyMergeSettings(selected.Raw, prepared.Raw, plan.ReplaceCommands)
	if err != nil {
		return result, err
	}
	result = AgyLayerSelection{Path: filepath.Join(selected.Root, selected.Relative), SHA256: agyDigest(raw), Layer: plan.Layer}
	check := func() error { return errors.Join(selected.recheck(), other.recheck(), prepared.recheck(), ctx.Err()) }
	if err := check(); err != nil {
		return AgyLayerSelection{}, err
	}
	if selected.Present && bytes.Equal(raw, selected.Raw) {
		return result, nil
	}
	parent, err := agySelectedParent(selected.Root, selected.Relative)
	if err != nil {
		return AgyLayerSelection{}, err
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	created, err := inspectAgyLayer(selected.Root, selected.Relative)
	if err != nil || selected.Present != created.Present || selected.File != created.File || !bytes.Equal(selected.Raw, created.Raw) {
		return AgyLayerSelection{}, errors.Join(err, errors.New("Agy selected layer changed while opening publication directory"))
	}
	for path, id := range selected.Directories {
		if created.Directories[path] != id {
			return AgyLayerSelection{}, errors.New("Agy selected ancestor changed")
		}
	}
	selected = created
	step := func(name string, action func() error) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if observe != nil {
			if err := observe("before:" + name); err != nil {
				return err
			}
		}
		if err := action(); err != nil {
			return err
		}
		if observe != nil {
			return observe("after:" + name)
		}
		return nil
	}
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return AgyLayerSelection{}, err
	}
	temporary := ".cairn-agy-" + hex.EncodeToString(nonce[:])
	mode := uint32(0600)
	if selected.Present {
		mode = selected.File.Mode
	}
	published := false
	var staged agyFileID
	defer func() {
		if err != nil && published {
			rollback := rollbackAgyLayer(parent, temporary, staged, selected.Present)
			err = errors.Join(err, rollback)
			result = AgyLayerSelection{}
			if rollback != nil {
				err = errors.Join(err, fmt.Errorf("retained Agy recovery member %s", filepath.Join(selected.Root, filepath.Dir(selected.Relative), temporary)))
				return
			}
		}
		if e := unix.Unlinkat(int(parent.Fd()), temporary, 0); e != nil && !errors.Is(e, unix.ENOENT) {
			err = errors.Join(err, e)
		}
	}()
	if err := step("settings-sync", func() error { return agyWriteTemporary(parent, temporary, raw, mode) }); err != nil {
		return AgyLayerSelection{}, err
	}
	_, staged, err = agyReadFile(parent, temporary)
	if err != nil {
		return AgyLayerSelection{}, err
	}
	if selected.Present && staged.GID != selected.File.GID {
		return AgyLayerSelection{}, errors.New("Agy publication would change the selected file group")
	}
	if err := step("publication", func() error {
		if err := check(); err != nil {
			return err
		}
		flags := uint(unix.RENAME_NOREPLACE)
		if selected.Present {
			flags = unix.RENAME_EXCHANGE
		}
		if err := unix.Renameat2(int(parent.Fd()), temporary, int(parent.Fd()), "hooks.json", flags); err != nil {
			return err
		}
		published = true
		if selected.Present {
			old, id, err := agyReadFile(parent, temporary)
			if err != nil || id.Dev != selected.File.Dev || id.Ino != selected.File.Ino || !bytes.Equal(old, selected.Raw) || id.Mode != selected.File.Mode {
				return errors.Join(err, errors.New("Agy selected file changed at publication"))
			}
		}
		return nil
	}); err != nil {
		return AgyLayerSelection{}, err
	}
	if err := step("readback", func() error {
		current, err := inspectAgyLayer(selected.Root, selected.Relative)
		if err != nil || !reflect.DeepEqual(current.Directories, selected.Directories) || current.File.Dev != staged.Dev || current.File.Ino != staged.Ino ||
			current.File.Mode&0777 != mode&0777 || !bytes.Equal(current.Raw, raw) {
			return errors.Join(err, errors.New("Agy selected publication readback changed"))
		}
		return other.recheck()
	}); err != nil {
		return AgyLayerSelection{}, err
	}
	if err := step("directory-sync", parent.Sync); err != nil {
		return AgyLayerSelection{}, err
	}
	if err := other.recheck(); err != nil {
		return AgyLayerSelection{}, err
	}
	result.Changed = true
	return result, nil
}

func rollbackAgyLayer(parent *os.File, temporary string, staged agyFileID, hadOld bool) error {
	_, current, err := agyReadFile(parent, "hooks.json")
	if err != nil || current.Dev != staged.Dev || current.Ino != staged.Ino {
		return errors.Join(err, errors.New("Agy rollback ownership changed; reconcile selected layer before reuse"))
	}
	if hadOld {
		err = unix.Renameat2(int(parent.Fd()), temporary, int(parent.Fd()), "hooks.json", unix.RENAME_EXCHANGE)
	} else {
		err = unix.Unlinkat(int(parent.Fd()), "hooks.json", 0)
	}
	return errors.Join(err, parent.Sync())
}

func agyDigest(raw []byte) string {
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}
