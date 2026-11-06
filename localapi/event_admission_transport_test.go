package localapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func admissionRefusal() *core.Error {
	return &core.Error{Code: "EVENT_ADMISSION_REFUSED", Message: "event admission dimension recipient_outstanding at policy revision 7; retry unchanged intent after accepted work drains",
		Admission: &core.EventAdmissionRefusal{Dimension: "recipient_outstanding", PolicyRevision: 7, RetryAfterSeconds: 3, Retryable: true}}
}

func serveRefusal(t *testing.T, err error) (*httptest.ResponseRecorder, map[string]json.RawMessage) {
	t.Helper()
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/v1/event-publish", strings.NewReader(`{}`))
	serveJSON(recorder, request, func(context.Context, struct{}) (struct{}, error) { return struct{}{}, err })
	var fields map[string]json.RawMessage
	if decodeErr := json.Unmarshal(recorder.Body.Bytes(), &fields); decodeErr != nil {
		t.Fatalf("envelope: %v %s", decodeErr, recorder.Body.String())
	}
	return recorder, fields
}

func fieldNames(fields map[string]json.RawMessage) []string {
	names := make([]string, 0, len(fields))
	for name := range fields {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// The server sends typed admission details only for the admission code, as a
// bounded object with no recipient data, and leaves every other envelope as it was.
func TestServeJSONEventAdmissionRefusalEnvelope(t *testing.T) {
	recorder, fields := serveRefusal(t, admissionRefusal())
	if recorder.Code != http.StatusTooManyRequests || recorder.Header().Get("Retry-After") != "3" {
		t.Fatalf("status %d retry-after %q", recorder.Code, recorder.Header().Get("Retry-After"))
	}
	if got := fieldNames(fields); !reflect.DeepEqual(got, []string{"admission", "message", "ok", "protocol", "schema", "status"}) {
		t.Fatalf("envelope fields %v", got)
	}
	var admission map[string]any
	if err := json.Unmarshal(fields["admission"], &admission); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{"dimension": "recipient_outstanding", "policy_revision": float64(7), "retry_after_seconds": float64(3), "retryable": true}
	if !reflect.DeepEqual(admission, want) {
		t.Fatalf("admission %v", admission)
	}
	if string(fields["status"]) != `"EVENT_ADMISSION_REFUSED"` || string(fields["ok"]) != "false" {
		t.Fatalf("status/ok %s %s", fields["status"], fields["ok"])
	}

	// A wrapped admission error keeps its details through errors.As.
	_, wrapped := serveRefusal(t, errors.Join(errors.New("context"), admissionRefusal()))
	if _, ok := wrapped["admission"]; !ok {
		t.Fatalf("wrapped admission refusal lost details: %v", fieldNames(wrapped))
	}

	// Details attached to another code are never forwarded.
	other := admissionRefusal()
	other.Code = "INVALID_REQUEST"
	recorder, fields = serveRefusal(t, other)
	if _, ok := fields["admission"]; ok || recorder.Header().Get("Retry-After") != "" || recorder.Code != http.StatusBadRequest {
		t.Fatalf("non-admission refusal exposed details: %d %v", recorder.Code, fieldNames(fields))
	}

	// An ordinary refusal keeps the existing envelope shape.
	recorder, fields = serveRefusal(t, &core.Error{Code: "NOT_FOUND", Message: "missing", RefusalID: "refusal-1"})
	if got := fieldNames(fields); !reflect.DeepEqual(got, []string{"message", "ok", "protocol", "refusal_id", "schema", "status"}) || recorder.Code != http.StatusNotFound {
		t.Fatalf("ordinary refusal envelope %d %v", recorder.Code, got)
	}

	// An admission refusal without guidance sets no Retry-After header.
	quiet := admissionRefusal()
	quiet.Admission.RetryAfterSeconds = 0
	if recorder, _ = serveRefusal(t, quiet); recorder.Header().Get("Retry-After") != "" {
		t.Fatalf("zero retry guidance set Retry-After %q", recorder.Header().Get("Retry-After"))
	}
}

func fixedEnvelopeClient(t *testing.T, status int, body string) *Client {
	t.Helper()
	dir, err := os.MkdirTemp("", "cairn-admission-transport-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	socket := filepath.Join(dir, "api.sock")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	})}
	go server.Serve(listener)
	t.Cleanup(func() { server.Close() })
	token := filepath.Join(dir, "token")
	if err = os.WriteFile(token, []byte("synthetic-admission-token"), 0o600); err != nil {
		t.Fatal(err)
	}
	client, err := NewClient(socket, token)
	if err != nil {
		t.Fatal(err)
	}
	return client
}

// The client returns admission details only for the admission code and
// tolerates fields added by a newer server.
func TestClientDecodesEventAdmissionRefusal(t *testing.T) {
	var encoded bytes.Buffer
	recorder, _ := serveRefusal(t, admissionRefusal())
	encoded.Write(recorder.Body.Bytes())
	client := fixedEnvelopeClient(t, recorder.Code, encoded.String())
	err := client.Call(context.Background(), "event-publish", map[string]string{}, &json.RawMessage{})
	var failure *core.Error
	if !errors.As(err, &failure) || failure.Code != "EVENT_ADMISSION_REFUSED" || failure.Admission == nil ||
		*failure.Admission != *admissionRefusal().Admission {
		t.Fatalf("decoded refusal: %#v", err)
	}

	newer := `{"schema":"cairn.response/1","ok":false,"status":"EVENT_ADMISSION_REFUSED","message":"m","protocol":{"min":1,"current":2},` +
		`"admission":{"dimension":"collection_outstanding","policy_revision":2,"retry_after_seconds":1,"retryable":true,"future":"ignored"},"future":1}`
	err = fixedEnvelopeClient(t, http.StatusTooManyRequests, newer).Call(context.Background(), "event-publish", map[string]string{}, &json.RawMessage{})
	if !errors.As(err, &failure) || failure.Admission == nil || failure.Admission.Dimension != "collection_outstanding" || failure.Admission.PolicyRevision != 2 {
		t.Fatalf("newer server refusal: %#v", err)
	}

	mislabeled := `{"schema":"cairn.response/1","ok":false,"status":"NOT_FOUND","message":"m","protocol":{"min":1,"current":2},` +
		`"admission":{"dimension":"recipient_outstanding","policy_revision":2,"retry_after_seconds":1,"retryable":true}}`
	err = fixedEnvelopeClient(t, http.StatusNotFound, mislabeled).Call(context.Background(), "event-publish", map[string]string{}, &json.RawMessage{})
	if !errors.As(err, &failure) || failure.Code != "NOT_FOUND" || failure.Admission != nil {
		t.Fatalf("admission details accepted for another code: %#v", err)
	}

	legacy := `{"schema":"cairn.response/1","ok":false,"status":"EVENT_ADMISSION_REFUSED","message":"m","protocol":{"min":1,"current":2}}`
	err = fixedEnvelopeClient(t, http.StatusTooManyRequests, legacy).Call(context.Background(), "event-publish", map[string]string{}, &json.RawMessage{})
	if !errors.As(err, &failure) || failure.Code != "EVENT_ADMISSION_REFUSED" || failure.Admission != nil {
		t.Fatalf("refusal without details: %#v", err)
	}
}
