package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
	"golang.org/x/sys/unix"
)

// watchPlan owns one bounded page at a time. Output must succeed before the
// cursor advances; a crash between output and checkpoint can repeat deliveries.
type watchPlan struct {
	socket, token, cursorFile string
	request                   core.InboxWatchRequest
	session                   *core.AgentSessionRef
	once                      bool
}

// A pipe supplied as stdout may initially use blocking I/O. Put a duplicate
// descriptor under Go's poller so cancellation can interrupt a full pipe.
func watchOutputFile(ctx context.Context, stdout *os.File) (*os.File, func(), error) {
	info, err := stdout.Stat()
	if err != nil {
		return nil, nil, err
	}
	if info.Mode()&os.ModeNamedPipe == 0 {
		return stdout, func() {}, nil
	}
	originalFlags, err := unix.FcntlInt(stdout.Fd(), unix.F_GETFL, 0)
	if err != nil {
		return nil, nil, err
	}
	fd, err := syscall.Dup(int(stdout.Fd()))
	if err != nil {
		return nil, nil, err
	}
	if err = syscall.SetNonblock(fd, true); err != nil {
		syscall.Close(fd)
		return nil, nil, err
	}
	output := os.NewFile(uintptr(fd), "watch-output")
	done := make(chan struct{})
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		select {
		case <-ctx.Done():
			output.Close()
		case <-done:
		}
	}()
	return output, func() {
		close(done)
		<-stopped
		output.Close()
		unix.FcntlInt(stdout.Fd(), unix.F_SETFL, originalFlags)
	}, nil
}

func prepareWatch(args []string) (any, error) {
	p := watchPlan{}
	f := flags("watch")
	f.StringVar(&p.socket, "socket", "", "Cairn Unix socket")
	f.StringVar(&p.token, "token-file", "", "existing owner-only API token file")
	profile := f.String("profile", "", "provisioned event profile")
	f.StringVar(&p.request.Repo, "repo", "", "configured collection")
	f.StringVar(&p.request.Agent, "agent", "", "assert the configured inbox identity")
	f.StringVar(&p.request.Topic, "topic", "", "watch only this topic")
	f.StringVar(&p.request.Cursor, "cursor", "", "resume an opaque inbox cursor")
	f.StringVar(&p.cursorFile, "cursor-file", "", "atomically save and resume an owner-only cursor file")
	f.IntVar(&p.request.Limit, "limit", 50, "deliveries per page (1..100)")
	f.BoolVar(&p.once, "once", false, "emit one page, including an empty page, then exit")
	agentID := f.String("agent-id", "", "registered session UUID")
	executionID := f.String("execution-id", "", "current session execution UUID")
	if err := f.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return agentFlagHelp("cairn watch [OPTIONS]", "Read inbox arrivals without claiming work. Emits newline-delimited page objects. Polls every second and reconnects after transport failures. Saved cursors advance after output; consumers must tolerate duplicate delivery IDs after a crash. STALE_CURSOR requires explicit operator rescan.", f), nil
		}
		return nil, invalid(err.Error())
	}
	if f.NArg() != 0 || p.request.Limit < 1 || p.request.Limit > 100 || (p.cursorFile != "" && p.request.Cursor != "") {
		return nil, invalid("watch requires limit 1..100 and at most one of --cursor or --cursor-file")
	}
	provided := map[string]bool{}
	f.Visit(func(value *flag.Flag) { provided[value.Name] = true })
	for _, name := range []string{"socket", "token-file", "profile", "cursor-file", "cursor"} {
		if provided[name] && f.Lookup(name).Value.String() == "" {
			return nil, invalid("watch connection and cursor flags cannot be explicitly empty")
		}
	}
	var err error
	p.token, err = agentProfileToken(*profile, p.token)
	if err != nil {
		return nil, err
	}
	if p.socket == "" || p.token == "" {
		dir, err := dataDirectory()
		if err != nil {
			return nil, err
		}
		if p.socket == "" {
			p.socket = filepath.Join(dir, "api.sock")
		}
		if p.token == "" {
			p.token = filepath.Join(dir, "agent.token")
		}
	}
	if provided["agent-id"] || provided["execution-id"] {
		p.session = &core.AgentSessionRef{AgentID: *agentID, ExecutionID: *executionID}
		if err := p.session.Validate(); err != nil {
			return nil, err
		}
	}
	return p, nil
}

func (p watchPlan) execute(ctx context.Context, out, diagnostics io.Writer) error {
	if p.cursorFile != "" {
		lock, err := os.OpenFile(p.cursorFile+".lock", os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0600)
		if err != nil {
			return err
		}
		defer lock.Close()
		if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
			return invalid("cursor file is already in use or cannot be locked")
		}
		defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
		file, err := os.OpenFile(p.cursorFile, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
		if err == nil {
			info, statErr := file.Stat()
			if statErr != nil || !info.Mode().IsRegular() {
				file.Close()
				return invalid("cursor must be a regular file")
			}
			body, readErr := io.ReadAll(io.LimitReader(file, 4097))
			closeErr := file.Close()
			if readErr != nil {
				return readErr
			}
			if closeErr != nil {
				return closeErr
			}
			if len(body) > 4096 || strings.TrimSpace(string(body)) == "" {
				return invalid("cursor file is empty or exceeds limit; choose an explicit rescan")
			}
			p.request.Cursor = strings.TrimSpace(string(body))
		} else if !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	client, err := localapi.NewClient(p.socket, p.token)
	if err != nil {
		return err
	}
	defer client.Close()
	if p.session != nil {
		client = client.ForAgentSession(*p.session)
	}
	disconnected := false
	first := true
	for {
		if err = ctx.Err(); err != nil {
			return err
		}
		var page core.InboxPage
		err = client.Call(ctx, "event-watch", p.request, &page)
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			var network net.Error
			retryable := errors.As(err, &network) || errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF)
			if p.once || !retryable {
				return err
			}
			if !disconnected {
				if _, err = fmt.Fprintln(diagnostics, "Cairn API unavailable; retrying the same inbox cursor."); err != nil {
					return err
				}
			}
			disconnected = true
		} else {
			disconnected = false
			if page.Cursor == "" || len(page.Cursor) > 4096 {
				return invalid("API returned an invalid inbox cursor")
			}
			if first || len(page.Deliveries) > 0 || page.Cursor != p.request.Cursor {
				if err = json.NewEncoder(out).Encode(page); err != nil {
					return err
				}
				if p.cursorFile != "" {
					if err = saveWatchCursor(p.cursorFile, page.Cursor); err != nil {
						return err
					}
				}
				p.request.Cursor = page.Cursor
			}
			first = false
			if p.once {
				return nil
			}
			if page.More {
				continue
			}
		}
		timer := time.NewTimer(time.Second)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}

func saveWatchCursor(path, cursor string) error {
	dir := filepath.Dir(path)
	file, err := os.CreateTemp(dir, ".cairn-watch-*")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if _, err = io.WriteString(file, cursor); err != nil {
		file.Close()
		return err
	}
	if err = file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err = file.Close(); err != nil {
		return err
	}
	if err = os.Rename(file.Name(), path); err != nil {
		return err
	}
	parent, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer parent.Close()
	return parent.Sync()
}
