package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
)

type ownedUnitInvocation struct {
	Program string   `json:"program"`
	SHA256  string   `json:"sha256"`
	Args    []string `json:"args"`
	Input   string   `json:"input"`
	Result  string   `json:"result"`
}

type ownedUnitResult struct {
	Exit   int    `json:"exit"`
	Stdout string `json:"stdout"`
	Stderr string `json:"stderr"`
	PID    int    `json:"pid"`
	PGID   int    `json:"pgid"`
}

func decodeOwnedUnitInvocation(raw []byte) (ownedUnitInvocation, error) {
	var plan ownedUnitInvocation
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&plan); err != nil {
		return plan, err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return plan, errors.New("trailing fixture input")
		}
		return plan, err
	}
	return plan, nil
}

func runOwnedUnitProgram(plan ownedUnitInvocation) (ownedUnitResult, error) {
	info, err := os.Lstat(plan.Program)
	if err != nil || !filepath.IsAbs(plan.Program) || !info.Mode().IsRegular() || info.Mode().Perm()&022 != 0 {
		return ownedUnitResult{}, errors.New("pinned regular fixture program required")
	}
	image, err := os.ReadFile(plan.Program)
	if err != nil {
		return ownedUnitResult{}, err
	}
	sum := sha256.Sum256(image)
	if hex.EncodeToString(sum[:]) != plan.SHA256 {
		return ownedUnitResult{}, errors.New("fixture program image changed before use")
	}
	var output, diagnostic bytes.Buffer
	command := exec.Command(plan.Program, plan.Args...)
	command.Stdin, command.Stdout, command.Stderr = bytes.NewBufferString(plan.Input), &output, &diagnostic
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := command.Start(); err != nil {
		return ownedUnitResult{}, err
	}
	pid := command.Process.Pid
	if pid <= 1 {
		return ownedUnitResult{}, errors.New("unsafe owned callback PID")
	}
	group, err := syscall.Getpgid(pid)
	if err != nil {
		command.Process.Kill()
		command.Wait()
		return ownedUnitResult{}, err
	}
	if group <= 1 {
		command.Process.Kill()
		command.Wait()
		return ownedUnitResult{}, errors.New("unsafe owned callback group")
	}
	if err := command.Wait(); err != nil {
		var exited *exec.ExitError
		if !errors.As(err, &exited) {
			return ownedUnitResult{}, err
		}
	}
	return ownedUnitResult{command.ProcessState.ExitCode(), output.String(), diagnostic.String(), pid, group}, nil
}

// The complete-unit driver uses the real dispatcher and configured API. It
// supplies no replacement API responses, state fields, journals or identities.
func TestR06OwnedUnitFixtureProcess(t *testing.T) {
	path := os.Getenv("CAIRN_R06_OWNED_UNIT_PLAN")
	if path == "" {
		t.Skip("private owned-unit helper; selected only by its disposable driver")
	}
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm() != 0600 || info.Sys().(*syscall.Stat_t).Uid != uint32(os.Getuid()) {
		t.Fatal("owned private helper plan required", err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	plan, err := decodeOwnedUnitInvocation(raw)
	if err != nil {
		t.Fatal(err)
	}
	if !filepath.IsAbs(plan.Result) || filepath.Dir(plan.Result) != filepath.Dir(path) {
		t.Fatal("unsafe helper result path")
	}
	result, err := runOwnedUnitProgram(plan)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	file, err := os.OpenFile(plan.Result, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.Write(append(encoded, '\n')); err != nil {
		file.Close()
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stdout.WriteString(result.Stdout); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stderr.WriteString(result.Stderr); err != nil {
		t.Fatal(err)
	}
}

func TestOwnedUnitFixtureRejectsAmbiguousInput(t *testing.T) {
	for _, input := range []string{`{"args":[],"authenticated":true}`, `{} {}`} {
		if _, err := decodeOwnedUnitInvocation([]byte(input)); err == nil {
			t.Fatal("unknown authority or trailing input accepted", input)
		}
	}
}

func TestOwnedUnitFixtureUsesProductRefusal(t *testing.T) {
	var output, diagnostic bytes.Buffer
	code := run([]string{"unit-control", "enable"}, bytes.NewBuffer(nil), &output, &diagnostic)
	if code == 0 || output.Len() == 0 && diagnostic.Len() == 0 {
		t.Fatalf("unsupported activation did not return the real product refusal: %d %s %s", code, output.String(), diagnostic.String())
	}
}

func TestOwnedUnitFixtureRejectsChangedProgram(t *testing.T) {
	path := filepath.Join(t.TempDir(), "unselected")
	if err := os.WriteFile(path, []byte("not an executable"), 0500); err != nil {
		t.Fatal(err)
	}
	if _, err := runOwnedUnitProgram(ownedUnitInvocation{Program: path, SHA256: "wrong"}); err == nil || err.Error() != "fixture program image changed before use" {
		t.Fatal("changed image reached execution", err)
	}
}
