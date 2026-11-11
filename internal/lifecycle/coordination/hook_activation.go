package coordination

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/ledger"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func formatValueJSON(v *value.Value, ensureASCII, compact, trailingLF bool) ([]byte, error) {
	var buf bytes.Buffer
	route := value.WriterRoute{
		Route:       "dumps",
		Codec:       &value.Codec{Name: "utf-8", Errors: "strict"},
		EnsureASCII: &ensureASCII,
		Compact:     &compact,
		TrailingLF:  &trailingLF,
	}
	if err := value.WriteKernel(&buf, v, route, 0); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func (c *CompatibilityContext) Validate(fallbackProfile *value.SourceReaderProfile, config *value.Value, requireEnabled bool) (*value.Value, error) {
	if err := c.CheckedBinding(fallbackProfile); err != nil {
		return nil, err
	}
	enabled := false
	if c.enabled != nil {
		enabled = *c.enabled
	} else if c.manifest != nil && c.manifest.Get("enabled") != nil && c.manifest.Get("enabled").Kind == value.Bool {
		enabled = c.manifest.Get("enabled").Boolean
	}
	if requireEnabled && !enabled {
		return nil, &value.Error{Class: "ValueError", Message: "inbox recall binding is disabled"}
	}
	matched := nativeEqual(config, c.memory)
	for _, coord := range c.coordinators {
		if matched {
			break
		}
		matched = nativeEqual(config, coord)
	}
	if !matched {
		return nil, &value.Error{Class: "ValueError", Message: "incompatible inbox recall profile binding"}
	}
	return c.memory, nil
}

func parseActivationResult(out, errBytes []byte, code int) (bool, error) {
	if code == 0 && len(errBytes) == 0 && (bytes.Equal(out, []byte("{\"enabled\":true}\n")) || bytes.Equal(out, []byte("{\"enabled\":false}\n"))) {
		return bytes.Equal(out, []byte("{\"enabled\":true}\n")), nil
	}
	if code != 1 || len(out) > 0 || len(errBytes) > 4096 {
		return false, &SubprocessError{Message: "invalid activation reply"}
	}
	frame, err := value.DecodeTextWithObjectPolicy(errBytes, "utf-8", 0, value.DuplicateReject)
	if err != nil || !exactReceiptObject(frame, "error") {
		return false, &SubprocessError{Message: "invalid activation reply"}
	}
	errObj := frame.Get("error")
	if !exactReceiptObject(errObj, "class", "reason", "errno") {
		return false, &SubprocessError{Message: "invalid activation reply"}
	}
	reasonVal := errObj.Get("reason")
	classVal := errObj.Get("class")
	errnoVal := errObj.Get("errno")
	if reasonVal == nil || reasonVal.Kind != value.String || classVal == nil || classVal.Kind != value.String {
		return false, &SubprocessError{Message: "invalid activation reply"}
	}
	reason := string(reasonVal.String)
	class := string(classVal.String)
	if reason != "activation" && reason != "compatibility_receipt" {
		return false, &SubprocessError{Message: "invalid activation reply"}
	}
	canonical, err := formatValueJSON(frame, true, true, true)
	if err != nil || !bytes.Equal(canonical, errBytes) {
		return false, &SubprocessError{Message: "invalid activation reply"}
	}
	if !nativeNone(errnoVal) {
		if errnoVal.Kind != value.Int {
			return false, &SubprocessError{Message: "invalid activation reply"}
		}
		n, parseErr := strconv.ParseInt(errnoVal.Integer, 10, 64)
		if parseErr != nil || n < 0 {
			return false, &SubprocessError{Message: "invalid activation reply"}
		}
	}
	if class == "CompatibilityPreflightError" && reason == "compatibility_receipt" && nativeNone(errnoVal) {
		return false, preflightError("compatibility_receipt")
	}
	allowed := []string{
		"ValueError", "TypeError", "KeyError", "AttributeError", "OSError", "FileNotFoundError",
		"PermissionError", "BlockingIOError", "FileExistsError", "NotADirectoryError", "IsADirectoryError", "TimeoutError",
		"JSONDecodeError", "UnicodeDecodeError", "UnicodeEncodeError",
	}
	if !nativeContains(allowed, class) {
		return false, &SubprocessError{Message: "invalid activation reply"}
	}
	return false, &value.Error{Class: class, Message: "lifecycle activation input"}
}

func openCompatibilityBinaryFile(path, expectedSHA string, uid uint32) (*os.File, error) {
	if !compatibilityCanonical(path) {
		return nil, &value.Error{Class: "ValueError", Message: "path"}
	}
	parts := strings.Split(path[1:], "/")
	parent, err := syscall.Open("/", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, &os.PathError{Op: "open", Path: "/", Err: err}
	}
	closedParent := false
	defer func() {
		if !closedParent {
			_ = syscall.Close(parent)
		}
	}()
	var s syscall.Stat_t
	for _, part := range parts[:len(parts)-1] {
		fd, e := syscall.Openat(parent, part, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
		if e != nil {
			return nil, &os.PathError{Op: "openat", Path: part, Err: e}
		}
		_ = syscall.Close(parent)
		parent = fd
		if e = syscall.Fstat(parent, &s); e != nil {
			return nil, &os.PathError{Op: "fstat", Path: part, Err: e}
		}
		if (s.Uid != 0 && s.Uid != uid) || (s.Mode&0o022 != 0 && !(s.Uid == 0 && s.Mode&syscall.S_ISVTX != 0)) {
			return nil, &value.Error{Class: "ValueError", Message: "ancestor"}
		}
	}
	if err := syscall.Fstat(parent, &s); err != nil {
		return nil, &os.PathError{Op: "fstat", Path: path, Err: err}
	}
	if s.Uid != uid || s.Mode&0o7777 != 0o700 {
		return nil, &value.Error{Class: "ValueError", Message: "private directory"}
	}
	fd, err := syscall.Openat(parent, parts[len(parts)-1], syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	_ = syscall.Close(parent)
	closedParent = true
	if err != nil {
		return nil, &os.PathError{Op: "openat", Path: path, Err: err}
	}
	file := os.NewFile(uintptr(fd), path)
	ok := false
	defer func() {
		if !ok {
			_ = file.Close()
		}
	}()
	if err := syscall.Fstat(int(file.Fd()), &s); err != nil {
		return nil, &os.PathError{Op: "fstat", Path: path, Err: err}
	}
	if s.Mode&syscall.S_IFMT != syscall.S_IFREG || s.Uid != uid || s.Mode&0o022 != 0 || s.Mode&0o100 == 0 || s.Mode&0o7000 != 0 {
		return nil, &value.Error{Class: "ValueError", Message: "file"}
	}
	h := sha256.New()
	count, err := io.Copy(h, file)
	if err != nil {
		return nil, err
	}
	var end syscall.Stat_t
	if err := syscall.Fstat(int(file.Fd()), &end); err != nil {
		return nil, &os.PathError{Op: "fstat", Path: path, Err: err}
	}
	if count != s.Size || !sameStatIdentity(s, end) || hex.EncodeToString(h.Sum(nil)) != expectedSHA {
		return nil, &value.Error{Class: "ValueError", Message: "changed"}
	}
	ok = true
	return file, nil
}

func (c *CompatibilityContext) defaultActivation(fallbackProfile *value.SourceReaderProfile, raw []byte) (bool, error) {
	if c.now() >= c.Deadline {
		return false, &os.SyscallError{Syscall: "lifecycle_activation_deadline", Err: syscall.ETIMEDOUT}
	}
	if signal.Ignored(syscall.SIGCHLD) {
		return false, &SubprocessError{Message: "activation wait status unavailable"}
	}
	prof := c.profile(fallbackProfile)
	receipt, err := c.ensureReceipt(prof)
	if err != nil {
		return false, err
	}
	if _, err := c.Config(prof, c.ConfigPath); err != nil {
		return false, err
	}
	binPath := nativeText(receipt.Get("binary").Get("path"))
	binSHA := nativeText(receipt.Get("binary").Get("sha256"))
	bindingSHA := nativeText(receipt.Get("binding").Get("sha256"))
	cfgSum := sha256.Sum256(c.currentRaw)
	cfgSHA := hex.EncodeToString(cfgSum[:])
	binFile, err := openCompatibilityBinaryFile(binPath, binSHA, c.uid())
	if err != nil {
		return false, err
	}
	defer func() { _ = binFile.Close() }()
	argv := []string{
		binPath, "lifecycle", "binding", "activation",
		"--entrypoint", c.role(),
		"--config", c.ConfigPath,
		"--config-sha256", cfgSHA,
		"--binding-sha256", bindingSHA,
		"--receipt", c.ReceiptPath,
		"--receipt-sha256", c.ReceiptSHA256,
		"--deadline", strconv.FormatFloat(c.Deadline, 'g', -1, 64),
	}
	out, errBytes, code, err := c.runActivationChild(binFile, argv, raw, 64)
	if err != nil {
		return false, err
	}
	return parseActivationResult(out, errBytes, code)
}

func (c *CompatibilityContext) runActivationChild(binFile *os.File, argv []string, raw []byte, stdoutLimit int) ([]byte, []byte, int, error) {
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT, syscall.SIGHUP)
	defer signal.Stop(sigCh)

	cmd := exec.Command("/proc/self/fd/3", argv[1:]...)
	cmd.Args = argv
	cmd.ExtraFiles = []*os.File{binFile}
	stdinPipe, err := cmd.StdinPipe()
	if err != nil {
		return nil, nil, 0, err
	}
	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		return nil, nil, 0, err
	}
	stderrPipe, err := cmd.StderrPipe()
	if err != nil {
		return nil, nil, 0, err
	}
	if err := cmd.Start(); err != nil {
		return nil, nil, 0, err
	}
	go func() {
		_, _ = stdinPipe.Write(raw)
		_ = stdinPipe.Close()
	}()
	outCh := make(chan activationStreamResult, 1)
	errCh := make(chan activationStreamResult, 1)
	boundCh := make(chan error, 2)
	go func() {
		b, e := io.ReadAll(io.LimitReader(stdoutPipe, int64(stdoutLimit)+1))
		if len(b) > stdoutLimit {
			boundCh <- &SubprocessError{Message: "activation reply exceeds bound"}
		}
		outCh <- activationStreamResult{b, e}
	}()
	go func() {
		b, e := io.ReadAll(io.LimitReader(stderrPipe, 4097))
		if len(b) > 4096 {
			boundCh <- &SubprocessError{Message: "activation reply exceeds bound"}
		}
		errCh <- activationStreamResult{b, e}
	}()
	return c.waitActivationChild(cmd, sigCh, boundCh, outCh, errCh)
}

type activationStreamResult struct {
	data []byte
	err  error
}

func (c *CompatibilityContext) waitActivationChild(
	cmd *exec.Cmd,
	sigCh chan os.Signal,
	boundCh chan error,
	outCh, errCh chan activationStreamResult,
) ([]byte, []byte, int, error) {
	rem := c.Deadline - c.now()
	if rem <= 0 {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
		return nil, nil, 0, &os.SyscallError{Syscall: "lifecycle_activation_deadline", Err: syscall.ETIMEDOUT}
	}
	timer := time.NewTimer(time.Duration(rem * float64(time.Second)))
	defer timer.Stop()

	doneCh := make(chan error, 1)
	var outRes, errRes activationStreamResult
	go func() {
		outRes = <-outCh
		errRes = <-errCh
		doneCh <- cmd.Wait()
	}()

	select {
	case sig := <-sigCh:
		_ = cmd.Process.Kill()
		<-doneCh
		signal.Stop(sigCh)
		if sysSig, ok := sig.(syscall.Signal); ok {
			signal.Reset(sig)
			_ = syscall.Kill(os.Getpid(), sysSig)
			os.Exit(128 + int(sysSig))
		}
		return nil, nil, 0, &SubprocessError{Message: "activation interrupted"}
	case bErr := <-boundCh:
		_ = cmd.Process.Kill()
		<-doneCh
		return nil, nil, 0, bErr
	case <-timer.C:
		_ = cmd.Process.Kill()
		<-doneCh
		return nil, nil, 0, &os.SyscallError{Syscall: "lifecycle_activation_deadline", Err: syscall.ETIMEDOUT}
	case waitErr := <-doneCh:
		select {
		case bErr := <-boundCh:
			return nil, nil, 0, bErr
		default:
		}
		if outRes.err != nil {
			return nil, nil, 0, outRes.err
		}
		if errRes.err != nil {
			return nil, nil, 0, errRes.err
		}
		code := 0
		if waitErr != nil {
			var exitErr *exec.ExitError
			if !errors.As(waitErr, &exitErr) {
				return nil, nil, 0, &SubprocessError{Message: "activation wait status unavailable", Cause: waitErr}
			}
			if exitErr.ProcessState != nil {
				if ws, ok := exitErr.ProcessState.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
					return nil, nil, 0, &SubprocessError{Message: "invalid activation reply"}
				}
			}
			code = exitErr.ExitCode()
		}
		return outRes.data, errRes.data, code, nil
	}
}


func (c *CLI) effectiveInboxConfig(ctx context.Context, config, event *value.Value, raw []byte) (*value.Value, error) {
	if c.Compatibility != nil {
		if raw == nil {
			var err error
			raw, err = formatValueJSON(event, true, true, false)
			if err != nil {
				return nil, err
			}
		}
		return c.Compatibility.EffectiveConfig(c.profile(), config, event, raw)
	}
	if !value.Truth(config.Get("inbox_recall_binding")) {
		return config, nil
	}
	cwd, err := os.Getwd()
	if err != nil {
		return nil, err
	}
	envMap := make(map[string][]rune)
	for _, s := range os.Environ() {
		if k, v, ok := strings.Cut(s, "="); ok {
			envMap[k] = admission.PathPoints(v)
		}
	}
	probe := admission.OSProbe{Directory: cwd}
	decision, err := admission.Decide(admission.Invocation{
		Kind:        "inbox_recall",
		Config:      config,
		Event:       event,
		Environment: envMap,
		Home: func() ([]rune, error) {
			if h, ok := envMap["HOME"]; ok {
				return h, nil
			}
			return admission.PathPoints(os.Getenv("HOME")), nil
		},
		Filesystem: probe,
	})
	if err != nil {
		return nil, err
	}
	if !decision.Run() {
		out := nativeClone(config)
		nativePop(out, "inbox_recall_binding")
		return out, nil
	}
	return c.resolveManifestActivation(ctx, config, event, envMap, probe)
}

func (c *CLI) resolveManifestActivation(
	ctx context.Context,
	config, event *value.Value,
	_ map[string][]rune,
	_ admission.OSProbe,
) (*value.Value, error) {
	pathVal := config.Get("inbox_recall_binding")
	path, err := sourcePathText(c.profile(), pathVal)
	if err != nil {
		return nil, err
	}
	if !filepath.IsAbs(path) {
		return nil, &CoordinationError{Code: "VALUE_ERROR", Message: "inbox recall binding must be owner-only", RefusalStage: "binding", RefusalReason: "manifest_owner", Cause: &value.Error{Class: "ValueError", Message: "inbox recall binding must be owner-only"}}
	}
	st, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !ok || sys.Uid != uint32(os.Getuid()) || st.Mode().Perm()&0o077 != 0 {
		return nil, &value.Error{Class: "ValueError", Message: "inbox recall binding must be owner-only"}
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	manifest, err := decodeCompatibilityJSON(c.profile(), SiteCompatibilityManifest, raw)
	if err != nil {
		return nil, err
	}
	if manifest == nil || manifest.Kind != value.Object || (!value.SameText(manifest.Get("schema"), "cairn.inbox-recall-binding/2") && !value.SameText(manifest.Get("schema"), "cairn.inbox-recall-binding/3")) || manifest.Get("enabled") == nil || manifest.Get("enabled").Kind != value.Bool {
		return nil, &value.Error{Class: "ValueError", Message: "unsupported inbox recall activation state"}
	}
	enabled := manifest.Get("enabled").Boolean
	if event != nil && event.Kind == value.Object && value.SameText(event.Get("hook_event_name"), "UserPromptSubmit") && inboxRecallWakeValue(event) {
		enabled, err = c.activationDecisionManifest(ctx, config, event, manifest, enabled)
		if err != nil {
			return nil, err
		}
	}
	if enabled {
		return config, nil
	}
	out := nativeClone(config)
	nativePop(out, "inbox_recall_binding")
	return out, nil
}

func claudePromptOwner(event *value.Value) (string, error) {
	if event == nil || event.Kind != value.Object {
		return "", &value.Error{Class: "ValueError", Message: "inbox recall requires actual Claude prompt ownership"}
	}
	prompt := event.Get("prompt_id")
	if prompt == nil || prompt.Kind != value.String || strings.TrimSpace(string(prompt.String)) == "" {
		return "", &value.Error{Class: "ValueError", Message: "inbox recall requires actual Claude prompt ownership"}
	}
	for _, r := range prompt.String {
		if r >= 0xd800 && r <= 0xdfff {
			return "", &value.Error{Class: "UnicodeEncodeError", Message: "Claude prompt owner"}
		}
		if r < 32 {
			return "", &value.Error{Class: "ValueError", Message: "inbox recall requires actual Claude prompt ownership"}
		}
	}
	s := string(prompt.String)
	if len(s) > 256 {
		return "", &value.Error{Class: "ValueError", Message: "inbox recall requires actual Claude prompt ownership"}
	}
	return "claude-channel:" + s, nil
}

func (c *CLI) activationDecisionManifest(ctx context.Context, config, event, manifest *value.Value, enabled bool) (bool, error) {
	harness := nativeText(config.Get("harness"))
	if (harness != "codex" && harness != "claude") || !value.SameText(event.Get("hook_event_name"), "UserPromptSubmit") {
		return enabled, nil
	}
	session := nativeText(event.Get("session_id"))
	var turn string
	if harness == "claude" {
		var err error
		turn, err = claudePromptOwner(event)
		if err != nil {
			return false, err
		}
	} else {
		turn = nativeText(event.Get("turn_id"))
	}
	if !isCanonicalUUID(session) || (harness == "codex" && !isCanonicalUUID(turn)) {
		return enabled, nil
	}
	memDesc, err := nativeRequired(manifest, "memory_config")
	if err != nil {
		return false, err
	}
	if memDesc.Kind != value.Object {
		return false, &journal.TypeError{Field: "memory_config"}
	}
	memPathVal, err := nativeRequired(memDesc, "path")
	if err != nil {
		return false, err
	}
	memPath, err := sourcePathText(c.profile(), memPathVal)
	if err != nil {
		return false, err
	}
	st, err := os.Stat(memPath)
	if err != nil {
		return false, err
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !filepath.IsAbs(memPath) || !ok || sys.Uid != uint32(os.Getuid()) || st.Mode().Perm()&0o022 != 0 {
		return false, &value.Error{Class: "ValueError", Message: "activation ledger configuration is not owner-controlled"}
	}
	memRaw, err := os.ReadFile(memPath)
	if err != nil {
		return false, err
	}
	memory, err := decodeCompatibilityJSON(c.profile(), SiteCompatibilityConfig, memRaw)
	if err != nil {
		return false, err
	}
	if memory == nil || memory.Kind != value.Object || memory.Get("state_dir") == nil || memory.Get("state_dir").Kind != value.String {
		return false, &value.Error{Class: "ValueError", Message: "activation ledger configuration lacks a state directory"}
	}
	return ledger.DecideActivation(ctx, ledger.Activation{Memory: memory, Harness: harness, Session: session, Turn: turn, Enabled: enabled})
}

func canonicalSortedJSONBytes(v *value.Value) ([]byte, error) {
	if v == nil {
		return []byte("null"), nil
	}
	switch v.Kind {
	case value.Null, value.Bool, value.Int, value.Float, value.String:
		return formatValueJSON(v, true, true, false)
	case value.Array:
		var buf bytes.Buffer
		buf.WriteByte('[')
		for i, item := range v.Items {
			if i > 0 {
				buf.WriteByte(',')
			}
			b, err := canonicalSortedJSONBytes(item)
			if err != nil {
				return nil, err
			}
			buf.Write(b)
		}
		buf.WriteByte(']')
		return buf.Bytes(), nil
	case value.Object:
		sorted := nativeClone(v)
		sortObjectMembers(sorted)
		var buf bytes.Buffer
		buf.WriteByte('{')
		for i, m := range sorted.Members {
			if i > 0 {
				buf.WriteByte(',')
			}
			kb, err := formatValueJSON(&value.Value{Kind: value.String, String: m.Key}, true, true, false)
			if err != nil {
				return nil, err
			}
			vb, err := canonicalSortedJSONBytes(m.Value)
			if err != nil {
				return nil, err
			}
			buf.Write(kb)
			buf.WriteByte(':')
			buf.Write(vb)
		}
		buf.WriteByte('}')
		return buf.Bytes(), nil
	default:
		return nil, &value.Error{Class: "TypeError", Message: "unsupported JSON value"}
	}
}

func sortObjectMembers(v *value.Value) {
	if v == nil || v.Kind != value.Object {
		return
	}
	for i := 0; i < len(v.Members); i++ {
		for j := i + 1; j < len(v.Members); j++ {
			if string(v.Members[j].Key) < string(v.Members[i].Key) {
				v.Members[i], v.Members[j] = v.Members[j], v.Members[i]
			}
		}
	}
}

func canonicalSortedJSONSHA256(v *value.Value) (string, error) {
	b, err := canonicalSortedJSONBytes(v)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:]), nil
}

func (c *CLI) settledJournalIndex(config, state *value.Value) bool {
	if state == nil || state.Kind != value.Object {
		return false
	}
	journals := state.Get("inbox_journals")
	if journals == nil {
		return true
	}
	if journals.Kind != value.Array {
		return false
	}
	stateDir, err := sourcePathText(c.profile(), config.Get("state_dir"))
	if err != nil {
		return false
	}
	for _, item := range journals.Items {
		if item == nil || item.Kind != value.String {
			return false
		}
		target := sourceJoin(sourceJoin(stateDir, "intents"), string(item.String)+".json")
		exists, err := sourceExists(target, false)
		if err != nil {
			return false
		}
		if !exists {
			continue
		}
		jObj, err := c.read(target, SiteCompactJournalRead)
		if err != nil || jObj == nil || jObj.Kind != value.Object {
			return false
		}
		for _, name := range []string{"completion", "response"} {
			slot := jObj.Get(name)
			if value.Truth(slot) {
				if slot.Kind != value.Object {
					return false
				}
				st := nativeText(slot.Get("status"))
				if st != "committed" && st != "refused" {
					return false
				}
			}
		}
	}
	return true
}

func (c *CLI) hasRetainedSessionJournals(config, state *value.Value) (bool, error) {
	stateDir, err := sourcePathText(c.profile(), config.Get("state_dir"))
	if err != nil {
		return false, err
	}
	entries, err := filepath.Glob(filepath.Join(stateDir, "intents", "*.json"))
	if err != nil {
		return false, err
	}
	for _, entry := range entries {
		if _, err := c.read(entry, SiteCompactJournalRead); err != nil {
			return false, err
		}
	}
	cloned := nativeClone(state)
	if err := c.seedSessionJournals(config, cloned); err != nil {
		return false, err
	}
	j := cloned.Get("inbox_journals")
	return j != nil && j.Kind == value.Array && len(j.Items) > 0, nil
}

func (c *CLI) ReconcileRejectedWake(ctx context.Context, config, state *value.Value, statePath string) (bool, error) {
	if state == nil || state.Kind != value.Object {
		return false, nil
	}
	keyVal := state.Get("wake_rejection")
	if keyVal == nil || keyVal.Kind != value.String || !hexText(keyVal.String, 64) {
		return false, nil
	}
	key := string(keyVal.String)
	target := sourceJoin(sourceJoin(filepath.Dir(statePath), "wake-refusals"), key+".json")
	lst, err := os.Lstat(target)
	if err != nil {
		return false, err
	}
	sys, ok := lst.Sys().(*syscall.Stat_t)
	if lst.Mode()&os.ModeSymlink != 0 || !ok || sys.Uid != uint32(os.Getuid()) || lst.Mode().Perm()&0o077 != 0 {
		return false, &value.Error{Class: "ValueError", Message: "wake refusal receipt must be owner-only"}
	}
	receipt, err := c.read(target, SiteSessionRead)
	if err != nil {
		return false, err
	}
	computed, err := canonicalSortedJSONSHA256(receipt)
	if err != nil || computed != key {
		return false, &value.Error{Class: "ValueError", Message: "wake refusal receipt changed"}
	}
	wakeObj := state.Get("idle_wake")
	recorded := receipt.Get("wake")
	matches := nativeEqual(wakeObj, recorded)
	if !matches && recorded != nil && recorded.Kind == value.Object && value.SameText(recorded.Get("status"), "uncertain") {
		sub := nativeClone(recorded)
		sub.Set("status", value.Text("submitted"))
		matches = nativeEqual(wakeObj, sub)
	}
	agent, err := nativeRequired(state, "agent")
	if err != nil {
		return false, err
	}
	ref, err := c.sessionRef(agent)
	if err != nil {
		return false, err
	}
	procVal, err := nativeRequired(state, "process")
	if err != nil {
		return false, err
	}
	recProc, err := nativeRequired(receipt, "process")
	if err != nil {
		return false, err
	}
	same, err := nativeSameProcess(procVal, recProc)
	if err != nil {
		return false, err
	}
	alive, err := c.processAlive(procVal)
	if err != nil {
		return false, err
	}
	if !value.SameText(receipt.Get("schema"), "cairn.preadmission-wake-refusal/1") ||
		!value.SameText(receipt.Get("phase"), "activation") || !matches ||
		wakeObj == nil || wakeObj.Kind != value.Object ||
		(!value.SameText(wakeObj.Get("status"), "uncertain") && !value.SameText(wakeObj.Get("status"), "submitted")) ||
		!value.SameText(wakeObj.Get("transport"), "claude-channel") ||
		!nativeEqual(wakeObj.Get("session"), ref) ||
		!nativeEqual(wakeObj.Get("native_id"), agent.Get("native_session_id")) ||
		!same || !alive || value.Truth(state.Get("ending")) || value.Truth(state.Get("retired")) ||
		value.Truth(state.Get("inbox_intent")) || value.Truth(state.Get("inbox_attempt")) ||
		value.Truth(state.Get("inbox_result_policy")) || value.Truth(state.Get("inbox_close")) ||
		value.Truth(state.Get("active_prompt")) || !c.settledJournalIndex(config, state) {
		return false, nil
	}
	return c.reconcileRejectedWakeStoreAndLedger(ctx, config, state, statePath, key, receipt, wakeObj, agent, ref)
}

func (c *CLI) reconcileRejectedWakeStoreAndLedger(
	ctx context.Context,
	config, state *value.Value,
	statePath, key string,
	receipt, wakeObj, agent, ref *value.Value,
) (bool, error) {
	if state.Get("inbox_journals") == nil {
		hasJournals, err := c.hasRetainedSessionJournals(config, state)
		if err != nil || hasJournals {
			return false, err
		}
	}
	eventIDVal := wakeObj.Get("event_id")
	if eventIDVal == nil || eventIDVal.Kind != value.String || !isCanonicalUUID(string(eventIDVal.String)) {
		return false, nil
	}
	eventID := string(eventIDVal.String)
	inspected, err := c.call(ctx, config, "event-inspect", nativeMap("event_id", eventID, "limit", 100), NativeCallOptions{Session: ref, Timeout: time.Second})
	if err != nil {
		return false, err
	}
	evObj, err := nativeObj(inspected.Get("event"), "event")
	if err != nil {
		return false, err
	}
	delivVal := inspected.Get("deliveries")
	var matches []*value.Value
	if delivVal != nil && delivVal.Kind == value.Array {
		for _, item := range delivVal.Items {
			if item != nil && item.Kind == value.Object && nativeEqual(item.Get("delivery_id"), wakeObj.Get("delivery_id")) {
				matches = append(matches, item)
			}
		}
	}
	if !value.SameText(evObj.Get("event_id"), eventID) || !nativeEqual(evObj.Get("repo"), config.Get("repo")) ||
		len(matches) != 1 || !nativeEqual(matches[0].Get("consumer"), agent.Get("inbox")) ||
		!value.SameText(matches[0].Get("state"), "pending") ||
		matches[0].Get("attempts") == nil || matches[0].Get("attempts").Kind != value.Int ||
		matches[0].Get("attempts").Integer != "0" {
		return false, nil
	}
	ready, err := c.call(ctx, config, "session-inbox-ready", ref, NativeCallOptions{Timeout: time.Second})
	if err != nil {
		return false, err
	}
	if !nativeEqual(ready.Get("delivery_id"), wakeObj.Get("delivery_id")) || !value.SameText(ready.Get("event_id"), eventID) {
		return false, nil
	}
	return c.reconcileRejectedWakeLedger(config, state, statePath, key, receipt, wakeObj)
}

func (c *CLI) reconcileRejectedWakeLedger(
	config, state *value.Value,
	statePath, key string,
	receipt, wakeObj *value.Value,
) (bool, error) {
	var memDirVal *value.Value
	if c.Compatibility != nil {
		mem, err := c.Compatibility.Validate(c.profile(), config, false)
		if err != nil {
			return false, err
		}
		memDirVal = mem.Get("state_dir")
	} else {
		var err error
		memDirVal, err = c.grants().MemoryStateDir(config)
		if err != nil {
			return false, err
		}
	}
	memDir, err := sourcePathText(c.profile(), memDirVal)
	if err != nil {
		return false, err
	}
	if err := os.MkdirAll(memDir, 0o700); err != nil {
		return false, err
	}
	st, err := os.Stat(memDir)
	if err != nil {
		return false, err
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !ok || sys.Uid != uint32(os.Getuid()) || st.Mode().Perm()&0o022 != 0 {
		return false, &value.Error{Class: "ValueError", Message: "inbox ledger directory is not owner-controlled"}
	}
	nativeVal := wakeObj.Get("native_id")
	nativeStr := nativeText(nativeVal)
	lockPath := sourceJoin(memDir, nativeStr+".lock")
	lockFile, err := os.OpenFile(lockPath, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o600)
	if err != nil {
		return false, err
	}
	defer func() { _ = lockFile.Close() }()
	if err := syscall.Flock(int(lockFile.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return false, err
	}
	defer func() { _ = syscall.Flock(int(lockFile.Fd()), syscall.LOCK_UN) }()
	grantsLedger, err := c.grants().LoadGrants(sourceJoin(memDir, nativeStr+".inbox-recall.json"), nativeVal)
	if err != nil {
		return false, err
	}
	grantsObj := grantsLedger.Get("grants")
	expectedOwner := "claude-channel:" + nativeText(receipt.Get("native_prompt_id"))
	if grantsObj != nil && grantsObj.Kind == value.Object {
		for _, m := range grantsObj.Members {
			idObj := m.Value.Get("identity")
			if idObj != nil && idObj.Kind == value.Object {
				if nativeEqual(idObj.Get("delivery_id"), wakeObj.Get("delivery_id")) || value.SameText(idObj.Get("native_owner"), expectedOwner) {
					return false, nil
				}
			}
		}
	}
	now := c.clock()
	nowFloat := float64(now.Unix()) + float64(now.Nanosecond())/1e9
	updated := nativeClone(state)
	nativePop(updated, "idle_wake", "wake_refusal")
	updated.Set("wake_reconciled", nativeMap("receipt", key, "at", nowFloat))
	if err := c.writeState(statePath, updated); err != nil {
		return false, err
	}
	state.Members = updated.Members
	return true, nil
}

func (c *CLI) recordActivationRefusal(ctx context.Context, config, event *value.Value, cause error) error {
	if !value.SameText(config.Get("harness"), "claude") || event == nil || event.Kind != value.Object || !value.SameText(event.Get("hook_event_name"), "UserPromptSubmit") {
		return nil
	}
	native := nativeText(event.Get("session_id"))
	promptVal := event.Get("prompt_id")
	if !nativeIDValid(event.Get("session_id")) || promptVal == nil || promptVal.Kind != value.String {
		return nil
	}
	promptID := string(promptVal.String)
	if strings.TrimSpace(promptID) == "" || len(promptID) > 256 {
		return nil
	}
	for _, r := range promptVal.String {
		if r < 32 {
			return nil
		}
	}
	process, err := c.OwnerProcess(config, event)
	if err != nil {
		return err
	}
	path, err := c.statePath(config, native)
	if err != nil {
		return err
	}
	lock, err := c.lockState(path)
	if err != nil {
		return err
	}
	defer func() { _ = lock.Close() }()
	state, err := c.read(path, SiteSessionRead)
	if err != nil {
		return err
	}
	return c.finishRecordActivationRefusal(ctx, config, event, state, path, process, native, promptID, cause)
}

func (c *CLI) finishRecordActivationRefusal(
	ctx context.Context,
	config, event, state *value.Value,
	path string,
	process *value.Value,
	native, promptID string,
	cause error,
) error {
	if state == nil || state.Kind != value.Object {
		return &journal.NonObjectError{Field: "state"}
	}
	wakeObj, err := nativeObj(state.Get("idle_wake"), "idle_wake")
	if err != nil {
		return err
	}
	agent, err := nativeRequired(state, "agent")
	if err != nil {
		return err
	}
	session, err := c.sessionRef(agent)
	if err != nil {
		return err
	}
	stateProc, err := nativeRequired(state, "process")
	if err != nil {
		return err
	}
	sameState, err := nativeSameProcess(process, stateProc)
	if err != nil {
		return err
	}
	parentObj, err := nativeObj(wakeObj.Get("parent"), "parent")
	if err != nil {
		return err
	}
	sameParent, err := nativeSameProcess(process, parentObj)
	if err != nil {
		return err
	}
	alive, err := c.processAlive(process)
	if err != nil {
		return err
	}
	if !value.SameText(wakeObj.Get("transport"), "claude-channel") ||
		(!value.SameText(wakeObj.Get("status"), "uncertain") && !value.SameText(wakeObj.Get("status"), "submitted")) ||
		!value.SameText(wakeObj.Get("native_id"), native) || !nativeEqual(wakeObj.Get("session"), session) ||
		!sameState || !sameParent || !alive || value.Truth(state.Get("ending")) || value.Truth(state.Get("retired")) {
		return nil
	}
	msgs, err := wakeMessagesValue(wakeObj)
	if err != nil {
		return err
	}
	promptMatched := false
	for _, text := range msgs {
		expected := fmt.Sprintf("<channel source=\"cairn-events\" native_session_id=%q agent_id=%q execution_id=%q delivery_id=%q>\n%s\n</channel>",
			native, nativeText(session.Get("agent_id")), nativeText(session.Get("execution_id")), nativeText(wakeObj.Get("delivery_id")), text)
		if value.SameText(event.Get("prompt"), expected) {
			promptMatched = true
			break
		}
	}
	if !promptMatched {
		return nil
	}
	receipt := nativeMap(
		"schema", "cairn.preadmission-wake-refusal/1",
		"phase", "activation",
		"error_class", refusalErrorClass(cause),
		"wake", wakeObj,
		"process", nativeMap("pid", process.Get("pid"), "start", process.Get("start"), "boot", process.Get("boot")),
		"native_prompt_id", promptID,
	)
	key, err := canonicalSortedJSONSHA256(receipt)
	if err != nil {
		return err
	}
	target := sourceJoin(sourceJoin(filepath.Dir(path), "wake-refusals"), key+".json")
	if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
		return err
	}
	exists, err := sourceExists(target, false)
	if err != nil {
		return err
	}
	if exists {
		existing, err := c.read(target, SiteSessionRead)
		if err != nil || !nativeEqual(existing, receipt) {
			return &value.Error{Class: "ValueError", Message: "wake refusal receipt changed"}
		}
	} else if err := c.writeState(target, receipt); err != nil {
		return err
	}
	state.Set("wake_rejection", value.Text(key))
	if err := c.writeState(path, state); err != nil {
		return err
	}
	_, err = c.ReconcileRejectedWake(ctx, config, state, path)
	return err
}
