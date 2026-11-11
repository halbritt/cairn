// Exercise fixed package launchers through the existing Go owners.
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

type input struct {
	ID        string                   `json:"id"`
	Request   core.SemanticRankRequest `json:"request"`
	Operation string                   `json:"operation"`
	Text      string                   `json:"text"`
}

type driver struct {
	rank  core.SemanticRanker
	embed *semantic.CommandEmbedder
}

func open(ctx context.Context, mode, path string) (driver, func(), error) {
	d := driver{}
	closeWorker := func() {}
	var err error
	switch mode {
	case "rank":
		d.rank, err = semantic.Command(path)
	case "stream":
		d.rank, closeWorker, err = semantic.StreamCommandWithIdleTimeout(ctx, path, 30*time.Second)
	case "embed":
		d.embed, err = semantic.EmbeddingCommand(ctx, path)
		if err == nil {
			closeWorker = d.embed.Close
		}
	default:
		err = fmt.Errorf("unknown mode %q", mode)
	}
	return d, closeWorker, err
}

func decode(line []byte) (input, error) {
	var in input
	d := json.NewDecoder(bytes.NewReader(line))
	d.DisallowUnknownFields()
	if err := d.Decode(&in); err != nil {
		return in, err
	}
	var extra any
	if err := d.Decode(&extra); err != io.EOF || in.ID == "" {
		return in, fmt.Errorf("expected one tagged input object")
	}
	return in, nil
}

func (d driver) call(ctx context.Context, in input) (any, error) {
	if d.embed == nil {
		result, err := d.rank(ctx, in.Request)
		return map[string]any{"id": in.ID, "result": result}, err
	}
	identity, err := d.embed.Identity(ctx)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"id": in.ID, "identity": identity}
	switch in.Operation {
	case "query":
		out["vector"], out["query_projection"], err = d.embed.QueryWithProjection(ctx, in.Text)
	case "document":
		out["passages"], err = d.embed.Document(ctx, in.Text)
	default:
		err = fmt.Errorf("unknown inference operation %q", in.Operation)
	}
	return out, err
}

func run() error {
	mode := flag.String("mode", "", "rank, stream or embed")
	path := flag.String("launcher", "", "immutable absolute package launcher")
	flag.Parse()
	if flag.NArg() != 0 {
		return fmt.Errorf("unexpected arguments")
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	d, closeWorker, err := open(ctx, *mode, *path)
	if err != nil {
		return err
	}
	defer closeWorker()
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 65536), 8*1024*1024)
	for scanner.Scan() {
		in, err := decode(scanner.Bytes())
		if err != nil {
			return err
		}
		started := time.Now()
		out, err := d.call(ctx, in)
		if err != nil {
			return fmt.Errorf("%s: %w", in.ID, err)
		}
		if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
			return err
		}
		fmt.Fprintf(os.Stderr, "%s %.6fs\n", in.ID, time.Since(started).Seconds())
	}
	return scanner.Err()
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
