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
	"os"
	"path/filepath"
	"strings"
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
	listen := f.String("listen", "", "optional remote TCP listener")
	cert := f.String("tls-cert", "", "remote listener TLS certificate")
	key := f.String("tls-key", "", "remote listener TLS private key")
	proxy := f.Bool("tls-terminated-proxy", false, "explicit loopback backend for a deployment-managed HTTPS proxy")
	hostname, _ := os.Hostname()
	machine := f.String("machine-id", strings.ToLower(strings.SplitN(hostname, ".", 2)[0]), "local directory machine ID")
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
	machineConfigured := false
	f.Visit(func(option *flag.Flag) {
		if option.Name == "machine-id" {
			machineConfigured = true
		}
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
	// A derived hostname is optional attribution, not a new startup requirement
	// for existing installations. Explicit machine IDs remain strictly checked.
	if err = handler.SetLocalMachineID(*machine); err != nil && machineConfigured {
		return err
	}
	remote, err := listenRemote(*listen, *cert, *key, *proxy)
	if err != nil {
		return err
	}
	if remote != nil {
		defer remote.Close()
	}
	local, err := listenPrivateUnix(*socket)
	if err != nil {
		return err
	}
	defer local.Close()
	endpoints := []apiEndpoint{{listener: local, handler: handler}}
	if remote != nil {
		endpoints = append(endpoints, apiEndpoint{listener: remote, handler: handler.RemoteHandler()})
	}
	return serveAPIs(ctx, endpoints)
}
