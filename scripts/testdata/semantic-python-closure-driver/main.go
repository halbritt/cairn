// Exercise the production classic supervisors with fixed zero-argument entries.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/semantic"
)

func run() error {
	mode := flag.String("mode", "", "rank, stream or embed")
	launcher := flag.String("launcher", "", "absolute selected fixed launcher")
	flag.Parse()
	if flag.NArg() != 0 {
		return fmt.Errorf("unexpected arguments")
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	var rank core.SemanticRanker
	var embed *semantic.CommandEmbedder
	var err error
	switch *mode {
	case "rank":
		rank, err = semantic.Command(*launcher)
	case "stream":
		var closeWorker func()
		rank, closeWorker, err = semantic.StreamCommand(ctx, *launcher)
		if err == nil {
			defer closeWorker()
		}
	case "embed":
		embed, err = semantic.EmbeddingCommand(ctx, *launcher)
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
	encoder := json.NewEncoder(os.Stdout)
	for scanner.Scan() {
		var input struct {
			ID        string                   `json:"id"`
			Request   core.SemanticRankRequest `json:"request"`
			Operation string                   `json:"operation"`
			Text      string                   `json:"text"`
		}
		if err := json.Unmarshal(scanner.Bytes(), &input); err != nil {
			return err
		}
		reply := map[string]any{"id": input.ID}
		if embed == nil {
			result, callErr := rank(ctx, input.Request)
			if callErr != nil {
				reply["error"] = callErr.Error()
			} else {
				reply["result"] = result
			}
		} else {
			identity, callErr := embed.Identity(ctx)
			if callErr == nil {
				reply["identity"] = identity
				switch input.Operation {
				case "identity":
					if input.Text != "" {
						callErr = fmt.Errorf("identity text must be empty")
					}
				case "query":
					reply["vector"], reply["query_projection"], callErr = embed.QueryWithProjection(ctx, input.Text)
				case "document":
					reply["passages"], callErr = embed.Document(ctx, input.Text)
				default:
					callErr = fmt.Errorf("unknown operation")
				}
			}
			if callErr != nil {
				reply = map[string]any{"id": input.ID, "error": callErr.Error()}
			}
		}
		if err := encoder.Encode(reply); err != nil {
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
