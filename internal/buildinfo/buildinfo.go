// Package buildinfo reports a small, configuration-free executable identity.
package buildinfo

import (
	"runtime"
	"runtime/debug"
	"strconv"
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
