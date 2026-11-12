package main

import (
	"errors"
	"math"
	"testing"
)

func TestFixedStoreInputsAndActualResultPreservation(t *testing.T) {
	if len(storeCases) != 14 {
		t.Fatal("missing fixed store boundary")
	}
	seen := map[string]bool{}
	for _, name := range storeCases {
		if seen[name] {
			t.Fatal("duplicate store boundary")
		}
		seen[name] = true
		passages, err := inputs(name)
		if err != nil {
			t.Fatal(err)
		}
		if name == "F-EMBED/too-many-passages/256" && len(passages) != 256 {
			t.Fatal("lost positive count boundary")
		}
		if name == "F-EMBED/bad-dimensions/384" && len(passages[0].Vector) != 384 {
			t.Fatal("lost positive dimension boundary")
		}
		if name == "F-EMBED/nonfinite-vectors/NaN" && !math.IsNaN(float64(passages[0].Vector[0])) {
			t.Fatal("not an actual NaN")
		}
		if len(inputBits(passages)) != len(passages) {
			t.Fatal("input capture dropped rows")
		}
	}
	if _, err := inputs("unknown"); err == nil {
		t.Fatal("accepted arbitrary store case")
	}
	result := observed(true, errors.New("refusal"))
	if result.Result != true || result.Error != "refusal" {
		t.Fatal("lost actual partial result alongside error")
	}
}
