package memoryclient

import (
	"bytes"
	"encoding/json"
	"time"
)

// RPCObservationComplete distinguishes observation loss from a measured zero.
// Without an observer the operation outcome remains known to its caller, but
// this separate observation stream is unavailable.
func (c *Client) RPCObservationComplete() bool { return c.observeRPC != nil && c.rpcComplete }

func (c *Client) observedRPC(operation string, started time.Time, payload, response any, err error) {
	if c.observeRPC == nil {
		return
	}
	defer func() {
		if recover() != nil {
			c.rpcComplete = false
		}
	}()
	snapshot := func(v any) json.RawMessage {
		if v == nil {
			return nil
		}
		if raw, ok := v.(json.RawMessage); ok {
			return bytes.Clone(raw)
		}
		raw, failure := json.Marshal(v)
		if failure != nil {
			c.rpcComplete = false
			return nil
		}
		return raw
	}
	c.observeRPC(operation, started, snapshot(payload), snapshot(response), err)
}
