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
//   - The insert is an INSERT statement that names the table in one string
//     literal, as cairn.agent_event or as agent_event, in any case, quoting
//     and spacing. Other ways to write rows are recognised and refused: the
//     table named on its own (as for CopyFrom, or for a statement joined
//     from constants), MERGE and COPY ... FROM.
//   - The statement is executed where it is written: the literal is the SQL
//     argument of an Exec, Query or QueryRow call, alone or as the leftmost
//     operand of a concatenation. A statement that is returned, kept in a
//     variable, a field or a constant, or passed to another function can run
//     anywhere, so no lock around its text covers it.
//   - The lock is the call lock(ctx, tx, "agent-events:"+X), where X is a
//     name or a dotted path such as req.Publication.Repo. A key with a
//     suffix or a prefix, another literal, a key held in a variable and a
//     key built by a call are other advisory locks to this guard.
//   - The call is a statement that runs whenever control reaches it: the
//     init of an if statement, a comparison in its condition, an assignment,
//     or an expression statement.
//   - That statement dominates the call that executes the insert: it is a
//     statement of a block that encloses the call, and it stands before the
//     statement of that block that holds the call. A lock in a branch, a
//     case, a loop body or another function literal, a deferred lock, a lock
//     in a go statement and a lock after the insert cover some paths at
//     most. The body of a function literal is a block like any other. A
//     function literal that is called or passed to a call runs inside the
//     statement that holds it; one that is stored or returned can run
//     anywhere, and only a lock inside it covers it. A goto in the function
//     voids its locks.
//   - A function that does not lock for itself is covered when every call of
//     it is covered in the same way, up to eight calls above the insert.
//     That needs every caller in view, so the function must be unexported
//     and must never be used as a value.
//   - The statement is one of agentEventAuditedSites. A new insert site
//     fails the guard until a person has reviewed its lock and listed it.
//
// The guard has no type information. It does not check that the lock and the
// insert use the same transaction, that X is the repository of the inserted
// event, or that no local name hides the lock helper. It does not see a
// table name that the program builds at run time. The audited list is there
// for what the analysis gets wrong: it cannot help with a write that the
// guard does not recognise at all.

// agentEventAuditedSites lists the insert statements of package core whose
// lock a person has reviewed, as file:function, one entry for each statement.
// The analysis below can be wrong in ways it cannot see, so a statement that
// is not listed here fails the guard whatever the analysis says about it.
// Before you add an entry, check what the analysis cannot: that the lock is
// taken in the transaction that inserts, with the repository of the inserted
// event, and that the transaction holds it to commit.
var agentEventAuditedSites = []string{
	"agent_event_reissue.go:ReissueEvent",
	"agent_events.go:publishEventInternalTx",
	"agent_stopped_backlog.go:commitStoppedDeliveryNoticeTx",
	"agent_wait_scheduler.go:DrainWaitOutbox",
}

// These patterns read text that agentEventNormalise has brought to one
// spelling. The table name must end where the pattern ends:
// cairn.agent_event_review is another table. The column list need not follow
// in the same literal, so a statement that is joined from pieces is still
// seen.
var (
	agentEventInsert = regexp.MustCompile(`insert into (cairn ?\. ?)?agent_event([^a-z0-9_]|$)`)

	// Other ways to write rows. The guard recognises them and cannot check
	// them: the table named on its own, which is how CopyFrom and a
	// statement joined from constants name it, the same inside an
	// Identifier as it reads in a file, MERGE, and COPY ... FROM.
	agentEventOtherWrite = regexp.MustCompile(`^(cairn ?\. ?)?agent_event$` +
		`|identifier ?[({]([^(){}]*[^a-z0-9_])?agent_event ?[)}]` +
		`|merge into (cairn ?\. ?)?agent_event([^a-z0-9_]|$)` +
		`|copy (cairn ?\. ?)?agent_event ?(\([^)]*\) ?)?from( |$)`)
)

// agentEventNormalise brings SQL text to one spelling: lower case, no quotes
// around identifiers, single spaces. Every double quote and backquote becomes
// a space, together with the backslashes that escape it in the language
// around the SQL, so the patterns also read a whole source file. A quote ends
// a token: INTO"cairn"."agent_event" names the same table as the spaced form.
func agentEventNormalise(text string) string {
	text = strings.ToLower(text)
	out := make([]byte, 0, len(text))
	space := func() {
		if len(out) > 0 && out[len(out)-1] != ' ' {
			out = append(out, ' ')
		}
	}
	for i := 0; i < len(text); i++ {
		switch c := text[i]; c {
		case '"', '`':
			for len(out) > 0 && out[len(out)-1] == '\\' {
				out = out[:len(out)-1]
			}
			space()
		case ' ', '\t', '\n', '\r', '\f', '\v':
			space()
		default:
			out = append(out, c)
		}
	}
	return strings.TrimSpace(string(out))
}

// agentEventStripComments replaces each SQL comment with a space. A comment
// separates tokens and names nothing: INSERT /* x */ INTO cairn.agent_event
// writes to the table. Block comments nest, as they do in PostgreSQL. A line
// comment ends at the line's end. In a source file that is read whole, a
// quoted string spells a line's end and a tab as an escape, so those escapes
// are separators here too.
func agentEventStripComments(text string) string {
	out := make([]byte, 0, len(text))
	for i := 0; i < len(text); {
		switch {
		case strings.HasPrefix(text[i:], "/*"):
			i += 2
			for depth := 1; i < len(text) && depth > 0; {
				switch {
				case strings.HasPrefix(text[i:], "/*"):
					depth++
					i += 2
				case strings.HasPrefix(text[i:], "*/"):
					depth--
					i += 2
				default:
					i++
				}
			}
			out = append(out, ' ')
		case strings.HasPrefix(text[i:], "--"):
			for i < len(text) && text[i] != '\n' && !strings.HasPrefix(text[i:], `\n`) {
				i++
			}
			out = append(out, ' ')
		case text[i] == '\\' && i+1 < len(text) && strings.IndexByte("ntr", text[i+1]) >= 0:
			out = append(out, ' ')
			i += 2
		default:
			out = append(out, text[i])
			i++
		}
	}
	return string(out)
}

// agentEventWriteKind reads a string literal or a whole file: "insert" for
// an INSERT statement into the table, "other" for a write in a form that the
// guard cannot check, and "" for text that does not write to the table. The
// text is read twice, as written and with its SQL comments taken as
// separators: in text that is not SQL, "--" and "/*" mean something else,
// and neither reading may hide a statement from the other.
func agentEventWriteKind(text string) string {
	if !strings.Contains(strings.ToLower(text), "agent_event") {
		// Every pattern needs the name. Most files do not have it, and the
		// second pattern is slow over a whole file.
		return ""
	}
	readings := []string{agentEventNormalise(text), agentEventNormalise(agentEventStripComments(text))}
	for _, reading := range readings {
		if agentEventInsert.MatchString(reading) {
			return "insert"
		}
	}
	for _, reading := range readings {
		if agentEventOtherWrite.MatchString(reading) {
			return "other"
		}
	}
	return ""
}

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
	others  []token.Pos                // literals that write to it in a form the guard cannot check
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
//
// A function literal on the path runs inside the statement that holds it
// only when that statement calls it or passes it to a call. One that is
// stored or returned can run anywhere, so only the blocks inside it count.
func agentEventLockDominates(path []ast.Node) bool {
	inside := 0
	for i := 1; i < len(path); i++ {
		if _, ok := path[i].(*ast.FuncLit); ok {
			if _, called := path[i-1].(*ast.CallExpr); !called {
				inside = i
			}
		}
	}
	for i, node := range path {
		block, ok := node.(*ast.BlockStmt)
		if !ok || i < inside || i+1 == len(path) {
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

// agentEventExecCall returns the call that executes the statement at the end
// of path: an Exec, Query or QueryRow call that has the literal as its SQL
// argument, the one after the context, alone or as the leftmost operand of a
// concatenation. It returns nil when the statement is not executed where it
// is written.
func agentEventExecCall(path []ast.Node) *ast.CallExpr {
	i := len(path) - 1
	for i > 0 {
		sum, ok := path[i-1].(*ast.BinaryExpr)
		if !ok || sum.Op != token.ADD || ast.Node(sum.X) != path[i] {
			break
		}
		i--
	}
	if i <= 0 {
		return nil
	}
	call, ok := path[i-1].(*ast.CallExpr)
	if !ok || len(call.Args) < 2 || ast.Node(call.Args[1]) != path[i] {
		return nil
	}
	if fun, ok := call.Fun.(*ast.SelectorExpr); ok {
		switch fun.Sel.Name {
		case "Exec", "Query", "QueryRow":
			return call
		}
	}
	return nil
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
					if value, err := strconv.Unquote(node.Value); err == nil {
						switch agentEventWriteKind(value) {
						case "insert":
							p.inserts = append(p.inserts, agentEventUse{fn: current, node: node})
						case "other":
							p.others = append(p.others, node.Pos())
						}
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
			text += fmt.Sprintf(" (the lock at line %d does not count: it must stand before line %d in a block around it, as an if, assignment or expression statement, with no stored or returned function literal between them)", at, line)
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
// and one line for each write to the table that the guard cannot check or
// that some path may reach without the collection lock.
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
		// The lock must cover the call that executes the statement. Text
		// that goes anywhere else can be executed anywhere else.
		call := agentEventExecCall(agentEventPath(insert.fn.decl, insert.node))
		if call == nil {
			violations = append(violations, pkg.at(insert.node.Pos())+" holds an insert into cairn.agent_event that is not executed where it is written: the statement must be the SQL argument of the Exec, Query or QueryRow call that runs it, so that this guard can check the lock on the path to that call")
			continue
		}
		if ok, why := pkg.locked(agentEventUse{fn: insert.fn, node: call}, 0, map[*agentEventFunc]bool{}); !ok {
			violations = append(violations, pkg.at(insert.node.Pos())+" inserts into cairn.agent_event on a path that has not taken lock(ctx, tx, \"agent-events:\"+repo): "+why)
		}
	}
	for _, pos := range pkg.others {
		violations = append(violations, pkg.at(pos)+" can write to cairn.agent_event in a form this guard cannot check (the table named on its own, as for CopyFrom or a statement joined from constants, MERGE, or COPY ... FROM): write an INSERT statement that names the table, as the literal SQL argument of the call that runs it")
	}
	sort.Strings(sites)
	return sites, violations
}

// agentEventAudit compares the insert sites that the analysis found with the
// reviewed ones, one entry for each statement, and says what differs.
func agentEventAudit(found, audited []string) []string {
	extra := map[string]int{}
	for _, site := range found {
		extra[site]++
	}
	for _, site := range audited {
		extra[site]--
	}
	names := make([]string, 0, len(extra))
	for name := range extra {
		names = append(names, name)
	}
	sort.Strings(names)
	var problems []string
	for _, name := range names {
		switch n := extra[name]; {
		case n > 0:
			problems = append(problems, fmt.Sprintf("%s has %d insert statement(s) into cairn.agent_event that agentEventAuditedSites does not list: a new site must be reviewed for its lock before it is added to the list", name, n))
		case n < 0:
			problems = append(problems, fmt.Sprintf("agentEventAuditedSites lists %s for %d insert statement(s) that the guard does not find: remove the entry when the statement is gone, and find out why when it is not", name, -n))
		}
	}
	return problems
}

// agentEventStrayFiles lists the files under root, outside package core, that
// write to the table, in any language. The guard cannot check a lock there.
func agentEventStrayFiles(root string) ([]string, error) {
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
		if agentEventWriteKind(string(raw)) != "" {
			stray = append(stray, rel)
		}
		return nil
	})
	return stray, err
}

func TestAgentEventInsertSitesTakeTheCollectionLock(t *testing.T) {
	root := agentEventRepoRoot(t)

	// 1. No file outside package core writes to the table, in any language.
	stray, err := agentEventStrayFiles(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(stray) > 0 {
		t.Fatalf("cairn.agent_event is written outside package core, where this guard cannot check the collection lock: %v", stray)
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

	// 3. The insert sites are the reviewed ones, no more and no fewer.
	for _, problem := range agentEventAudit(sites, agentEventAuditedSites) {
		t.Error(problem)
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
	for text, want := range map[string]string{
		"INSERT INTO cairn.agent_event(event_id) VALUES($1)":     "insert",
		"insert into cairn.agent_event (\n event_id) values($1)": "insert",
		"INSERT INTO cairn.agent_event":                          "insert",
		"INSERT INTO cairn.agent_event SELECT * FROM staged":     "insert",
		"WITH s AS (SELECT 1) INSERT INTO cairn.agent_event(a)":  "insert",
		`INSERT INTO "cairn"."agent_event"(event_id) VALUES($1)`: "insert",
		`insert into cairn."agent_event" (event_id) values($1)`:  "insert",
		`INSERT INTO "cairn" . "agent_event" (event_id)`:         "insert",
		"INSERT INTO `cairn`.`agent_event`(event_id) VALUES($1)": "insert",
		"INSERT INTO agent_event(event_id) VALUES($1)":           "insert",
		`INSERT INTO "agent_event"(event_id) VALUES($1)`:         "insert",
		"InSeRt\n\t  INTO\r\n  Cairn.Agent_Event\n(event_id)":    "insert",
		// A quote ends a token, and so does a comment.
		`INSERT INTO"cairn"."agent_event"(event_id) VALUES($1)`:             "insert",
		"INSERT /* c */ INTO cairn.agent_event(event_id) VALUES($1)":        "insert",
		"INSERT INTO /* c */ cairn.agent_event(event_id) VALUES($1)":        "insert",
		"INSERT/**/INTO/**/cairn.agent_event(event_id) VALUES($1)":          "insert",
		"INSERT INTO cairn /* c */ . /* d */ agent_event(event_id)":         "insert",
		`INSERT INTO "cairn"/* c */."agent_event"(event_id)`:                "insert",
		"INSERT /* a /* nested */ b */ INTO cairn.agent_event(event_id)":    "insert",
		"INSERT -- why\n INTO -- where\n cairn.agent_event(event_id)":       "insert",
		"INSERT INTO -- an unfinished /* comment\n agent_event(event_id)":   "insert",
		`run("INSERT -- why\n INTO cairn.agent_event(event_id) VALUES(1)")`: "insert",
		`run("INSERT\n\tINTO\r\ncairn.agent_event(event_id) VALUES(1)")`:    "insert",
		"MERGE /* c */ INTO cairn.agent_event e USING staged s ON false":    "other",
		"COPY /* c */ cairn.agent_event(event_id) FROM STDIN":               "other",
		// A flag is not a comment: the reading as written still stands.
		`psql --command "INSERT INTO cairn.agent_event(event_id) VALUES('x')"`:   "insert",
		"INSERT INTO /* cairn.agent_event */ cairn.agent_delivery(a) VALUES($1)": "",
		"SELECT 1 -- then INSERT INTO\n FROM cairn.agent_event_review":           "",
		// As the statement reads in a script, with the quotes of the shell.
		`psql -c "INSERT INTO \"cairn\".\"agent_event\"(event_id) VALUES('x')"`: "insert",

		"INSERT INTO cairn.agent_event_review(event_id) VALUES($1)":     "",
		`INSERT INTO "cairn"."agent_event_review"(event_id) VALUES($1)`: "",
		"INSERT INTO cairn.agent_event_admission_refusal(a) VALUES($1)": "",
		"INSERT INTO cairn.agent_delivery(delivery_id) VALUES($1)":      "",
		"INSERT INTO archive.agent_event(event_id) VALUES($1)":          "",
		"INSERT INTO cairn.my_agent_event(event_id) VALUES($1)":         "",
		"UPDATE cairn.agent_event SET summary=$1":                       "",
		"SELECT 1 FROM cairn.agent_event WHERE event_id=$1":             "",
		"COPY cairn.agent_event TO STDOUT":                              "",
		"COPY cairn.agent_event_review(event_id) FROM STDIN":            "",
		"MERGE INTO cairn.agent_event_review r USING staged s ON false": "",
		"agent_event_review":                            "",
		"the agent_event table":                         "",
		`pgx.Identifier{"cairn", "agent_event_review"}`: "",
		`require_fresh("agent_event", "agent_session")`: "",

		"agent_event":                                 "other",
		"cairn.agent_event":                           "other",
		`"cairn"."agent_event"`:                       "other",
		`pgx.Identifier{"cairn", "agent_event"}`:      "other",
		`pgx.Identifier{schema, "agent_event"}`:       "other",
		`sql.Identifier("agent_event")`:               "other",
		"COPY cairn.agent_event(event_id) FROM STDIN": "other",
		`copy "agent_event" from stdin`:               "other",
		"MERGE INTO cairn.agent_event e USING staged s ON false WHEN NOT MATCHED THEN INSERT (event_id) VALUES (s.event_id)": "other",
	} {
		if got := agentEventWriteKind(text); got != want {
			t.Errorf("%q: write recognised as %q, want %q", text, got, want)
		}
	}
}

// The audited list must notice a statement that comes, goes or moves.
func TestAgentEventInsertGuardAuditsSites(t *testing.T) {
	audited := []string{"a.go:one", "b.go:two"}
	const added = " insert statement(s) into cairn.agent_event that agentEventAuditedSites does not list: a new site must be reviewed for its lock before it is added to the list"
	cases := []struct {
		name  string
		found []string
		want  []string // the start of each problem, in order
	}{
		{name: "the audited sites", found: []string{"a.go:one", "b.go:two"}},
		{name: "the audited sites in another order", found: []string{"b.go:two", "a.go:one"}},
		{name: "a new site", found: []string{"a.go:one", "b.go:two", "c.go:three"}, want: []string{"c.go:three has 1" + added}},
		{name: "a second statement in an audited function", found: []string{"a.go:one", "a.go:one", "b.go:two"}, want: []string{"a.go:one has 1" + added}},
		{name: "an audited site that is gone", found: []string{"a.go:one"}, want: []string{"agentEventAuditedSites lists b.go:two for 1 insert statement(s) that the guard does not find"}},
		{name: "a statement that moved to another function", found: []string{"a.go:one", "b.go:moved"}, want: []string{"b.go:moved has 1" + added, "agentEventAuditedSites lists b.go:two for 1"}},
		{name: "no site at all", want: []string{"agentEventAuditedSites lists a.go:one for 1", "agentEventAuditedSites lists b.go:two for 1"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			problems := agentEventAudit(tc.found, audited)
			if len(problems) != len(tc.want) {
				t.Fatalf("problems = %q, want %d", problems, len(tc.want))
			}
			for i, problem := range problems {
				if !strings.HasPrefix(problem, tc.want[i]) {
					t.Errorf("problem %q does not start with %q", problem, tc.want[i])
				}
			}
		})
	}
}

// The scan outside package core reads every language through the same
// patterns, and skips what the real tree needs skipped.
func TestAgentEventInsertGuardScansFilesOutsideCore(t *testing.T) {
	const insert = "INSERT INTO cairn.agent_event(event_id) VALUES ('x');\n"
	files := map[string]string{
		// Writes, each in the spelling of its language.
		"scripts/seed.py":   "cur.execute('INSERT INTO \"cairn\".\"agent_event\" (event_id) VALUES (%s)', (event_id,))\n",
		"scripts/seed.sh":   "psql -c \"insert into \\\"cairn\\\".\\\"agent_event\\\" (event_id) values ('x')\"\n",
		"scripts/bare.sql":  "INSERT\n  INTO agent_event (event_id)\nVALUES ('x');\n",
		"cmd/tool/copy.go":  "package main\n\nfunc seed() { tx.CopyFrom(ctx, pgx.Identifier{\"cairn\", \"agent_event\"}, cols, rows) }\n",
		"web/app.js":        "const q = `MERGE INTO cairn.agent_event e USING staged s ON false`;\n",
		"core/tool/tool.go": "package tool\n\nconst q = `" + insert + "`\n",
		"rust/src/seed.rs":  "client.batch_execute(\"COPY cairn.agent_event (event_id) FROM STDIN\")?;\n",
		// Not writes to this table.
		"scripts/review.py": "cur.execute('INSERT INTO \"cairn\".\"agent_event_review\" (event_id) VALUES (%s)', (event_id,))\n",
		"scripts/read.py":   "host.require_fresh(\"agent_event\", \"agent_session\")\ncur.execute('SELECT count(*) FROM cairn.agent_event')\n",
		// Skipped: package core is read as Go by the analysis, and the rest
		// is tests, schema, retained sources, tool directories and prose.
		"core/store.go":          "package core\n\nconst q = `" + insert + "`\n",
		"core/schema/001.sql":    insert,
		"docs/sources/old.sql":   insert,
		"docs/notes.md":          insert,
		"scripts/test_seed.py":   insert,
		"scripts/check_seed.py":  insert,
		"cmd/tool/seed_test.go":  "package main\n\nconst q = `" + insert + "`\n",
		"node_modules/x/seed.js": insert,
		"vendor/x/seed.go":       insert,
		"target/seed.rs":         insert,
		"bin/seed.sh":            insert,
		".cache/seed.sql":        insert,
	}
	root := t.TempDir()
	for name, content := range files {
		path := filepath.Join(root, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	stray, err := agentEventStrayFiles(root)
	if err != nil {
		t.Fatal(err)
	}
	for i := range stray {
		stray[i] = filepath.ToSlash(stray[i])
	}
	sort.Strings(stray)
	want := "cmd/tool/copy.go core/tool/tool.go rust/src/seed.rs scripts/bare.sql scripts/seed.py scripts/seed.sh web/app.js"
	if got := strings.Join(stray, " "); got != want {
		t.Errorf("files that write to the table:\n got %s\nwant %s", got, want)
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
	// Most cases put their statements before the insert of this function, or
	// change the SQL argument of its Exec call, so a rejected case differs
	// from an accepted one in that part only.
	const shell = `
func publish(ctx context.Context, tx pgx.Tx, repo, id, kind string, serial bool) error {%s
	_, err := tx.Exec(ctx, %s, id)
	return err
}
`
	const statement = `"INSERT INTO cairn.agent_event(event_id) VALUES($1)"`
	const house = `
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}`
	const notExecuted = " holds an insert into cairn.agent_event that is not executed where it is written"
	const notChecked = " can write to cairn.agent_event in a form this guard cannot check"
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
		before     string   // statements before the insert in shell, and
		sql        string   // the SQL argument of its Exec call, or
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
			name: "lock before a closure that is called at once",
			before: house + `
	func() {
		_, _ = tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", kind)
	}()`,
			sites: 2,
		},
		{
			name: "lock before a closure that is returned with the insert",
			sources: []string{`
func (s *Store) Prepare(ctx context.Context, tx pgx.Tx, repo string) (func(string) error, error) {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return nil, err
	}
	return func(id string) error {
		_, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", id)
		return err
	}, nil
}
`},
			sites: 1, violations: 1, want: "the lock at line 4 does not count",
		},
		{
			name: "closure that is stored and takes the lock itself",
			sources: []string{`
func (s *Store) Prepare(ctx context.Context, repo string) {
	s.publish = func(tx pgx.Tx, id string) error {
		if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
			return err
		}
		_, err := tx.Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", id)
		return err
	}
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
		{name: "p house form at function top level", before: house, sites: 1},
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

		// The table, however the statement spells it.
		{name: "quoted schema and table", sql: `"INSERT INTO \"cairn\".\"agent_event\"(event_id) VALUES($1)"`, sites: 1, violations: 1, want: "it has no caller"},
		{name: "quoted schema and table below the lock", before: house, sql: `"INSERT INTO \"cairn\".\"agent_event\"(event_id) VALUES($1)"`, sites: 1},
		{name: "quoted table only", sql: `"insert into cairn.\"agent_event\" (event_id) values($1)"`, sites: 1, violations: 1, want: "it has no caller"},
		{name: "backquoted schema and table", sql: "\"INSERT INTO `cairn`.`agent_event`(event_id) VALUES($1)\"", sites: 1, violations: 1, want: "it has no caller"},
		{name: "bare table name without schema", sql: `"INSERT INTO agent_event(event_id) VALUES($1)"`, sites: 1, violations: 1, want: "it has no caller"},
		{name: "mixed case and odd whitespace between the words", sql: `"InSeRt\n\t  INTO\r\n  Cairn . Agent_Event\n\t(event_id) VALUES($1)"`, sites: 1, violations: 1, want: "it has no caller"},
		{name: "quoted name of another table", sql: `"INSERT INTO \"cairn\".\"agent_event_review\"(event_id) VALUES($1)"`, sites: 0},
		{name: "quoted table directly after the keyword", sql: `"INSERT INTO\"cairn\".\"agent_event\"(event_id) VALUES($1)"`, sites: 1, violations: 1, want: "it has no caller"},
		{name: "block comment between the keywords", sql: `"INSERT /* why */ INTO cairn.agent_event(event_id) VALUES($1)"`, sites: 1, violations: 1, want: "it has no caller"},
		{name: "block comment between the keywords below the lock", before: house, sql: `"INSERT /* why */ INTO cairn.agent_event(event_id) VALUES($1)"`, sites: 1},
		{name: "block comment between the schema and the table", sql: `"INSERT INTO cairn./* which */agent_event(event_id) VALUES($1)"`, sites: 1, violations: 1, want: "it has no caller"},
		{name: "line comments between the keywords", sql: `"INSERT -- why\n INTO -- where\n cairn.agent_event(event_id) VALUES($1)"`, sites: 1, violations: 1, want: "it has no caller"},
		{name: "line comments between the keywords in a raw string", sql: "`INSERT -- why\n INTO -- where\n cairn.agent_event(event_id) VALUES($1)`", sites: 1, violations: 1, want: "it has no caller"},
		{name: "comment that names the table in a write to another one", sql: `"INSERT INTO /* not cairn.agent_event */ cairn.agent_delivery(delivery_id) VALUES($1)"`, sites: 0},

		// The statement must be executed where it is written.
		{
			name: "helper that returns the statement from inside a locked block, executed by another function",
			sources: []string{`
func (s *Store) statement(ctx context.Context, tx pgx.Tx, repo string) (string, error) {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return "", err
	}
	return "INSERT INTO cairn.agent_event(event_id) VALUES($1)", nil
}

func (s *Store) Publish(ctx context.Context, tx pgx.Tx, repo, id string) error {
	statement, err := s.statement(ctx, tx, repo)
	if err != nil {
		return err
	}
	if err = tx.Commit(ctx); err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx, statement, id)
	return err
}
`},
			sites: 1, violations: 1, want: "case_0.go:7" + notExecuted,
		},
		{
			name: "statement in a local variable and executed below the lock, refused by design",
			before: house + `
	statement := "INSERT INTO cairn.agent_event(event_id) VALUES($1)"`,
			sql:   "statement",
			sites: 1, violations: 1, want: "case_0.go:7" + notExecuted,
		},
		{
			name: "statement passed to a wrapper function below the lock",
			sources: []string{`
func (s *Store) Publish(ctx context.Context, tx pgx.Tx, repo, id string) error {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	return s.exec(ctx, tx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", id)
}
`},
			sites: 1, violations: 1, want: "case_0.go:7" + notExecuted,
		},
		{
			name: "statement prepared below the lock and executed later",
			sources: []string{`
func (s *Store) Publish(ctx context.Context, conn *sql.Conn, tx pgx.Tx, repo, id string) error {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	prepared, err := conn.PrepareContext(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)")
	if err != nil {
		return err
	}
	s.insertEvent = prepared
	return nil
}
`},
			sites: 1, violations: 1, want: "case_0.go:7" + notExecuted,
		},
		{
			name: "statement stored in a composite literal below the lock",
			before: house + `
	statements := []string{"INSERT INTO cairn.agent_event(event_id) VALUES($1)"}`,
			sql:   "statements[0]",
			sites: 1, violations: 1, want: "case_0.go:7" + notExecuted,
		},
		{
			name: "statement queued in a batch below the lock",
			sources: []string{`
func publish(ctx context.Context, tx pgx.Tx, batch *pgx.Batch, repo, id string) error {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	batch.Queue("INSERT INTO cairn.agent_event(event_id) VALUES($1)", id)
	return tx.SendBatch(ctx, batch).Close()
}
`},
			sites: 1, violations: 1, want: "case_0.go:7" + notExecuted,
		},
		{
			name: "statement executed by a function named Exec that is not a method, below the lock",
			sources: []string{`
func publish(ctx context.Context, tx pgx.Tx, repo, id string) error {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	return Exec(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1)", tx, id)
}
`},
			sites: 1, violations: 1, want: "case_0.go:7" + notExecuted,
		},
		{
			name:   "statement as a parameter value of another statement below the lock",
			before: house,
			sql:    `"SELECT cairn.run($1)", "INSERT INTO cairn.agent_event(event_id) VALUES('x')"`,
			sites:  1, violations: 1, want: "case_0.go:7" + notExecuted,
		},
		{
			name:   "statement on the right of a concatenation below the lock",
			before: house,
			sql:    `prefix+"INSERT INTO cairn.agent_event(event_id) VALUES($1)"`,
			sites:  1, violations: 1, want: "case_0.go:7" + notExecuted,
		},
		{
			name:   "house form with a concatenated statement as the Exec argument",
			before: house,
			sql:    `"INSERT INTO cairn.agent_event("+cols+") VALUES("+values+")"`,
			sites:  1,
		},
		{
			name: "house form with QueryRow and with Query",
			sources: []string{`
func position(ctx context.Context, tx pgx.Tx, repo, id string) (int64, error) {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return 0, err
	}
	var position int64
	err := tx.QueryRow(ctx, "INSERT INTO cairn.agent_event(event_id) VALUES($1) RETURNING position", id).Scan(&position)
	return position, err
}

func positions(ctx context.Context, tx pgx.Tx, repo string, ids []string) (pgx.Rows, error) {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return nil, err
	}
	return tx.Query(ctx, "INSERT INTO cairn.agent_event(event_id) SELECT unnest($1::uuid[]) RETURNING position", ids)
}
`},
			sites: 2,
		},

		// Other ways to write rows: recognised, and refused whatever the lock.
		{
			name: "table named for CopyFrom below the lock",
			sources: []string{`
func publish(ctx context.Context, tx pgx.Tx, repo string, rows [][]any) error {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	_, err := tx.CopyFrom(ctx, pgx.Identifier{"cairn", "agent_event"}, []string{"event_id"}, pgx.CopyFromRows(rows))
	return err
}
`},
			sites: 0, violations: 1, want: "case_0.go:7" + notChecked,
		},
		{
			name: "table name in a constant and joined into the statement below the lock",
			sources: []string{`
const eventTable = "cairn.agent_event"

func publish(ctx context.Context, tx pgx.Tx, repo, id string) error {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, "INSERT INTO "+eventTable+"(event_id) VALUES($1)", id)
	return err
}
`},
			sites: 0, violations: 1, want: "case_0.go:3" + notChecked,
		},
		{
			name:   "MERGE below the lock",
			before: house,
			sql:    `"MERGE INTO cairn.agent_event e USING staged s ON e.event_id = s.event_id WHEN NOT MATCHED THEN INSERT (event_id) VALUES (s.event_id)"`,
			sites:  0, violations: 1, want: "case_0.go:7" + notChecked,
		},
		{
			name: "COPY FROM below the lock",
			sources: []string{`
func publish(ctx context.Context, tx pgx.Tx, repo string, rows io.Reader) error {
	if err := lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	_, err := tx.Conn().PgConn().CopyFrom(ctx, rows, "COPY cairn.agent_event(event_id) FROM STDIN")
	return err
}
`},
			sites: 0, violations: 1, want: "case_0.go:7" + notChecked,
		},
		{
			name: "reads of the table and writes to other tables",
			sources: []string{`
func export(ctx context.Context, tx pgx.Tx, out io.Writer, id string) error {
	if _, err := tx.Conn().PgConn().CopyTo(ctx, out, "COPY cairn.agent_event TO STDOUT"); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, "UPDATE cairn.agent_event SET summary=$2 WHERE event_id=$1", id, "seen"); err != nil {
		return err
	}
	if _, err := tx.CopyFrom(ctx, pgx.Identifier{"cairn", "agent_event_review"}, []string{"event_id"}, pgx.CopyFromRows(nil)); err != nil {
		return err
	}
	return tx.QueryRow(ctx, "SELECT count(*) FROM cairn.agent_event WHERE event_id=$1", id).Scan(new(int))
}
`},
			sites: 0,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			sources := tc.sources
			if sources == nil {
				sql := tc.sql
				if sql == "" {
					sql = statement
				}
				sources = []string{fmt.Sprintf(shell, tc.before, sql)}
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
