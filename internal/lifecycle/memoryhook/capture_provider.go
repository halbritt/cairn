package memoryhook

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const captureOutputLimit = 1 << 20

func captureModelName(v *value.Value) bool {
	if v == nil || v.Kind != value.String || len(v.String) == 0 || len(v.String) > 256 || text(v) != strip(text(v)) {
		return false
	}
	for _, r := range v.String {
		if pySpace(r) || r < 32 || r >= 127 && r <= 159 {
			return false
		}
	}
	return true
}

type selectorSpec struct {
	schema, prompt, stage string
	timeout               time.Duration
}

func captureProvider(ctx context.Context, config, excerpt, observed *value.Value) (*value.Value, error) {
	return selectorProvider(ctx, config, excerpt, observed, selectorSpec{captureSchemaJSON, capturePrompt, "capture", 35 * time.Second})
}

func selectorProvider(ctx context.Context, config, excerpt, observed *value.Value, spec selectorSpec) (*value.Value, error) {
	if config.Get("claude") == nil {
		return nil, &value.Error{Class: "KeyError", Message: "claude"}
	}
	executable, err := pathValue(config.Get("claude"))
	if err != nil {
		return nil, err
	}
	for _, key := range []string{"preview_model", "recall_model"} {
		if v := config.Get(key); v != nil && !captureModelName(v) {
			return nil, fail("invalid lifecycle " + key + ": expected a nonempty model name without whitespace or controls")
		}
	}
	schema, err := value.DecodeText([]byte(spec.schema), "utf-8", 0)
	if err != nil {
		return nil, err
	}
	schemaJSON, err := encoded(schema, false)
	if err != nil {
		return nil, err
	}
	argv := []string{"--print", "--output-format", "json", "--disable-slash-commands", "--no-session-persistence", "--setting-sources", "", "--settings", `{"disableAllHooks":true}`, "--strict-mcp-config", "--mcp-config", `{"mcpServers":{}}`, "--tools", "", "--max-turns", "2", "--json-schema", string(schemaJSON), "--system-prompt", spec.prompt}
	model := config.Get("model")
	keys := []string{"model"}
	if spec.stage == "preview" {
		keys = []string{"preview_model", "recall_model", "model"}
	}
	if spec.stage == "recall" {
		keys = []string{"recall_model", "model"}
	}
	for _, key := range keys {
		if candidate := config.Get(key); value.Truth(candidate) {
			model = candidate
			break
		}
	}

	if value.Truth(model) {
		if model.Kind != value.String {
			return nil, &value.Error{Class: "TypeError", Message: "selector model must be a string"}
		}
		argv = append(argv, "--model", text(model))
	} else {
		model = nil
	}
	work, err := os.MkdirTemp("", "cairn-selection-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(work)
	observed.Set("requested_model", model)
	input, err := encoded(excerpt, false)
	if err != nil {
		return nil, err
	}
	env := make([]string, 0, len(os.Environ())+1)
	for _, entry := range os.Environ() {
		if !strings.HasPrefix(entry, "CLAUDECODE=") && !strings.HasPrefix(entry, "CAIRN_LIFECYCLE_CHILD=") {
			env = append(env, entry)
		}
	}
	env = append(env, "CAIRN_LIFECYCLE_CHILD=1")
	limited, cancel := context.WithTimeout(ctx, spec.timeout)
	defer cancel()
	raw, status, err := captureCommand(limited, executable, argv, input, env, work, observed)
	if err != nil {
		return nil, err
	}
	result, decodeErr := value.DecodeText(raw, "utf-8", 0)
	if status != 0 {
		observed.Set("outcome", value.Text("nonzero_exit"))
		code := ""
		if decodeErr == nil && result.Kind == value.Object {
			code = text(result.Get("status"))
		}
		if code == "BUDGET_REFUSED" {
			return nil, fail("memory expansion budget exhausted")
		}
		return nil, &hookError{class: "HookError", message: fmt.Sprintf("memory command exited %d; operation not confirmed", status), code: code, reason: "NONZERO_EXIT"}
	}
	if decodeErr != nil || result.Kind != value.Object {
		observed.Set("outcome", value.Text("invalid_json"))
		return nil, &hookError{class: "HookError", message: "memory command returned invalid JSON", reason: "INVALID_JSON"}
	}
	if value.Truth(result.Get("is_error")) {
		observed.Set("outcome", value.Text("reported_error"))
	}
	if models := result.Get("modelUsage"); models != nil && models.Kind == value.Object {
		var names []*value.Value
		for _, member := range models.Members {
			name := &value.Value{Kind: value.String, String: member.Key}
			if captureModelName(name) {
				names = append(names, name)
			}
		}
		if len(names) == 1 {
			observed.Set("reported_model", names[0])
		}
	}
	for _, pair := range [][2]string{{"duration_ms", "reported_duration_ms"}, {"duration_api_ms", "reported_api_duration_ms"}, {"total_cost_usd", "reported_cost_usd"}} {
		v := result.Get(pair[0])
		if _, ok := captureMetric(v, math.Inf(1)); ok {
			observed.Set(pair[1], v)
		}
	}
	if usage := result.Get("usage"); usage != nil && usage.Kind == value.Object {
		selected := value.Mapping()
		for _, key := range []string{"input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"} {
			v := usage.Get(key)
			if v != nil && v.Kind == value.Int && !strings.HasPrefix(v.Integer, "-") {
				selected.Set(key, v)
			}
		}
		observed.Set("usage", selected)
	}
	return result, nil
}

type capturePipeResult struct {
	name  string
	bytes []byte
	first time.Duration
	err   error
}

// Explicit file pipes let the deadline cover children that retain a pipe after
// the direct selector exits. Each reader retains at most the cap plus one byte.
func captureCommand(ctx context.Context, executable string, argv []string, input []byte, env []string, directory string, observed *value.Value) (output []byte, status int, resultErr error) {
	started := time.Now()
	observed.Set("outcome", value.Text("incomplete"))
	var files []*os.File
	pipe := func() (*os.File, *os.File, error) {
		r, w, err := os.Pipe()
		if err == nil {
			files = append(files, r, w)
		}
		return r, w, err
	}
	var command *exec.Cmd
	var waited chan error
	var results chan capturePipeResult
	livePipes := 0
	counts := map[string]int{}
	observePipe := func(result capturePipeResult) {
		counts[result.name] = len(result.bytes)
		if result.first > 0 {
			observed.Set("first_"+result.name+"_ms", floating(float64(result.first.Microseconds())/1000))
		}
	}
	defer func() {
		if command != nil && command.Process != nil && waited != nil {
			_ = command.Process.Kill() // Source cleans up only its direct child.
			<-waited
		}
		for _, f := range files {
			_ = f.Close()
		}
		for livePipes > 0 {
			observePipe(<-results)
			livePipes--
		}
		observed.Set("elapsed_ms", floating(float64(time.Since(started).Microseconds())/1000))
		var code *value.Value
		if command != nil && command.ProcessState != nil {
			n := command.ProcessState.ExitCode()
			if ws, ok := command.ProcessState.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
				n = -int(ws.Signal())
			}
			code = integer(n)
		}
		observed.Set("returncode", code)
		observed.Set("stdout_bytes", integer(counts["stdout"]))
		observed.Set("stderr_bytes", integer(counts["stderr"]))
	}()
	startFailure := func() ([]byte, int, error) {
		outcome := "spawn_failed"
		if command != nil && command.Process != nil {
			outcome = "communication_failed"
		}
		observed.Set("outcome", value.Text(outcome))
		return nil, 0, &hookError{class: "HookError", message: "could not start memory command", code: "API_UNAVAILABLE", reason: "START_FAILED"}
	}
	if ctx.Err() != nil {
		observed.Set("outcome", value.Text("timeout"))
		return nil, 0, &hookError{class: "HookError", message: "command timed out; memory operation not confirmed", code: "TIMEOUT"}
	}
	inR, inW, err := pipe()
	if err != nil {
		return startFailure()
	}
	outR, outW, err := pipe()
	if err != nil {
		return startFailure()
	}
	errR, errW, err := pipe()
	if err != nil {
		return startFailure()
	}
	command = exec.Command(executable, argv...)
	command.Dir, command.Env = directory, env
	command.Stdin, command.Stdout, command.Stderr = inR, outW, errW
	if err = command.Start(); err != nil {
		return startFailure()
	}
	observed.Set("spawn_ms", floating(float64(time.Since(started).Microseconds())/1000))
	_ = inR.Close()
	_ = outW.Close()
	_ = errW.Close()
	results = make(chan capturePipeResult, 3)
	livePipes = 3
	go func() {
		_, err := io.Copy(inW, bytes.NewReader(input))
		_ = inW.Close()
		if errors.Is(err, syscall.EPIPE) {
			err = nil
		}
		results <- capturePipeResult{name: "stdin", err: err}
	}()
	read := func(name string, stream *os.File) {
		var b bytes.Buffer
		var first time.Duration
		buffer := make([]byte, 65536)
		var readErr error
		for b.Len() <= captureOutputLimit {
			n, err := stream.Read(buffer[:min(len(buffer), captureOutputLimit+1-b.Len())])
			if n > 0 {
				if b.Len() == 0 {
					first = time.Since(started)
				}
				_, _ = b.Write(buffer[:n])
			}
			if err != nil {
				if err != io.EOF {
					readErr = err
				}
				break
			}
		}
		results <- capturePipeResult{name: name, bytes: b.Bytes(), first: first, err: readErr}
	}
	go read("stdout", outR)
	go read("stderr", errR)
	waited = make(chan error, 1)
	go func() { waited <- command.Wait() }()
	var waitErr error
	for exited := false; livePipes > 0 || !exited; {
		select {
		case <-ctx.Done():
			observed.Set("outcome", value.Text("timeout"))
			return nil, 0, &hookError{class: "HookError", message: "command timed out; memory operation not confirmed", code: "TIMEOUT"}
		case waitErr = <-waited:
			exited, waited = true, nil
		case result := <-results:
			livePipes--
			observePipe(result)
			if len(result.bytes) > captureOutputLimit {
				observed.Set("outcome", value.Text("communication_failed"))
				return nil, 0, &hookError{class: "HookError", message: "memory command output exceeded limit; operation not confirmed", reason: "OUTPUT_LIMIT"}
			}
			if result.err != nil {
				return startFailure()
			}
			if result.name == "stdout" {
				output = result.bytes
			}
		}
	}
	if ctx.Err() != nil {
		observed.Set("outcome", value.Text("timeout"))
		return nil, 0, &hookError{class: "HookError", message: "command timed out; memory operation not confirmed", code: "TIMEOUT"}
	}
	if waitErr != nil {
		var exit *exec.ExitError
		if !errors.As(waitErr, &exit) {
			return startFailure()
		}
	}
	if !utf8.Valid(output) {
		observed.Set("outcome", value.Text("communication_failed"))
		return nil, 0, &hookError{class: "HookError", message: "memory command returned invalid JSON", reason: "INVALID_JSON"}
	}
	observed.Set("outcome", value.Text("completed"))
	status = command.ProcessState.ExitCode()
	if ws, ok := command.ProcessState.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
		status = -int(ws.Signal())
	}
	return output, status, nil
}

func captureMetric(v *value.Value, limit float64) (float64, bool) {
	if v == nil || v.Kind != value.Int && v.Kind != value.Float {
		return 0, false
	}
	n := v.Number
	if v.Kind == value.Int {
		n, _ = strconv.ParseFloat(v.Integer, 64)
	}
	return n, !math.IsNaN(n) && !math.IsInf(n, 0) && n >= 0 && n <= limit
}
