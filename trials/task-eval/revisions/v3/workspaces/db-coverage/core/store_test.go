package core

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

// TestActiveQueryDatabase executes ActiveQuery against a real database.
func TestActiveQueryDatabase(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("CAIRN_TEST_DATABASE_URL not set")
	}
	script := `CREATE TABLE records (id int, repo text, body text, lifecycle text, written_at timestamptz);
INSERT INTO records VALUES (1, 'r', 'old', 'active', '2026-01-01'), (2, 'r', 'new', 'active', '2026-02-01'),
  (3, 'r', 'gone', 'retired', '2026-03-01'), (4, 'x', 'other', 'active', '2026-04-01');
PREPARE q(text) AS ` + ActiveQuery("r") + `;
EXECUTE q('r');`
	out, err := exec.Command("psql", dsn, "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", script).CombinedOutput()
	if err != nil {
		t.Fatalf("query failed: %v\n%s", err, out)
	}
	rows := strings.Fields(strings.TrimSpace(string(out)))
	if len(rows) != 2 || !strings.HasPrefix(rows[0], "2|") || !strings.HasPrefix(rows[1], "1|") {
		t.Fatalf("unexpected rows %q", rows)
	}
	if err := os.WriteFile("../.integration-ran", []byte("db-verified rows=2\n"), 0o644); err != nil {
		t.Fatal(err)
	}
}
