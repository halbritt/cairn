package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
	"github.com/halbritt/cairn/localapi"
)

const machineHelp = `Cairn machines (one central API shared by several hosts)

Central host (edits the API identity configuration; values only, never prints tokens):
  machine provision --machine ID --upstream https://HOST[:PORT] --collection REPO --out FILE
    [--observer] [--allow-new-collection] [--identities PATH] [--restart]
  machine rotate --machine ID --upstream https://HOST[:PORT] --out FILE [--identities PATH] [--restart]
  machine revoke --machine ID [--identities PATH] [--restart]
  machine list [--identities PATH]

Joining host:
  machine enroll --file FILE [--no-service] [--keep-file]
  machine status
  machine unenroll (stop the relay and remove this host's tokens and machine.json)

provision and rotate write an owner-only enrollment file holding the new tokens.
Copy it privately to the joining host; enroll installs the existing token files,
records the canonical collection, installs cairn-relay.service and checks
connectivity, then deletes the file. The API loads identities at startup:
without --restart, restart cairn-api.service before the new tokens work.
See docs/multi-machine.md.`

const (
	enrollmentSchema    = "cairn.machine-enrollment/1"
	machineConfigSchema = "cairn.machine/1"
	maxIdentities       = 32
)

var machineIDPattern = regexp.MustCompile(`^[a-z][a-z0-9-]{0,62}$`)

type enrollmentProfile struct {
	Role      string `json:"role"`
	Principal string `json:"principal"`
	Token     string `json:"token"`
}

type enrollment struct {
	Schema       string              `json:"schema"`
	MachineID    string              `json:"machine_id"`
	Upstream     string              `json:"upstream"`
	Collection   string              `json:"collection"`
	CairnVersion string              `json:"cairn_version"`
	CreatedAt    string              `json:"created_at"`
	Profiles     []enrollmentProfile `json:"profiles"`
}

// configuredIdentity reads the fields machine operations depend on. Entries are
// otherwise preserved as the operator wrote them.
type configuredIdentity struct {
	TokenSHA256 string `json:"token_sha256"`
	Principal   string `json:"principal"`
	Repo        string `json:"repo"`
	Role        string `json:"role"`
	Destination string `json:"destination"`
	Remote      bool   `json:"remote"`
	MachineID   string `json:"machine_id"`
}

type identityEntry struct {
	raw   json.RawMessage
	value configuredIdentity
}

type machineProfile struct {
	Role      string `json:"role"`
	Principal string `json:"principal"`
	Repo      string `json:"repo"`
}

type machineSummary struct {
	MachineID string           `json:"machine_id"`
	Profiles  []machineProfile `json:"profiles"`
}

type machineChange struct {
	Operation       string           `json:"operation"`
	MachineID       string           `json:"machine_id"`
	Profiles        []machineProfile `json:"profiles"`
	Identities      string           `json:"identities"`
	Backup          string           `json:"backup"`
	Enrollment      string           `json:"enrollment,omitempty"`
	IdentityCount   int              `json:"identity_count"`
	RestartRequired bool             `json:"restart_required"`
	Restarted       bool             `json:"restarted"`
	Verified        []string         `json:"verified_roles,omitempty"`
	Next            string           `json:"next"`
}

func machineCommand(ctx context.Context, args []string) (any, error) {
	if len(args) == 0 || args[0] == "--help" || args[0] == "help" {
		return commandHelp(machineHelp), nil
	}
	switch args[0] {
	case "provision", "rotate", "revoke":
		return changeMachine(ctx, args[0], args[1:])
	case "list":
		return listMachines(args[1:])
	case "enroll":
		return enrollMachine(ctx, args[1:])
	case "unenroll":
		return unenrollMachine(ctx, args[1:])
	case "status":
		return machineStatus(ctx, args[1:])
	}
	return nil, invalid("unknown machine command; use machine --help")
}

func changeMachine(ctx context.Context, operation string, args []string) (machineChange, error) {
	result := machineChange{Operation: operation}
	directory, err := dataDirectory()
	if err != nil {
		return result, err
	}
	f := flags("machine " + operation)
	config := f.String("identities", filepath.Join(directory, "identities.json"), "API identity configuration")
	machine := f.String("machine", "", "stable machine ID")
	restart := f.Bool("restart", false, "restart cairn-api.service and verify the change")
	var upstream, collection, out *string
	var observer, newCollection *bool
	if operation != "revoke" {
		out = f.String("out", "", "new owner-only enrollment file")
	}
	if operation != "revoke" {
		upstream = f.String("upstream", "", "central HTTPS endpoint")
	}
	if operation == "provision" {
		collection = f.String("collection", "", "canonical collection (repository identity)")
		observer = f.Bool("observer", false, "also provision the host observer role")
		newCollection = f.Bool("allow-new-collection", false, "permit a collection no existing identity uses")
	}
	if err = f.Parse(args); err != nil {
		return result, invalid(err.Error())
	}
	if f.NArg() != 0 || !machineIDPattern.MatchString(*machine) {
		return result, invalid("machine " + operation + " requires --machine matching [a-z][a-z0-9-]{0,62} and no positional arguments")
	}
	result.MachineID = *machine
	var endpoint string
	if upstream != nil {
		if endpoint, err = normalizeUpstream(*upstream); err != nil {
			return result, err
		}
	}
	if operation == "provision" {
		if strings.TrimSpace(*collection) == "" || *collection == "*" || len(*collection) > 256 {
			return result, invalid("--collection must name the canonical collection (1-256 bytes, not *)")
		}
	}
	if out != nil {
		if *out == "" {
			return result, invalid("--out must name a new enrollment file")
		}
		if *out, err = filepath.Abs(*out); err != nil {
			return result, err
		}
		result.Enrollment = *out
	}
	if *config, err = filepath.Abs(*config); err != nil {
		return result, err
	}
	result.Identities = *config
	unlock, err := lockIdentities(filepath.Dir(*config))
	if err != nil {
		return result, err
	}
	defer unlock()
	entries, err := readIdentities(*config)
	if err != nil {
		return result, err
	}
	var owned []int
	for i, entry := range entries {
		if entry.value.MachineID == *machine {
			owned = append(owned, i)
		}
	}
	var issued *enrollment
	switch operation {
	case "provision":
		if len(owned) != 0 {
			return result, invalid("machine is already provisioned; use machine rotate or machine revoke")
		}
		known := false
		for _, entry := range entries {
			known = known || entry.value.Repo == *collection
		}
		if !known && !*newCollection {
			return result, invalid("no configured identity uses that collection; check the value or pass --allow-new-collection")
		}
		roles := []string{"agent"}
		if *observer {
			roles = append(roles, "observer")
		}
		issued = &enrollment{MachineID: *machine, Upstream: endpoint, Collection: *collection}
		for _, role := range roles {
			token, err := newToken()
			if err != nil {
				return result, err
			}
			identity := configuredIdentity{TokenSHA256: tokenDigest(token), Principal: machinePrincipal(*machine, role), Repo: *collection, Role: role, Destination: "hosted", Remote: true, MachineID: *machine}
			raw, err := json.Marshal(identity)
			if err != nil {
				return result, err
			}
			entries = append(entries, identityEntry{raw, identity})
			issued.Profiles = append(issued.Profiles, enrollmentProfile{Role: role, Principal: identity.Principal, Token: token})
		}
	case "rotate":
		if len(owned) == 0 {
			return result, &core.Error{Code: "NOT_FOUND", Message: "machine is not provisioned"}
		}
		issued = &enrollment{MachineID: *machine, Upstream: endpoint}
		for _, i := range owned {
			identity := entries[i].value
			if issued.Collection == "" {
				issued.Collection = identity.Repo
			}
			token, err := newToken()
			if err != nil {
				return result, err
			}
			if entries[i].raw, err = replaceDigest(entries[i].raw, tokenDigest(token)); err != nil {
				return result, err
			}
			entries[i].value.TokenSHA256 = tokenDigest(token)
			issued.Profiles = append(issued.Profiles, enrollmentProfile{Role: identity.Role, Principal: identity.Principal, Token: token})
		}
	case "revoke":
		if len(owned) == 0 {
			return result, &core.Error{Code: "NOT_FOUND", Message: "machine is not provisioned"}
		}
		for _, i := range owned {
			identity := entries[i].value
			result.Profiles = append(result.Profiles, machineProfile{identity.Role, identity.Principal, identity.Repo})
		}
		kept := entries[:0]
		for _, entry := range entries {
			if entry.value.MachineID != *machine {
				kept = append(kept, entry)
			}
		}
		entries = kept
	}
	for _, entry := range entries {
		if operation != "revoke" && entry.value.MachineID == *machine {
			result.Profiles = append(result.Profiles, machineProfile{entry.value.Role, entry.value.Principal, entry.value.Repo})
		}
	}
	if err = validateIdentities(entries); err != nil {
		return result, err
	}
	result.IdentityCount = len(entries)
	if issued != nil {
		issued.Schema = enrollmentSchema
		issued.CairnVersion = buildinfo.Read().Label()
		issued.CreatedAt = time.Now().UTC().Format(time.RFC3339)
		if err = writeEnrollment(*out, issued); err != nil {
			return result, err
		}
	}
	if result.Backup, err = writeIdentities(*config, entries, operation, *machine); err != nil {
		if issued != nil {
			_ = os.Remove(*out) // its tokens were never configured
		}
		return result, err
	}
	result.RestartRequired = true
	result.Next = "restart cairn-api.service to load the change"
	if !*restart {
		return result, nil
	}
	if err = restartAPI(ctx); err != nil {
		return result, restartFailure(result, err)
	}
	result.Restarted, result.RestartRequired = true, false
	result.Next = ""
	if issued != nil {
		socket := filepath.Join(filepath.Dir(*config), "api.sock")
		for _, profile := range issued.Profiles {
			if _, err = versionOverSocket(ctx, socket, profile.Token, 20*time.Second); err != nil {
				return result, restartFailure(result, fmt.Errorf("%s token was not accepted after restart: %w", profile.Role, err))
			}
			result.Verified = append(result.Verified, profile.Role)
		}
	}
	if issued != nil {
		result.Next = "copy the enrollment file privately to the machine and run cairn machine enroll --file FILE"
	}
	return result, nil
}

func restartFailure(result machineChange, err error) error {
	return &core.Error{Code: "INSTALL_FAILED", Message: fmt.Sprintf("identity change written but the API did not come back cleanly (%v); to roll back: install -m 600 %s %s && systemctl --user restart cairn-api.service", err, result.Backup, result.Identities)}
}

func listMachines(args []string) ([]machineSummary, error) {
	directory, err := dataDirectory()
	if err != nil {
		return nil, err
	}
	f := flags("machine list")
	config := f.String("identities", filepath.Join(directory, "identities.json"), "API identity configuration")
	if err = f.Parse(args); err != nil || f.NArg() != 0 {
		return nil, invalid("machine list accepts only --identities")
	}
	entries, err := readIdentities(*config)
	if err != nil {
		return nil, err
	}
	machines := map[string]*machineSummary{}
	for _, entry := range entries {
		id := entry.value.MachineID
		if id == "" {
			continue
		}
		if machines[id] == nil {
			machines[id] = &machineSummary{MachineID: id}
		}
		machines[id].Profiles = append(machines[id].Profiles, machineProfile{entry.value.Role, entry.value.Principal, entry.value.Repo})
	}
	result := []machineSummary{}
	for _, summary := range machines {
		result = append(result, *summary)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].MachineID < result[j].MachineID })
	return result, nil
}

func machinePrincipal(machine, role string) string { return "machine:" + machine + "/" + role }

func normalizeUpstream(value string) (string, error) {
	parsed, err := url.Parse(value)
	if err != nil || localapi.ValidateUpstream(value) != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || (parsed.Path != "" && parsed.Path != "/") || parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", invalid("--upstream must be an https://HOST[:PORT] origin without path, query or credentials")
	}
	return "https://" + parsed.Host, nil
}

func newToken() (string, error) {
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(secret), nil
}

func tokenDigest(token string) string {
	digest := sha256.Sum256([]byte(token))
	return hex.EncodeToString(digest[:])
}

var identityLockWait = 10 * time.Second

// lockIdentities shares scripts/provision-event-profiles.py's advisory lock,
// waiting a bounded time for another provisioning run.
func lockIdentities(directory string) (func(), error) {
	path := filepath.Join(directory, ".identities.lock")
	fd, err := syscall.Open(path, syscall.O_WRONLY|syscall.O_CREAT|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0600)
	if err != nil {
		return nil, installationError("identity lock", err)
	}
	file := os.NewFile(uintptr(fd), path)
	var stat syscall.Stat_t
	if err = syscall.Fstat(fd, &stat); err != nil || stat.Mode&syscall.S_IFMT != syscall.S_IFREG || stat.Uid != uint32(os.Geteuid()) {
		file.Close()
		return nil, invalid("identity lock must be a regular file owned by the current user")
	}
	deadline := time.Now().Add(identityLockWait)
	for {
		err = syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			return func() { file.Close() }, nil
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) || time.Now().After(deadline) {
			file.Close()
			return nil, &core.Error{Code: "INSTALL_FAILED", Message: "another identity change holds " + path + "; retry when it finishes"}
		}
		time.Sleep(100 * time.Millisecond)
	}
}

// readOwnedFile reads a credential or configuration file without following a
// final symbolic link. It must be a regular file owned by the current user and,
// when ownerOnly, carry no group or other permissions. A missing file returns
// an error matching os.ErrNotExist.
func readOwnedFile(path, label string, ownerOnly bool, limit int64) ([]byte, error) {
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if err != nil {
		if errors.Is(err, syscall.ENOENT) {
			return nil, &os.PathError{Op: "open", Path: path, Err: err}
		}
		if errors.Is(err, syscall.ELOOP) {
			return nil, invalid(label + " is a symbolic link; replace it with a regular file")
		}
		return nil, installationError(label, err)
	}
	file := os.NewFile(uintptr(fd), path)
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, installationError(label, err)
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() || stat.Uid != uint32(os.Geteuid()) || (ownerOnly && info.Mode().Perm()&0077 != 0) {
		if ownerOnly {
			return nil, invalid(label + " must be a regular owner-only (0600) file owned by the current user")
		}
		return nil, invalid(label + " must be a regular file owned by the current user")
	}
	body, err := io.ReadAll(io.LimitReader(file, limit+1))
	if err != nil {
		return nil, installationError(label, err)
	}
	if int64(len(body)) > limit {
		return nil, invalid(fmt.Sprintf("%s exceeds %d KiB", label, limit/1024))
	}
	return body, nil
}

// decodeOne decodes exactly one JSON value with no unknown fields or trailing data.
func decodeOne(body []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		return errors.New("trailing data")
	}
	return nil
}

func readIdentities(path string) ([]identityEntry, error) {
	body, err := readOwnedFile(path, "identity configuration", true, 128*1024) // cairn serve's bound
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, installationError("identity configuration", err)
		}
		return nil, err
	}
	var raws []json.RawMessage
	if err = json.Unmarshal(body, &raws); err != nil {
		return nil, invalid("identity configuration is not one JSON array")
	}
	entries := make([]identityEntry, 0, len(raws))
	for _, raw := range raws {
		var value configuredIdentity
		if err = json.Unmarshal(raw, &value); err != nil {
			return nil, invalid("identity configuration has an invalid entry")
		}
		entries = append(entries, identityEntry{raw, value})
	}
	return entries, validateIdentities(entries)
}

// validateIdentities adds operator-facing explanations to the API's own
// startup validation, so a change is refused here rather than by a failed restart.
func validateIdentities(entries []identityEntry) error {
	if len(entries) == 0 {
		return invalid("identity configuration would be empty")
	}
	if len(entries) > maxIdentities {
		return invalid(fmt.Sprintf("the API accepts at most %d identities; revoke unused machines or profiles first", maxIdentities))
	}
	principals, digests := map[string]bool{}, map[string]bool{}
	identities := make([]localapi.Identity, 0, len(entries))
	for _, entry := range entries {
		identity := entry.value
		if principals[identity.Principal] {
			return invalid("duplicate principal in identity configuration: " + identity.Principal)
		}
		if digests[identity.TokenSHA256] {
			return invalid("duplicate token digest in identity configuration")
		}
		principals[identity.Principal], digests[identity.TokenSHA256] = true, true
		if identity.Remote != (identity.MachineID != "") {
			return invalid("remote identities require machine_id, and only remote identities may set it: " + identity.Principal)
		}
		if identity.Remote && (!machineIDPattern.MatchString(identity.MachineID) || identity.Destination != "hosted" || identity.Principal != machinePrincipal(identity.MachineID, identity.Role)) {
			return invalid("remote identity must be hosted and named machine:ID/ROLE: " + identity.Principal)
		}
		if !identity.Remote && strings.HasPrefix(identity.Principal, "machine:") {
			return invalid("machine:* principals are reserved for remote identities: " + identity.Principal)
		}
		identities = append(identities, localapi.Identity{TokenSHA256: identity.TokenSHA256, Principal: identity.Principal, Repo: identity.Repo, Role: identity.Role, Destination: identity.Destination, Remote: identity.Remote, MachineID: identity.MachineID})
	}
	if err := localapi.ValidateIdentities(identities); err != nil {
		return invalid("identity configuration would not load: " + err.Error())
	}
	return nil
}

func replaceDigest(raw json.RawMessage, digest string) (json.RawMessage, error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		return nil, invalid("identity configuration has an invalid entry")
	}
	encoded, _ := json.Marshal(digest)
	fields["token_sha256"] = encoded
	return json.Marshal(fields)
}

func writeIdentities(path string, entries []identityEntry, operation, machine string) (string, error) {
	previous, err := os.ReadFile(path)
	if err != nil {
		return "", installationError("identity configuration", err)
	}
	backup := fmt.Sprintf("%s.before-%s-%s-%s", path, operation, machine, time.Now().UTC().Format("20060102T150405Z"))
	if err = writeNewPrivateFile(backup, previous); err != nil {
		return "", installationError("identity backup", err)
	}
	raws := make([]json.RawMessage, len(entries))
	for i, entry := range entries {
		raws[i] = entry.raw
	}
	body, err := json.MarshalIndent(raws, "", "  ")
	if err != nil {
		return "", err
	}
	if err = installFile(path, append(body, '\n')); err != nil {
		return "", installationError("identity configuration", err)
	}
	return backup, nil
}

// writeNewPrivateFile never replaces an existing path.
func writeNewPrivateFile(path string, body []byte) error {
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	if _, err = file.Write(body); err == nil {
		err = file.Sync()
	}
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		_ = os.Remove(path)
	}
	return err
}

func writeEnrollment(path string, value *enrollment) error {
	body, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	if err = writeNewPrivateFile(path, append(body, '\n')); err != nil {
		if errors.Is(err, os.ErrExist) {
			return invalid("enrollment file already exists; choose a new --out path")
		}
		return installationError("enrollment file", err)
	}
	return nil
}

var systemctlProgram = "systemctl"

func systemctl(ctx context.Context, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	return exec.CommandContext(ctx, systemctlProgram, append([]string{"--user"}, args...)...).CombinedOutput()
}

func restartAPI(ctx context.Context) error {
	if output, err := systemctl(ctx, "restart", "cairn-api.service"); err != nil {
		return fmt.Errorf("systemctl restart: %v: %s", err, bytes.TrimSpace(output))
	}
	deadline := time.Now().Add(20 * time.Second)
	for {
		output, err := systemctl(ctx, "is-active", "cairn-api.service")
		if err == nil && strings.TrimSpace(string(output)) == "active" {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("cairn-api.service is %s", strings.TrimSpace(string(output)))
		}
		time.Sleep(500 * time.Millisecond)
	}
}

// versionOverSocket proves a token authenticates without writing it to disk.
// It retries only this read-only call while the listener starts.
func versionOverSocket(ctx context.Context, socket, token string, wait time.Duration) (buildinfo.Info, error) {
	var info buildinfo.Info
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", socket)
	}}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 35 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	deadline := time.Now().Add(wait)
	for {
		err := func() error {
			req, err := http.NewRequestWithContext(ctx, "POST", "http://cairn/v1/version", strings.NewReader("{}"))
			if err != nil {
				return err
			}
			req.Header.Set("Authorization", "Bearer "+token)
			req.Header.Set("Content-Type", "application/json")
			reply, err := client.Do(req)
			if err != nil {
				return err
			}
			defer reply.Body.Close()
			var envelope struct {
				Schema string         `json:"schema"`
				OK     bool           `json:"ok"`
				Status string         `json:"status"`
				Data   buildinfo.Info `json:"data"`
			}
			if err = json.NewDecoder(io.LimitReader(reply.Body, 64*1024)).Decode(&envelope); err != nil || envelope.Schema != "cairn.response/1" {
				return &core.Error{Code: "API_CONNECTION_FAILED", Message: fmt.Sprintf("not a Cairn API reply (HTTP %d)", reply.StatusCode)}
			}
			if reply.StatusCode != http.StatusOK || !envelope.OK {
				if envelope.OK || envelope.Status == "" || envelope.Status == "OK" {
					return &core.Error{Code: "API_CONNECTION_FAILED", Message: fmt.Sprintf("inconsistent API reply (HTTP %d)", reply.StatusCode)}
				}
				return &core.Error{Code: envelope.Status, Message: "version call refused: " + envelope.Status}
			}
			info = envelope.Data
			return nil
		}()
		var refusal *core.Error
		if err == nil || errors.As(err, &refusal) || time.Now().After(deadline) {
			return info, err
		}
		select {
		case <-ctx.Done():
			return info, ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
}
