package main

import (
	"context"
	"encoding/json"
	"github.com/halbritt/cairn/core"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

func agentRequest(ctx context.Context, args []string, input io.Reader) (any, error) {
	directory, err := dataDirectory()
	if err != nil {
		return nil, err
	}
	f := flags("agent")
	tokenFile := f.String("token-file", filepath.Join(directory, "agent.token"), "owner-only API token file")
	socket := f.String("socket", filepath.Join(directory, "api.sock"), "Cairn Unix socket")
	if err = f.Parse(args); err != nil {
		return nil, invalid(err.Error())
	}
	if f.NArg() != 1 {
		return nil, invalid("agent requires an API operation and JSON on stdin")
	}
	operation := f.Arg(0)
	switch operation {
	case "refusal", "index", "expand", "create", "edit", "compile", "get", "usage", "usage-coverage", "evidence", "spawn", "terminal", "task-state", "bind-run", "delivery", "outcome", "assess-run", "use-report":
	default:
		return nil, invalid("unknown agent operation")
	}
	info, err := os.Lstat(*tokenFile)
	if err != nil {
		return nil, err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || stat.Uid != uint32(os.Geteuid()) || info.Size() > 512 {
		return nil, invalid("token file must be bounded and owner-only")
	}
	token, err := os.ReadFile(*tokenFile)
	if err != nil {
		return nil, err
	}
	secret := strings.TrimSpace(string(token))
	if secret == "" {
		return nil, invalid("empty API token")
	}
	body, err := io.ReadAll(io.LimitReader(input, 128*1024+1))
	if err != nil {
		return nil, err
	}
	if len(body) > 128*1024 {
		return nil, invalid("request exceeds 128 KiB")
	}
	request, err := http.NewRequestWithContext(ctx, "POST", "http://cairn/v1/"+operation, strings.NewReader(string(body)))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Authorization", "Bearer "+secret)
	request.Header.Set("Content-Type", "application/json")
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", *socket)
	}}
	defer transport.CloseIdleConnections()
	client := http.Client{Transport: transport, Timeout: 35 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	var result struct {
		RefusalID string          `json:"refusal_id"`
		OK        bool            `json:"ok"`
		Status    string          `json:"status"`
		Message   string          `json:"message"`
		Data      json.RawMessage `json:"data"`
	}
	decoder := json.NewDecoder(io.LimitReader(response.Body, 8*1024*1024))
	if err = decoder.Decode(&result); err != nil {
		return nil, err
	}
	if response.StatusCode != 200 || !result.OK {
		return nil, &core.Error{Code: result.Status, Message: result.Message, RefusalID: result.RefusalID}
	}
	return result.Data, nil
}
