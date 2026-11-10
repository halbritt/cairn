//go:build linux

// cairn-stage-lifecycle publishes a pinned executable without activating it.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"

	"github.com/halbritt/cairn/internal/lifecycle/install"
)

func run(ctx context.Context, args []string, out, diagnostic io.Writer) error {
	flags := flag.NewFlagSet("cairn-stage-lifecycle", flag.ContinueOnError)
	flags.SetOutput(diagnostic)
	root := flags.String("root", "", "existing persistent private staging directory")
	binary := flags.String("binary", "", "absolute source executable path")
	digest := flags.String("sha256", "", "independently selected source SHA-256")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 || *root == "" || *binary == "" || *digest == "" {
		return fmt.Errorf("--root, --binary and --sha256 are required; positional arguments are not accepted")
	}
	staged, err := install.StageBinary(ctx, *root, *binary, *digest)
	if err != nil {
		return err
	}
	return json.NewEncoder(out).Encode(staged)
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := runCommand(ctx, os.Args[1:], os.Stdin, os.Stdout, os.Stderr); err != nil && err != flag.ErrHelp {
		fmt.Fprintln(os.Stderr, "cairn-stage-lifecycle:", err)
		os.Exit(1)
	}
}
