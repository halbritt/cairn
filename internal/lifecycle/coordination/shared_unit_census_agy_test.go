package coordination

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// These are synthetic codec bytes, never native settings, registrations or
// session files. They exercise multiplicity and strict raw syscall decoding.
func agySyntheticCodec() ([]byte, WatchCensusAgyReadback, map[string][]byte, []agyCensusCommand) {
	var trace strings.Builder
	client := "/synthetic-codec/agy"
	fmt.Fprintf(&trace, "200 execve(%q, [%q], 0x0 /* 0 vars */) = 0\n", client, client)
	files := map[string][]byte{"/synthetic-codec/account/hooks.json": []byte("account bytes"), "/synthetic-codec/project/.agents/hooks.json": []byte("project bytes")}
	for _, path := range []string{"/synthetic-codec/account/hooks.json", "/synthetic-codec/project/.agents/hooks.json"} {
		fmt.Fprintf(&trace, "200 openat(AT_FDCWD, %q, O_RDONLY|O_CLOEXEC) = 8\n", path)
		fmt.Fprintf(&trace, "200 read(8, %q, 4096) = %d\n", files[path], len(files[path]))
		trace.WriteString("200 read(8, \"\", 4096) = 0\n200 close(8) = 0\n")
	}
	start := trace.Len()
	var commands []agyCensusCommand
	for i, event := range []string{"PreInvocation", "PreInvocation", "Stop", "Stop"} {
		payload, _ := json.Marshal(map[string]any{"conversationId": "synthetic-codec-session", "workspacePaths": []string{"/synthetic-codec/project"}, "hookEventName": event})
		fmt.Fprintf(&trace, "200 write(9, %q, %d) = %d\n", payload, len(payload), len(payload))
		argv := []string{"/synthetic-codec/cairn", "coordination", "--receipt", "/synthetic-codec/receipt", "--", "hook", "--config", "/synthetic-codec/profile", "--event", event}
		var quoted []string
		for _, arg := range argv {
			quoted = append(quoted, strconv.Quote(arg))
		}
		fmt.Fprintf(&trace, "%d execve(%q, [%s], 0x0 /* 0 vars */) = 0\n", 240+i, argv[0], strings.Join(quoted, ", "))
		fmt.Fprintf(&trace, "%d +++ exited with 0 +++\n", 240+i)
		commands = append(commands, agyCensusCommand{event: event, program: WatchCensusPin{Path: argv[0]}, argv: argv})
	}
	raw := []byte(trace.String())
	return raw, WatchCensusAgyReadback{NativePID: 200, NativeSession: "synthetic-codec-session", Client: WatchCensusPin{Path: client}, ClientArgv: []string{client}, Start: start, End: len(raw)}, files, commands
}

func TestAgyTraceCumulativeCodec(t *testing.T) {
	for _, mutation := range []string{"none", "missing-exec", "extra-exec", "dedup-command", "failed-exit", "invalid-pid", "wrong-layer", "partial-read", "wrong-session", "wrong-workspace", "extra-event", "truncated-exec", "missing-prefix", "missing-eof", "wrong-image", "unmatched-argv", "interval"} {
		t.Run(mutation, func(t *testing.T) {
			raw, observed, files, commands := agySyntheticCodec()
			switch mutation {
			case "missing-exec":
				raw = bytes.Replace(raw, []byte("240 execve("), []byte("240 ignored("), 1)
			case "extra-exec":
				raw = append(raw, []byte("250 execve(\"/synthetic-codec/foreign\", [\"/synthetic-codec/foreign\", \"coordination\"], 0x0) = 0\n250 +++ exited with 0 +++\n")...)
			case "dedup-command":
				commands = commands[1:]
			case "failed-exit":
				raw = bytes.Replace(raw, []byte("240 +++ exited with 0"), []byte("240 +++ exited with 1"), 1)
			case "invalid-pid":
				observed.NativePID = 1
			case "wrong-layer":
				raw = bytes.ReplaceAll(raw, []byte("/synthetic-codec/account/hooks.json"), []byte("/synthetic-codec/unknown/hooks.json"))
			case "partial-read":
				raw = bytes.Replace(raw, []byte("read(8, \"account bytes\", 4096) = 13"), []byte("read(8, \"account bytes\", 4096) = 12"), 1)
			case "wrong-session":
				observed.NativeSession = "other-session"
			case "wrong-workspace":
				raw = bytes.ReplaceAll(raw, []byte(`synthetic-codec/project\"]`), []byte(`synthetic-codec/foreign\"]`))
			case "extra-event":
				commands[0].event = "Unknown"
			case "truncated-exec":
				raw = bytes.Replace(raw, []byte(`"coordination", "--receipt"`), []byte(`"coordination", ...`), 1)
			case "missing-prefix":
				raw = append([]byte("299 <... read resumed>\"\", 1) = 0\n"), raw...)
			case "missing-eof":
				raw = bytes.Replace(raw, []byte("200 read(8, \"\", 4096) = 0\n"), nil, 1)
			case "wrong-image":
				observed.Client.Path = "/synthetic-codec/foreign"
			case "unmatched-argv":
				commands[0].argv = slices.Clone(commands[0].argv)
				commands[0].argv[3] = "/synthetic-codec/other"
			case "interval":
				observed.Start++
			}
			if mutation != "interval" {
				observed.Start = bytes.Index(raw, []byte("200 write("))
				observed.End = len(raw)
			}
			err := agyTraceReadback(raw, observed, "/synthetic-codec/project", files, commands)
			if mutation == "none" && err != nil {
				t.Fatal(err)
			}
			if mutation != "none" && err == nil {
				t.Fatal("invalid synthetic codec input was accepted")
			}
		})
	}
}

func TestR06AgyTypedCensusProcess(t *testing.T) {
	path := os.Getenv("CAIRN_R06_AGY_TYPED_FIXTURE")
	if path == "" {
		t.Skip("private genuine-producer Agy fixture not selected")
	}
	var fixture struct {
		Catalog, Selection, Readback WatchCensusPin
		Proposal                     WatchCensusProposal
		Root                         string
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := hookDecode(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	read := func(path string, dst any) {
		t.Helper()
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if err := hookDecode(raw, dst); err != nil {
			t.Fatal(err)
		}
	}
	for _, mutation := range []string{"none", "missing-inventory", "extra-inventory", "wrong-layer", "absent-present", "missing-layer", "mixed-settings", "unknown-tag", "strict-field", "preparation-native-claim", "stale-pin", "duplicate-selection"} {
		t.Run(mutation, func(t *testing.T) {
			directory, err := os.MkdirTemp(fixture.Root, "agy-unit-")
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = os.RemoveAll(directory) })
			var catalog WatchCensusCatalog
			var selected WatchCensusAgyHooks
			read(fixture.Catalog.Path, &catalog)
			read(fixture.Selection.Path, &selected)
			want := agyNativeUnknown
			switch mutation {
			case "missing-inventory":
				selected.LayerFiles = selected.LayerFiles[1:]
				want = "unknown or omitted Agy"
			case "extra-inventory":
				selected.LayerFiles = append(selected.LayerFiles, fixture.Readback)
				want = "extra Agy pin"
			case "wrong-layer":
				selected.Layers[0].Installed.Path = selected.Layers[1].Installed.Path
				want = "another layer"
			case "absent-present":
				selected.Layers[0].Installed.SHA256 = ""
				selected.Layers[0].Generation = WatchCensusPin{}
				selected.Layers[0].Settings = WatchCensusPin{}
				want = "declared absent Agy"
			case "missing-layer":
				selected.Layers = selected.Layers[:1]
				want = "exhaustive ordered"
			case "mixed-settings":
				selected.Layers[0].Settings = selected.Readback
				want = "generation membership"
			case "unknown-tag":
				selected.Schema = "cairn.watch-agy-hooks/unknown"
				want = "unsupported native harness"
			case "preparation-native-claim":
				selected.Readback = censusTestJSON(t, filepath.Join(directory, "readback.json"), WatchCensusAgyReadback{Schema: "cairn.watch-agy-readback/1", Harness: "agy", EvidenceKind: "code-preparation", NativePID: 2})
				want = "invented native evidence"
			case "stale-pin":
				selected.Layers[0].Generation.SHA256 = strings.Repeat("0", 64)
				want = "census pin changed"
			}
			pin := censusTestJSON(t, filepath.Join(directory, "selection.json"), selected)
			if mutation == "strict-field" {
				decoded := map[string]any{}
				read(pin.Path, &decoded)
				decoded["Authorized"] = true
				pin = censusTestJSON(t, pin.Path, decoded)
				want = "unknown field"
			}
			catalog.HookSelections = []WatchCensusPin{pin}
			if mutation == "duplicate-selection" {
				catalog.HookSelections = append(catalog.HookSelections, pin)
				want = "duplicate or external"
			}
			catalogPin := censusTestJSON(t, filepath.Join(directory, "catalog.json"), catalog)
			profile := stage2ServicesProfile()
			host := NewFileHost(FileHostOptions{Profile: profile})
			host.Call = func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error) {
				t.Fatal("typed census attempted API")
				return nil, nil
			}
			cli := &CLI{Host: host, Profile: profile}
			result, err := cli.InspectWatchUnitCensus(context.Background(), catalogPin, fixture.Proposal)
			if err == nil || !strings.Contains(err.Error(), want) || result.FixtureReady || result.ExternalCoverage != "unknown" || result.NativeRefresh != "unobserved" {
				t.Fatalf("want refusal %q; result=%+v err=%v", want, result, err)
			}
		})
	}
}

func TestAgyTraceStrictFrames(t *testing.T) {
	good := []byte("200 read(8, <unfinished ...>\n200 <... read resumed>\"\\x61\", 1) = 1\n")
	calls, err := agyTraceCalls(good)
	if err != nil || len(calls) != 1 {
		t.Fatal(calls, err)
	}
	raw, fd, err := agyTraceIO(calls[0])
	if err != nil || string(raw) != "a" || fd != 8 {
		t.Fatal(string(raw), fd, err)
	}
	for _, bad := range [][]byte{good[:len(good)-1], []byte("200 read(8, <unfinished ...>\n"), []byte("1 close(8) = 0\n"), []byte("strace: detached\n")} {
		if _, err := agyTraceCalls(bad); err == nil {
			t.Fatal("incomplete synthetic frame accepted")
		}
	}
}

func TestAgyTraceHistoricalDescriptorKeepsCurrentFieldsUnavailable(t *testing.T) {
	// Version 1 retains its historical/preparation shape. This is a synthetic
	// unit record, not a current or native readiness constructor.
	var old WatchCensusAgyReadback
	if err := hookDecode([]byte(`{"Schema":"cairn.watch-agy-readback/1","Harness":"agy","EvidenceKind":"code-preparation"}`), &old); err != nil {
		t.Fatal(err)
	}
	for _, extra := range []string{`"Before":{}`, `"Generation":{}`, `"Authorized":true`} {
		raw := []byte(`{"Schema":"cairn.watch-agy-readback/1","Harness":"agy","EvidenceKind":"code-preparation",` + extra + `}`)
		if err := hookDecode(raw, &old); err == nil {
			t.Fatal("current or authority field leaked into historical descriptor")
		}
	}
}

// Optional real retained bytes test decoding only. The extractor records the
// original whole-trace pin and exact byte offsets; no current native claim.
func TestAgyRetainedFrameCodec(t *testing.T) {
	path := os.Getenv("CAIRN_R06_AGY_RETAINED_FRAMES")
	if path == "" {
		t.Skip("explicit retained codec source not selected")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Schema string
		Source WatchCensusPin
		Frames []struct {
			Offset int
			SHA256 string
			Raw    string
		}
	}
	if err := hookDecode(raw, &corpus); err != nil {
		t.Fatal(err)
	}
	if corpus.Schema != "cairn.agy-retained-frame-codec/1" || !filepath.IsAbs(corpus.Source.Path) || !censusDigest(corpus.Source.SHA256) || len(corpus.Frames) < 3 {
		t.Fatal("missing retained source provenance")
	}
	source, err := os.ReadFile(corpus.Source.Path)
	if err != nil {
		t.Fatal(err)
	}
	if agyTraceSHA(source) != corpus.Source.SHA256 {
		t.Fatal("retained source changed")
	}
	for _, frame := range corpus.Frames {
		if frame.Offset < 0 || frame.Offset+len(frame.Raw) > len(source) || string(source[frame.Offset:frame.Offset+len(frame.Raw)]) != frame.Raw || agyTraceSHA([]byte(frame.Raw)) != frame.SHA256 {
			t.Fatal("retained frame provenance changed")
		}
		calls, err := agyTraceCalls([]byte(frame.Raw))
		if err != nil || len(calls) != 1 {
			t.Fatal(err)
		}
		switch calls[0].name {
		case "execve":
			if _, _, err := agyExec(calls[0]); err != nil {
				t.Fatal(err)
			}
		case "write", "read":
			if _, _, err := agyTraceIO(calls[0]); err != nil {
				t.Fatal(err)
			}
		default:
			t.Fatal("unexpected retained codec frame")
		}
	}
}
