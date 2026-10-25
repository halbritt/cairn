package siwc

// TrialStoreTools adapts a disposable task-eval TrialStore to CairnTools.
// It is intentionally trial-only: it runs the authenticated local CLI and
// rejects paths that point at Cairn's managed local profile.

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf8"
)

const (
	trialSearchTimeout = 15 * time.Second
	trialPullTimeout   = 30 * time.Second
	trialOutputLimit   = 2 << 20
	trialBodyLimit     = 65536
)

var trialUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var trialSHA256 = regexp.MustCompile(`^[0-9a-f]{64}$`)
var trialLabel = regexp.MustCompile(`^[A-Za-z0-9._/-]{1,256}$`)

const trialRepository = "trial:task-eval"

// TrialStoreProvenance must be populated from one freshly constructed
// scripts/trial_task_eval.py TrialStore. SocketDir is the target of its
// root/sock symlink; BinarySHA256 pins the exact local CLI used by the fixture.
// The token is read briefly to verify its hosted identity binding and by the
// Cairn CLI; its contents are never retained here.
type TrialStoreProvenance struct {
	Root         string
	SocketDir    string
	Socket       string
	TokenFile    string
	Binary       string
	BinarySHA256 string
	Repo         string
	Task         string
	Run          string
}

// TrialDeliveryReceipt records source identity and extent without retaining
// source text or tool payloads.
type TrialDeliveryReceipt struct {
	RecordID        string `json:"record_id"`
	Version         int    `json:"version"`
	Extent          string `json:"extent"`
	Component       string `json:"component,omitempty"`
	Offset          *int   `json:"offset,omitempty"`
	End             *int   `json:"end,omitempty"`
	TotalBytes      *int   `json:"total_bytes,omitempty"`
	SourceSHA256    string `json:"source_sha256"`
	DeliveredSHA256 string `json:"delivered_sha256"`
	DeliveredBytes  int    `json:"delivered_bytes"`
	Mandatory       bool   `json:"mandatory,omitempty"`
}

// TrialStoreTools is a narrowly scoped CairnTools implementation for one
// disposable trial store. It never accepts caller-created pull identities.
type TrialStoreTools struct {
	provenance TrialStoreProvenance
	root       string
	binary     string
	token      string
	socket     string
	mu         sync.Mutex
	returned   map[string]trialExpectedSource
	receipts   []TrialDeliveryReceipt
}

type trialExpectedSource struct {
	RecordID string
	Version  int
	SHA256   string
}

func NewTrialStoreTools(p TrialStoreProvenance) (*TrialStoreTools, error) {
	canonical, err := validateTrialStoreProvenance(p)
	if err != nil {
		return nil, err
	}
	return &TrialStoreTools{provenance: p, root: canonical.root, binary: canonical.binary,
		token: canonical.token, socket: canonical.socket, returned: make(map[string]trialExpectedSource)}, nil
}

type canonicalTrialPaths struct{ root, binary, token, socket, socketDir string }

func validateTrialStoreProvenance(p TrialStoreProvenance) (canonicalTrialPaths, error) {
	var paths canonicalTrialPaths
	for _, path := range []string{p.Root, p.SocketDir, p.Socket, p.TokenFile, p.Binary} {
		if !filepath.IsAbs(path) {
			return paths, errors.New("trial Cairn provenance paths must be absolute")
		}
	}
	if p.Repo != trialRepository || !strings.HasPrefix(p.Task, "source-fit/") || !trialLabel.MatchString(p.Task) || !trialLabel.MatchString(p.Run) {
		return paths, errors.New("trial Cairn scope must use the synthetic task-eval repo and pinned source-fit task/run labels")
	}
	if !trialSHA256.MatchString(p.BinarySHA256) {
		return paths, errors.New("trial Cairn binary requires a pinned SHA-256")
	}
	var err error
	if paths.root, err = filepath.EvalSymlinks(p.Root); err != nil {
		return paths, errors.New("trial root unavailable")
	}
	if !filepath.IsAbs(paths.root) {
		return paths, errors.New("trial root must be absolute")
	}
	rootInfo, err := os.Stat(paths.root)
	if err != nil || !rootInfo.IsDir() || !ownedMode(rootInfo, 0700) {
		return paths, errors.New("trial root must be an owner-only directory")
	}
	managed := ""
	if home := os.Getenv("HOME"); home != "" {
		managed, _ = filepath.Abs(filepath.Join(home, ".local", "share", "cairn"))
		if resolved, resolveErr := filepath.EvalSymlinks(managed); resolveErr == nil {
			managed = resolved
		}
	}
	if managed != "" && pathWithin(paths.root, managed) {
		return paths, errors.New("managed Cairn local-store paths are not trial stores")
	}
	paths.token, err = filepath.Abs(p.TokenFile)
	if err != nil || !pathWithin(paths.token, paths.root) || filepath.Clean(paths.token) != filepath.Join(paths.root, "agent.token") {
		return paths, errors.New("trial token must be inside its disposable root")
	}
	tokenInfo, err := os.Lstat(paths.token)
	if err != nil || tokenInfo == nil {
		return paths, errors.New("trial token must be an owner-only regular file")
	}
	stat, statOK := tokenInfo.Sys().(*syscall.Stat_t)
	if !tokenInfo.Mode().IsRegular() || !ownedMode(tokenInfo, 0600) || !statOK || stat.Nlink != 1 {
		return paths, errors.New("trial token must be an owner-only regular file")
	}
	if err := validateHostedTrialIdentity(paths.root, paths.token, p.Repo); err != nil {
		return paths, err
	}
	paths.socketDir, err = filepath.EvalSymlinks(p.SocketDir)
	if err != nil || filepath.Base(paths.socketDir) == "" || !strings.HasPrefix(filepath.Base(paths.socketDir), "cte-sock-") {
		return paths, errors.New("trial socket directory must be the recorded task-eval temporary directory")
	}
	rootLink := filepath.Join(paths.root, "sock")
	linkTarget, err := filepath.EvalSymlinks(rootLink)
	if err != nil || linkTarget != paths.socketDir {
		return paths, errors.New("trial root socket link does not match recorded socket directory")
	}
	sockDirInfo, err := os.Stat(paths.socketDir)
	if err != nil || !sockDirInfo.IsDir() || !ownedMode(sockDirInfo, 0700) {
		return paths, errors.New("trial socket directory must be owner-only")
	}
	paths.socket, err = filepath.Abs(p.Socket)
	if err != nil || filepath.Dir(paths.socket) != paths.socketDir || filepath.Base(paths.socket) != "api.sock" {
		return paths, errors.New("trial API socket must be api.sock in its recorded socket directory")
	}
	sockInfo, err := os.Lstat(paths.socket)
	if err != nil || sockInfo.Mode()&os.ModeSocket == 0 || !owned(sockInfo) {
		return paths, errors.New("trial API socket is unavailable or not owner-owned")
	}
	paths.binary, err = filepath.EvalSymlinks(p.Binary)
	if err != nil {
		return paths, errors.New("pinned trial Cairn binary unavailable")
	}
	if !filepath.IsAbs(paths.binary) || (managed != "" && pathWithin(paths.binary, managed)) {
		return paths, errors.New("trial binary path is not an allowed pinned fixture binary")
	}
	binaryInfo, err := os.Stat(paths.binary)
	if err != nil || !binaryInfo.Mode().IsRegular() || binaryInfo.Mode().Perm()&0111 == 0 {
		return paths, errors.New("trial Cairn binary must be an executable regular file")
	}
	digest, err := fileSHA256(paths.binary)
	if err != nil || digest != p.BinarySHA256 {
		return paths, errors.New("trial Cairn binary digest does not match its pin")
	}
	return paths, nil
}

func owned(info os.FileInfo) bool {
	stat, ok := info.Sys().(*syscall.Stat_t)
	return ok && int(stat.Uid) == os.Getuid()
}

func ownedMode(info os.FileInfo, mode os.FileMode) bool {
	return owned(info) && info.Mode().Perm() == mode
}

func pathWithin(path, root string) bool {
	root, err := filepath.Abs(root)
	if err != nil || root == "." || root == string(filepath.Separator) {
		return false
	}
	path, err = filepath.Abs(path)
	if err != nil {
		return false
	}
	rel, err := filepath.Rel(root, path)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

func fileSHA256(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	h := sha256.New()
	if _, err := io.Copy(h, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func validateHostedTrialIdentity(root, tokenPath, repo string) error {
	token, err := os.ReadFile(tokenPath)
	if err != nil || len(token) == 0 || len(token) > 4096 {
		return errors.New("trial token unavailable")
	}
	tokenDigest := sha256.Sum256(token)
	identitiesPath := filepath.Join(root, "home", "identities.json")
	info, err := os.Lstat(identitiesPath)
	if err != nil || !info.Mode().IsRegular() || !ownedMode(info, 0600) {
		return errors.New("trial hosted identity file is unavailable or unsafe")
	}
	body, err := os.ReadFile(identitiesPath)
	if err != nil {
		return errors.New("trial hosted identity declaration is unavailable")
	}
	var identities []struct {
		TokenSHA256 string `json:"token_sha256"`
		Principal   string `json:"principal"`
		Repo        string `json:"repo"`
		Role        string `json:"role"`
		Destination string `json:"destination"`
	}
	if json.Unmarshal(body, &identities) != nil || len(identities) != 1 {
		return errors.New("trial hosted identity declaration is invalid")
	}
	identity := identities[0]
	if identity.TokenSHA256 != hex.EncodeToString(tokenDigest[:]) || !strings.HasPrefix(identity.Principal, "agent:task-eval-") || identity.Repo != repo || identity.Role != "agent" || identity.Destination != "hosted" {
		return errors.New("trial token is not bound to the pinned hosted task-eval identity")
	}
	return nil
}

func (t *TrialStoreTools) Search(ctx context.Context, raw json.RawMessage) (json.RawMessage, error) {
	var args struct {
		Query    string `json:"query"`
		Semantic *bool  `json:"semantic,omitempty"`
	}
	if err := strictJSON(raw, &args); err != nil || strings.TrimSpace(args.Query) == "" || len(args.Query) > 1024 || !utf8.ValidString(args.Query) {
		return nil, errors.New("invalid trial cairn_search arguments")
	}
	command := []string{"agent", "--socket", t.socket, "--token-file", t.token,
		"search", "--repo", t.provenance.Repo, "--task", t.provenance.Task,
		"--run", t.provenance.Run, "--tokens", "64000"}
	if args.Semantic != nil && *args.Semantic {
		command = append(command, "--semantic")
	}
	command = append(command, "--", args.Query)
	result, err := t.invoke(ctx, command, nil, trialSearchTimeout)
	if err != nil {
		return nil, err
	}
	data, err := unwrapCLIResult(result)
	if err != nil {
		return nil, err
	}
	if err := validateTrialSearchView(data, t.provenance.Repo, t.provenance.Task, t.provenance.Run); err != nil {
		return nil, err
	}
	pulls, err := collectPullArguments(data)
	if err != nil {
		return nil, err
	}
	receipts, err := validateTrialSearchDeliveries(data, t.provenance.Repo, t.provenance.Task, t.provenance.Run)
	if err != nil {
		return nil, err
	}
	sanitized, err := stripPullCommands(data)
	if err != nil {
		return nil, err
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	if len(t.returned)+len(pulls) > 512 {
		return nil, errors.New("trial search pull-argument bound exceeded")
	}
	for key, value := range pulls {
		if previous, exists := t.returned[key]; exists && previous != value {
			return nil, errors.New("trial Cairn search reused pull arguments for different source identities")
		}
		t.returned[key] = value
	}
	t.receipts = append(t.receipts, receipts...)
	return sanitized, nil
}

func (t *TrialStoreTools) Pull(ctx context.Context, raw json.RawMessage) (json.RawMessage, error) {
	canonical, err := canonicalJSON(raw)
	if err != nil {
		return nil, errors.New("invalid trial cairn_pull arguments")
	}
	t.mu.Lock()
	expected, ok := t.returned[string(canonical)]
	t.mu.Unlock()
	if !ok {
		return nil, errors.New("cairn_pull arguments were not returned by this trial store's search")
	}
	ctx, cancel := context.WithTimeout(ctx, trialPullTimeout)
	defer cancel()
	result, err := t.invoke(ctx, []string{"agent", "--socket", t.socket, "--token-file", t.token, "pull"}, canonical, trialPullTimeout)
	if err != nil {
		return nil, err
	}
	data, err := unwrapCLIResult(result)
	if err != nil {
		return nil, err
	}
	receipts, err := validateTrialExpansion(data, t.provenance.Repo, t.provenance.Task, t.provenance.Run)
	if err != nil {
		return nil, err
	}
	if len(receipts) == 0 || receipts[0].RecordID != expected.RecordID || receipts[0].Version != expected.Version || receipts[0].SourceSHA256 != expected.SHA256 {
		return nil, errors.New("trial Cairn pull did not match the searched record version and digest")
	}
	t.mu.Lock()
	t.receipts = append(t.receipts, receipts...)
	t.mu.Unlock()
	return data, nil
}

// Receipts returns metadata only; source text and tool output are not retained.
func (t *TrialStoreTools) Receipts() []TrialDeliveryReceipt {
	t.mu.Lock()
	defer t.mu.Unlock()
	return append([]TrialDeliveryReceipt(nil), t.receipts...)
}

func (t *TrialStoreTools) invoke(ctx context.Context, args []string, input []byte, timeout time.Duration) ([]byte, error) {
	paths, err := validateTrialStoreProvenance(t.provenance)
	if err != nil {
		return nil, err
	}
	if paths.root != t.root || paths.binary != t.binary || paths.token != t.token || paths.socket != t.socket {
		return nil, errors.New("trial Cairn provenance changed after construction")
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, t.binary, args...)
	cmd.Stdin = bytes.NewReader(input)
	cmd.Env = []string{"PATH=" + os.Getenv("PATH"), "HOME=" + t.root, "CAIRN_HOME=" + filepath.Join(t.root, "home")}
	var stdout, stderr limitedBuffer
	stdout.limit, stderr.limit = trialOutputLimit, 4096
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("authenticated trial Cairn CLI failed (%T)", err)
	}
	if stdout.exceeded {
		return nil, errors.New("trial Cairn CLI output exceeded its limit")
	}
	return append([]byte(nil), stdout.Bytes()...), nil
}

type limitedBuffer struct {
	bytes.Buffer
	limit    int
	exceeded bool
}

func (b *limitedBuffer) Write(data []byte) (int, error) {
	remaining := b.limit - b.Len()
	if remaining <= 0 {
		b.exceeded = true
		return len(data), nil
	}
	if len(data) > remaining {
		b.exceeded = true
		_, _ = b.Buffer.Write(data[:remaining])
		return len(data), nil
	}
	return b.Buffer.Write(data)
}

func strictJSON(raw []byte, destination any) error {
	if err := rejectDuplicateJSONKeys(raw); err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return err
	}
	if decoder.Decode(new(any)) != io.EOF {
		return errors.New("trailing JSON")
	}
	return nil
}

func canonicalJSON(raw []byte) ([]byte, error) {
	if err := rejectDuplicateJSONKeys(raw); err != nil {
		return nil, err
	}
	var value any
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	if decoder.Decode(new(any)) != io.EOF {
		return nil, errors.New("trailing JSON")
	}
	return json.Marshal(value)
}

func rejectDuplicateJSONKeys(raw []byte) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var readValue func() error
	readValue = func() error {
		token, err := decoder.Token()
		if err != nil {
			return err
		}
		delimiter, ok := token.(json.Delim)
		if !ok {
			return nil
		}
		switch delimiter {
		case '{':
			seen := make(map[string]bool)
			for decoder.More() {
				keyToken, err := decoder.Token()
				if err != nil {
					return err
				}
				key, ok := keyToken.(string)
				if !ok || seen[key] {
					return errors.New("duplicate or invalid JSON object key")
				}
				seen[key] = true
				if err := readValue(); err != nil {
					return err
				}
			}
			_, err = decoder.Token()
			return err
		case '[':
			for decoder.More() {
				if err := readValue(); err != nil {
					return err
				}
			}
			_, err = decoder.Token()
			return err
		default:
			return errors.New("invalid JSON delimiter")
		}
	}
	if err := readValue(); err != nil {
		return err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return errors.New("trailing JSON")
	}
	return nil
}

func unwrapCLIResult(raw []byte) (json.RawMessage, error) {
	var envelope struct {
		OK     bool            `json:"ok"`
		Status string          `json:"status"`
		Data   json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil || !envelope.OK || len(envelope.Data) == 0 || !json.Valid(envelope.Data) {
		return nil, errors.New("trial Cairn CLI returned an invalid or unsuccessful envelope")
	}
	return envelope.Data, nil
}

func stripPullCommands(data json.RawMessage) (json.RawMessage, error) {
	var view map[string]any
	if err := json.Unmarshal(data, &view); err != nil || view == nil {
		return nil, errors.New("trial Cairn search returned an invalid view")
	}
	for _, key := range []string{"index", "selected"} {
		rows, ok := view[key].([]any)
		if !ok {
			continue
		}
		for _, row := range rows {
			if object, ok := row.(map[string]any); ok {
				delete(object, "pull_command")
			}
		}
	}
	encoded, err := json.Marshal(view)
	return encoded, err
}

func validateTrialSearchView(data json.RawMessage, repo, task, run string) error {
	var view struct {
		Schema string `json:"schema"`
		Scope  struct {
			Repo   string `json:"repo"`
			TaskID string `json:"task_id"`
			RunID  string `json:"run_id"`
		} `json:"scope"`
		Destination struct {
			Name       string `json:"name"`
			AllowLocal *bool  `json:"allow_local"`
		} `json:"destination"`
		Index []struct {
			RecordID string `json:"record_id"`
			Version  int    `json:"version"`
			Class    string `json:"class"`
			BodySHA  string `json:"body_sha256"`
			Summary  string `json:"summary"`
		} `json:"index"`
	}
	if err := json.Unmarshal(data, &view); err != nil || view.Schema != "cairn.agent-search/1" || view.Scope.Repo != repo || view.Scope.TaskID != task || view.Scope.RunID != run || view.Destination.Name != "hosted" || view.Destination.AllowLocal == nil || *view.Destination.AllowLocal {
		return errors.New("trial Cairn search did not prove the pinned hosted scope")
	}
	if len(view.Index) > 128 {
		return errors.New("trial Cairn search exceeded its result bound")
	}
	for _, entry := range view.Index {
		if !trialUUID.MatchString(entry.RecordID) || entry.Version < 1 || (entry.Class != "A" && entry.Class != "B" && entry.Class != "C") || !trialSHA256.MatchString(entry.BodySHA) || entry.Summary == "" || !utf8.ValidString(entry.Summary) {
			return errors.New("trial Cairn search returned an invalid source preview")
		}
	}
	return nil
}

func validateTrialSearchDeliveries(data json.RawMessage, repo, task, run string) ([]TrialDeliveryReceipt, error) {
	var view struct {
		Index []struct {
			RecordID    string `json:"record_id"`
			Version     int    `json:"version"`
			BodySHA     string `json:"body_sha256"`
			Summary     string `json:"summary"`
			SummarySpan *struct {
				Offset int `json:"offset"`
				Length int `json:"length"`
			} `json:"summary_span"`
		} `json:"index"`
		Selected []trialSelection `json:"selected"`
	}
	if err := json.Unmarshal(data, &view); err != nil {
		return nil, errors.New("trial Cairn search delivery view is invalid")
	}
	if len(view.Index) > 128 || len(view.Selected) > 128 {
		return nil, errors.New("trial Cairn search delivery set exceeded its bound")
	}
	indexes := make(map[string]string, len(view.Index))
	receipts := make([]TrialDeliveryReceipt, 0, len(view.Index)+len(view.Selected))
	for _, entry := range view.Index {
		if len(entry.Summary) > trialBodyLimit || !utf8.ValidString(entry.Summary) {
			return nil, errors.New("trial Cairn search preview exceeded its bound")
		}
		identity := entry.RecordID + "/" + fmt.Sprint(entry.Version)
		indexes[identity] = entry.BodySHA
		digest := sha256.Sum256([]byte(entry.Summary))
		item := TrialDeliveryReceipt{RecordID: entry.RecordID, Version: entry.Version, Extent: "preview",
			SourceSHA256: entry.BodySHA, DeliveredSHA256: hex.EncodeToString(digest[:]), DeliveredBytes: len([]byte(entry.Summary))}
		if entry.SummarySpan != nil {
			if entry.SummarySpan.Offset < 0 || entry.SummarySpan.Length <= 0 || entry.SummarySpan.Offset > trialBodyLimit || entry.SummarySpan.Length > trialBodyLimit-entry.SummarySpan.Offset {
				return nil, errors.New("trial Cairn search returned an invalid summary source location")
			}
			item.Component = "summary"
			offset, end := entry.SummarySpan.Offset, entry.SummarySpan.Offset+entry.SummarySpan.Length
			item.Offset, item.End = &offset, &end
		}
		receipts = append(receipts, item)
	}
	for _, selection := range view.Selected {
		record := selection.Record
		if err := validateTrialRecordEligibility(record, repo, task, run); err != nil {
			return nil, err
		}
		item, err := receiptForRecord(record, nil, selection.Mandatory)
		if err != nil {
			return nil, err
		}
		identity := record.RecordID + "/" + fmt.Sprint(record.Version)
		if expected, ok := indexes[identity]; ok && expected != item.SourceSHA256 {
			return nil, errors.New("trial Cairn selected source differs from its indexed digest")
		}
		receipts = append(receipts, item)
	}
	return receipts, nil
}

func collectPullArguments(data json.RawMessage) (map[string]trialExpectedSource, error) {
	var view map[string]json.RawMessage
	if err := json.Unmarshal(data, &view); err != nil || view == nil {
		return nil, errors.New("trial Cairn search returned an invalid view")
	}
	result := make(map[string]trialExpectedSource)
	for _, key := range []string{"index", "selected"} {
		rawList, ok := view[key]
		if !ok {
			continue
		}
		var entries []json.RawMessage
		if json.Unmarshal(rawList, &entries) != nil || len(entries) > 128 {
			return nil, errors.New("trial Cairn search returned an invalid bounded entry list")
		}
		for _, entry := range entries {
			var wrapper struct {
				PullArguments json.RawMessage `json:"pull_arguments"`
				RecordID      string          `json:"record_id"`
				Version       int             `json:"version"`
				Class         string          `json:"class"`
				BodySHA256    string          `json:"body_sha256"`
				Summary       string          `json:"summary"`
			}
			if json.Unmarshal(entry, &wrapper) != nil {
				return nil, errors.New("trial Cairn search returned an invalid entry")
			}
			if len(wrapper.PullArguments) == 0 {
				continue
			}
			canonical, err := canonicalJSON(wrapper.PullArguments)
			if err != nil || len(canonical) > 128<<10 {
				return nil, errors.New("trial Cairn search returned invalid pull arguments")
			}
			var arguments map[string]json.RawMessage
			if json.Unmarshal(canonical, &arguments) != nil || arguments == nil || validateToolArguments("cairn_pull", arguments) != nil {
				return nil, errors.New("trial Cairn search returned incomplete pull arguments")
			}
			if !trialUUID.MatchString(wrapper.RecordID) || wrapper.Version < 1 || (wrapper.Class != "A" && wrapper.Class != "B" && wrapper.Class != "C") || !trialSHA256.MatchString(wrapper.BodySHA256) || wrapper.Summary == "" {
				return nil, errors.New("trial Cairn pull arguments lack their source identity")
			}
			expected := trialExpectedSource{RecordID: wrapper.RecordID, Version: wrapper.Version, SHA256: wrapper.BodySHA256}
			if previous, exists := result[string(canonical)]; exists && previous != expected {
				return nil, errors.New("trial Cairn search reused pull arguments for different source identities")
			}
			result[string(canonical)] = expected
		}
	}
	return result, nil
}

type trialRecord struct {
	RecordID    string `json:"record_id"`
	Version     int    `json:"version"`
	Class       string `json:"class"`
	Lifecycle   string `json:"lifecycle"`
	Sensitivity string `json:"sensitivity"`
	Scope       struct {
		Repo   string `json:"repo"`
		TaskID string `json:"task_id"`
		RunID  string `json:"run_id"`
	} `json:"scope"`
	Body string `json:"body"`
}

type trialSelection struct {
	Record    trialRecord `json:"record"`
	Mandatory bool        `json:"mandatory"`
}

type trialSpan struct {
	Offset       int    `json:"offset"`
	End          int    `json:"end"`
	TotalBytes   int    `json:"total_bytes"`
	SHA256       string `json:"sha256"`
	SourceSHA256 string `json:"source_sha256"`
	Body         string `json:"body"`
	BodyBase64   string `json:"body_base64"`
}

type trialExpansion struct {
	Selection     trialSelection   `json:"selection"`
	Competing     []trialSelection `json:"competing"`
	Span          *trialSpan       `json:"span"`
	SourceOpening *trialByteSpan   `json:"source_opening"`
}

type trialByteSpan struct {
	Offset     int    `json:"offset"`
	End        int    `json:"end"`
	TotalBytes int    `json:"total_bytes"`
	SHA256     string `json:"sha256"`
	Body       string `json:"body"`
	BodyBase64 string `json:"body_base64"`
}

func validateTrialExpansion(data json.RawMessage, repo, task, run string) ([]TrialDeliveryReceipt, error) {
	var expansion trialExpansion
	if err := json.Unmarshal(data, &expansion); err != nil {
		return nil, errors.New("trial Cairn pull returned an invalid expansion")
	}
	selections := append([]trialSelection{expansion.Selection}, expansion.Competing...)
	if len(selections) == 0 || len(selections) > 129 {
		return nil, errors.New("trial Cairn pull returned an invalid selection set")
	}
	receipts := make([]TrialDeliveryReceipt, 0, len(selections))
	for i, selection := range selections {
		record := selection.Record
		if err := validateTrialRecordEligibility(record, repo, task, run); err != nil {
			return nil, err
		}
		span := expansion.Span
		if i > 0 {
			span = nil
		}
		item, err := receiptForRecord(record, span, selection.Mandatory)
		if err != nil {
			return nil, err
		}
		receipts = append(receipts, item)
		if i == 0 && expansion.SourceOpening != nil {
			if expansion.Span == nil || expansion.SourceOpening.Offset != 0 {
				return nil, errors.New("trial Cairn source opening has no matching partial span")
			}
			opening, err := receiptForOpening(record, expansion.SourceOpening, expansion.Span.SourceSHA256, selection.Mandatory)
			if err != nil {
				return nil, err
			}
			receipts = append(receipts, opening)
		}
	}
	return receipts, nil
}

func validateTrialRecordEligibility(record trialRecord, repo, task, run string) error {
	if !trialUUID.MatchString(record.RecordID) || record.Version < 1 || (record.Class != "A" && record.Class != "B" && record.Class != "C") || (record.Lifecycle != "active" && record.Lifecycle != "superseded") || record.Sensitivity != "shareable" || record.Scope.Repo != repo || (record.Scope.TaskID != "*" && record.Scope.TaskID != task) || (record.Scope.RunID != "*" && record.Scope.RunID != run) {
		return errors.New("trial Cairn source is not eligible for the pinned hosted trial")
	}
	return nil
}

func receiptForRecord(record trialRecord, span *trialSpan, mandatory bool) (TrialDeliveryReceipt, error) {
	if span == nil {
		body := []byte(record.Body)
		if len(body) == 0 || len(body) > trialBodyLimit || !utf8.Valid(body) {
			return TrialDeliveryReceipt{}, errors.New("trial Cairn source exceeded the delivery bound or is not UTF-8")
		}
		digest := sha256.Sum256(body)
		hexDigest := hex.EncodeToString(digest[:])
		return TrialDeliveryReceipt{RecordID: record.RecordID, Version: record.Version, Extent: "full_body",
			Offset: intPointer(0), End: intPointer(len(body)), TotalBytes: intPointer(len(body)),
			SourceSHA256: hexDigest, DeliveredSHA256: hexDigest, DeliveredBytes: len(body), Mandatory: mandatory}, nil
	}
	var body []byte
	if span.BodyBase64 != "" {
		decoded, err := base64.StdEncoding.DecodeString(span.BodyBase64)
		if err != nil {
			return TrialDeliveryReceipt{}, errors.New("trial Cairn source span encoding is invalid")
		}
		body = decoded
	} else {
		body = []byte(span.Body)
	}
	if record.Body != "" || span.Offset < 0 || span.End <= span.Offset || span.TotalBytes < span.End || span.TotalBytes > trialBodyLimit || len(body) != span.End-span.Offset || (span.BodyBase64 != "" && span.Body != "") || !trialSHA256.MatchString(span.SHA256) || !trialSHA256.MatchString(span.SourceSHA256) {
		return TrialDeliveryReceipt{}, errors.New("trial Cairn source span identity is invalid")
	}
	digest := sha256.Sum256(body)
	actual := hex.EncodeToString(digest[:])
	if actual != span.SHA256 {
		return TrialDeliveryReceipt{}, errors.New("trial Cairn source span digest mismatch")
	}
	return TrialDeliveryReceipt{RecordID: record.RecordID, Version: record.Version, Extent: "partial_span",
		Offset: intPointer(span.Offset), End: intPointer(span.End), TotalBytes: intPointer(span.TotalBytes),
		SourceSHA256: span.SourceSHA256, DeliveredSHA256: actual, DeliveredBytes: len(body), Mandatory: mandatory}, nil
}

func receiptForOpening(record trialRecord, span *trialByteSpan, sourceSHA string, mandatory bool) (TrialDeliveryReceipt, error) {
	var body []byte
	if span.BodyBase64 != "" {
		decoded, err := base64.StdEncoding.DecodeString(span.BodyBase64)
		if err != nil {
			return TrialDeliveryReceipt{}, errors.New("trial Cairn source opening encoding is invalid")
		}
		body = decoded
	} else {
		body = []byte(span.Body)
	}
	if span.Offset != 0 || span.End <= span.Offset || span.End > 768 || span.TotalBytes < span.End || span.TotalBytes > trialBodyLimit || len(body) != span.End || (span.BodyBase64 != "" && span.Body != "") || !trialSHA256.MatchString(span.SHA256) || !trialSHA256.MatchString(sourceSHA) {
		return TrialDeliveryReceipt{}, errors.New("trial Cairn source opening extent is invalid")
	}
	digest := sha256.Sum256(body)
	actual := hex.EncodeToString(digest[:])
	if actual != span.SHA256 {
		return TrialDeliveryReceipt{}, errors.New("trial Cairn source opening digest mismatch")
	}
	return TrialDeliveryReceipt{RecordID: record.RecordID, Version: record.Version, Extent: "partial_span", Component: "source_opening",
		Offset: intPointer(span.Offset), End: intPointer(span.End), TotalBytes: intPointer(span.TotalBytes),
		SourceSHA256: sourceSHA, DeliveredSHA256: actual, DeliveredBytes: len(body), Mandatory: mandatory}, nil
}

func intPointer(value int) *int { return &value }
