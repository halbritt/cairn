package main

import (
	"errors"
	"reflect"
	"testing"
)

func TestRecordedPreservesResultAlongsideError(t *testing.T) {
	result := map[string]any{"partial": 1}
	actual := recorded(result, errors.New("failure"))
	if actual.Error != "failure" || !reflect.DeepEqual(actual.Result, result) {
		t.Fatalf("lost actual Go result/error tuple: %#v", actual)
	}
}
