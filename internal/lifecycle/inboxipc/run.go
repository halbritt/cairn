// Package inboxipc owns the checked private claimed-inbox process boundary.
package inboxipc

import (
	"bytes"
	"context"
	"errors"
	"flag"
	"io"
	"math"
	"os"
	"syscall"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/memoryhook"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/sys/unix"
)

const ReplyLimit = 256 * 1024

func Run(args []string, input io.Reader, output, diagnostic io.Writer) int {
	parent := os.Getppid()
	if err := unix.Prctl(unix.PR_SET_PDEATHSIG, uintptr(syscall.SIGKILL), 0, 0, 0); err != nil {
		return refuse(output, "binding", err)
	}
	if parent < 2 || os.Getppid() != parent {
		return refuse(output, "binding", errors.New("lifecycle parent exited"))
	}
	if len(args) < 3 || args[0] != "lifecycle" || args[1] != "inbox" || args[2] != "recall" {
		return refuse(output, "binding", errors.New("invalid private inbox operation"))
	}
	flags := flag.NewFlagSet("private inbox", flag.ContinueOnError)
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
		return refuse(output, "binding", errors.New("invalid private inbox arguments"))
	}
	var now unix.Timespec
	if err := unix.ClockGettime(unix.CLOCK_MONOTONIC, &now); err != nil {
		return refuse(output, "binding", err)
	}
	nowMono := float64(now.Sec) + float64(now.Nsec)/1e9
	remaining := *deadline - nowMono
	if remaining <= 0 || math.IsNaN(remaining) || math.IsInf(remaining, 0) {
		return refuse(output, "binding", context.DeadlineExceeded)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(remaining*float64(time.Second)))
	defer cancel()
	raw, err := io.ReadAll(io.LimitReader(input, 1024*1024+1))
	if err != nil || len(raw) > 1024*1024 {
		return refuse(output, "binding", errors.New("inbox input exceeds limit or is unavailable"))
	}
	frame, err := value.DecodeBytesWithObjectPolicy(raw, 0, value.DuplicateReject)
	if err != nil {
		return refuse(output, "binding", err)
	}
	if frame.Kind != value.Object || len(frame.Members) != 5 {
		return refuse(output, "binding", errors.New("invalid inbox producer frame"))
	}
	for _, key := range []string{"event", "observation", "state"} {
		v := frame.Get(key)
		if v == nil || v.Kind != value.Object {
			return refuse(output, "binding", errors.New("invalid inbox producer snapshot"))
		}
	}
	control, started := frame.Get("control"), frame.Get("started_monotonic")
	if control == nil || control.Kind != value.String || started == nil || (started.Kind != value.Float && started.Kind != value.Int) {
		return refuse(output, "binding", errors.New("invalid inbox producer control or timing"))
	}
	var event bytes.Buffer
	yes, no := true, false
	route := value.WriterRoute{EnsureASCII: &yes, Compact: &yes, TrailingLF: &no, Codec: &value.Codec{Name: "utf-8", Errors: "strict"}}
	if err = value.WriteKernel(&event, frame.Get("event"), route, 0); err != nil {
		return refuse(output, "binding", err)
	}
	in.Event = event.Bytes()
	in.Directory, err = os.Getwd()
	if err != nil {
		return refuse(output, "binding", err)
	}
	in.Environment = os.Environ()
	checked, err := binding.ResolveInbox(ctx, in)
	if err != nil {
		return refuse(output, "binding", err)
	}
	result := memoryhook.RunInbox(ctx, memoryhook.InboxInvocation{Config: checked.Config, Memory: checked.Memory, Event: checked.Event, Observation: frame.Get("observation"), State: frame.Get("state"), Control: string(control.String), Started: time.Now(), Directory: in.Directory})
	return reply(output, result.Wire())
}

func refuse(out io.Writer, stage string, err error) int {
	return reply(out, (memoryhook.InboxResult{Stage: stage, Err: err}).Wire())
}

func reply(out io.Writer, frame *value.Value) int {
	var encoded bytes.Buffer
	yes, no := true, false
	err := value.WriteKernel(&encoded, frame, value.WriterRoute{EnsureASCII: &no, Compact: &yes, TrailingLF: &yes, Codec: &value.Codec{Name: "utf-8", Errors: "strict"}}, 0)
	if err != nil || encoded.Len() > ReplyLimit {
		return 1
	}
	if _, err = out.Write(encoded.Bytes()); err != nil {
		return 1
	}
	return 0
}
