package localapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5/pgconn"
)

// serveCapturingLog runs one request through serveJSON with the given request
// context and returns the response and the single refusal log line.
func serveCapturingLog(t *testing.T, ctx context.Context, err error) (*httptest.ResponseRecorder, map[string]any, map[string]any) {
	t.Helper()
	reader, writer, pipeErr := os.Pipe()
	if pipeErr != nil {
		t.Fatal(pipeErr)
	}
	saved := os.Stderr
	os.Stderr = writer
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/v1/index", strings.NewReader(`{}`)).WithContext(ctx)
	serveJSON(recorder, request, func(context.Context, struct{}) (struct{}, error) { return struct{}{}, err })
	os.Stderr = saved
	_ = writer.Close()
	logged, _ := io.ReadAll(reader)
	_ = reader.Close()
	var line map[string]any
	if decodeErr := json.Unmarshal(bytes.TrimSpace(logged), &line); decodeErr != nil {
		t.Fatalf("refusal log line: %v %q", decodeErr, logged)
	}
	var envelope map[string]any
	if decodeErr := json.Unmarshal(recorder.Body.Bytes(), &envelope); decodeErr != nil {
		t.Fatalf("envelope: %v %s", decodeErr, recorder.Body.String())
	}
	return recorder, envelope, line
}

func endedContext(t *testing.T, deadline bool) context.Context {
	t.Helper()
	if deadline {
		ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
		t.Cleanup(cancel)
		return ctx
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	return ctx
}

// A request whose context ended is reported as the retryable TIMEOUT with 504,
// never as STORE_ERROR/500, whatever error the driver returned after it ended.
// The caller message is fixed, and the log line carries only the cause class.
func TestServeJSONEndedRequestContextIsTimeoutNotStoreError(t *testing.T) {
	aborted := &pgconn.PgError{Code: "57014", Message: "canceling statement due to user request", Detail: "secret record body"}
	for _, tc := range []struct {
		name  string
		ctx   context.Context
		err   error
		cause string
	}{
		{"deadline with cancelled statement", endedContext(t, true), aborted, "context_deadline"},
		{"deadline with lost connection", endedContext(t, true), errors.New("conn closed"), "context_deadline"},
		{"caller disconnect", endedContext(t, false), fmt.Errorf("copy: %w", context.Canceled), "client_cancel"},
		{"wrapped deadline on a live context", context.Background(), fmt.Errorf("timeout: %w", context.DeadlineExceeded), "context_deadline"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			recorder, envelope, line := serveCapturingLog(t, tc.ctx, tc.err)
			if recorder.Code != http.StatusGatewayTimeout || envelope["status"] != "TIMEOUT" || envelope["ok"] != false {
				t.Fatalf("HTTP %d %s", recorder.Code, recorder.Body.String())
			}
			if envelope["message"] != requestEndedMessage || strings.Contains(recorder.Body.String(), "secret") || strings.Contains(recorder.Body.String(), "conn closed") {
				t.Fatalf("message leaked driver text: %s", recorder.Body.String())
			}
			if len(line) != 5 || line["event"] != "refusal" || line["operation"] != "index" || line["code"] != "TIMEOUT" || line["status"] != float64(504) || line["cause"] != tc.cause {
				t.Fatalf("log line: %v", line)
			}
		})
	}
}

// Store failures on a live request stay STORE_ERROR/500. The log names only the
// SQLSTATE class, and the envelope keeps the fixed private message.
func TestServeJSONStoreErrorLogsSQLStateClassOnly(t *testing.T) {
	recorder, envelope, line := serveCapturingLog(t, context.Background(), fmt.Errorf("insert: %w", &pgconn.PgError{Code: "23505", Message: "duplicate key", Detail: "Key (body)=(secret)"}))
	if recorder.Code != http.StatusInternalServerError || envelope["status"] != "STORE_ERROR" || envelope["message"] != "store operation failed" {
		t.Fatalf("HTTP %d %s", recorder.Code, recorder.Body.String())
	}
	if len(line) != 5 || line["code"] != "STORE_ERROR" || line["status"] != float64(500) || line["cause"] != "sqlstate_23" {
		t.Fatalf("log line: %v", line)
	}
	// An untyped failure with no recognized cause omits the field.
	_, _, line = serveCapturingLog(t, context.Background(), errors.New("unclassified"))
	if _, ok := line["cause"]; ok || len(line) != 4 || line["code"] != "STORE_ERROR" {
		t.Fatalf("log line: %v", line)
	}
}

// A typed domain refusal keeps its own code and status even if the request
// context ended afterwards, and carries no cause.
func TestServeJSONTypedRefusalIgnoresEndedContext(t *testing.T) {
	recorder, envelope, line := serveCapturingLog(t, endedContext(t, true), &core.Error{Code: "NOT_FOUND", Message: "record not found", Cause: context.DeadlineExceeded})
	if recorder.Code != http.StatusNotFound || envelope["status"] != "NOT_FOUND" {
		t.Fatalf("HTTP %d %s", recorder.Code, recorder.Body.String())
	}
	if _, ok := line["cause"]; ok || line["code"] != "NOT_FOUND" {
		t.Fatalf("log line: %v", line)
	}
}

func TestFailureCauseClasses(t *testing.T) {
	live := context.Background()
	for _, tc := range []struct {
		name  string
		ctx   context.Context
		err   error
		cause string
		ended bool
	}{
		{"deadline", endedContext(t, true), errors.New("x"), "context_deadline", true},
		{"cancel", endedContext(t, false), errors.New("x"), "client_cancel", true},
		{"wrapped cancel", live, fmt.Errorf("x: %w", context.Canceled), "client_cancel", true},
		{"sqlstate", live, &pgconn.PgError{Code: "40001"}, "sqlstate_40", false},
		{"typed store error with sqlstate", live, &core.Error{Code: "STORE_ERROR", Message: "m", Cause: &pgconn.PgError{Code: "53300"}}, "sqlstate_53", false},
		{"typed domain refusal", live, &core.Error{Code: "VERSION_CONFLICT", Cause: &pgconn.PgError{Code: "40001"}}, "", false},
		{"malformed sqlstate", live, &pgconn.PgError{Code: "4"}, "", false},
		{"unknown", live, errors.New("x"), "", false},
	} {
		if cause, ended := failureCause(tc.ctx, tc.err); cause != tc.cause || ended != tc.ended {
			t.Fatalf("%s: cause %q ended %v, want %q %v", tc.name, cause, ended, tc.cause, tc.ended)
		}
	}
}
