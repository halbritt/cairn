package admission

import (
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// PathBytes retains Linux surrogateescape filenames without replacing code points.
func PathBytes(points []rune) (string, error) {
	var b strings.Builder
	for _, p := range points {
		switch {
		case p == 0:
			return "", sourceError("ValueError", "embedded null byte")
		case p >= 0xdc80 && p <= 0xdcff:
			b.WriteByte(byte(p - 0xdc00))
		case p >= 0xd800 && p <= 0xdfff:
			return "", sourceError("UnicodeEncodeError", "filesystem surrogate encoding failure")
		default:
			b.WriteRune(p)
		}
	}
	return b.String(), nil
}

func PathPoints(raw string) []rune {
	var result []rune
	for len(raw) > 0 {
		p, n := utf8.DecodeRuneInString(raw)
		if p == utf8.RuneError && n == 1 {
			p = 0xdc00 + rune(raw[0])
		}
		result = append(result, p)
		raw = raw[n:]
	}
	return result
}

// OSProbe uses the originating process cwd, never event metadata, for relative paths.
type OSProbe struct{ Directory string }

func (p OSProbe) path(raw []rune) (string, error) {
	s, err := PathBytes(raw)
	if err != nil {
		return "", err
	}
	if !filepath.IsAbs(s) {
		s = filepath.Join(p.Directory, s)
	}
	return s, nil
}
func (p OSProbe) Resolve(v *value.Value) ([]rune, error) {
	if v == nil || v.Kind != value.String {
		return nil, sourceError("TypeError", "filesystem path must be a string")
	}
	raw, err := p.path(v.String)
	if err != nil {
		return nil, err
	}
	current := "/"
	parts := strings.Split(raw, "/")
	links := 0
	for len(parts) > 0 {
		part := parts[0]
		parts = parts[1:]
		if part == "" || part == "." {
			continue
		}
		if part == ".." {
			current = filepath.Dir(current)
			continue
		}
		next := filepath.Join(current, part)
		info, err := os.Lstat(next)
		if os.IsNotExist(err) {
			current = next
			continue
		}
		if err != nil {
			return nil, err
		}
		if info.Mode()&os.ModeSymlink == 0 {
			current = next
			continue
		}
		links++
		if links > 255 {
			return nil, &os.PathError{Op: "resolve", Path: next, Err: syscall.ELOOP}
		}
		target, err := os.Readlink(next)
		if err != nil {
			return nil, err
		}
		if filepath.IsAbs(target) {
			current = "/"
		}
		parts = append(strings.Split(target, "/"), parts...)
	}
	return PathPoints(current), nil
}
func (p OSProbe) IsDir(raw []rune) (bool, error) {
	s, err := p.path(raw)
	if err != nil {
		return false, err
	}
	info, err := os.Stat(s)
	if os.IsNotExist(err) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return info.IsDir(), nil
}
func (p OSProbe) Exists(raw []rune) (bool, error) {
	s, err := p.path(raw)
	if err != nil {
		return false, err
	}
	_, err = os.Stat(s)
	if os.IsNotExist(err) {
		return false, nil
	}
	return err == nil, err
}
