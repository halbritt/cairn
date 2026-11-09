package coordination

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// All routes below are explicit synthetic kernel fixtures, not production
// profile/domain selection or isolated-launch qualification.
type stage2FileRoutes struct{}

func (stage2FileRoutes) CheckDefinition(d value.SourceProfileDefinition, sites []string) error {
	for _, s := range sites {
		if _, ok := d.Readers[s]; !ok {
			if _, ok = d.Writers[s]; !ok {
				return &value.ProfileUnavailable{}
			}
		}
	}
	return nil
}
func (stage2FileRoutes) Decode(_ string, raw []byte, route value.ReaderRoute, n int, b func([]byte, int) (*value.Value, error), s func([]byte, string, int) (*value.Value, error)) (*value.Value, error) {
	if route.Input == "bytes-json" {
		return b(raw, n)
	}
	return s(raw, route.Codec.Name, n)
}
func (stage2FileRoutes) Write(_ string, w io.Writer, v *value.Value, r value.WriterRoute, n int, k func(io.Writer, *value.Value, value.WriterRoute, int) error) error {
	return k(w, v, r, n)
}
func (stage2FileRoutes) Filesystem() value.FilesystemRoutes { return stage2FileSystem{} }

type stage2FileSystem struct{}

func (stage2FileSystem) Encode(points []rune) (string, error) {
	var b strings.Builder
	for _, r := range points {
		if r >= 0xdc80 && r <= 0xdcff {
			b.WriteByte(byte(r - 0xdc00))
		} else if !utf8.ValidRune(r) {
			return "", &value.Error{Class: "UnicodeEncodeError"}
		} else {
			b.WriteRune(r)
		}
	}
	return b.String(), nil
}
func (stage2FileSystem) Decode(s string) ([]rune, error) {
	var out []rune
	for len(s) > 0 {
		r, n := utf8.DecodeRuneInString(s)
		if r == utf8.RuneError && n == 1 {
			r = 0xdc00 + rune(s[0])
		}
		out = append(out, r)
		s = s[n:]
	}
	return out, nil
}
func (f stage2FileSystem) Resolve(v *value.Value) ([]rune, error) {
	s, e := f.Encode(v.String)
	if e != nil {
		return nil, e
	}
	s, e = filepath.Abs(s)
	if e != nil {
		return nil, e
	}
	return f.Decode(s)
}
func (f stage2FileSystem) Exists(p []rune) (bool, error) {
	s, e := f.Encode(p)
	if e != nil {
		return false, e
	}
	_, e = os.Stat(s)
	if errors.Is(e, os.ErrNotExist) {
		return false, nil
	}
	return e == nil, e
}
func (f stage2FileSystem) IsDir(p []rune) (bool, error) {
	s, e := f.Encode(p)
	if e != nil {
		return false, e
	}
	st, e := os.Stat(s)
	if errors.Is(e, os.ErrNotExist) {
		return false, nil
	}
	return e == nil && st.IsDir(), e
}
func (stage2FileSystem) Home() ([]rune, error) { return nil, &value.ProfileUnavailable{} }
func stage2FileProfile() *value.SourceReaderProfile {
	n := 0
	yes, no := true, false
	codec := &value.Codec{Name: "utf-8", Errors: "strict"}
	return &value.SourceReaderProfile{Definition: value.SourceProfileDefinition{Schema: "cairn.source-reader-profile/1", State: "definition", ReadIntegerDigits: &n, WriteIntegerDigits: &n, Filesystem: &value.Codec{Name: "utf-8", Errors: "surrogateescape"}, Readers: map[string]value.ReaderRoute{SiteSessionRead: {Input: "text-json", Codec: codec, Domain: "synthetic"}, SiteCallResponse: {Input: "text-json", Codec: codec, Domain: "synthetic"}}, Writers: map[string]value.WriterRoute{SiteSessionWrite: {Route: "dump", Codec: codec, EnsureASCII: &no, Compact: &no, TrailingLF: &yes, Domain: "synthetic"}, SiteCallRequest: {Route: "dumps", Codec: codec, EnsureASCII: &yes, Compact: &no, TrailingLF: &no, Domain: "synthetic"}}}, Routes: stage2FileRoutes{}}
}

func TestStage2FileHostLoadPersistLock(t *testing.T) {
	h := NewFileHost(FileHostOptions{Profile: stage2FileProfile(), Log: func(string) error { return nil }})
	config := nativeMap("binding", "test", "state_dir", t.TempDir())
	s, release, err := OpenSession(config, stage2Agent, h, true)
	if err != nil {
		t.Fatal(err)
	}
	s.Record = stage2State()
	if err = s.Save(); err != nil {
		t.Fatal(err)
	}
	_, _, err = OpenSession(config, stage2Agent, h, false)
	stage2Code(t, err, "SESSION_BUSY")
	if err = release(); err != nil {
		t.Fatal(err)
	}
	s, release, err = OpenSession(config, stage2Agent, h, false)
	if err != nil {
		t.Fatal(err)
	}
	defer release()
	if !reflect.DeepEqual(s.Record, stage2State()) {
		t.Fatal("file roundtrip changed ordered values", s.Record)
	}
	st, err := os.Stat(s.Path)
	if err != nil || st.Mode().Perm() != 0600 {
		t.Fatal(st, err)
	}
	bytes, err := os.ReadFile(s.Path)
	if err != nil || !strings.HasSuffix(string(bytes), "\n") {
		t.Fatal(string(bytes), err)
	}
	if h.Journal.(*SessionJournalAdapter).Projection != nil {
		t.Fatal("test must use concrete default projection")
	}
}

func TestStage2FileHostFailureBoundaries(t *testing.T) {
	for _, kind := range []string{"write", "file_sync", "file_close", "rename", "directory_sync", "encoding"} {
		t.Run(kind, func(t *testing.T) {
			dir := t.TempDir()
			target := filepath.Join(dir, "state.json")
			old := []byte("{\"old\":true}\n")
			if err := os.WriteFile(target, old, 0600); err != nil {
				t.Fatal(err)
			}
			sentinel := &os.PathError{Op: kind, Path: "synthetic", Err: io.ErrUnexpectedEOF}
			options := FileHostOptions{Profile: stage2FileProfile()}
			switch kind {
			case "write":
				options.IO.Write = func(f *os.File, p []byte) (int, error) {
					if len(p) > 0 {
						_, _ = f.Write(p[:1])
					}
					return 1, sentinel
				}
			case "file_sync", "directory_sync":
				options.IO.Sync = func(f *os.File) error {
					st, err := f.Stat()
					if err != nil {
						return err
					}
					if st.IsDir() == (kind == "directory_sync") {
						return sentinel
					}
					return f.Sync()
				}
			case "file_close":
				options.IO.Close = func(f *os.File) error {
					err := f.Close()
					if !strings.Contains(f.Name(), "tmp") {
						return err
					}
					return sentinel
				}
			case "rename":
				options.IO.Rename = func(string, string) error { return sentinel }
			}
			next := nativeMap("next", true)
			if kind == "encoding" {
				next.Set("unpaired", &value.Value{Kind: value.String, String: []rune{0xd800}})
			}
			err := NewFileHost(options).Persist(target, next)
			if kind == "encoding" {
				if value.ErrorClass(err) != "UnicodeEncodeError" {
					t.Fatal(err)
				}
			} else if err != sentinel {
				t.Fatal(err)
			}
			raw, e := os.ReadFile(target)
			if e != nil {
				t.Fatal(e)
			}
			if kind == "directory_sync" {
				if string(raw) == string(old) {
					t.Fatal("post-rename error rolled back publication")
				}
			} else if string(raw) != string(old) {
				t.Fatal("failure before publication replaced the session", string(raw))
			}
			entries, e := os.ReadDir(dir)
			if e != nil || len(entries) != 1 || entries[0].Name() != "state.json" {
				t.Fatal("pending file leaked", entries, e)
			}
		})
	}
}

func TestStage2ConcreteSourceCall(t *testing.T) {
	config := nativeMap("cairn", "/unrunnable/fixture-cairn", "socket", "/synthetic/api.sock", "token_file", "/nonexistent/profile-selector")
	calls := 0
	runner := journal.RunnerFunc(func(ctx context.Context, argv []string, input string) (journal.Result, error) {
		calls++
		want := []string{"/unrunnable/fixture-cairn", "agent", "--socket", "/synthetic/api.sock", "--token-file", "/nonexistent/profile-selector", "--agent-id", stage2Agent, "--execution-id", stage2Execution, "session-inbox-ready"}
		if !reflect.DeepEqual(argv, want) {
			t.Fatal(argv, want)
		}
		if input != "{\"keep\": true}" {
			t.Fatal(input)
		}
		if deadline, ok := ctx.Deadline(); !ok || time.Until(deadline) > time.Second {
			t.Fatal("Source timeout not bounded")
		}
		return journal.Result{Stdout: `{"ok":true,"data":{"integer":9007199254740993,"float":1.0,"flag":true}}`}, nil
	})
	h := NewFileHost(FileHostOptions{Profile: stage2FileProfile(), Runner: runner})
	out, err := h.Call(context.Background(), config, "session-inbox-ready", nativeMap("keep", true), NativeCallOptions{Session: nativeMap("agent_id", stage2Agent, "execution_id", stage2Execution), Timeout: time.Second})
	if err != nil || out.Get("integer").Integer != "9007199254740993" || out.Get("float").Kind != value.Float || out.Get("flag").Kind != value.Bool || calls != 1 {
		t.Fatal(out, err, calls)
	}
	_, err = h.Call(context.Background(), config.Without("token_file"), "test", value.Mapping(), NativeCallOptions{})
	stage2Code(t, err, "INVALID_CONFIG")
	if calls != 1 {
		t.Fatal(calls)
	}
}
