// Package activationipc owns the bounded private activation process protocol.
// It is consumed by the independently selected private lifecycle image; the
// ordinary cairn CLI's public lifecycle registration remains disabled.
package activationipc

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"os"
	"syscall"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/sys/unix"
)

func Run(args []string, input io.Reader, output, diagnostic io.Writer) int {
	if len(args) < 3 || args[0] != "lifecycle" || args[1] != "binding" || args[2] != "activation" {
		return refuse(diagnostic, errors.New("invalid private activation operation"))
	}
	flags := flag.NewFlagSet("private activation", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	in := admission.HookInvocation{}
	flags.StringVar(&in.EntryPoint, "entrypoint", "", "")
	flags.StringVar(&in.ConfigPath, "config", "", "")
	flags.StringVar(&in.ConfigSHA256, "config-sha256", "", "")
	flags.StringVar(&in.BindingSHA256, "binding-sha256", "", "")
	flags.StringVar(&in.ReceiptPath, "receipt", "", "")
	flags.StringVar(&in.ReceiptSHA256, "receipt-sha256", "", "")
	deadline := flags.Float64("deadline", 0, "")
	if err := flags.Parse(args[3:]); err != nil || flags.NArg() != 0 {
		return refuse(diagnostic, errors.New("invalid private activation arguments"))
	}
	var now unix.Timespec
	if err := unix.ClockGettime(unix.CLOCK_MONOTONIC, &now); err != nil {
		return refuse(diagnostic, err)
	}
	remaining := *deadline - float64(now.Sec) - float64(now.Nsec)/1e9
	if remaining <= 0 {
		return refuse(diagnostic, context.DeadlineExceeded)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(remaining*float64(time.Second)))
	defer cancel()
	var err error
	in.Event, err = io.ReadAll(io.LimitReader(input, 1024*1024+1))
	if err != nil {
		return refuse(diagnostic, err)
	}
	if len(in.Event) > 1024*1024 {
		return refuse(diagnostic, errors.New("hook event exceeds limit"))
	}
	in.Directory, err = os.Getwd()
	if err != nil {
		return refuse(diagnostic, err)
	}
	in.Environment = os.Environ()
	enabled, err := binding.ResolveActivation(ctx, in)
	if err != nil {
		return refuse(diagnostic, err)
	}
	if err = json.NewEncoder(output).Encode(struct {
		Enabled bool `json:"enabled"`
	}{enabled}); err != nil {
		return refuse(diagnostic, err)
	}
	return 0
}

func refuse(out io.Writer, err error) int {
	class, reason := "ValueError", "activation"
	var number *int
	var preflight *binding.ActivationError
	var source *value.Error
	var errno syscall.Errno
	switch {
	case errors.As(err, &preflight):
		class, reason = preflight.Class, preflight.Reason
	case errors.Is(err, context.DeadlineExceeded), errors.Is(err, context.Canceled):
		class = "TimeoutError"
		n := int(syscall.ETIMEDOUT)
		number = &n
	case errors.As(err, &source):
		class = source.Class
	case errors.As(err, &errno):
		class = "OSError"
		n := int(errno)
		number = &n
		switch errno {
		case syscall.ENOENT:
			class = "FileNotFoundError"
		case syscall.EACCES, syscall.EPERM:
			class = "PermissionError"
		case syscall.EAGAIN:
			class = "BlockingIOError"
		case syscall.EEXIST:
			class = "FileExistsError"
		case syscall.ENOTDIR:
			class = "NotADirectoryError"
		case syscall.EISDIR:
			class = "IsADirectoryError"
		}
	}
	frame := struct {
		Error struct {
			Class  string `json:"class"`
			Reason string `json:"reason"`
			Errno  *int   `json:"errno"`
		} `json:"error"`
	}{}
	frame.Error.Class, frame.Error.Reason, frame.Error.Errno = class, reason, number
	// These fixed fields cannot exceed the protocol bound or include source text.
	json.NewEncoder(out).Encode(frame)
	return 1
}
