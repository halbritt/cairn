package install

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"golang.org/x/sys/unix"
)

func TestNativeExchangeRetainsExactInodeMetadataAndRefusesDrift(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "settings.json")
	raw := []byte(" {\"opaque\":900719925474099312345,\"hooks\":{}} \n")
	if err := os.WriteFile(path, raw, 0640); err != nil {
		t.Fatal(err)
	}
	stamp := time.Unix(1750000000, 123456789)
	if err := os.Chtimes(path, stamp, stamp); err != nil {
		t.Fatal(err)
	}
	if err := unix.Setxattr(path, "user.cairn-test", []byte("retained"), 0); err != nil {
		t.Fatal(err)
	}
	setNativeACL(t, path)
	original, err := InspectSettings(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	prepared, err := PatchSettingsHooks(raw, []byte(`{"opaque":900719925474099312345,"hooks":{"SessionStart":[]}}`))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.HasPrefix(prepared, []byte(" {\"opaque\":900719925474099312345,")) || !bytes.HasSuffix(prepared, []byte(" \n")) {
		t.Fatal("non-hook bytes changed")
	}
	tx, err := PrepareSettingsExchange(ctx, original, prepared)
	if err != nil {
		t.Fatal(err)
	}
	guardCalls := 0
	guard := func(context.Context) error { guardCalls++; return nil }
	before, err := InspectSettingsExchange(ctx, tx.selection)
	if err != nil || before.State != "prepared" {
		t.Fatal(before, err)
	}
	result, err := tx.Publish(ctx, guard)
	if err != nil || result.State != "published" || !result.Committed {
		t.Fatal(result, err)
	}
	if guardCalls < 2 {
		t.Fatal("final guard was omitted")
	}
	if _, err := json.Marshal(tx); err == nil {
		t.Fatal("live exchange was serializable")
	}
	current, err := InspectSettings(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if current.Metadata().Inode == original.Metadata().Inode || !bytes.Equal(current.Bytes(), prepared) {
		t.Fatal("forward image correspondence lost")
	}
	if err := unix.Setxattr(path, "user.cairn-test", []byte("changed!"), 0); err != nil {
		t.Fatal(err)
	}
	if _, err := tx.Reverse(ctx, guard); err == nil {
		t.Fatal("inverse overwrote drift")
	}
	if err := unix.Setxattr(path, "user.cairn-test", []byte("retained"), 0); err != nil {
		t.Fatal(err)
	}
	result, err = tx.Reverse(ctx, guard)
	if err != nil || result.State != "reversed" {
		t.Fatal(result, err)
	}
	restored, err := InspectSettings(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(restored.Metadata(), original.Metadata()) || !bytes.Equal(restored.Bytes(), raw) {
		t.Fatal("exact original metadata/bytes not restored", restored.Metadata(), original.Metadata())
	}
	if _, err := os.Lstat(tx.data.Candidate); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("new candidate absence not restored", err)
	}
}

func TestNativeExchangeRetainsCommittedRenameAfterCancelledReadback(t *testing.T) {
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "settings.json")
	if err := os.WriteFile(path, []byte(`{"hooks":{}}`), 0600); err != nil {
		t.Fatal(err)
	}
	original, err := InspectSettings(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	tx, err := PrepareSettingsExchange(context.Background(), original, []byte(`{"hooks":{"SessionStart":[]}}`))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	calls := 0
	guard := func(context.Context) error {
		calls++
		if calls == 2 {
			cancel()
		}
		return nil
	}
	partial, err := tx.Publish(ctx, guard)
	if err == nil || !partial.Committed || partial.State != "published-unverified" {
		t.Fatal("committed rename was hidden by failed readback", partial, err)
	}
	allow := func(context.Context) error { return nil }
	recovered, err := tx.Resume(context.Background(), allow)
	if err != nil || recovered.State != "published" || !recovered.Committed {
		t.Fatal("pinned recovery lost committed state", recovered, err)
	}
	if _, err := tx.Reverse(context.Background(), allow); err != nil {
		t.Fatal(err)
	}
	restored, err := InspectSettings(context.Background(), path)
	if err != nil || !reflect.DeepEqual(restored.Metadata(), original.Metadata()) {
		t.Fatal("recovery inverse lost original identity", err)
	}
}

func TestNativeExchangePreservesOriginalAbsenceAndGuardRefusal(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "settings.json")
	original, err := InspectSettings(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	tx, err := PrepareSettingsExchange(ctx, original, []byte(`{"hooks":{}}`))
	if err != nil {
		t.Fatal(err)
	}
	refuse := func(context.Context) error { return errors.New("actual admission unavailable") }
	if _, err := tx.Publish(ctx, refuse); err == nil {
		t.Fatal("caller refusal ignored")
	}
	if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("refusal changed original absence", err)
	}
	allow := func(context.Context) error { return nil }
	if _, err := tx.Publish(ctx, allow); err != nil {
		t.Fatal(err)
	}
	if _, err := tx.Reverse(ctx, allow); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("inverse did not restore absence", err)
	}
}
