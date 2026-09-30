package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"

	"github.com/halbritt/cairn/core"
)

const maxRecoveryBytes = core.MaxRecoveryBytes

type exportedSegment struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
	Index  int    `json:"index"`
}

// exportedRecovery keeps the single-file fields unchanged. A segment set names
// its files instead, and every one of them must be retained.
type exportedRecovery struct {
	Path        string            `json:"path,omitempty"`
	SHA256      string            `json:"sha256,omitempty"`
	RootGrantID string            `json:"root_grant_id"`
	SetID       string            `json:"set_id,omitempty"`
	Segments    []exportedSegment `json:"segments,omitempty"`
}

// recoverySegmentPaths names one file for a single record. A segment set inserts
// its position before the extension: after.json becomes after.part-0001-of-0003.json.
func recoverySegmentPaths(path string, count int) []string {
	if count == 1 {
		return []string{path}
	}
	extension := filepath.Ext(path)
	stem := strings.TrimSuffix(path, extension)
	width := max(4, len(strconv.Itoa(count)))
	paths := make([]string, count)
	for i := range paths {
		paths[i] = fmt.Sprintf("%s.part-%0*d-of-%0*d%s", stem, width, i+1, width, count, extension)
	}
	return paths
}

// Export a complete set on success. Ordinary errors clean up this call's files;
// interrupted processes can leave partial sets which inspection refuses.
func exportRecovery(ctx context.Context, store *core.Store, path string) (any, error) {
	set, err := store.CaptureRecoverySet(ctx)
	if err != nil {
		return nil, err
	}
	return writeRecoverySet(path, set)
}

// Keep only one encoded segment in memory. A failed ordinary write removes and
// synchronizes the files created by this call; a crash may leave a detectable
// incomplete set, which inspection refuses rather than treating as complete.
func writeRecoverySet(path string, set []core.RecoveryRecord) (any, error) {
	if len(set) == 0 {
		return nil, invalid("empty recovery export")
	}
	paths := recoverySegmentPaths(path, len(set))
	for i, record := range set {
		body, err := json.MarshalIndent(record, "", "  ")
		if err == nil {
			body = append(body, '\n')
			if len(body) > maxRecoveryBytes {
				err = invalid("recovery record exceeds 16 MiB; incomplete export removed")
			}
		}
		if err == nil {
			err = writeRecoveryFile(paths[i], body)
		}
		if err != nil {
			for _, created := range paths[:i] {
				err = errors.Join(err, removeRecoveryFile(created))
			}
			return nil, err
		}
	}
	if len(set) == 1 {
		return exportedRecovery{Path: path, SHA256: set[0].SHA256, RootGrantID: set[0].RootGrantID}, nil
	}
	result := exportedRecovery{RootGrantID: set[0].RootGrantID, SetID: set[0].Segment.SetID}
	for i, record := range set {
		result.Segments = append(result.Segments, exportedSegment{Path: paths[i], SHA256: record.SHA256, Index: i + 1})
	}
	return result, nil
}

func removeRecoveryFile(path string) (err error) {
	directory, err := os.OpenRoot(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, directory.Close()) }()
	parent, err := directory.Open(".")
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	if err = directory.Remove(filepath.Base(path)); err != nil {
		return err
	}
	return parent.Sync()
}

// Exports are immutable files. A later capture needs a new path so an older
// database cannot silently overwrite the separately retained expectation.
func writeRecoveryFile(path string, body []byte) error {
	return writeRecoveryFileWith(path, body, (*os.File).Write)
}

func writeRecoveryFileWith(path string, body []byte, write func(*os.File, []byte) (int, error)) (err error) {
	directory, err := os.OpenRoot(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, directory.Close()) }()
	parent, err := directory.Open(".")
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	name := filepath.Base(path)
	file, err := directory.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return err
	}
	written, writeErr := write(file, body)
	if writeErr == nil && written != len(body) {
		writeErr = io.ErrShortWrite
	}
	if writeErr == nil {
		writeErr = file.Sync()
	}
	writeErr = errors.Join(writeErr, file.Close())
	if writeErr == nil {
		writeErr = parent.Sync()
	}
	if writeErr != nil {
		cleanupErr := directory.Remove(name)
		if cleanupErr == nil {
			cleanupErr = parent.Sync()
		}
		return errors.Join(writeErr, cleanupErr)
	}
	return nil
}

func readRecoveryFile(path string) (record core.RecoveryRecord, err error) {
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return record, err
	}
	defer func() { err = errors.Join(err, file.Close()) }()
	info, err := file.Stat()
	if err != nil {
		return record, err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || stat.Uid != uint32(os.Geteuid()) {
		return record, invalid("recovery record must be an owner-only regular file owned by this operator")
	}
	body, err := io.ReadAll(io.LimitReader(file, maxRecoveryBytes+1))
	if err != nil {
		return record, err
	}
	if len(body) > maxRecoveryBytes {
		return record, invalid("recovery record exceeds 16 MiB")
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&record); err != nil {
		return record, invalid("invalid recovery record JSON")
	}
	if err := decoder.Decode(new(any)); !errors.Is(err, io.EOF) {
		return record, invalid("expected exactly one recovery record")
	}
	return record, nil
}

type inspectedRecovery struct {
	Consistent bool                      `json:"consistent"`
	Segments   []core.RecoveryInspection `json:"segments"`
}

// inspectRecovery checks one record, or every segment file of one export. A file
// list that names a segment set but omits a position is refused before any
// segment is inspected, so a partial list cannot look consistent.
func inspectRecovery(ctx context.Context, store *core.Store, paths []string) (any, error) {
	records := make([]core.RecoveryRecord, len(paths))
	for i, path := range paths {
		record, err := readRecoveryFile(path)
		if err != nil {
			return nil, err
		}
		records[i] = record
	}
	if err := core.CheckRecoverySegments(records); err != nil {
		return nil, err
	}
	mismatch := &core.Error{Code: "INTEGRITY_FAILURE", Message: "restored state differs from the separately retained recovery record"}
	if len(records) == 1 {
		report, err := store.InspectRecovery(ctx, records[0])
		if err == nil && !report.Consistent {
			err = mismatch
		}
		return report, err
	}
	result := inspectedRecovery{Consistent: true, Segments: []core.RecoveryInspection{}}
	for _, record := range records {
		report, err := store.InspectRecovery(ctx, record)
		if err != nil {
			return nil, err
		}
		result.Segments = append(result.Segments, report)
		result.Consistent = result.Consistent && report.Consistent
	}
	if !result.Consistent {
		return result, mismatch
	}
	return result, nil
}
