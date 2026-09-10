package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestAgentEvidenceReadsSelectedFileWithoutSendingPath(t *testing.T) {
	path := filepath.Join(t.TempDir(), "local-only-name.bin")
	body := []byte{0, 0xff, 0xfe, '\r', '\n'}
	if err := os.WriteFile(path, body, 0600); err != nil {
		t.Fatal(err)
	}
	requestID := uuid.NewString()
	observed := make(chan core.EvidenceRequest, 1)
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/evidence" || r.Header.Get("Authorization") != "Bearer capture-test-token" {
			t.Error("incorrect capture endpoint/authentication")
		}
		raw, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
			return
		}
		if strings.Contains(string(raw), path) || strings.Contains(string(raw), filepath.Base(path)) {
			t.Error("local path entered request")
		}
		var req core.EvidenceRequest
		if err := json.Unmarshal(raw, &req); err != nil {
			t.Error(err)
		}
		observed <- req
		_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": core.Evidence{ID: uuid.NewString(), Witness: "testimony", State: "resolvable"}})
	})
	_, err := run(context.Background(), append(args, "evidence", "--file", path, "--source", "selected source label", "--repo", "fixture:file", "--request-id", requestID), strings.NewReader("not a JSON request"))
	if err != nil {
		t.Fatal(err)
	}
	got := <-observed
	if got.Body != "" || got.BodyBase64 != base64.StdEncoding.EncodeToString(body) || got.Source != "selected source label" || got.Repo != "fixture:file" || got.RequestID != requestID || got.Sensitivity != "local" {
		t.Fatalf("file capture intent changed: %+v", got)
	}
}

func TestAgentEvidenceRejectsInvalidFileBeforeAPI(t *testing.T) {
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("invalid file capture reached API")
		http.Error(w, "unexpected request", http.StatusBadRequest)
	})
	dir := t.TempDir()
	empty, large, fifo, regular := filepath.Join(dir, "empty"), filepath.Join(dir, "large"), filepath.Join(dir, "fifo"), filepath.Join(dir, "valid")
	for path, body := range map[string][]byte{empty: nil, large: make([]byte, 1048577), regular: []byte("source")} {
		if err := os.WriteFile(path, body, 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := syscall.Mkfifo(fifo, 0600); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{empty, large, fifo, dir, "/dev/null", filepath.Join(dir, "missing"), ""} {
		_, err := run(context.Background(), append(append([]string{}, args...), "evidence", "--file", path, "--source", "source", "--repo", "fixture:file"), strings.NewReader(""))
		if err == nil {
			t.Fatalf("invalid file %q accepted", path)
		}
	}
	for _, extra := range [][]string{{"--source", ""}, {"--source", strings.Repeat("s", 513)}, {"--repo", "*"}, {"--request-id", ""}, {"--request-id", uuid.Nil.String()}, {"--request-id", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}, {"--unknown"}, {"unexpected argument"}} {
		command := append(append([]string{}, args...), "evidence", "--file", regular, "--source", "source", "--repo", "fixture:file")
		_, err := run(context.Background(), append(command, extra...), strings.NewReader(""))
		if core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("invalid intent %v: %v", extra, err)
		}
	}
}

func TestEvidenceFileSupportsRegularSymlinksAndExplicitSharing(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "source")
	link := filepath.Join(dir, "link")
	if err := os.WriteFile(path, []byte("text\r\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	request, err := evidenceFileRequest([]string{"--file", link, "--source", "chosen label", "--shareable"})
	if err != nil {
		t.Fatal(err)
	}
	if request.Sensitivity != "shareable" || request.Body != "" || request.BodyBase64 != base64.StdEncoding.EncodeToString([]byte("text\r\n")) {
		t.Fatalf("source transformation: %+v", request)
	}
	if _, err := uuid.Parse(request.RequestID); err != nil {
		t.Fatal(err)
	}
}
