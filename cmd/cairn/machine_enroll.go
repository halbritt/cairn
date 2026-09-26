package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
)

// Role token files keep the names existing hooks, skills and bindings already use.
var roleTokenFiles = map[string]string{"agent": "hosted-agent.token", "observer": "hosted-observer.token"}

var tokenPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{16,512}$`)

const relayUnit = "cairn-relay.service"

type machineConfig struct {
	Schema     string   `json:"schema"`
	MachineID  string   `json:"machine_id"`
	Upstream   string   `json:"upstream"`
	Collection string   `json:"collection"`
	Principals []string `json:"principals"`
	EnrolledAt string   `json:"enrolled_at"`
}

type installedToken struct {
	Role      string `json:"role"`
	Principal string `json:"principal"`
	Path      string `json:"path"`
	State     string `json:"state"`
}

type roleCheck struct {
	Role          string         `json:"role"`
	OK            bool           `json:"ok"`
	Status        string         `json:"status,omitempty"`
	ServerVersion string         `json:"server_version,omitempty"`
	server        buildinfo.Info // compared, not reported twice
}

type machineEnrollment struct {
	MachineID    string           `json:"machine_id"`
	Upstream     string           `json:"upstream"`
	Collection   string           `json:"collection"`
	Config       string           `json:"config"`
	Socket       string           `json:"socket"`
	Tokens       []installedToken `json:"tokens"`
	Unit         string           `json:"unit,omitempty"`
	Service      string           `json:"service"`
	LocalVersion string           `json:"local_version"`
	Checks       []roleCheck      `json:"checks"`
	Linger       string           `json:"linger"`
	FileRemoved  bool             `json:"enrollment_file_removed"`
	Notes        []string         `json:"notes,omitempty"`
}

func enrollMachine(ctx context.Context, args []string) (machineEnrollment, error) {
	var result machineEnrollment
	f := flags("machine enroll")
	file := f.String("file", "", "owner-only enrollment file from machine provision or rotate")
	noService := f.Bool("no-service", false, "do not install or start cairn-relay.service")
	keep := f.Bool("keep-file", false, "keep the enrollment file after a successful import")
	if err := f.Parse(args); err != nil {
		return result, invalid(err.Error())
	}
	if f.NArg() != 0 || *file == "" {
		return result, invalid("machine enroll requires --file and no positional arguments")
	}
	issued, err := readEnrollment(*file)
	if err != nil {
		return result, err
	}
	result.MachineID, result.Upstream, result.Collection = issued.MachineID, issued.Upstream, issued.Collection
	local := localBuild()
	result.LocalVersion = local.Label()
	directory, err := privateDataDirectory()
	if err != nil {
		return result, err
	}
	result.Config = filepath.Join(directory, "machine.json")
	result.Socket = filepath.Join(directory, "api.sock")

	// Preflight every target before the first write.
	if _, err = os.Lstat(filepath.Join(directory, "identities.json")); err == nil {
		return result, invalid("this host has an API identity configuration; it is a central host, not a joining machine")
	}
	existing, err := enrolledMachine()
	if err != nil {
		return result, err
	}
	if existing != nil {
		if existing.MachineID != issued.MachineID {
			return result, invalid("this host is enrolled as machine " + existing.MachineID + "; run cairn machine unenroll before joining as " + issued.MachineID)
		}
		if strings.Join(existing.Principals, ",") != strings.Join(principalsOf(issued), ",") {
			return result, invalid("the enrollment file changes this machine's profiles; run cairn machine unenroll, then enroll")
		}
		if existing.Collection != issued.Collection {
			result.Notes = append(result.Notes, "collection changed from "+existing.Collection)
		}
		if existing.Upstream != issued.Upstream {
			result.Notes = append(result.Notes, "upstream changed from "+existing.Upstream)
		}
	} else if socketAnswers(result.Socket) {
		return result, invalid("a local Cairn API already serves " + result.Socket + "; stop it before enrolling this host")
	}
	for _, profile := range issued.Profiles {
		path := filepath.Join(directory, roleTokenFiles[profile.Role])
		current, err := readPrivateTarget(path, true)
		state := "created"
		switch {
		case errors.Is(err, os.ErrNotExist):
		case err != nil:
			return result, err
		case strings.TrimSpace(string(current)) == profile.Token:
			state = "unchanged" // rerun of an interrupted enrollment
		case existing != nil:
			state = "replaced" // rotation of this machine's own profile
		default:
			return result, invalid(path + " already holds a different token; move it aside before enrolling this host")
		}
		result.Tokens = append(result.Tokens, installedToken{profile.Role, profile.Principal, path, state})
	}
	if _, err = readPrivateTarget(result.Config, true); err != nil && !errors.Is(err, os.ErrNotExist) {
		return result, err
	}
	var unitPath string
	var unit []byte
	if !*noService {
		if unitPath, unit, err = relayUnitFile(result.Socket, issued.Upstream); err != nil {
			return result, err
		}
		current, err := readPrivateTarget(unitPath, false)
		if err == nil && existing == nil && !bytes.Equal(current, unit) {
			return result, invalid(unitPath + " already exists and this host is not enrolled; remove it before enrolling")
		}
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			return result, err
		}
	}

	// Mutations: tokens, then metadata, then service. A rerun with the same
	// file finishes an interrupted enrollment.
	incomplete := func(step string, err error) error {
		return &core.Error{Code: "INSTALL_FAILED", Message: fmt.Sprintf("enrollment incomplete at %s (%v); the enrollment file was kept: fix the cause and rerun cairn machine enroll --file %s", step, err, *file)}
	}
	for i, profile := range issued.Profiles {
		if result.Tokens[i].State == "unchanged" {
			continue
		}
		if err = installFile(result.Tokens[i].Path, []byte(profile.Token+"\n")); err != nil {
			return result, incomplete(profile.Role+" token", err)
		}
	}
	config := machineConfig{Schema: machineConfigSchema, MachineID: issued.MachineID, Upstream: issued.Upstream, Collection: issued.Collection, Principals: principalsOf(issued), EnrolledAt: time.Now().UTC().Format(time.RFC3339)}
	body, err := json.MarshalIndent(config, "", "  ")
	if err != nil {
		return result, err
	}
	if err = installFile(result.Config, append(body, '\n')); err != nil {
		return result, incomplete("machine.json", err)
	}
	result.Service = "not installed"
	if !*noService {
		if err = os.MkdirAll(filepath.Dir(unitPath), 0700); err != nil {
			return result, incomplete("relay unit", err)
		}
		if err = installFile(unitPath, unit); err != nil {
			return result, incomplete("relay unit", err)
		}
		result.Unit = unitPath
		for _, step := range [][]string{{"daemon-reload"}, {"enable", relayUnit}, {"restart", relayUnit}} {
			if output, err := systemctl(ctx, step...); err != nil {
				return result, incomplete("systemctl --user "+strings.Join(step, " "), fmt.Errorf("%v: %s", err, bytes.TrimSpace(output)))
			}
		}
		result.Service = serviceState(ctx)
	}
	result.Linger = lingerState(ctx)
	if result.Linger != "yes" {
		result.Notes = append(result.Notes, "user lingering is "+result.Linger+"; without it the relay stops at logout (loginctl enable-linger)")
	}
	if *noService && !socketAnswers(result.Socket) {
		// The operator runs the relay; nothing can be checked until it starts.
		result.Checks = []roleCheck{}
		result.Notes = append(result.Notes, "relay not managed by enrollment: start cairn relay --socket "+result.Socket+" --upstream "+issued.Upstream+", then run cairn machine status")
	} else {
		result.Checks = checkRoles(ctx, result.Socket, issued.Profiles, 15*time.Second)
	}
	for _, check := range result.Checks {
		if !check.OK {
			return result, incomplete(check.Role+" connectivity check", errors.New(check.Status))
		}
		if err = sameBuild(local, check.server); err != nil {
			return result, incomplete("build check", err)
		}
	}
	if !*keep {
		if err = os.Remove(*file); err != nil {
			result.Notes = append(result.Notes, "could not delete the enrollment file; delete it because it holds tokens")
		} else {
			result.FileRemoved = true
		}
	}
	return result, nil
}

type machineUnenrollment struct {
	MachineID string   `json:"machine_id"`
	Removed   []string `json:"removed"`
	Service   string   `json:"service"`
	Next      string   `json:"next"`
}

// unenrollMachine removes this host's local enrollment. Central profiles stay
// valid until the operator revokes them.
func unenrollMachine(ctx context.Context, args []string) (machineUnenrollment, error) {
	result := machineUnenrollment{Removed: []string{}}
	if len(args) != 0 {
		return result, invalid("machine unenroll takes no arguments")
	}
	existing, err := enrolledMachine()
	if err != nil {
		return result, err
	}
	if existing == nil {
		return result, &core.Error{Code: "NOT_FOUND", Message: "this host is not enrolled"}
	}
	result.MachineID = existing.MachineID
	directory, err := dataDirectory()
	if err != nil {
		return result, err
	}
	unitPath, err := relayUnitPath()
	if err != nil {
		return result, err
	}
	if _, err = os.Lstat(unitPath); err == nil {
		if output, err := systemctl(ctx, "disable", "--now", relayUnit); err != nil {
			return result, &core.Error{Code: "INSTALL_FAILED", Message: fmt.Sprintf("systemctl --user disable --now %s: %v: %s", relayUnit, err, bytes.TrimSpace(output))}
		}
	}
	paths := []string{unitPath}
	for _, principal := range existing.Principals {
		paths = append(paths, filepath.Join(directory, roleTokenFiles[principal[strings.LastIndexByte(principal, '/')+1:]]))
	}
	paths = append(paths, filepath.Join(directory, "machine.json")) // last, so a rerun finds the enrollment
	for _, path := range paths {
		if err = os.Remove(path); err == nil {
			result.Removed = append(result.Removed, path)
		} else if !errors.Is(err, os.ErrNotExist) {
			return result, installationError(path, err)
		}
	}
	_, _ = systemctl(ctx, "daemon-reload")
	result.Service = serviceState(ctx)
	result.Next = "revoke the central profiles with cairn machine revoke --machine " + existing.MachineID + " on the central host"
	return result, nil
}

type machineStatusResult struct {
	Enrolled bool           `json:"enrolled"`
	Config   *machineConfig `json:"config,omitempty"`
	Service  string         `json:"service"`
	Linger   string         `json:"linger"`
	Local    string         `json:"local_version"`
	Checks   []roleCheck    `json:"checks"`
}

func machineStatus(ctx context.Context, args []string) (machineStatusResult, error) {
	result := machineStatusResult{Checks: []roleCheck{}, Local: localBuild().Label()}
	if len(args) != 0 {
		return result, invalid("machine status takes no arguments")
	}
	config, err := enrolledMachine()
	if err != nil || config == nil {
		return result, err
	}
	directory, err := dataDirectory()
	if err != nil {
		return result, err
	}
	result.Enrolled, result.Config = true, config
	result.Service, result.Linger = serviceState(ctx), lingerState(ctx)
	for _, principal := range config.Principals {
		role := principal[strings.LastIndexByte(principal, '/')+1:]
		path := filepath.Join(directory, roleTokenFiles[role])
		token, err := os.ReadFile(path)
		if err != nil {
			result.Checks = append(result.Checks, roleCheck{Role: role, Status: "token file unreadable"})
			continue
		}
		result.Checks = append(result.Checks, checkRoles(ctx, filepath.Join(directory, "api.sock"), []enrollmentProfile{{Role: role, Token: strings.TrimSpace(string(token))}}, 0)...)
	}
	return result, nil
}

func readEnrollment(path string) (enrollment, error) {
	var issued enrollment
	body, err := readOwnedFile(path, "enrollment file", true, 64*1024)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return issued, invalid("enrollment file not found: " + path)
		}
		return issued, err
	}
	if err = decodeOne(body, &issued); err != nil || issued.Schema != enrollmentSchema {
		return issued, invalid("not a single " + enrollmentSchema + " object")
	}
	if !machineIDPattern.MatchString(issued.MachineID) || !validCollection(issued.Collection) {
		return issued, invalid("enrollment file has an invalid machine ID or collection")
	}
	if issued.Upstream, err = normalizeUpstream(issued.Upstream); err != nil {
		return issued, invalid("enrollment file has an invalid upstream")
	}
	roles := map[string]bool{}
	for _, profile := range issued.Profiles {
		if _, known := roleTokenFiles[profile.Role]; !known || roles[profile.Role] || profile.Principal != machinePrincipal(issued.MachineID, profile.Role) || !tokenPattern.MatchString(profile.Token) {
			return issued, invalid("enrollment file has an invalid profile")
		}
		roles[profile.Role] = true
	}
	if !roles["agent"] {
		return issued, invalid("enrollment file has no agent profile")
	}
	return issued, nil
}

func validCollection(collection string) bool {
	return strings.TrimSpace(collection) != "" && collection != "*" && len(collection) <= 256
}

// readMachineConfig validates the whole stored enrollment, because enroll,
// status and unenroll act on it.
func readMachineConfig(path string) (machineConfig, error) {
	var config machineConfig
	body, err := readOwnedFile(path, path, true, 64*1024)
	if err != nil {
		return config, err
	}
	fail := func() (machineConfig, error) {
		return machineConfig{}, invalid(path + " is not a valid machine configuration; inspect it, or remove it and enroll again")
	}
	if decodeOne(body, &config) != nil || config.Schema != machineConfigSchema || !machineIDPattern.MatchString(config.MachineID) || !validCollection(config.Collection) || config.EnrolledAt == "" {
		return fail()
	}
	if upstream, err := normalizeUpstream(config.Upstream); err != nil || upstream != config.Upstream {
		return fail()
	}
	seen := map[string]bool{}
	for _, principal := range config.Principals {
		role := principal[strings.LastIndexByte(principal, '/')+1:]
		if _, known := roleTokenFiles[role]; !known || seen[role] || principal != machinePrincipal(config.MachineID, role) {
			return fail()
		}
		seen[role] = true
	}
	if !seen["agent"] {
		return fail()
	}
	return config, nil
}

// enrolledMachine returns nil when this host has not joined a central API.
// A present but unreadable configuration is an error, never a silent default.
func enrolledMachine() (*machineConfig, error) {
	directory, err := dataDirectory()
	if err != nil {
		// Explicit connection flags work without a home directory, and no
		// enrollment can live in a directory that cannot be located.
		return nil, nil
	}
	config, err := readMachineConfig(filepath.Join(directory, "machine.json"))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &config, nil
}

// defaultAgentToken is the ordinary profile an enrolled machine installed.
func defaultAgentToken(enrolled *machineConfig) string {
	if enrolled != nil {
		return roleTokenFiles["agent"]
	}
	return "agent.token"
}

func principalsOf(issued enrollment) []string {
	principals := []string{}
	for _, profile := range issued.Profiles {
		principals = append(principals, profile.Principal)
	}
	return principals
}

// readPrivateTarget reads a file enrollment may replace. Links, other file
// types and other owners are refused so a write can never be redirected.
func readPrivateTarget(path string, ownerOnly bool) ([]byte, error) {
	return readOwnedFile(path, path, ownerOnly, 64*1024)
}

var localBuild = buildinfo.Read

// sameBuild requires clean, identical VCS revisions: the trial supports one
// tested protocol version on every host, and missing stamps prove nothing.
func sameBuild(local, server buildinfo.Info) error {
	describe := func(info buildinfo.Info) string { return info.Label() }
	if local.Revision == "" || server.Revision == "" || local.Modified == nil || server.Modified == nil || *local.Modified || *server.Modified {
		return fmt.Errorf("cannot confirm the same build (central %s, this host %s); install Cairn built from one clean commit on both hosts", describe(server), describe(local))
	}
	if local.Revision != server.Revision {
		return fmt.Errorf("the central API runs %s and this host runs %s; install the same Cairn build on both hosts", describe(server), describe(local))
	}
	return nil
}

func privateDataDirectory() (string, error) {
	directory, err := dataDirectory()
	if err != nil {
		return "", err
	}
	if err = os.MkdirAll(directory, 0700); err != nil {
		return "", installationError(directory, err)
	}
	info, err := os.Lstat(directory)
	if err != nil {
		return "", installationError(directory, err)
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.IsDir() || stat.Uid != uint32(os.Geteuid()) || info.Mode().Perm()&0077 != 0 {
		return "", invalid(directory + " must be a real directory owned by the current user and owner-only (chmod 700)")
	}
	return directory, nil
}

func socketAnswers(path string) bool {
	connection, err := net.DialTimeout("unix", path, time.Second)
	if err != nil {
		return false
	}
	connection.Close()
	return true
}

var (
	unitPathPattern     = regexp.MustCompile(`^/[A-Za-z0-9._/@+-]+$`)
	unitUpstreamPattern = regexp.MustCompile(`^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$`)
)

func relayUnitPath() (string, error) {
	configHome := os.Getenv("XDG_CONFIG_HOME")
	if configHome == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		configHome = filepath.Join(home, ".config")
	}
	return filepath.Join(configHome, "systemd", "user", relayUnit), nil
}

// relayUnitFile renders the unit without shell or systemd specifiers: every
// interpolated value is restricted to characters systemd passes literally.
func relayUnitFile(socket, upstream string) (string, []byte, error) {
	executable, err := os.Executable()
	if err != nil {
		return "", nil, installationError("Cairn executable", err)
	}
	if !unitPathPattern.MatchString(executable) || !unitPathPattern.MatchString(socket) || !unitUpstreamPattern.MatchString(upstream) {
		return "", nil, invalid("the Cairn executable, data directory and upstream must use plain characters (no spaces, quotes or %) to be written into a systemd unit")
	}
	path, err := relayUnitPath()
	if err != nil {
		return "", nil, err
	}
	unit := fmt.Sprintf(`[Unit]
Description=Cairn relay to the central agent API
After=network-online.target

[Service]
Type=simple
ExecStart=%s relay --socket %s --upstream %s
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=yes

[Install]
WantedBy=default.target
`, executable, socket, upstream)
	return path, []byte(unit), nil
}

func serviceState(ctx context.Context) string {
	output, _ := systemctl(ctx, "is-active", relayUnit)
	if state := strings.TrimSpace(string(output)); state != "" {
		return state
	}
	return "unknown"
}

func lingerState(ctx context.Context) string {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	output, err := exec.CommandContext(ctx, "loginctl", "show-user", strconv.Itoa(os.Getuid()), "--property", "Linger", "--value").Output()
	if state := strings.TrimSpace(string(output)); err == nil && state != "" {
		return state
	}
	return "unknown"
}

func checkRoles(ctx context.Context, socket string, profiles []enrollmentProfile, wait time.Duration) []roleCheck {
	checks := []roleCheck{}
	for _, profile := range profiles {
		check := roleCheck{Role: profile.Role}
		info, err := versionOverSocket(ctx, socket, profile.Token, wait)
		if err == nil {
			check.OK, check.Status, check.ServerVersion, check.server = true, "OK", info.Label(), info
		} else if code := core.Code(err); code != "" && code != "STORE_ERROR" {
			check.Status = code
		} else {
			check.Status = "API_CONNECTION_FAILED"
		}
		checks = append(checks, check)
	}
	return checks
}
