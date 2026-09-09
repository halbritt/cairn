// Package runner observes the process boundary of a wrapped task. It cannot
// observe a closed harness's internal tool calls, compaction, or understanding.
package runner

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/artifacts"
	"github.com/halbritt/cairn/core"
)

type Request struct {
	AttemptID         string
	Compile           core.CompileRequest
	Retained          *core.RunPackageRequest
	Destination       core.Destination
	Command           []string
	Directory         string
	Carrier           string
	Prompt            string
	Timeout           time.Duration
	TaskClass         string
	BindingID         string
	CapabilityID      string
	Revision          string
	WorkspaceSHA256   string
	ArtifactDirectory string
}
type Result struct {
	AttemptID    string `json:"attempt_id,omitempty"`
	ReceiptID    string `json:"receipt_id"`
	Seal         string `json:"seal"`
	ProcessState string `json:"process_state"`
	ExitCode     *int   `json:"exit_code,omitempty"`
	OutcomeID    string `json:"outcome_id,omitempty"`
	Artifacts    string `json:"artifacts"`
}

// Store is the persistence boundary used by a process observer. Both a local
// store and an authenticated Unix client enforce the same core contracts.
type Store interface {
	artifacts.ContextRegistrar
	Compile(context.Context, core.CompileRequest, core.Destination) (core.Package, error)
	RunPackage(context.Context, core.RunPackageRequest, core.Destination) (core.Package, error)
	BindRun(context.Context, core.RunBindingRequest) (core.Observation, error)
	ClaimRun(context.Context, string) error
	RecordDelivery(context.Context, core.DeliveryRequest) (core.Observation, error)
	RecordOutcome(context.Context, core.OutcomeRequest) (core.Observation, error)
}

func Run(ctx context.Context, store Store, req Request, stdout, stderr io.Writer) (Result, error) {
	kinds, err := core.NormalizeKinds(req.Compile.Kinds)
	if err != nil {
		return Result{}, err
	}
	req.Compile.Kinds = kinds
	if req.Compile.Mode != "" {
		return Result{}, &core.Error{Code: "INVALID_REQUEST", Message: "H0 requires body compilation; index pull needs a host tool route"}
	}
	if len(req.Command) == 0 || (req.Carrier != "stdin" && req.Carrier != "argv") || req.Timeout <= 0 || req.Timeout > time.Hour || len(req.Prompt) > 131072 {
		return Result{}, &core.Error{Code: "INVALID_REQUEST", Message: "invalid command, carrier, timeout or prompt"}
	}
	taskClass, bindingID, capabilityID := req.TaskClass, req.BindingID, req.CapabilityID
	if taskClass == "" {
		taskClass = "unknown"
	}
	if bindingID == "" {
		bindingID = filepath.Base(req.Command[0]) + "/process-h0"
	}
	if capabilityID == "" {
		capabilityID = "unknown"
	}
	pins := core.ContextPins{Revision: req.Revision, WorkspaceSHA256: req.WorkspaceSHA256, TaskClass: taskClass, BindingID: bindingID, CapabilityID: capabilityID}
	if req.Compile.Context != nil && *req.Compile.Context != pins {
		return Result{}, &core.Error{Code: "INVALID_REQUEST", Message: "compile context must match declared run metadata"}
	}
	req.Compile.Context = &pins
	var pkg core.Package
	if req.Retained == nil {
		pkg, err = store.Compile(ctx, req.Compile, req.Destination)
	} else {
		pkg, err = store.RunPackage(ctx, *req.Retained, req.Destination)
	}
	if err != nil {
		return Result{}, err
	}
	result := Result{AttemptID: req.AttemptID, ReceiptID: pkg.ReceiptID, Seal: pkg.Seal, ProcessState: "unknown", Artifacts: filepath.Join(req.ArtifactDirectory, pkg.ReceiptID)}
	if req.Retained != nil {
		query := req.Compile.Query
		if pkg.Semantic.Schema != "cairn.semantic/1" {
			digest := sha256.Sum256([]byte(query))
			query = "sha256:" + hex.EncodeToString(digest[:])
		}
		if pkg.ReceiptID != req.Retained.ReceiptID || pkg.Seal != req.Retained.Seal || pkg.Semantic.Mode != "" ||
			pkg.Semantic.Destination != req.Destination || pkg.Semantic.Scope != req.Compile.Scope ||
			pkg.Semantic.Context == nil || *pkg.Semantic.Context != pins || pkg.Semantic.Query != query ||
			pkg.Semantic.Purpose != req.Compile.Purpose || pkg.Semantic.AvailableTokens != req.Compile.AvailableTokens ||
			!slices.Equal(pkg.Semantic.Kinds, req.Compile.Kinds) {
			return result, &core.Error{Code: "INVALID_REQUEST", Message: "retained package must match the receipt, seal, scope, query, context, purpose, kinds and memory budget of this run"}
		}
	}
	encodedCommand, err := json.Marshal(req.Command)
	if err != nil {
		return result, err
	}
	commandDigest := sha256.Sum256(encodedCommand)
	_, err = store.BindRun(ctx, core.RunBindingRequest{AttemptID: req.AttemptID, RequestID: req.Compile.RequestID, ReceiptID: pkg.ReceiptID, TaskClass: taskClass, BindingID: bindingID, CapabilityID: capabilityID, CommandSHA256: hex.EncodeToString(commandDigest[:]), Revision: req.Revision, WorkspaceSHA256: req.WorkspaceSHA256})
	if err != nil {
		return result, err
	}
	if err = store.ClaimRun(ctx, pkg.ReceiptID); err != nil {
		return result, err
	}
	result.Artifacts, err = artifacts.WriteContext(ctx, store, pkg, req.ArtifactDirectory)
	if err != nil {
		return prelaunchFailure(store, result, err)
	}
	rendered, err := pkg.Render()
	if err != nil {
		return prelaunchFailure(store, result, err)
	}
	input := rendered + "\nTASK\n" + req.Prompt
	digest := sha256.Sum256([]byte(input))
	delivery := core.DeliveryRequest{RequestID: uuid.NewString(), ReceiptID: pkg.ReceiptID, Adapter: filepath.Base(req.Command[0]) + "/process-h0", Carrier: req.Carrier, Assurance: "unknown", RenderedSHA256: hex.EncodeToString(digest[:]), BlindSpots: "Native memory influence is not isolated. No internal tools, compaction, model contact, instruction compliance or task acceptance observed."}
	// Persist launch intent before any process can execute. A crash afterward is
	// visible as an unfinished run, not a manufactured successful outcome.
	if _, err = store.RecordDelivery(ctx, delivery); err != nil {
		return prelaunchFailure(store, result, err)
	}
	runCtx, cancel := context.WithTimeout(ctx, req.Timeout)
	defer cancel()
	command := exec.CommandContext(runCtx, req.Command[0], req.Command[1:]...)
	command.Dir = req.Directory
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	command.Cancel = func() error { return syscall.Kill(-command.Process.Pid, syscall.SIGKILL) }
	command.WaitDelay = 2 * time.Second
	command.Env = childEnvironment()
	if req.Carrier == "stdin" {
		command.Stdin = strings.NewReader(input)
	} else {
		command.Args = append(command.Args, input)
	}
	outHash, errHash := sha256.New(), sha256.New()
	command.Stdout = io.MultiWriter(stdout, outHash)
	command.Stderr = io.MultiWriter(stderr, errHash)
	started := time.Now()
	startErr := command.Start()
	var waitErr, deliveryErr error
	if startErr == nil {
		delivery.RequestID = uuid.NewString()
		delivery.Assurance = "available"
		if _, err = store.RecordDelivery(ctx, delivery); err != nil {
			deliveryErr = fmt.Errorf("delivery persistence failed after launch: %w", err)
			if killErr := syscall.Kill(-command.Process.Pid, syscall.SIGKILL); killErr != nil && !errors.Is(killErr, syscall.ESRCH) {
				deliveryErr = errors.Join(deliveryErr, killErr)
			}
		}
		waitErr = command.Wait()
		// The run owns its process group, including children that survived the main
		// process. ESRCH means it has already disappeared.
		if killErr := syscall.Kill(-command.Process.Pid, syscall.SIGKILL); killErr != nil && !errors.Is(killErr, syscall.ESRCH) {
			return result, killErr
		}
	}
	state := "exited"
	var exitCode *int
	if startErr != nil {
		state = "launch_failed"
	} else if errors.Is(runCtx.Err(), context.DeadlineExceeded) {
		state = "timeout"
	} else if runCtx.Err() != nil {
		state = "cancelled"
	} else {
		code := command.ProcessState.ExitCode()
		exitCode = &code
	}
	result.ProcessState = state
	result.ExitCode = exitCode
	outcome := core.OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: pkg.ReceiptID, ExitCode: exitCode, DurationMS: time.Since(started).Milliseconds(), ProcessState: state, StdoutSHA256: hex.EncodeToString(outHash.Sum(nil)), StderrSHA256: hex.EncodeToString(errHash.Sum(nil))}
	// A cancelled task still needs a durable outcome. If the DB is down, preserve
	// the exact retry request locally and report the persistence failure.
	encoded, err := json.MarshalIndent(outcome, "", "  ")
	if err != nil {
		return result, err
	}
	pending := filepath.Join(result.Artifacts, "outcome.pending.json")
	if err = os.WriteFile(pending, encoded, 0600); err != nil {
		return result, err
	}
	finishCtx, finishCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer finishCancel()
	observation, err := store.RecordOutcome(finishCtx, outcome)
	if err != nil {
		return result, fmt.Errorf("outcome not committed; retry request retained at %s: %w", pending, err)
	}
	result.OutcomeID = observation.ID
	if err = os.Rename(pending, filepath.Join(result.Artifacts, "outcome.json")); err != nil {
		return result, err
	}
	if startErr != nil {
		return result, startErr
	}
	if deliveryErr != nil {
		return result, deliveryErr
	}
	if state == "exited" {
		var exitErr *exec.ExitError
		if waitErr != nil && !errors.As(waitErr, &exitErr) {
			return result, waitErr
		}
	}
	return result, nil
}

// Preparation failed before Start was called. Retain that known non-execution
// even if the caller cancelled. The artifact path may have been refused, so this
// path must not attempt an outcome-file write there. A DB failure remains explicit.
func prelaunchFailure(store Store, result Result, cause error) (Result, error) {
	result.ProcessState = "launch_failed"
	empty := sha256.Sum256(nil)
	digest := hex.EncodeToString(empty[:])
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	observation, err := store.RecordOutcome(ctx, core.OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: result.ReceiptID, ProcessState: result.ProcessState, StdoutSHA256: digest, StderrSHA256: digest})
	if err != nil {
		return result, errors.Join(cause, fmt.Errorf("pre-launch outcome not committed: %w", err))
	}
	result.OutcomeID = observation.ID
	return result, cause
}

func childEnvironment() []string {
	env := []string{}
	for _, entry := range os.Environ() {
		key, _, _ := strings.Cut(entry, "=")
		if strings.HasPrefix(key, "CAIRN_") || key == "PGPASSWORD" || key == "PGPASSFILE" || key == "PGSERVICEFILE" {
			continue
		}
		env = append(env, entry)
	}
	return env
}
