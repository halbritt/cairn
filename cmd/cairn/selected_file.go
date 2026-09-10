package main

import (
	"fmt"
	"io"
	"os"
	"syscall"
)

// Callers request their limit plus one byte to detect overflow without reading
// an unbounded source. Nonblocking open permits refusal of a FIFO without a writer.
func readRegularFilePrefix(path string, maximum int) ([]byte, error) {
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, fmt.Errorf("open selected file: %w", err)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, fmt.Errorf("inspect selected file: %w", err)
	}
	if !info.Mode().IsRegular() {
		return nil, invalid("selected file must be regular")
	}
	body, err := io.ReadAll(io.LimitReader(file, int64(maximum)))
	if err != nil {
		return nil, fmt.Errorf("read selected file: %w", err)
	}
	return body, nil
}
