package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"syscall"

	"github.com/halbritt/cairn/core"
)

const maxRecoveryBytes = core.MaxRecoveryBytes

func exportRecovery(ctx context.Context, store *core.Store, path string) (any, error) {
	record, err := store.CaptureRecovery(ctx)
	if err != nil {
		return nil, err
	}
	body, err := json.MarshalIndent(record, "", "  ")
	if err != nil {
		return nil, err
	}
	body = append(body, '\n')
	if len(body) > maxRecoveryBytes {
		return nil, invalid("recovery record exceeds 16 MiB; no file exported")
	}
	if err = writeRecoveryFile(path, body); err != nil {
		return nil, err
	}
	return struct {
		Path        string `json:"path"`
		SHA256      string `json:"sha256"`
		RootGrantID string `json:"root_grant_id"`
	}{path, record.SHA256, record.RootGrantID}, nil
}

// Exports are immutable files. A later capture needs a new path so an older
// database cannot silently overwrite the separately retained expectation.
func writeRecoveryFile(path string, body []byte) (err error) {
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
	file, err := directory.OpenFile(filepath.Base(path), os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, file.Close()) }()
	if _, err = file.Write(body); err != nil {
		return err
	}
	if err = file.Sync(); err != nil {
		return err
	}
	return parent.Sync()
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
	if decoder.Decode(new(any)) != io.EOF {
		return record, invalid("expected exactly one recovery record")
	}
	return record, nil
}

func inspectRecovery(ctx context.Context, store *core.Store, path string) (core.RecoveryInspection, error) {
	record, err := readRecoveryFile(path)
	if err != nil {
		return core.RecoveryInspection{}, err
	}
	report, err := store.InspectRecovery(ctx, record)
	if err == nil && !report.Consistent {
		err = &core.Error{Code: "INTEGRITY_FAILURE", Message: "restored state differs from the separately retained recovery record"}
	}
	return report, err
}
