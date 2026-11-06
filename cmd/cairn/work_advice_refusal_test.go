package main

import (
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/halbritt/cairn/core"
)

// These are wire-contract expectations, exercised through the real CLI main
// and work HTTP response writer. The synthetic API only supplies classified
// failures; no database, provider, or domain authorization is simulated.
func TestWorkAdviceRefusalTransport(t *testing.T) {
	cases := []struct {
		code       string
		httpStatus int
		exit       int
		detail     string
		message    string // Empty means preserve the safe code-prefixed detail.
	}{
		{"RESTORE_FENCE_TRIGGERED", 409, 4, "restore generation moved from 2 to 3", ""},
		{"INFLIGHT_LIMIT", 409, 4, "job 00000000-0000-0000-0000-000000000123 is already running", ""},
		{"CONFIG_CONFLICT", 409, 4, "work project config revision moved", ""},
		{"EXCLUSION_CONFLICT", 409, 4, "metadata exclusions revision moved from 1 to 2", ""},
		{"POLICY_CONFLICT", 409, 4, "work advice policy revision moved from 1 to 2", ""},
		{"STALE_OBSERVATION", 409, 4, "active progress annotation modified after advice generation", ""},
		{"INVALID_STATE", 409, 4, "proposal is in state rejected, cannot apply", ""},
		{"ITEM_ABSENT", 409, 4, "work item was marked absent", ""},
		{"RECONCILED_AT_STARTUP", 409, 4, "job 00000000-0000-0000-0000-000000000123 is in state uncertain", ""},
		{"CANCELLED", 409, 4, "private native cancellation sentinel", "advice request was cancelled"},
		{"EXPIRED", 410, 4, "advice job was deleted by retention policy and proposal has expired", ""},
		{"INPUT_TOO_LARGE", 413, 2, "selected sources limit exceeded", ""},
		{"INVALID_PAYLOAD", 422, 2, "private malformed payload sentinel", "advice action payload is invalid"},
		{"OUTPUT_LIMIT", 422, 4, "private native output-limit sentinel", "advice provider output exceeded the allowed limit"},
		{"TIMEOUT", 504, 4, "job 00000000-0000-0000-0000-000000000123 is in state timed_out", ""},
		{"MODEL_TIMEOUT", 504, 4, "private native timeout sentinel", "advice model execution timed out"},
		{"SUBSCRIPTION_REQUIRED", 422, 6, "private native authentication sentinel", "selected subscription authentication is required"},
		{"QUOTA_EXHAUSTED", 429, 4, "private native quota sentinel", "selected subscription quota is exhausted"},
		{"INVALID_PROPOSAL", 422, 2, "private unknown model field sentinel", "advice provider returned an invalid proposal"},
		{"REFUSAL_EXCLUDED", 403, 6, "private exclusion pattern sentinel", "advice output matches an active metadata exclusion"},
		{"INVALID_PROCESS_IDENTITY", 500, 7, "private process identity sentinel", "advice process identity could not be verified"},
		{"JOB_STATE_UNCERTAIN", 503, 7, "private database failure sentinel", "advice job state is uncertain; inspect the retained job before retrying"},
		{"CLEANUP_UNCONFIRMED", 503, 7, "private process cleanup sentinel", "advice process cleanup is unconfirmed; inspect the retained job before retrying"},
		{"RECORD_FAILED", 500, 7, "private proposal storage sentinel", "advice proposal could not be recorded; inspect the retained job"},
		{"MODEL_EXECUTION_FAILED", 502, 7, "private executable failure sentinel", "advice model execution failed; inspect the retained job"},
		{"PROVIDER_ERROR", 502, 7, "private provider response sentinel", "advice provider request failed; inspect the retained job"},
		{"FUTURE_ADVICE_ERROR", 422, 7, "private unknown failure sentinel", "operation failed; inspect the local store and task runtime"},
	}
	for _, tc := range cases {
		t.Run(tc.code, func(t *testing.T) {
			// A short owner-only socket path avoids Unix pathname length limits.
			dir, err := os.MkdirTemp("", "cairn-advice-refusal-")
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { os.RemoveAll(dir) })
			socket, token := filepath.Join(dir, "api.sock"), filepath.Join(dir, "token")
			if err := os.WriteFile(token, []byte("synthetic"), 0600); err != nil {
				t.Fatal(err)
			}
			listener, err := net.Listen("unix", socket)
			if err != nil {
				t.Fatal(err)
			}
			server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(tc.httpStatus)
				_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": false, "status": tc.code, "message": tc.detail})
			})}
			go server.Serve(listener)
			t.Cleanup(func() { server.Close() })
			exit, result := operatorProcess(t, "agent", "--socket", socket, "--token-file", token, "pull", "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002")
			wantMessage := tc.message
			if wantMessage == "" {
				wantMessage = tc.code + ": " + tc.detail
			}
			if exit != tc.exit || result.Status != tc.code || result.Message != wantMessage {
				t.Errorf("CLI exit=%d response=%+v; want exit=%d code=%s message=%q", exit, result, tc.exit, tc.code, wantMessage)
			}

			// The actual work HTTP writer must use the same classified safe text.
			// Raw advice-error normalization is a separate Store boundary test.
			response := httptest.NewRecorder()
			writeWorkAdviceHTTPError(response, &core.Error{Code: tc.code, Message: tc.detail})
			var payload struct {
				Code  string `json:"code"`
				Error string `json:"error"`
			}
			if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
				t.Fatal(err)
			}
			if response.Code != tc.httpStatus || payload.Code != tc.code {
				t.Errorf("HTTP status=%d code=%q; want status=%d code=%q", response.Code, payload.Code, tc.httpStatus, tc.code)
			}
			wantHTTPMessage := wantMessage
			if tc.code == "FUTURE_ADVICE_ERROR" {
				// Preserve the explicit unknown-code HTTP contract; untyped failures
				// instead become STORE_ERROR and are redacted below.
				wantHTTPMessage = tc.code + ": " + tc.detail
			}
			if payload.Error != wantHTTPMessage {
				t.Errorf("HTTP error=%q; want %q", payload.Error, wantHTTPMessage)
			}
		})
	}
}

func TestWorkAdviceHTTPUnknownFailureIsPrivate(t *testing.T) {
	response := httptest.NewRecorder()
	writeWorkAdviceHTTPError(response, errors.New("private unclassified database failure sentinel"))
	var payload struct {
		Code  string `json:"code"`
		Error string `json:"error"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if response.Code != http.StatusInternalServerError || payload.Code != "STORE_ERROR" || payload.Error != "store operation failed" {
		t.Fatalf("unknown failure disclosed or misclassified: HTTP %d %+v", response.Code, payload)
	}
}
