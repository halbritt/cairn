// Package fixture: actual Go supervisors, fixed zero-argument launchers, no DB.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/semantic"
)

func run() error {
	mode := flag.String("mode", "", "rank, stream or embed")
	path := flag.String("launcher", "", "selected absolute immutable launcher")
	idle := flag.Duration("idle", 3*time.Second, "fixture idle timeout")
	control := flag.Bool("observer-control", false, "exercise connect and telemetry-store observer")
	flag.Parse()
	if flag.NArg() != 0 {
		return fmt.Errorf("unexpected arguments")
	}
	if *control {
		connection, _ := net.DialTimeout("tcp", "127.0.0.1:1", time.Second)
		if connection != nil {
			if err := connection.Close(); err != nil {
				return err
			}
		}
		store := "/nonexistent/.cache/Microsoft/DeveloperTools/.onnxruntime/onnxruntime.db"
		if err := os.MkdirAll(filepath.Dir(store), 0o700); err != nil {
			return err
		}
		return os.WriteFile(store, []byte("observer control; no telemetry payload"), 0o600)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var rank core.SemanticRanker
	var embed *semantic.CommandEmbedder
	var err error
	switch *mode {
	case "rank":
		rank, err = semantic.Command(*path)
	case "stream":
		var closeWorker func()
		rank, closeWorker, err = semantic.StreamCommandWithIdleTimeout(ctx, *path, *idle)
		if err == nil {
			defer closeWorker()
		}
	case "embed":
		embed, err = semantic.EmbeddingCommand(ctx, *path)
		if err == nil {
			defer embed.Close()
		}
	default:
		return fmt.Errorf("unknown mode")
	}
	if err != nil {
		return err
	}
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 65536), 8*1024*1024)
	for scanner.Scan() {
		var input struct {
			Request core.SemanticRankRequest `json:"request"`
		}
		if err := json.Unmarshal(scanner.Bytes(), &input); err != nil {
			return err
		}
		var result any
		if embed != nil {
			result, err = embed.Identity(ctx)
		} else {
			result, err = rank(ctx, input.Request)
		}
		output := map[string]any{"result": result}
		if err != nil {
			output = map[string]any{"error": err.Error()}
		}
		if err := json.NewEncoder(os.Stdout).Encode(output); err != nil {
			return err
		}
	}
	return scanner.Err()
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
