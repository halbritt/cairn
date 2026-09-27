//go:build cairn_protocol_min2

package localapi

// Test-only build: a server that has raised its minimum, used by the skew
// suite to prove legacy and header-stripped requests are refused before any
// effect. Releases never set this tag.
const protocolMin = 2
