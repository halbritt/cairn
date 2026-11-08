package procpeer

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func clientTestPolicy() ClientPolicy {
	return ClientPolicy{Owner: ProcessOwner, Messages: ClientMessages{
		WrongProcess:        "native socket belongs to a different process",
		WrongUser:           "native socket belongs to another user",
		IdentityChanged:     "native process identity has changed",
		EndpointUnavailable: "native queue endpoint is unavailable",
	}}
}

func clientTestServer(t *testing.T) (string, ProcessRef, <-chan []byte) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "p")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })
	ref, err := ProcessRefOf(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	received := make(chan []byte, 1)
	go func() {
		conn, err := listener.Accept()
		if err != nil {
			received <- nil
			return
		}
		defer conn.Close()
		_ = conn.SetReadDeadline(time.Now().Add(3 * time.Second))
		data, _ := io.ReadAll(conn)
		received <- data
	}()
	return path, ref, received
}

func clientTestReceived(t *testing.T, received <-chan []byte) []byte {
	t.Helper()
	select {
	case data := <-received:
		return data
	case <-time.After(4 * time.Second):
		t.Fatal("owned socket did not close")
		return nil
	}
}

func TestClientProcessExactIdentity(t *testing.T) {
	if path := os.Getenv("CAIRN_J04_FIXTURE_SOCKET"); path != "" {
		clientTestChildServe(t, path)
		return
	}
	endpoint, target, received := clientTestServer(t)
	conn, peer, err := ConnectClient(context.Background(), endpoint, target, clientTestPolicy(), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if peer != target {
		t.Fatalf("peer = %#v; want %#v", peer, target)
	}
	if err := conn.Close(); err != nil {
		t.Fatal(err)
	}
	if data := clientTestReceived(t, received); len(data) != 0 {
		t.Fatalf("verification sent %q", data)
	}
}

func clientTestChildServe(t *testing.T, path string) {
	t.Helper()
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	if _, err := fmt.Fprintln(os.Stdout, "READY"); err != nil {
		t.Fatal(err)
	}
	conn, err := listener.Accept()
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
		t.Fatal(err)
	}
	_, _ = io.Copy(io.Discard, conn)
}

func clientTestOwnedChild(t *testing.T) (string, ProcessRef) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "p")
	cmd := exec.Command(os.Args[0], "-test.run=^TestClientProcessExactIdentity$")
	cmd.Env = append(os.Environ(), "CAIRN_J04_FIXTURE_SOCKET="+path)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	if cmd.Process.Pid <= 1 {
		t.Fatal("invalid owned child PID")
	}
	t.Logf("owned Go socket fixture PID %d; direct owned-child cleanup only", cmd.Process.Pid)
	t.Cleanup(func() {
		// Only this unreaped owned child can be signaled; never a process group.
		if cmd.Process.Pid <= 1 {
			t.Error("refusing child control for PID<=1")
			return
		}
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	})
	ready := make(chan string, 1)
	go func() { line, _ := bufio.NewReader(stdout).ReadString('\n'); ready <- line }()
	select {
	case line := <-ready:
		if line != "READY\n" {
			t.Fatalf("child readiness %q", line)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("owned child readiness timeout")
	}
	ref, err := ProcessRefOf(cmd.Process.Pid)
	if err != nil {
		t.Fatal(err)
	}
	return path, ref
}

func TestClientProcessRejectsChildSquatter(t *testing.T) {
	endpoint, child := clientTestOwnedChild(t)
	target, err := ProcessRefOf(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	if child.PID == target.PID {
		t.Fatal("fixture is not a distinct child")
	}
	conn, _, err := ConnectClient(context.Background(), endpoint, target, clientTestPolicy(), time.Second)
	var refused *ClientRefusal
	if conn != nil || !errors.As(err, &refused) || refused.Error() != "native socket belongs to a different process" {
		t.Fatalf("squatter: conn=%v err=%v", conn, err)
	}
}

func TestClientProcessOptionalUID(t *testing.T) {
	uid := uint32(os.Geteuid())
	wrong := uid ^ 1
	for _, tc := range []struct {
		name     string
		expected *uint32
		refused  bool
	}{
		{"unset", nil, false}, {"matching", &uid, false}, {"different", &wrong, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			endpoint, target, received := clientTestServer(t)
			policy := clientTestPolicy()
			policy.ExpectedUID = tc.expected
			conn, peer, err := ConnectClient(context.Background(), endpoint, target, policy, time.Second)
			if tc.refused {
				var refused *ClientRefusal
				if conn != nil || !errors.As(err, &refused) || refused.Error() != policy.Messages.WrongUser {
					t.Fatalf("UID refusal: conn=%v err=%v", conn, err)
				}
			} else {
				if err != nil || peer != target {
					t.Fatalf("optional UID: peer=%#v err=%v", peer, err)
				}
				_ = conn.Close()
			}
			if data := clientTestReceived(t, received); len(data) != 0 {
				t.Fatalf("verification sent %q", data)
			}
		})
	}
	target := ProcessRef{PID: 42, Start: 123, Boot: "fixture-boot"}
	peer, err := verifyClientPeer(&syscall.Ucred{Pid: 42, Uid: wrong}, target, clientTestPolicy(), func(int) (ProcessRef, error) { return target, nil })
	if err != nil || peer != target {
		t.Fatalf("nil UID added an implicit UID predicate: %#v %v", peer, err)
	}
}

func TestClientUserAllowsDifferentPeerAndReturnsIdentity(t *testing.T) {
	endpoint, server := clientTestOwnedChild(t)
	target, err := ProcessRefOf(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	policy := clientTestPolicy()
	policy.Owner = UserOwner
	uid := uint32(os.Geteuid())
	policy.ExpectedUID = &uid
	conn, peer, err := ConnectClient(context.Background(), endpoint, target, policy, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if peer != server || peer.PID == target.PID {
		t.Fatalf("actual peer = %#v; server=%#v target=%#v", peer, server, target)
	}
}

func TestClientUserChecksRecordedTarget(t *testing.T) {
	endpoint, target, received := clientTestServer(t)
	target.Start++
	policy := clientTestPolicy()
	policy.Owner = UserOwner
	uid := uint32(os.Geteuid())
	policy.ExpectedUID = &uid
	conn, _, err := ConnectClient(context.Background(), endpoint, target, policy, time.Second)
	var refused *ClientRefusal
	if conn != nil || !errors.As(err, &refused) || refused.Error() != policy.Messages.IdentityChanged {
		t.Fatalf("target remeasurement: conn=%v err=%v", conn, err)
	}
	if data := clientTestReceived(t, received); len(data) != 0 {
		t.Fatalf("unverified write %q", data)
	}
}

func TestClientUserRejectsWrongUID(t *testing.T) {
	endpoint, target, received := clientTestServer(t)
	policy := clientTestPolicy()
	policy.Owner = UserOwner
	wrong := uint32(os.Geteuid()) ^ 1
	policy.ExpectedUID = &wrong
	conn, _, err := ConnectClient(context.Background(), endpoint, target, policy, time.Second)
	var refused *ClientRefusal
	if conn != nil || !errors.As(err, &refused) || refused.Error() != policy.Messages.WrongUser {
		t.Fatalf("user refusal: conn=%v err=%v", conn, err)
	}
	if data := clientTestReceived(t, received); len(data) != 0 {
		t.Fatalf("unverified write %q", data)
	}
}

func TestClientStartBootMismatch(t *testing.T) {
	for _, field := range []string{"start", "boot"} {
		t.Run(field, func(t *testing.T) {
			endpoint, target, received := clientTestServer(t)
			if field == "start" {
				target.Start++
			} else {
				target.Boot += "-different"
			}
			policy := clientTestPolicy()
			conn, _, err := ConnectClient(context.Background(), endpoint, target, policy, time.Second)
			var refused *ClientRefusal
			if conn != nil || !errors.As(err, &refused) || refused.Error() != policy.Messages.IdentityChanged {
				t.Fatalf("%s mismatch: conn=%v err=%v", field, conn, err)
			}
			if data := clientTestReceived(t, received); len(data) != 0 {
				t.Fatalf("unverified write %q", data)
			}
		})
	}
}

func TestClientRefusalBeforePayload(t *testing.T) {
	endpoint, target, received := clientTestServer(t)
	target.Start++
	policy := clientTestPolicy()
	policy.Messages.IdentityChanged = "chosen identity message"
	conn, _, err := ConnectClient(context.Background(), endpoint, target, policy, time.Second)
	var refused *ClientRefusal
	if conn != nil || !errors.As(err, &refused) || refused.Error() != "chosen identity message" {
		t.Fatalf("checked refusal: conn=%v err=%v", conn, err)
	}
	if data := clientTestReceived(t, received); len(data) != 0 {
		t.Fatalf("refusal leaked %q", data)
	}
	credentials := &syscall.Ucred{Pid: 42}
	ref := ProcessRef{PID: 42, Start: 1, Boot: "fixture"}
	fault := &strconv.NumError{Func: "ParseUint", Num: "malformed", Err: strconv.ErrSyntax}
	_, err = verifyClientPeer(credentials, ref, policy, func(int) (ProcessRef, error) { return ProcessRef{}, fault })
	if !errors.Is(err, fault) || errors.As(err, &refused) {
		t.Fatalf("parser fault became a checked refusal: %v", err)
	}
	osFault := &os.PathError{Op: "read", Path: "owned-fixture-stat", Err: syscall.EACCES}
	_, err = verifyClientPeer(credentials, ref, policy, func(int) (ProcessRef, error) { return ProcessRef{}, osFault })
	if !errors.As(err, &refused) || refused.Error() != policy.Messages.EndpointUnavailable || !errors.Is(err, osFault) {
		t.Fatalf("OS cause/message lost: %v", err)
	}
}

func TestClientConnectTimeoutAndClose(t *testing.T) {
	for _, expired := range []bool{false, true} {
		var ctx context.Context
		var cancel context.CancelFunc
		want := context.Canceled
		if expired {
			ctx, cancel = context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
			want = context.DeadlineExceeded
		} else {
			ctx, cancel = context.WithCancel(context.Background())
			cancel()
		}
		conn, _, err := ConnectClient(ctx, filepath.Join(t.TempDir(), "unused"), ProcessRef{}, clientTestPolicy(), time.Second)
		cancel()
		if conn != nil || !errors.Is(err, want) {
			t.Fatalf("connect cancellation: conn=%v err=%v; want %v", conn, err, want)
		}
	}
	endpoint, target, received := clientTestServer(t)
	target.Boot += "-changed"
	conn, _, err := ConnectClient(context.Background(), endpoint, target, clientTestPolicy(), time.Second)
	if conn != nil || err == nil {
		t.Fatalf("failed verification returned a socket: %v %v", conn, err)
	}
	if data := clientTestReceived(t, received); len(data) != 0 {
		t.Fatalf("closed socket carried %q", data)
	}
	for _, policy := range []ClientPolicy{{}, {Owner: UserOwner}} {
		if conn, _, err := ConnectClient(context.Background(), "unused", ProcessRef{}, policy, time.Second); conn != nil || err == nil {
			t.Fatalf("invalid policy accepted: %#v %v", policy, err)
		}
	}
}

type clientTestConn struct {
	reader         io.Reader
	write          func([]byte) (int, error)
	readDeadlines  []time.Time
	writeDeadlines []time.Time
	closed         bool
}

func (c *clientTestConn) Read(p []byte) (int, error) { return c.reader.Read(p) }
func (c *clientTestConn) Write(p []byte) (int, error) {
	if c.write == nil {
		return 0, errors.New("unexpected fixture write")
	}
	return c.write(p)
}
func (c *clientTestConn) Close() error       { c.closed = true; return nil }
func (*clientTestConn) LocalAddr() net.Addr  { return &net.UnixAddr{Name: "fixture", Net: "unix"} }
func (*clientTestConn) RemoteAddr() net.Addr { return &net.UnixAddr{Name: "fixture", Net: "unix"} }
func (c *clientTestConn) SetReadDeadline(d time.Time) error {
	c.readDeadlines = append(c.readDeadlines, d)
	return nil
}
func (c *clientTestConn) SetWriteDeadline(d time.Time) error {
	c.writeDeadlines = append(c.writeDeadlines, d)
	return nil
}
func (c *clientTestConn) SetDeadline(d time.Time) error {
	_ = c.SetReadDeadline(d)
	return c.SetWriteDeadline(d)
}

func TestClientLineBoundsAndEOF(t *testing.T) {
	for _, tc := range []struct {
		name, input, want string
		limit             int
		requireNewline    bool
		wantErr           error
	}{
		{"exact-with-delimiter", "{}\n", "{}", 3, true, nil},
		{"delimiter-counts", "{}\n", "", 2, true, io.ErrShortBuffer},
		{"coalesced-tail-counts", "x\ntrailing", "", 2, true, io.ErrShortBuffer},
		{"first-line", "x\ny", "x", 3, true, nil},
		{"optional-nonempty-eof", "{}", "{}", 2, false, nil},
		{"required-newline", "{}", "", 2, true, io.EOF},
		{"empty-eof", "", "", 2, false, io.EOF},
	} {
		t.Run(tc.name, func(t *testing.T) {
			conn := &clientTestConn{reader: strings.NewReader(tc.input)}
			got, err := ReadClientLine(conn, tc.limit, time.Second, tc.requireNewline)
			if !errors.Is(err, tc.wantErr) || string(got) != tc.want {
				t.Fatalf("got (%q, %v); want (%q, %v)", got, err, tc.want, tc.wantErr)
			}
		})
	}
}

type clientTestReader func([]byte) (int, error)

func (f clientTestReader) Read(p []byte) (int, error) { return f(p) }

func TestClientPerReadVersusAbsoluteDeadline(t *testing.T) {
	reader := func() io.Reader {
		index := 0
		return clientTestReader(func(p []byte) (int, error) {
			if index == 3 {
				return 0, io.EOF
			}
			time.Sleep(time.Millisecond)
			p[0] = "ab\n"[index]
			index++
			return 1, nil
		})
	}
	perRead := &clientTestConn{reader: reader()}
	if got, err := ReadClientLine(perRead, 3, time.Second, true); err != nil || string(got) != "ab" {
		t.Fatalf("per-read: %q %v", got, err)
	}
	if len(perRead.readDeadlines) != 3 || !perRead.readDeadlines[2].After(perRead.readDeadlines[0]) {
		t.Fatalf("deadline was not renewed: %v", perRead.readDeadlines)
	}
	deadline := time.Now().Add(time.Second)
	absolute := &clientTestConn{reader: reader()}
	if got, err := ReadClientLineUntil(absolute, 3, deadline, true); err != nil || string(got) != "ab" {
		t.Fatalf("absolute: %q %v", got, err)
	}
	if len(absolute.readDeadlines) != 3 {
		t.Fatalf("reads = %d", len(absolute.readDeadlines))
	}
	for _, d := range absolute.readDeadlines {
		if d != deadline {
			t.Fatalf("absolute deadline changed to %v", d)
		}
	}
	reads := 0
	expired := &clientTestConn{reader: clientTestReader(func([]byte) (int, error) { reads++; return 0, io.EOF })}
	if _, err := ReadClientLineUntil(expired, 3, time.Now().Add(-time.Second), true); !errors.Is(err, os.ErrDeadlineExceeded) || reads != 0 {
		t.Fatalf("expired deadline: err=%v reads=%d", err, reads)
	}
}

func TestClientShortWriteAndCancellation(t *testing.T) {
	var written bytes.Buffer
	conn := &clientTestConn{write: func(p []byte) (int, error) {
		if len(p) > 2 {
			p = p[:2]
		}
		return written.Write(p)
	}}
	if err := WriteClientFrame(conn, []byte("payload\n"), time.Second); err != nil {
		t.Fatal(err)
	}
	if written.String() != "payload\n" || len(conn.writeDeadlines) != 1 {
		t.Fatalf("sendall = %q, deadlines=%v", written.String(), conn.writeDeadlines)
	}
	stalled := &clientTestConn{write: func([]byte) (int, error) { return 0, nil }}
	if err := WriteClientFrame(stalled, []byte("x"), time.Second); !errors.Is(err, io.ErrNoProgress) {
		t.Fatalf("zero progress: %v", err)
	}
	partial := &clientTestConn{write: func([]byte) (int, error) { return 1, context.Canceled }}
	if err := WriteClientFrame(partial, []byte("xx"), time.Second); !errors.Is(err, context.Canceled) {
		t.Fatalf("partial failure: %v", err)
	}
	left, right := net.Pipe()
	t.Cleanup(func() { _ = left.Close(); _ = right.Close() })
	done := make(chan error, 1)
	go func() { done <- WriteClientFrame(left, []byte("owned fixture"), time.Second) }()
	_ = right.Close()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("closed write declared success")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("closed write did not return")
	}
}

func TestClientCumulativeHandshakeFrameBudget(t *testing.T) {
	overflow := errors.New("caller byte limit")
	const limit = 1 << 20
	underlying := &clientTestConn{reader: strings.NewReader(strings.Repeat("h", limit-2) + "ff")}
	conn, err := LimitClientReads(underlying, limit, overflow)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := io.ReadFull(conn, make([]byte, limit-2)); err != nil {
		t.Fatal("handshake:", err)
	}
	frame := make([]byte, 2)
	if _, err := io.ReadFull(conn, frame); err != nil || string(frame) != "ff" {
		t.Fatalf("frame: %q %v", frame, err)
	}
	if n, err := conn.Read(make([]byte, 1)); n != 0 || !errors.Is(err, overflow) {
		t.Fatalf("cumulative limit: n=%d err=%v", n, err)
	}
	if err := conn.Close(); err != nil || !underlying.closed {
		t.Fatalf("close was not forwarded: %v", err)
	}
	reads := 0
	overshoot, err := LimitClientReads(&clientTestConn{reader: clientTestReader(func(p []byte) (int, error) { reads++; return copy(p, "abc"), nil })}, 2, overflow)
	if err != nil {
		t.Fatal(err)
	}
	for range 2 {
		if n, err := overshoot.Read(make([]byte, 4)); n != 0 || !errors.Is(err, overflow) {
			t.Fatalf("overshoot delivered bytes: n=%d err=%v", n, err)
		}
	}
	if reads != 1 {
		t.Fatalf("read after overflow: %d", reads)
	}
}
