// Package procpeer retains the Go process measurement and bounded local frame
// policies used by the Claude channel bridge.
package procpeer

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

// ProcessRef identifies one operating-system process across PID reuse.
type ProcessRef struct {
	PID   int    `json:"pid"`
	Start uint64 `json:"start"`
	Boot  string `json:"boot"`
}

// ProcessRefOf measures a live process, or returns an error when it is gone.
func ProcessRefOf(pid int) (ProcessRef, error) {
	raw, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return ProcessRef{}, err
	}
	text := string(raw)
	fields := strings.Fields(text[strings.LastIndexByte(text, ')')+1:])
	if len(fields) < 20 {
		return ProcessRef{}, fmt.Errorf("short /proc/%d/stat", pid)
	}
	start, err := strconv.ParseUint(fields[19], 10, 64)
	if err != nil {
		return ProcessRef{}, err
	}
	boot, err := bootID()
	if err != nil {
		return ProcessRef{}, err
	}
	return ProcessRef{PID: pid, Start: start, Boot: boot}, nil
}

func bootID() (string, error) {
	raw, err := os.ReadFile("/proc/sys/kernel/random/boot_id")
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(raw)), nil
}

// Live retains measurement equality, including a measurable zombie until reaped.
// It does not apply the separate Python Z/X exclusion policy.
func Live(ref ProcessRef) bool {
	current, err := ProcessRefOf(ref.PID)
	return err == nil && current == ref
}
