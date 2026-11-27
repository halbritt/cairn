package core

import (
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// The swarm view's event cursor is "position above N". That skips nothing
// only because every insert into cairn.agent_event takes the collection's
// advisory lock "agent-events:<repo>" first and holds it to commit, so
// positions become visible in order (TestSwarmStreamHoldsBehindAnUncommittedEarlierPosition,
// and its control, show both sides). This guard fails when a new insert site
// does not take that lock.

// The table name must end there: cairn.agent_event_review is another table.
// The column list need not follow in the same literal, so a statement that is
// joined from pieces is still seen.
var agentEventInsert = regexp.MustCompile(`(?i)INSERT\s+INTO\s+cairn\.agent_event([^A-Za-z0-9_]|$)`)

type agentEventFunc struct {
	name    string
	file    string
	decl    *ast.FuncDecl
	locks   []token.Pos            // lock(ctx, tx, "agent-events:"+...) calls
	inserts []token.Pos            // SQL literals that insert into cairn.agent_event
	calls   map[string][]token.Pos // callee name -> call positions
}

func agentEventRepoRoot(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("no caller information")
	}
	return filepath.Dir(filepath.Dir(file))
}

func agentEventLockCall(call *ast.CallExpr) bool {
	fn, ok := call.Fun.(*ast.Ident)
	if !ok || fn.Name != "lock" || len(call.Args) != 3 {
		return false
	}
	// The key is "agent-events:" + repo, in any concatenation that starts
	// with that literal.
	expr := call.Args[2]
	for {
		binary, ok := expr.(*ast.BinaryExpr)
		if !ok {
			break
		}
		expr = binary.X
	}
	lit, ok := expr.(*ast.BasicLit)
	if !ok || lit.Kind != token.STRING {
		return false
	}
	value, err := strconv.Unquote(lit.Value)
	return err == nil && value == "agent-events:"
}

func TestAgentEventInsertSitesTakeTheCollectionLock(t *testing.T) {
	root := agentEventRepoRoot(t)

	// 1. No file outside package core inserts into the table, in any language.
	var stray []string
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(root, path)
		if d.IsDir() {
			name := d.Name()
			if path != root && (strings.HasPrefix(name, ".") || name == "node_modules" || name == "target" || name == "bin" || name == "vendor") {
				return filepath.SkipDir
			}
			if rel == filepath.Join("core", "schema") || rel == filepath.Join("docs", "sources") {
				return filepath.SkipDir
			}
			return nil
		}
		switch filepath.Ext(path) {
		case ".go", ".py", ".sql", ".sh", ".rs", ".ts", ".js":
		default:
			return nil
		}
		base := filepath.Base(path)
		if strings.HasSuffix(base, "_test.go") || strings.HasPrefix(base, "test_") || strings.HasPrefix(base, "check_") {
			return nil
		}
		if filepath.Dir(rel) == "core" && filepath.Ext(path) == ".go" {
			return nil
		}
		raw, rErr := os.ReadFile(path)
		if rErr != nil {
			return rErr
		}
		if agentEventInsert.Match(raw) {
			stray = append(stray, rel)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(stray) > 0 {
		t.Fatalf("cairn.agent_event is inserted outside package core, where this guard cannot check the collection lock: %v", stray)
	}

	// 2. In package core, every insert is reached only after the lock.
	fset := token.NewFileSet()
	packages, err := parser.ParseDir(fset, filepath.Join(root, "core"), func(info fs.FileInfo) bool {
		return !strings.HasSuffix(info.Name(), "_test.go")
	}, parser.SkipObjectResolution)
	if err != nil {
		t.Fatal(err)
	}
	funcs := map[string][]*agentEventFunc{}
	var sites []*agentEventFunc
	literalInserts := 0
	for _, pkg := range packages {
		for file, parsed := range pkg.Files {
			for _, decl := range parsed.Decls {
				fn, ok := decl.(*ast.FuncDecl)
				if !ok || fn.Body == nil {
					continue
				}
				entry := &agentEventFunc{name: fn.Name.Name, file: filepath.Base(file), decl: fn, calls: map[string][]token.Pos{}}
				ast.Inspect(fn.Body, func(n ast.Node) bool {
					switch node := n.(type) {
					case *ast.CallExpr:
						if agentEventLockCall(node) {
							entry.locks = append(entry.locks, node.Pos())
						}
						switch callee := node.Fun.(type) {
						case *ast.Ident:
							entry.calls[callee.Name] = append(entry.calls[callee.Name], node.Pos())
						case *ast.SelectorExpr:
							entry.calls[callee.Sel.Name] = append(entry.calls[callee.Sel.Name], node.Pos())
						}
					case *ast.BasicLit:
						if node.Kind == token.STRING {
							if value, uErr := strconv.Unquote(node.Value); uErr == nil && agentEventInsert.MatchString(value) {
								entry.inserts = append(entry.inserts, node.Pos())
							}
						}
					}
					return true
				})
				funcs[entry.name] = append(funcs[entry.name], entry)
				if len(entry.inserts) > 0 {
					sites = append(sites, entry)
					literalInserts += len(entry.inserts)
				}
			}
			// A statement held in a package-level constant or variable is
			// outside every function body, so the walk above cannot tell
			// which path runs it.
			ast.Inspect(parsed, func(n ast.Node) bool {
				if lit, ok := n.(*ast.BasicLit); ok && lit.Kind == token.STRING {
					if value, uErr := strconv.Unquote(lit.Value); uErr == nil && agentEventInsert.MatchString(value) {
						literalInserts--
					}
				}
				return true
			})
		}
	}
	if literalInserts != 0 {
		t.Fatalf("package core has %d insert statement(s) into cairn.agent_event outside a function body; write the statement as a literal inside the function that runs it so this guard can check its lock", -literalInserts)
	}
	if len(sites) == 0 {
		t.Fatal("found no insert into cairn.agent_event; the guard no longer sees the publication path")
	}

	// lockedBefore reports whether every path to pos in fn has taken the
	// lock: fn takes it earlier in its own body, or every caller of fn has
	// taken it before the call.
	var lockedBefore func(fn *agentEventFunc, pos token.Pos, depth int, seen map[string]bool) (bool, string)
	lockedBefore = func(fn *agentEventFunc, pos token.Pos, depth int, seen map[string]bool) (bool, string) {
		for _, lockPos := range fn.locks {
			if lockPos < pos {
				return true, ""
			}
		}
		where := fn.file + ":" + fn.name
		if depth == 8 || seen[where] {
			return false, where + " (call chain too deep or recursive)"
		}
		seen[where] = true
		defer delete(seen, where)
		callers := 0
		for _, candidates := range funcs {
			for _, caller := range candidates {
				for _, callPos := range caller.calls[fn.name] {
					callers++
					if ok, why := lockedBefore(caller, callPos, depth+1, seen); !ok {
						return false, why
					}
				}
			}
		}
		if callers == 0 {
			return false, where + " (does not take the lock and has no caller that does)"
		}
		return true, ""
	}
	var report []string
	for _, site := range sites {
		for _, pos := range site.inserts {
			place := fset.Position(pos)
			if ok, why := lockedBefore(site, pos, 0, map[string]bool{}); !ok {
				t.Errorf("%s:%d inserts into cairn.agent_event on a path that has not taken lock(ctx, tx, \"agent-events:\"+repo): %s", filepath.Base(place.Filename), place.Line, why)
			}
			report = append(report, site.file+":"+site.name)
		}
	}
	sort.Strings(report)
	t.Logf("checked %d insert site(s): %s", len(report), strings.Join(report, ", "))
}

// The guard must fail for the mistakes it exists to catch. Each case is a
// small package body run through the same lock and call analysis.
func TestAgentEventInsertGuardRecognisesLockCalls(t *testing.T) {
	cases := map[string]bool{
		`lock(ctx, tx, "agent-events:"+repo)`:            true,
		`lock(ctx, tx, "agent-events:"+req.Repo+suffix)`: true,
		`lock(ctx, tx, "campaign-op:"+repo)`:             false,
		`lock(ctx, tx, key)`:                             false,
		`s.lock(ctx, tx, "agent-events:"+repo)`:          false,
		`unlock(ctx, tx, "agent-events:"+repo)`:          false,
	}
	for source, want := range cases {
		expr, err := parser.ParseExpr(source)
		if err != nil {
			t.Fatal(err)
		}
		call, ok := expr.(*ast.CallExpr)
		if !ok {
			t.Fatalf("%s is not a call", source)
		}
		if got := agentEventLockCall(call); got != want {
			t.Errorf("%s: lock call recognised = %v, want %v", source, got, want)
		}
	}
	for statement, want := range map[string]bool{
		"INSERT INTO cairn.agent_event(event_id) VALUES($1)":            true,
		"insert into cairn.agent_event (\n event_id) values($1)":        true,
		"INSERT INTO cairn.agent_event_review(event_id) VALUES($1)":     false,
		"INSERT INTO cairn.agent_delivery(delivery_id) VALUES($1)":      false,
		"UPDATE cairn.agent_event SET summary=$1":                       false,
		"SELECT 1 FROM cairn.agent_event WHERE event_id=$1":             false,
		"INSERT INTO cairn.agent_event_admission_refusal(a) VALUES($1)": false,
		"INSERT INTO cairn.agent_event":                                 true,
		"INSERT INTO cairn.agent_event SELECT * FROM staged":            true,
	} {
		if got := agentEventInsert.MatchString(statement); got != want {
			t.Errorf("%q: insert recognised = %v, want %v", statement, got, want)
		}
	}
}
