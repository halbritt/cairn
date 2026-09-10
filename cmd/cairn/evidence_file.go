package main

import (
	"encoding/base64"
	"strings"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func evidenceFileRequest(args []string) (core.EvidenceRequest, error) {
	f := flags("evidence --file")
	file := f.String("file", "", "selected regular file (1 MiB maximum)")
	source := f.String("source", "", "retained source label (required; file path is not retained)")
	repo := f.String("repo", defaultRepo(), "canonical repository identity")
	request := f.String("request-id", uuid.NewString(), "stable UUID for an identical retry")
	share := f.Bool("shareable", false, "allow hosted delivery of this selected source")
	if err := f.Parse(args); err != nil {
		return core.EvidenceRequest{}, invalid(err.Error())
	}
	if f.NArg() != 0 || *file == "" || strings.TrimSpace(*source) == "" || len(*source) > 512 || *repo == "" || *repo == "*" || len(*repo) > 256 {
		return core.EvidenceRequest{}, invalid("evidence file capture requires --file, --source and an exact repository, without positional arguments")
	}
	if parsed, err := uuid.Parse(*request); err != nil || parsed == uuid.Nil || parsed.String() != *request {
		return core.EvidenceRequest{}, invalid("request-id must be a nonzero canonical UUID")
	}
	body, err := readRegularFilePrefix(*file, 1048576+1)
	if err != nil {
		return core.EvidenceRequest{}, err
	}
	if len(body) == 0 || len(body) > 1048576 {
		return core.EvidenceRequest{}, invalid("evidence file must contain 1-1048576 bytes")
	}
	sensitivity := "local"
	if *share {
		sensitivity = "shareable"
	}
	return core.EvidenceRequest{RequestID: *request, Repo: *repo, Source: *source, Sensitivity: sensitivity, BodyBase64: base64.StdEncoding.EncodeToString(body)}, nil
}
