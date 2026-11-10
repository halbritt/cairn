package main

import (
	"context"
	"flag"
	"io"
	"os"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/lifecycle/binding"
)

func lifecycleCommand(ctx context.Context, args []string, input io.Reader) (any, error) {
	if !lifecycleCommandEnabled {
		return nil, &core.Error{Code: "LIFECYCLE_DISABLED", Message: "lifecycle is disabled; activation requires an owner grant"}
	}
	return executeLifecycle(ctx, args, input)
}

func executeLifecycle(ctx context.Context, args []string, input io.Reader) (any, error) {
	if len(args) > 0 && args[0] == "coordination" {
		return lifecycleCoordinationPlan{args: args[1:], input: input}, nil
	}
	return executeLifecycleCheck(ctx, args, input)
}

// This is the actual handler behind the closed public registration, not a
// second fixture implementation. F-04 activation is deliberately not dispatched.
func executeLifecycleCheck(ctx context.Context, args []string, input io.Reader) (any, error) {
	if len(args) == 0 || args[0] != "check" {
		return nil, invalid("expected lifecycle check")
	}
	flags := flag.NewFlagSet("lifecycle check", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	in := binding.Invocation{}
	flags.StringVar(&in.EntryPoint, "entrypoint", "", "memory or coordination")
	flags.StringVar(&in.ConfigPath, "config", "", "selected configuration")
	flags.StringVar(&in.ReceiptPath, "receipt", "", "independently pinned compatibility receipt")
	flags.StringVar(&in.ReceiptSHA256, "receipt-sha256", "", "external receipt digest")
	if err := flags.Parse(args[1:]); err != nil {
		return nil, invalid("invalid lifecycle check arguments")
	}
	if flags.NArg() != 0 {
		return nil, invalid("unexpected lifecycle check arguments")
	}
	var err error
	in.Directory, err = os.Getwd()
	if err != nil {
		return nil, err
	}
	in.Environment = os.Environ()
	in.Event, err = io.ReadAll(io.LimitReader(input, 1024*1024+1))
	if err != nil {
		return nil, err
	}
	return binding.Check(ctx, in)
}
