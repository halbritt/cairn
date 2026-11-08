package procpeer

import (
	"bufio"
	"errors"
	"io"
	"net"
	"time"
)

// These limits belong to the retained bridge server, not its Python client.
const (
	MaxLine        = 8192 // excludes the terminating newline
	SocketDeadline = 2 * time.Second
)

// ReadLine consumes the bridge's single bounded newline-terminated request.
func ReadLine(conn net.Conn) ([]byte, error) {
	if err := conn.SetReadDeadline(time.Now().Add(SocketDeadline)); err != nil {
		return nil, err
	}
	line, err := bufio.NewReader(io.LimitReader(conn, MaxLine+1)).ReadBytes('\n')
	if err != nil || len(line) > MaxLine+1 {
		if err == nil {
			err = errors.New("oversized frame")
		}
		return nil, err
	}
	line = line[:len(line)-1]
	if len(line) > MaxLine {
		return nil, errors.New("oversized frame")
	}
	return line, nil
}

// WriteLine retains the bridge's reply write and best-effort deadline setting.
func WriteLine(conn net.Conn, data []byte) error {
	_ = conn.SetWriteDeadline(time.Now().Add(SocketDeadline))
	_, err := conn.Write(append(data, '\n'))
	return err
}
