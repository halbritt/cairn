package coordination

import (
	"bytes"
	"math"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// unitReceiptPass applies the existing receipt selection predicates to this
// census's tracked bytes. It never sets CompatibilityContext.receiptVerified
// and never grants authority to a cached digest or caller-supplied flag.
// Keep these predicates in parity with the generic validator; only the two
// disk read sites differ, including the same explicit absent-memory selection.
type unitReceiptPass struct {
	*CompatibilityContext
	read *watchCensusRead
}

func (r *watchCensusRead) verifyUnitReceipt(selection *CompatibilityContext, receipt *value.Value) error {
	return (&unitReceiptPass{CompatibilityContext: selection, read: r}).validateReceiptSelection(receipt)
}

func (c *unitReceiptPass) validateReceiptSelection(receipt *value.Value) error {
	role := c.role()
	if (role != "memory" && role != "coordination") || !compatibilityCanonical(c.ConfigPath) || !compatibilityCanonical(c.LaunchRoot) || !compatibilityCanonical(c.shell()) {
		return preflightError("selection")
	}
	if math.IsNaN(c.HookTimeout) || math.IsInf(c.HookTimeout, 0) {
		return preflightError("timeout")
	}
	if role == "memory" {
		if c.HookTimeout != 13 && c.HookTimeout != 150 {
			return preflightError("timeout")
		}
	} else if c.HookTimeout != 2 && c.HookTimeout != 15 {
		return preflightError("timeout")
	}
	if goOnlyReceipt(receipt) {
		return goOnlyReceiptRules(role, c.ConfigPath, c.Entrypoint, receipt, func(path, sha string) error {
			if _, err := c.read.pin(WatchCensusPin{path, sha}, true); err != nil {
				return preflightError("binary pin")
			}
			return nil
		})
	}
	if !exactReceiptObject(receipt, "schema", "source_revision", "snapshot_root", "members", "bootstrap", "loader", "roles", "aliases", "binary", "binding", "memory_config", "coordination_configs") {
		return preflightError("receipt")
	}
	if !value.SameText(receipt.Get("schema"), "cairn.lifecycle-compatibility/1") || !value.SameText(receipt.Get("bootstrap"), "lifecycle_compatibility.py") || !value.SameText(receipt.Get("loader"), "lifecycle_bundle.py") || !canonicalReceiptRunes(receipt.Get("snapshot_root")) {
		return preflightError("receipt")
	}
	return c.validateReceiptClosureAndPins(role, receipt)
}

func (c *unitReceiptPass) validateReceiptClosureAndPins(role string, receipt *value.Value) error {
	srcRev := receipt.Get("source_revision")
	if srcRev == nil || srcRev.Kind != value.String || hasSurrogateRunes(srcRev.String) {
		return preflightError("provenance")
	}
	roles := receipt.Get("roles")
	if !exactReceiptObject(roles, "memory", "coordination", "inbox_recall", "inbox_binding") {
		return preflightError("roles")
	}
	for _, rName := range []string{"memory", "coordination", "inbox_recall", "inbox_binding"} {
		if !value.SameText(roles.Get(rName), rName+".py") {
			return preflightError("roles")
		}
	}
	aliases := receipt.Get("aliases")
	if aliases == nil || aliases.Kind != value.Object {
		return preflightError("roles")
	}
	baseNames := make(map[string]bool, len(CompatibilityClosureMembers))
	expectedClosure := make(map[string]bool, len(CompatibilityClosureMembers)+len(aliases.Members))
	for _, name := range CompatibilityClosureMembers {
		baseNames[name] = true
		expectedClosure[name] = true
	}
	for _, m := range aliases.Members {
		if len(m.Key) == 0 || hasSurrogateRunes(m.Key) {
			return preflightError("alias")
		}
		name := string(m.Key)
		if name == "." || name == ".." || strings.ContainsRune(name, '/') || strings.ContainsRune(name, '\\') || strings.ContainsRune(name, 0) || baseNames[name] {
			return preflightError("alias")
		}
		if m.Value == nil || m.Value.Kind != value.String || (!value.SameText(m.Value, "memory.py") && !value.SameText(m.Value, "coordination.py")) {
			return preflightError("alias")
		}
		expectedClosure[name] = true
	}
	members := receipt.Get("members")
	if members == nil || members.Kind != value.Object || len(members.Members) != len(expectedClosure) {
		return preflightError("closure")
	}
	for _, m := range members.Members {
		if hasSurrogateRunes(m.Key) || !expectedClosure[string(m.Key)] {
			return preflightError("closure")
		}
	}
	if !validReceiptDescriptor(receipt.Get("binary"), "path", "sha256") || !validMemoryDescriptor(receipt.Get("memory_config")) || !validReceiptDescriptor(receipt.Get("binding"), "path", "sha256", "schema") {
		return preflightError("descriptor")
	}
	coordList := receipt.Get("coordination_configs")
	if !value.SameText(receipt.Get("binding").Get("schema"), "cairn.inbox-recall-binding/3") || coordList == nil || coordList.Kind != value.Array || len(coordList.Items) < 1 || len(coordList.Items) > 8 {
		return preflightError("binding")
	}
	for _, item := range coordList.Items {
		if !validReceiptDescriptor(item, "path", "sha256") {
			return preflightError("descriptor")
		}
	}
	return c.validateReceiptRoleAndDiskPins(role, receipt, aliases, members, coordList)
}

func (c *unitReceiptPass) validateReceiptRoleAndDiskPins(role string, receipt, aliases, members, coordList *value.Value) error {
	if role == "memory" {
		if memoryAbsent(receipt.Get("memory_config")) || !value.SameText(receipt.Get("memory_config").Get("path"), c.ConfigPath) {
			return preflightError("config role")
		}
	} else {
		matched := false
		for _, item := range coordList.Items {
			if value.SameText(item.Get("path"), c.ConfigPath) {
				matched = true
				break
			}
		}
		if !matched {
			return preflightError("config role")
		}
	}
	wantEntry := role + ".py"
	if c.Entrypoint != wantEntry && !value.SameText(aliases.Get(c.Entrypoint), wantEntry) {
		return preflightError("entrypoint")
	}
	snapshotRootVal := receipt.Get("snapshot_root")
	if hasSurrogateRunes(snapshotRootVal.String) {
		return preflightError("receipt")
	}
	snapshotRoot := string(snapshotRootVal.String)
	sources := make(map[string][]byte, len(members.Members))
	hashes := make(map[string]string, len(members.Members))
	for _, m := range members.Members {
		name := string(m.Key)
		d := m.Value
		if !exactReceiptObject(d, "sha256", "bytes") {
			return preflightError("member")
		}
		bVal := d.Get("bytes")
		sVal := d.Get("sha256")
		if bVal == nil || bVal.Kind != value.Int || sVal == nil || sVal.Kind != value.String || hasSurrogateRunes(sVal.String) || !hexText(sVal.String, 64) {
			return preflightError("member")
		}
		nBytes, err := strconv.ParseInt(bVal.Integer, 10, 64)
		if err != nil || nBytes < 0 || nBytes > 2097152 {
			return preflightError("member")
		}
		data, err := c.read.pinLimit(WatchCensusPin{sourceJoin(snapshotRoot, name), string(sVal.String)}, false, 2097152)
		if err != nil || int64(len(data)) != nBytes {
			return preflightError("member pin")
		}
		sources[name] = data
		hashes[name] = c.read.files[sourceJoin(snapshotRoot, name)]
	}
	return c.validateSnapshotAndBinaryPins(snapshotRoot, receipt, aliases, sources, hashes)
}

func (c *unitReceiptPass) validateSnapshotAndBinaryPins(snapshotRoot string, receipt, aliases *value.Value, sources map[string][]byte, hashes map[string]string) error {
	identity := CompatibilitySnapshotIdentity(hashes)
	if filepath.Base(snapshotRoot) != identity {
		return preflightError("snapshot")
	}
	for _, m := range aliases.Members {
		name := string(m.Key)
		target := string(m.Value.String)
		if !bytes.Equal(sources[name], sources[target]) {
			return preflightError("alias pin")
		}
	}
	binPathVal := receipt.Get("binary").Get("path")
	binSHAVal := receipt.Get("binary").Get("sha256")
	if hasSurrogateRunes(binPathVal.String) {
		return preflightError("binary pin")
	}
	_, err := c.read.pin(WatchCensusPin{string(binPathVal.String), string(binSHAVal.String)}, true)
	if err != nil {
		return preflightError("binary pin")
	}
	return nil
}
