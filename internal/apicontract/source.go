// Package apicontract generates Cairn's HTTP API contract from the localapi
// source. Routes, request and response types, error codes and the envelope
// are read from the type-checked handlers, so the document cannot silently
// drift from the implementation.
package apicontract

import (
	"fmt"
	"go/ast"
	"go/importer"
	"go/parser"
	"go/token"
	"go/types"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// Route is one or more /v1 paths served by a single serveJSON call.
type Route struct {
	Operations []string
	Request    types.Type
	Response   types.Type
	// RequiresLocalDestination records a handler guard on destination.AllowLocal:
	// hosted profiles are refused before the request body is decoded.
	RequiresLocalDestination bool
	File                     string
}

// ErrorCode is a status/code pair the handlers can write.
type ErrorCode struct {
	HTTPStatus int    `json:"http_status"`
	Code       string `json:"code"`
	Source     string `json:"source"`
}

// Source is the extracted API surface of one localapi package directory.
type Source struct {
	Routes      []Route
	Envelope    types.Type
	Errors      []ErrorCode
	DefaultCode int // HTTP status serveJSON uses for store codes it does not list
	Headers     []string
	// StoreCodes are literal error codes constructed in core and localapi.
	// Codes built from variables are not listed; the set is open.
	StoreCodes []string
	fset       *token.FileSet
}

// Load parses and type-checks the non-test Go files in dir, and reads literal
// store error codes from the sibling core package.
func Load(dir string) (*Source, error) {
	source, err := load(dir)
	if err != nil {
		return nil, err
	}
	codes := map[string]bool{}
	for _, directory := range []string{dir, filepath.Join(dir, "..", "core")} {
		if err := scanCodes(directory, codes); err != nil {
			return nil, err
		}
	}
	for code := range codes {
		source.StoreCodes = append(source.StoreCodes, code)
	}
	sort.Strings(source.StoreCodes)
	return source, nil
}

// scanCodes collects failure("CODE", ...) calls and Error{Code: "CODE"} literals.
func scanCodes(dir string, codes map[string]bool) error {
	names, err := filepath.Glob(filepath.Join(dir, "*.go"))
	if err != nil {
		return err
	}
	fset := token.NewFileSet()
	for _, name := range names {
		if strings.HasSuffix(name, "_test.go") {
			continue
		}
		file, err := parser.ParseFile(fset, name, nil, 0)
		if err != nil {
			return err
		}
		ast.Inspect(file, func(node ast.Node) bool {
			switch node := node.(type) {
			case *ast.CallExpr:
				if name, ok := node.Fun.(*ast.Ident); ok && name.Name == "failure" && len(node.Args) > 0 {
					if code, ok := stringLiteral(node.Args[0]); ok {
						codes[code] = true
					}
				}
			case *ast.KeyValueExpr:
				if key, ok := node.Key.(*ast.Ident); ok && key.Name == "Code" {
					if code, ok := stringLiteral(node.Value); ok && code != "" && strings.ToUpper(code) == code {
						codes[code] = true
					}
				}
			}
			return true
		})
	}
	return nil
}

func load(dir string) (*Source, error) {
	fset := token.NewFileSet()
	names, err := filepath.Glob(filepath.Join(dir, "*.go"))
	if err != nil {
		return nil, err
	}
	sort.Strings(names)
	var files []*ast.File
	for _, name := range names {
		if strings.HasSuffix(name, "_test.go") {
			continue
		}
		file, err := parser.ParseFile(fset, name, nil, 0)
		if err != nil {
			return nil, err
		}
		files = append(files, file)
	}
	info := &types.Info{Types: map[ast.Expr]types.TypeAndValue{}, Defs: map[*ast.Ident]types.Object{}, Uses: map[*ast.Ident]types.Object{}}
	config := types.Config{Importer: importer.ForCompiler(fset, "source", nil)}
	pkg, err := config.Check("github.com/halbritt/cairn/localapi", fset, files, info)
	if err != nil {
		return nil, fmt.Errorf("type-check localapi: %w", err)
	}
	source := &Source{fset: fset}
	envelope := pkg.Scope().Lookup("response")
	if envelope == nil {
		return nil, fmt.Errorf("localapi has no response envelope type")
	}
	source.Envelope = envelope.Type()
	headers := map[string]bool{}
	for _, file := range files {
		if err := source.scanFile(file, info, headers); err != nil {
			return nil, err
		}
	}
	for header := range headers {
		source.Headers = append(source.Headers, header)
	}
	sort.Strings(source.Headers)
	sort.Slice(source.Routes, func(i, j int) bool { return source.Routes[i].Operations[0] < source.Routes[j].Operations[0] })
	sort.Slice(source.Errors, func(i, j int) bool {
		a, b := source.Errors[i], source.Errors[j]
		if a.Code != b.Code {
			return a.Code < b.Code
		}
		if a.HTTPStatus != b.HTTPStatus {
			return a.HTTPStatus < b.HTTPStatus
		}
		return a.Source < b.Source
	})
	if len(source.Routes) == 0 {
		return nil, fmt.Errorf("no routes found")
	}
	seen := map[string]bool{}
	for _, route := range source.Routes {
		for _, operation := range route.Operations {
			if seen[operation] {
				return nil, fmt.Errorf("operation %s is served twice", operation)
			}
			seen[operation] = true
		}
	}
	return source, nil
}

func stringLiteral(expr ast.Expr) (string, bool) {
	literal, ok := expr.(*ast.BasicLit)
	if !ok || literal.Kind != token.STRING {
		return "", false
	}
	value, err := strconv.Unquote(literal.Value)
	return value, err == nil
}

func intLiteral(expr ast.Expr) (int, bool) {
	literal, ok := expr.(*ast.BasicLit)
	if !ok || literal.Kind != token.INT {
		return 0, false
	}
	value, err := strconv.Atoi(literal.Value)
	return value, err == nil
}

func (s *Source) position(node ast.Node) string {
	position := s.fset.Position(node.Pos())
	return filepath.Base(position.Filename)
}

func (s *Source) scanFile(file *ast.File, info *types.Info, headers map[string]bool) error {
	var failure error
	ast.Inspect(file, func(node ast.Node) bool {
		if failure != nil {
			return false
		}
		switch node := node.(type) {
		case *ast.FuncDecl:
			if node.Name.Name == "serveJSON" {
				failure = s.scanStatusMap(node)
			}
		case *ast.CallExpr:
			s.scanWriteError(node)
			s.scanHeader(node, headers)
		case *ast.CaseClause:
			var operations []string
			for _, expr := range node.List {
				if path, ok := stringLiteral(expr); ok && strings.HasPrefix(path, "/v1/") {
					operations = append(operations, strings.TrimPrefix(path, "/v1/"))
				}
			}
			if len(operations) > 0 {
				failure = s.scanRoute(node, operations, info)
			}
		}
		return true
	})
	return failure
}

// scanHeader records header names read from requests.
func (s *Source) scanHeader(call *ast.CallExpr, headers map[string]bool) {
	selector, ok := call.Fun.(*ast.SelectorExpr)
	if !ok || (selector.Sel.Name != "Get" && selector.Sel.Name != "Values") || len(call.Args) != 1 {
		return
	}
	inner, ok := selector.X.(*ast.SelectorExpr)
	if !ok || inner.Sel.Name != "Header" {
		return
	}
	if request, ok := inner.X.(*ast.Ident); !ok || request.Name != "r" {
		return // only the incoming request, not relay replies
	}
	if name, ok := stringLiteral(call.Args[0]); ok {
		headers[name] = true
	}
}

func (s *Source) scanWriteError(call *ast.CallExpr) {
	name, ok := call.Fun.(*ast.Ident)
	if !ok || name.Name != "writeError" || len(call.Args) < 3 {
		return
	}
	status, ok := intLiteral(call.Args[1])
	if !ok {
		return
	}
	code, ok := stringLiteral(call.Args[2])
	if !ok {
		code = "*" // a store error code passed through
	}
	s.Errors = append(s.Errors, ErrorCode{HTTPStatus: status, Code: code, Source: s.position(call)})
}

// scanStatusMap extracts serveJSON's store-code to HTTP status mapping.
func (s *Source) scanStatusMap(decl *ast.FuncDecl) error {
	found := false
	ast.Inspect(decl.Body, func(node ast.Node) bool {
		assign, ok := node.(*ast.AssignStmt)
		if ok && len(assign.Lhs) == 1 && len(assign.Rhs) == 1 {
			if name, ok := assign.Lhs[0].(*ast.Ident); ok && name.Name == "status" {
				if value, ok := intLiteral(assign.Rhs[0]); ok && assign.Tok == token.DEFINE {
					s.DefaultCode = value
				}
			}
		}
		clause, ok := node.(*ast.CaseClause)
		if !ok {
			return true
		}
		status := 0
		for _, statement := range clause.Body {
			assign, ok := statement.(*ast.AssignStmt)
			if !ok || len(assign.Lhs) != 1 {
				continue
			}
			if name, ok := assign.Lhs[0].(*ast.Ident); ok && name.Name == "status" {
				status, _ = intLiteral(assign.Rhs[0])
			}
		}
		for _, expr := range clause.List {
			if code, ok := stringLiteral(expr); ok && status != 0 {
				s.Errors = append(s.Errors, ErrorCode{HTTPStatus: status, Code: code, Source: "serveJSON"})
				found = true
			}
		}
		return true
	})
	if !found || s.DefaultCode == 0 {
		return fmt.Errorf("could not extract serveJSON status mapping")
	}
	return nil
}

func (s *Source) scanRoute(clause *ast.CaseClause, operations []string, info *types.Info) error {
	route := Route{Operations: operations, File: s.position(clause)}
	calls := 0
	for _, statement := range clause.Body {
		ast.Inspect(statement, func(node ast.Node) bool {
			if guard, ok := node.(*ast.IfStmt); ok && isLocalDestinationGuard(guard) {
				route.RequiresLocalDestination = true
			}
			call, ok := node.(*ast.CallExpr)
			if !ok {
				return true
			}
			name, ok := call.Fun.(*ast.Ident)
			if !ok || name.Name != "serveJSON" || len(call.Args) != 3 {
				return true
			}
			calls++
			signature, ok := info.Types[call.Args[2]].Type.Underlying().(*types.Signature)
			if !ok || signature.Params().Len() != 2 || signature.Results().Len() != 2 {
				return true
			}
			route.Request = signature.Params().At(1).Type()
			route.Response = signature.Results().At(0).Type()
			if _, isInterface := route.Response.Underlying().(*types.Interface); isInterface {
				route.Response = concreteReturn(call.Args[2], info)
			}
			return true
		})
	}
	if calls != 1 || route.Request == nil || route.Response == nil {
		return fmt.Errorf("route %v: expected one typed serveJSON call, found %d", operations, calls)
	}
	s.Routes = append(s.Routes, route)
	return nil
}

// isLocalDestinationGuard matches `if !x.AllowLocal { writeError(w, 403, ...) }`,
// which refuses hosted profiles before decoding. A condition that merely
// filters local records (for example `!AllowLocal && sensitivity == "local"`)
// is not a guard.
func isLocalDestinationGuard(statement *ast.IfStmt) bool {
	not, ok := statement.Cond.(*ast.UnaryExpr)
	if !ok || not.Op != token.NOT {
		return false
	}
	selector, ok := not.X.(*ast.SelectorExpr)
	if !ok || selector.Sel.Name != "AllowLocal" {
		return false
	}
	refuses := false
	ast.Inspect(statement.Body, func(node ast.Node) bool {
		call, ok := node.(*ast.CallExpr)
		if !ok {
			return true
		}
		if name, isIdent := call.Fun.(*ast.Ident); isIdent && name.Name == "writeError" && len(call.Args) >= 2 {
			status, _ := intLiteral(call.Args[1])
			refuses = refuses || status == 403
		}
		return true
	})
	return refuses
}

// concreteReturn resolves a handler declared to return an interface by the
// single concrete type of its successful return expressions.
func concreteReturn(handler ast.Expr, info *types.Info) types.Type {
	literal, ok := handler.(*ast.FuncLit)
	if !ok {
		return nil
	}
	var found types.Type
	ambiguous := false
	ast.Inspect(literal.Body, func(node ast.Node) bool {
		if _, nested := node.(*ast.FuncLit); nested {
			return false
		}
		ret, ok := node.(*ast.ReturnStmt)
		if !ok || len(ret.Results) != 2 {
			return true
		}
		typ := info.Types[ret.Results[0]].Type
		if typ == nil {
			return true
		}
		if _, isInterface := typ.Underlying().(*types.Interface); isInterface {
			return true // e.g. an untyped nil error path
		}
		if basic, ok := typ.(*types.Basic); ok && basic.Kind() == types.UntypedNil {
			return true
		}
		if found != nil && !types.Identical(found, typ) {
			ambiguous = true
		}
		found = typ
		return true
	})
	if ambiguous {
		return nil
	}
	return found
}
