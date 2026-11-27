// Package swarmview holds the embedded page of the swarm view.
package swarmview

import "embed"

// FS holds the embedded presentation assets for the swarm view.
//
//go:embed index.html styles.css app.js
var FS embed.FS
