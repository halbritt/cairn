// Package apicontract generates Cairn's HTTP API contract from the localapi
// source and shared catalogs. Request and response types and the envelope
// are read from the type-checked handlers; refusal status mappings come from
// the same classification table used by the HTTP and CLI adapters.
package apicontract

import (
	"fmt"
	"go/ast"
	"go/build"
	"go/constant"
	"go/importer"
	"go/parser"
	"go/token"
	"go/types"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"

	"github.com/halbritt/cairn/internal/refusal"
	"github.com/halbritt/cairn/localapi"
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
		if match, err := build.Default.MatchFile(filepath.Dir(name), filepath.Base(name)); err != nil || !match {
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
		// Select files exactly as the default build does (build tags, GOOS).
		if match, err := build.Default.MatchFile(filepath.Dir(name), filepath.Base(name)); err != nil || !match {
			if err != nil {
				return nil, err
			}
			continue
		}
		file, err := parser.ParseFile(fset, name, nil, parser.ParseComments)
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
	source := &Source{fset: fset, DefaultCode: refusal.DefaultHTTPStatus}
	for code, status := range refusal.HTTPStatusOverrides() {
		source.Errors = append(source.Errors, ErrorCode{HTTPStatus: status, Code: code, Source: "serveJSON"})
	}
	envelope := pkg.Scope().Lookup("response")
	if envelope == nil {
		return nil, fmt.Errorf("localapi has no response envelope type")
	}
	source.Envelope = envelope.Type()
	headers := map[string]bool{}
	for _, file := range files {
		source.scanFile(file, info, headers)
	}
	routes, err := loadCatalogRoutes(pkg)
	if err != nil {
		return nil, err
	}
	source.Routes = routes
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

func (s *Source) scanFile(file *ast.File, info *types.Info, headers map[string]bool) {
	ast.Inspect(file, func(node ast.Node) bool {
		switch node := node.(type) {
		case *ast.CallExpr:
			s.scanWriteError(node)
			s.scanHeader(node, headers, info)
		}
		return true
	})
}

// scanHeader records header names read from incoming requests, whether the
// name is a literal or a constant.
func (s *Source) scanHeader(call *ast.CallExpr, headers map[string]bool, info *types.Info) {
	// Helpers such as ParseProtocol(r.Header, ProtocolHeader) receive the
	// request's headers and the name separately.
	passesRequestHeader := false
	for _, arg := range call.Args {
		if field, ok := arg.(*ast.SelectorExpr); ok && field.Sel.Name == "Header" {
			if receiver := info.Types[field.X].Type; receiver != nil && receiver.String() == "*net/http.Request" {
				passesRequestHeader = true
			}
		}
	}
	if passesRequestHeader {
		for _, arg := range call.Args {
			if value := info.Types[arg].Value; value != nil && value.Kind() == constant.String {
				headers[constant.StringVal(value)] = true
			}
		}
	}
	selector, ok := call.Fun.(*ast.SelectorExpr)
	if !ok || (selector.Sel.Name != "Get" && selector.Sel.Name != "Values") || len(call.Args) != 1 {
		return
	}
	inner, ok := selector.X.(*ast.SelectorExpr)
	if !ok || inner.Sel.Name != "Header" {
		return
	}
	if receiver := info.Types[inner.X].Type; receiver == nil || receiver.String() != "*net/http.Request" {
		return // replies read by the relay are not request headers
	}
	if value := info.Types[call.Args[0]].Value; value != nil && value.Kind() == constant.String {
		headers[constant.StringVal(value)] = true
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

func loadCatalogRoutes(pkg *types.Package) ([]Route, error) {
	catalog := localapi.Operations()
	routes := make([]Route, 0, len(catalog))
	for _, spec := range catalog {
		if spec.Name == "" || spec.Method != "POST" || spec.Path != "/v1/"+spec.Name || spec.SourceFile == "" {
			return nil, fmt.Errorf("invalid operation spec %q", spec.Name)
		}
		if spec.RequiresLocalDestination != (spec.LocalDestinationMessage != "") {
			return nil, fmt.Errorf("operation %s has inconsistent local destination guard configuration", spec.Name)
		}
		req, err := resolveType(pkg, spec.RequestType)
		if err != nil {
			return nil, fmt.Errorf("operation %s request type: %w", spec.Name, err)
		}
		resp, err := resolveType(pkg, spec.ResponseType)
		if err != nil {
			return nil, fmt.Errorf("operation %s response type: %w", spec.Name, err)
		}
		routes = append(routes, Route{
			Operations:               []string{spec.Name},
			Request:                  req,
			Response:                 resp,
			RequiresLocalDestination: spec.RequiresLocalDestination,
			File:                     spec.SourceFile,
		})
	}
	return routes, nil
}

func findPackage(root *types.Package, path string) *types.Package {
	if root.Path() == path {
		return root
	}
	for _, imp := range root.Imports() {
		if found := findPackage(imp, path); found != nil {
			return found
		}
	}
	return nil
}

func resolveType(pkg *types.Package, rt reflect.Type) (types.Type, error) {
	if rt == nil {
		return nil, fmt.Errorf("nil reflect.Type")
	}
	if rt.PkgPath() != "" && rt.Name() != "" {
		target := findPackage(pkg, rt.PkgPath())
		if target == nil {
			return nil, fmt.Errorf("package %s not found in imports of %s", rt.PkgPath(), pkg.Path())
		}
		obj := target.Scope().Lookup(rt.Name())
		if obj == nil {
			return nil, fmt.Errorf("type %s.%s not found", rt.PkgPath(), rt.Name())
		}
		return obj.Type(), nil
	}
	switch rt.Kind() {
	case reflect.Slice:
		elem, err := resolveType(pkg, rt.Elem())
		if err != nil {
			return nil, err
		}
		return types.NewSlice(elem), nil
	case reflect.Pointer:
		elem, err := resolveType(pkg, rt.Elem())
		if err != nil {
			return nil, err
		}
		return types.NewPointer(elem), nil
	case reflect.Struct:
		fields := make([]*types.Var, rt.NumField())
		tags := make([]string, rt.NumField())
		for i := 0; i < rt.NumField(); i++ {
			sf := rt.Field(i)
			ft, err := resolveType(pkg, sf.Type)
			if err != nil {
				return nil, err
			}
			fields[i] = types.NewField(token.NoPos, pkg, sf.Name, ft, sf.Anonymous)
			tags[i] = string(sf.Tag)
		}
		return types.NewStruct(fields, tags), nil
	case reflect.Bool:
		return types.Typ[types.Bool], nil
	case reflect.Int:
		return types.Typ[types.Int], nil
	case reflect.Int64:
		return types.Typ[types.Int64], nil
	case reflect.String:
		return types.Typ[types.String], nil
	default:
		return nil, fmt.Errorf("unsupported unnamed reflect kind %s", rt)
	}
}
