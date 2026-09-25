package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
	"github.com/halbritt/cairn/semantic"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

func serveLocal(ctx context.Context, dsn string, args []string) error {
	directory, err := dataDirectory()
	if err != nil {
		return err
	}
	f := flags("serve")
	config := f.String("identities", filepath.Join(directory, "identities.json"), "owner-only identity configuration")
	socket := f.String("socket", filepath.Join(directory, "api.sock"), "private Unix socket")
	semanticCommand := f.String("semantic-command", "", "optional absolute local CPU scoring executable")
	semanticStreamCommand := f.String("semantic-stream-command", "", "optional absolute reusable local CPU scoring executable")
	semanticIdle := f.Duration("semantic-idle-timeout", 30*time.Second, "positive idle lifetime for the reusable semantic worker")
	if err = f.Parse(args); err != nil {
		return invalid(err.Error())
	}
	if f.NArg() != 0 {
		return invalid("unexpected serve arguments")
	}
	if *semanticCommand != "" && *semanticStreamCommand != "" {
		return invalid("choose one semantic command mode")
	}
	idleConfigured := false
	f.Visit(func(option *flag.Flag) {
		if option.Name == "semantic-idle-timeout" {
			idleConfigured = true
		}
	})
	if idleConfigured && *semanticStreamCommand == "" {
		return invalid("semantic-idle-timeout requires --semantic-stream-command")
	}
	if *semanticIdle <= 0 {
		return invalid("semantic idle timeout must be positive")
	}
	info, err := os.Lstat(*config)
	if err != nil {
		return err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || stat.Uid != uint32(os.Geteuid()) {
		return invalid("identity configuration must be a regular owner-only file owned by the current user")
	}
	file, err := os.Open(*config)
	if err != nil {
		return err
	}
	defer file.Close()
	decoder := json.NewDecoder(io.LimitReader(file, 128*1024))
	decoder.DisallowUnknownFields()
	var identities []localapi.Identity
	if err = decoder.Decode(&identities); err != nil {
		return invalid("invalid identity configuration")
	}
	var extra any
	if err = decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		return invalid("expected one identity array")
	}
	var ranker core.SemanticRanker
	if *semanticCommand != "" {
		ranker, err = semantic.Command(*semanticCommand)
		if err != nil {
			return err
		}
	}
	if *semanticStreamCommand != "" {
		var closeWorker func()
		ranker, closeWorker, err = semantic.StreamCommandWithIdleTimeout(ctx, *semanticStreamCommand, *semanticIdle)
		if err != nil {
			return err
		}
		defer closeWorker()
	}
	handler, err := localapi.NewWithSemanticRanker(ctx, dsn, identities, ranker)
	if err != nil {
		return err
	}
	defer handler.Close()
	parent := filepath.Dir(*socket)
	if err = os.MkdirAll(parent, 0700); err != nil {
		return err
	}
	parentInfo, err := os.Stat(parent)
	if err != nil {
		return err
	}
	if parentInfo.Mode().Perm()&0077 != 0 {
		return invalid("socket directory must be owner-only")
	}
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: *socket, Net: "unix"})
	if err != nil {
		if errors.Is(err, syscall.EADDRINUSE) {
			return invalid("API socket is already in use")
		}
		return err
	}
	defer listener.Close()
	if err = os.Chmod(*socket, 0600); err != nil {
		return err
	}
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 35 * time.Second, WriteTimeout: 35 * time.Second, IdleTimeout: 60 * time.Second}
	defer server.Close()
	done := make(chan struct{})
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		select {
		case <-ctx.Done():
			shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if err := server.Shutdown(shutdownCtx); err != nil {
				_ = server.Close()
			}
		case <-done:
		}
	}()
	err = server.Serve(listener)
	close(done)
	<-stopped
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}
