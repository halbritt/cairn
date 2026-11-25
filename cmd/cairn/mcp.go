package main

import (
	"context"
	"flag"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/outputbound"
	"github.com/halbritt/cairn/localapi"
	"github.com/halbritt/cairn/mcpapi"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"os"
	"path/filepath"
)

type mcpOptions struct {
	socket  string
	token   string
	harness string
	config  mcpapi.Config
	pins    core.ContextPins
}

func mcpFlags(f *flag.FlagSet) *mcpOptions {
	o := &mcpOptions{}
	f.StringVar(&o.socket, "socket", "", "Cairn Unix socket (required)")
	f.StringVar(&o.token, "token-file", "", "owner-only ordinary agent token (required)")
	f.StringVar(&o.config.Scope.Repo, "repo", "", "repository identity (required)")
	f.StringVar(&o.config.Scope.TaskID, "task", "", "host task identity (required unless --codex-thread)")
	f.StringVar(&o.config.Scope.RunID, "run", "", "host run identity (required unless --codex-thread)")
	f.BoolVar(&o.config.CodexThread, "codex-thread", false, "group searches by Codex tool-call thread metadata instead of --task/--run")
	f.IntVar(&o.config.AvailableTokens, "tokens", 16000, "memory input room per tool result")
	f.IntVar(&o.config.MaxTokens, "max-tokens", 160000, "maximum explicit search input room and pull output; --tokens remains the default")
	f.IntVar(&o.config.OutputMaxBytes, "output-max-bytes", outputbound.DefaultOutputMaxBytes, "maximum bounded output bytes for non-strict tools (0 = unbounded)")
	f.StringVar(&o.config.SearchView, "search-view", "compact/1", "model-facing search presentation: compact/1 or full")
	f.StringVar(&o.config.ToolProfile, "tool-profile", "compact/2", "model-facing tool descriptions: compact/1 (search/pull), compact/2 (all tools), or full")
	f.BoolVar(&o.config.Messaging, "messaging", false, "also expose session inbox tools (agents, send, inbox, ack, complete, renew, event status)")
	f.StringVar(&o.config.NativeSessionID, "native-session", "", "native conversation ID registered for this session (requires --messaging; defaults to CLAUDE_CODE_SESSION_ID)")
	f.StringVar(&o.config.CoordinationStateRoot, "coordination-state-root", "", "coordination state directory for host-process session lookup (requires --messaging)")
	f.StringVar(&o.pins.Revision, "revision", "", "declared repository revision")
	f.StringVar(&o.pins.WorkspaceSHA256, "workspace-sha256", "", "declared workspace digest")
	f.StringVar(&o.pins.TaskClass, "task-class", "", "task category")
	f.StringVar(&o.pins.TaskPhase, "task-phase", "", "declared task phase (exact label)")
	f.StringVar(&o.pins.BindingID, "binding", "", "binding identity")
	f.StringVar(&o.pins.CapabilityID, "capability", "", "capability identity")
	f.StringVar(&o.harness, "client-harness", "", "optional harness label reported in client observations (codex, claude, opencode, hermes, agy, other); reported, never verified or inferred")
	return o
}

func (o *mcpOptions) validate(positional int) error {
	if positional != 0 || o.socket == "" || o.token == "" {
		return invalid("MCP requires --socket and --token-file and accepts no positional arguments")
	}
	if err := validateHarnessText(o.socket, o.token, o.config.CoordinationStateRoot); err != nil {
		return err
	}
	if o.config.CoordinationStateRoot != "" {
		absolute, err := filepath.Abs(o.config.CoordinationStateRoot)
		if err != nil {
			return err
		}
		o.config.CoordinationStateRoot = absolute
	}
	if o.harness != "" && !map[string]bool{"codex": true, "claude": true, "opencode": true, "hermes": true, "agy": true, "other": true}[o.harness] {
		return invalid("--client-harness must be one of codex, claude, opencode, hermes, agy, other")
	}
	// The host conversation is declared by the launcher, never inferred from
	// the model or account. Codex declares it per tool call instead.
	if o.pins != (core.ContextPins{}) {
		o.config.Context = &o.pins
	}
	if o.config.OutputMaxBytes == 0 {
		o.config.UnboundedOutput = true
	}
	return o.config.Validate()
}

// declaration is what this MCP process reports about itself. The harness is
// only ever the explicitly configured label; absent configuration is unknown.
func (o *mcpOptions) declaration() localapi.ClientDiagnostics {
	harness := o.harness
	if harness == "" {
		harness = "unknown"
	}
	return localapi.ClientDiagnostics{Surface: "mcp", Harness: harness, RetrievalCapabilities: localapi.CurrentRetrievalCapabilities(), ReaderCapabilities: localapi.CurrentReaderCapabilities()}
}

func serveMCP(ctx context.Context, args []string) error {
	f := flags("mcp")
	o := mcpFlags(f)
	if err := parseHarnessFlags(f, args, os.Stdout); err != nil {
		return err
	}
	if err := o.validate(f.NArg()); err != nil {
		return err
	}
	outputMaxSet := false
	f.Visit(func(option *flag.Flag) {
		if option.Name == "output-max-bytes" {
			outputMaxSet = true
		}
	})
	if !outputMaxSet {
		resolved := outputbound.ResolveMaxBytes(-1, "CAIRN_MCP_OUTPUT_MAX_BYTES", "CAIRN_TOOL_OUTPUT_MAX_BYTES")
		o.config.OutputMaxBytes = resolved
		o.config.UnboundedOutput = resolved == 0
	} else if outputbound.ResolveMaxBytes(o.config.OutputMaxBytes, "CAIRN_MCP_OUTPUT_MAX_BYTES") == 0 {
		o.config.OutputMaxBytes = 0
		o.config.UnboundedOutput = true
	}
	// The host conversation is declared by the launcher, never inferred from
	// the model or account. Codex declares it per tool call instead. Generated
	// configuration never captures the generating shell's conversation.
	if o.config.Messaging && !o.config.CodexThread && o.config.NativeSessionID == "" {
		o.config.NativeSessionID = os.Getenv("CLAUDE_CODE_SESSION_ID")
		if o.config.NativeSessionID == "" && o.config.CoordinationStateRoot == "" {
			o.config.CoordinationStateRoot = defaultCoordinationStateRoot()
		}
	}
	client, err := localapi.NewClient(o.socket, o.token)
	if err != nil {
		return err
	}
	defer client.Close()
	// The facade declares itself once, at construction, so replacing the installed
	// executable never changes what an already running process reports.
	declared := client.WithDiagnostics(o.declaration())
	server, err := mcpapi.NewServer(declared, o.config)
	if err != nil {
		return err
	}
	return server.Run(ctx, &mcp.StdioTransport{})
}

func defaultCoordinationStateRoot() string {
	var base string
	if configured := os.Getenv("CAIRN_HOME"); configured != "" {
		abs, err := filepath.Abs(configured)
		if err != nil {
			return ""
		}
		base = abs
	} else if xdg := os.Getenv("XDG_DATA_HOME"); xdg != "" && filepath.IsAbs(xdg) {
		base = filepath.Join(xdg, "cairn")
	} else {
		dir, err := dataDirectory()
		if err != nil {
			return ""
		}
		base = dir
	}
	root := filepath.Join(base, "coordination", "state")
	if !filepath.IsAbs(root) {
		return ""
	}
	info, err := os.Lstat(root)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return ""
	}
	return root
}
