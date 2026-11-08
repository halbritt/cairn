package procpeer

import (
	"bytes"
	"errors"
	"io"
	"net"
	"testing"
	"time"
)

func TestReadLineBoundaries(t *testing.T) {
	if MaxLine != 8192 {
		t.Fatalf("retained bridge frame limit = %d, want 8192", MaxLine)
	}
	for _, tc := range []struct {
		name string
		raw  []byte
		ok   bool
	}{
		{"empty", []byte("\n"), true},
		{"exact-limit", append(bytes.Repeat([]byte("x"), 8192), '\n'), true},
		{"over-limit", append(bytes.Repeat([]byte("x"), 8193), '\n'), false},
		{"truncated", []byte("no newline"), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			server, client := net.Pipe()
			defer server.Close()
			done := make(chan struct{})
			go func() { defer close(done); defer client.Close(); _, _ = client.Write(tc.raw) }()
			line, err := ReadLine(server)
			server.Close()
			<-done
			if (err == nil) != tc.ok {
				t.Fatalf("line length %d, err %v", len(line), err)
			}
			if tc.ok && !bytes.Equal(line, tc.raw[:len(tc.raw)-1]) {
				t.Fatal("line bytes changed")
			}
		})
	}
}

func TestFragmentedLineAndReply(t *testing.T) {
	server, client := net.Pipe()
	defer server.Close()
	defer client.Close()
	done := make(chan error, 1)
	go func() {
		for _, fragment := range []string{"{", `"status":"written"`, "}", "\n"} {
			if _, err := io.WriteString(client, fragment); err != nil {
				done <- err
				return
			}
		}
		raw := make([]byte, 4)
		_, err := io.ReadFull(client, raw)
		if err == nil && string(raw) != "yes\n" {
			t.Errorf("reply bytes = %q", raw)
		}
		done <- err
	}()
	line, err := ReadLine(server)
	if err != nil || string(line) != `{"status":"written"}` {
		t.Fatalf("fragmented line %q: %v", line, err)
	}
	if err := WriteLine(server, []byte("yes")); err != nil {
		t.Fatal(err)
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}

func TestConnectionReadAndReplyDeadlines(t *testing.T) {
	for _, operation := range []string{"read", "write"} {
		t.Run(operation, func(t *testing.T) {
			t.Parallel()
			server, client := net.Pipe()
			defer server.Close()
			defer client.Close()
			start := time.Now()
			var err error
			finished := make(chan error, 1)
			go func() {
				if operation == "read" {
					_, err := ReadLine(server)
					finished <- err
				} else {
					finished <- WriteLine(server, []byte("blocked"))
				}
			}()
			select {
			case err = <-finished:
			case <-time.After(4 * time.Second):
				t.Fatal("connection operation lost its bounded deadline")
			}
			elapsed := time.Since(start)
			netErr, ok := err.(net.Error)
			if !ok || !netErr.Timeout() || elapsed < time.Second || elapsed > 5*time.Second {
				t.Fatalf("deadline = %v after %v", err, elapsed)
			}
		})
	}
}

// Keeping the sender open distinguishes a byte bound from an EOF or deadline.
func TestOversizedUnterminatedFrameRefusedWithoutWaitingForDeadline(t *testing.T) {
	server, client := net.Pipe()
	written := make(chan error, 1)
	go func() {
		_, err := client.Write(bytes.Repeat([]byte("x"), 65536))
		written <- err
	}()
	defer func() {
		_ = server.Close()
		_ = client.Close()
		<-written
	}()
	_, err := ReadLine(server)
	if err == nil {
		t.Fatal("unterminated oversized frame was accepted")
	}
	var timeout net.Error
	if errors.As(err, &timeout) && timeout.Timeout() {
		t.Fatalf("oversized frame waited for the socket deadline: %v", err)
	}
}
