package coordination

import (
	"bufio"
	"context"
	"errors"
	"io"
	"math/big"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func stringArray(xs ...string) *value.Value {
	out := &value.Value{Kind: value.Array}
	for _, s := range xs {
		out.Items = append(out.Items, &value.Value{Kind: value.String, String: journalPoints(s)})
	}
	return out
}
func requiredText(v *value.Value, key string) (string, error) {
	x, err := nativeRequired(v, key)
	if err != nil {
		return "", err
	}
	if x.Kind != value.String {
		return "", &journal.TypeError{Field: key}
	}
	return journalText(x.String), nil
}
func (c *CLI) ownerOnly(path, message string) error {
	if strings.ContainsRune(path, 0) {
		return &value.Error{Class: "ValueError", Message: "embedded null byte"}
	}
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != c.uid() || info.Mode().Perm()&0o077 != 0 {
		return nativeRefusal("INVALID_HOST", message)
	}
	return nil
}

func (c *CLI) contextJournal(v *value.Value) (journal.RawContext, error) {
	var out journal.RawContext
	stateDir, err := nativeRequired(v, "state_dir")
	if err != nil {
		return out, err
	}
	attempt, err := nativeRequired(v, "attempt_id")
	if err != nil {
		return out, err
	}
	out.StateDir, err = sourcePathText(c.profile(), stateDir)
	if err != nil {
		return out, err
	}
	if attempt.Kind != value.String {
		return out, &journal.TypeError{Field: "attempt_id"}
	}
	out.AttemptID = journalText(attempt.String)
	raw, err := journalGoValue(v)
	if err != nil {
		return out, err
	}
	out.Values = journal.DefaultJSONConstants(raw).(map[string]any)
	out.Command = func(name string) ([]any, error) {
		commands, err := nativeDefaultObj(v.Get("commands"), "commands")
		if err != nil {
			return nil, err
		}
		items, err := sourceList(commands.Get(name))
		if err != nil {
			return nil, err
		}
		raw, err := journalGoValue(items)
		if err != nil {
			return nil, err
		}
		return journal.DefaultJSONConstants(raw).([]any), nil
	}
	return out, nil
}

// ReadUTF8Input is available for a frontend that explicitly selects strict
// UTF-8 stdin. It reads Source's bounded character count without replacing an
// invalid byte. Selection and native codec qualification belong to the caller.
func ReadUTF8Input(in io.Reader, limit int) (string, error) {
	reader := bufio.NewReader(in)
	var body strings.Builder
	for n := 0; n < limit; n++ {
		r, size, err := reader.ReadRune()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return "", err
		}
		if r == utf8.RuneError && size == 1 {
			return "", &value.Error{Class: "UnicodeDecodeError", Message: "inbox stdin is not UTF-8"}
		}
		body.WriteRune(r)
	}
	return body.String(), nil
}

type sourceJournalRunner struct {
	profile *value.SourceReaderProfile
	base    journal.Runner
}

func (r sourceJournalRunner) Run(ctx context.Context, argv []string, input string) (journal.Result, error) {
	if r.profile == nil || r.profile.Routes == nil || r.profile.Routes.Filesystem() == nil {
		return journal.Result{}, &value.ProfileUnavailable{}
	}
	encoded := make([]string, len(argv))
	for i, arg := range argv {
		var err error
		encoded[i], err = r.profile.Routes.Filesystem().Encode(journalPoints(arg))
		if err != nil {
			return journal.Result{}, err
		}
		if strings.ContainsRune(encoded[i], 0) {
			return journal.Result{}, &value.Error{Class: "ValueError", Message: "embedded null byte"}
		}
	}
	return r.base.Run(ctx, encoded, input)
}

func (c *CLI) InboxCommand(ctx context.Context, argv []string, in io.Reader, out, errOut io.Writer) (int, error) {
	const prog = "coordination.py inbox"
	a, err := parseCLI(argv, prog, map[string]int{"--context": 1, "--version": 2, "--disposition": 1, "--code": 1})
	if err != nil {
		return 0, err
	}
	if err = requireCLI(a, prog, "--context"); err != nil {
		return 0, err
	}
	if len(a.position) == 0 {
		return 0, parseFail(prog, "the following arguments are required: kind")
	}
	if len(a.position) > 1 {
		return 0, parseFail(prog, "unrecognized arguments: "+strings.Join(a.position[1:], " "))
	}
	kind := a.position[0]
	if err = choiceCLI(prog, "kind", kind, []string{"complete", "ack", "respond", "finish-reply"}); err != nil {
		return 0, err
	}
	if a.has("--disposition") {
		if err = choiceCLI(prog, "--disposition", a.get("--disposition"), []string{"handled", "ignored", "failed"}); err != nil {
			return 0, err
		}
	}
	if a.has("--code") {
		if err = choiceCLI(prog, "--code", a.get("--code"), []string{"unsupported_kind", "source_unavailable", "processing_failed"}); err != nil {
			return 0, err
		}
	}
	path := purePosixPath(a.get("--context"))
	if err = c.ownerOnly(path, "native inbox context must be owner-only"); err != nil {
		return 0, err
	}
	native, err := c.read(path, SiteInboxContextRead)
	if err != nil {
		return 0, err
	}
	if native == nil || native.Kind != value.Object || !value.SameText(native.Get("schema"), "cairn.session-inbox/1") {
		return 0, nativeRefusal("INVALID_HOST", "not a native inbox context")
	}
	commands, err := nativeDefaultObj(native.Get("commands"), "commands")
	if err != nil {
		return 0, err
	}
	commandKind := kind
	if kind == "finish-reply" {
		commandKind = "complete"
	}
	command := commands.Get(commandKind)
	if !value.Truth(command) {
		return 0, nativeRefusal("INVALID_REQUEST", "this inbox context has no "+kind+" command")
	}
	// The journal owner validates the selected command and immutable request.
	// Read only when the selected command actually accepts stdin, in characters.
	items, err := sourceList(command)
	if err != nil {
		return 0, err
	}
	stdin := false
	for _, item := range items.Items {
		stdin = stdin || value.SameText(item, "--stdin")
	}
	request := journal.Request{Kind: kind, Version: a.values["--version"], Disposition: a.get("--disposition"), Code: a.get("--code")}
	if kind == "finish-reply" && (!value.SameText(native.Get("kind"), "request") || !value.Truth(commands.Get("respond"))) {
		return 0, nativeRefusal("INVALID_REQUEST", "finish-reply requires a request and its explicit reply command")
	}
	if kind == "respond" {
		id, e := journal.ReplyVersion(request.Version)
		if e != nil {
			var public *journal.Error
			if errors.As(e, &public) {
				return 0, nativeRefusal(public.Code, public.Message)
			}
			return 0, e
		}
		request.Version[1] = id
	}
	// Validate options before touching stdin or loading the journal, as Source.
	if kind != "respond" && a.has("--version") {
		return 0, nativeRefusal("INVALID_REQUEST", "--version applies only to a reply")
	}
	if kind == "ack" {
		d := request.Disposition
		if d == "" {
			d = "handled"
		}
		if d == "handled" && request.Code != "" || d != "handled" && request.Code == "" {
			return 0, nativeRefusal("INVALID_REQUEST", "ignored or failed acknowledgement needs a reason code")
		}
	} else if request.Disposition != "" || request.Code != "" {
		return 0, nativeRefusal("INVALID_REQUEST", "disposition and code apply only to acknowledgement")
	}

	if stdin {
		if c.ReadInput == nil {
			return 0, &value.ProfileUnavailable{}
		}
		request.Input, err = c.ReadInput(in, 1024*1024+1)
		if err != nil {
			return 0, err
		}
	}
	if utf8.RuneCountInString(request.Input) > 1024*1024 {
		return 0, nativeRefusal("INVALID_REQUEST", "inbox command input exceeds limit")
	}
	jctx, err := c.contextJournal(native)
	if err != nil {
		return 0, err
	}
	options := c.Journal
	base := options.Runner
	if base == nil {
		base = journal.CommandRunner{}
	}
	options.Runner = sourceJournalRunner{c.profile(), base}
	result, err := journal.ExecuteRaw(ctx, jctx, request, options)
	if err != nil {
		var public *journal.Error
		if errors.As(err, &public) {
			if public.Code == "INVALID_HOST" && public.Cause != nil {
				return 0, public.Cause
			}
			if public.ExitCode == 4 {
				return 4, c.output(out, nativeMap("schema", "cairn.response/1", "ok", false, "status", public.Code, "message", public.Error()), false)
			}
			return 0, nativeRefusal(public.Code, public.Message)
		}
		return 0, err
	}
	if _, err = io.WriteString(out, result.Stdout); err != nil {
		return 0, err
	}
	if _, err = io.WriteString(errOut, result.Stderr); err != nil {
		return 0, err
	}
	return result.ExitCode, nil
}

// InboxHandoff creates the same fenced commands used by native and manual
// delivery. The caller owns admission and persistence; selectors contain paths.
func (c *CLI) InboxHandoff(config, session, delivery *value.Value, attemptID string, requestIDs *value.Value) (string, *value.Value, error) {
	event, err := nativeRequired(delivery, "event")
	if err != nil {
		return "", nil, err
	}
	stateDir, err := nativeRequired(config, "state_dir")
	if err != nil {
		return "", nil, err
	}
	dir, err := sourcePathText(c.profile(), stateDir)
	if err != nil {
		return "", nil, err
	}
	target := sourceJoin(sourceJoin(dir, "inbox"), attemptID+".json")
	common := stringArray()
	for _, arg := range []struct {
		flag, key string
		object    *value.Value
	}{
		{"--socket", "socket", config}, {"--token-file", "token_file", config}, {"--agent-id", "agent_id", session}, {"--execution-id", "execution_id", session},
	} {
		x, e := nativeRequired(arg.object, arg.key)
		if e != nil {
			return "", nil, e
		}
		common.Items = append(common.Items, value.Text(arg.flag), x)
	}
	completionID, err := nativeRequired(requestIDs, "completion")
	if err != nil {
		return "", nil, err
	}
	leaseID, err := nativeRequired(delivery, "lease_id")
	if err != nil {
		return "", nil, err
	}
	leased := append(append([]*value.Value{}, common.Items...), value.Text("--request-id"), completionID, value.Text("--lease"), leaseID)
	cairn, err := nativeRequired(config, "cairn")
	if err != nil {
		return "", nil, err
	}
	deliveryID, err := nativeRequired(delivery, "delivery_id")
	if err != nil {
		return "", nil, err
	}
	complete := &value.Value{Kind: value.Array, Items: append(append([]*value.Value{cairn, value.Text("complete")}, leased...), value.Text("--shareable"), value.Text("--stdin"), deliveryID)}
	ack := &value.Value{Kind: value.Array, Items: append(append([]*value.Value{cairn, value.Text("ack")}, leased...), deliveryID)}
	commands := nativeMap("complete", complete, "ack", ack)
	kind, err := nativeRequired(event, "kind")
	if err != nil {
		return "", nil, err
	}
	if value.SameText(kind, "request") {
		responseID, e := nativeRequired(requestIDs, "response")
		if e != nil {
			return "", nil, e
		}
		sender, e := nativeRequired(event, "from")
		if e != nil {
			return "", nil, e
		}
		eventID, e := nativeRequired(event, "event_id")
		if e != nil {
			return "", nil, e
		}
		respond := &value.Value{Kind: value.Array, Items: append(append([]*value.Value{cairn, value.Text("publish")}, common.Items...), value.Text("--request-id"), responseID, value.Text("--to"), sender, value.Text("--kind"), value.Text("response"), value.Text("--causation-id"), eventID)}
		if value.Truth(event.Get("correlation_id")) {
			respond.Items = append(respond.Items, value.Text("--correlation-id"), event.Get("correlation_id"))
		}
		commands.Set("respond", respond)
	}
	if len(c.Command) == 0 {
		return "", nil, &value.ProfileUnavailable{}
	}
	journaled := append(append([]string{}, c.Command...), "inbox", "--context", target)
	eventID, err := nativeRequired(event, "event_id")
	if err != nil {
		return "", nil, err
	}
	sender, err := nativeRequired(event, "from")
	if err != nil {
		return "", nil, err
	}
	ref, err := nativeRequired(event, "ref")
	if err != nil {
		return "", nil, err
	}
	read := &value.Value{Kind: value.Array, Items: []*value.Value{cairn, value.Text("agent"), value.Text("--socket"), config.Get("socket"), value.Text("--token-file"), config.Get("token_file"), value.Text("history")}}
	native := nativeMap("schema", "cairn.session-inbox/1", "agent_id", session.Get("agent_id"), "execution_id", session.Get("execution_id"), "attempt_id", attemptID, "event_id", eventID, "kind", kind, "sender", sender, "source", ref, "delivery_id", deliveryID, "lease_id", leaseID, "read", read, "read_input", ref, "completion", stringArray(append(append([]string{}, journaled...), "complete")...), "acknowledgement", stringArray(append(append([]string{}, journaled...), "ack")...), "state_dir", stateDir, "commands", commands, "request_ids", requestIDs)
	if value.SameText(kind, "request") {
		native.Set("response", stringArray(append(journaled, "respond")...))
	}
	return target, native, nil
}

func (c *CLI) DrainCommand(ctx context.Context, argv []string, out io.Writer) (int, error) {
	const prog = "coordination.py inbox drain"
	a, err := parseCLI(argv, prog, map[string]int{"--config": 1, "--agent-id": 1, "--execution-id": 1, "--lease-seconds": 1})
	if err != nil {
		return 0, err
	}
	if err = requireCLI(a, prog, "--config", "--agent-id", "--execution-id"); err != nil {
		return 0, err
	}
	if len(a.position) > 0 {
		return 0, parseFail(prog, "unrecognized arguments: "+strings.Join(a.position, " "))
	}
	seconds := big.NewInt(900)
	if a.has("--lease-seconds") {
		parsed, e := procInteger(pythonStrip(a.get("--lease-seconds")))
		if e != nil {
			return 0, parseFail(prog, "argument --lease-seconds: invalid int value: '"+a.get("--lease-seconds")+"'")
		}
		seconds, _ = new(big.Int).SetString(parsed.Integer, 10)
	}
	if seconds.Cmp(big.NewInt(60)) < 0 || seconds.Cmp(big.NewInt(3600)) > 0 {
		return 0, nativeRefusal("INVALID_REQUEST", "--lease-seconds must be 60-3600")
	}
	config, err := LoadConfig(c.Host, a.get("--config"))
	if err != nil {
		return 0, err
	}
	session := nativeMap("agent_id", a.get("--agent-id"), "execution_id", a.get("--execution-id"))
	dir, err := sourcePathText(c.profile(), config.Get("state_dir"))
	if err != nil {
		return 0, err
	}
	entries, err := os.ReadDir(dir)
	if sourceOSError(err) {
		entries = nil
		err = nil
	}
	if err != nil {
		return 0, err
	}
	candidates := []string{}
	for _, entry := range entries {
		if strings.HasSuffix(entry.Name(), ".json") {
			candidates = append(candidates, entry.Name())
		}
	}
	sort.Strings(candidates)
	var owner *value.Value
	e := c.engine()
	for _, name := range candidates {
		state, err := c.read(sourceJoin(dir, name), SiteDrainStateRead)
		if err != nil {
			if sourceReadCaught(err) {
				continue
			}
			return 0, err
		}
		if state == nil || state.Kind != value.Object || state.Get("agent") == nil || state.Get("agent").Kind != value.Object {
			continue
		}
		ref, err := (&NativeSession{Host: c.Host}).sessionRef(state.Get("agent"))
		if err != nil {
			return 0, err
		}
		if !nativeEqual(ref, session) || value.Truth(state.Get("retired")) || value.Truth(state.Get("ending")) {
			continue
		}
		alive, err := e.alive(state.Get("process"))
		if err != nil {
			return 0, err
		}
		if alive {
			owner = state
			break
		}
	}
	if owner == nil {
		return 0, nativeRefusal("NOT_THIS_SESSION", "no live registered session of this binding has this agent and execution; drain only your own current session inbox")
	}
	if value.Truth(owner.Get("inbox_intent")) || value.Truth(owner.Get("inbox_attempt")) {
		return 0, nativeRefusal("NATIVE_ACTIVE", "a native attempt holds this inbox; handle the supplied native context instead of draining")
	}
	repo, err := nativeRequired(config, "repo")
	if err != nil {
		return 0, err
	}
	claimed, err := c.Host.Call(ctx, config, "event-next", nativeMap("repo", repo, "lease_seconds", value.IntegerText(seconds.String())), NativeCallOptions{Session: session})
	if err != nil {
		return 0, err
	}
	var delivery *value.Value
	if claimed != nil && claimed.Kind == value.Object {
		delivery = claimed.Get("delivery")
	}
	if !value.Truth(delivery) {
		return 0, c.output(out, nativeMap("schema", "cairn.inbox-drain/1", "delivery_id", nil, "message", "nothing claimable: the inbox is empty, or a native attempt or wake hold owns it"), false)
	}
	leaseID, err := requiredText(delivery, "lease_id")
	if err != nil {
		return 0, err
	}
	newID := uuid.NewString
	if c.Host.NewID != nil {
		newID = c.Host.NewID
	}
	target, native, err := c.InboxHandoff(config, session, delivery, "drain-"+leaseID, nativeMap("completion", newID(), "response", newID()))
	if err != nil {
		return 0, err
	}
	native.Set("route", value.Text("drain"))
	if c.Host.WriteContext == nil {
		return 0, &value.ProfileUnavailable{}
	}
	if err = c.Host.WriteContext(target, native); err != nil {
		return 0, err
	}
	common := []string{"--socket", nativeText(config.Get("socket")), "--token-file", nativeText(config.Get("token_file")), "--agent-id", a.get("--agent-id"), "--execution-id", a.get("--execution-id")}
	renew := append([]string{nativeText(config.Get("cairn")), "renew"}, common...)
	renew = append(renew, "--lease", leaseID, "--lease-seconds", seconds.String(), nativeText(delivery.Get("delivery_id")))
	event, err := nativeRequired(delivery, "event")
	if err != nil {
		return 0, err
	}
	result := nativeMap("schema", "cairn.inbox-drain/1", "delivery_id", delivery.Get("delivery_id"), "lease_id", delivery.Get("lease_id"), "event_id", event.Get("event_id"), "kind", event.Get("kind"), "sender", event.Get("from"), "context", target, "read", native.Get("read"), "read_input", event.Get("ref"), "completion", native.Get("completion"), "acknowledgement", native.Get("acknowledgement"), "renew", stringArray(renew...), "instructions", "Read the source with read and read_input. For a request, run completion with a concise selected result on stdin, then response with --version RESULT_VERSION RESULT_RECORD_UUID from its result. For a response or notice, run acknowledgement. Rerun the same command to retry an uncertain outcome. Run drain again for the next item.")
	if response := native.Get("response"); response != nil {
		result.Set("response", response)
	}
	return 0, c.output(out, result, false)
}

func (c *CLI) resolve(v *value.Value) (string, error) {
	p := c.profile()
	if p == nil || p.Routes == nil || p.Routes.Filesystem() == nil {
		return "", &value.ProfileUnavailable{}
	}
	points, err := p.Routes.Filesystem().Resolve(v)
	if err != nil {
		return "", err
	}
	return p.Routes.Filesystem().Encode(points)
}
func (c *CLI) compactRoot() (string, error) {
	root := c.Root
	if root != "" {
		return c.resolve(value.Text(root))
	}
	if c.LookupEnv != nil {
		if configured, present := c.LookupEnv("CAIRN_INBOX_ROOT"); present {
			return c.resolve(value.Text(configured))
		}
	} else if c.Env != nil {
		root = c.Env("CAIRN_INBOX_ROOT")
		if root != "" {
			return c.resolve(value.Text(root))
		}
	} else if configured, present := os.LookupEnv("CAIRN_INBOX_ROOT"); present {
		return c.resolve(value.Text(configured))
	}
	p := c.profile()
	if p == nil || p.Routes == nil || p.Routes.Filesystem() == nil {
		return "", &value.ProfileUnavailable{}
	}
	home, err := p.Routes.Filesystem().Home()
	if err != nil {
		return "", err
	}
	h, err := p.Routes.Filesystem().Encode(home)
	if err != nil {
		return "", err
	}
	return c.resolve(value.Text(filepath.Join(h, ".local/share/cairn/coordination")))
}
