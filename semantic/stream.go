package semantic

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os/exec"
	"strconv"
	"syscall"
	"time"

	"github.com/halbritt/cairn/core"
)

const streamCacheLimit = 600 * 1024
const streamFrameLimit = streamCacheLimit + 65536

type streamReply struct {
	result core.SemanticRankResult
	err    error
}
type streamJob struct {
	ctx     context.Context
	request core.SemanticRankRequest
	reply   chan streamReply
}
type streamWorkerProcess struct {
	command   *exec.Cmd
	input     io.WriteCloser
	output    io.ReadCloser
	reader    *bufio.Reader
	model     string
	algorithm string
	restore   json.RawMessage
	cache     json.RawMessage
}

// StreamCommand owns one lazy JSON-line worker. Close cancels active work,
// reaps the child and waits for the owner loop; it is safe to call repeatedly.
// Requests never queue behind another caller. Idle workers are released after
// 30 seconds. A bounded worker-provided cache survives idle release in this
// owner's memory only; failure and Close discard it. Existing one-shot executables
// must use Command instead.
func StreamCommand(owner context.Context, path string) (core.SemanticRanker, func(), error) {
	return StreamCommandWithIdleTimeout(owner, path, 30*time.Second)
}

// StreamCommandWithIdleTimeout sets how long the host retains an unused worker.
// The request deadline and cache eligibility rules are unchanged.
func StreamCommandWithIdleTimeout(owner context.Context, path string, idle time.Duration) (core.SemanticRanker, func(), error) {
	if idle <= 0 {
		return nil, nil, fmt.Errorf("semantic worker idle timeout must be positive")
	}
	if err := validateCommand(path); err != nil {
		return nil, nil, err
	}
	lifetime, cancel := context.WithCancel(owner)
	jobs := make(chan streamJob)
	done := make(chan struct{})
	busy := make(chan struct{}, 1)
	go serveStream(lifetime, path, idle, jobs, done)
	closeWorker := func() { cancel(); <-done }
	rank := func(ctx context.Context, request core.SemanticRankRequest) (core.SemanticRankResult, error) {
		select {
		case busy <- struct{}{}:
			defer func() { <-busy }()
		default:
			return core.SemanticRankResult{}, fmt.Errorf("semantic worker busy")
		}
		ctx, stop := context.WithTimeout(ctx, workerTimeout)
		defer stop()
		job := streamJob{ctx: ctx, request: request, reply: make(chan streamReply, 1)}
		select {
		case <-ctx.Done():
			return core.SemanticRankResult{}, ctx.Err()
		case <-lifetime.Done():
			return core.SemanticRankResult{}, lifetime.Err()
		case jobs <- job:
		}
		// The owner responds only after failed/cancelled children are reaped. Holding
		// the busy slot until then prevents a second caller from queueing for cleanup.
		reply := <-job.reply
		return reply.result, reply.err
	}
	return rank, closeWorker, nil
}

func serveStream(ctx context.Context, path string, idle time.Duration, jobs <-chan streamJob, done chan<- struct{}) {
	defer close(done)
	var worker *streamWorkerProcess
	// Opaque worker state is never part of core results or receipts.
	var cache json.RawMessage
	defer func() {
		if worker != nil {
			worker.stop()
		}
	}()
	timer := time.NewTimer(idle)
	defer timer.Stop()
	sequence := uint64(0)
	for {
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
			if worker != nil {
				cache = worker.cache
				worker.stop()
				worker = nil
			}
		case job := <-jobs:
			timer.Stop()
			sequence++
			result, err := func() (core.SemanticRankResult, error) {
				if err := job.ctx.Err(); err != nil {
					return core.SemanticRankResult{}, err
				}
				if err := ctx.Err(); err != nil {
					return core.SemanticRankResult{}, err
				}
				if worker == nil {
					var err error
					worker, err = startStream(path)
					if err != nil {
						return core.SemanticRankResult{}, err
					}
					worker.restore, cache = cache, nil
				}
				exchanged := make(chan streamReply, 1)
				go func(w *streamWorkerProcess, id string) {
					result, err := w.exchange(id, job.request)
					exchanged <- streamReply{result, err}
				}(worker, strconv.FormatUint(sequence, 10))
				select {
				case reply := <-exchanged:
					return reply.result, reply.err
				case <-job.ctx.Done():
					worker.stop()
					worker = nil
					<-exchanged
					return core.SemanticRankResult{}, job.ctx.Err()
				case <-ctx.Done():
					worker.stop()
					worker = nil
					<-exchanged
					return core.SemanticRankResult{}, ctx.Err()
				}
			}()
			if err != nil {
				cache = nil
				if worker != nil {
					worker.stop()
					worker = nil
				}
			}
			job.reply <- streamReply{result, err}
			if worker != nil {
				timer.Reset(idle)
			}
		}
	}
}

func startStream(path string) (*streamWorkerProcess, error) {
	command := exec.Command(path)
	// Older workers ignore this capability. New workers omit cache responses
	// when launched by an older host, which does not advertise it.
	command.Env = append(workerEnvironment(), "CAIRN_SEMANTIC_STREAM_CACHE=1")
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	command.WaitDelay = time.Second
	command.Stderr = io.Discard
	input, err := command.StdinPipe()
	if err != nil {
		return nil, err
	}
	output, err := command.StdoutPipe()
	if err != nil {
		input.Close()
		return nil, err
	}
	if err = command.Start(); err != nil {
		input.Close()
		output.Close()
		return nil, err
	}
	return &streamWorkerProcess{command: command, input: input, output: output, reader: bufio.NewReaderSize(output, streamFrameLimit+1)}, nil
}

func (w *streamWorkerProcess) stop() {
	// The direct child is unreaped until Wait, so its PID cannot be reused here.
	// Terminate the group even if the leader exited while a descendant held a pipe.
	_ = syscall.Kill(-w.command.Process.Pid, syscall.SIGKILL)
	_ = w.input.Close()
	_ = w.output.Close()
	// Stop deliberately discards the exit status of an already rejected or idle
	// child. Request failures are returned by exchange or the owning context.
	_ = w.command.Wait()
}

func (w *streamWorkerProcess) exchange(id string, request core.SemanticRankRequest) (core.SemanticRankResult, error) {
	envelope := struct {
		ID      string                   `json:"id"`
		Request core.SemanticRankRequest `json:"request"`
		Cache   json.RawMessage          `json:"cache,omitempty"`
	}{id, request, w.restore}
	input, err := json.Marshal(envelope)
	if err != nil {
		return core.SemanticRankResult{}, err
	}
	if len(input) > 8*1024*1024 {
		return core.SemanticRankResult{}, fmt.Errorf("semantic worker request exceeds limit")
	}
	if _, err = w.input.Write(append(input, '\n')); err != nil {
		return core.SemanticRankResult{}, fmt.Errorf("semantic worker write: %w", err)
	}
	line, err := w.reader.ReadSlice('\n')
	if err != nil {
		return core.SemanticRankResult{}, fmt.Errorf("semantic worker response framing: %w", err)
	}
	if len(line) > streamFrameLimit {
		return core.SemanticRankResult{}, fmt.Errorf("semantic worker output exceeds limit")
	}
	var response struct {
		ID     string                   `json:"id"`
		Result *core.SemanticRankResult `json:"result"`
		Cache  json.RawMessage          `json:"cache,omitempty"`
	}
	if err = decodeOne(bytes.NewReader(line), &response); err != nil {
		return core.SemanticRankResult{}, err
	}
	if response.ID != id {
		return core.SemanticRankResult{}, fmt.Errorf("semantic worker response ID mismatch")
	}
	if response.Result == nil {
		return core.SemanticRankResult{}, fmt.Errorf("semantic worker omitted result")
	}
	if bytes.Equal(response.Cache, []byte("null")) {
		response.Cache = nil
	}
	if len(response.Cache) > streamCacheLimit {
		return core.SemanticRankResult{}, fmt.Errorf("semantic worker cache exceeds limit")
	}
	// An optional cache must not enlarge the scoring response allowance.
	ordinary, err := json.Marshal(struct {
		ID     string                   `json:"id"`
		Result *core.SemanticRankResult `json:"result"`
	}{response.ID, response.Result})
	if err != nil || len(ordinary)+1 > 65536 || (len(response.Cache) == 0 && len(line) > 65536) {
		return core.SemanticRankResult{}, fmt.Errorf("semantic worker output exceeds limit")
	}
	result := *response.Result
	if w.model != "" && (result.ModelSHA256 != w.model || result.Algorithm != w.algorithm) {
		return core.SemanticRankResult{}, fmt.Errorf("semantic worker changed model identity")
	}
	w.model, w.algorithm = result.ModelSHA256, result.Algorithm
	w.restore, w.cache = nil, response.Cache
	return result, nil
}
