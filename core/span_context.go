package core

const sourceOpening96 = "cairn.source-opening-96/1"

func validateSpanContext(p SemanticPackage) error {
	if p.SpanContext != "" && (p.SpanContext != sourceOpening96 || p.Mode != "index" || p.Purpose != "context") {
		return failure("INTEGRITY_FAILURE", "historical note span context contract is invalid")
	}
	return nil
}
