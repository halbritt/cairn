package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash"
	"io"
	"os"
	"path/filepath"
	"syscall"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"golang.org/x/sys/unix"
)

const recoveryBundleSchema = "cairn.recovery-bundle/1"
const recoveryManifestName = "manifest.jsonl"

type recoveryBundleHeader struct {
	Schema      string `json:"schema"`
	RootGrantID string `json:"root_grant_id"`
	SetID       string `json:"set_id,omitempty"`
	Count       int    `json:"count"`
}
type recoveryBundlePart struct {
	Index        int    `json:"index"`
	File         string `json:"file"`
	SHA256       string `json:"sha256"`
	RecordSHA256 string `json:"record_sha256"`
}
type recoveryBundleDigest struct {
	SHA256 string `json:"sha256"`
}
type recoveryBundleResult struct {
	Directory      string `json:"directory"`
	ManifestSHA256 string `json:"manifest_sha256"`
	core.RecoveryExport
}

func recoveryPartName(index int) string { return fmt.Sprintf("part-%09d.json", index) }

func exportRecoveryDirectory(ctx context.Context, store *core.Store, path string) (any, error) {
	return writeRecoveryDirectory(path, func(emit func(core.RecoveryRecord) error) (core.RecoveryExport, error) {
		return store.StreamRecovery(ctx, emit)
	})
}

// Only a completed stream is published. Rename without replacement preserves
// an existing destination, including an empty directory. Crashes can leave the
// distinct private .cairn-recovery-staging-* directory, never a partial target.
func writeRecoveryDirectory(path string, produce func(func(core.RecoveryRecord) error) (core.RecoveryExport, error)) (result recoveryBundleResult, err error) {
	path = filepath.Clean(path)
	parentPath, name := filepath.Dir(path), filepath.Base(path)
	if name == "." || name == ".." || name == string(filepath.Separator) {
		return result, invalid("recovery bundle needs a new directory name")
	}
	parent, err := os.Open(parentPath)
	if err != nil {
		return result, err
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	staging, err := os.MkdirTemp(parentPath, ".cairn-recovery-staging-")
	if err != nil {
		return result, err
	}
	published := false
	defer func() {
		if !published {
			err = errors.Join(err, os.RemoveAll(staging))
		}
	}()
	manifest, err := os.OpenFile(filepath.Join(staging, recoveryManifestName), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return result, err
	}
	defer func() {
		if manifest != nil {
			err = errors.Join(err, manifest.Close())
		}
	}()
	digest := sha256.New()
	writer := io.MultiWriter(manifest, digest)
	header := recoveryBundleHeader{Schema: recoveryBundleSchema}
	written := 0
	summary, err := produce(func(record core.RecoveryRecord) error {
		if written == 0 {
			header.RootGrantID = record.RootGrantID
			header.Count = 1
			if record.Segment != nil {
				header.SetID = record.Segment.SetID
				header.Count = record.Segment.Count
			}
			if err := validateBundleHeader(header); err != nil {
				return err
			}
			if err := json.NewEncoder(writer).Encode(header); err != nil {
				return err
			}
		}
		index := written + 1
		if err := checkBundlePosition(header, index, record); err != nil {
			return err
		}
		body, err := json.MarshalIndent(record, "", "  ")
		if err != nil {
			return err
		}
		body = append(body, '\n')
		if len(body) > maxRecoveryBytes {
			return invalid("one recovery segment exceeds 16 MiB")
		}
		filename := recoveryPartName(index)
		if err = writeRecoveryFile(filepath.Join(staging, filename), body); err != nil {
			return err
		}
		sum := sha256.Sum256(body)
		if err = json.NewEncoder(writer).Encode(recoveryBundlePart{index, filename, hex.EncodeToString(sum[:]), record.SHA256}); err != nil {
			return err
		}
		written++
		return nil
	})
	if err != nil {
		return result, err
	}
	if written == 0 || written != header.Count || summary.Count != written || summary.RootGrantID != header.RootGrantID || summary.SetID != header.SetID {
		return result, invalid("recovery stream ended without the declared complete set")
	}
	checksum := hex.EncodeToString(digest.Sum(nil))
	if err = json.NewEncoder(manifest).Encode(recoveryBundleDigest{checksum}); err != nil {
		return result, err
	}
	if err = manifest.Sync(); err != nil {
		return result, err
	}
	err = manifest.Close()
	manifest = nil
	if err != nil {
		return result, err
	}
	directory, err := os.Open(staging)
	if err != nil {
		return result, err
	}
	err = errors.Join(directory.Sync(), directory.Close())
	if err != nil {
		return result, err
	}
	if err = unix.Renameat2(int(parent.Fd()), filepath.Base(staging), int(parent.Fd()), name, unix.RENAME_NOREPLACE); err != nil {
		return result, err
	}
	published = true
	result = recoveryBundleResult{Directory: path, ManifestSHA256: checksum, RecoveryExport: summary}
	return result, parent.Sync()
}

func validateBundleHeader(h recoveryBundleHeader) error {
	if h.Schema != recoveryBundleSchema || h.Count < 1 || h.Count > core.MaxRecoverySegments {
		return invalid("unsupported recovery bundle header")
	}
	if _, err := uuid.Parse(h.RootGrantID); err != nil {
		return invalid("invalid recovery bundle root")
	}
	if h.Count == 1 {
		if h.SetID != "" {
			return invalid("one-record bundle must be unsegmented")
		}
	} else if _, err := uuid.Parse(h.SetID); err != nil {
		return invalid("invalid recovery bundle set identity")
	}
	return nil
}
func checkBundlePosition(h recoveryBundleHeader, index int, r core.RecoveryRecord) error {
	if r.RootGrantID != h.RootGrantID || index > h.Count {
		return invalid("recovery bundle root or count mismatch")
	}
	if h.Count == 1 {
		if r.Segment != nil {
			return invalid("unexpected segment metadata in one-record bundle")
		}
	} else if r.Segment == nil || r.Segment.SetID != h.SetID || r.Segment.Index != index || r.Segment.Count != h.Count {
		return invalid("recovery bundle segment position mismatch")
	}
	return nil
}

// The manifest is JSON Lines with a fixed-size header, one descriptor per part,
// and a final checksum over exact preceding lines including their newlines.
// A 4096-byte line bound prevents hostile scalar/header allocation; descriptors
// are consumed one at a time and may name only their exact generated basename.
type recoveryBundleReader struct {
	root           *os.Root
	file           *os.File
	scanner        *bufio.Scanner
	header         recoveryBundleHeader
	digest         hash.Hash
	index          int
	finished       bool
	manifestSHA256 string
}

func openRecoveryBundle(path string) (reader *recoveryBundleReader, err error) {
	root, err := os.OpenRoot(path)
	if err != nil {
		return nil, err
	}
	defer func() {
		if err != nil {
			err = errors.Join(err, root.Close())
		}
	}()
	info, err := root.Stat(".")
	if err != nil {
		return nil, err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.IsDir() || info.Mode().Perm()&0077 != 0 || stat.Uid != uint32(os.Geteuid()) {
		return nil, invalid("recovery bundle must be an owner-only directory owned by this operator")
	}
	file, err := root.OpenFile(recoveryManifestName, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	defer func() {
		if err != nil {
			err = errors.Join(err, file.Close())
		}
	}()
	if err = privateRecoveryFile(file); err != nil {
		return nil, err
	}
	reader = &recoveryBundleReader{root: root, file: file, digest: sha256.New(), scanner: bufio.NewScanner(file)}
	reader.scanner.Buffer(make([]byte, 4096), 4096)
	reader.scanner.Split(recoveryManifestLine)
	if err = reader.line(&reader.header, true); err != nil {
		return nil, err
	}
	if err = validateBundleHeader(reader.header); err != nil {
		return nil, err
	}
	return reader, nil
}
func (r *recoveryBundleReader) Close() error { return errors.Join(r.file.Close(), r.root.Close()) }

// Keep the exact newline bytes in each token. ScanLines would normalize CRLF
// and accept an unterminated last line, weakening the manifest byte checksum.
func recoveryManifestLine(data []byte, atEOF bool) (int, []byte, error) {
	if end := bytes.IndexByte(data, '\n'); end >= 0 {
		return end + 1, data[:end+1], nil
	}
	if atEOF && len(data) != 0 {
		return 0, nil, invalid("unterminated recovery bundle manifest line")
	}
	return 0, nil, nil
}
func (r *recoveryBundleReader) line(value any, hashed bool) error {
	if !r.scanner.Scan() {
		if err := r.scanner.Err(); err != nil {
			return err
		}
		return invalid("incomplete recovery bundle manifest")
	}
	body := r.scanner.Bytes()
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		return invalid("invalid recovery bundle manifest line")
	}
	if err := decoder.Decode(new(any)); !errors.Is(err, io.EOF) {
		return invalid("multiple values on recovery bundle manifest line")
	}
	if hashed {
		r.digest.Write(body)
	}
	return nil
}
func (r *recoveryBundleReader) Next() (core.RecoveryRecord, bool, error) {
	var record core.RecoveryRecord
	if r.finished {
		return record, false, nil
	}
	if r.index == r.header.Count {
		var trailer recoveryBundleDigest
		if err := r.line(&trailer, false); err != nil {
			return record, false, err
		}
		expected := hex.EncodeToString(r.digest.Sum(nil))
		if trailer.SHA256 != expected {
			return record, false, invalid("recovery bundle manifest checksum mismatch")
		}
		if r.scanner.Scan() {
			return record, false, invalid("extra records after recovery bundle checksum")
		}
		if err := r.scanner.Err(); err != nil {
			return record, false, err
		}
		r.manifestSHA256 = expected
		r.finished = true
		return record, false, nil
	}
	var part recoveryBundlePart
	if err := r.line(&part, true); err != nil {
		return record, false, err
	}
	index := r.index + 1
	if part.Index != index || part.File != recoveryPartName(index) {
		return record, false, invalid("recovery bundle has an invalid path or position")
	}
	file, err := r.root.OpenFile(part.File, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return record, false, err
	}
	var fileDigest string
	record, fileDigest, err = readRecoveryOpenedWithDigest(file)
	if err != nil {
		return record, false, err
	}
	if err = checkBundlePosition(r.header, index, record); err != nil {
		return record, false, err
	}
	if part.SHA256 != fileDigest || part.RecordSHA256 != record.SHA256 {
		return record, false, invalid("recovery bundle part checksum differs from manifest")
	}
	r.index = index
	return record, true, nil
}
func inspectRecoveryDirectory(ctx context.Context, store *core.Store, path string) (result any, err error) {
	reader, err := openRecoveryBundle(path)
	if err != nil {
		return nil, err
	}
	defer func() { err = errors.Join(err, reader.Close()) }()
	inspection, err := store.InspectRecoveryStream(ctx, reader.Next)
	if err != nil {
		return nil, err
	}
	result = struct {
		Directory      string                  `json:"directory"`
		ManifestSHA256 string                  `json:"manifest_sha256"`
		Inspection     core.RecoveryInspection `json:"inspection"`
	}{path, reader.manifestSHA256, inspection}
	if !inspection.Consistent {
		err = &core.Error{Code: "INTEGRITY_FAILURE", Message: "restored state differs from the complete recovery bundle"}
	}
	return result, err
}
