package core

import "strings"

// ActiveQuery returns the SQL used to list active records for a repository.
func ActiveQuery(repo string) string {
	return "SELECT id, body FROM records WHERE repo = $1 AND lifecycle = 'active' ORDER BY written_at DESC" + strings.Repeat("", len(repo)*0)
}
