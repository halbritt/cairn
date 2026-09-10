// Package semantic adapts a bounded local CPU worker to Cairn's eligible-note
// scoring boundary. No executable or model configuration comes from an agent.
package semantic

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"

	"github.com/halbritt/cairn/core"
)

// Leave time to deliver fallback before native clients' 30-second deadline.
const workerTimeout = 25 * time.Second

type boundedOutput struct{ buffer bytes.Buffer }

func (b *boundedOutput) Write(p []byte) (int, error) {
	if len(p) > 65536-b.buffer.Len() {
		return 0, fmt.Errorf("semantic worker output exceeds limit")
	}
	return b.buffer.Write(p)
}

// Command permits one worker at a time across callers. Busy, failed or timed-out
// workers return errors for the compiler's labelled lexical fallback.
func Command(path string) (core.SemanticRanker, error) {
	if err := validateCommand(path); err != nil {
		return nil, err
	}
	busy := make(chan struct{}, 1)
	return func(ctx context.Context, req core.SemanticRankRequest) (core.SemanticRankResult, error) {
		select {
		case busy <- struct{}{}:
			defer func() { <-busy }()
		default:
			return core.SemanticRankResult{}, fmt.Errorf("semantic worker busy")
		}
		ctx, cancel := context.WithTimeout(ctx, workerTimeout)
		defer cancel()
		input, err := json.Marshal(req)
		if err != nil {
			return core.SemanticRankResult{}, err
		}
		command := exec.CommandContext(ctx, path)
		command.Env = workerEnvironment()
		command.Stdin = bytes.NewReader(input)
		command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
		command.Cancel = func() error { return syscall.Kill(-command.Process.Pid, syscall.SIGKILL) }
		command.WaitDelay = time.Second
		output := &boundedOutput{}
		command.Stdout = output
		// Worker diagnostics may include note/query content. They are not API
		// responses or persistent logs; the receipt retains fallback disposition.
		command.Stderr = io.Discard
		if err = command.Run(); err != nil {
			return core.SemanticRankResult{}, fmt.Errorf("semantic worker failed: %w", err)
		}
		var result core.SemanticRankResult
		err = decodeOne(&output.buffer, &result)
		return result, err
	}, nil
}

func validateCommand(path string) error {
	if !filepath.IsAbs(path) {
		return fmt.Errorf("semantic command must be an absolute executable path")
	}
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0111 == 0 {
		return fmt.Errorf("semantic command must be executable")
	}
	return nil
}

func workerEnvironment() []string {
	return []string{"PATH=/usr/bin:/bin", "HF_HUB_OFFLINE=1", "HF_HUB_DISABLE_TELEMETRY=1", "TOKENIZERS_PARALLELISM=false"}
}

func decodeOne(input io.Reader, result any) error {
	decoder := json.NewDecoder(input)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(result); err != nil {
		return fmt.Errorf("invalid semantic response: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return fmt.Errorf("semantic worker must return one JSON response")
	}
	return nil
}
