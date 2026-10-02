package core

const sourceOpening96 = "cairn.source-opening-96/1"

func validateSpanContext(p SemanticPackage) error {
	if p.SpanContext != "" && (p.SpanContext != sourceOpening96 || p.Mode != "index" || p.Purpose != "context") {
		return failure("INTEGRITY_FAILURE", "historical note span context contract is invalid")
	}
	return nil
}

func noteOpeningBytes(req ExpandRequest, policy string) int {
	if req.OpeningBytes == 0 && policy == sourceOpening96 {
		return 96
	}
	return req.OpeningBytes
}
