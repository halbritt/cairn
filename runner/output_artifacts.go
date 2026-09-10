package runner

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

const maxOutputArtifactBytes int64 = 64 * 1024 * 1024

// OutputArtifact selects a host file. Only Label, never Path or file contents,
// enters the captured fingerprint evidence.
type OutputArtifact struct {
	Label string
	Path  string
}

type ArtifactFingerprint struct {
	Label  string `json:"label"`
	SHA256 string `json:"sha256"`
	Bytes  int64  `json:"bytes"`
}

type ArtifactManifest struct {
	Schema       string                `json:"schema"`
	ReceiptID    string                `json:"receipt_id"`
	OutcomeID    string                `json:"outcome_id"`
	ProcessState string                `json:"process_state"`
	ObservedAt   time.Time             `json:"observed_at"`
	Files        []ArtifactFingerprint `json:"files"`
	Meaning      string                `json:"meaning"`
}

func prepareOutputArtifacts(directory string, outputs []OutputArtifact, share bool) ([]OutputArtifact, error) {
	if len(outputs) > 16 || (share && len(outputs) == 0) {
		return nil, &core.Error{Code: "INVALID_REQUEST", Message: "select 1-16 artifacts before sharing their fingerprint evidence"}
	}
	if len(outputs) == 0 {
		return nil, nil
	}
	base, err := filepath.Abs(directory)
	if err != nil {
		return nil, err
	}
	prepared := slices.Clone(outputs)
	seen := map[string]bool{}
	for i, output := range prepared {
		if output.Label == "" || len(output.Label) > 128 || strings.Trim(output.Label, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-") != "" || seen[output.Label] ||
			strings.TrimSpace(output.Path) == "" || len(output.Path) > 4096 || strings.ContainsRune(output.Path, 0) {
			return nil, &core.Error{Code: "INVALID_REQUEST", Message: "artifacts require distinct 1-128 character alphanumeric/_.- labels and bounded nonempty paths"}
		}
		seen[output.Label] = true
		if !filepath.IsAbs(output.Path) {
			prepared[i].Path = filepath.Join(base, output.Path)
		}
	}
	slices.SortFunc(prepared, func(a, b OutputArtifact) int { return strings.Compare(a.Label, b.Label) })
	return prepared, nil
}

func fingerprintOutput(ctx context.Context, output OutputArtifact, remaining int64) (ArtifactFingerprint, error) {
	file, err := os.OpenFile(output.Path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return ArtifactFingerprint{}, err
	}
	defer file.Close()
	before, err := file.Stat()
	if err != nil {
		return ArtifactFingerprint{}, err
	}
	if !before.Mode().IsRegular() || before.Size() > remaining {
		return ArtifactFingerprint{}, fmt.Errorf("requires a regular file within the remaining %d-byte allowance", remaining)
	}
	digest := sha256.New()
	reader := io.LimitReader(file, remaining+1)
	buffer := make([]byte, 32*1024)
	var size int64
	for {
		if err := ctx.Err(); err != nil {
			return ArtifactFingerprint{}, err
		}
		n, err := reader.Read(buffer)
		if n > 0 {
			digest.Write(buffer[:n])
			size += int64(n)
		}
		if err == io.EOF {
			break
		}
		if err != nil {
			return ArtifactFingerprint{}, err
		}
	}
	after, err := file.Stat()
	if err != nil {
		return ArtifactFingerprint{}, err
	}
	if size > remaining || size != before.Size() || size != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return ArtifactFingerprint{}, fmt.Errorf("file changed during reading or exceeded its byte allowance")
	}
	return ArtifactFingerprint{Label: output.Label, SHA256: hex.EncodeToString(digest.Sum(nil)), Bytes: size}, nil
}

func captureOutputArtifacts(store Store, result Result, repo string, outputs []OutputArtifact, share bool) (core.Evidence, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	manifest := ArtifactManifest{Schema: "cairn.run-artifact-fingerprints/1", ReceiptID: result.ReceiptID,
		OutcomeID: result.OutcomeID, ProcessState: result.ProcessState, ObservedAt: time.Now().UTC(),
		Meaning: "Host-read fingerprints after the observed process stopped. Files may predate the run or be modified by other writers. No creation, correctness, task acceptance or atomic filesystem snapshot is established."}
	remaining := maxOutputArtifactBytes
	for _, output := range outputs {
		fingerprint, err := fingerprintOutput(ctx, output, remaining)
		if err != nil {
			return core.Evidence{}, &core.Error{Code: "ARTIFACT_EVIDENCE_FAILED", Message: "cannot fingerprint selected artifact " + output.Label, Cause: err}
		}
		manifest.Files = append(manifest.Files, fingerprint)
		remaining -= fingerprint.Bytes
	}
	body, err := json.Marshal(manifest)
	if err != nil {
		return core.Evidence{}, err
	}
	sensitivity := "local"
	if share {
		sensitivity = "shareable"
	}
	request := core.EvidenceRequest{RequestID: uuid.NewString(), Repo: repo, Body: string(body),
		Source: "cairn-run-artifact-fingerprints:" + result.ReceiptID, Sensitivity: sensitivity}
	encoded, err := json.MarshalIndent(request, "", "  ")
	if err != nil {
		return core.Evidence{}, err
	}
	pending := filepath.Join(result.Artifacts, "artifact-evidence.pending.json")
	if err = os.WriteFile(pending, encoded, 0600); err != nil {
		return core.Evidence{}, err
	}
	evidence, err := store.CaptureEvidence(ctx, request)
	if err != nil {
		return evidence, fmt.Errorf("artifact evidence unconfirmed; retry the exact request at %s: %w", pending, err)
	}
	return evidence, os.Rename(pending, filepath.Join(result.Artifacts, "artifact-evidence.json"))
}
