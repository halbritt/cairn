package coordination

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const (
	SiteConfigRead         = "coordination.config.read"
	SiteCLIOutput          = "coordination.cli.output"
	SiteRecoveryOutput     = "coordination.cli.recovery_output"
	SiteDrainStateRead     = "coordination.drain.state_read"
	SiteInboxContextRead   = "coordination.inbox.context_read"
	SiteInboxContextWrite  = "coordination.inbox.context_write"
	SiteCompactIndexRead   = "coordination.compact.index_read"
	SiteCompactStateRead   = "coordination.compact.state_read"
	SiteCompactContextRead = "coordination.compact.context_read"
	SiteCompactJournalRead = "coordination.compact.journal_read"
	SiteCompactReplyRead   = "coordination.compact.reply_read"
)

var cliOutcomes = []string{"admitted_active", "completed", "cancelled", "never_admitted", "unknown"}
var cliLocator = regexp.MustCompile(`^(0|[1-9][0-9]{0,15}):([1-9][0-9]{0,5}):([0-9a-f]{64})$`)

func ParseLocator(text string) (*value.Value, error) {
	m := cliLocator.FindStringSubmatch(text)
	if len(m) == 0 || len(m[2]) > 5 || len(m[2]) == 5 && m[2] > "65536" {
		return nil, nativeRefusal("INVALID_REQUEST", "native record locator must be OFFSET:LENGTH:SHA256 within bounds")
	}
	return nativeMap("offset", value.IntegerText(m[1]), "length", value.IntegerText(m[2]), "sha256", m[3]), nil
}

func absoluteText(v *value.Value) bool {
	return v != nil && v.Kind == value.String && len(v.String) > 0 && v.String[0] == '/'
}
func boundedName(v *value.Value, nonempty bool) bool {
	if v == nil || v.Kind != value.String || len(v.String) > 128 || nonempty && len(v.String) == 0 {
		return false
	}
	for _, r := range v.String {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '_' || r == '.' || r == '-') {
			return false
		}
	}
	return true
}
func nativeIDValid(v *value.Value) bool {
	if v == nil || v.Kind != value.String || strings.TrimSpace(string(v.String)) == "" {
		return false
	}
	n := 0
	for _, r := range v.String {
		if r < 32 || r >= 0xd800 && r <= 0xdfff {
			return false
		}
		switch {
		case r < 0x80:
			n++
		case r < 0x800:
			n += 2
		case r < 0x10000:
			n += 3
		default:
			n += 4
		}
	}
	return n <= 256
}

// ValidateConfig follows the Source's check order. In particular optional flag
// types use exact bool/int checks; ordinary equality sites still admit bools.
func ValidateConfig(config *value.Value) (*value.Value, error) {
	if config == nil || config.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: "config"}
	}
	fail := func(s string) (*value.Value, error) { return nil, nativeRefusal("INVALID_CONFIG", s) }
	for _, key := range []string{"cairn", "socket", "token_file", "state_dir"} {
		if !absoluteText(config.Get(key)) {
			return fail("absolute " + key + " required")
		}
	}
	h := nativeText(config.Get("harness"))
	if !nativeContains([]string{"codex", "claude", "agy", "opencode", "hermes"}, h) || !value.Truth(config.Get("repo")) || !value.Truth(config.Get("binding")) {
		return fail("harness, collection and binding required")
	}
	if v := config.Get("opencode_cancel_enabled"); v != nil && (h != "opencode" || v.Kind != value.Bool) {
		return fail("opencode_cancel_enabled requires an OpenCode boolean")
	}
	if v := config.Get("remind_unacknowledged_turn_end"); v != nil && v.Kind != value.Bool {
		return fail("remind_unacknowledged_turn_end requires a boolean")
	}
	if v := config.Get("compact_inbox_version"); v != nil && (v.Kind != value.Int || v.Integer != "1" || !nativeContains([]string{"codex", "claude"}, h) || !value.Truth(config.Get("native_delivery"))) {
		return fail("compact_inbox_version 1 requires Codex or Claude native delivery")
	}
	if v := config.Get("notice_batch_version"); v != nil && (v.Kind != value.Int || v.Integer != "1" || !nativeEqual(config.Get("compact_inbox_version"), value.IntegerText("1")) || !nativeContains([]string{"codex", "claude"}, h) || !value.Truth(config.Get("native_delivery"))) {
		return fail("notice_batch_version 1 requires compact Codex or Claude native delivery")
	}
	binding := config.Get("binding")
	if wake := config.Get("idle_wakeup"); !nativeNone(wake) {
		bridge := h == "opencode" && wake.Kind == value.Bool && wake.Boolean
		if !value.Truth(config.Get("native_delivery")) || !(bridge || absoluteText(wake)) {
			return fail("idle_wakeup requires an absolute Herdr executable and native_delivery (true is accepted only for the OpenCode bridge)")
		}
	}
	if sessions := config.Get("claude_channel_sessions"); sessions != nil {
		if !absoluteText(config.Get("claude_channel_dir")) {
			return fail("claude_channel_sessions requires an absolute claude_channel_dir")
		}
		valid := h == "claude" && sessions.Kind == value.Array
		for _, s := range sessions.Items {
			valid = valid && nativeIDValid(s)
		}
		if !valid {
			return fail("claude_channel_sessions requires a Claude list of bounded native session IDs")
		}
		seen := map[string]bool{}
		for _, s := range sessions.Items {
			key := nativeText(s)
			if seen[key] {
				return fail("claude_channel_sessions must not contain duplicate native session IDs")
			}
			seen[key] = true
		}
	}
	if server := config.Get("claude_channel_server"); server != nil {
		valid := h == "claude" && server.Kind == value.String && len(server.String) > 0 && len(server.String) <= 128
		for _, r := range server.String {
			valid = valid && r != ',' && r != ':' && r > 32
		}
		if !valid {
			return fail("claude_channel_server requires a bounded Claude MCP server name")
		}
	}
	if !boundedName(binding, false) || value.SameText(binding, ".") || value.SameText(binding, "..") {
		return fail("invalid binding name")
	}
	return config, nil
}

func LoadConfig(host *Host, path string) (*value.Value, error) {
	if host == nil || host.ReadConfig == nil {
		return nil, &value.ProfileUnavailable{}
	}
	path = purePosixPath(path)
	if strings.ContainsRune(path, 0) {
		return nil, &value.Error{Class: "ValueError", Message: "embedded null byte"}
	}
	v, err := host.ReadConfig(path)
	if err != nil {
		return nil, err
	}
	return ValidateConfig(v)
}

// CLI is a callable frontend. Command is the invocation prefix that this host
// actually installs (used in generated contexts). NextContext is the native
// frontend's inbox_context owner; the CLI does not duplicate hook admission or
// render a substitute context after notice advancement. All JSON sites require
// the selected profile, independently of the session reader/writer sites.
type CLI struct {
	Host           *Host
	Engine         *RecoveryEngine
	Profile        *value.SourceReaderProfile
	Journal        journal.Options
	Command        []string
	Now            func() time.Time
	Root           string
	ProcRoot       string
	UID            *uint32
	Env            func(string) string
	CallerDescends func(*value.Value) (bool, error)
	// ReadInput is the selected stdin text codec, bounded in characters. The
	// frontend must supply it for complete/finish-reply; no locale is inferred.
	ReadInput   func(io.Reader, int) (string, error)
	NextContext func(context.Context, *value.Value, *value.Value, string, *value.Value) (*value.Value, error)
}

func (c *CLI) profile() *value.SourceReaderProfile {
	if c.Profile != nil {
		return c.Profile
	}
	if c.Host != nil {
		return c.Host.Profile
	}
	return nil
}
func (c *CLI) read(path, site string) (*value.Value, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return c.profile().Decode(site, raw)
}
func (c *CLI) env(name string) string {
	if c.Env != nil {
		return c.Env(name)
	}
	return os.Getenv(name)
}
func (c *CLI) clock() time.Time {
	if c.Now != nil {
		return c.Now()
	}
	return time.Now()
}
func (c *CLI) engine() *RecoveryEngine {
	if c.Engine != nil {
		return c.Engine
	}
	return NewRecoveryEngine(c.Host, nil, RecoveryHooks{}, c.ProcRoot)
}
func (c *CLI) uid() uint32 {
	if c.UID != nil {
		return *c.UID
	}
	return uint32(os.Getuid())
}

// sortedSource clones iteratively, retaining rune keys and number/string atoms.
// It preserves cycles for the selected writer's own recursion/circular policy.
func sortedSource(v *value.Value) *value.Value {
	if v == nil {
		return nil
	}
	seen := map[*value.Value]*value.Value{}
	clone := func(n *value.Value) *value.Value {
		if n == nil {
			return nil
		}
		if x := seen[n]; x != nil {
			return x
		}
		x := *n
		x.Items = nil
		x.Members = nil
		seen[n] = &x
		return &x
	}
	root := clone(v)
	pending := []*value.Value{v}
	done := map[*value.Value]bool{}
	for len(pending) > 0 {
		n := pending[len(pending)-1]
		pending = pending[:len(pending)-1]
		if done[n] {
			continue
		}
		done[n] = true
		x := seen[n]
		for _, child := range n.Items {
			x.Items = append(x.Items, clone(child))
			if child != nil {
				pending = append(pending, child)
			}
		}
		for _, m := range n.Members {
			x.Members = append(x.Members, value.Member{Key: append([]rune(nil), m.Key...), Value: clone(m.Value)})
			if m.Value != nil {
				pending = append(pending, m.Value)
			}
		}
		sort.SliceStable(x.Members, func(i, j int) bool {
			a, b := x.Members[i].Key, x.Members[j].Key
			for k := 0; k < len(a) && k < len(b); k++ {
				if a[k] != b[k] {
					return a[k] < b[k]
				}
			}
			return len(a) < len(b)
		})
	}
	return root
}
func (c *CLI) output(out io.Writer, v *value.Value, sorted bool) error {
	site := SiteCLIOutput
	if sorted {
		site = SiteRecoveryOutput
		v = sortedSource(v)
	}
	return c.profile().Write(site, out, v)
}

type cliParseError struct{ prog, message string }
type cliHelp struct{ prog string }

func (e *cliHelp) Error() string { return e.prog + " help" }

func (e *cliParseError) Error() string     { return e.message }
func parseFail(prog, message string) error { return &cliParseError{prog, message} }

func (c *CLI) usage(prog string) string {
	var groups []string
	switch prog {
	case "coordination.py inbox drain":
		groups = []string{"[-h]", "--config CONFIG", "--agent-id AGENT_ID", "--execution-id EXECUTION_ID", "[--lease-seconds LEASE_SECONDS]"}
	case "coordination.py inbox":
		groups = []string{"[-h]", "--context CONTEXT", "[--version VERSION RECORD_UUID]", "[--disposition {handled,ignored,failed}]", "[--code {unsupported_kind,source_unavailable,processing_failed}]", "{complete,ack,respond,finish-reply}"}
	case "cairn-inbox":
		groups = []string{"[-h]", "[--code {unsupported_kind,source_unavailable,processing_failed}]", "{read,inspect,settle,advance}", "handle", "[{handled,ignored,failed}]"}
	default:
		groups = []string{"[-h]", "[--config CONFIG]", "[--event EVENT]", "[--config-dir CONFIG_DIR]", "[--once]", "[--native-session-id NATIVE_SESSION_ID]", "[--attempt-id ATTEMPT_ID]", "[--native-turn-id NATIVE_TURN_ID]", "[--delivery-id DELIVERY_ID]", "[--queued-submission-id QUEUED_SUBMISSION_ID]", "[--outcome {admitted_active,completed,cancelled,never_admitted,unknown}]", "[--record RECORD]", "[--enqueue-record ENQUEUE_RECORD]", "[--dequeue-record DEQUEUE_RECORD]", "[--refusal-record REFUSAL_RECORD]", "[--start-record START_RECORD]", "[--context-record CONTEXT_RECORD]", "[--end-record END_RECORD]", "[--check-only]", "{hook,watch,recover-refused-wake,recover-native-refused-wake,recover-codex-refused-wake,recover-codex-ended-turn,recover-daemon-start-claim,check-codex-native-health,reconcile-codex-queue-wake,inspect-held-refusal}"}
	}
	width := 78
	if n, err := strconv.Atoi(c.env("COLUMNS")); err == nil && n > 0 {
		width = n - 2
	}
	prefix := "usage: " + prog + " "
	indent := strings.Repeat(" ", len(prefix))
	line := prefix
	var text strings.Builder
	for _, group := range groups {
		separator := " "
		if line == prefix || line == indent {
			separator = ""
		}
		if len(line)+len(separator)+len(group) > width && line != prefix && line != indent {
			text.WriteString(line + "\n")
			line = indent
			separator = ""
		}
		line += separator + group
	}
	text.WriteString(line + "\n")
	return text.String()
}

type cliArgs struct {
	values   map[string][]string
	position []string
}

func (a cliArgs) get(k string) string {
	v := a.values[k]
	if len(v) == 0 {
		return ""
	}
	return v[len(v)-1]
}
func (a cliArgs) has(k string) bool { _, ok := a.values[k]; return ok }

func optionalCLI(arg string) bool {
	if !strings.HasPrefix(arg, "-") || arg == "-" || strings.Contains(arg, " ") {
		return false
	}
	// argparse's negative-number exception accepts integers and decimals, but
	// neither exponent notation nor a decimal point with no fractional digits.
	negative := []rune(strings.TrimPrefix(arg, "-"))
	dot, digits := false, 0
	for i, r := range negative {
		if r == '.' && !dot && i < len(negative)-1 {
			dot = true
			continue
		}
		if !unicode.IsDigit(r) {
			return true
		}
		digits++
	}
	return digits == 0
}

func optionCLI(arg string, spec map[string]int) bool {
	key, _, _ := strings.Cut(arg, "=")
	if strings.HasPrefix(key, "-") && key != "-" {
		for name := range spec {
			if strings.HasPrefix(name, key) {
				return true
			}
		}
	}
	return optionalCLI(arg)
}

// parseCLI retains repeatable --record, option=value, option abbreviation and
// options interspersed with positional arguments, as argparse accepts them.
func parseCLI(argv []string, prog string, spec map[string]int) (cliArgs, error) {
	specCopy := make(map[string]int, len(spec)+2)
	for key, n := range spec {
		specCopy[key] = n
	}
	specCopy["-h"] = 0
	specCopy["--help"] = 0
	spec = specCopy
	a := cliArgs{values: map[string][]string{}}
	options := true
	for i := 0; i < len(argv); i++ {
		arg := argv[i]
		if options && arg == "--" {
			options = false
			continue
		}
		if !options || !optionCLI(arg, spec) {
			a.position = append(a.position, arg)
			continue
		}
		key, eq, hasEq := strings.Cut(arg, "=")
		n, ok := spec[key]
		if !ok {
			matches := []string{}
			for name := range spec {
				if strings.HasPrefix(name, key) {
					matches = append(matches, name)
				}
			}
			sort.Strings(matches)
			if len(matches) == 1 {
				key = matches[0]
				n = spec[key]
				ok = true
			} else if len(matches) > 1 {
				return a, parseFail(prog, "ambiguous option: "+arg+" could match "+strings.Join(matches, ", "))
			}
		}
		if !ok {
			return a, parseFail(prog, "unrecognized arguments: "+strings.Join(argv[i:], " "))
		}
		if n == 0 {
			if hasEq {
				return a, parseFail(prog, "argument "+key+": ignored explicit argument '"+eq+"'")
			}
			if key == "-h" || key == "--help" {
				return a, &cliHelp{prog}
			}
			a.values[key] = []string{"true"}
			continue
		}
		vals := []string{}
		if hasEq {
			vals = append(vals, eq)
		}
		for len(vals) < n {
			if i+1 >= len(argv) || optionCLI(argv[i+1], spec) {
				suffix := "expected one argument"
				if n != 1 {
					suffix = fmt.Sprintf("expected %d arguments", n)
				}
				return a, parseFail(prog, "argument "+key+": "+suffix)
			}
			i++
			vals = append(vals, argv[i])
		}
		if key == "--record" {
			a.values[key] = append(a.values[key], vals...)
		} else {
			a.values[key] = vals
		}
	}
	return a, nil
}
func requireCLI(a cliArgs, prog string, keys ...string) error {
	missing := []string{}
	for _, k := range keys {
		if !a.has(k) {
			missing = append(missing, k)
		}
	}
	if len(missing) > 0 {
		return parseFail(prog, "the following arguments are required: "+strings.Join(missing, ", "))
	}
	return nil
}
func choiceCLI(prog, name, v string, choices []string) error {
	if nativeContains(choices, v) {
		return nil
	}
	return parseFail(prog, "argument "+name+": invalid choice: '"+v+"' (choose from "+strings.Join(choices, ", ")+")")
}

func cliCaught(err error) bool {
	var exit *exec.ExitError
	return journal.Classified(err) || sourceClass(err, "OSError", "ValueError", "JSONDecodeError", "UnicodeDecodeError", "UnicodeEncodeError", "KeyError", "TypeError") || errors.As(err, &exit)
}

func sourceReadCaught(err error) bool {
	return sourceOSError(err) || nativeContains([]string{"ValueError", "JSONDecodeError", "UnicodeDecodeError", "UnicodeEncodeError"}, sourceErrorName(err))
}

// RunCLI reports handled Source failures using its stdout/stderr and exit
// policy. Escaping exceptions and unavailable profiles return an error.
func (c *CLI) RunCLI(ctx context.Context, args []string, in io.Reader, out, errOut io.Writer) (int, error) {
	if in == nil {
		in = strings.NewReader("")
	}
	inbox := len(args) > 0 && args[0] == "inbox"
	var code int
	var err error
	if inbox {
		switch {
		case len(args) > 1 && args[1] == "drain":
			code, err = c.DrainCommand(ctx, args[2:], out)
		case len(args) > 1 && args[1] == "compact":
			code, err = c.CompactCommand(ctx, args[2:], out, errOut)
		default:
			code, err = c.InboxCommand(ctx, args[1:], in, out, errOut)
		}
	} else {
		code, err = c.runRecovery(ctx, args, out)
	}
	if err == nil {
		return code, nil
	}
	var help *cliHelp
	if errors.As(err, &help) {
		_, e := io.WriteString(out, c.usage(help.prog)+c.help(help.prog))
		return 0, e
	}
	var parse *cliParseError
	if errors.As(err, &parse) {
		_, e := fmt.Fprintf(errOut, "%s%s: error: %s\n", c.usage(parse.prog), parse.prog, parse.message)
		return 2, e
	}
	var coordination *CoordinationError
	if inbox {
		status, message := "", ""
		if errors.As(err, &coordination) {
			status = coordination.Code
			message = err.Error()
			code = 7
			if status == "INVALID_REQUEST" {
				code = 2
			}
		} else if cliCaught(err) {
			status = "INVALID_HOST"
			message = "INVALID_HOST: " + sourceErrorName(err) + " reading the native inbox context"
			code = 7
		} else {
			return 0, err
		}
		return code, c.output(out, nativeMap("schema", "cairn.response/1", "ok", false, "status", status, "message", message), false)
	}
	if errors.As(err, &coordination) {
		suffix := "session presence unavailable"
		code = 1
		if coordination.Code == "NATIVE_PROMPT_REFUSED" {
			suffix = "channel text rejected, owner prompt continues"
			code = 2
		}
		_, e := fmt.Fprintf(errOut, "Cairn coordination: %s; %s\n", err, suffix)
		return code, e
	}
	if cliCaught(err) {
		_, e := fmt.Fprintf(errOut, "Cairn coordination: %s; session presence unavailable\n", sourceErrorName(err))
		return 1, e
	}
	return 0, err
}

func (c *CLI) runRecovery(ctx context.Context, argv []string, out io.Writer) (int, error) {
	const prog = "coordination.py"
	spec := map[string]int{"--config": 1, "--event": 1, "--config-dir": 1, "--once": 0, "--native-session-id": 1, "--attempt-id": 1, "--native-turn-id": 1, "--delivery-id": 1, "--queued-submission-id": 1, "--outcome": 1, "--record": 1, "--enqueue-record": 1, "--dequeue-record": 1, "--refusal-record": 1, "--start-record": 1, "--context-record": 1, "--end-record": 1, "--check-only": 0}
	a, err := parseCLI(argv, prog, spec)
	if err != nil {
		return 0, err
	}
	if len(a.position) == 0 {
		return 0, parseFail(prog, "the following arguments are required: mode")
	}
	if len(a.position) > 1 {
		return 0, parseFail(prog, "unrecognized arguments: "+strings.Join(a.position[1:], " "))
	}
	mode := a.position[0]
	modes := []string{"hook", "watch", "recover-refused-wake", "recover-native-refused-wake", "recover-codex-refused-wake", "recover-codex-ended-turn", "recover-daemon-start-claim", "check-codex-native-health", "reconcile-codex-queue-wake", "inspect-held-refusal"}
	if err = choiceCLI(prog, "mode", mode, modes); err != nil {
		return 0, err
	}
	if mode == "hook" || mode == "watch" {
		return 0, &value.ProfileUnavailable{}
	}
	if a.has("--outcome") {
		if err = choiceCLI(prog, "--outcome", a.get("--outcome"), cliOutcomes); err != nil {
			return 0, err
		}
	}
	required := []string{"--config", "--native-session-id"}
	message := mode + " requires --config and --native-session-id"
	switch mode {
	case "reconcile-codex-queue-wake":
		required = append(required, "--delivery-id", "--queued-submission-id")
		message = mode + " requires --config, --native-session-id, --delivery-id and --queued-submission-id"
	case "recover-daemon-start-claim":
		required = append(required, "--attempt-id")
		message = mode + " requires --config, --native-session-id and --attempt-id"
	case "recover-codex-ended-turn":
		required = append(required, "--attempt-id", "--start-record", "--end-record")
		message = mode + " requires --config, --native-session-id, --attempt-id, --start-record and --end-record"
	case "recover-codex-refused-wake":
		required = append(required, "--attempt-id", "--start-record", "--context-record", "--end-record")
		message = mode + " requires --config, --native-session-id, --attempt-id and three record locators"
	case "recover-native-refused-wake":
		required = append(required, "--attempt-id", "--enqueue-record", "--dequeue-record", "--refusal-record")
		message = mode + " requires --config, --native-session-id, --attempt-id and three record locators"
	case "recover-refused-wake":
		required = append(required, "--attempt-id", "--native-turn-id")
		message = mode + " requires --config, --native-session-id, --attempt-id and --native-turn-id"
	}
	for _, key := range required {
		if !a.has(key) {
			return 0, parseFail(prog, message)
		}
	}
	locators := []string{}
	switch mode {
	case "reconcile-codex-queue-wake":
		locators = a.values["--record"]
	case "recover-codex-ended-turn":
		locators = []string{a.get("--start-record"), a.get("--end-record")}
	case "recover-codex-refused-wake":
		locators = []string{a.get("--start-record"), a.get("--context-record"), a.get("--end-record")}
	case "recover-native-refused-wake":
		locators = []string{a.get("--enqueue-record"), a.get("--dequeue-record"), a.get("--refusal-record")}
	}
	var records *value.Value
	if len(locators) > 0 {
		records = &value.Value{Kind: value.Array}
		for _, text := range locators {
			locator, e := ParseLocator(text)
			if e != nil {
				return 0, e
			}
			records.Items = append(records.Items, locator)
		}
	}
	config, err := LoadConfig(c.Host, a.get("--config"))
	if err != nil {
		return 0, err
	}
	r := RecoveryRequest{NativeSessionID: a.get("--native-session-id"), AttemptID: a.get("--attempt-id"), NativeTurnID: a.get("--native-turn-id"), DeliveryID: a.get("--delivery-id"), QueuedSubmissionID: a.get("--queued-submission-id"), Records: records, CheckOnly: a.has("--check-only")}
	if a.has("--outcome") {
		r.Outcome = value.Text(a.get("--outcome"))
	}
	e := c.engine()
	var result *value.Value
	switch mode {
	case "check-codex-native-health":
		result, err = e.CheckCodexNativeHealth(ctx, config, r.NativeSessionID)
	case "reconcile-codex-queue-wake":
		result, err = e.ReconcileCodexQueueWake(ctx, config, r)
	case "inspect-held-refusal":
		result, err = e.InspectHeldRefusal(ctx, config, r)
	case "recover-daemon-start-claim":
		result, err = e.RecoverDaemonStartClaim(ctx, config, r)
	case "recover-codex-ended-turn":
		result, err = e.RecoverCodexEndedTurn(ctx, config, r)
	case "recover-codex-refused-wake":
		result, err = e.RecoverCodexRefusedWake(ctx, config, r)
	case "recover-native-refused-wake":
		config = nativeClone(config)
		config.Set("config_path", value.Text(purePosixPath(a.get("--config"))))
		result, err = e.RecoverNativeRefusedWake(ctx, config, r)
	case "recover-refused-wake":
		result, err = e.RecoverRefusedWake(ctx, config, r)
	}
	if err != nil {
		return 0, err
	}
	return 0, c.output(out, result, true)
}
