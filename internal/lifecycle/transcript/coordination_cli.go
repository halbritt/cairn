package transcript

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"os/user"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/coordination"
	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// CoordinationCLIOptions configures the private coordination CLI constructor
// used by cmd/cairn-lifecycle (and cmd/cairn's gated lifecycle plan).
type CoordinationCLIOptions struct {
	WorkDir             string
	Root                string
	ProcRoot            string
	Executable          string
	Command             []string
	UID                 *uint32
	Now                 func() time.Time
	Env                 func(string) string
	LookupEnv           func(string) (string, bool)
	ErrOut              io.Writer
	Compatibility       *coordination.CompatibilityContext
	CompatibilityVector []string
}

// ExecuteCoordinationCLI constructs and runs the coordination CLI with the
// given arguments, returning its exit code and any escaping error.
func ExecuteCoordinationCLI(ctx context.Context, args []string, input io.Reader, out, errOut io.Writer) (int, error) {
	return ExecuteCoordinationCLIWithOptions(ctx, CoordinationCLIOptions{ErrOut: errOut}, args, input, out, errOut)
}

// ExecuteCoordinationCLIWithOptions runs the coordination CLI with explicit
// options. When invoked with a leading /3 --receipt flag, it establishes and
// verifies the full CompatibilityContext (including CompatibilityVector) before
// running the target tail.
func ExecuteCoordinationCLIWithOptions(ctx context.Context, opts CoordinationCLIOptions, args []string, input io.Reader, out, errOut io.Writer) (int, error) {
	if opts.ErrOut == nil {
		opts.ErrOut = errOut
	}
	if opts.Compatibility == nil && isCompatibilitySelectionArgs(args) {
		workDir := opts.WorkDir
		if workDir == "" {
			workDir, _ = os.Getwd()
		}
		prof := NewCoordinationProfile(workDir, opts.Env, opts.LookupEnv)
		compat, tail, err := coordination.ParseCompatibilityCommand(args, opts.CompatibilityVector, prof)
		if err != nil || compat.Role != "coordination" {
			prefix := compatibilityRefusalPrefix(args)
			if errOut != nil {
				_, _ = io.WriteString(errOut, prefix+" diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n")
			}
			return 1, nil
		}
		opts.Compatibility = compat
		args = tail
	}
	cli, err := NewCoordinationCLI(opts)
	if err != nil {
		return 1, err
	}
	return cli.RunCLI(ctx, args, input, out, errOut)
}

func isCompatibilitySelectionArgs(args []string) bool {
	return len(args) > 0 && (args[0] == "--receipt" || strings.HasPrefix(args[0], "--receipt="))
}

func compatibilityRefusalPrefix(args []string) string {
	for i := 0; i < len(args); i++ {
		if args[i] == "--role" {
			if i+1 < len(args) && args[i+1] == "coordination" {
				return "Cairn coordination"
			}
			return "Cairn lifecycle"
		}
	}
	return "Cairn lifecycle"
}

// RunCoordinationProcess runs ExecuteCoordinationCLI and translates any
// escaping error into a non-zero process exit code and stderr diagnostic.
func RunCoordinationProcess(ctx context.Context, args []string, input io.Reader, out, errOut io.Writer) int {
	code, err := ExecuteCoordinationCLI(ctx, args, input, out, errOut)
	if err != nil {
		if errOut != nil {
			_, _ = fmt.Fprintf(errOut, "Cairn coordination: %v\n", err)
		}
		if code != 0 {
			return code
		}
		return 1
	}
	return code
}

// NewCoordinationCLI builds a coordination.CLI backed by the production-grade
// SourceReaderProfile, FileHost, and transcript RecoveryEngine. When
// options.Command is empty, CLI.Command defaults to [executable, "coordination"].
func NewCoordinationCLI(options CoordinationCLIOptions) (*coordination.CLI, error) {
	workDir := options.WorkDir
	if workDir == "" {
		var err error
		workDir, err = os.Getwd()
		if err != nil {
			return nil, err
		}
	}
	procRoot := options.ProcRoot
	if procRoot == "" {
		procRoot = "/proc"
	}
	uid := uint32(os.Getuid())
	if options.UID != nil {
		uid = *options.UID
	}
	clock := options.Now
	if clock == nil {
		clock = time.Now
	}
	executable := options.Executable
	if executable == "" {
		var err error
		executable, err = os.Executable()
		if err != nil {
			return nil, err
		}
	}
	errOut := options.ErrOut
	if errOut == nil {
		errOut = os.Stderr
	}
	cmd := options.Command
	if len(cmd) == 0 {
		cmd = []string{executable, "coordination"}
	}
	profile := NewCoordinationProfile(workDir, options.Env, options.LookupEnv)
	services := &coordination.ServiceBindings{
		Profile: profile,
		MemoryStateDir: func(config *value.Value) (*value.Value, error) {
			return ResolveBoundMemoryStateDir(profile, config, uid)
		},
		UID: &uid,
		Now: clock,
	}
	logFn := func(message string) error {
		_, err := io.WriteString(errOut, message+"\n")
		return err
	}
	host := coordination.NewFileHost(coordination.FileHostOptions{
		Services:      services,
		Profile:       profile,
		Compatibility: options.Compatibility,
		Now:           clock,
		Log:           logFn,
	})
	reader := NewReader(profile)
	reader.ProcRoot = procRoot
	reader.ExpectedUID = uid
	reader.Now = clock
	engine := reader.BindRecovery(host, coordination.RecoveryHooks{})
	return &coordination.CLI{
		Host:          host,
		Engine:        engine,
		Profile:       profile,
		Compatibility: options.Compatibility,
		Command:       cmd,
		Now:           clock,
		Root:          options.Root,
		ProcRoot:      procRoot,
		UID:           &uid,
		Env:           options.Env,
		LookupEnv:     options.LookupEnv,
		ReadInput:     coordination.ReadUTF8Input,
	}, nil
}

// NewCoordinationProfile returns the SourceReaderProfile for coordination and
// transcript JSON/filesystem sites.
func NewCoordinationProfile(workDir string, envFn func(string) string, lookupEnvFn func(string) (string, bool)) *value.SourceReaderProfile {
	// Interpreter-specific integer digit limits are Class C; Go's reader uses
	// an explicit unlimited integer setting (digits = 0) with no inferred
	// originating interpreter profile or calibrated limit (98d46758 item 2).
	digits := 0
	yes, no := true, false
	utf8Strict := &value.Codec{Name: "utf-8", Errors: "strict"}
	domain := "cairn-lifecycle-coordination"
	readers := map[string]value.ReaderRoute{
		SiteLastJSONLRecord:  {Input: "bytes-json", Domain: domain},
		SiteDaemonStartClaim: {Input: "bytes-json", Domain: domain},
		SiteStrictNativeLine: {Input: "text-json", Codec: utf8Strict, Domain: domain},
		SiteFindCodexRefusal: {Input: "text-json", Codec: utf8Strict, Domain: domain},
	}
	for _, site := range []string{
		coordination.SiteSessionRead,
		coordination.SiteCallResponse,
		coordination.SiteConfigRead,
		coordination.SiteDrainStateRead,
		coordination.SiteInboxContextRead,
		coordination.SiteCompactIndexRead,
		coordination.SiteCompactStateRead,
		coordination.SiteCompactContextRead,
		coordination.SiteCompactJournalRead,
		coordination.SiteCompactReplyRead,
		coordination.SiteGrantLedger,
		coordination.SiteGrantMarker,
		coordination.SiteCodexBudget,
		coordination.SiteCompatibilityConfig,
		coordination.SiteCompatibilityReceipt,
		coordination.SiteCompatibilityManifest,
		coordination.SiteCompactExisting,
		coordination.SiteCompactCandidate,
	} {
		readers[site] = value.ReaderRoute{Input: "text-json", Codec: utf8Strict, Domain: domain}
	}
	writers := map[string]value.WriterRoute{
		coordination.SiteSessionWrite:      {Route: "dump", Codec: utf8Strict, EnsureASCII: &no, Compact: &no, TrailingLF: &yes, Domain: domain},
		coordination.SiteInboxContextWrite: {Route: "dump", Codec: utf8Strict, EnsureASCII: &no, Compact: &no, TrailingLF: &yes, Domain: domain},
		coordination.SiteCallRequest:       {Route: "dumps", Codec: utf8Strict, EnsureASCII: &yes, Compact: &no, TrailingLF: &no, Domain: domain},
		coordination.SiteCLIOutput:         {Route: "dumps", Codec: utf8Strict, EnsureASCII: &yes, Compact: &no, TrailingLF: &yes, Domain: domain},
		coordination.SiteRecoveryOutput:    {Route: "dumps", Codec: utf8Strict, EnsureASCII: &yes, Compact: &no, TrailingLF: &yes, Domain: domain},
		SiteQueueMarker:                    {Route: "dumps", Codec: utf8Strict, EnsureASCII: &yes, Compact: &no, TrailingLF: &no, Domain: domain},
	}
	fs := coordinationFilesystemRoutes{workDir: workDir, env: envFn, lookupEnv: lookupEnvFn}
	return &value.SourceReaderProfile{
		Definition: value.SourceProfileDefinition{
			Schema:             "cairn.source-reader-profile/1",
			State:              "definition",
			ReadIntegerDigits:  &digits,
			WriteIntegerDigits: &digits,
			Filesystem:         &value.Codec{Name: "utf-8", Errors: "surrogateescape"},
			Readers:            readers,
			Writers:            writers,
			Domains:            map[string]value.DomainSelection{domain: {Class: "class-c-unlimited", Route: "go-kernel"}},
		},
		Routes: coordinationProfileRoutes{fs: fs},
	}
}

type coordinationProfileRoutes struct {
	fs coordinationFilesystemRoutes
}

func (coordinationProfileRoutes) CheckDefinition(d value.SourceProfileDefinition, sites []string) error {
	if d.ReadIntegerDigits == nil || d.WriteIntegerDigits == nil {
		return &value.ProfileUnavailable{}
	}
	for _, site := range sites {
		if _, ok := d.Readers[site]; !ok {
			if _, ok = d.Writers[site]; !ok {
				return &value.ProfileUnavailable{}
			}
		}
	}
	return nil
}

func (coordinationProfileRoutes) Decode(_ string, raw []byte, route value.ReaderRoute, digits int, bytesKernel func([]byte, int) (*value.Value, error), textKernel func([]byte, string, int) (*value.Value, error)) (*value.Value, error) {
	if route.Input == "bytes-json" {
		return bytesKernel(raw, digits)
	}
	return textKernel(raw, route.Codec.Name, digits)
}

func (coordinationProfileRoutes) Write(_ string, out io.Writer, v *value.Value, route value.WriterRoute, digits int, kernel func(io.Writer, *value.Value, value.WriterRoute, int) error) error {
	return kernel(out, v, route, digits)
}

func (r coordinationProfileRoutes) Filesystem() value.FilesystemRoutes { return r.fs }

func (r coordinationProfileRoutes) TranscriptRuntime() *SourceRuntime {
	return &SourceRuntime{ParseClaim: ParseCoordinationClaimInstant, Filesystem: r.fs}
}

type coordinationFilesystemRoutes struct {
	workDir   string
	env       func(string) string
	lookupEnv func(string) (string, bool)
}

func (f coordinationFilesystemRoutes) probe() admission.OSProbe {
	dir := f.workDir
	if dir == "" {
		dir, _ = os.Getwd()
	}
	return admission.OSProbe{Directory: dir}
}

func (f coordinationFilesystemRoutes) Encode(points []rune) (string, error) {
	return admission.PathBytes(points)
}

func (f coordinationFilesystemRoutes) Decode(raw string) ([]rune, error) {
	return admission.PathPoints(raw), nil
}

func (f coordinationFilesystemRoutes) Resolve(v *value.Value) ([]rune, error) {
	return f.probe().Resolve(v)
}

func (f coordinationFilesystemRoutes) IsDir(points []rune) (bool, error) {
	return f.probe().IsDir(points)
}

func (f coordinationFilesystemRoutes) Exists(points []rune) (bool, error) {
	return f.probe().Exists(points)
}

func (f coordinationFilesystemRoutes) Home() ([]rune, error) {
	if f.lookupEnv != nil {
		if home, ok := f.lookupEnv("HOME"); ok {
			if home == "" {
				return []rune{'/'}, nil
			}
			return admission.PathPoints(home), nil
		}
	} else if f.env != nil {
		if home := f.env("HOME"); home != "" {
			return admission.PathPoints(home), nil
		}
	} else if home, ok := os.LookupEnv("HOME"); ok {
		if home == "" {
			return []rune{'/'}, nil
		}
		return admission.PathPoints(home), nil
	}
	current, err := user.Current()
	if err != nil {
		return nil, err
	}
	return admission.PathPoints(current.HomeDir), nil
}

// ParseCoordinationClaimInstant parses Cairn store RFC 3339 / ISO 8601 claim
// timestamps and common ISO 8601 separator/precision variants, refusing
// malformed inputs such as year 0000 or lowercase 'z' (98d46758 item 2(b)).
func ParseCoordinationClaimInstant(site ClaimSite, raw *value.Value) (ClaimInstant, error) {
	if raw == nil {
		return ClaimInstant{}, &value.Error{Class: "KeyError", Message: "created_at"}
	}
	if raw.Kind != value.String {
		class := "TypeError"
		if site != ClaimNative {
			class = "AttributeError"
		}
		return ClaimInstant{}, &value.Error{Class: class, Message: "claim timestamp must be a string"}
	}
	for _, r := range raw.String {
		if !utf8.ValidRune(r) {
			return ClaimInstant{}, &value.Error{Class: "ValueError", Message: "invalid ISO timestamp"}
		}
	}
	s := string(raw.String)
	if site != ClaimNative {
		s = strings.ReplaceAll(s, "Z", "+00:00")
	}
	for _, layout := range []string{
		time.RFC3339Nano,
		"2006-01-02 15:04:05Z07:00",
		"2006-01-02 15:04:05.999999999Z07:00",
		"2006-01-02T15:04:05-0700",
		"2006-01-02T15:04:05.999999999-0700",
		"2006-01-02 15:04:05-0700",
		"2006-01-02 15:04:05.999999999-0700",
	} {
		if at, err := time.Parse(layout, s); err == nil && at.Year() >= 1 {
			return ClaimInstant{Seconds: float64(at.Unix()) + float64(at.Nanosecond())/1e9, Aware: true}, nil
		}
	}
	for _, layout := range []string{
		"2006-01-02T15:04:05",
		"2006-01-02T15:04:05.999999999",
		"2006-01-02 15:04:05",
		"2006-01-02 15:04:05.999999999",
		"2006-01-02",
	} {
		if at, err := time.ParseInLocation(layout, s, time.Local); err == nil && at.Year() >= 1 {
			return ClaimInstant{Seconds: float64(at.Unix()) + float64(at.Nanosecond())/1e9, Aware: false}, nil
		}
	}
	return ClaimInstant{}, &value.Error{Class: "ValueError", Message: "invalid ISO timestamp"}
}

// ResolveBoundMemoryStateDir validates the inbox_recall_binding/3 manifest and
// returns its bound memory state_dir.
func ResolveBoundMemoryStateDir(profile *value.SourceReaderProfile, config *value.Value, uid uint32) (*value.Value, error) {
	if config == nil || config.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: "config"}
	}
	bindingVal := config.Get("inbox_recall_binding")
	if bindingVal == nil {
		return nil, &journal.KeyError{Key: "inbox_recall_binding"}
	}
	if bindingVal.Kind != value.String {
		return nil, &journal.TypeError{Field: "inbox_recall_binding"}
	}
	manifestPath, err := admission.PathBytes(bindingVal.String)
	if err != nil {
		return nil, err
	}
	if !filepath.IsAbs(manifestPath) || filepath.Clean(manifestPath) != manifestPath {
		return nil, &value.Error{Class: "ValueError", Message: "binding data path must be canonical"}
	}
	manifestRaw, err := readOwnerBindingData(manifestPath, uid, true)
	if err != nil {
		return nil, err
	}
	manifest, err := profile.Decode(coordination.SiteConfigRead, manifestRaw)
	if err != nil {
		return nil, err
	}
	if manifest == nil || manifest.Kind != value.Object {
		return nil, &value.Error{Class: "ValueError", Message: "binding must be an object"}
	}
	schema := manifest.Get("schema")
	enabled := manifest.Get("enabled")
	if schema == nil || schema.Kind != value.String || enabled == nil || enabled.Kind != value.Bool {
		return nil, &value.Error{Class: "ValueError", Message: "unknown inbox recall binding"}
	}
	schemaText, err := admission.PathBytes(schema.String)
	if err != nil {
		return nil, err
	}
	switch schemaText {
	case "cairn.inbox-recall-binding/3":
		binPath, binSHA, err := readBindingDescriptor(manifest.Get("binary"))
		if err != nil {
			return nil, err
		}
		if err = binding.ValidateBinary(binPath, binSHA); err != nil {
			return nil, &value.Error{Class: "ValueError", Message: err.Error()}
		}
	case "cairn.inbox-recall-binding/2":
		return nil, &value.Error{Class: "ValueError", Message: binding.ErrLegacyBinding.Error()}
	default:
		return nil, &value.Error{Class: "ValueError", Message: "unknown inbox recall binding"}
	}
	return finishBoundMemoryStateDir(profile, config, manifest, bindingVal.String, uid)
}

func finishBoundMemoryStateDir(profile *value.SourceReaderProfile, config, manifest *value.Value, manifestPath []rune, uid uint32) (*value.Value, error) {
	memPath, memSHA, err := readBindingDescriptor(manifest.Get("memory_config"))
	if err != nil {
		return nil, err
	}
	memRaw, err := readCheckedBindingFile(memPath, memSHA, uid)
	if err != nil {
		return nil, err
	}
	memory, err := profile.Decode(coordination.SiteConfigRead, memRaw)
	if err != nil {
		return nil, err
	}
	coordsVal := manifest.Get("coordination_configs")
	if coordsVal == nil || coordsVal.Kind != value.Array || len(coordsVal.Items) < 1 || len(coordsVal.Items) > 8 {
		return nil, &value.Error{Class: "ValueError", Message: "one to eight explicit coordination configurations required"}
	}
	coordinators := make([]*value.Value, 0, len(coordsVal.Items))
	for _, item := range coordsVal.Items {
		cPath, cSHA, err := readBindingDescriptor(item)
		if err != nil {
			return nil, err
		}
		cRaw, err := readCheckedBindingFile(cPath, cSHA, uid)
		if err != nil {
			return nil, err
		}
		coord, err := profile.Decode(coordination.SiteConfigRead, cRaw)
		if err != nil {
			return nil, err
		}
		coordinators = append(coordinators, coord)
	}
	if err = binding.ValidateConfigurationProfiles(binding.ConfigurationProfiles{
		Current:      config,
		Memory:       memory,
		Coordinators: coordinators,
		ManifestPath: manifestPath,
		Resolve:      profile.Routes.Filesystem().Resolve,
	}); err != nil {
		return nil, &value.Error{Class: "ValueError", Message: err.Error()}
	}
	if memory == nil || memory.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: "memory_config"}
	}
	stateDir := memory.Get("state_dir")
	if stateDir == nil {
		return nil, &journal.KeyError{Key: "state_dir"}
	}
	return stateDir, nil
}

func readBindingDescriptor(v *value.Value) (string, string, error) {
	if v == nil || v.Kind != value.Object {
		return "", "", &value.Error{Class: "ValueError", Message: "binding descriptor must be an object"}
	}
	p := v.Get("path")
	h := v.Get("sha256")
	if p == nil || p.Kind != value.String || h == nil || h.Kind != value.String {
		return "", "", &value.Error{Class: "ValueError", Message: "binding descriptor requires string path and sha256"}
	}
	pathText, err := admission.PathBytes(p.String)
	if err != nil {
		return "", "", err
	}
	shaText, err := admission.PathBytes(h.String)
	if err != nil {
		return "", "", err
	}
	if !filepath.IsAbs(pathText) || len(shaText) != 64 || strings.Trim(shaText, "0123456789abcdef") != "" {
		return "", "", &value.Error{Class: "ValueError", Message: "invalid binding descriptor"}
	}
	return pathText, shaText, nil
}

func readCheckedBindingFile(path, expectedSHA string, uid uint32) ([]byte, error) {
	raw, err := readOwnerBindingData(path, uid, false)
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256(raw)
	if hex.EncodeToString(sum[:]) != expectedSHA {
		return nil, &value.Error{Class: "ValueError", Message: "inbox recall binding changed"}
	}
	return raw, nil
}

func readOwnerBindingData(path string, uid uint32, private bool) ([]byte, error) {
	if !filepath.IsAbs(path) {
		return nil, &value.Error{Class: "ValueError", Message: "binding data path must be absolute"}
	}
	f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	var before, after syscall.Stat_t
	if err = syscall.Fstat(int(f.Fd()), &before); err != nil {
		f.Close()
		return nil, err
	}
	mode := uint32(0022)
	if private {
		mode = 0077
	}
	if before.Uid != uid || before.Mode&mode != 0 || before.Mode&syscall.S_IFMT != syscall.S_IFREG || before.Size < 0 || before.Size > 1024*1024 {
		f.Close()
		return nil, &value.Error{Class: "ValueError", Message: "binding data is not owner-controlled"}
	}
	raw, readErr := io.ReadAll(io.LimitReader(f, 1024*1024+1))
	statErr := syscall.Fstat(int(f.Fd()), &after)
	closeErr := f.Close()
	if readErr != nil {
		return nil, readErr
	}
	if statErr != nil {
		return nil, statErr
	}
	if closeErr != nil {
		return nil, closeErr
	}
	if before.Dev != after.Dev || before.Ino != after.Ino || before.Mode != after.Mode || before.Nlink != after.Nlink || before.Uid != after.Uid || before.Gid != after.Gid || before.Size != after.Size || before.Mtim != after.Mtim || before.Ctim != after.Ctim || int64(len(raw)) != before.Size {
		return nil, &value.Error{Class: "ValueError", Message: "binding data changed during read"}
	}
	return raw, nil
}
