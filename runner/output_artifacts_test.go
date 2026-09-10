package runner

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func artifactRunRequest(t *testing.T) Request {
	t.Helper()
	return Request{Compile: core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: uuid.NewString(), TaskID: "build", RunID: "build-1"},
		Purpose: "context", AvailableTokens: 32000}, Destination: core.Destination{Name: "local", AllowLocal: true},
		Command: []string{"/bin/true"}, Directory: t.TempDir(), Carrier: "stdin", Timeout: 5 * time.Second, ArtifactDirectory: t.TempDir()}
}

func TestSelectedOutputFingerprintCanSupportSeparateAssessment(t *testing.T) {
	s := runStore(t)
	for _, share := range []bool{false, true} {
		req := artifactRunRequest(t)
		body := []byte("OUTPUT-CONTENT-" + uuid.NewString() + "\x00\xff")
		if err := os.WriteFile(filepath.Join(req.Directory, "source.bin"), body, 0600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(req.Directory, "output.bin"), []byte("old output"), 0600); err != nil {
			t.Fatal(err)
		}
		req.Command = []string{"/bin/cp", "source.bin", "output.bin"}
		req.OutputArtifacts = []OutputArtifact{{Label: "build-output", Path: "output.bin"}}
		req.ShareArtifactEvidence = share
		result, err := Run(context.Background(), s, req, io.Discard, io.Discard)
		if err != nil || result.ArtifactEvidence == nil || result.ArtifactEvidence.Witness != "instrumented" {
			t.Fatalf("artifact run: %+v %v", result, err)
		}
		doc, err := s.ReadEvidence(context.Background(), result.ArtifactEvidence.ID)
		if err != nil {
			t.Fatal(err)
		}
		var manifest ArtifactManifest
		if err = json.Unmarshal([]byte(doc.Body), &manifest); err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256(body)
		if manifest.ReceiptID != result.ReceiptID || manifest.OutcomeID != result.OutcomeID || manifest.ProcessState != "exited" ||
			len(manifest.Files) != 1 || manifest.Files[0] != (ArtifactFingerprint{"build-output", hex.EncodeToString(digest[:]), int64(len(body))}) {
			t.Fatalf("fingerprint lost file or run identity: %+v", manifest)
		}
		if strings.Contains(doc.Body, "OUTPUT-CONTENT-") || strings.Contains(doc.Body, req.Directory) || strings.Contains(doc.Body, "output.bin") ||
			(doc.Sensitivity == "shareable") != share {
			t.Fatalf("file contents/path or sharing boundary lost: %+v", doc)
		}
		report, err := s.RunReport(context.Background(), core.RunReportRequest{Repo: req.Compile.Scope.Repo, Limit: 10})
		if err != nil || len(report.Rows) != 1 || report.Rows[0].TaskOutcome != "unknown" {
			t.Fatalf("fingerprint inferred acceptance: %+v %v", report, err)
		}
		_, err = s.AssessRun(context.Background(), core.AssessmentRequest{RequestID: uuid.NewString(), ReceiptID: result.ReceiptID,
			TaskOutcome: "accepted", FailureDomain: "none", Method: "exact-output-fixture/1", EvidenceIDs: []string{doc.ID},
			Reason: "Fixture reviewer independently compared output bytes with the required source; fingerprint identifies the inspected artifact."})
		if err != nil {
			t.Fatal(err)
		}
		requestBody, err := os.ReadFile(filepath.Join(result.Artifacts, "artifact-evidence.json"))
		if err != nil {
			t.Fatal(err)
		}
		var capture core.EvidenceRequest
		if err = json.Unmarshal(requestBody, &capture); err != nil {
			t.Fatal(err)
		}
		retry, err := s.CaptureEvidence(context.Background(), capture)
		if err != nil || retry.ID != doc.ID {
			t.Fatalf("manifest retry duplicated evidence: %+v %v", retry, err)
		}
	}
}

func TestArtifactReadFailuresPreserveObservedProcess(t *testing.T) {
	s := runStore(t)
	for _, name := range []string{"missing", "directory", "symlink", "fifo", "oversized", "aggregate"} {
		t.Run(name, func(t *testing.T) {
			req := artifactRunRequest(t)
			path := filepath.Join(req.Directory, name)
			var err error
			switch name {
			case "directory":
				err = os.Mkdir(path, 0700)
			case "symlink":
				err = os.Symlink("/dev/null", path)
			case "fifo":
				err = syscall.Mkfifo(path, 0600)
			case "oversized":
				err = os.WriteFile(path, nil, 0600)
				if err == nil {
					err = os.Truncate(path, maxOutputArtifactBytes+1)
				}
			case "aggregate":
				err = os.WriteFile(path, nil, 0600)
				if err == nil {
					err = os.Truncate(path, maxOutputArtifactBytes/2+1)
				}
			}
			if err != nil {
				t.Fatal(err)
			}
			req.OutputArtifacts = []OutputArtifact{{Label: name, Path: path}}
			if name == "aggregate" {
				// Two explicit selections each fit, but together exceed the run limit.
				req.OutputArtifacts = append(req.OutputArtifacts, OutputArtifact{Label: "second", Path: path})
			}
			result, err := Run(context.Background(), s, req, io.Discard, io.Discard)
			if core.Code(err) != "ARTIFACT_EVIDENCE_FAILED" || result.ProcessState != "exited" || result.ExitCode == nil || *result.ExitCode != 0 || result.OutcomeID == "" || result.ArtifactEvidence != nil {
				t.Fatalf("artifact failure overwrote process observation: %+v %v", result, err)
			}
			if _, err = os.Stat(filepath.Join(result.Artifacts, "artifact-evidence.pending.json")); !errors.Is(err, os.ErrNotExist) {
				t.Fatalf("incomplete manifest retained as capture-ready: %v", err)
			}
		})
	}
}

type lostArtifactResponse struct {
	*core.Store
	committed *core.Evidence
}

func (s lostArtifactResponse) CaptureEvidence(ctx context.Context, req core.EvidenceRequest) (core.Evidence, error) {
	evidence, err := s.Store.CaptureEvidence(ctx, req)
	if err != nil {
		return core.Evidence{}, err
	}
	*s.committed = evidence
	return core.Evidence{}, errors.New("synthetic lost capture response")
}

func TestArtifactCaptureAmbiguityRetainsExactRequest(t *testing.T) {
	s := runStore(t)
	req := artifactRunRequest(t)
	path := filepath.Join(req.Directory, "output")
	if err := os.WriteFile(path, []byte("already-existing artifact"), 0600); err != nil {
		t.Fatal(err)
	}
	req.OutputArtifacts = []OutputArtifact{{Label: "existing", Path: path}}
	var committed core.Evidence
	result, err := Run(context.Background(), lostArtifactResponse{s, &committed}, req, io.Discard, io.Discard)
	if err == nil || result.OutcomeID == "" || result.ArtifactEvidence != nil {
		t.Fatalf("ambiguous capture reported success: %+v %v", result, err)
	}
	body, err := os.ReadFile(filepath.Join(result.Artifacts, "artifact-evidence.pending.json"))
	if err != nil {
		t.Fatal(err)
	}
	var reqCapture core.EvidenceRequest
	if err = json.Unmarshal(body, &reqCapture); err != nil {
		t.Fatal(err)
	}
	first, err := s.CaptureEvidence(context.Background(), reqCapture)
	if err != nil {
		t.Fatal(err)
	}
	again, err := s.CaptureEvidence(context.Background(), reqCapture)
	if err != nil || first.ID != again.ID || first.ID != committed.ID {
		t.Fatalf("recovery duplicated evidence: %+v %v", again, err)
	}
}

func TestArtifactSelectionRejectsInvalidIntentBeforeRetrieval(t *testing.T) {
	for _, outputs := range [][]OutputArtifact{
		{{Label: "", Path: "output"}}, {{Label: "file", Path: ""}}, {{Label: "bad/name", Path: "output"}},
		{{Label: "same", Path: "a"}, {Label: "same", Path: "b"}}, make([]OutputArtifact, 17),
	} {
		req := artifactRunRequest(t)
		req.OutputArtifacts = outputs
		if _, err := Run(context.Background(), nil, req, io.Discard, io.Discard); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("invalid selection reached retrieval: %v", err)
		}
	}
	req := artifactRunRequest(t)
	req.ShareArtifactEvidence = true
	if _, err := Run(context.Background(), nil, req, io.Discard, io.Discard); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("sharing without selection reached retrieval: %v", err)
	}
}

func TestFailedCommandStillCapturesSelectedExistingArtifact(t *testing.T) {
	s := runStore(t)
	req := artifactRunRequest(t)
	req.Command = []string{"/bin/false"}
	req.OutputArtifacts = []OutputArtifact{{Label: "existing", Path: "empty-file"}}
	if err := os.WriteFile(filepath.Join(req.Directory, "empty-file"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	result, err := Run(context.Background(), s, req, io.Discard, io.Discard)
	if err != nil || result.ExitCode == nil || *result.ExitCode != 1 || result.ArtifactEvidence == nil {
		t.Fatalf("failed process lost artifact evidence: %+v %v", result, err)
	}
	doc, err := s.ReadEvidence(context.Background(), result.ArtifactEvidence.ID)
	if err != nil {
		t.Fatal(err)
	}
	var manifest ArtifactManifest
	if err := json.Unmarshal([]byte(doc.Body), &manifest); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(nil)
	if len(manifest.Files) != 1 || manifest.Files[0].Bytes != 0 || manifest.Files[0].SHA256 != hex.EncodeToString(digest[:]) {
		t.Fatalf("empty pre-existing file lost its identity: %+v", manifest)
	}
}
