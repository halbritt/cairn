package main

import (
	"io"
	"os"
	"strings"
	"syscall"
	"unicode/utf8"
)

// No raw task text or private file path is included in refusal messages.
// Regular files are opened nonblocking/no-follow so FIFOs cannot hang a hook.
func readSemanticQuery(query, path string, fileSelected, stdin bool, input io.Reader) (string, error) {
	if !fileSelected && !stdin {
		return "", nil
	}
	if fileSelected && (stdin || path == "") {
		return "", invalid("choose one nonempty semantic-query-file or semantic-query-stdin")
	}
	if fileSelected {
		file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
		if err != nil {
			return "", invalid("cannot open semantic input file")
		}
		defer file.Close()
		info, err := file.Stat()
		if err != nil || !info.Mode().IsRegular() || info.Size() > 4096 {
			return "", invalid("semantic input must be a bounded regular file")
		}
		input = file
	}
	if input == nil {
		return "", invalid("semantic input is unavailable")
	}
	body, err := io.ReadAll(io.LimitReader(input, 4097))
	if err != nil {
		return "", invalid("cannot read semantic input")
	}
	text := string(body)
	if len(query)+len(body) > 4096 || !utf8.Valid(body) || strings.ContainsRune(text, 0) || strings.TrimSpace(text) == "" {
		return "", invalid("semantic input is invalid or combined query input exceeds limits")
	}
	return text, nil
}
