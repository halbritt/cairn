package native

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/procpeer"
	"golang.org/x/sys/unix"
)

type clientMessage struct {
	object map[string]json.RawMessage
	err    error
}

type claudeClient struct {
	command     *exec.Cmd
	input       io.WriteCloser
	events      chan clientMessage
	ref         procpeer.ProcessRef
	id          string
	ctx         context.Context
	cancel      context.CancelFunc
	inputClosed bool
	stderr      limitedDiagnostic
}

type limitedDiagnostic struct{ raw []byte }

func (d *limitedDiagnostic) Write(raw []byte) (int, error) {
	const limit = 65536
	count := len(raw)
	if room := limit - len(d.raw); room > 0 {
		if len(raw) > room {
			raw = raw[:room]
		}
		d.raw = append(d.raw, raw...)
	}
	return count, nil
}

type ClaudeReadback struct {
	SessionID string                     `json:"session_id"`
	Process   procpeer.ProcessRef        `json:"process"`
	Settings  map[string]json.RawMessage `json:"settings"`
	Hooks     map[string]json.RawMessage `json:"hooks"`
}

func strictJSON(raw []byte, result any) error {
	if err := jsontext.CheckUnicode(raw); err != nil {
		return err
	}
	if _, err := value.DecodeBytesWithObjectPolicy(raw, 0, value.DuplicateReject); err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	return decoder.Decode(result)
}

func (o *Owner) clientCommand(ctx context.Context, args ...string) *exec.Cmd {
	command := exec.CommandContext(ctx, "/proc/self/fd/3", args...)
	command.Args[0] = o.config.Client.Path
	command.ExtraFiles = []*os.File{o.image}
	command.Dir = o.config.Work
	// Keep the host HOME, PATH and authentication. Only application-owned state
	// and explicitly model-free native behavior are selected here.
	command.Env = append(os.Environ(), "CLAUDE_CONFIG_DIR="+o.config.Account,
		"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1", "ENABLE_TOOL_SEARCH=false",
		"CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS=150000")
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true, Pdeathsig: syscall.SIGTERM}
	return command
}

// Initialize starts only a new owned Claude stdio process. It sends native
// control messages, never a user/model prompt. Existing processes cannot attach.
func (o *Owner) Initialize(ctx context.Context, expectedGeneration uint64) (_ ClaudeReadback, err error) {
	if o == nil {
		return ClaudeReadback{}, errors.New("qualified native controller unavailable")
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	defer func() {
		if err != nil && o.client != nil {
			o.uncertain, o.phase = true, "unknown"
		}
	}()
	if o.origin != o || o.closed || !o.controller || !procpeer.Live(o.creator) || o.creator.PID != os.Getpid() {
		return ClaudeReadback{}, errors.New("qualified native controller unavailable")
	}
	if expectedGeneration == 0 || expectedGeneration != o.generation {
		return ClaudeReadback{}, errors.New("native owner generation changed or was not selected")
	}
	if o.client != nil || o.pendingMutation || o.phase != "never-launched" && o.phase != "joined" {
		return ClaudeReadback{}, errors.New("native admission is busy or unknown")
	}
	if len(o.nativeIDs) != 0 {
		if observed := o.quiescenceLocked(ctx); observed.Verdict != Quiescent {
			return ClaudeReadback{}, fmt.Errorf("native admission requires complete G2 (%s): %s", observed.Verdict, observed.Reason)
		}
	}
	if err := errors.Join(o.settings.Recheck(ctx), checkImage(ctx, o.image, o.imageInfo, o.config.Client)); err != nil {
		return ClaudeReadback{}, err
	}
	if o.material != nil {
		if err := o.material.recheck(ctx); err != nil {
			return ClaudeReadback{}, err
		}
	}
	version, err := o.clientCommand(ctx, "--version").Output()
	if err != nil || strings.TrimSpace(string(version)) != o.config.ClientVersion+" (Claude Code)" {
		return ClaudeReadback{}, errors.Join(err, errors.New("actual native version differs from selected version"))
	}
	if children, err := controllerChildren(); err != nil || len(children) != 0 {
		return ClaudeReadback{}, errors.Join(err, errors.New("native controller has unjoined children"))
	}
	o.phase, o.generation = "admitting", o.generation+1
	defer func() {
		if err != nil {
			o.phase, o.uncertain = "unknown", true
		}
	}()
	id := uuid.NewString()
	clientCtx, cancelClient := context.WithCancel(o.lifetime)
	command := o.clientCommand(clientCtx, "--print", "--verbose", "--input-format", "stream-json", "--output-format", "stream-json",
		"--setting-sources", "", "--settings", filepath.Join(o.root, "settings.json"),
		"--strict-mcp-config", "--mcp-config", `{"mcpServers":{}}`, "--tools", "",
		"--permission-mode", "dontAsk", "--session-id", id)
	input, err := command.StdinPipe()
	if err != nil {
		cancelClient()
		return ClaudeReadback{}, err
	}
	output, err := command.StdoutPipe()
	if err != nil {
		cancelClient()
		return ClaudeReadback{}, errors.Join(err, input.Close())
	}
	client := &claudeClient{command: command, input: input, events: make(chan clientMessage, 16), id: id, ctx: clientCtx, cancel: cancelClient}
	command.Stderr = &client.stderr
	command.WaitDelay = 5 * time.Second
	if err := command.Start(); err != nil {
		cancelClient()
		return ClaudeReadback{}, errors.Join(err, input.Close(), output.Close())
	}
	o.client = client
	o.nativeIDs = append(o.nativeIDs, id)
	go client.read(output)
	ref, err := procpeer.ProcessRefOf(command.Process.Pid)
	if err != nil {
		return ClaudeReadback{}, err
	}
	client.ref = ref
	actual, err := os.Stat(fmt.Sprintf("/proc/%d/exe", ref.PID))
	if err != nil || !sameImageInfo(actual, o.imageInfo) {
		return ClaudeReadback{}, errors.Join(err, errors.New("native child is not the selected image descriptor"))
	}
	if _, err := client.control(ctx, "initialize"); err != nil {
		return ClaudeReadback{}, err
	}
	settings, err := client.control(ctx, "get_settings")
	if err != nil {
		return ClaudeReadback{}, err
	}
	hooks, err := client.control(ctx, "get_hooks_listing")
	if err != nil {
		return ClaudeReadback{}, err
	}
	if err := verifyNativeReadback(o.settings.Bytes(), settings, hooks); err != nil {
		return ClaudeReadback{}, err
	}
	if err := errors.Join(o.settings.Recheck(ctx), checkImage(ctx, o.image, o.imageInfo, o.config.Client)); err != nil {
		return ClaudeReadback{}, err
	}
	o.phase = "initialized"
	return ClaudeReadback{id, ref, settings, hooks}, nil
}

func (c *claudeClient) read(output io.ReadCloser) {
	defer close(c.events)
	defer output.Close()
	scanner := bufio.NewScanner(output)
	scanner.Buffer(make([]byte, 65536), 4*1024*1024)
	for scanner.Scan() {
		var event map[string]json.RawMessage
		err := strictJSON(scanner.Bytes(), &event)
		if err == nil && event == nil {
			err = errors.New("native event is not an object")
		}
		select {
		case c.events <- clientMessage{event, err}:
		case <-c.ctx.Done():
			return
		}
	}
	if err := scanner.Err(); err != nil {
		select {
		case c.events <- clientMessage{err: err}:
		case <-c.ctx.Done():
		}
	}
}

func (c *claudeClient) control(ctx context.Context, subtype string) (map[string]json.RawMessage, error) {
	id := uuid.NewString()
	request, err := json.Marshal(map[string]any{"type": "control_request", "request_id": id,
		"request": map[string]any{"subtype": subtype}})
	if err != nil {
		return nil, err
	}
	if _, err := c.input.Write(append(request, '\n')); err != nil {
		return nil, err
	}
	for {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case message, ok := <-c.events:
			if !ok {
				return nil, errors.New("native stdout closed before the selected control response")
			}
			if message.err != nil {
				return nil, message.err
			}
			var kind string
			if err := strictJSON(message.object["type"], &kind); err != nil {
				return nil, err
			}
			if kind == "system" {
				if err := checkNativeSystem(message.object); err != nil {
					return nil, err
				}
				continue
			}
			if kind != "control_response" {
				return nil, fmt.Errorf("unsupported unsolicited native event %q", kind)
			}
			var response struct {
				RequestID string                     `json:"request_id"`
				Subtype   string                     `json:"subtype"`
				Response  map[string]json.RawMessage `json:"response"`
			}
			if err := strictJSON(message.object["response"], &response); err != nil {
				return nil, err
			}
			if response.RequestID != id || response.Subtype != "success" || response.Response == nil {
				return nil, errors.New("native control refused or returned mismatched identity")
			}
			return response.Response, nil
		}
	}
}

// CloseNative closes and joins only the child this owner launched. A missing
// process is not a join; remaining descendants leave the owner UNKNOWN.
func (o *Owner) CloseNative(ctx context.Context) (err error) {
	if o == nil {
		return errors.New("qualified native controller unavailable")
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.closeNativeLocked(ctx)
}

func (o *Owner) closeNativeAt(ctx context.Context, generation uint64) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	if generation == 0 || generation != o.generation {
		return errors.New("native owner generation changed or was not selected")
	}
	return o.closeNativeLocked(ctx)
}

func (o *Owner) closeNativeLocked(ctx context.Context) (err error) {
	if o.origin != o || !o.controller {
		return errors.New("qualified native controller unavailable")
	}
	c := o.client
	if c == nil {
		return nil
	}
	o.phase = "joining"
	defer func() {
		if err != nil {
			o.phase, o.uncertain = "unknown", true
		}
	}()
	if !c.inputClosed {
		c.inputClosed = true
		err = errors.Join(err, c.input.Close())
	}
	for {
		select {
		case <-ctx.Done():
			err = errors.Join(err, ctx.Err())
			c.cancel()
			goto joined
		case message, ok := <-c.events:
			if !ok {
				goto joined
			}
			if message.err != nil {
				err = errors.Join(err, message.err)
			} else {
				err = errors.Join(err, checkNativeSystem(message.object))
			}
		}
	}
joined:
	err = errors.Join(err, c.command.Wait())
	c.cancel()
	o.client = nil
	if err != nil {
		return err
	}
	children, err := controllerChildren()
	if err != nil || len(children) != 0 {
		return errors.Join(err, errors.New("native descendants remain; join is incomplete"))
	}
	o.joined = append(o.joined, c.ref)
	o.phase, o.generation = "joined", o.generation+1
	if c.ref.PID < 2 || c.ref.Start == 0 || c.ref.Boot == "" || o.uncertain {
		return errors.New("native join does not repair an uncertain origin or protocol")
	}
	return nil
}

func controllerChildren() ([]procpeer.ProcessRef, error) {
	tasks, err := os.ReadDir("/proc/self/task")
	if err != nil {
		return nil, err
	}
	seen := map[int]bool{}
	var children []procpeer.ProcessRef
	for _, task := range tasks {
		raw, err := os.ReadFile(filepath.Join("/proc/self/task", task.Name(), "children"))
		if err != nil {
			return nil, err
		}
		for _, word := range strings.Fields(string(raw)) {
			pid, err := strconv.Atoi(word)
			if err != nil || pid < 2 {
				return nil, errors.Join(err, errors.New("native descendant identity is unknown"))
			}
			if seen[pid] {
				continue
			}
			ref, err := procpeer.ProcessRefOf(pid)
			if err != nil {
				return nil, err
			}
			seen[pid] = true
			children = append(children, ref)
		}
	}
	return children, nil
}

// enableController is used only by the dedicated public controller process.
// Subreaping keeps detached descendants attributable after the native PID exits.
func (o *Owner) enableController() error {
	if err := unix.Prctl(unix.PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0); err != nil {
		return err
	}
	if children, err := controllerChildren(); err != nil || len(children) != 0 {
		return errors.Join(err, errors.New("dedicated native controller already has children"))
	}
	o.controller = true
	return nil
}
