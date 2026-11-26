package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"io"
	"os"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

// offlineCommandEnvironment makes any store or API connection fail, so a help
// path that passes proves it never needed one.
func offlineCommandEnvironment(t *testing.T) {
	t.Helper()
	t.Setenv("CAIRN_HOME", t.TempDir())
	t.Setenv("CAIRN_DATABASE_URL", "host=/absent-help-db dbname=denied")
}

func TestFlagParsedCommandsRenderUsageForHelp(t *testing.T) {
	offlineCommandEnvironment(t)
	for name, flagLine := range map[string]string{"serve": "-semantic-rust-config", "opencode-install": "-config-dir"} {
		for _, spelling := range []string{"--help", "-h", "-help"} {
			result, err := run(context.Background(), []string{name, spelling}, unreadHelpInput{})
			text, ok := result.(commandHelp)
			if err != nil || !ok {
				t.Fatalf("%s %s: %T %v, %v", name, spelling, result, result, err)
			}
			if !strings.HasPrefix(string(text), "Usage: cairn "+name+" ") || !strings.Contains(string(text), flagLine) {
				t.Fatalf("%s %s rendered no usable flag list: %s", name, spelling, text)
			}
		}
	}
}

func TestFlagHelpDoesNotHideRealParseErrors(t *testing.T) {
	offlineCommandEnvironment(t)
	for _, args := range [][]string{
		{"serve", "--unknown-flag"},
		{"serve", "--semantic-idle-timeout", "soon"},
		{"serve", "extra"},
		{"opencode-install", "--unknown-flag"},
		{"opencode-install", "--tokens", "many"},
		// A help spelling consumed as a flag's value is a value, not a request.
		{"opencode-install", "--socket", "--help"},
		{"opencode-install", "--", "--help"},
	} {
		result, err := run(context.Background(), args, unreadHelpInput{})
		if _, help := result.(commandHelp); help || err == nil || flagHelpRequested(err) {
			t.Fatalf("%q became help or success: %T %v, %v", args, result, result, err)
		}
		envelope, code := commandEnvelope(nil, err)
		if code == 0 || envelope.Status == "" {
			t.Fatalf("%q lost its failure: %+v exit %d", args, envelope, code)
		}
	}
}

// The usage request of an earlier parse must not turn a later, unrelated
// refusal into help, and a help-shaped error without a flag set stays an error.
func TestFlagHelpRequiresThisRunsOwnUsageRequest(t *testing.T) {
	offlineCommandEnvironment(t)
	f := flags("stale")
	if err := f.Parse([]string{"--help"}); err != flag.ErrHelp {
		t.Fatal(err)
	}
	if usageRequested.Load() != f {
		t.Fatal("flags() did not record the usage request")
	}
	result, err := run(context.Background(), []string{"serve", "extra"}, unreadHelpInput{})
	if _, help := result.(commandHelp); help || core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("stale usage request became help: %v, %v", result, err)
	}
	if usageRequested.Load() != nil {
		t.Fatal("run kept an earlier usage request")
	}
	if !flagHelpRequested(invalid(flag.ErrHelp.Error())) || !flagHelpRequested(flag.ErrHelp) || flagHelpRequested(invalid("flag provided but not defined: -x")) || flagHelpRequested(&core.Error{Code: "AUTHORITY_DENIED", Message: flag.ErrHelp.Error()}) {
		t.Fatal("help request classification changed")
	}
}

// Store-backed commands parse their flags only after the store opens, so this
// needs the integration database.
func TestStoreCommandFlagHelpRendersUsage(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	t.Setenv("CAIRN_DATABASE_URL", dsn)
	for name, flagLine := range map[string]string{"conflicts": "-include-resolved", "list": "-offset", "use-report": "-days", "runs": "-policy-rev"} {
		result, err := run(context.Background(), []string{name, "--help"}, unreadHelpInput{})
		text, ok := result.(commandHelp)
		if err != nil || !ok || !strings.Contains(string(text), flagLine) {
			t.Fatalf("%s --help: %T %v, %v", name, result, result, err)
		}
		if _, err := run(context.Background(), []string{name, "--unknown-flag"}, unreadHelpInput{}); core.Code(err) != "INVALID_REQUEST" || flagHelpRequested(err) {
			t.Fatalf("%s lost its parse refusal: %v", name, err)
		}
	}
}

// Every flag serve defines is named in the top-level synopsis.
func TestServeSynopsisNamesEveryServeFlag(t *testing.T) {
	offlineCommandEnvironment(t)
	result, err := run(context.Background(), []string{"serve", "--help"}, unreadHelpInput{})
	if err != nil {
		t.Fatal(err)
	}
	var synopsis string
	for _, line := range strings.Split(help, "\n") {
		if strings.Contains(line, "| serve [") {
			synopsis = line[strings.Index(line, "| serve ["):]
		}
	}
	names := regexp.MustCompile(`(?m)^  -([a-z0-9-]+)`).FindAllStringSubmatch(string(result.(commandHelp)), -1)
	if synopsis == "" || len(names) < 10 {
		t.Fatalf("no serve synopsis or flag list: %q %v", synopsis, names)
	}
	for _, name := range names {
		if !regexp.MustCompile(`--` + regexp.QuoteMeta(name[1]) + `[ \]|)]`).MatchString(synopsis) {
			t.Errorf("serve synopsis omits --%s: %s", name[1], synopsis)
		}
	}
}

func clientsLimitLine(t *testing.T, text commandHelp) string {
	t.Helper()
	lines := strings.Split(string(text), "\n")
	for i, line := range lines {
		if strings.HasPrefix(strings.TrimSpace(line), "-limit ") && i+1 < len(lines) {
			return strings.TrimSpace(lines[i+1])
		}
	}
	t.Fatalf("no --limit option in help: %s", text)
	return ""
}

func TestClientsHelpStatesTheLimitTheParserEnforces(t *testing.T) {
	offlineCommandEnvironment(t)
	catalog, err := clientsOperationHelp()
	if err != nil {
		t.Fatal(err)
	}
	want := clientsLimitLine(t, catalog)
	bounds := regexp.MustCompile(`\d+`).FindAllString(want, -1)
	if len(bounds) != 3 || bounds[0] != "1" || bounds[1] != strconv.Itoa(localapi.ClientsMaxLimit) || bounds[2] != strconv.Itoa(localapi.ClientsDefaultLimit) {
		t.Fatalf("help bounds %v differ from 1, %d, default %d: %s", bounds, localapi.ClientsMaxLimit, localapi.ClientsDefaultLimit, want)
	}
	for _, args := range [][]string{{"clients", "--help"}, {"agent", "clients", "--help"}} {
		result, err := run(context.Background(), args, unreadHelpInput{})
		if err != nil {
			t.Fatalf("%v: %v", args, err)
		}
		if got := clientsLimitLine(t, result.(commandHelp)); got != want {
			t.Fatalf("%v states %q, catalog help states %q", args, got, want)
		}
	}
	// The stated maximum passes argument checks (the absent API refuses it
	// later); one more is refused as an argument.
	socket := t.TempDir() + "/absent.sock"
	token := t.TempDir() + "/token"
	if err := os.WriteFile(token, []byte("fixture-token\n"), 0600); err != nil {
		t.Fatal(err)
	}
	base := []string{"clients", "--socket", socket, "--token-file", token, "--limit"}
	if _, err := run(context.Background(), append(base[:len(base):len(base)], bounds[1]), strings.NewReader("")); core.Code(err) == "INVALID_REQUEST" || err == nil {
		t.Fatalf("stated maximum refused as an argument: %v", err)
	}
	if _, err := run(context.Background(), append(base[:len(base):len(base)], strconv.Itoa(localapi.ClientsMaxLimit+1)), strings.NewReader("")); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("limit above the stated maximum accepted: %v", err)
	}
}

func TestRecallObservationHelpExampleValidates(t *testing.T) {
	decode := func(example string) core.RecallObservationRequest {
		t.Helper()
		var request core.RecallObservationRequest
		decoder := json.NewDecoder(strings.NewReader(example))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&request); err != nil {
			t.Fatal(err)
		}
		return request
	}
	example := agentJSONHelp["recall-observation"].Example
	// Placeholders stand for caller values; everything else is checked as written.
	example = strings.NewReplacer("NEW_UUID", uuid.NewString(), "RECEIPT_UUID", uuid.NewString()).Replace(example)
	request := decode(example)
	if len(request.SelectorCalls) == 0 {
		t.Fatal("example no longer shows a selector call")
	}
	if err := request.Validate(); err != nil {
		t.Fatalf("recall-observation help example is refused: %v\n%s", err, example)
	}
	// Guard the guard: the validation used here refuses an unknown stage.
	request.SelectorCalls[0].Stage = "select"
	if err := request.Validate(); core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "selector stage") {
		t.Fatalf("validation accepted an unknown selector stage: %v", err)
	}
}

func TestHarnessConfigForwardsOnlyAnExplicitClientHarness(t *testing.T) {
	parse := func(args []string) *mcpOptions {
		t.Helper()
		f := flags("mcp")
		o := mcpFlags(f)
		if err := f.Parse(args); err != nil {
			t.Fatal(err)
		}
		if err := o.validate(f.NArg()); err != nil {
			t.Fatal(err)
		}
		return o
	}
	base := []string{"--socket", "/s", "--token-file", "/t", "--repo", "/r", "--task", "t", "--run", "r"}
	for _, harness := range []string{"codex", "claude", "opencode", "hermes", "agy", "other"} {
		command, err := parse(append(base[:len(base):len(base)], "--client-harness", harness)).command("/cairn")
		if err != nil {
			t.Fatal(err)
		}
		// The rendered launch, parsed as cairn mcp parses it, declares the label.
		if got := parse(command[2:]).declaration().Harness; got != harness {
			t.Fatalf("rendered command %q declares harness %q, want %q", command, got, harness)
		}
	}
	command, err := parse(base).command("/cairn")
	if err != nil {
		t.Fatal(err)
	}
	if got := parse(command[2:]).declaration().Harness; got != "unknown" || strings.Contains(strings.Join(command, " "), "client-harness") {
		t.Fatalf("an unconfigured harness was supplied: %q declares %q", command, got)
	}
	for _, generator := range []struct {
		name, own string
		write     func(io.Writer, []string, string) error
		scope     []string
	}{
		{"claude-config", "claude", writeClaudeConfig, []string{"--task", "t", "--run", "r"}},
		{"codex-config", "codex", writeCodexConfig, []string{"--codex-thread"}},
		{"opencode-config", "opencode", writeOpenCodeConfig, []string{"--task", "t", "--run", "r"}},
	} {
		args := append([]string{"--socket", "/s", "--token-file", "/t", "--repo", "/r"}, generator.scope...)
		var labelled, plain bytes.Buffer
		if err := generator.write(&labelled, append(args[:len(args):len(args)], "--client-harness", "other"), "/cairn"); err != nil {
			t.Fatalf("%s: %v", generator.name, err)
		}
		if !regexp.MustCompile(`"--client-harness",\s*"other"`).Match(labelled.Bytes()) {
			t.Fatalf("%s dropped the configured harness label: %s", generator.name, labelled.String())
		}
		if err := generator.write(&plain, args, "/cairn"); err != nil {
			t.Fatalf("%s: %v", generator.name, err)
		}
		if strings.Contains(plain.String(), "client-harness") {
			t.Fatalf("%s inferred a harness label: %s", generator.name, plain.String())
		}
		var refused bytes.Buffer
		if err := generator.write(&refused, append(args[:len(args):len(args)], "--client-harness", "robot"), "/cairn"); core.Code(err) != "INVALID_REQUEST" || refused.Len() != 0 {
			t.Fatalf("%s accepted an unknown harness label: %v %s", generator.name, err, refused.String())
		}
	}
}
