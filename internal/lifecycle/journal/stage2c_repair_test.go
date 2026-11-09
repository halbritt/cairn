package journal

import (
	"context"
	"errors"
	"testing"
)

func TestStage2CRepairRawRequestIDAndPersistedInvalidArgv(t *testing.T) {
	native := fixtureContext(t)
	raw := native.raw()
	raw.Values["delivery_id"] = []any{true, "raw"}
	raw.Values["request_ids"].(map[string]any)["completion"] = false
	base := raw.Command
	raw.Command = func(name string) ([]any, error) {
		args, err := base(name)
		return append(args, true), err
	}
	options := Options{Runner: RunnerFunc(func(context.Context, []string, string) (Result, error) {
		t.Fatal("invalid argv reached runner")
		return Result{}, nil
	})}
	_, err := ExecuteRaw(context.Background(), raw, Request{Kind: "ack"}, options)
	var typ *TypeError
	if !errors.As(err, &typ) {
		t.Fatal(err)
	}
	v := read(t, native)
	slot := dict(t, v["completion"])
	if value(slot["request_id"]) != false || text(slot["status"]) != "pending" || !equal(v["delivery_id"], []any{true, "raw"}) {
		t.Fatal(v)
	}
	_, err = ExecuteRaw(context.Background(), raw, Request{Kind: "ack"}, options)
	if !errors.As(err, &typ) {
		t.Fatal("raw argv changed across retry", err)
	}
}

func TestStage2CRepairLazyFinishCompletionID(t *testing.T) {
	native := fixtureContext(t)
	put(t, native.StateDir, native.AttemptID, map[string]any{"completion": map[string]any{"kind": "ack"}})
	raw := native.raw()
	delete(raw.Values["request_ids"].(map[string]any), "completion")
	_, err := ExecuteRaw(context.Background(), raw, Request{Kind: "finish-reply"}, Options{Runner: RunnerFunc(func(context.Context, []string, string) (Result, error) {
		t.Fatal("conflict reached runner")
		return Result{}, nil
	})})
	requireError(t, err, "IDEMPOTENCY_CONFLICT", 4)
}

func TestStage2CRepairDecodeOutputWithoutSubprocess(t *testing.T) {
	for _, streams := range [][2][]byte{{{0xff}, nil}, {nil, {0xff}}} {
		_, err := commandResult(streams[0], streams[1], nil)
		var decode *UnicodeDecodeError
		if !errors.As(err, &decode) || !Classified(err) {
			t.Fatal(err)
		}
	}
	result, err := commandResult([]byte("one\r\ntwo\r"), nil, nil)
	if err != nil || result.Stdout != "one\ntwo\n" {
		t.Fatal(result, err)
	}
	_, err = decode([]byte{0xff})
	var invalid *UnicodeDecodeError
	if !errors.As(err, &invalid) {
		t.Fatal(err)
	}
}
