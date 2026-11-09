package journal

import (
	"bytes"
	"context"
	"encoding/json"
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

func TestStage2CRepairNonFiniteJSONAndIdentity(t *testing.T) {
	raw := `[NaN,Infinity,-Infinity,"NaN Infinity -Infinity",123456789012345678901234567890,1.2300e+05,{"nested":NaN}]`
	a, err := decode([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	b, err := decode([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := encode(a)
	if err != nil || string(bytes.TrimSpace(encoded)) != raw {
		t.Fatal(string(encoded), err)
	}
	if !equal(a, b) {
		t.Fatal("default-decoder NaN identity must survive container retry comparison")
	}
	left := a.([]any)
	right := b.([]any)
	if equal(left[0], right[0]) || !equal(left[1], right[1]) || !equal(left[2], right[2]) {
		t.Fatal("scalar and container equality were conflated")
	}
	if equal(json.Number("NaN"), json.Number("NaN")) {
		t.Fatal("unproven NaN token equality is not object identity")
	}
	for _, invalid := range []string{`[NaN1]`, `[1NaN]`, `[Infinityx]`, `[-Infinity0]`, `{"x":NaNNaN}`} {
		if _, err := decode([]byte(invalid)); err == nil {
			t.Fatal("accepted malformed constant", invalid)
		}
	}
}

func TestStage2CRepairCompletionIDSubscriptOrder(t *testing.T) {
	native := fixtureContext(t)
	put(t, native.StateDir, native.AttemptID, map[string]any{"completion": map[string]any{"kind": "complete", "argv": native.Commands["complete"]}})
	raw := native.raw()
	delete(raw.Values["request_ids"].(map[string]any), "completion")
	_, err := ExecuteRaw(context.Background(), raw, Request{Kind: "finish-reply"}, Options{Runner: RunnerFunc(func(context.Context, []string, string) (Result, error) {
		t.Fatal("invalid completion reached runner")
		return Result{}, nil
	})})
	var missing *KeyError
	if !errors.As(err, &missing) || missing.Key != "request_id" {
		t.Fatal(err)
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
