package mcpapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/halbritt/cairn/core"
)

const (
	maxAncestorHops          = 32
	maxCoordinationStateSize = 1024 * 1024
)

type coordinationSessionState struct {
	Schema  string `json:"schema"`
	Retired any    `json:"retired"`
	Process struct {
		PID   int    `json:"pid"`
		Start uint64 `json:"start"`
		Boot  string `json:"boot"`
	} `json:"process"`
	Agent struct {
		AgentID         string `json:"agent_id"`
		ExecutionID     string `json:"execution_id"`
		NativeSessionID string `json:"native_session_id"`
	} `json:"agent"`
}

func defaultHostAncestors() ([]HostProcess, error) {
	return hostAncestorsAt("/proc", "/proc/sys/kernel/random/boot_id", os.Getppid())
}

func hostAncestorsAt(procRoot, bootIDPath string, startPID int) ([]HostProcess, error) {
	rawBoot, err := os.ReadFile(bootIDPath)
	if err != nil {
		return nil, err
	}
	boot := strings.TrimSpace(string(rawBoot))
	if boot == "" {
		return nil, errors.New("empty kernel boot_id")
	}
	seen := make(map[int]bool, maxAncestorHops)
	ancestors := make([]HostProcess, 0, 8)
	pid := startPID
	for hop := 0; hop < maxAncestorHops && pid > 1 && !seen[pid]; hop++ {
		seen[pid] = true
		raw, err := os.ReadFile(filepath.Join(procRoot, strconv.Itoa(pid), "stat"))
		if err != nil {
			break
		}
		end := bytes.LastIndexByte(raw, ')')
		if end < 0 {
			break
		}
		fields := strings.Fields(string(raw[end+1:]))
		if len(fields) < 20 || fields[0] == "Z" {
			break
		}
		ppid, err1 := strconv.Atoi(fields[1])
		start, err2 := strconv.ParseUint(fields[19], 10, 64)
		if err1 != nil || err2 != nil {
			break
		}
		ancestors = append(ancestors, HostProcess{PID: pid, Start: start, Boot: boot})
		pid = ppid
	}
	return ancestors, nil
}

func (t messagingTools) selfByHostProcess(ctx context.Context) (core.AgentSessionRef, *core.AgentInstance, error) {
	unknown := func() (core.AgentSessionRef, *core.AgentInstance, error) {
		return core.AgentSessionRef{}, nil, messagingError("IDENTITY_UNKNOWN", "this MCP process has no native session ID and no unique online session matches its host process; pass agent_id and execution_id from your Cairn session context")
	}
	root := t.config.CoordinationStateRoot
	if root == "" {
		return unknown()
	}
	rootInfo, err := os.Lstat(root)
	if err != nil || !rootInfo.IsDir() || rootInfo.Mode()&os.ModeSymlink != 0 {
		return unknown()
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return unknown()
	}
	bindings := make([]string, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() && entry.Type()&os.ModeSymlink == 0 {
			bindings = append(bindings, entry.Name())
		}
	}
	if len(bindings) == 0 {
		return unknown()
	}
	readAncestors := t.config.HostAncestors
	if readAncestors == nil {
		readAncestors = defaultHostAncestors
	}
	ancestors, err := readAncestors()
	if err != nil || len(ancestors) == 0 {
		return unknown()
	}
	ancestorDepth := make(map[HostProcess]int, len(ancestors))
	for depth, proc := range ancestors {
		if proc.PID <= 1 || proc.Boot == "" {
			continue
		}
		if _, exists := ancestorDepth[proc]; !exists {
			ancestorDepth[proc] = depth
		}
	}
	if len(ancestorDepth) == 0 {
		return unknown()
	}
	bestDepth := len(ancestors) + 1
	var bestMatches []core.AgentInstance
	matchedIDs := map[string]bool{}
	query := core.AgentDirectoryQuery{Limit: 100}
	for page := 0; page < messagingDirectoryPages; page++ {
		result, err := t.directory(ctx, query)
		if err != nil {
			return core.AgentSessionRef{}, nil, err
		}
		for _, agent := range result.Agents {
			if !agent.Online || agent.Stopped || agent.NativeSessionID == "" {
				continue
			}
			if depth, ok := matchCoordinationSession(root, bindings, agent, ancestorDepth); ok {
				if depth < bestDepth {
					bestDepth = depth
					bestMatches = []core.AgentInstance{agent}
					matchedIDs = map[string]bool{agent.AgentID: true}
				} else if depth == bestDepth && !matchedIDs[agent.AgentID] {
					bestMatches = append(bestMatches, agent)
					matchedIDs[agent.AgentID] = true
				}
			}
		}
		if !result.More || result.NextAfter <= query.After {
			break
		}
		query.After = result.NextAfter
	}
	switch len(bestMatches) {
	case 1:
		found := bestMatches[0]
		return core.AgentSessionRef{AgentID: found.AgentID, ExecutionID: found.ExecutionID}, &found, nil
	case 0:
		return unknown()
	default:
		return core.AgentSessionRef{}, nil, messagingError("IDENTITY_AMBIGUOUS", "several online sessions share this host process; pass agent_id and execution_id from your Cairn session context")
	}
}

func matchCoordinationSession(root string, bindings []string, agent core.AgentInstance, ancestorDepth map[HostProcess]int) (int, bool) {
	best := 0
	found := false
	for _, binding := range bindings {
		digest := sha256.Sum256([]byte(binding + "\x00" + agent.NativeSessionID))
		path := filepath.Join(root, binding, hex.EncodeToString(digest[:])+".json")
		info, err := os.Lstat(path)
		if err != nil || !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > maxCoordinationStateSize {
			continue
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var state coordinationSessionState
		if err := json.Unmarshal(raw, &state); err != nil {
			continue
		}
		if state.Schema != "cairn.native-session/1" || (state.Retired != nil && state.Retired != false) {
			continue
		}
		if state.Agent.AgentID != agent.AgentID || state.Agent.ExecutionID != agent.ExecutionID || state.Agent.NativeSessionID != agent.NativeSessionID {
			continue
		}
		depth, ok := ancestorDepth[HostProcess{PID: state.Process.PID, Start: state.Process.Start, Boot: state.Process.Boot}]
		if ok && (!found || depth < best) {
			best = depth
			found = true
		}
	}
	return best, found
}
