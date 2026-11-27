package core

import (
	"fmt"
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
//
// The guard reads source. It accepts one shape of code and fails for every
// other, including some that are correct:
//
//   - The lock is the call lock(ctx, tx, "agent-events:"+X), where X is a
//     name or a dotted path such as req.Publication.Repo. A key with a
//     suffix or a prefix, another literal, a key held in a variable and a
//     key built by a call are other advisory locks to this guard.
//   - The call is a statement that runs whenever control reaches it: the
//     init of an if statement, a comparison in its condition, an assignment,
//     or an expression statement.
//   - That statement dominates the insert: it is a statement of a block that
//     encloses the insert, and it stands before the statement of that block
//     that holds the insert. A lock in a branch, a case, a loop body or
//     another function literal, a deferred lock, a lock in a go statement
//     and a lock after the insert cover some paths at most. The body of a
//     function literal is a block like any other, and a function literal
//     runs inside the statement that holds it. A goto in the function voids
//     its locks.
//   - A function that does not lock for itself is covered when every call of
//     it is covered in the same way, up to eight calls above the insert.
//     That needs every caller in view, so the function must be unexported
//     and must never be used as a value.
//   - The insert statement is a literal inside a function declaration.
//
// The guard has no type information. It does not check that the lock and the
// insert use the same transaction, that X is the repository of the inserted
// event, or that no local name hides the lock helper.

// The table name must end there: cairn.agent_event_review is another table.
// The column list need not follow in the same literal, so a statement that is
// joined from pieces is still seen.
var agentEventInsert = regexp.MustCompile(`(?i)INSERT\s+INTO\s+cairn\.agent_event([^A-Za-z0-9_]|$)`)

// agentEventFunc is one function declaration of the package.
type agentEventFunc struct {
	name  string
	file  string
	decl  *ast.FuncDecl
	jumps bool // has a goto, which can pass around a statement
}

// agentEventUse is an insert literal or a call, with the function
// declaration it stands in: nil outside every function declaration.
type agentEventUse struct {
	fn   *agentEventFunc
	node ast.Node
}

// agentEventPackage is what the analysis reads from the files of a package.
// It has no type information, so it follows calls by name.
type agentEventPackage struct {
	fset    *token.FileSet
	inserts []agentEventUse            // SQL literals that insert into cairn.agent_event
	calls   map[string][]agentEventUse // callee name -> direct calls of that name
	values  map[string][]token.Pos     // name -> uses that neither declare nor directly call it
}

func agentEventRepoRoot(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("no caller information")
	}
	return filepath.Dir(filepath.Dir(file))
}

// agentEventLockCall reports whether call is lock(ctx, tx, "agent-events:"+X)
// with X a name or a dotted path. The key must be exactly that:
// "agent-events:"+req.Repo+req.DeliveryID is another advisory lock, which
// does not exclude publication.
func agentEventLockCall(call *ast.CallExpr) bool {
	fn, ok := call.Fun.(*ast.Ident)
	if !ok || fn.Name != "lock" || len(call.Args) != 3 {
		return false
	}
	key, ok := call.Args[2].(*ast.BinaryExpr)
	if !ok || key.Op != token.ADD {
		return false
	}
	lit, ok := key.X.(*ast.BasicLit)
	if !ok || lit.Kind != token.STRING {
		return false
	}
	if value, err := strconv.Unquote(lit.Value); err != nil || value != "agent-events:" {
		return false
	}
	repo := key.Y
	for {
		switch operand := repo.(type) {
		case *ast.Ident:
			return true
		case *ast.SelectorExpr:
			repo = operand.X
		default:
			return false
		}
	}
}

// agentEventLockNamesKey reports whether call is a lock call that mentions
// the collection key at all. It only improves the message for a key that is
// almost right.
func agentEventLockNamesKey(call *ast.CallExpr) bool {
	fn, ok := call.Fun.(*ast.Ident)
	if !ok || fn.Name != "lock" {
		return false
	}
	found := false
	ast.Inspect(call, func(n ast.Node) bool {
		if lit, ok := n.(*ast.BasicLit); ok && lit.Kind == token.STRING && strings.Contains(lit.Value, "agent-events:") {
			found = true
		}
		return !found
	})
	return found
}

// agentEventLockStmt reports whether stmt takes the collection lock every
// time control reaches it.
func agentEventLockStmt(stmt ast.Stmt) bool {
	isLock := func(expr ast.Expr) bool {
		call, ok := expr.(*ast.CallExpr)
		return ok && agentEventLockCall(call)
	}
	switch s := stmt.(type) {
	case *ast.ExprStmt:
		return isLock(s.X)
	case *ast.AssignStmt:
		return (s.Tok == token.ASSIGN || s.Tok == token.DEFINE) && len(s.Rhs) == 1 && isLock(s.Rhs[0])
	case *ast.IfStmt:
		// The init always runs. In the condition the call must be an
		// operand of the comparison itself: behind && or || it may not run.
		if agentEventLockStmt(s.Init) {
			return true
		}
		cond, ok := s.Cond.(*ast.BinaryExpr)
		return ok && (cond.Op == token.NEQ || cond.Op == token.EQL) && (isLock(cond.X) || isLock(cond.Y))
	}
	return false
}

// agentEventPath returns the nodes from root down to target, both included.
func agentEventPath(root, target ast.Node) []ast.Node {
	var stack, path []ast.Node
	ast.Inspect(root, func(n ast.Node) bool {
		switch {
		case path != nil:
			return false
		case n == nil:
			stack = stack[:len(stack)-1]
			return false
		}
		stack = append(stack, n)
		if n == target {
			path = append(path, stack...)
		}
		return true
	})
	return path
}

// agentEventLockDominates reports whether, in a block on the path, a lock
// statement stands before the statement through which the path goes on.
func agentEventLockDominates(path []ast.Node) bool {
	for i, node := range path {
		block, ok := node.(*ast.BlockStmt)
		if !ok || i+1 == len(path) {
			continue
		}
		locked := false
		for _, stmt := range block.List {
			if ast.Node(stmt) == path[i+1] {
				if locked {
					return true
				}
				break
			}
			locked = locked || agentEventLockStmt(stmt)
		}
	}
	return false
}

// scan records the inserts, the calls and the other uses of names in a file.
func (p *agentEventPackage) scan(file *ast.File) {
	accounted := map[*ast.Ident]bool{} // declared names and direct call targets
	for _, decl := range file.Decls {
		var current *agentEventFunc
		if fn, ok := decl.(*ast.FuncDecl); ok {
			current = &agentEventFunc{name: fn.Name.Name, file: filepath.Base(p.fset.Position(fn.Pos()).Filename), decl: fn}
			accounted[fn.Name] = true
		}
		ast.Inspect(decl, func(n ast.Node) bool {
			switch node := n.(type) {
			case *ast.InterfaceType:
				// A method that an interface names is still called by name.
				if node.Methods != nil {
					for _, method := range node.Methods.List {
						for _, name := range method.Names {
							accounted[name] = true
						}
					}
				}
			case *ast.BranchStmt:
				if node.Tok == token.GOTO && current != nil {
					current.jumps = true
				}
			case *ast.CallExpr:
				var callee *ast.Ident
				switch fun := node.Fun.(type) {
				case *ast.Ident:
					callee = fun
				case *ast.SelectorExpr:
					callee = fun.Sel
				}
				if callee != nil {
					accounted[callee] = true
					p.calls[callee.Name] = append(p.calls[callee.Name], agentEventUse{fn: current, node: node})
				}
			case *ast.Ident:
				if !accounted[node] {
					p.values[node.Name] = append(p.values[node.Name], node.Pos())
				}
			case *ast.BasicLit:
				if node.Kind == token.STRING {
					if value, err := strconv.Unquote(node.Value); err == nil && agentEventInsert.MatchString(value) {
						p.inserts = append(p.inserts, agentEventUse{fn: current, node: node})
					}
				}
			}
			return true
		})
	}
}

func (p *agentEventPackage) at(pos token.Pos) string {
	place := p.fset.Position(pos)
	return fmt.Sprintf("%s:%d", filepath.Base(place.Filename), place.Line)
}

// unlocked says that the function of use does not cover it, and names the
// lock calls of that function that come close.
func (p *agentEventPackage) unlocked(use agentEventUse) string {
	line := p.fset.Position(use.node.Pos()).Line
	text := fmt.Sprintf("%s:%s does not take the lock on every path to line %d", use.fn.file, use.fn.name, line)
	ast.Inspect(use.fn.decl, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		at := p.fset.Position(call.Pos()).Line
		switch {
		case !agentEventLockCall(call):
			if agentEventLockNamesKey(call) {
				text += fmt.Sprintf(" (the lock at line %d has a key other than \"agent-events:\"+<repo>)", at)
			}
		case use.fn.jumps:
			text += fmt.Sprintf(" (a goto can pass around the lock at line %d)", at)
		default:
			text += fmt.Sprintf(" (the lock at line %d does not count: it must stand before line %d in a block around it, as an if, assignment or expression statement)", at, line)
		}
		return true
	})
	return text
}

// locked reports whether every path to use.node has taken the collection
// lock: a lock statement of its function dominates it, or every call of that
// function is locked in the same way. The second half needs every caller in
// view, so it holds only for an unexported function whose name has no use
// other than a direct call.
func (p *agentEventPackage) locked(use agentEventUse, depth int, seen map[*agentEventFunc]bool) (bool, string) {
	fn := use.fn
	if !fn.jumps && agentEventLockDominates(agentEventPath(fn.decl, use.node)) {
		return true, ""
	}
	callers := p.calls[fn.name]
	switch {
	case seen[fn]:
		return false, p.unlocked(use) + ", and the call chain above it is recursive"
	case depth == 8:
		return false, p.unlocked(use) + ", and the call chain above it is too deep"
	case ast.IsExported(fn.name):
		return false, p.unlocked(use) + ", and it is exported, so the guard cannot see all its callers"
	case len(p.values[fn.name]) > 0:
		return false, p.unlocked(use) + ", and " + p.at(p.values[fn.name][0]) + " uses the name without calling it, so the guard cannot see all its callers"
	case len(callers) == 0:
		return false, p.unlocked(use) + ", and it has no caller"
	}
	seen[fn] = true
	defer delete(seen, fn)
	for _, call := range callers {
		if call.fn == nil {
			return false, p.unlocked(use) + ", and " + p.at(call.node.Pos()) + " calls it outside a function declaration"
		}
		if ok, why := p.locked(call, depth+1, seen); !ok {
			return false, why
		}
	}
	return true, ""
}

// agentEventLockViolations is the whole analysis of one package. It returns
// "file:function" for each insert statement inside a function declaration,
// and one line for each insert statement that some path may reach without
// the collection lock.
func agentEventLockViolations(fset *token.FileSet, files map[string]*ast.File) (sites, violations []string) {
	pkg := &agentEventPackage{fset: fset, calls: map[string][]agentEventUse{}, values: map[string][]token.Pos{}}
	names := make([]string, 0, len(files))
	for name := range files {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		pkg.scan(files[name])
	}
	for _, insert := range pkg.inserts {
		// A statement held in a package-level constant or variable is
		// outside every function, so no path to it can be followed.
		if insert.fn == nil {
			violations = append(violations, pkg.at(insert.node.Pos())+" holds an insert into cairn.agent_event outside a function declaration; write the statement as a literal inside the function that runs it so this guard can check its lock")
			continue
		}
		sites = append(sites, insert.fn.file+":"+insert.fn.name)
		if ok, why := pkg.locked(insert, 0, map[*agentEventFunc]bool{}); !ok {
			violations = append(violations, pkg.at(insert.node.Pos())+" inserts into cairn.agent_event on a path that has not taken lock(ctx, tx, \"agent-events:\"+repo): "+why)
		}
	}
	sort.Strings(sites)
	return sites, violations
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
	files := map[string]*ast.File{}
	entries, err := os.ReadDir(filepath.Join(root, "core"))
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		parsed, pErr := parser.ParseFile(fset, filepath.Join(root, "core", name), nil, parser.SkipObjectResolution)
		if pErr != nil {
			t.Fatal(pErr)
		}
		files[name] = parsed
	}
	sites, violations := agentEventLockViolations(fset, files)
	for _, violation := range violations {
		t.Error(violation)
	}
	if len(sites) == 0 {
		t.Fatal("found no insert into cairn.agent_event; the guard no longer sees the publication path")
	}
	t.Logf("checked %d insert site(s): %s", len(sites), strings.Join(sites, ", "))
}

// The two recognisers on their own: the lock call and the insert statement.
func TestAgentEventInsertGuardRecognisesLockCalls(t *testing.T) {
	cases := map[string]bool{
		`lock(ctx, tx, "agent-events:"+repo)`:                    true,
		`lock(ctx, tx, "agent-events:"+req.Repo)`:                true,
		`lock(ctx, tx, "agent-events:"+req.Publication.Repo)`:    true,
		`lock(ctx, tx, "agent-events:"+req.Repo+suffix)`:         false,
		`lock(ctx, tx, "agent-events:"+req.Repo+req.DeliveryID)`: false,
		`lock(ctx, tx, prefix+"agent-events:"+repo)`:             false,
		`lock(ctx, tx, "agent-events:"-repo)`:                    false,
		`lock(ctx, tx, "agent-events:"+(repo+suffix))`:           false,
		`lock(ctx, tx, "agent-events:"+scope(repo))`:             false,
		`lock(ctx, tx, "agent-events:"+req.Scope().Repo)`:        false,
		`lock(ctx, tx, "agent-events:"+repos[0])`:                false,
		`lock(ctx, tx, "agent-events:")`:                         false,
		`lock(ctx, tx, fmt.Sprintf("agent-events:%s", repo))`:    false,
		`lock(ctx, tx, "campaign-op:"+repo)`:                     false,
		`lock(ctx, tx, key)`:                                     false,
		`lock(ctx, tx, "agent-events:"+repo, wait)`:              false,
		`s.lock(ctx, tx, "agent-events:"+repo)`:                  false,
		`unlock(ctx, tx, "agent-events:"+repo)`:                  false,
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

// agentEventChainSource is an insert below hops functions that do not lock,
// and a function above them that does.
func agentEventChainSource(hops int) string {
	var source strings.Builder
	source.WriteString(`
func hop0(ctx context.Context, tx pgx.Tx, id string) error {
	_, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", id)
	return err
}
`)
	for hop := 1; hop < hops; hop++ {
		fmt.Fprintf(&source, "\nfunc hop%d(ctx context.Context, tx pgx.Tx, id string) error {\n\treturn hop%d(ctx, tx, id)\n}\n", hop, hop-1)
	}
	fmt.Fprintf(&source, `
func top(ctx context.Context, tx pgx.Tx, repo, id string) error {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	return hop%d(ctx, tx, id)
}
`, hops-1)
	return source.String()
}

// The guard must fail for the mistakes it exists to catch, and pass for the
// forms the package uses. Each case is a small package that goes through the
// whole analysis.
func TestAgentEventInsertGuardRejects(t *testing.T) {
	// Most cases put their statements before the insert of this function,
	// so a rejected case differs from an accepted one in those statements
	// only.
	const shell = `
func publish(ctx context.Context, tx pgx.Tx, repo, id, kind string, serial bool) error {%s
	_, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", id)
	return err
}
`
	// A helper that inserts and leaves the lock to its one caller.
	const helper = `
func (s *Store) Publish(ctx context.Context, repo, id string) error {
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	if err = lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	return s.insertEventTx(ctx, tx, id)
}

func (s *Store) insertEventTx(ctx context.Context, tx pgx.Tx, id string) error {
	_, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", id)
	return err
}
`
	cases := []struct {
		name       string
		before     string   // statements before the insert in shell, or
		sources    []string // the files of a package, without the package clause
		sites      int      // insert statements the analysis must see inside functions
		violations int
		want       string // part of each violation
	}{
		{name: "a insert with no lock", sites: 1, violations: 1, want: "it has no caller"},
		{
			name: "b lock after the insert",
			sources: []string{`
func publish(ctx context.Context, tx pgx.Tx, repo, id string) error {
	if _, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", id); err != nil {
		return err
	}
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	return nil
}
`},
			sites: 1, violations: 1, want: "the lock at line 7 does not count",
		},
		{
			name: "c lock only inside an if before the insert",
			before: `
	if serial {
		if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
			return err
		}
	}`,
			sites: 1, violations: 1, want: "the lock at line 5 does not count",
		},
		{
			name: "d lock in one switch case and insert after the switch",
			before: `
	switch kind {
	case "request":
		if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
			return err
		}
	case "notice":
	}`,
			sites: 1, violations: 1, want: "the lock at line 6 does not count",
		},
		{
			name: "e lock inside a for body and insert after the loop",
			before: `
	for attempt := 0; attempt < 2; attempt++ {
		if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
			return err
		}
	}`,
			sites: 1, violations: 1, want: "the lock at line 5 does not count",
		},
		{
			name: "f key with a suffix",
			before: `
	if err := lock(ctx, tx, "agent-events:"+repo+kind); err != nil {
		return err
	}`,
			sites: 1, violations: 1, want: "the lock at line 4 has a key other than",
		},
		{
			name: "g key with another literal",
			before: `
	if err := lock(ctx, tx, "campaign-op:"+repo); err != nil {
		return err
	}`,
			sites: 1, violations: 1, want: "it has no caller",
		},
		{
			name: "h key held in a variable",
			before: `
	key := "agent-events:" + repo
	if err := lock(ctx, tx, key); err != nil {
		return err
	}`,
			sites: 1, violations: 1, want: "it has no caller",
		},
		{name: "i helper whose only caller takes the lock first", sources: []string{helper}, sites: 1},
		{
			name: "j the same helper with a second caller that does not lock",
			sources: []string{helper, `
func (s *Store) republish(ctx context.Context, tx pgx.Tx, id string) error {
	return s.insertEventTx(ctx, tx, id)
}
`},
			sites: 1, violations: 1, want: "case_1.go:republish does not take the lock on every path to line 4, and it has no caller",
		},
		{
			name: "k lock and insert inside the same closure",
			sources: []string{`
func (s *Store) Reissue(ctx context.Context, repo, id string) error {
	return mutate(ctx, s, func(tx pgx.Tx) error {
		if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
			return err
		}
		_, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", id)
		return err
	})
}
`},
			sites: 1,
		},
		{
			name: "l lock before the call that takes the closure with the insert",
			sources: []string{`
func (s *Store) Publish(ctx context.Context, tx pgx.Tx, repo string, ids []string) error {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	return each(ids, func(id string) error {
		_, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", id)
		return err
	})
}
`},
			sites: 1,
		},
		{
			name: "m statement built by concatenation without lock",
			sources: []string{`
func publish(ctx context.Context, tx pgx.Tx, cols, id string) error {
	_, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event"+cols, id)
	return err
}
`},
			sites: 1, violations: 1, want: "it has no caller",
		},
		{
			name: "n statement held in a package-level const",
			sources: []string{`
const insertEvent = "INSERT INTO cairn.agent_event(event_id) VALUES($1)"

func publish(ctx context.Context, tx pgx.Tx, repo, id string) error {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, insertEvent, id)
	return err
}
`},
			sites: 0, violations: 1, want: "case_0.go:3 holds an insert into cairn.agent_event outside a function declaration",
		},
		{
			name: "o lock in a defer",
			before: `
	defer lock(ctx, tx, "agent-events:"+repo)`,
			sites: 1, violations: 1, want: "the lock at line 4 does not count",
		},
		{
			name: "p house form at function top level",
			before: `
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}`,
			sites: 1,
		},
		{
			name: "q insert into another table with no lock",
			sources: []string{`
func review(ctx context.Context, tx pgx.Tx, id string) error {
	_, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event_review(event_id) VALUES($1)", id)
	return err
}
`},
			sites: 0,
		},
		{
			name: "r lock in the else branch only",
			before: `
	if serial {
		id = "serial-" + id
	} else {
		if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
			return err
		}
	}`,
			sites: 1, violations: 1, want: "the lock at line 7 does not count",
		},
		{
			name: "lock in the init of an else if",
			before: `
	if serial {
		id = "serial-" + id
	} else if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}`,
			sites: 1, violations: 1, want: "the lock at line 6 does not count",
		},
		{
			name: "lock on the right of a logical and in an if condition",
			before: `
	if serial && lock(ctx, tx, "agent-events:"+repo) != nil {
		return errLocked
	}`,
			sites: 1, violations: 1, want: "the lock at line 4 does not count",
		},
		{
			name: "lock on the right of a logical or in an if condition",
			before: `
	if serial || lock(ctx, tx, "agent-events:"+repo) {
		return errLocked
	}`,
			sites: 1, violations: 1, want: "the lock at line 4 does not count",
		},
		{
			name: "lock compared with nil in an if condition",
			before: `
	if lock(ctx, tx, "agent-events:"+repo) != nil {
		return errLocked
	}`,
			sites: 1,
		},
		{
			name: "insert in the init of the if statement whose condition locks",
			sources: []string{`
func publish(ctx context.Context, tx pgx.Tx, repo, id string) error {
	if _, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", id); lock(ctx, tx, "agent-events:"+repo) != nil {
		return err
	}
	return nil
}
`},
			sites: 1, violations: 1, want: "the lock at line 4 does not count",
		},
		{
			name: "lock assigned and then checked",
			before: `
	lockErr := lock(ctx, tx, "agent-events:"+repo)
	if lockErr != nil {
		return lockErr
	}`,
			sites: 1,
		},
		{
			name: "lock as an expression statement",
			before: `
	lock(ctx, tx, "agent-events:"+repo)`,
			sites: 1,
		},
		{
			name: "lock in a go statement",
			before: `
	go lock(ctx, tx, "agent-events:"+repo)`,
			sites: 1, violations: 1, want: "the lock at line 4 does not count",
		},
		{
			name: "lock in a deferred closure",
			before: `
	defer func() {
		_ = lock(ctx, tx, "agent-events:"+repo)
	}()`,
			sites: 1, violations: 1, want: "the lock at line 5 does not count",
		},
		{
			name: "lock in a closure and insert after the call that takes it",
			before: `
	if err := retry(func() error {
		if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
			return err
		}
		return nil
	}); err != nil {
		return err
	}`,
			sites: 1, violations: 1, want: "the lock at line 5 does not count",
		},
		{
			name: "goto around the lock",
			before: `
	if serial {
		goto insert
	}
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
insert:`,
			sites: 1, violations: 1, want: "a goto can pass around the lock at line 7",
		},
		{
			name: "function other than lock with the collection key",
			before: `
	if err := lockNoop(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}`,
			sites: 1, violations: 1, want: "it has no caller",
		},
		{
			name: "insert in a labelled loop and a branch below a lock at function top level",
			sources: []string{`
func (s *Store) Drain(ctx context.Context, tx pgx.Tx, repo string, ids []string) error {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
drain:
	for _, id := range ids {
		for _, done := range s.done {
			if id == done {
				continue drain
			}
		}
		if id != "" {
			if _, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", id); err != nil {
				return err
			}
		}
	}
	return nil
}
`},
			sites: 1,
		},
		{
			name: "lock and insert in the same loop body",
			sources: []string{`
func drain(ctx context.Context, txs []pgx.Tx, repo, id string) error {
	for _, tx := range txs {
		if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", id); err != nil {
			return err
		}
	}
	return nil
}
`},
			sites: 1,
		},
		{
			name: "two inserts with the lock between them",
			sources: []string{`
func publish(ctx context.Context, tx pgx.Tx, repo, first, second string) error {
	if _, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", first); err != nil {
		return err
	}
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", second)
	return err
}
`},
			sites: 2, violations: 1, want: "case_0.go:4 inserts",
		},
		{
			name:    "exported helper below a caller that locks",
			sources: []string{strings.ReplaceAll(helper, "insertEventTx", "InsertEventTx")},
			sites:   1, violations: 1, want: "case_0.go:InsertEventTx does not take the lock on every path to line 15, and it is exported",
		},
		{
			name: "helper that is also used as a value",
			sources: []string{helper, `
func (s *Store) sweep(ctx context.Context, tx pgx.Tx, ids []string) error {
	return each(ctx, tx, ids, s.insertEventTx)
}
`},
			sites: 1, violations: 1, want: "case_1.go:4 uses the name without calling it",
		},
		{
			name: "helper that is also called outside a function declaration",
			sources: []string{helper, `
var seeded = new(Store).insertEventTx(context.Background(), seedTx, "seed")
`},
			sites: 1, violations: 1, want: "case_1.go:3 calls it outside a function declaration",
		},
		{
			name: "helper that an interface names, called below the lock",
			sources: []string{helper, `
type eventWriter interface {
	insertEventTx(ctx context.Context, tx pgx.Tx, id string) error
}

func publishThrough(ctx context.Context, tx pgx.Tx, w eventWriter, repo, id string) error {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	return w.insertEventTx(ctx, tx, id)
}
`},
			sites: 1,
		},
		{
			name: "helper that calls itself below a caller that locks",
			sources: []string{`
func (s *Store) Publish(ctx context.Context, tx pgx.Tx, repo string, ids []string) error {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	return s.insertEventsTx(ctx, tx, ids)
}

func (s *Store) insertEventsTx(ctx context.Context, tx pgx.Tx, ids []string) error {
	if len(ids) == 0 {
		return nil
	}
	if _, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", ids[0]); err != nil {
		return err
	}
	return s.insertEventsTx(ctx, tx, ids[1:])
}
`},
			sites: 1, violations: 1, want: "case_0.go:insertEventsTx does not take the lock on every path to line 17, and the call chain above it is recursive",
		},
		{name: "lock eight calls above the insert", sources: []string{agentEventChainSource(8)}, sites: 1},
		{
			name:    "lock nine calls above the insert",
			sources: []string{agentEventChainSource(9)},
			sites:   1, violations: 1, want: "case_0.go:hop8 does not take the lock on every path to line 37, and the call chain above it is too deep",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			sources := tc.sources
			if sources == nil {
				sources = []string{fmt.Sprintf(shell, tc.before)}
			}
			fset := token.NewFileSet()
			files := map[string]*ast.File{}
			for i, source := range sources {
				name := fmt.Sprintf("case_%d.go", i)
				parsed, err := parser.ParseFile(fset, name, "package core\n"+source, parser.SkipObjectResolution)
				if err != nil {
					t.Fatal(err)
				}
				files[name] = parsed
			}
			sites, violations := agentEventLockViolations(fset, files)
			if len(sites) != tc.sites {
				t.Errorf("insert sites seen = %q, want %d", sites, tc.sites)
			}
			if len(violations) != tc.violations {
				t.Errorf("violations = %q, want %d", violations, tc.violations)
			}
			for _, violation := range violations {
				if !strings.Contains(violation, tc.want) {
					t.Errorf("violation %q does not say %q", violation, tc.want)
				}
			}
		})
	}
}
