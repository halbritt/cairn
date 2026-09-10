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
}

// StreamCommand owns one lazy JSON-line worker. Close cancels active work,
// reaps the child and waits for the owner loop; it is safe to call repeatedly.
// Requests never queue behind another caller. Idle workers are released after
// 30 seconds. Existing one-shot executables must use Command instead.
func StreamCommand(owner context.Context, path string) (core.SemanticRanker, func(), error) {
	return streamCommand(owner, path, 30*time.Second)
}

func streamCommand(owner context.Context, path string, idle time.Duration) (core.SemanticRanker, func(), error) {
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
			if err != nil && worker != nil {
				worker.stop()
				worker = nil
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
	command.Env = workerEnvironment()
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
	return &streamWorkerProcess{command: command, input: input, output: output, reader: bufio.NewReaderSize(output, 65537)}, nil
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
	}{id, request}
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
	if len(line) > 65536 {
		return core.SemanticRankResult{}, fmt.Errorf("semantic worker output exceeds limit")
	}
	var response struct {
		ID     string                   `json:"id"`
		Result *core.SemanticRankResult `json:"result"`
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
	result := *response.Result
	if w.model != "" && (result.ModelSHA256 != w.model || result.Algorithm != w.algorithm) {
		return core.SemanticRankResult{}, fmt.Errorf("semantic worker changed model identity")
	}
	w.model, w.algorithm = result.ModelSHA256, result.Algorithm
	return result, nil
}
