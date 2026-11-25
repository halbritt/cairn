package main

import (
	"bytes"
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os/exec"
	"sync"
	"syscall"

	"github.com/halbritt/cairn/internal/outputbound"
)

type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (s *syncBuffer) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.buf.Write(p)
}

func execBoundedCommand(ctx context.Context, args []string, stdin io.Reader, out, errOut io.Writer) (int, error) {
	f := flag.NewFlagSet("cairn exec-bounded", flag.ContinueOnError)
	f.SetOutput(io.Discard)
	maxFlag := f.Int("output-max-bytes", outputbound.DefaultOutputMaxBytes, "maximum bounded output bytes (0 = unbounded)")
	fullOutput := f.Bool("full-output", false, "disable output bounding and pass stdout/stderr through")
	spoolDir := f.String("spool-dir", "", "override spool directory for oversized output")
	if err := f.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			_, _ = fmt.Fprintln(out, "Usage: cairn exec-bounded [--output-max-bytes N] [--full-output] [--spool-dir DIR] -- COMMAND [ARGS...]")
			return 0, nil
		}
		return 2, err
	}
	cmdArgs := f.Args()
	if len(cmdArgs) == 0 {
		return 2, errors.New("exec-bounded requires -- COMMAND [ARGS...]")
	}
	maxFlagSet := false
	f.Visit(func(fl *flag.Flag) {
		if fl.Name == "output-max-bytes" {
			maxFlagSet = true
		}
	})
	if *maxFlag < 0 || *maxFlag > 10000000 {
		return 2, errors.New("--output-max-bytes must be between 0 and 10000000")
	}

	var maxBytes int
	switch {
	case *fullOutput:
		maxBytes = 0
	case maxFlagSet:
		maxBytes = outputbound.ResolveMaxBytes(*maxFlag, "CAIRN_CLI_OUTPUT_MAX_BYTES", "CAIRN_TOOL_OUTPUT_MAX_BYTES")
	default:
		maxBytes = outputbound.ResolveMaxBytes(-1, "CAIRN_CLI_OUTPUT_MAX_BYTES", "CAIRN_TOOL_OUTPUT_MAX_BYTES")
	}

	var stdoutBuf, stderrBuf bytes.Buffer
	var combined syncBuffer
	cmd := exec.CommandContext(ctx, cmdArgs[0], cmdArgs[1:]...)
	cmd.Stdin = stdin
	cmd.Stdout = io.MultiWriter(&stdoutBuf, &combined)
	cmd.Stderr = io.MultiWriter(&stderrBuf, &combined)

	runErr := cmd.Run()
	exitCode := 0
	sigName := ""
	if runErr != nil {
		var exitErr *exec.ExitError
		if !errors.As(runErr, &exitErr) {
			return 127, runErr
		}
		exitCode = exitErr.ExitCode()
		if ws, ok := exitErr.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
			sigName = ws.Signal().String()
			if exitCode < 0 {
				exitCode = 128 + int(ws.Signal())
			}
		}
	}

	if maxBytes <= 0 || combined.buf.Len() <= maxBytes {
		if stdoutBuf.Len() > 0 {
			if _, err := out.Write(stdoutBuf.Bytes()); err != nil {
				return 1, err
			}
		}
		if stderrBuf.Len() > 0 {
			if _, err := errOut.Write(stderrBuf.Bytes()); err != nil {
				return 1, err
			}
		}
		return exitCode, nil
	}

	header := ""
	if exitCode != 0 {
		if sigName != "" {
			header = fmt.Sprintf("[cairn exec-bounded] exit_code=%d signal=%s\n", exitCode, sigName)
		} else {
			header = fmt.Sprintf("[cairn exec-bounded] exit_code=%d\n", exitCode)
		}
	}
	excerptBudget := maxBytes - len(header)
	if excerptBudget < 64 {
		excerptBudget = maxBytes
		header = ""
	}
	res, err := outputbound.ExcerptWithSpool(combined.buf.Bytes(), excerptBudget, *spoolDir, "log", outputbound.ExecMeta{
		ExitCode: &exitCode,
		Signal:   sigName,
	})
	if err != nil {
		return 1, err
	}
	dest := out
	if stdoutBuf.Len() == 0 && stderrBuf.Len() > 0 {
		dest = errOut
	}
	if _, err := io.WriteString(dest, header+res.Output); err != nil {
		return 1, err
	}
	return exitCode, nil
}
