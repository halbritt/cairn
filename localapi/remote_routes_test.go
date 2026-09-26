package localapi

import (
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

func TestEveryAPIRouteHasRemoteClassification(t *testing.T) {
	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatal(err)
	}
	routes := map[string]bool{}
	for _, name := range files {
		if strings.HasSuffix(name, "_test.go") {
			continue
		}
		file, err := parser.ParseFile(token.NewFileSet(), name, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		ast.Inspect(file, func(node ast.Node) bool {
			clause, ok := node.(*ast.CaseClause)
			if !ok {
				return true
			}
			for _, expr := range clause.List {
				literal, ok := expr.(*ast.BasicLit)
				if !ok || literal.Kind != token.STRING {
					continue
				}
				path, err := strconv.Unquote(literal.Value)
				if err != nil {
					t.Fatal(err)
				}
				if operation, ok := strings.CutPrefix(path, "/v1/"); ok {
					routes[operation] = true
				}
			}
			return true
		})
	}
	if len(routes) == 0 {
		t.Fatal("API route inventory was empty")
	}
	for route := range routes {
		if _, ok := remoteOperations[route]; !ok {
			t.Errorf("route %q needs an explicit remote decision", route)
		}
		if RequestBodyLimit(route) > MaxRequestBodyLimit {
			t.Errorf("relay would refuse legal %s bodies", route)
		}
	}
	for route := range remoteOperations {
		if !routes[route] {
			t.Errorf("classification %q has no API route", route)
		}
	}
}
