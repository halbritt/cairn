// The comparison driver uses the production Go supervisors and their deadlines.
// It opens no database and returns worker failures without lexical substitution.
package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/semantic"
)

func decode(line []byte, output any) error {
	d := json.NewDecoder(bytes.NewReader(line))
	d.DisallowUnknownFields()
	if err := d.Decode(output); err != nil {
		return err
	}
	var extra any
	if err := d.Decode(&extra); err != io.EOF {
		return fmt.Errorf("expected one input object")
	}
	return nil
}

func run() error {
	configPath := flag.String("config", "", "absolute Rust worker configuration")
	flag.Parse()
	if flag.NArg() != 0 {
		return fmt.Errorf("unexpected arguments")
	}
	config, err := semantic.ReadRustWorkerConfig(*configPath)
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	var rank core.SemanticRanker
	var model *semantic.CommandEmbedder
	switch config.Mode {
	case semantic.RustRank:
		rank, err = semantic.RustCommand(config)
	case semantic.RustStream:
		var closeWorker func()
		rank, closeWorker, err = semantic.RustStreamCommand(ctx, config, 30*time.Second)
		if err == nil {
			defer closeWorker()
		}
	case semantic.RustEmbed:
		model, err = semantic.RustEmbeddingCommand(ctx, config)
		if err == nil {
			defer model.Close()
		}
	}
	if err != nil {
		return err
	}
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 65536), 8*1024*1024)
	encoder := json.NewEncoder(os.Stdout)
	for scanner.Scan() {
		started := time.Now()
		if model == nil {
			var input struct {
				ID      string                   `json:"id"`
				Request core.SemanticRankRequest `json:"request"`
			}
			if err := decode(scanner.Bytes(), &input); err != nil {
				return err
			}
			result, err := rank(ctx, input.Request)
			if err != nil {
				return fmt.Errorf("%s: %w", input.ID, err)
			}
			if err := encoder.Encode(struct {
				ID     string                  `json:"id"`
				Result core.SemanticRankResult `json:"result"`
			}{input.ID, result}); err != nil {
				return err
			}
			fmt.Fprintf(os.Stderr, "%s %.6fs\n", input.ID, time.Since(started).Seconds())
			continue
		}
		var input struct {
			ID        string `json:"id"`
			Operation string `json:"operation"`
			Text      string `json:"text"`
		}
		if err := decode(scanner.Bytes(), &input); err != nil {
			return err
		}
		identity, err := model.Identity(ctx)
		if err != nil {
			return err
		}
		reply := map[string]any{"id": input.ID, "identity": identity}
		switch input.Operation {
		case "identity":
			if input.Text != "" {
				return fmt.Errorf("identity text must be empty")
			}
		case "query":
			vector, projection, err := model.QueryWithProjection(ctx, input.Text)
			if err != nil {
				return fmt.Errorf("%s: %w", input.ID, err)
			}
			reply["vector"], reply["query_projection"] = vector, projection
		case "document":
			passages, err := model.Document(ctx, input.Text)
			if err != nil {
				return fmt.Errorf("%s: %w", input.ID, err)
			}
			reply["passages"] = passages
		default:
			return fmt.Errorf("unknown operation %q", input.Operation)
		}
		if err := encoder.Encode(reply); err != nil {
			return err
		}
		fmt.Fprintf(os.Stderr, "%s %.6fs\n", input.ID, time.Since(started).Seconds())
	}
	return scanner.Err()
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
