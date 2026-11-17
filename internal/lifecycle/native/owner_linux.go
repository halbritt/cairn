// Package native composes native admission with checked settings publication.
// Existing, unmanaged clients are outside its supported owner contract.
package native

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/procpeer"
)

type Pin struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}

type ClaudeConfig struct {
	Client         Pin
	ClientVersion  string
	SourceSettings Pin
	Parent         string
	Account        string
	Work           string
}

const QualifiedClaudeVersion = "2.1.326"
const QualifiedClaudeSHA256 = "b6b3647d981130dc44fc670846ae97b6cde531ffb667cf9c4d17cfcafb38cb37"

type Verdict string

const (
	Quiescent Verdict = "QUIESCENT"
	Busy      Verdict = "BUSY"
	Unknown   Verdict = "UNKNOWN"
)

// Observation is selected evidence only. It cannot reconstruct an Owner or
// authorize publication. Store and local-state coverage must also be complete.
type Observation struct {
	Schema       string              `json:"schema"`
	Verdict      Verdict             `json:"verdict"`
	Reason       string              `json:"reason,omitempty"`
	Owner        procpeer.ProcessRef `json:"owner"`
	Generation   uint64              `json:"generation"`
	Settings     string              `json:"settings"`
	Client       Pin                 `json:"client"`
	Version      string              `json:"version"`
	Phase        string              `json:"phase"`
	NativeIDs    []string            `json:"native_ids"`
	ObservedAt   time.Time           `json:"observed_at"`
	Coverage     string              `json:"coverage"`
	OutsideScope string              `json:"outside_scope"`
	Sessions     []SessionFacts      `json:"sessions,omitempty"`
	Files        map[string]string   `json:"files,omitempty"`
}

type Owner struct {
	mu              sync.Mutex
	origin          *Owner
	creator         procpeer.ProcessRef
	config          ClaudeConfig
	lifetime        context.Context
	cancel          context.CancelFunc
	root            string
	settings        *install.SettingsSnapshot
	image           *os.File
	imageInfo       os.FileInfo
	phase           string
	generation      uint64
	nativeIDs       []string
	closed          bool
	client          *claudeClient
	controller      bool
	joined          []procpeer.ProcessRef
	uncertain       bool
	material        *publicationMaterial
	exchange        *install.SettingsExchange
	pendingMutation bool
}

// NewClaudeOwner allocates a fresh, private settings namespace from birth. It
// does not attach to an existing native owner, launch Claude or publish Go rows.
func NewClaudeOwner(ctx context.Context, cfg ClaudeConfig) (_ *Owner, err error) {
	for _, path := range []string{cfg.Client.Path, cfg.SourceSettings.Path, cfg.Parent, cfg.Account, cfg.Work} {
		if !utf8.ValidString(path) || strings.ContainsRune(path, 0) || !filepath.IsAbs(path) || filepath.Clean(path) != path {
			return nil, errors.New("native owner requires explicit canonical paths")
		}
	}
	if cfg.ClientVersion != QualifiedClaudeVersion || cfg.Client.SHA256 != QualifiedClaudeSHA256 {
		return nil, errors.New("native Claude version/image has no qualified entry contract")
	}
	creator, err := procpeer.ProcessRefOf(os.Getpid())
	if err != nil {
		return nil, err
	}
	parentProbe, err := install.InspectSettings(ctx, filepath.Join(cfg.Parent, ".cairn-native-parent-probe"))
	if err != nil || parentProbe.Metadata().Present {
		return nil, errors.Join(err, errors.New("native parent is not a qualified private directory"))
	}
	source, err := install.InspectSettings(ctx, cfg.SourceSettings.Path)
	if err != nil || !source.Metadata().Present || source.Metadata().SHA256 != cfg.SourceSettings.SHA256 {
		return nil, errors.Join(err, errors.New("selected native settings source differs"))
	}
	if _, err := ownedCommandRows(source.Bytes()); err != nil {
		return nil, err
	}
	image, err := os.Open(cfg.Client.Path)
	if err != nil {
		return nil, err
	}
	defer func() {
		if err != nil {
			err = errors.Join(err, image.Close())
		}
	}()
	info, err := image.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0022 != 0 || info.Mode().Perm()&0100 == 0 || info.Mode()&(os.ModeSetuid|os.ModeSetgid) != 0 {
		return nil, errors.Join(err, errors.New("selected native image is not a safe executable"))
	}
	if err := checkImage(ctx, image, info, cfg.Client); err != nil {
		return nil, err
	}
	root, err := os.MkdirTemp(cfg.Parent, "cairn-native-claude-")
	if err != nil {
		return nil, err
	}
	settings, err := install.CopySettings(ctx, source, filepath.Join(root, "settings.json"))
	if err != nil {
		return nil, fmt.Errorf("native owner initialization incomplete; retain %s: %w", root, err)
	}
	lifetime, cancel := context.WithCancel(ctx)
	o := &Owner{creator: creator, config: cfg, lifetime: lifetime, cancel: cancel, root: root, settings: settings,
		image: image, imageInfo: info, phase: "never-launched", generation: 1, nativeIDs: []string{}}
	o.origin = o
	return o, nil
}

func sameImageInfo(a, b os.FileInfo) bool {
	if a == nil || b == nil {
		return false
	}
	x, xok := a.Sys().(*syscall.Stat_t)
	y, yok := b.Sys().(*syscall.Stat_t)
	return xok && yok && os.SameFile(a, b) && a.Size() == b.Size() && a.ModTime() == b.ModTime() && a.Mode() == b.Mode() && x.Uid == y.Uid && x.Gid == y.Gid && x.Nlink == y.Nlink && x.Ctim == y.Ctim
}

func checkImage(ctx context.Context, file *os.File, selected os.FileInfo, pin Pin) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	before, err := file.Stat()
	if err != nil || !sameImageInfo(selected, before) {
		return errors.Join(err, errors.New("native image descriptor changed"))
	}
	hash := sha256.New()
	if _, err := io.Copy(hash, io.NewSectionReader(file, 0, selected.Size())); err != nil {
		return err
	}
	after, err := file.Stat()
	pathInfo, pathErr := os.Lstat(pin.Path)
	if err != nil || pathErr != nil || !sameImageInfo(before, after) || !sameImageInfo(after, pathInfo) || hex.EncodeToString(hash.Sum(nil)) != pin.SHA256 {
		return errors.Join(err, pathErr, errors.New("native image bytes or named identity changed"))
	}
	return ctx.Err()
}

func (*Owner) MarshalJSON() ([]byte, error) {
	return nil, errors.New("native owner is process-local; serialize its observation instead")
}

func (*Owner) UnmarshalJSON([]byte) error {
	return errors.New("serialized observations cannot create a native owner")
}

func (o *Owner) inspectLocked(ctx context.Context) Observation {
	result := Observation{Schema: "cairn.native-owner-observation/1", Verdict: Unknown,
		ObservedAt: time.Now().UTC(), Coverage: "owned-fresh-settings-and-stdio-client",
		OutsideScope: "unmanaged owners, other settings namespaces and external restart supervisors unsupported"}
	if o == nil || o.origin != o || o.closed || !procpeer.Live(o.creator) || o.creator.PID != os.Getpid() {
		result.Reason = "process-local owner unavailable"
		return result
	}
	result.Owner, result.Generation, result.Client, result.Version, result.Phase = o.creator, o.generation, o.config.Client, o.config.ClientVersion, o.phase
	result.Settings = filepath.Join(o.root, "settings.json")
	result.NativeIDs = append([]string{}, o.nativeIDs...)
	if o.uncertain {
		result.Reason = "native protocol or descendant ownership became uncertain"
		return result
	}
	if err := errors.Join(o.settings.Recheck(ctx), checkImage(ctx, o.image, o.imageInfo, o.config.Client)); err != nil {
		result.Reason = err.Error()
		return result
	}
	if o.phase != "never-launched" && o.phase != "joined" {
		result.Verdict, result.Reason = Busy, "owned native admission or callbacks are active"
		return result
	}
	result.Reason = "native owner known; authenticated store and durable-state coverage not yet supplied"
	return result
}

// Inspect takes only an in-process mutex and reads selected existing files. A
// missing/zero/copied owner remains UNKNOWN, never an empty-owner positive.
func (o *Owner) Inspect(ctx context.Context) Observation {
	if o == nil {
		return o.inspectLocked(ctx)
	}
	if !o.mu.TryLock() {
		return Observation{Schema: "cairn.native-owner-observation/1", Verdict: Busy,
			Reason: "owned admission operation is in progress", ObservedAt: time.Now().UTC()}
	}
	defer o.mu.Unlock()
	return o.quiescenceLocked(ctx)
}

func (o *Owner) Close() error {
	if o == nil {
		return nil
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.origin != o {
		return errors.New("native owner copy is invalid")
	}
	if o.closed {
		return nil
	}
	if o.client != nil {
		return errors.New("cannot close an owner with active native admission")
	}
	o.closed = true
	o.cancel()
	return o.image.Close()
}
