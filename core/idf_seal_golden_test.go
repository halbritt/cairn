package core

import "testing"

func TestIDFSealedRepresentationGolden(t *testing.T) {
	p := SemanticPackage{Schema: "cairn.semantic/17", IDF: &IDFSnapshot{Algorithm: idfAlgorithm, N: 2, CohortSHA256: "cohort", Terms: []IDFTerm{{Digest: "term", DF: 1, Weight: 693147}}}}
	_, seal, err := sealPackage(p)
	if err != nil {
		t.Fatal(err)
	}
	if want := "blake3:67ebb6731420eda82cd0c90c0a820d06c60fb31919cc8708d4d7453c0ba92b9b"; seal != want {
		t.Fatalf("sealed snapshot changed: got %s, want %s", seal, want)
	}
}
