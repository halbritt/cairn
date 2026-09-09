// Package opencode supplies the native adapter shipped with this Cairn binary.
package opencode

import _ "embed"

//go:embed cairn.ts
var adapter string

func Adapter() string { return adapter }
