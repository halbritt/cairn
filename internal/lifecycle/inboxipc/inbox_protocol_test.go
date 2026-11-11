package inboxipc

import (
	"bytes"
	"encoding/json"
	"io"
	"strconv"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/sys/unix"
)

func protocolTime(t *testing.T) float64 {
	t.Helper()
	var ts unix.Timespec
	if err := unix.ClockGettime(unix.CLOCK_MONOTONIC, &ts); err != nil {
		t.Fatal(err)
	}
	return float64(ts.Sec) + float64(ts.Nsec)/1e9
}

func TestInboxProtocolRejectsMalformedFramesBeforeProfileAccess(t *testing.T) {
	now := protocolTime(t)
	for _, tc := range []struct{ name, operation, body string }{
		{"duplicate", "recall", `{"event":{},"event":{},"observation":{},"state":{},"control":"x","started_monotonic":0}`},
		{"missing", "recall", `{"event":{},"observation":{},"control":"x","started_monotonic":0}`},
		{"future", "recall", `{"event":{},"observation":{},"state":{},"control":"x","started_monotonic":` + strconv.FormatFloat(now+100, 'f', 6, 64) + `}`},
		{"bad-report", "report", `{"trace_report":null,"unknown":null,"started_monotonic":0}`},
		{"too-large", "recall", strings.Repeat(" ", 1024*1024+1)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var out, diagnostic bytes.Buffer
			status := Run([]string{"lifecycle", "inbox", tc.operation, "--deadline", strconv.FormatFloat(now+10, 'f', 6, 64)}, strings.NewReader(tc.body), &out, &diagnostic)
			if status != 0 || diagnostic.Len() != 0 {
				t.Fatalf("private refusal streams: %d %q", status, diagnostic.String())
			}
			var frame struct {
				OK    bool `json:"ok"`
				Error struct {
					Message string `json:"message"`
				} `json:"error"`
			}
			if err := json.Unmarshal(out.Bytes(), &frame); err != nil || frame.OK || frame.Error.Message == "" {
				t.Fatalf("missing structured refusal: %s %v", out.String(), err)
			}
		})
	}
}

type shortWriter struct{ bytes.Buffer }

func (w *shortWriter) Write(raw []byte) (int, error) { return w.Buffer.Write(raw[:len(raw)/2]) }

func TestInboxReplyLimitAndWriteFailure(t *testing.T) {
	frame := value.Mapping(value.Field("ok", value.Boolean(true)), value.Field("message", value.Text(strings.Repeat("x", ReplyLimit))))
	var out bytes.Buffer
	if reply(&out, frame) != 1 || out.Len() != 0 {
		t.Fatal("oversized reply exposed partial required context")
	}
	frame.Set("message", value.Text("whole context"))
	if reply(new(shortWriter), frame) != 1 {
		t.Fatal("short output write was reported complete")
	}
	if reply(writerError{}, frame) != 1 {
		t.Fatal("failed output write was reported complete")
	}
}

type writerError struct{}

func (writerError) Write([]byte) (int, error) { return 0, io.ErrClosedPipe }
