package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
	"github.com/halbritt/cairn/runner"
	"golang.org/x/sys/unix"
)

// The launch replaces this CLI process. It has no observer authority, detached
// child, context file, outcome claim or retry/recovery state of its own.
type agentStartPlan struct {
	executable string
	argv       []string
	stdin      string
	expiresAt  time.Time
}

func (p agentStartPlan) execute(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if !p.expiresAt.After(time.Now()) {
		return &core.Error{Code: "STALE_HANDLE", Message: "startup index expired before launch; start again for fresh context"}
	}
	if p.stdin != "" {
		file, err := startupInput(p.stdin)
		if err != nil {
			return err
		}
		// Exec closes the original CLOEXEC descriptor and retains descriptor 0.
		// On failure the defer releases the original too.
		defer file.Close()
		if file.Fd() == 0 {
			_, err = unix.FcntlInt(file.Fd(), unix.F_SETFD, 0)
		} else {
			err = unix.Dup2(int(file.Fd()), 0)
		}
		if err != nil {
			return err
		}
	}
	return syscall.Exec(p.executable, p.argv, runner.ChildEnvironment())
}

// A Linux anonymous memory file avoids a pipe-writer process and never creates a
// named context file. Unsupported kernels refuse; there is no disk fallback.
func startupInput(input string) (*os.File, error) {
	fd, err := unix.MemfdCreate("cairn-start", unix.MFD_CLOEXEC)
	if err != nil {
		return nil, err
	}
	file := os.NewFile(uintptr(fd), "cairn-start")
	if err = file.Chmod(0600); err == nil {
		_, err = file.WriteString(input)
	}
	if err == nil {
		_, err = file.Seek(0, io.SeekStart)
	}
	if err != nil {
		return nil, errors.Join(err, file.Close())
	}
	return file, nil
}

func prepareAgentStart(ctx context.Context, client *localapi.Client, args []string) (agentStartPlan, error) {
	f := flags("agent start")
	repo := f.String("repo", defaultRepo(), "canonical repository identity")
	task := f.String("task", "", "declared task identity (required)")
	run := f.String("run", "", "declared run identity (required)")
	query := f.String("query", "", "memory search query; use --browse instead for eligible previews")
	browse := f.Bool("browse", false, "browse eligible previews without a query")
	semantic := f.Bool("semantic", false, "optional semantic search with labelled lexical fallback")
	prompt := f.String("prompt", "", "task text for the harness (or use --prompt-file)")
	promptFile := f.String("prompt-file", "", "read task text from a regular UTF-8 file instead of --prompt")
	carrier := f.String("carrier", "argv", "initial input route: argv or stdin (stdin recommended for OpenCode)")
	pullTool := f.String("pull-tool", "", "configured native body-pull tool name (required)")
	searchTool := f.String("search-tool", "", "configured native search tool name (required)")
	tokens := f.Int("tokens", 32000, "initial memory and task input room, using the UTF-8 byte upper bound")
	var kinds []string
	f.Func("kind", "optional record kind; repeat for several kinds", func(value string) error { kinds = append(kinds, value); return nil })
	pins := core.ContextPins{}
	f.StringVar(&pins.Revision, "revision", "", "declared repository revision")
	f.StringVar(&pins.WorkspaceSHA256, "workspace-sha256", "", "declared workspace digest")
	f.StringVar(&pins.TaskClass, "task-class", "", "task category")
	f.StringVar(&pins.TaskPhase, "task-phase", "", "declared task phase (exact label)")
	f.StringVar(&pins.BindingID, "binding", "", "binding identity")
	f.StringVar(&pins.CapabilityID, "capability", "", "capability identity")
	if err := f.Parse(args); err != nil {
		return agentStartPlan{}, invalid(err.Error())
	}
	if f.NArg() == 0 || strings.TrimSpace(*task) == "" || strings.TrimSpace(*run) == "" || *task == "*" || *run == "*" {
		return agentStartPlan{}, invalid("start requires explicit --task, --run and -- COMMAND ARGS...")
	}
	if *carrier != "argv" && *carrier != "stdin" {
		return agentStartPlan{}, invalid("startup carrier must be argv or stdin")
	}
	prefix, err := (runner.IndexTools{Pull: *pullTool, Search: *searchTool}).Guidance()
	if err != nil {
		return agentStartPlan{}, err
	}

	if (*browse && (*query != "" || *semantic)) || (!*browse && strings.TrimSpace(*query) == "") {
		return agentStartPlan{}, invalid("start requires --query or --browse; semantic search cannot accompany browsing")
	}
	// A single Linux argv element includes a terminating NUL and cannot exceed
	// 128 KiB. Refuse before retrieval rather than losing the task at exec.
	if *tokens < 256 || *tokens > 131071 {
		return agentStartPlan{}, invalid("startup input room must be between 256 and 131071 bytes")
	}
	var hasPrompt, hasFile bool
	f.Visit(func(value *flag.Flag) {
		hasPrompt = hasPrompt || value.Name == "prompt"
		hasFile = hasFile || value.Name == "prompt-file"
	})
	if hasPrompt == hasFile {
		return agentStartPlan{}, invalid("start requires exactly one of --prompt or --prompt-file")
	}
	if hasFile {
		text, err := readStartupTask(*promptFile, *tokens)
		if err != nil {
			return agentStartPlan{}, err
		}
		*prompt = text
	}
	if strings.TrimSpace(*prompt) == "" || !utf8.ValidString(*prompt) || strings.ContainsRune(*prompt, 0) {
		return agentStartPlan{}, invalid("startup task must be nonempty UTF-8 without NUL bytes")
	}
	command := append([]string{}, f.Args()...)
	for _, arg := range command {
		if strings.ContainsRune(arg, 0) {
			return agentStartPlan{}, invalid("command arguments cannot contain NUL bytes")
		}
	}
	executable, err := exec.LookPath(command[0])
	if err != nil {
		return agentStartPlan{}, invalid("startup executable is unavailable")
	}

	suffix := "\nTASK\n" + *prompt
	room := *tokens - len(prefix) - len(suffix)
	if room < 256 {
		return agentStartPlan{}, &core.Error{Code: "BUDGET_REFUSED", Message: "task and startup guidance leave insufficient memory input room"}
	}
	request := core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: *repo, TaskID: *task, RunID: *run},
		Query: *query, Purpose: "context", AvailableTokens: room, Kinds: kinds, Context: &pins, Semantic: *semantic}
	if *browse {
		offset := 0
		request.BrowseOffset = &offset
	}
	var result core.IndexResult
	if err := client.Call(ctx, "index", request, &result); err != nil {
		return agentStartPlan{}, err
	}
	if result.Package.Semantic.Destination != (core.Destination{Name: "hosted", AllowLocal: false}) {
		return agentStartPlan{}, &core.Error{Code: "DESTINATION_PROHIBITED", Message: "start requires a hosted-destination profile; no local memory may enter the harness"}
	}
	view, err := presentAgentSearch(result, request.RequestID, nil, room)
	if err != nil {
		return agentStartPlan{}, err
	}
	body, err := json.Marshal(view)
	if err != nil {
		return agentStartPlan{}, err
	}
	input := prefix + string(body) + suffix
	if len(input) > *tokens {
		return agentStartPlan{}, &core.Error{Code: "BUDGET_REFUSED", Message: "startup context and task exceed input room"}
	}
	plan := agentStartPlan{executable: executable, argv: command, expiresAt: result.ExpiresAt}
	if *carrier == "stdin" {
		plan.stdin = input
	} else {
		plan.argv = append(plan.argv, input)
	}
	return plan, nil
}

func readStartupTask(path string, limit int) (string, error) {
	body, err := readRegularFilePrefix(path, limit+1)
	if err != nil {
		return "", err
	}
	if len(body) > limit {
		return "", &core.Error{Code: "BUDGET_REFUSED", Message: "task file exceeds startup input room"}
	}
	return string(body), nil
}
