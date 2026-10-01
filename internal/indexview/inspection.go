package indexview

import "github.com/halbritt/cairn/core"

// InspectionViewRoom verifies that an explicitly requested allocation survived
// the API boundary. Its remaining grant must also fit beside this rendered
// view. Preparation may have a smaller receipt cap than its total allowance.
// On an identical retry the grant can be below the original derived reserve;
// validating that reserve again would reject legitimate spent receipts.
func InspectionViewRoom(result core.IndexResult, policy string, allowance, receiptCap int) (int, error) {
	invalid := func() (int, error) {
		return 0, &core.Error{Code: "INVALID_RESPONSE", Message: "API did not honor inspection allocation"}
	}
	b := result.Package.Semantic.MemoryBudget
	if b == nil || b.Schema != "cairn.memory-budget/3" || b.InspectionPolicy != policy || policy != "first-fitting-whole/1" || b.Bytes != receiptCap || b.MinPullBytes < 0 || b.MinPullBytes > min(24000, receiptCap) || b.SkippedUnits < 0 || receiptCap > allowance || result.BytesRemaining < 0 || result.BytesRemaining > min(24000, receiptCap) {
		return invalid()
	}
	switch b.InspectionStatus {
	case "ready":
		if b.MinPullBytes == 0 || len(result.Package.Semantic.Index) == 0 {
			return invalid()
		}
	case "no_candidates", "no_whole_fits":
		if b.MinPullBytes != 0 || len(result.Package.Semantic.Index) != 0 {
			return invalid()
		}
	default:
		return invalid()
	}
	return allowance - result.BytesRemaining, nil
}
