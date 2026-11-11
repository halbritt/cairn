package coordination

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const (
	SiteSessionRead  = "coordination.session.read"
	SiteSessionWrite = "coordination.session.write"
	SiteCallRequest  = "coordination.call.request"
	SiteCallResponse = "coordination.call.response"
)

// FileHostOptions supplies the Source routes, API transport and durable I/O.
// SourceReaderProfile owns both Decode and Write in the Foundation interface.
// Its session writer route covers json.dump followed by the terminating LF.
type FileHostOptions struct {
	Services          *ServiceBindings
	Profile           *value.SourceReaderProfile
	Compatibility     *CompatibilityContext
	Journal           *SessionJournalAdapter
	JournalOptions    journal.Options
	Runner            journal.Runner
	Call              func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error)
	Now               func() time.Time
	Log               func(string) error
	IO                journal.IO
	OwnerProcess      func(*value.Value, *value.Value) (*value.Value, error)
	LoadInboxRecall   func(context.Context, *value.Value) error
	InboxRecallOutput func(context.Context, InboxRecallRequest) (string, error)
}

type sessionLease struct{ lease *LockLease }

func (l sessionLease) Close() error { return l.lease.Release() }

type stateWriter struct {
	file  *os.File
	write func(*os.File, []byte) (int, error)
}

func (w stateWriter) Write(raw []byte) (int, error) { return w.write(w.file, raw) }

func sourcePathText(profile *value.SourceReaderProfile, v *value.Value) (string, error) {
	if v == nil || v.Kind != value.String {
		return "", &journal.TypeError{Field: "path"}
	}
	if profile == nil || profile.Routes == nil || profile.Routes.Filesystem() == nil {
		return "", &value.ProfileUnavailable{}
	}
	return profile.Routes.Filesystem().Encode(v.String)
}

// NewFileHost performs no I/O and selects no production profile or credentials.
// Every transition uses the existing NativeSession and journal implementations.
func NewFileHost(options FileHostOptions) *Host {
	clock := options.Now
	if clock == nil {
		clock = time.Now
	}
	log := options.Log
	if log == nil {
		log = func(message string) error { _, err := io.WriteString(os.Stderr, message+"\n"); return err }
	}
	adapter := options.Journal
	if adapter == nil {
		adapter = &SessionJournalAdapter{Options: options.JournalOptions, Now: clock, Log: log}
	}
	if adapter.Now == nil {
		adapter.Now = clock
	}
	if adapter.Log == nil {
		adapter.Log = log
	}
	if adapter.PathText == nil {
		adapter.PathText = func(v *value.Value) (string, error) { return sourcePathText(options.Profile, v) }
	}
	h := &Host{
		Journal:           adapter,
		Log:               log,
		Profile:           options.Profile,
		Services:          options.Services,
		Compatibility:     options.Compatibility,
		OwnerProcess:      options.OwnerProcess,
		LoadInboxRecall:   options.LoadInboxRecall,
		InboxRecallOutput: options.InboxRecallOutput,
	}
	h.Call = options.Call
	if h.Call == nil {
		h.Call = sourceCallWithCompatibility(options.Profile, options.Runner, options.Compatibility)
	}
	h.Locate = func(config *value.Value, nativeID string) (string, error) {
		binding, err := nativeRequired(config, "binding")
		if err != nil {
			return "", err
		}
		if binding.Kind != value.String {
			return "", &journal.TypeError{Field: "binding"}
		}
		for _, r := range binding.String {
			if !utf8.ValidRune(r) {
				return "", &value.Error{Class: "UnicodeEncodeError", Message: "session binding is not UTF-8 encodable"}
			}
		}
		if !utf8.ValidString(nativeID) {
			return "", &value.Error{Class: "UnicodeEncodeError", Message: "native session ID is not UTF-8 encodable"}
		}
		dirValue, err := nativeRequired(config, "state_dir")
		if err != nil {
			return "", err
		}
		dir, err := sourcePathText(options.Profile, dirValue)
		if err != nil {
			return "", err
		}
		key := sha256.Sum256([]byte(string(binding.String) + "\x00" + nativeID))
		return filepath.Join(dir, hex.EncodeToString(key[:])+".json"), nil
	}
	h.Lock = func(path string) (io.Closer, error) {
		lease, err := AcquireSessionLock(path)
		if err != nil {
			return nil, err
		}
		return sessionLease{lease}, nil
	}
	h.Exists = func(path string) (bool, error) {
		if options.Profile == nil || options.Profile.Routes == nil || options.Profile.Routes.Filesystem() == nil {
			return false, &value.ProfileUnavailable{}
		}
		fs := options.Profile.Routes.Filesystem()
		points, err := fs.Decode(path)
		if err != nil {
			return false, err
		}
		return fs.Exists(points)
	}
	h.Load = func(path string) (*value.Value, error) {
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		return options.Profile.Decode(SiteSessionRead, raw)
	}
	h.ReadConfig = func(path string) (*value.Value, error) {
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		return options.Profile.Decode(SiteConfigRead, raw)
	}
	h.ReadContext = func(path string) (*value.Value, error) {
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		return options.Profile.Decode(SiteInboxContextRead, raw)
	}
	h.Persist = sourceStateWriter(options, SiteSessionWrite)
	h.WriteContext = sourceStateWriter(options, SiteInboxContextWrite)
	return h
}

func sourceStateWriter(options FileHostOptions, site string) func(string, *value.Value) error {
	return func(path string, record *value.Value) (err error) {
		if err = os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			return err
		}
		file, err := os.CreateTemp(filepath.Dir(path), "tmp*")
		if err != nil {
			return err
		}
		pending := file.Name()
		closed := false
		defer func() {
			if !closed {
				if e := file.Close(); e != nil {
					err = e
				}
			}
			if e := os.Remove(pending); e != nil && !errors.Is(e, os.ErrNotExist) {
				err = e
			}
		}()
		write, sync, close, rename := options.IO.Write, options.IO.Sync, options.IO.Close, options.IO.Rename
		if write == nil {
			write = (*os.File).Write
		}
		if sync == nil {
			sync = (*os.File).Sync
		}
		if close == nil {
			close = (*os.File).Close
		}
		if rename == nil {
			rename = os.Rename
		}
		if err = options.Profile.Write(site, stateWriter{file, write}, record); err != nil {
			return err
		}
		if err = sync(file); err != nil {
			return err
		}
		closed = true
		if err = close(file); err != nil {
			return err
		}
		if err = rename(pending, path); err != nil {
			return err
		}
		dir, err := os.OpenFile(filepath.Dir(path), os.O_RDONLY|syscall.O_DIRECTORY, 0)
		if err != nil {
			return err
		}
		err = sync(dir)
		if e := close(dir); e != nil {
			return e
		}
		return err
	}
}

func sourceCall(profile *value.SourceReaderProfile, runner journal.Runner) func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error) {
	return sourceCallWithCompatibility(profile, runner, nil)
}

func sourceCallWithCompatibility(profile *value.SourceReaderProfile, runner journal.Runner, compat *CompatibilityContext) func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error) {
	if runner == nil {
		runner = journal.CommandRunner{}
	}
	return func(ctx context.Context, config *value.Value, operation string, request *value.Value, options NativeCallOptions) (*value.Value, error) {
		timeout := options.Timeout
		if timeout == 0 {
			timeout = 4 * time.Second
		}
		if compat != nil {
			var err error
			if timeout, err = compat.Remaining(timeout); err != nil {
				return nil, err
			}
		}
		args := []string{}
		for _, key := range []string{"cairn", "socket", "token_file"} {
			if !value.Truth(config.Get(key)) {
				return nil, nativeRefusal("INVALID_CONFIG", "missing "+key+" in config")
			}
			text, err := sourcePathText(profile, config.Get(key))
			if err != nil {
				return nil, err
			}
			args = append(args, text)
		}
		command := []string{args[0], "agent", "--socket", args[1], "--token-file", args[2]}
		if value.Truth(options.Session) {
			for _, pair := range [][2]string{{"--agent-id", "agent_id"}, {"--execution-id", "execution_id"}} {
				v, err := nativeRequired(options.Session, pair[1])
				if err != nil {
					return nil, err
				}
				text, err := sourcePathText(profile, v)
				if err != nil {
					return nil, err
				}
				command = append(command, pair[0], text)
			}
		}
		command = append(command, operation)
		var input bytes.Buffer
		if err := profile.Write(SiteCallRequest, &input, request); err != nil {
			return nil, err
		}
		callCtx, cancel := context.WithTimeout(ctx, timeout)
		defer cancel()
		result, err := runner.Run(callCtx, command, input.String())
		if err != nil {
			if callValueError(err) || !callRunnerUnavailable(err) {
				return nil, err
			}
			return nil, &CoordinationError{Code: "API_UNAVAILABLE", Message: "Cairn lifecycle command is unavailable", Cause: err}
		}
		response, err := profile.Decode(SiteCallResponse, []byte(result.Stdout))
		if err != nil {
			if !callValueError(err) {
				return nil, err
			}
			return nil, &CoordinationError{Code: "API_UNAVAILABLE", Message: "Cairn did not return a JSON response", Cause: err}
		}
		if response.Kind != value.Object || response.Get("ok") == nil || response.Get("ok").Kind != value.Bool {
			return nil, nativeRefusal("API_UNAVAILABLE", "Cairn returned an invalid response")
		}
		if result.ExitCode != 0 || !response.Get("ok").Boolean {
			code := nativeText(response.Get("status"))
			if code == "" || code == "OK" {
				code = "API_UNAVAILABLE"
			}
			return nil, nativeRefusal(code, "Cairn refused the lifecycle operation")
		}
		return response.Get("data"), nil
	}
}

func callValueError(err error) bool {
	var journalValue *journal.ValueError
	var syntax *json.SyntaxError
	return errors.As(err, &journalValue) || errors.As(err, &syntax) || sourceClass(err, "ValueError", "JSONDecodeError", "UnicodeDecodeError", "UnicodeEncodeError")
}
func callRunnerUnavailable(err error) bool {
	var path *os.PathError
	var call *os.SyscallError
	var errno syscall.Errno
	var execution *exec.Error
	var exit *exec.ExitError
	return errors.As(err, &path) || errors.As(err, &call) || errors.As(err, &errno) || errors.As(err, &execution) || errors.As(err, &exit) || errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) || errors.Is(err, exec.ErrWaitDelay) || errors.Is(err, os.ErrNotExist) || errors.Is(err, os.ErrPermission)
}

// NewRecoveryEngine binds the concrete file/journal owner and transcript side
// without an import cycle. Service defaults preserve explicit bridge and transport seams.
func NewRecoveryEngine(host *Host, evidence TranscriptEvidence, hooks RecoveryHooks, procRoot string) *RecoveryEngine {
	proc := NewProcessHooks(procRoot)
	if hooks.Alive == nil {
		hooks.Alive = proc.Alive
	}
	if hooks.Daemon == nil && hooks.DaemonView == nil {
		hooks.Daemon = proc.Daemon
		hooks.DaemonView = proc.DaemonView
	}
	var adapter *SessionJournalAdapter
	if host != nil {
		adapter, _ = host.Journal.(*SessionJournalAdapter)
	}
	clock := time.Now
	if adapter != nil && adapter.Now != nil {
		clock = adapter.Now
	}
	bindings := ServiceBindings{}
	if host != nil {
		bindings.Profile = host.Profile
		if host.Services != nil {
			bindings = *host.Services
			if bindings.Profile == nil {
				bindings.Profile = host.Profile
			}
		}
	}
	hooks = bindings.bind(host, hooks, procRoot, clock)
	return &RecoveryEngine{Host: host, Journal: adapter, Evidence: evidence, Hooks: hooks, Now: clock}
}
