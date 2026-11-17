package native

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"time"

	"github.com/google/uuid"
)

type controlRequest struct {
	ID         string                `json:"id"`
	Operation  string                `json:"operation"`
	Generation uint64                `json:"expected_generation,omitempty"`
	Selection  *PublicationSelection `json:"publication,omitempty"`
}

type controlResponse struct {
	Schema string `json:"schema"`
	ID     string `json:"id,omitempty"`
	OK     bool   `json:"ok"`
	Data   any    `json:"data,omitempty"`
	Error  string `json:"error,omitempty"`
}

type completedControl struct {
	request  []byte
	response []byte
}

// RunClaudeOwner is a dedicated controller, not an attach or refresh command.
// Its child admission and publication operations share one process-local lock.
func RunClaudeOwner(ctx context.Context, cfg ClaudeConfig, input io.Reader, output io.Writer) (err error) {
	o, err := NewClaudeOwner(ctx, cfg)
	if err != nil {
		return err
	}
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		err = errors.Join(err, o.CloseNative(cleanup), o.Close())
	}()
	if err := o.enableController(); err != nil {
		return err
	}
	encoder := json.NewEncoder(output)
	if err := encoder.Encode(controlResponse{Schema: "cairn.native-owner-control/1", OK: true, Data: o.Inspect(ctx)}); err != nil {
		return err
	}
	scanner := bufio.NewScanner(input)
	scanner.Buffer(make([]byte, 65536), 1024*1024)
	// This process never resumes an owner or evicts a mutation's request ID.
	// Once full it refuses new operations; retries retain their exact result.
	completed := map[string]completedControl{}
	for scanner.Scan() {
		var request controlRequest
		parseErr := strictJSON(scanner.Bytes(), &request)
		if parseErr == nil {
			decoder := json.NewDecoder(bytes.NewReader(scanner.Bytes()))
			decoder.DisallowUnknownFields()
			parseErr = decoder.Decode(&request)
		}
		if id, idErr := uuid.Parse(request.ID); parseErr == nil && (idErr != nil || id == uuid.Nil || id.String() != request.ID) {
			parseErr = errors.New("canonical nonzero native control request ID required")
		}
		if previous, exists := completed[request.ID]; parseErr == nil && exists {
			if bytes.Equal(previous.request, scanner.Bytes()) {
				if _, err := output.Write(previous.response); err != nil {
					return err
				}
				continue
			}
			parseErr = errors.New("native control request ID was already used with different bytes")
		}
		if parseErr == nil && len(completed) >= 1024 {
			parseErr = errors.New("native controller request limit reached; no further operations admitted")
		}
		var data any
		operationErr := parseErr
		operationContext, cancelOperation := context.WithTimeout(ctx, 3*time.Minute)
		if operationErr == nil {
			switch request.Operation {
			case "inspect":
				data = o.Inspect(operationContext)
			case "initialize":
				data, operationErr = o.Initialize(operationContext, request.Generation)
			case "close-native":
				operationErr = o.closeNativeAt(operationContext, request.Generation)
				if operationErr == nil {
					data = o.Inspect(operationContext)
				}
			case "select":
				if request.Selection == nil {
					operationErr = errors.New("publication selection required")
				} else {
					data, operationErr = o.selectPublication(operationContext, request.Generation, *request.Selection)
				}
			case "publish", "inverse", "recover":
				data, operationErr = o.changePublication(operationContext, request.Generation, request.Operation)
			default:
				operationErr = errors.New("unsupported native owner operation")
			}
		}
		cancelOperation()
		response := controlResponse{Schema: "cairn.native-owner-control/1", ID: request.ID,
			OK: operationErr == nil, Data: data}
		if operationErr != nil {
			response.Error = operationErr.Error()
		}
		raw, err := json.Marshal(response)
		if err != nil {
			return err
		}
		raw = append(raw, '\n')
		if parseErr == nil {
			completed[request.ID] = completedControl{append([]byte{}, scanner.Bytes()...), raw}
		}
		if _, err := output.Write(raw); err != nil {
			return err
		}
	}
	return scanner.Err()
}
