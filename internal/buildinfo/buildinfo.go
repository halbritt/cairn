// Package buildinfo reports a small, configuration-free executable identity.
package buildinfo

import (
	"regexp"
	"runtime"
	"runtime/debug"
	"strconv"
	"time"
)

type Info struct {
	Schema        string `json:"schema"`
	GoVersion     string `json:"go_version"`
	ModuleVersion string `json:"module_version,omitempty"`
	VCS           string `json:"vcs,omitempty"`
	Revision      string `json:"vcs_revision,omitempty"`
	Time          string `json:"vcs_time,omitempty"`
	Modified      *bool  `json:"vcs_modified"`
}

func Read() Info {
	info, _ := debug.ReadBuildInfo()
	return fromBuildInfo(info)
}

func fromBuildInfo(info *debug.BuildInfo) Info {
	result := Info{Schema: "cairn.build/1", GoVersion: runtime.Version()}
	if info == nil {
		return result
	}
	result.GoVersion, result.ModuleVersion = info.GoVersion, info.Main.Version
	for _, setting := range info.Settings {
		switch setting.Key {
		case "vcs":
			result.VCS = setting.Value
		case "vcs.revision":
			result.Revision = setting.Value
		case "vcs.time":
			result.Time = setting.Value
		case "vcs.modified":
			if modified, err := strconv.ParseBool(setting.Value); err == nil {
				result.Modified = &modified
			}
		}
	}
	return result
}

// Label supplies the MCP implementation version, not a protocol or capability.
func (i Info) Label() string {
	if i.Revision != "" {
		if i.Modified == nil {
			return i.Revision + "-unknown"
		}
		if *i.Modified {
			return i.Revision + "-modified"
		}
		return i.Revision
	}
	if i.ModuleVersion != "" && i.ModuleVersion != "(devel)" {
		return i.ModuleVersion
	}
	return "unknown"
}

var (
	goVersionPattern     = regexp.MustCompile(`^(devel )?go[0-9][A-Za-z0-9._+-]*( [A-Za-z0-9:+ -]+)?$`)
	moduleVersionPattern = regexp.MustCompile(`^(\(devel\)|v[0-9][A-Za-z0-9.+-]*)$`)
	revisionPattern      = regexp.MustCompile(`^([0-9a-f]{40}|[0-9a-f]{64})$`)
	svnRevisionPattern   = regexp.MustCompile(`^[0-9]{1,20}$`)
)

// Valid reports whether a build object that crossed a trust boundary is
// well-formed and bounded. Peer metadata is a declaration, not trusted
// arbitrary text to relay onward: a malformed identity is refused as a whole,
// without reflecting its contents. A missing revision or a null modified flag
// is valid and stays unknown.
func (i Info) Valid() bool {
	if i.Schema != "cairn.build/1" || len(i.GoVersion) > 128 || !goVersionPattern.MatchString(i.GoVersion) {
		return false
	}
	if i.ModuleVersion != "" && (len(i.ModuleVersion) > 128 || !moduleVersionPattern.MatchString(i.ModuleVersion)) {
		return false
	}
	if i.VCS != "" && i.VCS != "git" && i.VCS != "hg" && i.VCS != "svn" && i.VCS != "bzr" && i.VCS != "fossil" {
		return false
	}
	if i.Revision != "" {
		valid := revisionPattern.MatchString(i.Revision)
		if i.VCS == "svn" {
			valid = svnRevisionPattern.MatchString(i.Revision)
		}
		if !valid {
			return false
		}
	}
	if i.Time != "" {
		if len(i.Time) > 40 {
			return false
		}
		if _, err := time.Parse(time.RFC3339, i.Time); err != nil {
			return false
		}
	}
	return true
}
