package main

import (
	"context"
	"net/http"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestRequestJSONRejectsLossyUnicode(t *testing.T) {
	for _, body := range []string{"{\"body\":\"\xff\"}", `{"body":"\ud800"}`, `{"body":"\udc00"}`} {
		var request core.EvidenceRequest
		if err := decode(strings.NewReader(body), &request); err == nil {
			t.Errorf("operator JSON changed source: %q", body)
		}
	}
}

func TestAgentRejectsLossyJSONBeforeAPI(t *testing.T) {
	args := agentCaptureAPI(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("lossy JSON reached API")
		_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":{}}`))
	})
	for _, body := range []string{"{\"body\":\"\xff\"}", `{"body":"\ud800"}`, `{"body":"\udc00"}`} {
		_, err := run(context.Background(), append(append([]string{}, args...), "evidence"), strings.NewReader(body))
		if core.Code(err) != "INVALID_REQUEST" {
			t.Errorf("lossy input did not refuse locally: %v", err)
		}
	}
}
