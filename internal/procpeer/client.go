package procpeer

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"sync"
	"syscall"
	"time"
)

// ClientOwner selects the reference transport's process or same-user policy.
type ClientOwner uint8

const (
	ProcessOwner ClientOwner = iota + 1
	UserOwner
)

// ClientMessages are the consumer's existing pre-payload refusal messages.
type ClientMessages struct {
	WrongProcess, WrongUser, IdentityChanged, EndpointUnavailable string
}

// ClientPolicy preserves optional process-mode UID checks. UserOwner requires
// the caller's selected/effective UID; it may connect to a different peer PID.
type ClientPolicy struct {
	Owner       ClientOwner
	ExpectedUID *uint32
	Messages    ClientMessages
}

// ClientRefusal retains a checked pre-payload refusal and its optional OS cause.
// Native admission, response and retry classification belong to the consumer.
type ClientRefusal struct {
	Message string
	Cause   error
}

func (e *ClientRefusal) Error() string { return e.Message }
func (e *ClientRefusal) Unwrap() error { return e.Cause }

// ConnectClient verifies the connected Unix peer before returning any socket
// to the caller. The returned reference identifies the actual socket peer.
// Liveness states such as Z/X remain a separate consumer policy.
func ConnectClient(ctx context.Context, endpoint string, target ProcessRef, policy ClientPolicy, timeout time.Duration) (*net.UnixConn, ProcessRef, error) {
	if policy.Owner != ProcessOwner && policy.Owner != UserOwner {
		return nil, ProcessRef{}, fmt.Errorf("invalid client owner mode")
	}
	if policy.Owner == UserOwner && policy.ExpectedUID == nil {
		return nil, ProcessRef{}, fmt.Errorf("user-owned client requires an explicit UID")
	}
	if timeout <= 0 {
		return nil, ProcessRef{}, fmt.Errorf("client connect timeout must be positive")
	}
	dialer := net.Dialer{Timeout: timeout}
	raw, err := dialer.DialContext(ctx, "unix", endpoint)
	if err != nil {
		return nil, ProcessRef{}, &ClientRefusal{policy.Messages.EndpointUnavailable, err}
	}
	conn := raw.(*net.UnixConn)
	peer, err := connectedClientPeer(conn, target, policy)
	if err != nil {
		_ = conn.Close()
		return nil, ProcessRef{}, err
	}
	return conn, peer, nil
}

func connectedClientPeer(conn *net.UnixConn, target ProcessRef, policy ClientPolicy) (ProcessRef, error) {
	raw, err := conn.SyscallConn()
	if err != nil {
		return ProcessRef{}, &ClientRefusal{policy.Messages.EndpointUnavailable, err}
	}
	var credentials *syscall.Ucred
	var socketError error
	err = raw.Control(func(fd uintptr) {
		credentials, socketError = syscall.GetsockoptUcred(int(fd), syscall.SOL_SOCKET, syscall.SO_PEERCRED)
	})
	if err == nil {
		err = socketError
	}
	if err != nil {
		return ProcessRef{}, &ClientRefusal{policy.Messages.EndpointUnavailable, err}
	}
	return verifyClientPeer(credentials, target, policy, ProcessRefOf)
}

func verifyClientPeer(credentials *syscall.Ucred, target ProcessRef, policy ClientPolicy, measure func(int) (ProcessRef, error)) (ProcessRef, error) {
	peerPID := int(credentials.Pid)
	checkedPID := peerPID
	if policy.Owner == ProcessOwner {
		if peerPID != target.PID {
			return ProcessRef{}, &ClientRefusal{Message: policy.Messages.WrongProcess}
		}
		if policy.ExpectedUID != nil && credentials.Uid != *policy.ExpectedUID {
			return ProcessRef{}, &ClientRefusal{Message: policy.Messages.WrongUser}
		}
	} else {
		if credentials.Uid != *policy.ExpectedUID {
			return ProcessRef{}, &ClientRefusal{Message: policy.Messages.WrongUser}
		}
		checkedPID = target.PID
	}
	checked, err := measure(checkedPID)
	if err != nil {
		return ProcessRef{}, clientIdentityError(policy, err)
	}
	if checked.Start != target.Start || checked.Boot != target.Boot {
		return ProcessRef{}, &ClientRefusal{Message: policy.Messages.IdentityChanged}
	}
	if policy.Owner == ProcessOwner {
		return checked, nil
	}
	peer, err := measure(peerPID)
	if err != nil {
		return ProcessRef{}, clientIdentityError(policy, err)
	}
	return peer, nil
}

func clientIdentityError(policy ClientPolicy, err error) error {
	var pathError *os.PathError
	var syscallError *os.SyscallError
	var errno syscall.Errno
	if errors.As(err, &pathError) || errors.As(err, &syscallError) || errors.As(err, &errno) {
		return &ClientRefusal{policy.Messages.EndpointUnavailable, err}
	}
	return err
}

// WriteClientFrame sends exactly the caller's framed bytes with one total
// timeout. An error never establishes that none of the request was received.
func WriteClientFrame(conn net.Conn, framed []byte, timeout time.Duration) error {
	if timeout <= 0 {
		return fmt.Errorf("client write timeout must be positive")
	}
	if err := conn.SetWriteDeadline(time.Now().Add(timeout)); err != nil {
		return err
	}
	for len(framed) > 0 {
		n, err := conn.Write(framed)
		framed = framed[n:]
		if err != nil {
			return err
		}
		if n == 0 {
			return io.ErrNoProgress
		}
	}
	return nil
}

// ReadClientLine renews the timeout for each read, matching socket.settimeout.
// The byte bound includes the delimiter and any bytes coalesced after it.
// io.ErrShortBuffer means that the received-byte bound was exceeded.
func ReadClientLine(conn net.Conn, maxReceived int, perReadTimeout time.Duration, requireNewline bool) ([]byte, error) {
	if perReadTimeout <= 0 {
		return nil, fmt.Errorf("client read timeout must be positive")
	}
	return readClientLine(conn, maxReceived, requireNewline, func() (time.Time, error) {
		return time.Now().Add(perReadTimeout), nil
	})
}

// ReadClientLineUntil keeps one absolute reply deadline across all fragments.
func ReadClientLineUntil(conn net.Conn, maxReceived int, deadline time.Time, requireNewline bool) ([]byte, error) {
	if deadline.IsZero() {
		return nil, fmt.Errorf("client reply deadline must be explicit")
	}
	return readClientLine(conn, maxReceived, requireNewline, func() (time.Time, error) {
		if !time.Now().Before(deadline) {
			return time.Time{}, os.ErrDeadlineExceeded
		}
		return deadline, nil
	})
}

func readClientLine(conn net.Conn, maxReceived int, requireNewline bool, nextDeadline func() (time.Time, error)) ([]byte, error) {
	if maxReceived <= 0 {
		return nil, fmt.Errorf("client read limit must be positive")
	}
	var reply []byte
	chunk := make([]byte, 4096)
	for {
		deadline, err := nextDeadline()
		if err != nil {
			return nil, err
		}
		if err := conn.SetReadDeadline(deadline); err != nil {
			return nil, err
		}
		n, err := conn.Read(chunk)
		if n > 0 {
			reply = append(reply, chunk[:n]...)
			if len(reply) > maxReceived {
				return nil, io.ErrShortBuffer
			}
			if end := bytes.IndexByte(reply, '\n'); end >= 0 {
				return reply[:end], nil
			}
		}
		if errors.Is(err, io.EOF) {
			if !requireNewline && len(reply) > 0 {
				return reply, nil
			}
			return nil, io.EOF
		}
		if err != nil {
			return nil, err
		}
		if n == 0 {
			return nil, io.ErrNoProgress
		}
	}
}

// LimitClientReads keeps one received-byte budget for the entire connection.
// It must wrap a Codex connection before both its upgrade and all frame reads.
// A chunk that exceeds the budget is consumed but never delivered to the caller.
func LimitClientReads(conn net.Conn, maxBytes int64, overflow error) (net.Conn, error) {
	if maxBytes <= 0 || overflow == nil {
		return nil, fmt.Errorf("client read budget requires a positive limit and overflow error")
	}
	return &limitedClientConn{Conn: conn, remaining: maxBytes, overflow: overflow}, nil
}

type limitedClientConn struct {
	net.Conn
	mu        sync.Mutex
	remaining int64
	overflow  error
}

func (c *limitedClientConn) Read(p []byte) (int, error) {
	if len(p) == 0 {
		return 0, nil
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.remaining <= 0 {
		return 0, c.overflow
	}
	if int64(len(p)) > c.remaining {
		p = p[:int(c.remaining)+1]
	}
	n, err := c.Conn.Read(p)
	c.remaining -= int64(n)
	if c.remaining < 0 {
		return 0, c.overflow
	}
	return n, err
}
