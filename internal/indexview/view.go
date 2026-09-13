// Package indexview presents a bounded index with directly usable pull arguments.
package indexview

import (
	"encoding/json"
	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"strings"
	"time"
)

func invalid(message string) error { return &core.Error{Code: "INVALID_REQUEST", Message: message} }

type Entry struct {
	core.IndexEntry
	PullCommand   string             `json:"pull_command,omitempty"`
	PullArguments core.ExpandRequest `json:"pull_arguments"`
}

// This view preserves semantic fields but is not itself a sealed package.
type View struct {
	core.SemanticPackage
	Schema           string    `json:"schema"`
	Index            []Entry   `json:"index"`
	SourceSchema     string    `json:"source_schema"`
	SourceSeal       string    `json:"source_seal"`
	ReceiptID        string    `json:"receipt_id"`
	RequestID        string    `json:"request_id"`
	ExpiresAt        time.Time `json:"expires_at"`
	CreditsRemaining int       `json:"credits_remaining"`
	BytesRemaining   int       `json:"bytes_remaining"`
}

func Present(result core.IndexResult, request string, command []string, room int) (View, error) {
	view := View{SemanticPackage: result.Package.Semantic, Schema: "cairn.agent-search/1",
		SourceSchema: result.Package.Semantic.Schema, SourceSeal: result.Package.Seal, ReceiptID: result.Package.ReceiptID,
		RequestID: request, Index: []Entry{}, ExpiresAt: result.ExpiresAt,
		CreditsRemaining: result.CreditsRemaining, BytesRemaining: result.BytesRemaining}
	type versionKey struct {
		record  string
		version int
	}
	handles := make(map[versionKey]string, len(result.Handles))
	for _, handle := range result.Handles {
		key := versionKey{handle.RecordID, handle.Version}
		if _, exists := handles[key]; exists || handle.Handle == "" {
			return View{}, invalid("index response has duplicate or empty handles")
		}
		handles[key] = handle.Handle
	}
	for _, entry := range result.Package.Semantic.Index {
		handle, exists := handles[versionKey{entry.RecordID, entry.Version}]
		if !exists {
			return View{}, invalid("index response has no handle for a record version")
		}
		pull := core.ExpandRequest{RequestID: uuid.NewString(), ReceiptID: result.Package.ReceiptID, Handle: handle}
		var pullCommand string
		if len(command) != 0 {
			argv := append(append([]string{}, command...), "pull", "--request-id", pull.RequestID, pull.ReceiptID, pull.Handle)
			pullCommand = ShellCommand(argv)
		}
		view.Index = append(view.Index, Entry{entry, pullCommand, pull})
	}
	encode := func() ([]byte, error) {
		return json.Marshal(struct {
			Schema string `json:"schema"`
			OK     bool   `json:"ok"`
			Status string `json:"status"`
			Data   View   `json:"data"`
		}{"cairn.response/1", true, "OK", view})
	}
	encoded, err := encode()
	if err != nil {
		return View{}, err
	}
	if len(encoded)+1 > room && len(command) != 0 {
		// Shell commands duplicate the complete structured arguments. Keep every
		// semantic entry and its handle when the convenience strings do not fit.
		for i := range view.Index {
			view.Index[i].PullCommand = ""
		}
		encoded, err = encode()
		if err != nil {
			return View{}, err
		}
	}
	if len(encoded)+1 > room {
		return View{}, &core.Error{Code: "BUDGET_REFUSED", Message: "search response with structured pull arguments exceeds input room; increase --tokens"}
	}
	return view, nil
}

func ShellCommand(args []string) string {
	words := make([]string, len(args))
	for i, word := range args {
		if word != "" && strings.Trim(word, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-/.:@=") == "" {
			words[i] = word
		} else {
			words[i] = "'" + strings.ReplaceAll(word, "'", "'\"'\"'") + "'"
		}
	}
	return strings.Join(words, " ")
}
