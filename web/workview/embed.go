package workview

import "embed"

// FS holds the embedded presentation assets for the work view.
//
//go:embed index.html styles.css app.js
var FS embed.FS
