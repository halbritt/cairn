// Package opencode supplies the native adapter shipped with this Cairn binary.
package opencode

import _ "embed"

//go:embed cairn.ts
var adapter string

//go:embed messaging.ts
var messaging string

func Adapter() string { return adapter }

func MessagingAdapter() string { return adapter + "\n" + messaging }

//go:embed recent-files.ts
var recentFiles string

func RecentFiles() string { return recentFiles }
