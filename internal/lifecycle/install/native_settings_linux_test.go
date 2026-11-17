package install

import (
	"bytes"
	"context"
	"encoding/binary"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"golang.org/x/sys/unix"
)

func setNativeACL(t *testing.T, path string) {
	t.Helper()
	raw := make([]byte, 4+5*8)
	binary.LittleEndian.PutUint32(raw, 2)
	for i, entry := range [][3]uint32{{1, 6, ^uint32(0)}, {2, 4, 65534}, {4, 4, ^uint32(0)}, {16, 4, ^uint32(0)}, {32, 0, ^uint32(0)}} {
		offset := 4 + 8*i
		binary.LittleEndian.PutUint16(raw[offset:], uint16(entry[0]))
		binary.LittleEndian.PutUint16(raw[offset+2:], uint16(entry[1]))
		binary.LittleEndian.PutUint32(raw[offset+4:], entry[2])
	}
	if err := unix.Setxattr(path, "system.posix_acl_access", raw, 0); err != nil {
		t.Fatal(err)
	}
}

func TestNativeSettingsReadIsPureAndRefusesByteAndAttributeDrift(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "settings.json")
	before, err := os.ReadDir(root)
	if err != nil {
		t.Fatal(err)
	}
	absent, err := InspectSettings(ctx, path)
	if err != nil || absent.Metadata().Present {
		t.Fatal(absent, err)
	}
	after, err := os.ReadDir(root)
	if err != nil || len(before) != len(after) {
		t.Fatal("inspection created state", err)
	}
	if err := os.WriteFile(path, []byte("{\"keep\":true}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := absent.Recheck(ctx); err == nil {
		t.Fatal("absence observation survived creation")
	}
	if err := unix.Setxattr(path, "user.cairn-test", []byte("before"), 0); err != nil {
		t.Fatal(err)
	}
	snapshot, err := InspectSettings(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	metadata := snapshot.Metadata()
	metadata.Xattrs["user.cairn-test"][0] = 'x'
	if err := snapshot.Recheck(ctx); err != nil {
		t.Fatal("mutable exported metadata affected retained observation", err)
	}
	if err := unix.Setxattr(path, "user.cairn-test", []byte("after!"), 0); err != nil {
		t.Fatal(err)
	}
	if err := snapshot.Recheck(ctx); err == nil {
		t.Fatal("attribute mutation accepted")
	}
	snapshot, err = InspectSettings(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("{\"keep\":true} "), 0600); err != nil {
		t.Fatal(err)
	}
	if err := snapshot.Recheck(ctx); err == nil {
		t.Fatal("same-length byte mutation accepted")
	}
}

func TestNativeSettingsCopyPreservesRepresentableMetadataAndNeverOverwrites(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(root, "original.json")
	target := filepath.Join(root, "copy.json")
	raw := []byte(" {\"unrelated\":900719925474099312345} \n")
	if err := os.WriteFile(source, raw, 0640); err != nil {
		t.Fatal(err)
	}
	stamp := time.Unix(1750000000, 123456789)
	if err := os.Chtimes(source, stamp, stamp); err != nil {
		t.Fatal(err)
	}
	if err := unix.Setxattr(source, "user.cairn-test", []byte("preserve"), 0); err != nil {
		t.Fatal(err)
	}
	setNativeACL(t, source)
	original, err := InspectSettings(ctx, source)
	if err != nil {
		t.Fatal(err)
	}
	copy, err := CopySettings(ctx, original, target)
	if err != nil {
		t.Fatal(err)
	}
	a, b := original.Metadata(), copy.Metadata()
	if a.Inode == b.Inode || !bytes.Equal(copy.Bytes(), raw) || a.Mode != b.Mode || a.UID != b.UID || a.GID != b.GID || a.Mtime != b.Mtime || !reflect.DeepEqual(a.Xattrs, b.Xattrs) {
		t.Fatal("copy lost source correspondence", a, b)
	}
	if _, err := CopySettings(ctx, original, target); err == nil {
		t.Fatal("copy overwrote an existing object")
	}
	if err := copy.Recheck(ctx); err != nil {
		t.Fatal("refused overwrite changed target", err)
	}
	if err := os.Chmod(source, 0440); err != nil {
		t.Fatal(err)
	}
	readOnly, err := InspectSettings(ctx, source)
	if err != nil {
		t.Fatal(err)
	}
	readOnlyCopy, err := CopySettings(ctx, readOnly, filepath.Join(root, "readonly-copy.json"))
	if err != nil || readOnlyCopy.Metadata().Mode != readOnly.Metadata().Mode || !reflect.DeepEqual(readOnlyCopy.Metadata().Xattrs, readOnly.Metadata().Xattrs) {
		t.Fatal("read-only source attributes were not copied", err)
	}
}
