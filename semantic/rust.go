package semantic

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/halbritt/cairn/core"
)

type RustMode string

const (
	RustRank   RustMode = "rank"
	RustStream RustMode = "stream"
	RustEmbed  RustMode = "embed"
)

// RustWorkerConfig is explicit operator configuration, never request data.
// Arguments and the child environment are constructed here, without a shell.
type RustWorkerConfig struct {
	Mode       RustMode `json:"mode"`
	Executable string   `json:"executable"`
	ModelDir   string   `json:"model_dir"`
	ORTRuntime string   `json:"ort_runtime"`
	OpenBLAS   string   `json:"openblas"`
	GFortran   string   `json:"gfortran"`
	Quadmath   string   `json:"quadmath"`
	SourceRoot string   `json:"source_root"`
}

type workerLaunch struct {
	path string
	args []string
	env  []string
}

// ReadRustWorkerConfig reads one bounded, strict configuration. Nothing starts
// a worker until the caller selects the corresponding supervisor.
func ReadRustWorkerConfig(path string) (RustWorkerConfig, error) {
	var config RustWorkerConfig
	if !filepath.IsAbs(path) {
		return config, fmt.Errorf("Rust worker configuration must be an absolute path")
	}
	file, err := os.Open(path)
	if err != nil {
		return config, err
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, 32769))
	if err != nil {
		return config, err
	}
	if len(data) > 32768 {
		return config, fmt.Errorf("Rust worker configuration exceeds 32768 bytes")
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&config); err != nil {
		return config, fmt.Errorf("invalid Rust worker configuration: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return config, fmt.Errorf("expected one Rust worker configuration object")
	}
	return config, config.Validate()
}

func (c RustWorkerConfig) Validate() error {
	switch c.Mode {
	case RustRank, RustStream, RustEmbed:
	default:
		return fmt.Errorf("Rust worker mode must be rank, stream or embed")
	}
	if err := validateCommand(c.Executable); err != nil {
		return fmt.Errorf("Rust worker executable: %w", err)
	}
	for _, item := range []struct {
		name, path string
		directory  bool
	}{
		{"model_dir", c.ModelDir, true},
		{"ort_runtime", c.ORTRuntime, false},
		{"openblas", c.OpenBLAS, false},
		{"gfortran", c.GFortran, false},
		{"quadmath", c.Quadmath, false},
		{"source_root", c.SourceRoot, true},
	} {
		if !filepath.IsAbs(item.path) {
			return fmt.Errorf("Rust worker %s must be an absolute path", item.name)
		}
		info, err := os.Stat(item.path)
		if err != nil {
			return fmt.Errorf("Rust worker %s: %w", item.name, err)
		}
		if item.directory && !info.IsDir() || !item.directory && !info.Mode().IsRegular() {
			return fmt.Errorf("Rust worker %s has the wrong file type", item.name)
		}
	}
	return nil
}

func (c RustWorkerConfig) launch(mode RustMode) (workerLaunch, error) {
	if err := c.Validate(); err != nil {
		return workerLaunch{}, err
	}
	if c.Mode != mode {
		return workerLaunch{}, fmt.Errorf("Rust worker mode %q cannot use the %s supervisor", c.Mode, mode)
	}
	args := []string{
		"--model-dir", c.ModelDir, "--ort-runtime", c.ORTRuntime,
		"--openblas", c.OpenBLAS, "--gfortran", c.GFortran,
		"--quadmath", c.Quadmath, "--source-root", c.SourceRoot,
	}
	if mode == RustStream {
		args = append(args, "--stream")
	}
	return workerLaunch{path: c.Executable, args: args,
		env: append(workerEnvironment(), "ORT_DISABLE_TELEMETRY=1")}, nil
}

func RustCommand(config RustWorkerConfig) (core.SemanticRanker, error) {
	launch, err := config.launch(RustRank)
	if err != nil {
		return nil, err
	}
	return command(launch), nil
}

func RustStreamCommand(owner context.Context, config RustWorkerConfig, idle time.Duration) (core.SemanticRanker, func(), error) {
	if idle <= 0 {
		return nil, nil, fmt.Errorf("semantic worker idle timeout must be positive")
	}
	launch, err := config.launch(RustStream)
	if err != nil {
		return nil, nil, err
	}
	return streamCommand(owner, launch, idle)
}

func RustEmbeddingCommand(owner context.Context, config RustWorkerConfig) (*CommandEmbedder, error) {
	launch, err := config.launch(RustEmbed)
	if err != nil {
		return nil, err
	}
	return embeddingCommand(owner, launch), nil
}
